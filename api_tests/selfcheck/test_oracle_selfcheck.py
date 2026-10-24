"""Meta-tests of helpers.oracle on synthetic pages and against the stub server."""

import pytest

from helpers import oracle as orc
from helpers.oracle import OracleClient, OracleError, ServerViolation
from helpers.proxy import FaultProxy, redirect, replace_link, rewrite_body
from stub_server import StubServer
from support import closed_port_url

pytestmark = pytest.mark.selfcheck


def item(number, created, pr=False, login="u", labels=(), title=None):
    """Build a synthetic API issue object.

    Args:
        number: Issue number.
        created: created_at text.
        pr: True to add the pull_request key.
        login: user.login value.
        labels: Iterable of label names.
        title: Title, default ``"t<number>"``.

    Returns:
        Dict shaped like a GitHub issue.

    Raises:
        None.
    """
    data = {"number": number, "title": title or f"t{number}", "user": {"login": login},
            "labels": [{"name": name} for name in labels], "created_at": created,
            "html_url": f"https://h/{number}"}
    if pr:
        data["pull_request"] = {}
    return data


def pages_of(*groups):
    """Wrap lists of items into page dicts.

    Args:
        groups: Lists of items, one per page.

    Returns:
        List of page dicts as produced by OracleClient.fetch_pages.

    Raises:
        None.
    """
    return [{"url": f"u{i}", "status": 200, "headers": {}, "items": list(group)} for i, group in enumerate(groups)]


# covers: parse_link_header
def test_parse_link_header_spec_example():
    value = '<https://x/r/issues?per_page=100&page=2>; rel="next", <https://x/r/issues?per_page=100&page=7>; rel="last"'
    assert orc.parse_link_header(value) == {"next": "https://x/r/issues?per_page=100&page=2",
                                            "last": "https://x/r/issues?per_page=100&page=7"}


# covers: parse_link_header
def test_parse_link_header_odd_but_valid_forms():
    assert orc.parse_link_header("<a,b>; rel=next") == {"next": "a,b"}
    assert orc.parse_link_header('<u>; title="x, y"; rel="prev"') == {"prev": "u"}
    assert orc.parse_link_header('<u>; REL="Next Last"') == {"next": "u", "last": "u"}
    assert orc.parse_link_header('<a>; rel="next",<b>;rel="last"') == {"next": "a", "last": "b"}
    assert orc.parse_link_header('<a>; rel="next", <b>; rel="next"') == {"next": "a"}


# covers: parse_link_header
def test_parse_link_header_empty_and_malformed():
    assert orc.parse_link_header(None) == {}
    assert orc.parse_link_header("") == {}
    assert orc.parse_link_header("<a>") == {}
    assert orc.parse_link_header("garbage") == {}
    assert orc.parse_link_header('garbage, <b>; rel="next"') == {"next": "b"}
    assert orc.parse_link_header('<b; rel="next"') == {}


# covers: parse_timestamp
def test_parse_timestamp_offsets_normalise_to_utc():
    base = orc.parse_timestamp("2026-01-05T10:00:00Z")
    assert base == orc.parse_timestamp("2026-01-05T13:00:00+03:00")
    assert base == orc.parse_timestamp("2026-01-05T04:30:00-05:30")
    assert base == orc.parse_timestamp("2026-01-05T10:00:00.123Z")
    assert base.utcoffset().total_seconds() == 0
    assert orc.parse_timestamp("2026-01-01T00:30:00+03:00").day == 31


# covers: parse_timestamp
@pytest.mark.parametrize("bad", ["2026-01-05", "2026-01-05T10:00:00", "yesterday", "", "2026-13-05T10:00:00Z", None, 5])
def test_parse_timestamp_rejects_malformed(bad):
    with pytest.raises(ValueError):
        orc.parse_timestamp(bad)


# covers: parse_since
def test_parse_since_midnight_utc_and_none():
    assert orc.parse_since(None) is None
    moment = orc.parse_since("2026-02-03")
    assert (moment.hour, moment.minute, moment.utcoffset().total_seconds()) == (0, 0, 0)
    assert orc.parse_since("2026-02-03") < orc.parse_timestamp("2026-02-03T00:00:01Z")


# covers: parse_since
@pytest.mark.parametrize("bad", ["2026-2-3", "2026-02-30", "03.02.2026", "", "2026-02-03T00:00:00Z"])
def test_parse_since_rejects_malformed(bad):
    with pytest.raises(ValueError):
        orc.parse_since(bad)


