"""Fault-injecting forwarding proxy placed between ghreport and the server under test.

The service is started with GITHUB_API_BASE_URL pointing at the proxy. The proxy forwards GET
requests to the real upstream, keeps a journal of every request the service made and can inject
faults (rate limiting, 5xx, broken bodies, redirects, dropped connections, delays, rewritten Link
headers). Link and Location headers coming from upstream are rewritten to the proxy address so the
service can never bypass the proxy.
"""

import http.client
import json
import re
import secrets
import socket
import struct
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

#: Sentinel returned by a ``before`` hook to abort the connection without any response.
RESET = object()

_HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
               "transfer-encoding", "upgrade", "content-length", "host", "accept-encoding"}
_LINK_URL = re.compile(r"<([^>]*)>([^<]*)")
_REL_VALUE = re.compile(r'rel\s*=\s*(?:"([^"]*)"|([^;,\s]*))', re.IGNORECASE)


@dataclass(eq=False)
class RequestRecord:
    """Journal entry for one request received by the proxy.

    Attributes:
        method: HTTP method as sent by the client.
        target: Request target (path plus query) exactly as received.
        headers: Request headers with lower-case names (last value wins).
        started: ``time.monotonic()`` value when the request arrived.
        overlapped: True when another request was in flight at any moment during this one.
        cursor: Value of the ``cursor`` query parameter, if the client sent one.
        cursor_valid: None for ordinary requests, True for a continuation with a cursor the proxy
            issued, False for a continuation that did not honour the opaque cursor.
        status: Status code sent back to the client (None until the response is produced).
        injected: True when a rule produced the response instead of the upstream.
    """

    method: str
    target: str
    headers: dict
    started: float
    overlapped: bool = False
    cursor: str = None
    cursor_valid: bool = None
    status: int = None
    injected: bool = False

    @property
    def path(self):
        """Path component of the target.

        Returns:
            Request path without the query string.

        Raises:
            None.
        """
        return urllib.parse.urlsplit(self.target).path

    def param(self, name):
        """Read one query parameter.

        Args:
            name: Parameter name (case-sensitive).

        Returns:
            First value of the parameter, or None when it is absent.

        Raises:
            None.
        """
        values = urllib.parse.parse_qs(urllib.parse.urlsplit(self.target).query, keep_blank_values=True)
        found = values.get(name)
        return found[0] if found else None


@dataclass
class ProxyResponse:
    """Response the proxy is about to send to the client.

    Attributes:
        status: HTTP status code.
        headers: List of (name, value) pairs; Content-Length is computed on send.
        body: Response body bytes.
    """

    status: int
    headers: list = field(default_factory=list)
    body: bytes = b""


@dataclass
class RequestContext:
    """State handed to rule actions while one request is processed.

    Attributes:
        proxy: The owning FaultProxy.
        record: Journal entry of the request.
        headers: Mutable dict (lower-case names) of the headers forwarded upstream.
        target: Mutable request target forwarded upstream.
    """

    proxy: object
    record: RequestRecord
    headers: dict
    target: str


@dataclass
class Action:
    """A fault injection step; either hook may be None.

    Attributes:
        before: ``before(ctx)`` runs before forwarding; returns a ProxyResponse or RESET to answer
            without contacting the upstream, or None to continue.
        after: ``after(ctx, response)`` runs on the upstream response and returns the (possibly
            modified) ProxyResponse.
    """

    before: object = None
    after: object = None


@dataclass
class Rule:
    """Pairs a request matcher with an action that fires a limited number of times.

    Attributes:
        match: None (always), str (substring of the target), compiled regex (search in the target)
            or callable taking a RequestRecord and returning bool.
        action: The Action to run.
        times: How many matching requests fire the rule; None means unlimited.
        hits: How many times the rule has fired so far.
    """

    match: object
    action: Action
    times: int = 1
    hits: int = 0

    def try_claim(self, record):
        """Check the matcher and consume one use of the rule.

        Args:
            record: Journal entry of the request being processed.

        Returns:
            True if the rule matches and still has uses left (one use is consumed), else False.

        Raises:
            None.
        """
        if self.times is not None and self.hits >= self.times:
            return False
        if not matches(self.match, record):
            return False
        self.hits += 1
        return True


