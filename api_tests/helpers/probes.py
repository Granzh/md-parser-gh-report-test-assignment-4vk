"""Probes of the server's own contract compliance.

Every violation message starts with "SERVER VIOLATION:" so a failing run points at the server,
not at the service under test.
"""

import math
import urllib.parse

from helpers.oracle import ServerViolation, parse_link_header, parse_timestamp, decode_items

PREFIX = "SERVER VIOLATION:"


def violation(text):
    """Format a violation message.

    Args:
        text: Description of the contract breach.

    Returns:
        ``"SERVER VIOLATION: <text>"``.

    Raises:
        None.
    """
    return f"{PREFIX} {text}"


def probe_unconditional_not_304(oracle, repo):
    """Check that a request without If-None-Match never gets 304.

    Args:
        oracle: OracleClient.
        repo: ``owner/repo`` to query.

    Returns:
        List of violation messages (empty when compliant).

    Raises:
        OracleError: Network failure.
    """
    status, _headers, _body = oracle.get(oracle.first_url(repo))
    if status == 304:
        return [violation(f"304 Not Modified for {repo} without If-None-Match")]
    return []


def probe_conditional(oracle, repo):
    """Check conditional-request behaviour: a matching ETag yields 304 without a body, or a full 200.

    Args:
        oracle: OracleClient.
        repo: ``owner/repo`` to query (must exist; a non-200 first answer yields no findings).

    Returns:
        List of violation messages (empty when compliant or when the server sends no ETag).

    Raises:
        OracleError: Network failure.
    """
    url = oracle.first_url(repo)
    status, headers, _body = oracle.get(url)
    etag = headers.get("etag")
    if status != 200 or not etag:
        return []
    status2, _headers2, body2 = oracle.get(url, {"If-None-Match": etag})
    if status2 == 304:
        return [violation(f"304 response for {repo} carries a body")] if body2 else []
    if status2 != 200:
        return [violation(f"conditional request for {repo} answered {status2}, expected 304 or 200")]
    return []


def probe_pull_requests_flagged(pages):
    """Check that every pull request carries the ``pull_request`` key.

    Args:
        pages: Page dicts from OracleClient.fetch_pages.

    Returns:
        List with one message per offending item (html_url of a pull request but no ``pull_request`` key).

    Raises:
        None.
    """
    found = []
    for page in pages:
        for item in page["items"]:
            if "/pull/" in str(item.get("html_url", "")) and "pull_request" not in item:
                found.append(violation(f"item #{item.get('number')} looks like a pull request "
                                       f"({item.get('html_url')}) but has no pull_request key"))
    return found


def probe_sorted(pages):
    """Check the requested ascending order by creation time across all pages.

    Args:
        pages: Page dicts from OracleClient.fetch_pages.

    Returns:
        List with a message for the first out-of-order pair (empty when sorted).

    Raises:
        None.
    """
    previous = None
    for page in pages:
        for item in page["items"]:
            try:
                moment = parse_timestamp(item["created_at"])
            except (KeyError, ValueError):
                return [violation(f"item #{item.get('number')} has no usable created_at")]
            if previous is not None and moment < previous[0]:
                return [violation(f"direction=asc violated: #{item.get('number')} ({item['created_at']}) "
                                  f"follows #{previous[1]} created later")]
            previous = (moment, item.get("number"))
    return []


def probe_pagination_complete(oracle, repo, pages):
    """Check that two walks with different page sizes see the same set of issue numbers.

    Args:
        oracle: OracleClient.
        repo: ``owner/repo``.
        pages: Pages of the default walk (per_page=100).

    Returns:
        List of messages; empty when the sets agree or the repository is too small/large to compare.

    Raises:
        ServerViolation: The second walk meets a malformed response.
        OracleError: Network failure.
    """
    first = {item.get("number") for page in pages for item in page["items"]}
    if len(first) < 3:
        return []
    other_size = max(7, math.ceil(len(first) / 40))
    if other_size >= 100:
        return []
    second_pages = oracle.fetch_pages(repo, per_page=other_size)
    second = {item.get("number") for page in second_pages for item in page["items"]}
    if first == second:
        return []
    missing = sorted(first ^ second, key=lambda value: (value is None, value))[:10]
    return [violation(f"listings with per_page=100 and per_page={other_size} differ for {repo} "
                      f"(for example numbers {missing}): items are lost between pages")]


def probe_link_present(oracle, repo):
    """Check that a full page is accompanied by ``Link rel="next"`` when a next page exists.

    Args:
        oracle: OracleClient.
        repo: ``owner/repo``.

    Returns:
        List of messages; empty when compliant or when the repository has fewer than three issues.

    Raises:
        ServerViolation: A response body is malformed.
        OracleError: Network failure.
    """
    url = oracle.first_url(repo, per_page=2)
    status, headers, body = oracle.get(url)
    if status != 200:
        return []
    items = decode_items(body, url)
    if len(items) < 2 or "next" in parse_link_header(headers.get("link", "")):
        return []
    status2, _headers2, body2 = oracle.get(oracle.first_url(repo, per_page=2, extra={"page": 2}))
    if status2 == 200 and decode_items(body2, url):
        return [violation(f"page 1 of {repo} (per_page=2) has no Link rel=\"next\" although page 2 is not empty")]
    return []


def probe_full_pages(pages, per_page=100):
    """Check that every page followed by another one holds exactly ``per_page`` items.

    Args:
        pages: Page dicts from OracleClient.fetch_pages (walked with ``per_page``).
        per_page: Page size requested for the walk.

    Returns:
        List with one message per short non-final page (empty when compliant).

    Raises:
        None.
    """
    found = []
    for index, page in enumerate(pages[:-1]):
        if len(page["items"]) != per_page:
            found.append(violation(f"page {index + 1} has {len(page['items'])} items with per_page={per_page} "
                                   f"although another page follows"))
    return found


def probe_empty_after_last(oracle, pages):
    """Check that the page after a page without ``rel="next"`` is empty.

    Args:
        oracle: OracleClient.
        pages: Page dicts from OracleClient.fetch_pages; only 200 pages lacking a next link are probed.

    Returns:
        List of messages (empty when compliant); the probe builds the ``page+1`` address itself.

    Raises:
        ServerViolation: The probed response is malformed.
        OracleError: Network failure.
    """
    found = []
    for page in pages:
        if page["status"] != 200 or "next" in parse_link_header(page["headers"].get("link", "")):
            continue
        parts = urllib.parse.urlsplit(page["url"])
        query = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
        query["page"] = int(query.get("page", 1)) + 1
        probe_url = urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))
        status, _headers, body = oracle.get(probe_url)
        if status == 200 and decode_items(body, probe_url):
            found.append(violation(f"no Link rel=\"next\" on {page['url']} although {probe_url} is not empty"))
    return found


def check_server_contract(oracle, repo):
    """Run all probes against one existing repository.

    Args:
        oracle: OracleClient.
        repo: ``owner/repo``.

    Returns:
        List of violation messages from every probe; malformed responses are reported as violations too.

    Raises:
        OracleError: Network failure.
    """
    found = []
    try:
        found += probe_unconditional_not_304(oracle, repo)
        pages = oracle.fetch_pages(repo)
        if pages and pages[0]["status"] == 200:
            found += probe_conditional(oracle, repo)
            found += probe_pull_requests_flagged(pages)
            found += probe_sorted(pages)
            found += probe_pagination_complete(oracle, repo, pages)
            found += probe_link_present(oracle, repo)
    except ServerViolation as error:
        found.append(str(error))
    return found
