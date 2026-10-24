"""Probes of the server (spec 5.1: the suite must work with any compliant server).

A failure with a "SERVER VIOLATION:" message blames the server, not ghreport; these tests run first so
that a broken server is recognised before the service tests produce confusing failures.
"""

import pytest

from helpers import probes
from helpers.oracle import ServerViolation


@pytest.fixture
def existing_repos(profiles, test_repos, need):
    """Repositories of GHREPORT_TEST_REPOS that exist on the server.

    Args:
        profiles: Profile cache.
        test_repos: Candidate repositories.
        need: Skip/fail gate.

    Returns:
        List of ``owner/repo`` whose first listing page answered 200.

    Raises:
        pytest.skip.Exception: No repository exists (pytest.fail.Exception with GHREPORT_STRICT=1).
    """
    found = []
    for repo in test_repos:
        try:
            if profiles.get(repo).status == 200:
                found.append(repo)
        except ServerViolation as error:
            pytest.fail(str(error), pytrace=False)
    need(found, "none of GHREPORT_TEST_REPOS exists on the server")
    return found


def collect(function, repos):
    """Run a probe over several repositories.

    Args:
        function: Callable taking a repository name and returning a list of violation messages.
        repos: Iterable of ``owner/repo``.

    Returns:
        Flat list of violation messages.

    Raises:
        None.
    """
    found = []
    for repo in repos:
        found += function(repo)
    return found


def test_pull_requests_carry_the_pull_request_key(profiles, existing_repos):
    """Spec 4.4.3 relies on the pull_request key: a /pull/ item without it makes PR filtering untestable."""
    found = collect(lambda repo: probes.probe_pull_requests_flagged(profiles.pages(repo)), existing_repos)
    assert not found, "\n".join(found)


def test_every_page_is_a_json_array_of_objects(profiles, existing_repos):
    """Spec 4.4.6 (invalid_response): a compliant server answers 200 only with a valid JSON array."""
    for repo in existing_repos:
        try:
            pages = profiles.pages(repo)
        except ServerViolation as error:
            pytest.fail(str(error), pytrace=False)
        assert all(isinstance(page["items"], list) for page in pages)


def test_created_at_is_ascending_across_pages(profiles, existing_repos):
    """Spec 4.4.1 (sort=created, direction=asc): the listing must come in ascending creation order."""
    found = collect(lambda repo: probes.probe_sorted(profiles.pages(repo)), existing_repos)
    assert not found, "\n".join(found)


def test_non_final_pages_are_full(profiles, existing_repos):
    """Spec 4.4.2: with per_page=100 every page that is followed by another holds exactly 100 items."""
    found = collect(lambda repo: probes.probe_full_pages(profiles.pages(repo), 100), existing_repos)
    assert not found, "\n".join(found)


def test_link_next_is_present_whenever_a_next_page_exists(oracle, profiles, existing_repos):
    """Spec 4.4.2: a page without rel="next" must be the last; page+1 (built by the test) must be empty."""
    found = collect(lambda repo: probes.probe_empty_after_last(oracle, profiles.pages(repo)), existing_repos)
    assert not found, "\n".join(found)


def test_unconditional_requests_never_get_304(oracle, test_repos):
    """Spec 4.4.4: 304 is only legal as an answer to If-None-Match."""
    found = collect(lambda repo: probes.probe_unconditional_not_304(oracle, repo), test_repos)
    assert not found, "\n".join(found)


def test_repeating_a_request_with_the_etag_gives_304_or_200(oracle, existing_repos):
    """Spec 4.4.4: a conditional request with the current ETag is answered 304 (no body) or 200."""
    found = collect(lambda repo: probes.probe_conditional(oracle, repo), existing_repos)
    assert not found, "\n".join(found)


def test_no_item_is_lost_between_pages(oracle, profiles, existing_repos):
    """Spec 5.4 (skipped element between pages): two walks with different page sizes see the same issues."""
    found = collect(lambda repo: probes.probe_pagination_complete(oracle, repo, profiles.pages(repo)),
                    existing_repos)
    assert not found, "\n".join(found)
