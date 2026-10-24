"""The --trace journal (spec 4.6, 5.3 item 8): fields, types, ordering and no credentials."""

import urllib.parse

from helpers.expect import BIG_LIMIT, GUARD_TOKEN, RFC3339_UTC, issue_requests, strip_auth
from helpers.oracle import parse_timestamp
from helpers.runner import report_of, trace_of

FIELDS = {"ts", "method", "url", "status", "attempt", "from_cache", "error"}


def traced_run(ghreport, proxy, repo, cache_dir, trace, token=None):
    """Run the service with --trace through the proxy.

    Args:
        ghreport: Runner fixture.
        proxy: Started FaultProxy.
        repo: ``owner/repo``.
        cache_dir: Private cache directory.
        trace: Path of the trace file.
        token: Token for the run, or None.

    Returns:
        Decoded trace records (list of dicts).

    Raises:
        AssertionError: The run did not exit with 0.
    """
    result = ghreport(repo, cache_dir, "--limit", str(BIG_LIMIT), trace=trace, base_url=proxy.url, token=token)
    report_of(result)
    return trace_of(trace)


def test_records_have_exactly_the_specified_fields_and_types(ghreport, proxy, cache_dir, tmp_path, plain_repo):
    """Spec 4.6: ts/method/url/status/attempt/from_cache/error, nothing more, with the right types.

    Catches: renamed or extra fields, strings for numbers, missing error key.
    """
    records = traced_run(ghreport, proxy, plain_repo.repo, cache_dir, tmp_path / "t.jsonl")
    assert records
    for record in records:
        assert set(record) == FIELDS
        assert isinstance(record["ts"], str) and RFC3339_UTC.match(record["ts"]), record["ts"]
        assert record["method"] == "GET"
        parts = urllib.parse.urlsplit(record["url"])
        assert parts.scheme in ("http", "https") and parts.netloc
        assert type(record["status"]) is int and type(record["attempt"]) is int
        assert type(record["from_cache"]) is bool
        assert record["error"] is None or isinstance(record["error"], str)


def test_timestamps_do_not_decrease_and_attempts_start_at_one(ghreport, proxy, cache_dir, tmp_path, plain_repo):
    """Spec 4.6: records are in execution order; the first attempt of a request is numbered 1.

    Catches: unordered writes, 0-based attempts.
    """
    records = traced_run(ghreport, proxy, plain_repo.repo, cache_dir, tmp_path / "t.jsonl")
    moments = [parse_timestamp(record["ts"]) for record in records]
    assert moments == sorted(moments)
    assert records[0]["attempt"] == 1 and all(record["attempt"] >= 1 for record in records)


def test_trace_matches_the_requests_seen_by_the_proxy(ghreport, proxy, cache_dir, tmp_path, multipage_repo):
    """Spec 4.6: one line per HTTP attempt - the journal agrees with what the server actually received.

    Catches: missing or invented records, wrong statuses, urls that differ from the requested ones.
    """
    records = traced_run(ghreport, proxy, multipage_repo.repo, cache_dir, tmp_path / "t.jsonl")
    seen = issue_requests(proxy)
    assert len(records) == len(seen) >= 2
    assert [r["status"] for r in records] == [r.status for r in seen]
    assert [urllib.parse.urlsplit(r["url"]).path + "?" + urllib.parse.urlsplit(r["url"]).query for r in records] == \
        [r.target for r in seen]


def test_first_url_carries_the_specified_query(ghreport, proxy, cache_dir, tmp_path, plain_repo):
    """Spec 4.4.1: GET /repos/{repo}/issues with state=open, sort=created, direction=asc, per_page=100.

    Catches: wrong endpoint or query parameters.
    """
    records = traced_run(ghreport, proxy, plain_repo.repo, cache_dir, tmp_path / "t.jsonl")
    parts = urllib.parse.urlsplit(records[0]["url"])
    query = urllib.parse.parse_qs(parts.query)
    assert parts.path == f"/repos/{plain_repo.repo}/issues"
    for key, value in {"state": "open", "sort": "created", "direction": "asc", "per_page": "100"}.items():
        assert query.get(key) == [value], (key, query)


def test_no_credentials_in_the_trace(ghreport, proxy, cache_dir, tmp_path, plain_repo):
    """Spec 4.6 / 4.7: Authorization and the token never appear in the journal.

    Catches: logging request headers, putting the token into urls.
    """
    strip_auth(proxy)
    trace = tmp_path / "t.jsonl"
    traced_run(ghreport, proxy, plain_repo.repo, cache_dir, trace, token=GUARD_TOKEN)
    assert any("authorization" in r.headers for r in issue_requests(proxy)), "the token was not sent"
    text = trace.read_text(encoding="utf-8")
    assert "authorization" not in text.lower() and GUARD_TOKEN not in text


def test_without_trace_option_no_journal_file_is_created(ghreport, proxy, cache_dir, tmp_path, plain_repo):
    """Spec 4.2: without --trace no journal is kept.

    Catches: a default trace file written next to the cache directory (the working directory is not inspected).
    """
    result = ghreport(plain_repo.repo, cache_dir, base_url=proxy.url, token=None)
    assert result.returncode == 0
    assert sorted(path.name for path in tmp_path.iterdir()) == ["cache"]
    assert not [path for path in cache_dir.rglob("*") if path.suffix == ".jsonl"]
