"""Request format and transport behaviour (spec 4.4.1, 4.7)."""

import pytest

from helpers import proxy as px
from helpers.expect import (BIG_LIMIT, GUARD_TOKEN, assert_repo_matches, files_containing, issue_requests,
                            repo_entry, strip_auth)
from helpers.runner import report_of

TOKEN = "unit-test-token-123"


def run_via(ghreport, proxy, repo, cache_dir, token=None, *args, **kwargs):
    """Run the service through the proxy.

    Args:
        ghreport: Runner fixture.
        proxy: Started FaultProxy.
        repo: Value of --repos.
        cache_dir: Private cache directory.
        token: Token for the run, or None for none.
        args: Extra command-line arguments.
        kwargs: Extra keyword arguments of the runner (for example trace).

    Returns:
        helpers.runner.Result.

    Raises:
        RunnerTimeout: The service did not finish in time.
    """
    return ghreport(repo, cache_dir, "--limit", str(BIG_LIMIT), *args, base_url=proxy.url, token=token, **kwargs)


def test_required_request_headers(ghreport, proxy, cache_dir, plain_repo):
    """Spec 4.4.1: Accept, a meaningful User-Agent and X-GitHub-Api-Version on every request.

    Catches: Go's default User-Agent, a missing or outdated API version, a wrong Accept.
    """
    run_via(ghreport, proxy, plain_repo.repo, cache_dir)
    requests = issue_requests(proxy)
    assert requests
    for record in requests:
        assert record.method == "GET"
        assert record.headers.get("accept") == "application/vnd.github+json"
        agent = record.headers.get("user-agent", "")
        assert agent.strip() and not agent.startswith("Go-http-client"), agent
        assert record.headers.get("x-github-api-version") == "2026-03-10"


def test_authorization_bearer_only_when_a_token_is_set(ghreport, proxy, cache_dir, tmp_path, plain_repo):
    """Spec 4.3 / 4.4.1: `Authorization: Bearer <token>` with a token, no Authorization header without one.

    Catches: wrong scheme, sending an empty header, leaking a token from the environment into tokenless runs.
    """
    strip_auth(proxy)
    run_via(ghreport, proxy, plain_repo.repo, cache_dir, token=TOKEN)
    assert all(r.headers.get("authorization") == f"Bearer {TOKEN}" for r in issue_requests(proxy))
    proxy.clear_requests()
    run_via(ghreport, proxy, plain_repo.repo, tmp_path / "cache-no-token", token=None)
    assert issue_requests(proxy) and all("authorization" not in r.headers for r in issue_requests(proxy))


def test_requests_are_strictly_sequential(ghreport, proxy, cache_dir, plain_repo):
    """Spec 4.4: all requests are sequential. A short delay on each answer makes any overlap observable.

    Catches: parallel fetching of repositories or pages.
    """
    proxy.add_rule(None, px.add_delay(0.05), times=None)
    repos = f"no/seq-1,{plain_repo.repo},no/seq-2,no/seq-3"
    result = run_via(ghreport, proxy, repos, cache_dir)
    assert result.returncode == 1
    requests = proxy.requests
    assert len(requests) >= 4
    assert not any(record.overlapped for record in requests), "two requests were in flight at the same time"
    starts = [record.started for record in requests]
    assert starts == sorted(starts)


@pytest.mark.parametrize("status", [301, 302, 307])
def test_redirects_are_followed_by_location(ghreport, proxy, cache_dir, profiles, plain_repo, status):
    """Spec 4.4.1: 301/302/307 are followed; the next request goes to the Location address.

    Catches: failing on redirects, re-requesting the original address, ignoring Location.
    """
    location = f"/repos/{plain_repo.repo}/issues?state=open&sort=created&direction=asc&per_page=100&moved={status}"
    rule = proxy.add_rule(lambda record: record.param("moved") is None, px.redirect(location, status=status))
    result = run_via(ghreport, proxy, plain_repo.repo, cache_dir)
    assert_repo_matches(repo_entry(report_of(result), plain_repo.repo), profiles.pages(plain_repo.repo),
                        limit=BIG_LIMIT)
    requests = issue_requests(proxy)
    assert rule.hits == 1 and requests[0].status == status
    assert requests[1].param("moved") == str(status) and requests[1].status == 200


def test_authorization_is_not_sent_to_another_origin_after_a_redirect(ghreport, proxy, other_proxy, cache_dir,
                                                                      plain_repo):
    """Spec 4.7 / GitHub redirect rules: the token must not follow a redirect to a different origin.

    Catches: forwarding Authorization to the redirect target (token leak to a third party).
    """
    strip_auth(proxy)
    strip_auth(other_proxy)
    target = other_proxy.url + f"/repos/{plain_repo.repo}/issues?state=open&sort=created&direction=asc&per_page=100"
    proxy.add_rule(None, px.redirect(target, status=302))
    run_via(ghreport, proxy, plain_repo.repo, cache_dir, token=GUARD_TOKEN)
    assert proxy.requests and "authorization" in proxy.requests[0].headers, "the token was never sent"
    assert other_proxy.requests, "the redirect was not followed"
    assert all("authorization" not in record.headers for record in other_proxy.requests)


def test_the_token_never_appears_in_any_output(ghreport, proxy, cache_dir, tmp_path, plain_repo):
    """Spec 4.7: no token in stdout, stderr, the trace or the cache - also when errors are reported.

    Catches: echoing the token in diagnostics, logging headers, persisting it.
    """
    strip_auth(proxy)
    trace = tmp_path / "t.jsonl"
    result = run_via(ghreport, proxy, f"{plain_repo.repo},no/leak-check", cache_dir, token=GUARD_TOKEN, trace=trace)
    assert result.returncode == 1
    assert GUARD_TOKEN not in result.stdout and GUARD_TOKEN not in result.stderr
    assert files_containing(trace, GUARD_TOKEN) == [] and files_containing(cache_dir, GUARD_TOKEN) == []
    assert any("authorization" in r.headers for r in issue_requests(proxy))
