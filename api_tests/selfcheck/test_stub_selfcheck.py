"""Meta-tests of the stub server: data set, paging arithmetic, headers and every violation flag."""

import json
import urllib.parse

import pytest

import stub_server as stubmod
from helpers.oracle import parse_link_header, parse_timestamp
from stub_server import StubServer
from support import http_request

pytestmark = pytest.mark.selfcheck

ALPHA = "/repos/stub/alpha/issues"


def listing(server, repo="stub/alpha", **params):
    """Fetch one listing page from a stub.

    Args:
        server: Running StubServer.
        repo: ``owner/repo``.
        params: Query parameters.

    Returns:
        Tuple (status, headers, decoded body or raw bytes when it is not JSON).

    Raises:
        OSError: Connection failure.
    """
    query = urllib.parse.urlencode(params)
    status, headers, body = http_request(f"{server.url}/repos/{repo}/issues?{query}")
    try:
        return status, headers, json.loads(body)
    except ValueError:
        return status, headers, body


# covers: parse_flags
def test_parse_flags_accepts_known_names_only():
    assert stubmod.parse_flags(None) == frozenset() and stubmod.parse_flags("") == frozenset()
    assert stubmod.parse_flags(" dup_items , no_link ,,") == {"dup_items", "no_link"}
    for name in stubmod.ALL_FLAGS:
        assert stubmod.parse_flags(name) == {name}
    with pytest.raises(ValueError, match="unknown stub flags"):
        stubmod.parse_flags("dup_items,bogus")


# covers: format_created_at
def test_format_created_at_utc_and_plus_three_denote_the_same_instants():
    assert stubmod.format_created_at(1) == "2026-01-01T06:00:00Z"
    assert stubmod.format_created_at(7) == "2026-01-02T21:00:00+03:00"
    assert parse_timestamp(stubmod.format_created_at(7)).isoformat() == "2026-01-02T18:00:00+00:00"
    moments = [parse_timestamp(stubmod.format_created_at(n)) for n in range(1, 251)]
    assert moments == sorted(moments) and len(set(moments)) == 250


# covers: make_item
def test_make_item_issue_pull_request_and_variants():
    issue = stubmod.make_item("o/r", 4, False)
    assert issue["html_url"] == "https://github.example/o/r/issues/4" and "pull_request" not in issue
    assert issue["title"] == "Issue 4" and issue["labels"] == [{"name": "bug"}, {"name": "помощь"}]
    pr = stubmod.make_item("o/r", 5, True, labelled=False, unicode_title=True)
    assert "pull_request" in pr and "/pull/5" in pr["html_url"] and pr["labels"] == []
    assert pr["title"] == "Проблема №5 — ёлка 🎄" and pr["user"]["login"] == "user0"


# covers: build_repos
def test_build_repos_data_set_shape():
    repos = stubmod.build_repos()
    assert set(repos) == {"stub/alpha", "stub/small", "stub/empty"}
    alpha = repos["stub/alpha"]
    assert len(alpha) == 250 and len({i["number"] for i in alpha}) == 250
    assert sum("pull_request" in i for i in alpha) == 50
    assert sum(i["created_at"].endswith("+03:00") for i in alpha) == 35
    assert sum(not i["labels"] for i in alpha) == 62 and any(ord(c) > 127 for i in alpha for c in i["title"])
    assert len(repos["stub/small"]) == 3 and repos["stub/empty"] == []


# covers: page_bounds
@pytest.mark.parametrize("flags,page,expected", [
    ((), 1, (0, 100, 3)), ((), 2, (100, 200, 3)), ((), 3, (200, 250, 3)), ((), 4, (250, 250, 3)),
    (("dup_items",), 2, (99, 199, 3)), (("dup_items",), 3, (198, 250, 3)),
    (("drop_item_between_pages",), 2, (101, 201, 3)), (("drop_item_between_pages",), 3, (202, 250, 3)),
])
def test_page_bounds_for_flags(flags, page, expected):
    assert stubmod.page_bounds(250, page, 100, frozenset(flags)) == expected


# covers: page_bounds
def test_page_bounds_edge_cases():
    assert stubmod.page_bounds(0, 1, 100, frozenset()) == (0, 0, 1)
    assert stubmod.page_bounds(100, 1, 100, frozenset()) == (0, 100, 1)
    assert stubmod.page_bounds(101, 1, 100, frozenset()) == (0, 100, 2)
    assert stubmod.page_bounds(5, 1, 1, frozenset({"dup_items"}))[2] == 5
    assert stubmod.page_bounds(5, 99, 2, frozenset()) == (5, 5, 3)


