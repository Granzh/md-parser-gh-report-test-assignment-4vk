"""Independent oracle: reads the server's issue listing and computes the expected report.

Nothing here imports or reuses the service; the Link header parser, the timestamp parser and the
filtering rules are a separate implementation of the specification (parts 2 and 3).
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

API_VERSION = "2026-03-10"
USER_AGENT = "ghreport-api-tests-oracle/1.0"
MAX_PAGES = 2000

_LINK_ITEM = re.compile(
    r'\s*<([^>]*)>((?:\s*;\s*[A-Za-z0-9*_.-]+(?:\s*=\s*(?:"[^"]*"|[^;,\s]*))?)*)\s*(?:,|$)')
_REL_PARAM = re.compile(r';\s*rel\s*=\s*(?:"([^"]*)"|([^;,\s]*))', re.IGNORECASE)


class OracleError(AssertionError):
    """The oracle could not read the server (network failure, wrong base URL)."""


class ServerViolation(AssertionError):
    """The server broke the contract; the message always starts with "SERVER VIOLATION:"."""

    def __init__(self, message):
        """Create the violation.

        Args:
            message: Description without the prefix; "SERVER VIOLATION: " is added automatically.

        Returns:
            None.

        Raises:
            None.
        """
        super().__init__(f"SERVER VIOLATION: {message}")


def parse_link_header(value):
    """Parse an RFC 8288 ``Link`` header value.

    Args:
        value: Header text (several comma-separated links allowed); None or "" means no header.

    Returns:
        Dict mapping each lower-case rel name to the first URL carrying it. Malformed
        fragments are skipped; links without rel are ignored.

    Raises:
        None.
    """
    links = {}
    position = 0
    text = value or ""
    while position < len(text):
        match = _LINK_ITEM.match(text, position)
        if match is None or match.end() == position:
            comma = text.find(",", position)
            if comma < 0:
                break
            position = comma + 1
            continue
        position = match.end()
        rel_match = _REL_PARAM.search(match.group(2) or "")
        if rel_match is None:
            continue
        rel_value = rel_match.group(1) if rel_match.group(1) is not None else rel_match.group(2)
        for rel in rel_value.split():
            links.setdefault(rel.lower(), match.group(1))
    return links


def parse_timestamp(text):
    """Parse an RFC 3339 timestamp into an aware UTC datetime.

    Args:
        text: Timestamp such as ``2026-01-05T10:00:00Z`` or ``2026-01-05T13:00:00+03:00``.

    Returns:
        Timezone-aware datetime converted to UTC.

    Raises:
        ValueError: The text is not an RFC 3339 timestamp with an explicit offset.
    """
    if not isinstance(text, str):
        raise ValueError(f"timestamp must be a string, got {type(text).__name__}")
    match = re.fullmatch(
        r"(\d{4})-(\d{2})-(\d{2})[Tt ](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:([Zz])|([+-])(\d{2}):(\d{2}))", text)
    if match is None:
        raise ValueError(f"not an RFC 3339 timestamp: {text!r}")
    year, month, day, hour, minute, second = (int(match.group(i)) for i in range(1, 7))
    moment = datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
    if match.group(8):
        offset = timedelta(hours=int(match.group(9)), minutes=int(match.group(10)))
        moment -= offset if match.group(8) == "+" else -offset
    return moment


def parse_since(text):
    """Convert a --since date to the instant it denotes.

    Args:
        text: Date ``YYYY-MM-DD``, or None.

    Returns:
        datetime for 00:00:00 UTC of that date, or None when ``text`` is None.

    Raises:
        ValueError: The text is not a valid calendar date in ``YYYY-MM-DD`` form.
    """
    if text is None:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) is None:
        raise ValueError(f"not a YYYY-MM-DD date: {text!r}")
    return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def to_report_issue(item):
    """Map one API issue object to the report's issue object (specification table 4.5).

    Args:
        item: Decoded JSON object of one element of the issues listing.

    Returns:
        Dict with number, title, author (user.login), labels (labels[].name in response order),
        created_at (unchanged) and url (html_url).

    Raises:
        ServerViolation: A required field is missing or has a wrong type.
    """
    try:
        labels = [label["name"] if isinstance(label, dict) else label for label in item["labels"]]
        issue = {
            "number": item["number"],
            "title": item["title"],
            "author": item["user"]["login"],
            "labels": labels,
            "created_at": item["created_at"],
            "url": item["html_url"],
        }
    except (KeyError, TypeError) as error:
        raise ServerViolation(f"issue lacks a required field ({error!r}): {str(item)[:200]}") from None
    return issue


def eligible_items(pages, since=None, limit=None):
    """Select the raw items the service must report, in the order the service meets them.

    Args:
        pages: Page dicts as returned by OracleClient.fetch_pages (only ``items`` is used).
        since: ``YYYY-MM-DD`` or None; items created before 00:00:00 UTC of that date are skipped.
        limit: Maximum number of items, or None for unlimited; collection stops at the limit.

    Returns:
        List of raw items: no ``pull_request`` key, created at or after ``since``, first occurrence
        of each ``number`` only, at most ``limit`` of them, in server order.

    Raises:
        ServerViolation: An item lacks number or created_at.
        ValueError: ``since`` is malformed.
    """
    threshold = parse_since(since)
    seen = set()
    chosen = []
    for page in pages:
        for item in page["items"]:
            if "pull_request" in item:
                continue
            try:
                number = item["number"]
                created = parse_timestamp(item["created_at"])
            except (KeyError, ValueError) as error:
                raise ServerViolation(f"issue without usable number/created_at ({error}): {str(item)[:200]}") from None
            if threshold is not None and created < threshold:
                continue
            if number in seen:
                continue
            seen.add(number)
            chosen.append(item)
            if limit is not None and len(chosen) >= limit:
                return chosen
    return chosen


def expected_issues(pages, limit=None, since=None):
    """Compute the issues array the service must put into the report for one repository.

    Args:
        pages: Page dicts from OracleClient.fetch_pages.
        limit: Maximum number of issues (the service's --limit), or None for unlimited.
        since: ``YYYY-MM-DD`` (the service's --since) or None.

    Returns:
        List of report issue dicts, filtered as in eligible_items and sorted by
        (created_at as an instant, number) ascending.

    Raises:
        ServerViolation: An item cannot be mapped to the report format.
        ValueError: ``since`` is malformed.
    """
    chosen = eligible_items(pages, since, limit)
    chosen.sort(key=lambda item: (parse_timestamp(item["created_at"]), item["number"]))
    return [to_report_issue(item) for item in chosen]


def all_items(pages):
    """Concatenate the items of all pages in server order.

    Args:
        pages: Page dicts from OracleClient.fetch_pages.

    Returns:
        List of raw items (duplicates and pull requests included).

    Raises:
        None.
    """
    return [item for page in pages for item in page["items"]]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Redirect handler that turns every redirect into an HTTPError so the oracle sees the 3xx."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Refuse to follow a redirect.

        Args:
            req: Original request.
            fp: Response body stream.
            code: Redirect status code.
            msg: Status text.
            headers: Response headers.
            newurl: Redirect target.

        Returns:
            None, which makes urllib raise HTTPError for the redirect response.

        Raises:
            None.
        """
        return None


class OracleClient:
    """Read-only GET client for the server under test, used to derive the expected report."""

    def __init__(self, base, token=None, timeout=30.0):
        """Create a client.

        Args:
            base: Base URL (GITHUB_API_BASE_URL); a trailing slash is removed.
            token: Optional token sent as ``Authorization: Bearer`` to this base only.
            timeout: Seconds for each request (positive float).

        Returns:
            None.

        Raises:
            ValueError: ``base`` is not an http(s) URL with a host.
        """
        parts = urllib.parse.urlsplit(base)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError(f"base URL must be http(s) with a host: {base!r}")
        self.base = base.rstrip("/")
        self.token = token
        self.timeout = timeout
        self._origin = (parts.scheme, parts.netloc.lower())
        self._opener = urllib.request.build_opener(_NoRedirect)

    def get(self, url, headers=None):
        """Perform one GET request; only URLs on the base origin are allowed.

        Args:
            url: Absolute URL on the same scheme and host as the base.
            headers: Optional extra request headers (dict) such as If-None-Match.

        Returns:
            Tuple (status, response headers as dict with lower-case names - repeated headers joined
            with ", ", body bytes).

        Raises:
            ServerViolation: The URL points outside the base origin (the suite never leaves it).
            OracleError: A network-level failure or timeout occurred.
        """
        parts = urllib.parse.urlsplit(url)
        if (parts.scheme, parts.netloc.lower()) != self._origin:
            raise ServerViolation(f"URL leaves the configured base origin: {url}")
        request = urllib.request.Request(url, method="GET")
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("User-Agent", USER_AGENT)
        request.add_header("X-GitHub-Api-Version", API_VERSION)
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                return response.status, _header_dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            with error:
                return error.code, _header_dict(error.headers), error.read()
        except (urllib.error.URLError, OSError) as error:
            raise OracleError(f"GET {url} failed: {error}") from None

    def first_url(self, repo, per_page=100, extra=None):
        """Build the URL of the first listing request, exactly as the service forms it.

        Args:
            repo: ``owner/repo``.
            per_page: Page size (1..100).
            extra: Optional dict of additional query parameters appended after per_page.

        Returns:
            Absolute URL string.

        Raises:
            None.
        """
        query = {"state": "open", "sort": "created", "direction": "asc", "per_page": per_page}
        query.update(extra or {})
        return f"{self.base}/repos/{repo}/issues?{urllib.parse.urlencode(query)}"

    def fetch_pages(self, repo, per_page=100):
        """Read the whole issue listing of a repository by following ``Link rel="next"``.

        Args:
            repo: ``owner/repo``.
            per_page: Page size used for the first request.

        Returns:
            List of dicts {url, status, headers, items}. A non-200 status ends the walk and yields
            ``items == []`` for that page (so a 404 repository returns one page with status 404).

        Raises:
            ServerViolation: A 200 body is not a JSON array of objects, the Link chain loops or leaves
                the base origin, or it exceeds MAX_PAGES pages.
            OracleError: A network-level failure occurred.
        """
        pages = []
        seen = set()
        url = self.first_url(repo, per_page)
        while url is not None:
            if url in seen:
                raise ServerViolation(f"Link chain loops back to {url}")
            if len(pages) >= MAX_PAGES:
                raise ServerViolation(f"more than {MAX_PAGES} pages for {repo}")
            seen.add(url)
            status, headers, body = self.get(url)
            items = []
            if status == 200:
                items = decode_items(body, url)
            pages.append({"url": url, "status": status, "headers": headers, "items": items})
            if status != 200:
                break
            nxt = parse_link_header(headers.get("link", "")).get("next")
            url = urllib.parse.urljoin(url, nxt) if nxt else None
        return pages


def decode_items(body, url="<unknown>"):
    """Decode a listing response body.

    Args:
        body: Response bytes.
        url: Request URL used in the error message.

    Returns:
        List of dicts (the issue objects).

    Raises:
        ServerViolation: The body is not UTF-8 JSON containing an array of objects.
    """
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise ServerViolation(f"response body of {url} is not valid JSON ({error})") from None
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise ServerViolation(f"response body of {url} is not a JSON array of objects")
    return data


def _header_dict(message):
    """Flatten an http.client message into a dict.

    Args:
        message: ``http.client.HTTPMessage`` (may be None).

    Returns:
        Dict with lower-case header names; repeated headers are joined with ", ".

    Raises:
        None.
    """
    result = {}
    if message is None:
        return result
    for name, value in message.items():
        key = name.lower()
        result[key] = f"{result[key]}, {value}" if key in result else value
    return result
