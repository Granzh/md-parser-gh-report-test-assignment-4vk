"""Meta-tests of helpers.probes and helpers.classify: compliant servers pass, every mutant is caught."""

import json

import pytest

from helpers import classify, probes
from helpers.oracle import OracleClient
from helpers.proxy import FaultProxy, respond
from stub_server import VIOLATION_FLAGS, StubServer
from support import FakeOracle

pytestmark = pytest.mark.selfcheck

EXPECTED_MESSAGE = {
    "drop_item_between_pages": "items are lost between pages",
    "pr_without_flag": "no pull_request key",
    "always_304": "304 Not Modified",
    "broken_json": "not valid JSON",
    "reversed_sort": "direction=asc violated",
    "no_link": "no Link rel=\"next\"",
}


def page(*items):
    """Wrap items into a synthetic page dict.

    Args:
        items: Raw issue dicts.

    Returns:
        Page dict as produced by OracleClient.fetch_pages.

    Raises:
        None.
    """
    return {"url": "u", "status": 200, "headers": {}, "items": list(items)}


# covers: violation
def test_violation_prefix():
    assert probes.violation("x") == "SERVER VIOLATION: x"


# covers: check_server_contract
@pytest.mark.parametrize("flags", [(), ("dup_items",)])
def test_compliant_servers_pass_all_probes(flags):
    with StubServer(flags) as server:
        for repo in ("stub/alpha", "stub/small", "stub/empty", "no/such"):
            assert probes.check_server_contract(OracleClient(server.url), repo) == [], (flags, repo)


# covers: check_server_contract
@pytest.mark.parametrize("flag", VIOLATION_FLAGS)
def test_each_mutant_is_detected_with_a_server_violation_message(flag):
    with StubServer(flag) as server:
        found = probes.check_server_contract(OracleClient(server.url), "stub/alpha")
    assert found and all(text.startswith("SERVER VIOLATION:") for text in found)
    assert any(EXPECTED_MESSAGE[flag] in text for text in found), found


# covers: probe_unconditional_not_304
def test_probe_unconditional_not_304():
    with StubServer() as good, StubServer("always_304") as bad:
        assert probes.probe_unconditional_not_304(OracleClient(good.url), "stub/small") == []
        found = probes.probe_unconditional_not_304(OracleClient(bad.url), "stub/small")
    assert len(found) == 1 and "without If-None-Match" in found[0]


# covers: probe_conditional
def test_probe_conditional_accepts_compliant_behaviour(stub):
    assert probes.probe_conditional(OracleClient(stub.url), "stub/alpha") == []
    assert probes.probe_conditional(OracleClient(stub.url), "no/such") == []


# covers: probe_conditional
def test_probe_conditional_flags_error_statuses(stub):
    with FaultProxy(stub.url) as proxy:
        proxy.add_rule(lambda r: "if-none-match" in r.headers, respond(500), times=None)
        found = probes.probe_conditional(OracleClient(proxy.url), "stub/small")
    assert len(found) == 1 and "answered 500" in found[0]


# covers: probe_conditional
def test_probe_conditional_flags_a_304_with_body_and_ignores_missing_etag():
    etag_page = (200, {"etag": '"e"'}, b"[]")
    oracle = FakeOracle([etag_page, (304, {}, b"oops")])
    found = probes.probe_conditional(oracle, "o/r")
    assert len(found) == 1 and "carries a body" in found[0]
    assert oracle.calls[1][1] == {"If-None-Match": '"e"'}
    assert probes.probe_conditional(FakeOracle([(200, {}, b"[]")]), "o/r") == []
    assert probes.probe_conditional(FakeOracle([(200, {"etag": '"e"'}, b"[]"), (200, {}, b"[]")]), "o/r") == []


# covers: probe_pull_requests_flagged
def test_probe_pull_requests_flagged_on_synthetic_pages():
    good = {"number": 1, "html_url": "https://h/o/r/pull/1", "pull_request": {}}
    bad = {"number": 2, "html_url": "https://h/o/r/pull/2"}
    issue = {"number": 3, "html_url": "https://h/o/r/issues/3"}
    assert probes.probe_pull_requests_flagged([page(good, issue)]) == []
    found = probes.probe_pull_requests_flagged([page(issue), page(bad)])
    assert len(found) == 1 and "#2" in found[0]
    assert probes.probe_pull_requests_flagged([page({"number": 4})]) == []


# covers: probe_sorted
def test_probe_sorted_on_synthetic_pages():
    def make(number, created):
        """Build a minimal item.

        Args:
            number: Issue number.
            created: created_at text.

        Returns:
            Item dict.

        Raises:
            None.
        """
        return {"number": number, "created_at": created}

    ordered = [page(make(1, "2026-01-01T00:00:00Z"), make(2, "2026-01-01T00:00:00Z")),
               page(make(3, "2026-01-02T03:00:00+03:00"), make(4, "2026-01-02T00:00:01Z"))]
    assert probes.probe_sorted(ordered) == []
    found = probes.probe_sorted([page(make(1, "2026-01-02T00:00:00Z")), page(make(2, "2026-01-01T00:00:00Z"))])
    assert len(found) == 1 and "#2" in found[0] and "direction=asc" in found[0]
    assert "no usable created_at" in probes.probe_sorted([page({"number": 9})])[0]
    assert probes.probe_sorted([]) == []