# covers: etag_matches
def test_etag_matches_forms():
    assert stubmod.etag_matches('"a"', '"a"') and stubmod.etag_matches('W/"a"', '"a"')
    assert stubmod.etag_matches('"x", "a"', '"a"') and stubmod.etag_matches("*", '"a"')
    assert not stubmod.etag_matches('"b"', '"a"') and not stubmod.etag_matches(None, '"a"')
    assert not stubmod.etag_matches("", '"a"')


# covers: link_header
def test_link_header_pages():
    query = {"state": "open", "per_page": "100"}
    first = parse_link_header(stubmod.link_header("h:1", ALPHA, query, 1, 3))
    assert set(first) == {"next", "last"} and first["next"] == f"http://h:1{ALPHA}?state=open&per_page=100&page=2"
    middle = parse_link_header(stubmod.link_header("h:1", ALPHA, query, 2, 3))
    assert set(middle) == {"next", "last", "first", "prev"} and middle["prev"].endswith("page=1")
    last = parse_link_header(stubmod.link_header("h:1", ALPHA, query, 3, 3))
    assert set(last) == {"first", "prev"}
    assert stubmod.link_header("h:1", ALPHA, query, 1, 1) == ""
    assert "next" not in parse_link_header(stubmod.link_header("h:1", ALPHA, query, 4, 3))


# covers: render_listing
def test_render_listing_direct_flags():
    items = stubmod.build_repos()["stub/alpha"]
    status, headers, body = stubmod.render_listing(items, ALPHA, {"per_page": "100"}, "h:1", frozenset(), None)
    assert status == 200 and len(json.loads(body)) == 100 and 'rel="next"' in headers["Link"]
    assert headers["ETag"].startswith('"') and headers["Content-Type"].startswith("application/json")
    assert stubmod.render_listing(items, ALPHA, {}, "h", frozenset({"always_304"}), None)[0] == 304
    cond = stubmod.render_listing(items, ALPHA, {"per_page": "100"}, "h:1", frozenset(), headers["ETag"])
    assert cond[0] == 304 and cond[2] == b"" and "Link" not in cond[1]
    desc = json.loads(stubmod.render_listing(items, ALPHA, {"direction": "desc"}, "h", frozenset(), None)[2])
    assert desc[0]["number"] == 250
    assert json.loads(stubmod.render_listing(items, ALPHA, {"per_page": "x", "page": "y"}, "h", frozenset(),
                                             None)[2])[0]["number"] == 1
    assert len(json.loads(stubmod.render_listing(items, ALPHA, {"per_page": "1000"}, "h", frozenset(), None)[2])) == 100
    assert len(json.loads(stubmod.render_listing(items, ALPHA, {"per_page": "0"}, "h", frozenset(), None)[2])) == 1


# covers: do_GET, answer, record, __init__, __enter__, __exit__
def test_server_serves_alpha_in_three_pages_with_absolute_links(stub):
    status, headers, page1 = listing(stub, per_page=100, state="open")
    assert status == 200 and len(page1) == 100 and page1[0]["number"] == 1
    link = parse_link_header(headers["link"])
    assert link["next"].startswith(stub.url + ALPHA) and "page=2" in link["next"] and "per_page=100" in link["next"]
    _s, _h, page2 = listing(stub, per_page=100, page=2)
    _s, headers3, page3 = listing(stub, per_page=100, page=3)
    assert len(page2) == 100 and len(page3) == 50 and "next" not in parse_link_header(headers3["link"])
    assert listing(stub, per_page=100, page=4)[2] == []
    assert stub.requests[-1][0].endswith("page=4")


# covers: answer
def test_server_default_page_size_and_small_empty_missing(stub):
    assert len(listing(stub)[2]) == 30
    status, headers, body = listing(stub, "stub/small")
    assert status == 200 and len(body) == 3 and "link" not in headers
    status, headers, body = listing(stub, "stub/empty")
    assert status == 200 and body == [] and "link" not in headers
    status, _h, body = listing(stub, "no/such")
    assert status == 404 and body == {"message": "Not Found"}
    assert http_request(stub.url + "/elsewhere")[0] == 404
    assert http_request(stub.url + "/repos/stub/alpha")[0] == 404
    assert http_request(stub.url + "/repos/stub/alpha/issues/1")[0] == 404


# covers: answer
def test_server_rejects_the_invalid_token_everywhere(stub):
    for path in (ALPHA, "/repos/no/such/issues", "/anything"):
        status, _h, body = http_request(stub.url + path, headers={"Authorization": "Bearer invalid"})
        assert status == 401 and b"Bad credentials" in body
    assert http_request(stub.url + ALPHA, headers={"Authorization": "Bearer other"})[0] == 200


