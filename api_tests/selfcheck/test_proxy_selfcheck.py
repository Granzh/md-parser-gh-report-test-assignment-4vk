"""Meta-tests of helpers.proxy: forwarding, journal, Link rewriting and fault injection."""

import json
import re

import pytest

from helpers import proxy as px
from helpers.oracle import OracleClient
from helpers.proxy import FaultProxy, RequestRecord, Rule
from support import closed_port_url, http_request

pytestmark = pytest.mark.selfcheck

SMALL = "/repos/stub/small/issues?state=open&sort=created&direction=asc&per_page=100"
ALPHA = "/repos/stub/alpha/issues?state=open&sort=created&direction=asc&per_page=100"


def record(target="/x?a=1&b=2", headers=None):
    """Build a journal record for unit tests.

    Args:
        target: Request target.
        headers: Optional header dict.

    Returns:
        RequestRecord.

    Raises:
        None.
    """
    return RequestRecord("GET", target, headers or {}, 0.0)


@pytest.fixture
def chain(stub):
    """Proxy in front of the stub server.

    Args:
        stub: Running stub server.

    Returns:
        Tuple (started FaultProxy, the stub server).

    Raises:
        None.
    """
    with FaultProxy(stub.url) as running:
        yield running, stub


# covers: __enter__, __exit__, __init__, process, forward, handle_any, send_outcome, http_request
def test_proxy_forwards_get_and_keeps_a_journal(chain):
    proxy, stub = chain
    status, headers, body = http_request(proxy.url + SMALL, headers={"X-Probe": "1"})
    assert status == 200 and len(json.loads(body)) == 3 and "etag" in headers
    (entry,) = proxy.requests
    assert entry.method == "GET" and entry.target == SMALL and entry.status == 200 and not entry.injected
    assert entry.headers["x-probe"] == "1" and entry.overlapped is False
    assert stub.requests[-1][0] == SMALL


# covers: forward
def test_proxy_asks_upstream_for_identity_encoding_and_forwards_auth(chain):
    proxy, stub = chain
    http_request(proxy.url + SMALL, headers={"Authorization": "Bearer abc", "Accept-Encoding": "gzip"})
    sent = {k.lower(): v for k, v in stub.requests[-1][1].items()}
    assert sent["accept-encoding"] == "identity" and sent["authorization"] == "Bearer abc"
    assert proxy.requests[-1].headers["accept-encoding"] == "gzip"  # journal shows what the client sent


# covers: forward
def test_proxy_forwards_conditional_requests_and_304(chain):
    proxy, _stub = chain
    _status, headers, _body = http_request(proxy.url + SMALL)
    status, headers2, body = http_request(proxy.url + SMALL, headers={"If-None-Match": headers["etag"]})
    assert status == 304 and body == b"" and headers2["etag"] == headers["etag"]
    assert proxy.requests[-1].status == 304


# covers: forward
def test_proxy_rewrites_link_and_location_to_its_own_address(chain):
    proxy, stub = chain
    _status, headers, _body = http_request(proxy.url + ALPHA)
    assert proxy.url + "/repos/stub/alpha/issues?" in headers["link"]
    assert stub.url not in headers["link"]
    pages = OracleClient(proxy.url).fetch_pages("stub/alpha")
    assert len(pages) == 3 and all(p["url"].startswith(proxy.url) for p in pages)


# covers: forward
def test_proxy_supports_an_upstream_path_prefix(chain):
    inner, _stub = chain
    with FaultProxy(inner.url + "/repos/") as outer:
        _status, headers, body = http_request(outer.url + "/stub/alpha/issues?per_page=100")
        assert len(json.loads(body)) == 100
        assert outer.url + "/stub/alpha/issues?" in headers["link"]
        assert inner.targets()[0].startswith("/repos/stub/alpha/issues?per_page=100")
        next_url = re.search(r'<([^>]*)>; rel="next"', headers["link"]).group(1)
        assert next_url.startswith(outer.url) and len(json.loads(http_request(next_url)[2])) == 100


# covers: forward, closed_port_url
def test_proxy_answers_502_when_upstream_is_down():
    with FaultProxy(closed_port_url(), timeout=2) as proxy:
        status, _headers, body = http_request(proxy.url + SMALL)
        assert status == 502 and b"upstream failure" in body
        assert proxy.requests[0].injected


# covers: process, handle_any
def test_proxy_rejects_non_get_without_contacting_upstream(chain):
    proxy, stub = chain
    before = len(stub.requests)
    status, headers, _body = http_request(proxy.url + SMALL, method="POST")
    assert status == 405 and headers["allow"] == "GET"
    assert proxy.requests[-1].method == "POST" and len(stub.requests) == before
    assert http_request(proxy.url + SMALL, method="DELETE")[0] == 405


