"""Contract-compliant fake GitHub issues server (stdlib only) with switchable contract violations.

Repositories: ``stub/alpha`` (250 items: issues and pull requests, labels, unicode titles, mixed UTC
offsets), ``stub/small`` (3 issues), ``stub/empty`` (no items); every other repository answers 404.
The token ``invalid`` is rejected with 401. Run it as a context manager (``with StubServer(): ...``)
or as a process (``python stub_server.py --port 8765 --flags dup_items``).
"""

import argparse
import hashlib
import json
import subprocess
import sys
import threading
import urllib.parse
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

#: Flags that keep the server contract-compliant (legitimate behaviour the service must tolerate).
VALID_FLAGS = ("dup_items",)
#: Flags that turn the server into a contract-breaking mutant (used to prove the suite notices).
VIOLATION_FLAGS = ("drop_item_between_pages", "pr_without_flag", "always_304", "broken_json",
                   "reversed_sort", "no_link")
ALL_FLAGS = VALID_FLAGS + VIOLATION_FLAGS

INVALID_TOKEN = "invalid"
_BASE_TIME = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
_PLUS_THREE = timezone(timedelta(hours=3))


def parse_flags(text):
    """Parse a comma-separated flag list.

    Args:
        text: Flag names separated by commas (blank entries ignored); None or "" means no flags.

    Returns:
        frozenset of flag names.

    Raises:
        ValueError: A name is not in ALL_FLAGS.
    """
    flags = frozenset(part.strip() for part in (text or "").split(",") if part.strip())
    unknown = flags - set(ALL_FLAGS)
    if unknown:
        raise ValueError(f"unknown stub flags: {sorted(unknown)}; known: {list(ALL_FLAGS)}")
    return flags


def format_created_at(number):
    """Compute the created_at string of item ``number``.

    Args:
        number: Item number (>= 1); the item is created ``6 * number`` hours after 2026-01-01T00:00:00Z.

    Returns:
        RFC 3339 text; every seventh item uses the +03:00 offset (same instant), the others use ``Z``.

    Raises:
        None.
    """
    moment = _BASE_TIME + timedelta(hours=6 * number)
    if number % 7 == 0:
        return moment.astimezone(_PLUS_THREE).strftime("%Y-%m-%dT%H:%M:%S+03:00")
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def make_item(repo, number, is_pull_request, labelled=True, unicode_title=False):
    """Build one API issue object.

    Args:
        repo: ``owner/repo`` used in html_url.
        number: Issue number (>= 1).
        is_pull_request: True to add the ``pull_request`` key and a /pull/ html_url.
        labelled: False yields an empty labels array.
        unicode_title: True makes the title contain Cyrillic text and an emoji.

    Returns:
        Dict shaped like a GitHub issue object.

    Raises:
        None.
    """
    kind = "pull" if is_pull_request else "issues"
    item = {
        "number": number,
        "title": f"Проблема №{number} — ёлка 🎄" if unicode_title else f"Issue {number}",
        "user": {"login": f"user{number % 5}"},
        "labels": [{"name": "bug"}, {"name": "помощь"}] if labelled else [],
        "state": "open",
        "created_at": format_created_at(number),
        "html_url": f"https://github.example/{repo}/{kind}/{number}",
    }
    if is_pull_request:
        item["pull_request"] = {"url": f"https://api.github.example/repos/{repo}/pulls/{number}"}
    return item


def build_repos():
    """Build the fixed data set of the stub.

    Returns:
        Dict repo -> list of items in ascending (created_at instant, number) order. ``stub/alpha``
        has 250 items (every fifth is a pull request), ``stub/small`` 3 issues, ``stub/empty`` none.

    Raises:
        None.
    """
    alpha = [make_item("stub/alpha", n, n % 5 == 0, labelled=n % 4 != 0, unicode_title=n % 3 == 0)
             for n in range(1, 251)]
    small = [make_item("stub/small", n, False) for n in (1, 2, 3)]
    return {"stub/alpha": alpha, "stub/small": small, "stub/empty": []}