def matches(match, record):
    """Evaluate a Rule matcher against a request.

    Args:
        match: None, str, compiled regex or callable (see Rule).
        record: RequestRecord to test.

    Returns:
        True when the request matches.

    Raises:
        TypeError: ``match`` has an unsupported type.
    """
    if match is None:
        return True
    if isinstance(match, str):
        return match in record.target
    if hasattr(match, "search"):
        return match.search(record.target) is not None
    if callable(match):
        return bool(match(record))
    raise TypeError(f"unsupported matcher: {match!r}")


def get_header(headers, name):
    """Find a header in a list of pairs.

    Args:
        headers: List of (name, value) tuples.
        name: Header name, case-insensitive.

    Returns:
        Value of the first header with that name, or None.

    Raises:
        None.
    """
    lowered = name.lower()
    for key, value in headers:
        if key.lower() == lowered:
            return value
    return None


def remove_header(headers, name):
    """Remove every occurrence of a header.

    Args:
        headers: List of (name, value) tuples.
        name: Header name, case-insensitive.

    Returns:
        New list without the header (the input is not modified).

    Raises:
        None.
    """
    lowered = name.lower()
    return [(key, value) for key, value in headers if key.lower() != lowered]


def set_header(headers, name, value):
    """Replace a header (removing duplicates) or append it.

    Args:
        headers: List of (name, value) tuples.
        name: Header name, case-insensitive.
        value: New header value.

    Returns:
        New list in which the header appears exactly once.

    Raises:
        None.
    """
    return remove_header(headers, name) + [(name, value)]


def retarget(value, old_base, new_base):
    """Replace the upstream base address by the proxy address inside a header value.

    Args:
        value: Header text (Link or Location).
        old_base: Upstream base URL without trailing slash.
        new_base: Proxy base URL without trailing slash.

    Returns:
        ``value`` with every occurrence of ``old_base`` replaced by ``new_base``.

    Raises:
        None.
    """
    return value.replace(old_base, new_base)


def map_link_urls(value, function):
    """Rewrite the URLs inside a Link header while keeping the rest of the text intact.

    Args:
        value: Link header text.
        function: ``function(url, rels)`` returning the replacement URL; ``rels`` is the list of
            lower-case rel names of that link.

    Returns:
        New header text.

    Raises:
        None.
    """
    def replace(match):
        """Rewrite one ``<url>params`` fragment.

        Args:
            match: Regex match of one link fragment.

        Returns:
            The fragment with its URL passed through ``function``.

        Raises:
            None.
        """
        rels = []
        for found in _REL_VALUE.finditer(match.group(2)):
            rels += (found.group(1) if found.group(1) is not None else found.group(2)).lower().split()
        return f"<{function(match.group(1), rels)}>{match.group(2)}"

    return _LINK_URL.sub(replace, value)


def strip_query_param(target, name):
    """Remove one query parameter from a request target without re-encoding the others.

    Args:
        target: Request target (path plus optional query).
        name: Parameter name to remove.

    Returns:
        Tuple (new target, value of the removed parameter or None). If the removal empties the
        query, the ``?`` is dropped too.

    Raises:
        None.
    """
    path, sep, query = target.partition("?")
    if not sep:
        return target, None
    kept, removed = [], None
    for piece in query.split("&"):
        key, _eq, val = piece.partition("=")
        if key == name and removed is None:
            removed = urllib.parse.unquote(val)
        elif key != name:
            kept.append(piece)
    return (path + "?" + "&".join(kept) if kept else path), removed


def add_query_param(url, name, value):
    """Append a query parameter to a URL.

    Args:
        url: Absolute URL or request target.
        name: Parameter name.
        value: Parameter value (percent-encoded here).

    Returns:
        URL with ``name=value`` appended to the query (before any fragment).

    Raises:
        None.
    """
    base, hash_sep, fragment = url.partition("#")
    joiner = "&" if "?" in base else "?"
    return f"{base}{joiner}{name}={urllib.parse.quote(value, safe='')}{hash_sep}{fragment}"