# covers: __init__
def test_proxy_rejects_bad_upstream():
    for bad in ("", "ftp://x", "http://", "nonsense"):
        with pytest.raises(ValueError):
            FaultProxy(bad)


# covers: respond, add_rule, process, _respond_before
def test_respond_injects_429_once_then_passes_through(chain):
    proxy, stub = chain
    rule = proxy.add_rule("per_page=100", px.respond(429, {"Retry-After": "7"}, '{"message":"rate limit"}'))
    first = http_request(proxy.url + SMALL)
    second = http_request(proxy.url + SMALL)
    assert first[0] == 429 and first[1]["retry-after"] == "7" and b"rate limit" in first[2]
    assert second[0] == 200
    assert rule.hits == 1 and proxy.requests[0].injected and not proxy.requests[1].injected


# covers: add_rule, process, try_claim
def test_rule_times_counts_and_unlimited(chain):
    proxy, _stub = chain
    proxy.add_rule(None, px.respond(500), times=2)
    assert [http_request(proxy.url + SMALL)[0] for _ in range(3)] == [500, 500, 200]
    proxy.clear_rules()
    proxy.add_rule(None, px.respond(503), times=None)
    assert [http_request(proxy.url + SMALL)[0] for _ in range(3)] == [503, 503, 503]
    proxy.clear_rules()
    assert http_request(proxy.url + SMALL)[0] == 200


# covers: try_claim, matches
def test_matchers_substring_regex_callable_and_none():
    rec = record("/repos/a/b/issues?page=2")
    assert px.matches(None, rec) and px.matches("page=2", rec) and not px.matches("page=3", rec)
    assert px.matches(re.compile(r"page=\d"), rec) and not px.matches(re.compile(r"page=x"), rec)
    assert px.matches(lambda r: r.param("page") == "2", rec) and not px.matches(lambda r: False, rec)
    with pytest.raises(TypeError):
        px.matches(42, rec)


# covers: try_claim
def test_rule_try_claim_consumes_only_matching_requests():
    rule = Rule("hit", px.respond(500), times=2)
    assert not rule.try_claim(record("/miss")) and rule.hits == 0
    assert rule.try_claim(record("/hit")) and rule.try_claim(record("/hit")) and not rule.try_claim(record("/hit"))
    unlimited = Rule(None, px.respond(500), times=None)
    assert all(unlimited.try_claim(record()) for _ in range(50))


# covers: process
def test_first_short_circuit_wins_and_later_rules_stay_unused(chain):
    proxy, _stub = chain
    first = proxy.add_rule(None, px.respond(418))
    second = proxy.add_rule(None, px.respond(500))
    assert http_request(proxy.url + SMALL)[0] == 418 and first.hits == 1 and second.hits == 0
    assert http_request(proxy.url + SMALL)[0] == 500 and second.hits == 1


# covers: drop_header, _drop_response_header_after
def test_drop_response_header(chain):
    proxy, _stub = chain
    proxy.add_rule(None, px.drop_header("ETag"))
    assert "etag" not in http_request(proxy.url + SMALL)[1]
    assert "etag" in http_request(proxy.url + SMALL)[1]


# covers: drop_header, _drop_request_header_before
def test_drop_request_header_makes_upstream_answer_200(chain):
    proxy, stub = chain
    etag = http_request(proxy.url + SMALL)[1]["etag"]
    proxy.add_rule(None, px.drop_header("If-None-Match", where="request"))
    assert http_request(proxy.url + SMALL, headers={"If-None-Match": etag})[0] == 200
    assert "if-none-match" not in {k.lower() for k in stub.requests[-1][1]}
    assert proxy.requests[-1].headers["if-none-match"] == etag


# covers: drop_header
def test_drop_header_rejects_unknown_side():
    with pytest.raises(ValueError):
        px.drop_header("X", where="both")


# covers: rewrite_body, _rewrite_body_after
def test_rewrite_body_corrupts_json(chain):
    proxy, _stub = chain
    proxy.add_rule(None, px.rewrite_body(lambda body: body[:-3]))
    body = http_request(proxy.url + SMALL)[2]
    with pytest.raises(ValueError):
        json.loads(body)
    assert json.loads(http_request(proxy.url + SMALL)[2])


# covers: replace_link, _replace_link_after
def test_replace_link_substitutes_adds_and_removes(chain):
    proxy, _stub = chain
    proxy.add_rule(None, px.replace_link(lambda value: None), times=None)
    assert "link" not in http_request(proxy.url + ALPHA)[1]
    proxy.clear_rules()
    proxy.add_rule(None, px.replace_link(lambda value: '<http://x/y>; rel="next"'))
    assert http_request(proxy.url + SMALL)[1]["link"] == '<http://x/y>; rel="next"'
    proxy.add_rule(None, px.replace_link(lambda value: value + "|" if value else "none"))
    assert http_request(proxy.url + ALPHA)[1]["link"].endswith("|")