# covers: to_report_issue
def test_to_report_issue_maps_fields_per_table():
    raw = item(7, "2026-01-05T13:00:00+03:00", login="octo", labels=("b", "a", "ё"), title="Заголовок")
    assert orc.to_report_issue(raw) == {
        "number": 7, "title": "Заголовок", "author": "octo", "labels": ["b", "a", "ё"],
        "created_at": "2026-01-05T13:00:00+03:00", "url": "https://h/7"}


# covers: to_report_issue
def test_to_report_issue_accepts_string_labels_and_empty_labels():
    raw = item(1, "2026-01-05T10:00:00Z")
    assert orc.to_report_issue(raw)["labels"] == []
    raw["labels"] = ["x", {"name": "y"}]
    assert orc.to_report_issue(raw)["labels"] == ["x", "y"]


# covers: to_report_issue
@pytest.mark.parametrize("field", ["number", "title", "user", "labels", "created_at", "html_url"])
def test_to_report_issue_missing_field_is_server_violation(field):
    raw = item(1, "2026-01-05T10:00:00Z")
    del raw[field]
    with pytest.raises(ServerViolation, match="^SERVER VIOLATION:"):
        orc.to_report_issue(raw)


# covers: to_report_issue
def test_to_report_issue_null_user_is_server_violation():
    raw = item(1, "2026-01-05T10:00:00Z")
    raw["user"] = None
    with pytest.raises(ServerViolation):
        orc.to_report_issue(raw)


# covers: __init__
def test_server_violation_message_prefix():
    assert str(ServerViolation("boom")) == "SERVER VIOLATION: boom"
    assert isinstance(ServerViolation("x"), AssertionError)


# covers: eligible_items
def test_eligible_items_drops_pull_requests_by_key_only():
    pages = pages_of([item(1, "2026-01-01T00:00:00Z"), item(2, "2026-01-02T00:00:00Z", pr=True)])
    pages[0]["items"][0]["html_url"] = "https://h/pull/1"  # a /pull/ address does not make it a PR
    assert [i["number"] for i in orc.eligible_items(pages)] == [1]


# covers: eligible_items
def test_eligible_items_since_uses_instants_in_utc():
    pages = pages_of([item(1, "2026-01-04T23:59:59Z"), item(2, "2026-01-05T00:00:00Z"),
                      item(3, "2026-01-05T02:00:00+03:00"), item(4, "2026-01-05T04:00:00-05:00")])
    assert [i["number"] for i in orc.eligible_items(pages, since="2026-01-05")] == [2, 4]
    assert [i["number"] for i in orc.eligible_items(pages, since=None)] == [1, 2, 3, 4]


# covers: eligible_items
def test_eligible_items_dedup_keeps_first_and_spans_pages():
    first = item(2, "2026-01-02T00:00:00Z", title="first")
    again = item(2, "2026-01-02T00:00:00Z", title="again")
    pages = pages_of([item(1, "2026-01-01T00:00:00Z"), first], [again, item(3, "2026-01-03T00:00:00Z")])
    chosen = orc.eligible_items(pages)
    assert [i["number"] for i in chosen] == [1, 2, 3] and chosen[1]["title"] == "first"


# covers: eligible_items
def test_eligible_items_limit_stops_early_and_ignores_filtered_items():
    pages = pages_of([item(1, "2026-01-01T00:00:00Z", pr=True), item(2, "2026-01-02T00:00:00Z"),
                      item(2, "2026-01-02T00:00:00Z")],
                     [item(3, "2026-01-03T00:00:00Z"), item(4, "2026-01-04T00:00:00Z")])
    assert [i["number"] for i in orc.eligible_items(pages, limit=2)] == [2, 3]
    assert [i["number"] for i in orc.eligible_items(pages, limit=100)] == [2, 3, 4]
    assert orc.eligible_items(pages, limit=1)[0]["number"] == 2


# covers: eligible_items
def test_eligible_items_malformed_items_are_server_violations():
    with pytest.raises(ServerViolation):
        orc.eligible_items(pages_of([{"number": 1}]))
    with pytest.raises(ServerViolation):
        orc.eligible_items(pages_of([{"created_at": "2026-01-01T00:00:00Z"}]))
    with pytest.raises(ServerViolation):
        orc.eligible_items(pages_of([item(1, "not a date")]))
    with pytest.raises(ValueError):
        orc.eligible_items(pages_of([]), since="bad")


