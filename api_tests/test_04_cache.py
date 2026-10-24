"""Caching (spec 5.3 item 4): ETag/If-None-Match, 304 handling, cache robustness and secrecy."""

import pytest

from helpers import proxy as px
from helpers.expect import (BIG_LIMIT, GUARD_TOKEN, assert_repo_matches, files_containing, issue_requests,
                            repo_entry, strip_auth, strip_volatile)
from helpers.runner import report_of, trace_of


def run_cached(ghreport, proxy, repo, cache_dir, trace, token=None):
    """Run the service through the proxy with a trace file and a full-size limit.

    Args:
        ghreport: Runner fixture.
        proxy: Started FaultProxy.
        repo: ``owner/repo``.
        cache_dir: Cache directory used by the run.
        trace: Path of the --trace file.
        token: Token for the run, or None for none.

    Returns:
        Decoded report.

    Raises:
        AssertionError: The run did not exit with 0.
    """
    result = ghreport(repo, cache_dir, "--limit", str(BIG_LIMIT), trace=trace, base_url=proxy.url, token=token)
    return report_of(result)


def etag_of(record):
    """Read the ETag the proxy sent for a journal entry.

    Args:
        record: RequestRecord.

    Returns:
        ETag value or None.

    Raises:
        None.
    """
    return px.get_header(record.response_headers, "etag")


def cache_files(cache_dir):
    """List the files of a cache directory.

    Args:
        cache_dir: Cache directory (may not exist).

    Returns:
        Sorted list of file Paths.

    Raises:
        None.
    """
    return sorted(path for path in cache_dir.rglob("*") if path.is_file()) if cache_dir.exists() else []


def test_first_run_with_an_empty_cache_is_unconditional(ghreport, proxy, cache_dir, tmp_path, plain_repo):
    """Spec 4.4.4: nothing is cached yet, so no If-None-Match, no 304, from_cache false everywhere.

    Catches: sending conditional headers from thin air, reporting cache hits on a cold start.
    """
    run_cached(ghreport, proxy, plain_repo.repo, cache_dir, tmp_path / "t.jsonl")
    requests = issue_requests(proxy)
    assert requests and all(record.status != 304 for record in requests)
    assert all("if-none-match" not in record.headers for record in requests)
    assert all(entry["from_cache"] is False for entry in trace_of(tmp_path / "t.jsonl"))


def test_second_run_revalidates_every_request_with_the_stored_etag(ghreport, proxy, cache_dir, tmp_path, profiles,
                                                                   plain_repo, need):
    """Spec 4.4.4 / B.3: same addresses, If-None-Match = first ETag, 304, from_cache true, identical report.

    Catches: not storing ETags, wrong header value, ignoring 304, a cache-hit report that differs.
    """
    first = run_cached(ghreport, proxy, plain_repo.repo, cache_dir, tmp_path / "t1.jsonl")
    first_requests = issue_requests(proxy)
    etags = [etag_of(record) for record in first_requests]
    need(all(etags), "the server sends no ETag for the listing, so caching cannot be exercised")
    proxy.clear_requests()
    second = run_cached(ghreport, proxy, plain_repo.repo, cache_dir, tmp_path / "t2.jsonl")
    second_requests = issue_requests(proxy)
    assert [r.target for r in second_requests] == [r.target for r in first_requests]
    assert [r.headers.get("if-none-match") for r in second_requests] == etags
    assert [r.status for r in second_requests] == [304] * len(second_requests)
    trace = trace_of(tmp_path / "t2.jsonl")
    assert len(trace) == len(second_requests) and all(e["from_cache"] is True and e["status"] == 304 for e in trace)
    assert strip_volatile(second) == strip_volatile(first)
    assert_repo_matches(repo_entry(second, plain_repo.repo), profiles.pages(plain_repo.repo), limit=BIG_LIMIT)


def test_a_different_cache_directory_starts_cold(ghreport, proxy, cache_dir, tmp_path, plain_repo, need):
    """Spec 4.4.4: the cache lives in --cache-dir only; another directory means unconditional requests.

    Catches: a global or shared cache location.
    """
    run_cached(ghreport, proxy, plain_repo.repo, cache_dir, tmp_path / "t1.jsonl")
    need(cache_files(cache_dir), "the service stored nothing (the server sends no ETag?)")
    proxy.clear_requests()
    run_cached(ghreport, proxy, plain_repo.repo, tmp_path / "other-cache", tmp_path / "t2.jsonl")
    requests = issue_requests(proxy)
    assert requests and all("if-none-match" not in r.headers and r.status == 200 for r in requests)
    assert all(e["from_cache"] is False for e in trace_of(tmp_path / "t2.jsonl"))


CORRUPTIONS = {
    "garbage": lambda data: b"\x00\xff not json at all \xfe",
    "empty": lambda data: b"",
    "truncated": lambda data: data[: len(data) // 2],
    "wrong_shape": lambda data: b'{"unexpected": true}',
    "json_array": lambda data: b"[1, 2, 3]",
    "null": lambda data: b"null",
}


@pytest.mark.parametrize("kind", sorted(CORRUPTIONS))
def test_damaged_cache_files_do_not_break_the_service(ghreport, proxy, cache_dir, tmp_path, profiles, plain_repo,
                                                      need, kind):
    """Spec 4.4.4 (+ robustness): a corrupted cache entry is a miss, never a crash or a wrong report.

    Catches: panics/exit codes on unreadable cache files, serving damaged bodies.
    """
    run_cached(ghreport, proxy, plain_repo.repo, cache_dir, tmp_path / "t1.jsonl")
    files = cache_files(cache_dir)
    need(files, "the service stored nothing (the server sends no ETag?)")
    for path in files:
        path.write_bytes(CORRUPTIONS[kind](path.read_bytes()))
    report = run_cached(ghreport, proxy, plain_repo.repo, cache_dir, tmp_path / "t2.jsonl")
    assert_repo_matches(repo_entry(report, plain_repo.repo), profiles.pages(plain_repo.repo), limit=BIG_LIMIT)


def test_the_token_is_never_written_to_the_cache(ghreport, proxy, cache_dir, tmp_path, plain_repo, need):
    """Spec 4.4.4: "the cache survives restarts and does not contain the token".

    Catches: storing request headers (Authorization) next to the cached body.
    """
    strip_auth(proxy)
    run_cached(ghreport, proxy, plain_repo.repo, cache_dir, tmp_path / "t.jsonl", token=GUARD_TOKEN)
    need(cache_files(cache_dir), "the service stored nothing (the server sends no ETag?)")
    assert any("authorization" in r.headers for r in issue_requests(proxy)), "the guard token was not sent at all"
    assert files_containing(cache_dir, GUARD_TOKEN) == []


def test_a_server_without_etag_needs_no_cache(ghreport, proxy, cache_dir, tmp_path, plain_repo):
    """Spec 4.4.4: without an ETag there is nothing to revalidate; the service still works and sends no condition.

    Catches: crashes or bogus If-None-Match values when the ETag header is missing.
    """
    proxy.add_rule(None, px.drop_header("etag"), times=None)
    run_cached(ghreport, proxy, plain_repo.repo, cache_dir, tmp_path / "t1.jsonl")
    proxy.clear_requests()
    run_cached(ghreport, proxy, plain_repo.repo, cache_dir, tmp_path / "t2.jsonl")
    assert all(not r.headers.get("if-none-match") for r in issue_requests(proxy))
