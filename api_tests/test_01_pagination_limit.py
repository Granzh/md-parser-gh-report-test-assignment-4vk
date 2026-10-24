"""Pagination and --limit (spec 5.3 item 1): exactly `limit` suitable items, no repeated numbers."""

import json

from helpers import proxy as px
from helpers.classify import pick_limit
from helpers.expect import BIG_LIMIT, assert_repo_matches, issue_requests, repo_entry
from helpers.oracle import eligible_items
from helpers.runner import report_of


def run_one(ghreport, proxy, repo, cache_dir, *args):
    """Run the service for one repository through the proxy without a token.

    Args:
        ghreport: Runner fixture.
        proxy: Started FaultProxy.
        repo: ``owner/repo``.
        cache_dir: Private cache directory.
        args: Extra command-line arguments.

    Returns:
        Decoded report entry of the repository.

    Raises:
        AssertionError: The run did not exit with 0.
    """
    result = ghreport(repo, cache_dir, *args, base_url=proxy.url, token=None)
    return repo_entry(report_of(result), repo)


def test_limit_smaller_than_the_listing_spans_pages(ghreport, proxy, cache_dir, profiles, limit_repo):
    """Spec 4.4.2: with limit crossing a page boundary the report holds exactly the first N suitable items.

    Catches: stopping at the page end, ignoring limit, returning more than limit.
    """
    pages = profiles.pages(limit_repo.repo)
    limit = pick_limit(limit_repo)
    entry = run_one(ghreport, proxy, limit_repo.repo, cache_dir, "--limit", str(limit))
    assert entry["count"] == limit == len(entry["issues"])
    assert_repo_matches(entry, pages, limit=limit)
    numbers = [issue["number"] for issue in entry["issues"]]
    assert len(set(numbers)) == len(numbers), "repeated issue numbers in the report"
    assert set(numbers) == {item["number"] for item in eligible_items(pages, limit=limit)}


def test_limit_equal_to_the_first_page_stops_after_one_request(ghreport, proxy, cache_dir, profiles, limit_repo,
                                                               need):
    """Spec 4.4.2: once `limit` items are collected the service stops; it must not fetch page 2.

    Catches: prefetching the next page, ignoring the stop rule.
    """
    pages = profiles.pages(limit_repo.repo)
    limit = limit_repo.first_page_eligible
    need(limit >= 1, "first page has no suitable issue")
    entry = run_one(ghreport, proxy, limit_repo.repo, cache_dir, "--limit", str(limit))
    assert_repo_matches(entry, pages, limit=limit)
    assert len(issue_requests(proxy)) == 1


def test_limit_larger_than_the_listing_returns_everything(ghreport, proxy, cache_dir, profiles, multipage_repo):
    """Spec 4.4.2: without reaching `limit` the service follows Link to the last page and reports all suitable items.

    Catches: dropping the last page, stopping early.
    """
    pages = profiles.pages(multipage_repo.repo)
    entry = run_one(ghreport, proxy, multipage_repo.repo, cache_dir, "--limit", str(BIG_LIMIT))
    assert_repo_matches(entry, pages, limit=BIG_LIMIT)
    assert entry["count"] == multipage_repo.eligible
    assert len(issue_requests(proxy)) == len(pages)


def test_limit_one_makes_exactly_one_request(ghreport, proxy, cache_dir, profiles, plain_repo):
    """Spec 4.4.2: --limit 1 is satisfied by the first suitable item; one request to the issues endpoint.

    Catches: fetching further pages after the limit is reached.
    """
    entry = run_one(ghreport, proxy, plain_repo.repo, cache_dir, "--limit", "1")
    assert_repo_matches(entry, profiles.pages(plain_repo.repo), limit=1)
    assert entry["count"] == 1 and len(issue_requests(proxy)) == 1


def inject_boundary_duplicate(pages):
    """Build a body rewriter that repeats the last suitable item of page 1 at the start of page 2.

    Args:
        pages: Oracle pages of the repository (at least two).

    Returns:
        Function taking and returning response body bytes; it prepends the duplicate to the array.

    Raises:
        None.
    """
    last = [item for item in pages[0]["items"] if "pull_request" not in item][-1]

    def rewrite(body):
        """Insert the duplicate at the head of a page.

        Args:
            body: JSON array bytes of page 2.

        Returns:
            JSON array bytes with the duplicate first.

        Raises:
            ValueError: The body is not JSON.
        """
        return json.dumps([last] + json.loads(body), ensure_ascii=False).encode("utf-8")

    return rewrite


def test_duplicate_at_the_page_boundary_is_counted_once(ghreport, proxy, cache_dir, profiles, multipage_repo):
    """Spec 4.4.3: an item repeated on the next page is deduplicated by number.

    Catches: duplicates in the report, count larger than the number of distinct issues.
    """
    pages = profiles.pages(multipage_repo.repo)
    proxy.add_rule(lambda record: record.param("page") == "2", px.rewrite_body(inject_boundary_duplicate(pages)))
    entry = run_one(ghreport, proxy, multipage_repo.repo, cache_dir, "--limit", str(BIG_LIMIT))
    assert_repo_matches(entry, pages, limit=BIG_LIMIT)
    numbers = [issue["number"] for issue in entry["issues"]]
    assert len(set(numbers)) == len(numbers)


def test_duplicate_does_not_consume_the_limit(ghreport, proxy, cache_dir, profiles, limit_repo):
    """Spec 4.4.2-4.4.3: a repeated item must not count towards --limit.

    Catches: deduplicating after the limit check (the report would be one issue short or hold a duplicate).
    """
    pages = profiles.pages(limit_repo.repo)
    limit = pick_limit(limit_repo)
    proxy.add_rule(lambda record: record.param("page") == "2", px.rewrite_body(inject_boundary_duplicate(pages)))
    entry = run_one(ghreport, proxy, limit_repo.repo, cache_dir, "--limit", str(limit))
    assert entry["count"] == limit
    assert_repo_matches(entry, pages, limit=limit)


def test_service_requests_exactly_the_page_addresses_it_was_given(ghreport, proxy, cache_dir, profiles,
                                                                  multipage_repo):
    """Spec 4.4.2: page addresses come from Link, never constructed; the proxy makes them opaque.

    Catches: building `page=N` URLs by hand (the proxy answers 400 for any continuation without its cursor).
    """
    pages = profiles.pages(multipage_repo.repo)
    proxy.add_rule(None, px.rewrite_link(strict=True), times=None)
    entry = run_one(ghreport, proxy, multipage_repo.repo, cache_dir, "--limit", str(BIG_LIMIT))
    assert_repo_matches(entry, pages, limit=BIG_LIMIT)
    requests = issue_requests(proxy)
    assert len(requests) == len(pages)
    assert all(record.status == 200 for record in requests)
    assert all(record.cursor_valid is True for record in requests[1:])
    assert requests[0].cursor_valid is None