def page_bounds(total, page, per_page, flags):
    """Compute which slice of the item list a page shows and how many pages exist.

    Args:
        total: Number of items.
        page: 1-based page number.
        per_page: Page size (>= 1).
        flags: Active flags; ``dup_items`` makes consecutive pages overlap by one item (a legitimate
            effect of concurrent inserts), ``drop_item_between_pages`` skips one item between pages.

    Returns:
        Tuple (start, end, last_page): the half-open slice and the number of the last page (>= 1).

    Raises:
        None.
    """
    stride = per_page
    if "dup_items" in flags:
        stride = max(1, per_page - 1)
    elif "drop_item_between_pages" in flags:
        stride = per_page + 1
    last = 1 if total <= per_page else -(-(total - per_page) // stride) + 1
    start = (page - 1) * stride
    return min(start, total), min(start + per_page, total), last


def etag_matches(header, etag):
    """Evaluate an If-None-Match header against the current ETag.

    Args:
        header: Header value (comma-separated list, ``*`` and weak ``W/`` tags allowed) or None.
        etag: Current strong ETag including quotes.

    Returns:
        True when the header selects the current representation.

    Raises:
        None.
    """
    if not header:
        return False
    for token in header.split(","):
        token = token.strip()
        if token == "*" or token.removeprefix("W/") == etag:
            return True
    return False


def link_header(host, path, query, page, last):
    """Build the Link header of a page.

    Args:
        host: ``host:port`` used for the absolute addresses (taken from the request's Host header).
        path: Request path.
        query: Ordered dict of the request's query parameters (single values).
        page: Current page number.
        last: Last page number.

    Returns:
        Header value with the relevant first/prev/next/last links, or "" when there is a single page.

    Raises:
        None.
    """
    if last <= 1 and page <= 1:
        return ""

    def address(number):
        """Absolute address of one page.

        Args:
            number: Page number.

        Returns:
            URL string with ``page=number`` appended to the original parameters.

        Raises:
            None.
        """
        params = dict(query)
        params["page"] = number
        return f"http://{host}{path}?{urllib.parse.urlencode(params)}"

    parts = []
    if page < last:
        parts.append(f'<{address(page + 1)}>; rel="next"')
        parts.append(f'<{address(last)}>; rel="last"')
    if page > 1:
        parts.append(f'<{address(1)}>; rel="first"')
        parts.append(f'<{address(page - 1)}>; rel="prev"')
    return ", ".join(parts)


def render_listing(items, path, query, host, flags, if_none_match):
    """Produce the answer to one listing request.

    Args:
        items: All items of the repository in ascending order.
        path: Request path.
        query: Ordered dict of the query parameters (single string values).
        host: ``host:port`` for absolute Link addresses.
        flags: Active flags (see ALL_FLAGS).
        if_none_match: Value of the If-None-Match request header or None.

    Returns:
        Tuple (status, headers dict, body bytes).

    Raises:
        None.
    """
    try:
        per_page = min(100, max(1, int(query.get("per_page", 30))))
    except ValueError:
        per_page = 30
    try:
        page = max(1, int(query.get("page", 1)))
    except ValueError:
        page = 1
    ordered = list(items)
    if "reversed_sort" in flags or query.get("direction") == "desc":
        ordered.reverse()
    start, end, last = page_bounds(len(ordered), page, per_page, flags)
    chosen = [dict(item) for item in ordered[start:end]]
    if "pr_without_flag" in flags:
        for item in chosen:
            item.pop("pull_request", None)
    body = json.dumps(chosen, ensure_ascii=False).encode("utf-8")
    if "broken_json" in flags:
        body = body[:-1]
    etag = '"' + hashlib.sha1(body).hexdigest()[:16] + '"'
    headers = {"ETag": etag, "Content-Type": "application/json; charset=utf-8",
               "X-RateLimit-Limit": "5000", "X-RateLimit-Remaining": "4999"}
    if "always_304" in flags or etag_matches(if_none_match, etag):
        return 304, {"ETag": etag}, b""
    link = "" if "no_link" in flags else link_header(host, path, query, page, last)
    if link:
        headers["Link"] = link
    return 200, headers, body


class _StubHandler(BaseHTTPRequestHandler):
    """Request handler of the stub; state lives in ``self.server.stub``."""

    protocol_version = "HTTP/1.0"

    def do_GET(self):
        """Answer a GET request.

        Returns:
            None.

        Raises:
            None.
        """
        stub = self.server.stub
        stub.record(self.path, dict(self.headers.items()))
        status, headers, body = stub.answer(self.path, self.headers.get("Host"), self.headers)
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        if status != 304:
            self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if status != 304:
            self.wfile.write(body)

    def do_POST(self):
        """Reject non-GET methods: the stub is read-only.

        Returns:
            None.

        Raises:
            None.
        """
        self.send_response(405)
        self.send_header("Allow", "GET")
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()

    do_PUT = do_DELETE = do_PATCH = do_POST

    def log_message(self, format, *args):
        """Silence the default access log.

        Args:
            format: printf-style format (unused).
            args: Format arguments (unused).

        Returns:
            None.

        Raises:
            None.
        """
        return None


class StubServer:
    """In-process stub server on 127.0.0.1 (context manager)."""

    def __init__(self, flags=(), port=0):
        """Configure a stopped server.

        Args:
            flags: Iterable of flag names or a comma-separated string (see ALL_FLAGS).
            port: TCP port; 0 lets the OS choose.

        Returns:
            None.

        Raises:
            ValueError: A flag is unknown.
        """
        self.flags = parse_flags(flags if isinstance(flags, str) else ",".join(flags))
        self.port = port
        self.repos = build_repos()
        self.url = None
        self._requests = []
        self._lock = threading.Lock()
        self._server = None
        self._thread = None

    def __enter__(self):
        """Start serving.

        Returns:
            This server; ``url`` holds its base address.

        Raises:
            OSError: The port cannot be bound.
        """
        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), _StubHandler)
        self._server.daemon_threads = True
        self._server.stub = self
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.05},
                                        daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback):
        """Stop serving.

        Args:
            exc_type: Exception type from the with-block, or None.
            exc: Exception instance, or None.
            traceback: Traceback, or None.

        Returns:
            False (exceptions are never suppressed).

        Raises:
            None.
        """
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        return False

    @property
    def requests(self):
        """Journal of received requests.

        Returns:
            Snapshot list of (target, headers dict) in arrival order.

        Raises:
            None.
        """
        with self._lock:
            return list(self._requests)

    def record(self, target, headers):
        """Append a journal entry.

        Args:
            target: Request target (path plus query).
            headers: Request headers as a dict.

        Returns:
            None.

        Raises:
            None.
        """
        with self._lock:
            self._requests.append((target, headers))

    def answer(self, target, host, headers):
        """Compute the response to a GET request.

        Args:
            target: Request target (path plus query).
            host: Value of the Host header (None falls back to the server's own address).
            headers: Request headers (mapping with case-insensitive ``get``).

        Returns:
            Tuple (status, headers dict, body bytes): 401 for the token ``invalid``, 404 for unknown
            paths and repositories, otherwise the listing from render_listing.

        Raises:
            None.
        """
        if (headers.get("Authorization") or "").strip() == f"Bearer {INVALID_TOKEN}":
            return 401, {"Content-Type": "application/json"}, b'{"message":"Bad credentials"}'
        parts = urllib.parse.urlsplit(target)
        segments = parts.path.strip("/").split("/")
        if len(segments) != 4 or segments[0] != "repos" or segments[3] != "issues" \
                or f"{segments[1]}/{segments[2]}" not in self.repos:
            return 404, {"Content-Type": "application/json"}, b'{"message":"Not Found"}'
        query = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
        authority = host or self.url.removeprefix("http://")
        return render_listing(self.repos[f"{segments[1]}/{segments[2]}"], parts.path, query, authority,
                              self.flags, headers.get("If-None-Match"))


