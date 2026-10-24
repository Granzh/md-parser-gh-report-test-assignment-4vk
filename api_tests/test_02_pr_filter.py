"""Pull request filtering (spec 5.3 item 2): items with the pull_request key never reach the report."""

import json

from helpers import proxy as px
from helpers.expect import BIG_LIMIT, assert_repo_matches, repo_entry
from helpers.oracle import all_items
from helpers.runner import report_of


def run_all(ghreport, proxy, repo, cache_dir):
    """Run the service with a limit larger than the listing.

    Args:
        ghreport: Runner fixture.
        proxy: Started FaultProxy.
        repo: ``owner/repo``.
        cache_dir: Private cache directory.

    Returns:
        Decoded report entry of the repository.

    Raises:
        AssertionError: The run did not exit with 0.
    """
    result = ghreport(repo, cache_dir, "--limit", str(BIG_LIMIT), base_url=proxy.url, token=None)
    return repo_entry(report_of(result), repo)


def pull_request_numbers(pages):
    """Collect the numbers of items that carry the pull_request key.

    Args:
        pages: Oracle pages.

    Returns:
        Set of numbers.

    Raises:
        None.
    """
    return {item["number"] for item in all_items(pages) if "pull_request" in item}


def test_no_report_item_is_a_pull_request(ghreport, proxy, cache_dir, profiles, pr_repo):
    """Spec 4.4.3: the pull_request key marks a PR; none of them may be reported.

    Catches: a service that filters PRs by URL, title or state instead of the key, or not at all.
    """
    pages = profiles.pages(pr_repo.repo)
    entry = run_all(ghreport, proxy, pr_repo.repo, cache_dir)
    reported = {issue["number"] for issue in entry["issues"]}
    assert not reported & pull_request_numbers(pages)
    assert_repo_matches(entry, pages, limit=BIG_LIMIT)


def test_no_report_url_points_to_a_pull_request(ghreport, proxy, cache_dir, pr_repo):
    """Spec 4.4.3 / 4.5: html_url of a reported issue is an issue address, not a /pull/ address.

    Catches: PRs leaking into the report with their own URLs.
    """
    entry = run_all(ghreport, proxy, pr_repo.repo, cache_dir)
    assert all("/pull/" not in issue["url"] for issue in entry["issues"])


def test_count_excludes_pull_requests(ghreport, proxy, cache_dir, profiles, pr_repo):
    """Spec 4.5: count is the number of reported issues, so PRs must not be included in it.

    Catches: count computed before filtering.
    """
    entry = run_all(ghreport, proxy, pr_repo.repo, cache_dir)
    assert entry["count"] == len(entry["issues"]) == pr_repo.eligible
    assert entry["count"] < pr_repo.items - pr_repo.duplicates


def null_pull_request_values(body):
    """Rewrite a listing so every pull_request value becomes JSON null.

    Args:
        body: Response body bytes (JSON array).

    Returns:
        JSON array bytes in which each existing pull_request key has the value null.

    Raises:
        ValueError: The body is not JSON.
    """
    items = json.loads(body)
    for item in items:
        if "pull_request" in item:
            item["pull_request"] = None
    return json.dumps(items, ensure_ascii=False).encode("utf-8")


def test_pull_request_with_null_value_is_still_a_pull_request(ghreport, proxy, cache_dir, profiles, pr_repo):
    """Spec 4.4.3: the criterion is the presence of the key, whatever its value.

    Catches: a service that tests the value (`!= null`) instead of the key's presence.
    """
    pages = profiles.pages(pr_repo.repo)
    proxy.add_rule(None, px.rewrite_body(null_pull_request_values), times=None)
    entry = run_all(ghreport, proxy, pr_repo.repo, cache_dir)
    assert not {issue["number"] for issue in entry["issues"]} & pull_request_numbers(pages)
    assert_repo_matches(entry, pages, limit=BIG_LIMIT)