def _respond_before(status, headers, body, ctx):
    """``before`` hook of respond().

    Args:
        status: Status code to send.
        headers: Dict of response headers.
        body: Body bytes (or str, encoded as UTF-8).
        ctx: RequestContext.

    Returns:
        ProxyResponse with the given content.

    Raises:
        None.
    """
    payload = body.encode("utf-8") if isinstance(body, str) else body
    return ProxyResponse(status, list((headers or {}).items()), payload)


def _redirect_before(target, status, ctx):
    """``before`` hook of redirect().

    Args:
        target: Redirect target; a leading "/" is resolved against the proxy address.
        status: Redirect status code (301, 302, 307, ...).
        ctx: RequestContext.

    Returns:
        ProxyResponse with a Location header and an empty body.

    Raises:
        None.
    """
    location = ctx.proxy.url + target if target.startswith("/") else target
    return ProxyResponse(status, [("Location", location)], b"")


def _reset_before(ctx):
    """``before`` hook of reset_connection().

    Args:
        ctx: RequestContext.

    Returns:
        RESET, which makes the handler drop the connection without a response.

    Raises:
        None.
    """
    return RESET


def _delay_before(seconds, ctx):
    """``before`` hook of add_delay().

    Args:
        seconds: Pause length in seconds (non-negative).
        ctx: RequestContext.

    Returns:
        None (the request continues after the pause).

    Raises:
        None.
    """
    ctx.proxy.pause(seconds)
    return None


def _drop_request_header_before(name, ctx):
    """``before`` hook of drop_header(where="request").

    Args:
        name: Request header to remove from the forwarded request.
        ctx: RequestContext.

    Returns:
        None (the request continues without the header).

    Raises:
        None.
    """
    ctx.headers.pop(name.lower(), None)
    return None


def _drop_response_header_after(name, ctx, response):
    """``after`` hook of drop_header(where="response").

    Args:
        name: Response header to remove.
        ctx: RequestContext.
        response: ProxyResponse from the upstream.

    Returns:
        The response without that header.

    Raises:
        None.
    """
    response.headers = remove_header(response.headers, name)
    return response


def _rewrite_body_after(function, ctx, response):
    """``after`` hook of rewrite_body().

    Args:
        function: Callable taking the body bytes and returning new bytes.
        ctx: RequestContext.
        response: ProxyResponse from the upstream.

    Returns:
        The response with the transformed body.

    Raises:
        None.
    """
    response.body = function(response.body)
    return response


def _replace_link_after(function, ctx, response):
    """``after`` hook of replace_link().

    Args:
        function: Callable taking the Link header text (or None) and returning new text or None
            (None removes the header).
        ctx: RequestContext.
        response: ProxyResponse from the upstream.

    Returns:
        The response with the rewritten Link header.

    Raises:
        None.
    """
    new_value = function(get_header(response.headers, "link"))
    response.headers = remove_header(response.headers, "link")
    if new_value is not None:
        response.headers.append(("Link", new_value))
    return response


def _cursor_before(param, strict, ctx):
    """``before`` hook of rewrite_link(): validate and strip the opaque cursor.

    Args:
        param: Name of the cursor query parameter.
        strict: When True a continuation request without a valid cursor gets a 400 response.
        ctx: RequestContext; its target loses the cursor parameter.

    Returns:
        ProxyResponse (400) for a strict violation, otherwise None.

    Raises:
        None.
    """
    stripped, token = strip_query_param(ctx.target, param)
    record = ctx.record
    proxy = ctx.proxy
    record.cursor = token
    if token is not None:
        issued = proxy.issued_cursors.get(token)
        record.cursor_valid = issued is not None and issued == stripped
        ctx.target = stripped
    elif ctx.target in proxy.issued_targets:
        record.cursor_valid = False
    if record.cursor_valid is False and strict:
        body = json.dumps({"message": "opaque cursor required: page addresses must be taken from the Link header"})
        return ProxyResponse(400, [("Content-Type", "application/json")], body.encode("utf-8"))
    return None