# covers: redirect, _redirect_before
def test_redirect_relative_and_absolute(chain):
    proxy, _stub = chain
    proxy.add_rule("per_page=100", px.redirect("/elsewhere"))
    status, headers, _body = http_request(proxy.url + SMALL)
    assert status == 302 and headers["location"] == proxy.url + "/elsewhere"
    proxy.add_rule("per_page=100", px.redirect("http://other.example/x", status=301))
    status, headers, _body = http_request(proxy.url + SMALL)
    assert status == 301 and headers["location"] == "http://other.example/x"


# covers: reset_connection, _reset_before, abort
def test_reset_connection_drops_without_response(chain):
    proxy, _stub = chain
    proxy.add_rule(None, px.reset_connection())
    with pytest.raises(OSError):
        http_request(proxy.url + SMALL)
    assert proxy.requests[0].status == 0 and proxy.requests[0].injected
    assert http_request(proxy.url + SMALL)[0] == 200


# covers: add_delay, _delay_before, pause
def test_delay_waits_before_forwarding(chain):
    import time
    proxy, _stub = chain
    proxy.add_rule(None, px.add_delay(0.3))
    started = time.monotonic()
    assert http_request(proxy.url + SMALL)[0] == 200
    assert time.monotonic() - started >= 0.25


# covers: pause, __exit__
def test_pause_is_cut_short_when_the_proxy_stops(stub):
    import time
    with FaultProxy(stub.url) as proxy:
        pass
    started = time.monotonic()
    proxy.pause(30)
    assert time.monotonic() - started < 5


# covers: begin, end
def test_overlapping_requests_are_flagged():
    with FaultProxy("http://127.0.0.1:9") as proxy:
        class Headers(dict):
            """Header mapping with the ``items`` API of an email message."""
        first = proxy.begin("GET", "/a", Headers(A="1"))
        second = proxy.begin("GET", "/b", Headers())
        assert first.overlapped and second.overlapped and first.headers == {"a": "1"}
        proxy.end(first)
        proxy.end(second)
        third = proxy.begin("GET", "/c", Headers())
        proxy.end(third)
        assert not third.overlapped


# covers: begin
def test_sequential_requests_are_not_flagged(chain):
    proxy, _stub = chain
    for _ in range(3):
        http_request(proxy.url + SMALL)
    assert [r.overlapped for r in proxy.requests] == [False, False, False]


# covers: rewrite_link, _cursor_after, _cursor_before, register_cursor
def test_opaque_cursor_is_added_to_next_and_stripped_on_forwarding(chain):
    proxy, stub = chain
    proxy.add_rule(None, px.rewrite_link(), times=None)
    _status, headers, _body = http_request(proxy.url + ALPHA)
    next_url = re.search(r'<([^>]*)>; rel="next"', headers["link"]).group(1)
    assert "&cursor=" in next_url and "cursor" not in re.search(r'<([^>]*)>; rel="last"', headers["link"]).group(1)
    status, _h, body = http_request(next_url)
    assert status == 200 and json.loads(body)[0]["number"] == 101
    assert "cursor" not in stub.requests[-1][0]
    follow = proxy.requests[-1]
    assert follow.cursor_valid is True and follow.cursor in next_url and proxy.requests[0].cursor_valid is None
    assert len(proxy.issued_cursors) == 2 and len(proxy.issued_targets) == 2


# covers: _cursor_before
def test_strict_cursor_rejects_constructed_page_addresses(chain):
    proxy, stub = chain
    proxy.add_rule(None, px.rewrite_link(), times=None)
    http_request(proxy.url + ALPHA)
    constructed = ALPHA + "&page=2"
    seen = len(stub.requests)
    status, _h, body = http_request(proxy.url + constructed)
    assert status == 400 and b"opaque cursor" in body and len(stub.requests) == seen
    assert proxy.requests[-1].cursor_valid is False and proxy.requests[-1].injected


# covers: _cursor_before
def test_strict_cursor_rejects_forged_and_swapped_cursors(chain):
    proxy, _stub = chain
    proxy.add_rule(None, px.rewrite_link(), times=None)
    http_request(proxy.url + ALPHA)
    assert http_request(proxy.url + ALPHA + "&page=2&cursor=deadbeef")[0] == 400
    assert proxy.requests[-1].cursor == "deadbeef" and proxy.requests[-1].cursor_valid is False
    token = next(iter(proxy.issued_cursors))
    assert http_request(proxy.url + ALPHA + f"&page=3&cursor={token}")[0] == 400


