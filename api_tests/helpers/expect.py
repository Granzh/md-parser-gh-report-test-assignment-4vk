"""Shared building blocks of the service tests: oracle-based expectations and journal queries."""

import copy
import re
from pathlib import Path

from helpers.oracle import expected_issues
from helpers.proxy import drop_header

#: Token handed to the service in leak checks; it must never appear in any output or file.
GUARD_TOKEN = "test-token-DO-NOT-USE"
#: The service's default --limit.
DEFAULT_LIMIT = 100
#: A --limit larger than any listing the tests expect.
BIG_LIMIT = 100000
RFC3339_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")


def strip_volatile(report):
    """Copy a report without the fields that change between runs.

    Args:
        report: Decoded report object.

    Returns:
        Deep copy without ``generated_at``.

    Raises:
        None.
    """
    clean = copy.deepcopy(report)
    clean.pop("generated_at", None)
    return clean


def repo_entry(report, name):
    """Find one repository in a report.

    Args:
        report: Decoded report object.
        name: ``owner/repo``.

    Returns:
        The repository object.

    Raises:
        AssertionError: The repository is absent from the report.
    """
    for entry in report["repos"]:
        if entry["repo"] == name:
            return entry
    raise AssertionError(f"{name} is missing from the report: {[e['repo'] for e in report['repos']]}")


def assert_repo_matches(entry, pages, limit=DEFAULT_LIMIT, since=None):
    """Compare a report entry with the oracle's expectation for the same listing.

    Args:
        entry: Repository object from the report.
        pages: Oracle pages of that repository (OracleClient.fetch_pages).
        limit: The --limit given to the service (default: the service's default of 100).
        since: The --since date given to the service, or None.

    Returns:
        None.

    Raises:
        AssertionError: error is not null, or count/issues differ from the oracle.
    """
    expected = expected_issues(pages, limit=limit, since=since)
    assert entry["error"] is None, f"{entry['repo']}: unexpected error {entry['error']!r}"
    assert entry["issues"] == expected, (
        f"{entry['repo']}: issues differ from the oracle (limit={limit}, since={since}): "
        f"report numbers {[i['number'] for i in entry['issues']][:20]}, "
        f"expected {[i['number'] for i in expected][:20]}")
    assert entry["count"] == len(expected)


def issue_requests(proxy):
    """List the journal entries of listing requests.

    Args:
        proxy: Started FaultProxy.

    Returns:
        RequestRecord list in arrival order, restricted to paths ending in ``/issues``.

    Raises:
        None.
    """
    return [record for record in proxy.requests if record.path.endswith("/issues")]


def requests_for(proxy, repo):
    """List the journal entries of one repository.

    Args:
        proxy: Started FaultProxy.
        repo: ``owner/repo``.

    Returns:
        RequestRecord list whose path is ``/repos/<repo>/issues``.

    Raises:
        None.
    """
    return [record for record in proxy.requests if record.path == f"/repos/{repo}/issues"]


def fault(proxy, repo, action, times=None):
    """Attach a fault action to the requests of one repository.

    Args:
        proxy: Started FaultProxy.
        repo: ``owner/repo`` whose requests are affected.
        action: Action built by helpers.proxy.
        times: Uses of the rule, None for unlimited.

    Returns:
        The registered Rule.

    Raises:
        None.
    """
    return proxy.add_rule(lambda record: record.path == f"/repos/{repo}/issues", action, times=times)


def strip_auth(proxy):
    """Make the proxy hide the Authorization header from the upstream (the journal still shows it).

    Args:
        proxy: Started FaultProxy.

    Returns:
        The registered Rule.

    Raises:
        None.
    """
    return proxy.add_rule(None, drop_header("authorization", where="request"), times=None)


def files_containing(root, needle):
    """Search a file tree for a text.

    Args:
        root: Directory or file (a missing path yields no matches).
        needle: Text to look for (searched as UTF-8 bytes).

    Returns:
        List of Paths whose content contains the text.

    Raises:
        None.
    """
    base = Path(root)
    if not base.exists():
        return []
    files = [base] if base.is_file() else [p for p in base.rglob("*") if p.is_file()]
    data = needle.encode("utf-8")
    return [path for path in files if data in path.read_bytes()]