def _cursor_after(param, ctx, response):
    """``after`` hook of rewrite_link(): attach a fresh random cursor to every next link.

    Args:
        param: Name of the cursor query parameter.
        ctx: RequestContext.
        response: ProxyResponse whose Link header already points at the proxy.

    Returns:
        The response with ``param=<random>`` appended to each rel="next" URL; the proxy remembers
        which cursor belongs to which continuation target.

    Raises:
        None.
    """
    proxy = ctx.proxy
    updated = []
    for name, value in response.headers:
        if name.lower() != "link":
            updated.append((name, value))
            continue

        def attach(url, rels):
            """Add the cursor to a next link.

            Args:
                url: Link URL (already on the proxy address).
                rels: Lower-case rel names of the link.

            Returns:
                The URL, with a cursor appended for rel="next".

            Raises:
                None.
            """
            if "next" not in rels:
                return url
            token = secrets.token_hex(8)
            proxy.register_cursor(token, url[len(proxy.url):] if url.startswith(proxy.url) else url)
            return add_query_param(url, param, token)

        updated.append((name, map_link_urls(value, attach)))
    response.headers = updated
    return response


def respond(status, headers=None, body=b""):
    """Answer with a fixed response instead of contacting the upstream.

    Args:
        status: HTTP status code.
        headers: Optional dict of response headers (for example Retry-After).
        body: Body as bytes or str.

    Returns:
        Action with a ``before`` hook.

    Raises:
        None.
    """
    return Action(before=partial(_respond_before, status, headers, body))


def drop_header(name, where="response"):
    """Remove a header from the upstream response or from the forwarded request.

    Args:
        name: Header name, case-insensitive.
        where: "response" (default) or "request".

    Returns:
        Action removing the header.

    Raises:
        ValueError: ``where`` is neither "response" nor "request".
    """
    if where == "response":
        return Action(after=partial(_drop_response_header_after, name))
    if where == "request":
        return Action(before=partial(_drop_request_header_before, name))
    raise ValueError(f"where must be 'response' or 'request', got {where!r}")


def rewrite_body(function):
    """Transform the body of the upstream response.

    Args:
        function: Callable taking and returning bytes.

    Returns:
        Action with an ``after`` hook.

    Raises:
        None.
    """
    return Action(after=partial(_rewrite_body_after, function))


def replace_link(function):
    """Substitute the Link header of the upstream response.

    Args:
        function: Callable taking the current Link text (None if absent) and returning the new text,
            or None to remove the header.

    Returns:
        Action with an ``after`` hook.

    Raises:
        None.
    """
    return Action(after=partial(_replace_link_after, function))


def rewrite_link(param="cursor", strict=True):
    """Make next-page addresses opaque: prove that the client takes them from the Link header.

    Args:
        param: Query parameter used as the opaque marker.
        strict: Answer 400 to continuation requests that lack the issued marker (default True);
            when False such requests are only flagged in the journal (cursor_valid=False).

    Returns:
        Action with both hooks. Add it to a rule with ``times=None`` so it covers every page.

    Raises:
        None.
    """
    return Action(before=partial(_cursor_before, param, strict), after=partial(_cursor_after, param))


def redirect(to, status=302):
    """Answer with a redirect.

    Args:
        to: Target; "/path" is resolved against the proxy address, otherwise used verbatim.
        status: Redirect status (default 302).

    Returns:
        Action with a ``before`` hook.

    Raises:
        None.
    """
    return Action(before=partial(_redirect_before, to, status))


def reset_connection():
    """Drop the TCP connection (RST) without sending any response.

    Returns:
        Action with a ``before`` hook.

    Raises:
        None.
    """
    return Action(before=_reset_before)


def add_delay(seconds):
    """Pause before forwarding the request.

    Args:
        seconds: Pause length in seconds (non-negative float).

    Returns:
        Action with a ``before`` hook.

    Raises:
        None.
    """
    return Action(before=partial(_delay_before, seconds))