def spawn_process(flags=(), port=0, timeout=15.0):
    """Start the stub as a separate process.

    Args:
        flags: Iterable of flag names (see ALL_FLAGS).
        port: TCP port, 0 for an OS-chosen one.
        timeout: Seconds to wait for the "READY" line before the process is killed.

    Returns:
        Tuple (subprocess.Popen, base URL). The caller must terminate the process.

    Raises:
        RuntimeError: The process did not announce its address in time or exited early.
    """
    command = [sys.executable, __file__, "--port", str(port), "--flags", ",".join(flags)]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, text=True, stdin=subprocess.DEVNULL)
    watchdog = threading.Timer(timeout, process.kill)
    watchdog.start()
    try:
        line = process.stdout.readline().strip()
    finally:
        watchdog.cancel()
    if not line.startswith("READY "):
        process.kill()
        raise RuntimeError(f"stub server did not start: {line!r}")
    return process, line.removeprefix("READY ")


def main(argv=None):
    """Command-line entry point: serve until interrupted.

    Args:
        argv: Argument list without the program name; None means sys.argv[1:].

    Returns:
        0 after a clean interrupt, 2 for invalid flags.

    Raises:
        SystemExit: argparse exits on malformed command lines.
    """
    parser = argparse.ArgumentParser(description="Fake GitHub issues server for the ghreport test suite")
    parser.add_argument("--port", type=int, default=0, help="TCP port (0 = any free port)")
    parser.add_argument("--flags", default="", help=f"comma-separated flags: {', '.join(ALL_FLAGS)}")
    args = parser.parse_args(argv)
    try:
        server = StubServer(args.flags, args.port)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2
    with server:
        print(f"READY {server.url}", flush=True)
        try:
            server._thread.join()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
