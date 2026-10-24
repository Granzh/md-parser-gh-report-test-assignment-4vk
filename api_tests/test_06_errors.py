"""Errors and exit codes (spec 5.3 item 6, table 4.4.6)."""

import json

import pytest

from helpers import proxy as px
from helpers.expect import (BIG_LIMIT, GUARD_TOKEN, assert_repo_matches, fault, repo_entry, requests_for,
                            strip_auth)
from helpers.runner import report_of, run_raw

MISSING = "no/such-repo"
MISSING2 = "no/such-repo-two"


def test_401_is_fatal_with_exit_code_3(ghreport, proxy, cache_dir, plain_repo):
    """Spec 4.4.6: a 401 answer -> exit code 3, stdout exactly {"error": "unauthorized"}, no token in stderr.

    Catches: treating 401 as a per-repository error, other exit codes, leaking the token into diagnostics.
    """
    proxy.add_rule(None, px.respond(401, {}, '{"message":"Bad credentials"}'), times=None)
    result = ghreport(f"{plain_repo.repo},{MISSING}", cache_dir, base_url=proxy.url, token=GUARD_TOKEN)
    assert result.returncode == 3
    assert json.loads(result.stdout) == {"error": "unauthorized"}
    assert GUARD_TOKEN not in result.stderr and GUARD_TOKEN not in result.stdout
    assert len(proxy.requests) == 1, "the service must stop at the first 401"


def test_missing_repository_among_existing_ones(ghreport, proxy, cache_dir, profiles, plain_repo):
    """Spec 5.3 item 6: 404 for one repository -> exit code 1, error not_found, count 0, others intact, order kept.

    Catches: aborting on 404, exit code 0 or 3, dropping or reordering repositories.
    """
    repos = [MISSING, plain_repo.repo, MISSING2]
    result = ghreport(",".join(repos), cache_dir, "--limit", str(BIG_LIMIT), base_url=proxy.url, token=None)
    report = report_of(result, allowed=(1,))
    assert result.returncode == 1
    assert [entry["repo"] for entry in report["repos"]] == repos
    for name in (MISSING, MISSING2):
        entry = repo_entry(report, name)
        assert (entry["error"], entry["count"], entry["issues"]) == ("not_found", 0, [])
    assert_repo_matches(repo_entry(report, plain_repo.repo), profiles.pages(plain_repo.repo), limit=BIG_LIMIT)
    assert len(requests_for(proxy, MISSING)) == 1, "404 must not be retried"


def test_all_repositories_missing_is_fatal(ghreport, proxy, cache_dir):
    """Spec 4.4.6: every repository failed -> exit code 3 and {"error": "all_failed"} on stdout.

    Catches: exit code 1 with a report when nothing succeeded.
    """
    result = ghreport(f"{MISSING},{MISSING2}", cache_dir, base_url=proxy.url, token=None)
    assert result.returncode == 3
    assert json.loads(result.stdout) == {"error": "all_failed"}


@pytest.mark.parametrize("name,body", [
    ("broken_json", '{"number": 1, '),
    ("not_json", "<html>oops</html>"),
    ("object_instead_of_array", '{"message": "I am not a list"}'),
    ("scalar", "42"),
], ids=["broken_json", "not_json", "object_instead_of_array", "scalar"])
def test_invalid_response_bodies(ghreport, proxy, cache_dir, profiles, plain_repo, name, body):
    """Spec 4.4.6: a 200 body that is not a JSON array -> invalid_response for that repository, no retries.

    Catches: crashing, retrying a hopeless body, reporting empty success for bad data, hurting other repositories.
    """
    fault(proxy, MISSING, px.respond(200, {"Content-Type": "application/json"}, body))
    result = ghreport(f"{MISSING},{plain_repo.repo}", cache_dir, "--limit", str(BIG_LIMIT), base_url=proxy.url,
                      token=None)
    report = report_of(result, allowed=(1,))
    entry = repo_entry(report, MISSING)
    assert (entry["error"], entry["count"], entry["issues"]) == ("invalid_response", 0, [])
    assert len(requests_for(proxy, MISSING)) == 1
    assert_repo_matches(repo_entry(report, plain_repo.repo), profiles.pages(plain_repo.repo), limit=BIG_LIMIT)


@pytest.mark.slow
def test_persistent_failures_become_request_failed_after_three_retries(ghreport, proxy, cache_dir, plain_repo):
    """Spec 4.4.5: 5xx and dropped connections are retried 3 times (1+2+4 s), then request_failed.

    One run covers both (7 s each): repository A answers 500 forever, repository B resets the connection.
    Catches: no retries, more or fewer than 4 attempts, skipping the pauses, one failure poisoning the other repos.
    """
    flaky, dropped = "no/flaky-500", "no/flaky-reset"
    fault(proxy, flaky, px.respond(500, {}, '{"message":"boom"}'))
    fault(proxy, dropped, px.reset_connection())
    result = ghreport(f"{plain_repo.repo},{flaky},{dropped}", cache_dir, base_url=proxy.url, token=None,
                      timeout=120)
    report = report_of(result, allowed=(1,))
    assert repo_entry(report, plain_repo.repo)["error"] is None
    for name in (flaky, dropped):
        entry = repo_entry(report, name)
        assert (entry["error"], entry["count"], entry["issues"]) == ("request_failed", 0, [])
        assert len(requests_for(proxy, name)) == 4, f"{name}: expected 1 attempt + 3 retries"
    assert result.elapsed >= 13.5, f"retry pauses were skipped (elapsed {result.elapsed:.1f} s)"


ARGUMENT_ERRORS = [
    ("no_repos_option", []),
    ("repos_without_slash", ["--repos", "a"]),
    ("repos_with_two_slashes", ["--repos", "a/b/c"]),
    ("limit_zero", ["--repos", "a/b", "--limit", "0"]),
    ("limit_text", ["--repos", "a/b", "--limit", "abc"]),
    ("limit_negative", ["--repos", "a/b", "--limit", "-1"]),
    ("since_invalid_date", ["--repos", "a/b", "--since", "2026-13-40"]),
    ("unknown_flag", ["--repos", "a/b", "--bogus"]),
]


@pytest.mark.parametrize("name,argv", ARGUMENT_ERRORS, ids=[name for name, _ in ARGUMENT_ERRORS])
def test_invalid_arguments_exit_with_code_2(proxy, name, argv):
    """Spec 4.2: invalid arguments -> message on stderr, empty stdout, exit code 2, and no network traffic.

    Catches: wrong exit codes, output on stdout, validating lazily after contacting the API.
    """
    result = run_raw(argv, base_url=proxy.url, token=None)
    assert result.returncode == 2, (name, result.stdout, result.stderr)
    assert result.stdout == "" and result.stderr.strip() != ""
    assert proxy.requests == []