# covers: expected_issues
def test_expected_issues_sorted_by_instant_then_number():
    pages = pages_of([item(5, "2026-01-02T00:00:00Z"), item(3, "2026-01-02T03:00:00+03:00"),
                      item(4, "2026-01-01T23:00:00Z"), item(1, "2026-01-02T00:00:00Z")])
    # 3 is 00:00:00Z as well as 5 and 1; 4 is earlier
    assert [i["number"] for i in orc.expected_issues(pages)] == [4, 1, 3, 5]


# covers: expected_issues
def test_expected_issues_limit_counts_in_server_order_before_sorting():
    pages = pages_of([item(9, "2026-01-09T00:00:00Z"), item(8, "2026-01-08T00:00:00Z")],
                     [item(1, "2026-01-01T00:00:00Z")])
    assert [i["number"] for i in orc.expected_issues(pages, limit=2)] == [8, 9]


# covers: expected_issues
def test_expected_issues_applies_all_filters_together():
    pages = pages_of(
        [item(1, "2026-01-01T00:00:00Z"), item(2, "2026-01-06T00:00:00Z", pr=True),
         item(3, "2026-01-06T00:00:00Z", labels=("x",))],
        [item(3, "2026-01-06T00:00:00Z"), item(4, "2026-01-07T00:00:00Z"), item(5, "2026-01-08T00:00:00Z")])
    result = orc.expected_issues(pages, limit=2, since="2026-01-05")
    assert [i["number"] for i in result] == [3, 4] and result[0]["labels"] == ["x"]
    assert orc.expected_issues(pages_of([])) == []


# covers: all_items
def test_all_items_concatenates_in_order():
    pages = pages_of([item(1, "2026-01-01T00:00:00Z")], [], [item(2, "2026-01-02T00:00:00Z", pr=True)])
    assert [i["number"] for i in orc.all_items(pages)] == [1, 2]


# covers: decode_items
def test_decode_items_accepts_arrays_and_rejects_everything_else():
    assert orc.decode_items(b"[]") == []
    assert orc.decode_items('[{"a": "Ё"}]'.encode("utf-8")) == [{"a": "Ё"}]
    for bad in (b"[", b"{}", b"[1]", b'"x"', b"\xff", b""):
        with pytest.raises(ServerViolation, match="^SERVER VIOLATION:"):
            orc.decode_items(bad, "http://u")


# covers: _header_dict
def test_header_dict_lowercases_and_joins_repeated_headers():
    from email.message import Message
    message = Message()
    message["ETag"] = '"a"'
    message["Link"] = "<x>; rel=next"
    message["link"] = "<y>; rel=last"
    assert orc._header_dict(message) == {"etag": '"a"', "link": "<x>; rel=next, <y>; rel=last"}
    assert orc._header_dict(None) == {}


# covers: redirect_request
def test_no_redirect_handler_returns_none():
    assert orc._NoRedirect().redirect_request(None, None, 302, "Found", {}, "http://x") is None


# covers: __init__
def test_client_rejects_bad_base_and_normalises_trailing_slash():
    for bad in ("", "ftp://x", "http://", "localhost:80"):
        with pytest.raises(ValueError):
            OracleClient(bad)
    client = OracleClient("http://127.0.0.1:9/", "tok", timeout=3)
    assert client.base == "http://127.0.0.1:9" and client.token == "tok" and client.timeout == 3


# covers: first_url
def test_first_url_matches_service_request_shape():
    client = OracleClient("http://h:1/")
    assert client.first_url("o/r") == "http://h:1/repos/o/r/issues?state=open&sort=created&direction=asc&per_page=100"
    assert client.first_url("o/r", 5, {"page": 2}).endswith("per_page=5&page=2")


# covers: get
def test_get_returns_status_headers_body(stub):
    client = OracleClient(stub.url)
    status, headers, body = client.get(client.first_url("stub/small"))
    assert status == 200 and headers["content-type"].startswith("application/json") and body.startswith(b"[")
    assert "etag" in headers


# covers: get
def test_get_returns_error_statuses_without_raising(stub):
    client = OracleClient(stub.url)
    assert client.get(client.first_url("no/such"))[0] == 404
    assert OracleClient(stub.url, "invalid").get(client.first_url("stub/small"))[0] == 401


# covers: get
def test_get_sends_required_headers_and_token(stub):
    OracleClient(stub.url, "tok123").get(stub.url + "/repos/stub/small/issues")
    sent = {k.lower(): v for k, v in stub.requests[-1][1].items()}
    assert sent["authorization"] == "Bearer tok123"
    assert sent["accept"] == "application/vnd.github+json" and sent["x-github-api-version"] == "2026-03-10"
    OracleClient(stub.url).get(stub.url + "/repos/stub/small/issues", {"If-None-Match": '"z"'})
    sent = {k.lower(): v for k, v in stub.requests[-1][1].items()}
    assert "authorization" not in sent and sent["if-none-match"] == '"z"'