# covers: answer, render_listing
def test_server_etag_conditional_flow(stub):
    _s, headers, _b = http_request(stub.url + ALPHA + "?per_page=100")
    status, headers2, body = http_request(stub.url + ALPHA + "?per_page=100",
                                          headers={"If-None-Match": headers["etag"]})
    assert status == 304 and body == b"" and headers2["etag"] == headers["etag"]
    assert http_request(stub.url + ALPHA + "?per_page=100", headers={"If-None-Match": '"other"'})[0] == 200
    page2 = http_request(stub.url + ALPHA + "?per_page=100&page=2")[1]["etag"]
    assert page2 != headers["etag"]


# covers: do_POST
def test_server_is_read_only(stub):
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        status, headers, _b = http_request(stub.url + ALPHA, method=method)
        assert status == 405 and headers["allow"] == "GET"


# covers: log_message
def test_server_log_message_is_silent():
    assert stubmod._StubHandler.log_message(None, "%s", "x") is None


# covers: __init__
def test_server_accepts_flag_iterables_strings_and_rejects_unknown():
    assert StubServer(["no_link"]).flags == {"no_link"} and StubServer("dup_items,no_link").flags == \
        {"dup_items", "no_link"}
    with pytest.raises(ValueError):
        StubServer(["nope"])


# covers: __enter__, __exit__, requests
def test_server_lifecycle_and_journal_snapshot():
    server = StubServer()
    with server:
        assert server.url.startswith("http://127.0.0.1:")
        http_request(server.url + ALPHA)
        snapshot = server.requests
        snapshot.clear()
        assert len(server.requests) == 1
    with pytest.raises(OSError):
        http_request(server.url + ALPHA, timeout=2)


# covers: render_listing, page_bounds
def test_flag_dup_items_repeats_one_item_at_the_boundary_and_is_compliant():
    with StubServer("dup_items") as server:
        _s, _h, page1 = listing(server, per_page=100, page=1)
        _s, _h, page2 = listing(server, per_page=100, page=2)
        _s, _h, page3 = listing(server, per_page=100, page=3)
    assert page2[0]["number"] == page1[-1]["number"] and page3[0]["number"] == page2[-1]["number"]
    numbers = {i["number"] for i in page1 + page2 + page3}
    assert numbers == set(range(1, 251)) and len(page3) == 52


# covers: render_listing, page_bounds
def test_flag_drop_item_between_pages_loses_items():
    with StubServer("drop_item_between_pages") as server:
        numbers = {i["number"] for page in (1, 2, 3) for i in listing(server, per_page=100, page=page)[2]}
    assert numbers == set(range(1, 251)) - {101, 202}


# covers: render_listing
def test_flag_pr_without_flag_hides_the_pull_request_key():
    with StubServer("pr_without_flag") as server:
        items = listing(server, per_page=100)[2]
    assert not any("pull_request" in i for i in items)
    assert any("/pull/" in i["html_url"] for i in items)


# covers: render_listing
def test_flag_always_304_ignores_conditional_headers():
    with StubServer("always_304") as server:
        status, headers, body = http_request(server.url + ALPHA)
        assert status == 304 and body == b"" and "etag" in headers
        assert http_request(server.url + "/repos/stub/small/issues", headers={"If-None-Match": '"x"'})[0] == 304


# covers: render_listing
def test_flag_broken_json_truncates_every_body():
    with StubServer("broken_json") as server:
        for repo in ("stub/alpha", "stub/small", "stub/empty"):
            body = http_request(f"{server.url}/repos/{repo}/issues")[2]
            with pytest.raises(ValueError):
                json.loads(body)


# covers: render_listing
def test_flag_reversed_sort_ignores_direction_asc():
    with StubServer("reversed_sort") as server:
        items = listing(server, per_page=100, direction="asc")[2]
    assert items[0]["number"] == 250 and items[0]["number"] > items[-1]["number"]


# covers: render_listing, link_header
def test_flag_no_link_removes_the_header():
    with StubServer("no_link") as server:
        status, headers, items = listing(server, per_page=100)
    assert status == 200 and len(items) == 100 and "link" not in headers


# covers: spawn_process, main
def test_stub_runs_as_a_separate_process():
    process, url = stubmod.spawn_process(["no_link"])
    try:
        status, headers, body = http_request(url + ALPHA + "?per_page=100")
        assert status == 200 and len(json.loads(body)) == 100 and "link" not in headers
    finally:
        process.kill()
        process.wait(timeout=10)
        process.stdout.close()


# covers: spawn_process
def test_spawn_process_reports_a_failed_start():
    with pytest.raises(RuntimeError, match="did not start"):
        stubmod.spawn_process(["bogus"])


# covers: main
def test_main_rejects_unknown_flags_with_exit_code_two(capsys):
    assert stubmod.main(["--flags", "bogus"]) == 2
    assert "unknown stub flags" in capsys.readouterr().err