class _Handler(BaseHTTPRequestHandler):
    """HTTP handler that delegates every request to FaultProxy.process."""

    protocol_version = "HTTP/1.0"

    def handle_any(self):
        """Process one request, whatever its method.

        Returns:
            None.

        Raises:
            None.
        """
        owner = self.server.owner
        record = owner.begin(self.command, self.path, self.headers)
        try:
            outcome = owner.process(record)
            if outcome is RESET:
                record.status = 0
                self.abort()
            else:
                record.status = outcome.status
                self.send_outcome(outcome)
        finally:
            owner.end(record)

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = handle_any

    def send_outcome(self, outcome):
        """Write a ProxyResponse to the client.

        Args:
            outcome: ProxyResponse to send.

        Returns:
            None.

        Raises:
            None.
        """
        self.send_response(outcome.status)
        for name, value in outcome.headers:
            if name.lower() not in _HOP_BY_HOP:
                self.send_header(name, value)
        has_body = outcome.status not in (204, 304) and outcome.status >= 200
        if has_body:
            self.send_header("Content-Length", str(len(outcome.body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if has_body and self.command != "HEAD":
            try:
                self.wfile.write(outcome.body)
            except OSError:
                pass

    def abort(self):
        """Close the connection abruptly (TCP RST) without a response.

        Returns:
            None.

        Raises:
            None.
        """
        try:
            self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        except OSError:
            pass
        self.close_connection = True
        self.connection.close()

    def log_message(self, format, *args):
        """Silence the default stderr access log.

        Args:
            format: printf-style format (unused).
            args: Format arguments (unused).

        Returns:
            None.

        Raises:
            None.
        """
        return None


class FaultProxy:
    """Forwarding proxy with a request journal and fault-injection rules (context manager)."""

    def __init__(self, upstream, timeout=30.0):
        """Create a stopped proxy.

        Args:
            upstream: Base URL of the real server (http or https, optional path prefix).
            timeout: Seconds allowed for each upstream request.

        Returns:
            None.

        Raises:
            ValueError: ``upstream`` is not an http(s) URL with a host.
        """
        parts = urllib.parse.urlsplit(upstream)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError(f"upstream must be http(s) with a host: {upstream!r}")
        self._parts = parts
        self.upstream = upstream.rstrip("/")
        self.timeout = timeout
        self.rules = []
        self.issued_cursors = {}
        self.issued_targets = set()
        self._requests = []
        self._inflight = set()
        self._lock = threading.Lock()
        self._closing = threading.Event()
        self._server = None
        self._thread = None
        self.url = None

    def __enter__(self):
        """Start serving on 127.0.0.1 with an OS-assigned port.

        Returns:
            This proxy; ``url`` holds its base address.

        Raises:
            OSError: The listening socket cannot be created.
        """
        self._closing.clear()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.daemon_threads = True
        self._server.owner = self
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.05},
                                        daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback):
        """Stop serving and wake any pending injected delays.

        Args:
            exc_type: Exception type from the with-block, or None.
            exc: Exception instance, or None.
            traceback: Traceback, or None.

        Returns:
            False (exceptions are never suppressed).

        Raises:
            None.
        """
        self._closing.set()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        return False

    @property
    def requests(self):
        """Journal of received requests.

        Returns:
            Snapshot list of RequestRecord in arrival order.

        Raises:
            None.
        """
        with self._lock:
            return list(self._requests)

    def targets(self):
        """List the request targets seen so far.

        Returns:
            List of path+query strings in arrival order.

        Raises:
            None.
        """
        return [record.target for record in self.requests]

    def clear_requests(self):
        """Forget the journal.

        Returns:
            None.

        Raises:
            None.
        """
        with self._lock:
            self._requests.clear()

    def add_rule(self, match, action, times=1):
        """Register a fault-injection rule (rules are tried in registration order).

        Args:
            match: Matcher (see Rule): None, substring, regex or callable.
            action: Action built by respond, drop_header, rewrite_body, replace_link, rewrite_link,
                redirect, reset_connection or add_delay.
            times: Uses of the rule, None for unlimited.

        Returns:
            The created Rule (its ``hits`` counter shows how often it fired).

        Raises:
            None.
        """
        rule = Rule(match, action, times)
        with self._lock:
            self.rules.append(rule)
        return rule

    def clear_rules(self):
        """Remove all rules.

        Returns:
            None.

        Raises:
            None.
        """
        with self._lock:
            self.rules.clear()

    def pause(self, seconds):
        """Wait for an injected delay; ends early when the proxy is shutting down.

        Args:
            seconds: Delay in seconds (non-negative).

        Returns:
            None.

        Raises:
            None.
        """
        # Emulated server latency: the delay is the behaviour being injected, not test synchronisation.
        self._closing.wait(seconds)

    def register_cursor(self, token, target):
        """Remember an issued opaque cursor.

        Args:
            token: Cursor value handed to the client.
            target: Path+query (without the cursor) of the continuation it protects.

        Returns:
            None.

        Raises:
            None.
        """
        with self._lock:
            self.issued_cursors[token] = target
            self.issued_targets.add(target)

    def begin(self, method, target, headers):
        """Journal a new request and update overlap tracking.

        Args:
            method: HTTP method.
            target: Request target.
            headers: ``email.message.Message`` of request headers.

        Returns:
            The new RequestRecord.

        Raises:
            None.
        """
        record = RequestRecord(method, target, {key.lower(): value for key, value in headers.items()},
                               time.monotonic())
        with self._lock:
            if self._inflight:
                record.overlapped = True
                for other in self._inflight:
                    other.overlapped = True
            self._inflight.add(record)
            self._requests.append(record)
        return record

    def end(self, record):
        """Mark a request as finished.

        Args:
            record: RequestRecord returned by begin.

        Returns:
            None.

        Raises:
            None.
        """
        with self._lock:
            self._inflight.discard(record)

    def process(self, record):
        """Decide the answer to one request: run rules, forward upstream, post-process.

        Args:
            record: RequestRecord of the request.

        Returns:
            ProxyResponse, or RESET to drop the connection.

        Raises:
            None.
        """
        if record.method != "GET":
            record.injected = True
            return ProxyResponse(405, [("Allow", "GET")], b'{"message":"read-only proxy"}')
        forwarded = {k: v for k, v in record.headers.items() if k not in _HOP_BY_HOP}
        ctx = RequestContext(self, record, forwarded, record.target)
        afters = []
        with self._lock:
            candidates = list(self.rules)
        for rule in candidates:
            with self._lock:
                if not rule.try_claim(record):
                    continue
            if rule.action.before is not None:
                outcome = rule.action.before(ctx)
                if outcome is not None:
                    record.injected = True
                    return outcome
            if rule.action.after is not None:
                afters.append(rule.action)
        response = self.forward(ctx)
        for action in afters:
            response = action.after(ctx, response)
        return response

    def forward(self, ctx):
        """Send the request to the upstream and rewrite addresses in the answer.

        Args:
            ctx: RequestContext holding the target and headers to forward.

        Returns:
            ProxyResponse built from the upstream answer; Link and Location values point at the proxy.
            A 502 ProxyResponse if the upstream cannot be reached.

        Raises:
            None.
        """
        parts = self._parts
        factory = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
        headers = dict(ctx.headers)
        headers["accept-encoding"] = "identity"
        headers["connection"] = "close"
        try:
            connection = factory(parts.hostname, parts.port, timeout=self.timeout)
            try:
                connection.request("GET", parts.path.rstrip("/") + ctx.target, headers=headers)
                answer = connection.getresponse()
                body = answer.read()
                pairs = answer.getheaders()
                status = answer.status
            finally:
                connection.close()
        except (OSError, http.client.HTTPException) as error:
            ctx.record.injected = True
            return ProxyResponse(502, [], json.dumps({"message": f"upstream failure: {error}"}).encode("utf-8"))
        kept = []
        for name, value in pairs:
            lowered = name.lower()
            if lowered in _HOP_BY_HOP:
                continue
            if lowered in ("link", "location"):
                value = retarget(value, self.upstream, self.url)
            kept.append((name, value))
        return ProxyResponse(status, kept, body)