# covers: get
def test_get_refuses_other_origins(stub):
    client = OracleClient(stub.url)
    for url in ("http://example.invalid/x", stub.url.replace("127.0.0.1", "localhost") + "/x",
                stub.url.replace("http", "https") + "/x"):
        with pytest.raises(ServerViolation, match="leaves the configured base origin"):
            client.get(url)


# covers: get
def test_get_does_not_follow_redirects(stub):
    with FaultProxy(stub.url) as proxy:
        proxy.add_rule(None, redirect("/repos/stub/small/issues"), times=None)
        client = OracleClient(proxy.url)
        status, headers, _body = client.get(client.first_url("stub/small"))
    assert status == 302 and headers["location"].endswith("/repos/stub/small/issues")


# covers: get
def test_get_wraps_connection_failures():
    url = closed_port_url()
    with pytest.raises(OracleError, match="failed"):
        OracleClient(url, timeout=2).get(url + "/x")


# covers: fetch_pages
def test_fetch_pages_follows_link_next_through_all_pages(stub):
    pages = OracleClient(stub.url).fetch_pages("stub/alpha")
    assert [len(p["items"]) for p in pages] == [100, 100, 50]
    assert all(p["status"] == 200 for p in pages) and "page=2" in pages[1]["url"]
    assert len(orc.all_items(pages)) == 250


# covers: fetch_pages
def test_fetch_pages_respects_per_page(stub):
    pages = OracleClient(stub.url).fetch_pages("stub/small", per_page=2)
    assert [len(p["items"]) for p in pages] == [2, 1]


# covers: fetch_pages
def test_fetch_pages_empty_missing_and_unauthorized(stub):
    client = OracleClient(stub.url)
    empty = client.fetch_pages("stub/empty")
    assert len(empty) == 1 and empty[0]["items"] == [] and empty[0]["status"] == 200
    missing = client.fetch_pages("no/such")
    assert len(missing) == 1 and missing[0]["status"] == 404 and missing[0]["items"] == []
    assert OracleClient(stub.url, "invalid").fetch_pages("stub/small")[0]["status"] == 401


# covers: fetch_pages
def test_fetch_pages_resolves_relative_next_links(stub):
    with FaultProxy(stub.url) as proxy:
        proxy.add_rule(None, replace_link(lambda value: '</repos/stub/small/issues?per_page=2&page=2>; rel="next"'
                                          if value and 'rel="prev"' not in value else None),
                       times=None)
        pages = OracleClient(proxy.url).fetch_pages("stub/small", per_page=2)
    assert [len(p["items"]) for p in pages] == [2, 1]


# covers: fetch_pages
def test_fetch_pages_detects_link_loop(stub):
    with FaultProxy(stub.url) as proxy:
        proxy.add_rule(None, replace_link(lambda value: f'<{proxy.url}/repos/stub/small/issues?loop=1>; rel="next"'),
                       times=None)
        with pytest.raises(ServerViolation, match="loops"):
            OracleClient(proxy.url).fetch_pages("stub/small")


# covers: fetch_pages
def test_fetch_pages_detects_link_leaving_the_origin(stub):
    with FaultProxy(stub.url) as proxy:
        proxy.add_rule(None, replace_link(lambda value: '<http://example.invalid/x>; rel="next"'), times=None)
        with pytest.raises(ServerViolation, match="leaves the configured base origin"):
            OracleClient(proxy.url).fetch_pages("stub/small")


# covers: fetch_pages
def test_fetch_pages_detects_page_budget_overrun(stub, monkeypatch):
    monkeypatch.setattr(orc, "MAX_PAGES", 2)
    with pytest.raises(ServerViolation, match="more than 2 pages"):
        OracleClient(stub.url).fetch_pages("stub/alpha", per_page=10)


# covers: fetch_pages
def test_fetch_pages_malformed_bodies_are_server_violations(stub):
    with FaultProxy(stub.url) as proxy:
        proxy.add_rule(None, rewrite_body(lambda body: b'{"not": "an array"}'), times=None)
        with pytest.raises(ServerViolation, match="not a JSON array"):
            OracleClient(proxy.url).fetch_pages("stub/small")
    with StubServer("broken_json") as mutant:
        with pytest.raises(ServerViolation, match="not valid JSON"):
            OracleClient(mutant.url).fetch_pages("stub/small")