# covers: probe_pagination_complete
def test_probe_pagination_complete_skips_tiny_and_huge_listings():
    tiny = [page({"number": 1}, {"number": 2})]
    assert probes.probe_pagination_complete(None, "o/r", tiny) == []
    huge = [page(*({"number": n} for n in range(1, 4001)))]
    assert probes.probe_pagination_complete(None, "o/r", huge) == []


# covers: probe_pagination_complete
def test_probe_pagination_complete_compares_two_walks(stub):
    oracle = OracleClient(stub.url)
    assert probes.probe_pagination_complete(oracle, "stub/alpha", oracle.fetch_pages("stub/alpha")) == []
    shortened = oracle.fetch_pages("stub/alpha")
    shortened[0]["items"] = shortened[0]["items"][1:]
    found = probes.probe_pagination_complete(oracle, "stub/alpha", shortened)
    assert len(found) == 1 and "[1]" in found[0]


# covers: probe_link_present
def test_probe_link_present(stub):
    assert probes.probe_link_present(OracleClient(stub.url), "stub/small") == []
    assert probes.probe_link_present(OracleClient(stub.url), "stub/empty") == []
    assert probes.probe_link_present(OracleClient(stub.url), "no/such") == []
    with StubServer("no_link") as mutant:
        found = probes.probe_link_present(OracleClient(mutant.url), "stub/small")
        assert len(found) == 1 and "page 2 is not empty" in found[0]
        assert probes.probe_link_present(OracleClient(mutant.url), "stub/empty") == []


# covers: profile_repo
def test_profile_repo_counts_on_alpha(stub):
    profile = classify.profile_repo("stub/alpha", OracleClient(stub.url).fetch_pages("stub/alpha"))
    assert (profile.status, profile.pages, profile.items, profile.pull_requests) == (200, 3, 250, 50)
    assert (profile.duplicates, profile.eligible, profile.first_page_eligible) == (0, 200, 80)
    assert profile.non_utc == 28 and profile.labelled == 150 and profile.non_ascii_titles > 0
    assert profile.dates[0] == "2026-01-01" and profile.dates == sorted(set(profile.dates))
    assert profile.multi_page and profile.has_pull_requests


# covers: profile_repo
def test_profile_repo_counts_duplicates_and_degenerate_repositories(stub):
    with StubServer("dup_items") as server:
        profile = classify.profile_repo("stub/alpha", OracleClient(server.url).fetch_pages("stub/alpha"))
    assert profile.duplicates == 2 and profile.eligible == 200 and profile.items == 252
    client = OracleClient(stub.url)
    empty = classify.profile_repo("stub/empty", client.fetch_pages("stub/empty"))
    assert (empty.eligible, empty.pages, empty.dates, empty.multi_page) == (0, 1, [], False)
    missing = classify.profile_repo("no/such", client.fetch_pages("no/such"))
    assert missing.status == 404 and missing.eligible == 0
    assert classify.profile_repo("x", []).status == 0


# covers: multi_page, has_pull_requests
def test_profile_properties():
    assert not classify.RepoProfile("a/b", pages=1).multi_page and classify.RepoProfile("a/b", pages=2).multi_page
    assert not classify.RepoProfile("a/b").has_pull_requests
    assert classify.RepoProfile("a/b", pull_requests=1).has_pull_requests


# covers: pick_since
def test_pick_since_splits_the_eligible_issues(stub):
    from helpers.oracle import expected_issues
    pages = OracleClient(stub.url).fetch_pages("stub/alpha")
    profile = classify.profile_repo("stub/alpha", pages)
    since = classify.pick_since(profile)
    assert since in profile.dates and since != profile.dates[0]
    kept = expected_issues(pages, since=since)
    assert 0 < len(kept) < profile.eligible
    assert classify.pick_since(classify.RepoProfile("a/b", dates=["2026-01-01"])) is None
    assert classify.pick_since(classify.RepoProfile("a/b")) is None
    assert classify.pick_since(classify.RepoProfile("a/b", dates=["2026-01-01", "2026-01-02"])) == "2026-01-02"


# covers: pick_limit
def test_pick_limit_crosses_the_first_page_boundary(stub):
    client = OracleClient(stub.url)
    alpha = classify.profile_repo("stub/alpha", client.fetch_pages("stub/alpha"))
    assert classify.pick_limit(alpha) == 81
    assert classify.pick_limit(classify.profile_repo("stub/small", client.fetch_pages("stub/small"))) is None
    assert classify.pick_limit(classify.RepoProfile("a/b", pages=2, eligible=5, first_page_eligible=5)) is None
    assert classify.pick_limit(classify.RepoProfile("a/b", pages=2, eligible=5, first_page_eligible=0)) is None


# covers: __init__, pages, get
def test_profile_cache_fetches_once(stub):
    cache = classify.ProfileCache(OracleClient(stub.url))
    before = len(stub.requests)
    first = cache.get("stub/small")
    after_first = len(stub.requests)
    assert cache.get("stub/small") is first and cache.pages("stub/small") is cache.pages("stub/small")
    assert after_first > before and len(stub.requests) == after_first
    assert first.eligible == 3 and cache.get("no/such").status == 404


# covers: find
def test_profile_cache_find_returns_first_match_in_order(stub):
    cache = classify.ProfileCache(OracleClient(stub.url))
    found = cache.find(["no/such", "stub/small", "stub/alpha"], lambda p: p.eligible >= 1)
    assert found.repo == "stub/small"
    assert cache.find(["stub/small", "stub/alpha"], lambda p: p.multi_page).repo == "stub/alpha"
    assert cache.find(["no/such"], lambda p: True) is None
    assert cache.find([], lambda p: True) is None
    assert json.dumps(found.dates)
