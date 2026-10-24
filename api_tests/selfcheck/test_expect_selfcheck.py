"""Meta-tests of helpers.expect and of the probes/proxy additions made for the service tests."""

import pytest

from helpers import expect, probes
from helpers import proxy as px
from helpers.oracle import OracleClient
from helpers.proxy import FaultProxy
from stub_server import StubServer
from support import http_request

pytestmark = pytest.mark.selfcheck


# covers: strip_volatile
def test_strip_volatile_removes_generated_at_without_touching_input():
    report = {"generated_at": "x", "repos": [{"repo": "a/b"}]}
    assert expect.strip_volatile(report) == {"repos": [{"repo": "a/b"}]} and "generated_at" in report
    assert expect.strip_volatile({"repos": []}) == {"repos": []}


# covers: repo_entry
def test_repo_entry_finds_or_fails():
    report = {"repos": [{"repo": "a/b"}, {"repo": "c/d"}]}
    assert expect.repo_entry(report, "c/d") == {"repo": "c/d"}
    with pytest.raises(AssertionError, match="missing"):
        expect.repo_entry(report, "x/y")


# covers: assert_repo_matches
def test_assert_repo_matches_accepts_the_oracle_result_and_rejects_deviations(stub):
    from helpers.oracle import expected_issues
    pages = OracleClient(stub.url).fetch_pages("stub/alpha")
    good = expected_issues(pages, limit=5)
    entry = {"repo": "stub/alpha", "error": None, "count": 5, "issues": good}
    expect.assert_repo_matches(entry, pages, limit=5)
    with pytest.raises(AssertionError, match="differ from the oracle"):
        expect.assert_repo_matches(dict(entry, issues=good[:-1], count=4), pages, limit=5)
    with pytest.raises(AssertionError, match="unexpected error"):
        expect.assert_repo_matches(dict(entry, error="not_found"), pages, limit=5)
    with pytest.raises(AssertionError):
        expect.assert_repo_matches(dict(entry, count=6), pages, limit=5)
    since = expected_issues(pages, limit=expect.DEFAULT_LIMIT, since="2026-02-01")
    expect.assert_repo_matches({"repo": "r", "error": None, "count": len(since), "issues": since}, pages,
                               since="2026-02-01")


# covers: issue_requests, requests_for, fault
def test_journal_queries_and_fault_rules(stub):
    with FaultProxy(stub.url) as proxy:
        rule = expect.fault(proxy, "stub/small", px.respond(500), times=1)
        http_request(proxy.url + "/repos/stub/small/issues")
        http_request(proxy.url + "/repos/stub/alpha/issues")
        http_request(proxy.url + "/repos/stub/small/issues")
        http_request(proxy.url + "/other")
        assert [r.status for r in expect.requests_for(proxy, "stub/small")] == [500, 200]
        assert len(expect.issue_requests(proxy)) == 3 and rule.hits == 1
        assert expect.requests_for(proxy, "stub/none") == []


# covers: strip_auth
def test_strip_auth_hides_the_header_upstream_but_keeps_it_in_the_journal(stub):
    with FaultProxy(stub.url) as proxy:
        expect.strip_auth(proxy)
        http_request(proxy.url + "/repos/stub/small/issues", headers={"Authorization": "Bearer abc"})
        assert proxy.requests[0].headers["authorization"] == "Bearer abc"
        assert "authorization" not in {k.lower() for k in stub.requests[-1][1]}


# covers: files_containing
def test_files_containing_searches_trees_files_and_missing_paths(tmp_path):
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "a.json").write_bytes(b'{"t": "secret-1"}')
    (tmp_path / "b.txt").write_bytes(b"nothing")
    assert expect.files_containing(tmp_path, "secret-1") == [tmp_path / "d" / "a.json"]
    assert expect.files_containing(tmp_path / "b.txt", "secret-1") == []
    assert expect.files_containing(tmp_path / "d" / "a.json", "secret-1") == [tmp_path / "d" / "a.json"]
    assert expect.files_containing(tmp_path / "missing", "x") == []
    assert expect.RFC3339_UTC.match("2026-01-02T03:04:05Z") and not expect.RFC3339_UTC.match("2026-01-02T03:04:05+00:00")


# covers: probe_full_pages
def test_probe_full_pages_flags_short_non_final_pages():
    def page(count):
        """Build a page with ``count`` items.

        Args:
            count: Number of items.

        Returns:
            Page dict.

        Raises:
            None.
        """
        return {"url": "u", "status": 200, "headers": {}, "items": [{}] * count}

    assert probes.probe_full_pages([page(100), page(100), page(3)]) == []
    assert probes.probe_full_pages([page(0)]) == []
    found = probes.probe_full_pages([page(100), page(99), page(3)])
    assert len(found) == 1 and "page 2 has 99 items" in found[0]
    assert probes.probe_full_pages([page(2), page(1)], per_page=2) == []
    with StubServer("dup_items") as server:
        pages = OracleClient(server.url).fetch_pages("stub/alpha")
        assert probes.probe_full_pages(pages) == []


# covers: probe_empty_after_last
def test_probe_empty_after_last_detects_a_missing_link(stub):
    oracle = OracleClient(stub.url)
    for repo in ("stub/alpha", "stub/small", "stub/empty", "no/such"):
        assert probes.probe_empty_after_last(oracle, oracle.fetch_pages(repo)) == []
    with StubServer("no_link") as mutant:
        client = OracleClient(mutant.url)
        found = probes.probe_empty_after_last(client, client.fetch_pages("stub/alpha"))
        assert len(found) == 1 and found[0].startswith("SERVER VIOLATION:") and "page=2" in found[0]


# covers: other_proxy
def test_other_proxy_fixture_is_a_distinct_origin(proxy, other_proxy):
    assert other_proxy.url != proxy.url and other_proxy.requests == []


# covers: _Handler, handle_any
def test_journal_keeps_response_headers(stub):
    with FaultProxy(stub.url) as proxy:
        http_request(proxy.url + "/repos/stub/small/issues")
        record = proxy.requests[0]
        assert px.get_header(record.response_headers, "etag") is not None
        proxy.add_rule(None, px.respond(429, {"Retry-After": "3"}))
        http_request(proxy.url + "/repos/stub/small/issues")
        assert px.get_header(proxy.requests[1].response_headers, "retry-after") == "3"
