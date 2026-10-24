"""Report schema and content (spec 4.5, 5.3 item 7)."""

import json
from datetime import datetime, timedelta, timezone

from helpers.expect import (BIG_LIMIT, RFC3339_UTC, assert_repo_matches, repo_entry, strip_volatile)
from helpers.oracle import all_items, parse_timestamp
from helpers.runner import report_of

MISSING = "no/such-schema"


def run_report(ghreport, proxy, repos, cache_dir, *args):
    """Run the service through the proxy and decode the report.

    Args:
        ghreport: Runner fixture.
        proxy: Started FaultProxy.
        repos: Value of --repos.
        cache_dir: Private cache directory.
        args: Extra command-line arguments.

    Returns:
        Tuple (Result, decoded report); exit codes 0 and 1 are accepted.

    Raises:
        AssertionError: The exit code is neither 0 nor 1.
    """
    result = ghreport(repos, cache_dir, *args, base_url=proxy.url, token=None)
    return result, report_of(result, allowed=(0, 1))


def test_top_level_keys_and_generated_at(ghreport, proxy, cache_dir, plain_repo):
    """Spec 4.5: exactly generated_at and repos; generated_at is RFC 3339 UTC and close to the current time.

    Catches: extra/missing keys, local-time stamps, a fixed or epoch timestamp.
    """
    before = datetime.now(timezone.utc) - timedelta(seconds=5)
    _, report = run_report(ghreport, proxy, plain_repo.repo, cache_dir)
    after = datetime.now(timezone.utc) + timedelta(seconds=5)
    assert set(report) == {"generated_at", "repos"} and isinstance(report["repos"], list)
    assert RFC3339_UTC.match(report["generated_at"]), report["generated_at"]
    assert before <= parse_timestamp(report["generated_at"]) <= after


def test_repository_order_follows_the_argument_and_duplicates_are_dropped(ghreport, proxy, cache_dir, plain_repo):
    """Spec 4.2 / 4.5: repositories in --repos order, a repeated name is processed once (first occurrence).

    Catches: sorting, reordering, duplicate entries.
    """
    repos = f"{MISSING}-b,{plain_repo.repo},{MISSING}-a,{plain_repo.repo},{MISSING}-b"
    _, report = run_report(ghreport, proxy, repos, cache_dir)
    assert [entry["repo"] for entry in report["repos"]] == [f"{MISSING}-b", plain_repo.repo, f"{MISSING}-a"]


def test_repository_entry_keys_and_types(ghreport, proxy, cache_dir, plain_repo):
    """Spec 4.5: entry keys are exactly repo/error/count/issues; issue keys exactly number..url; types are strict.

    Catches: missing keys, null instead of [], booleans or floats where integers are required.
    """
    _, report = run_report(ghreport, proxy, f"{plain_repo.repo},{MISSING}", cache_dir)
    for entry in report["repos"]:
        assert set(entry) == {"repo", "error", "count", "issues"}
        assert isinstance(entry["repo"], str) and (entry["error"] is None or isinstance(entry["error"], str))
        assert type(entry["count"]) is int and isinstance(entry["issues"], list)
        assert entry["count"] == len(entry["issues"])
        for issue in entry["issues"]:
            assert set(issue) == {"number", "title", "author", "labels", "created_at", "url"}
            assert type(issue["number"]) is int
            assert all(isinstance(issue[key], str) for key in ("title", "author", "created_at", "url"))
            assert isinstance(issue["labels"], list) and all(isinstance(label, str) for label in issue["labels"])
    failed = repo_entry(report, MISSING)
    assert failed["error"] == "not_found" and failed["issues"] == [] and failed["count"] == 0


def test_issues_are_sorted_by_created_at_then_number(ghreport, proxy, cache_dir, plain_repo):
    """Spec 4.5: ascending (created_at, number); created_at is compared as a moment in time.

    Catches: descending order, sorting by number only, comparing timestamps as strings across UTC offsets.
    """
    _, report = run_report(ghreport, proxy, plain_repo.repo, cache_dir, "--limit", str(BIG_LIMIT))
    issues = repo_entry(report, plain_repo.repo)["issues"]
    keys = [(parse_timestamp(issue["created_at"]), issue["number"]) for issue in issues]
    assert keys == sorted(keys) and len(issues) >= 1


def test_every_issue_equals_the_oracle_field_by_field(ghreport, proxy, cache_dir, profiles, plain_repo):
    """Spec 4.5 mapping: author=user.login, labels=labels[].name in order, url=html_url, created_at verbatim.

    Catches: any wrong field mapping, label reordering, reformatted timestamps.
    """
    _, report = run_report(ghreport, proxy, plain_repo.repo, cache_dir, "--limit", str(BIG_LIMIT))
    assert_repo_matches(repo_entry(report, plain_repo.repo), profiles.pages(plain_repo.repo), limit=BIG_LIMIT)


def test_unicode_titles_survive_unchanged(ghreport, proxy, cache_dir, profiles, plain_repo, need):
    """Spec 4.5 (UTF-8 report): non-ASCII titles are neither escaped into garbage nor mangled.

    Catches: wrong encoding on stdout, replacement characters, lost emoji.
    """
    pages = profiles.pages(plain_repo.repo)
    source_titles = {item["number"]: item["title"] for item in all_items(pages)}
    need(any(not title.isascii() for title in source_titles.values()), "the server has no non-ASCII titles")
    result, report = run_report(ghreport, proxy, plain_repo.repo, cache_dir, "--limit", str(BIG_LIMIT))
    reported = {issue["number"]: issue["title"] for issue in repo_entry(report, plain_repo.repo)["issues"]}
    assert any(not title.isascii() for title in reported.values())
    assert all(title == source_titles[number] for number, title in reported.items())
    assert "\ufffd" not in result.stdout or "\ufffd" in "".join(source_titles.values())


def test_out_file_equals_stdout_report(ghreport, proxy, cache_dir, tmp_path, plain_repo):
    """Spec 4.2: --out <file> writes the same report to the file and leaves stdout empty.

    Catches: writing to both, an empty or differently formatted file, relative-path surprises.
    """
    out = tmp_path / "nested" / "report.json"
    stdout_result, stdout_report = run_report(ghreport, proxy, plain_repo.repo, cache_dir)
    file_result = ghreport(plain_repo.repo, tmp_path / "cache2", "--out", str(out), base_url=proxy.url, token=None)
    assert file_result.returncode == 0 and file_result.stdout == ""
    from_file = json.loads(out.read_text(encoding="utf-8"))
    assert strip_volatile(from_file) == strip_volatile(stdout_report)
    assert RFC3339_UTC.match(from_file["generated_at"])