# covers: _cursor_before
def test_non_strict_cursor_only_flags_the_journal(chain):
    proxy, _stub = chain
    proxy.add_rule(None, px.rewrite_link(strict=False), times=None)
    http_request(proxy.url + ALPHA)
    status, _h, body = http_request(proxy.url + ALPHA + "&page=2")
    assert status == 200 and json.loads(body)[0]["number"] == 101
    assert proxy.requests[-1].cursor_valid is False


# covers: _cursor_after
def test_cursor_marker_skips_responses_without_next(chain):
    proxy, _stub = chain
    proxy.add_rule(None, px.rewrite_link(param="opaque"), times=None)
    status, headers, _body = http_request(proxy.url + SMALL)
    assert status == 200 and "link" not in headers and not proxy.issued_cursors
    _s, headers, _b = http_request(proxy.url + ALPHA)
    assert "opaque=" in headers["link"]


# covers: requests, targets, clear_requests, clear_rules
def test_journal_accessors(chain):
    proxy, _stub = chain
    http_request(proxy.url + SMALL)
    snapshot = proxy.requests
    snapshot.clear()
    assert len(proxy.requests) == 1 and proxy.targets() == [SMALL]
    proxy.clear_requests()
    assert proxy.requests == [] and proxy.targets() == []
    proxy.add_rule(None, px.respond(500))
    proxy.clear_rules()
    assert proxy.rules == [] and http_request(proxy.url + SMALL)[0] == 200


# covers: path, param
def test_record_path_and_param():
    rec = record("/a/b?x=1&y=&x=2")
    assert rec.path == "/a/b" and rec.param("x") == "1" and rec.param("y") == "" and rec.param("z") is None


# covers: get_header, remove_header, set_header
def test_header_list_helpers():
    pairs = [("Link", "a"), ("link", "b"), ("ETag", "e")]
    assert px.get_header(pairs, "LINK") == "a" and px.get_header(pairs, "x") is None
    assert px.remove_header(pairs, "link") == [("ETag", "e")] and len(pairs) == 3
    assert px.set_header(pairs, "Link", "c") == [("ETag", "e"), ("Link", "c")]
    assert px.set_header([], "A", "1") == [("A", "1")]


# covers: retarget
def test_retarget_replaces_every_occurrence():
    value = '<http://u/api/x?p=1>; rel="next", <http://u/api/x?p=9>; rel="last"'
    assert px.retarget(value, "http://u/api", "http://p") == '<http://p/x?p=1>; rel="next", <http://p/x?p=9>; rel="last"'
    assert px.retarget("nothing", "http://u", "http://p") == "nothing"


# covers: map_link_urls
def test_map_link_urls_passes_rels_and_keeps_the_rest():
    value = '<a>; rel="next", <b>; rel="last"; title="t", <c>; rel="first prev"'
    calls = []

    def function(url, rels):
        """Record the call and upper-case the URL.

        Args:
            url: Link URL.
            rels: Rel names.

        Returns:
            Upper-cased URL.

        Raises:
            None.
        """
        calls.append((url, rels))
        return url.upper()

    assert px.map_link_urls(value, function) == '<A>; rel="next", <B>; rel="last"; title="t", <C>; rel="first prev"'
    assert calls == [("a", ["next"]), ("b", ["last"]), ("c", ["first", "prev"])]
    assert px.map_link_urls("", function) == ""


# covers: strip_query_param
def test_strip_query_param_variants():
    assert px.strip_query_param("/p?a=1&cursor=x%20y&b=2", "cursor") == ("/p?a=1&b=2", "x y")
    assert px.strip_query_param("/p?cursor=x", "cursor") == ("/p", "x")
    assert px.strip_query_param("/p?a=1", "cursor") == ("/p?a=1", None)
    assert px.strip_query_param("/p", "cursor") == ("/p", None)
    assert px.strip_query_param("/p?a=%41&cursor=1", "cursor") == ("/p?a=%41", "1")
    assert px.strip_query_param("/p?cursor=1&cursor=2", "cursor") == ("/p", "1")


# covers: add_query_param
def test_add_query_param_variants():
    assert px.add_query_param("http://h/p", "c", "v") == "http://h/p?c=v"
    assert px.add_query_param("http://h/p?a=1", "c", "v") == "http://h/p?a=1&c=v"
    assert px.add_query_param("http://h/p?a=1#frag", "c", "v w") == "http://h/p?a=1&c=v%20w#frag"


# covers: log_message
def test_handler_log_message_is_silent():
    assert px._Handler.log_message(None, "%s", "x") is None
