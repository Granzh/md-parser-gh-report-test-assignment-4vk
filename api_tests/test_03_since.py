"""The --since filter (spec 5.3 item 3): only issues created at or after 00:00:00 UTC of the date."""

from datetime import date, timedelta

from helpers.classify import pick_since
from helpers.expect import BIG_LIMIT, assert_repo_matches, issue_requests, repo_entry
from helpers.oracle import eligible_items, expected_issues, parse_timestamp
from helpers.runner import report_of


def run_since(ghreport, proxy, repo, cache_dir, since, limit=BIG_LIMIT):
    """Run the service with --since (and --limit) through the proxy.

    Args:
        ghreport: Runner fixture.
        proxy: Started FaultProxy.
        repo: ``owner/repo``.
        cache_dir: Private cache directory.
        since: Value of --since (``YYYY-MM-DD``), or None to omit the option.
        limit: Value of --limit.

    Returns:
        Decoded report entry of the repository.

    Raises:
        AssertionError: The run did not exit with 0.
    """
    args = ["--limit", str(limit)] + (["--since", since] if since else [])
    result = ghreport(repo, cache_dir, *args, base_url=proxy.url, token=None)
    return repo_entry(report_of(result), repo)


def shifted(day, days):
    """Move a ``YYYY-MM-DD`` date.

    Args:
        day: Date text.
        days: Number of days to add (may be negative).

    Returns:
        The shifted date as ``YYYY-MM-DD``.

    Raises:
        ValueError: ``day`` is not a valid date.
    """
    return (date.fromisoformat(day) + timedelta(days=days)).isoformat()


def test_since_inside_the_range_matches_the_oracle(ghreport, proxy, cache_dir, profiles, since_repo):
    """Spec 4.4.3: issues created before the date are dropped, the rest stay (selected by the oracle).

    Catches: wrong comparison direction, comparing dates in local time, filtering by update time.
    """
    since = pick_since(since_repo)
    pages = profiles.pages(since_repo.repo)
    entry = run_since(ghreport, proxy, since_repo.repo, cache_dir, since)
    assert_repo_matches(entry, pages, limit=BIG_LIMIT, since=since)
    assert 0 < entry["count"] < since_repo.eligible


def test_since_after_every_issue_gives_an_empty_successful_report(ghreport, proxy, cache_dir, since_repo):
    """Spec 4.4.3 / 4.4.6: nothing matches -> count 0, error null, exit code 0 (not an error).

    Catches: treating an empty result as a failure.
    """
    since = shifted(since_repo.dates[-1], 1)
    entry = run_since(ghreport, proxy, since_repo.repo, cache_dir, since)
    assert entry["error"] is None and entry["count"] == 0 and entry["issues"] == []


def test_since_before_every_issue_equals_no_filter(ghreport, proxy, cache_dir, profiles, since_repo):
    """Spec 4.4.3: a date earlier than all issues changes nothing.

    Catches: off-by-one in the cut-off, dropping the oldest issues.
    """
    pages = profiles.pages(since_repo.repo)
    since = shifted(since_repo.dates[0], -1)
    filtered = run_since(ghreport, proxy, since_repo.repo, cache_dir, since)
    assert_repo_matches(filtered, pages, limit=BIG_LIMIT)
    assert_repo_matches(filtered, pages, limit=BIG_LIMIT, since=since)
    plain = run_since(ghreport, proxy, since_repo.repo, cache_dir / "plain", None)
    assert plain["issues"] == filtered["issues"]


def test_since_equal_to_the_earliest_creation_date_keeps_that_issue(ghreport, proxy, cache_dir, profiles, since_repo):
    """Spec 4.2: "created not earlier than 00:00:00 UTC of the date" - the boundary day is included.

    Catches: a strict `>` comparison against the start of the day, or comparing against the end of the day.
    """
    pages = profiles.pages(since_repo.repo)
    earliest = min(eligible_items(pages), key=lambda item: (parse_timestamp(item["created_at"]), item["number"]))
    since = since_repo.dates[0]
    entry = run_since(ghreport, proxy, since_repo.repo, cache_dir, since)
    assert_repo_matches(entry, pages, limit=BIG_LIMIT, since=since)
    assert earliest["number"] in {issue["number"] for issue in entry["issues"]} or \
        any(item["number"] == earliest["number"] for item in eligible_items(pages))


def test_the_since_parameter_is_not_sent_to_the_api(ghreport, proxy, cache_dir, since_repo):
    """Spec 4.4.3: the API's `since` filters by update time, so the service must filter locally.

    Catches: forwarding since=... in the query string.
    """
    run_since(ghreport, proxy, since_repo.repo, cache_dir, pick_since(since_repo))
    requests = issue_requests(proxy)
    assert requests
    assert all(record.param("since") is None and "since" not in record.target for record in requests)


def test_since_and_limit_together(ghreport, proxy, cache_dir, profiles, since_repo):
    """Spec 4.4.2-4.4.3: the limit counts only items that passed the date filter.

    Catches: applying the limit before the date filter.
    """
    pages = profiles.pages(since_repo.repo)
    since = pick_since(since_repo)
    kept = expected_issues(pages, limit=BIG_LIMIT, since=since)
    limit = max(1, len(kept) // 2)
    entry = run_since(ghreport, proxy, since_repo.repo, cache_dir, since, limit=limit)
    assert_repo_matches(entry, pages, limit=limit, since=since)
    assert entry["count"] == limit


def test_since_keeps_an_issue_created_exactly_at_midnight(ghreport, proxy, cache_dir, profiles, since_repo, need):
    """Spec 4.2: "created not earlier than 00:00:00 UTC" - an issue created at that very second is included.

    Catches: `<=` instead of `<` in the cut-off comparison (the boundary issue would be lost).
    """
    pages = profiles.pages(since_repo.repo)
    midnight = [item for item in eligible_items(pages)
                if parse_timestamp(item["created_at"]).strftime("%H:%M:%S") == "00:00:00"]
    need(midnight, "no suitable issue was created exactly at 00:00:00 UTC")
    since = parse_timestamp(midnight[0]["created_at"]).strftime("%Y-%m-%d")
    entry = run_since(ghreport, proxy, since_repo.repo, cache_dir, since)
    assert_repo_matches(entry, pages, limit=BIG_LIMIT, since=since)
    assert midnight[0]["number"] in {issue["number"] for issue in entry["issues"]}
