"""Rate limiting (spec 5.3 item 5): the service pauses as told and finishes successfully.

Only the LOWER bound of the pause is asserted (the pause must not be skipped); the upper bound is the
overall subprocess timeout. These tests really wait, so they are marked `slow` (two of them, about 4 s).
"""

import time

import pytest

from helpers import proxy as px
from helpers.expect import BIG_LIMIT, assert_repo_matches, issue_requests, repo_entry, requests_for
from helpers.runner import report_of, trace_of

LIMIT_BODY = '{"message":"API rate limit exceeded"}'


def run_traced(ghreport, proxy, repo, cache_dir, trace):
    """Run the service for one repository through the proxy, recording a trace.

    Args:
        ghreport: Runner fixture.
        proxy: Started FaultProxy.
        repo: ``owner/repo``.
        cache_dir: Private cache directory.
        trace: Path of the --trace file.

    Returns:
        Tuple (Result, decoded report).

    Raises:
        AssertionError: The exit code is not 0.
    """
    result = ghreport(repo, cache_dir, "--limit", str(BIG_LIMIT), trace=trace, base_url=proxy.url, token=None)
    return result, report_of(result)


@pytest.mark.slow
def test_429_with_retry_after_is_waited_out(ghreport, proxy, cache_dir, tmp_path, profiles, plain_repo):
    """Spec 4.4.5: 429 + retry-after: 1 -> pause at least that long, retry, exit code 0, correct report.

    Catches: giving up on 429, retrying immediately, ignoring retry-after, reporting an error.
    """
    proxy.add_rule(None, px.respond(429, {"Retry-After": "1"}, LIMIT_BODY))
    result, report = run_traced(ghreport, proxy, plain_repo.repo, cache_dir, tmp_path / "t.jsonl")
    assert_repo_matches(repo_entry(report, plain_repo.repo), profiles.pages(plain_repo.repo), limit=BIG_LIMIT)
    trace = trace_of(tmp_path / "t.jsonl")
    assert [(e["status"], e["attempt"]) for e in trace[:2]] == [(429, 1), (200, 2)]
    assert result.elapsed >= 0.9, f"the service did not wait (elapsed {result.elapsed:.2f} s)"
    assert [r.status for r in issue_requests(proxy)][:2] == [429, 200]


@pytest.mark.slow
def test_403_limit_signals_are_waited_out(ghreport, proxy, cache_dir, tmp_path, profiles, plain_repo):
    """Spec 4.4.5: 403 + x-ratelimit-remaining: 0 + reset (now+2), then 403 + retry-after, then 429 - all are limits.

    Catches: treating 403 as fatal, ignoring x-ratelimit-reset or retry-after on 403, not retrying more than once.
    """
    reset = str(int(time.time()) + 2)
    proxy.add_rule(None, px.respond(403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": reset}, "{}"))
    proxy.add_rule(None, px.respond(403, {"Retry-After": "1"}, '{"message":"forbidden"}'))
    proxy.add_rule(None, px.respond(429, {"Retry-After": "1"}, LIMIT_BODY))
    result, report = run_traced(ghreport, proxy, plain_repo.repo, cache_dir, tmp_path / "t.jsonl")
    assert_repo_matches(repo_entry(report, plain_repo.repo), profiles.pages(plain_repo.repo), limit=BIG_LIMIT)
    trace = trace_of(tmp_path / "t.jsonl")
    assert [(e["status"], e["attempt"]) for e in trace[:4]] == [(403, 1), (403, 2), (429, 3), (200, 4)]
    assert result.elapsed >= 2.0, f"pauses were skipped (elapsed {result.elapsed:.2f} s)"


def test_plain_403_is_not_a_rate_limit(ghreport, proxy, cache_dir, tmp_path, plain_repo):
    """Spec 4.4.5: 403 without retry-after, remaining=0 or a limit message is a plain error: no retries.

    Catches: retrying (and waiting 60 s) on every 403.
    """
    missing = "no/such-forbidden"
    px_rule = proxy.add_rule(lambda record: record.path == f"/repos/{missing}/issues",
                             px.respond(403, {}, '{"message":"Resource not accessible"}'), times=None)
    result = ghreport(f"{plain_repo.repo},{missing}", cache_dir, "--limit", str(BIG_LIMIT), base_url=proxy.url,
                      token=None)
    report = report_of(result, allowed=(1,))
    assert repo_entry(report, missing)["error"] == "request_failed"
    assert repo_entry(report, plain_repo.repo)["error"] is None
    assert len(requests_for(proxy, missing)) == 1 and px_rule.hits == 1
    assert result.elapsed < 20
