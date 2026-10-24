"""Small utilities shared by the selfcheck tests."""

import socket
import sys
import urllib.error
import urllib.request

from pathlib import Path

from helpers.oracle import _NoRedirect

#: Command prefix that starts the fake ghreport binary.
FAKE_BINARY = (sys.executable, str(Path(__file__).with_name("fake_ghreport.py")))


def http_request(url, method="GET", headers=None, timeout=10.0):
    """Perform one HTTP request without following redirects.

    Args:
        url: Absolute URL.
        method: HTTP method.
        headers: Optional dict of request headers.
        timeout: Seconds before the request is abandoned.

    Returns:
        Tuple (status, headers dict with lower-case names, body bytes).

    Raises:
        OSError: Connection-level failure (reset, refused, timeout).
    """
    data = b"" if method in ("POST", "PUT", "PATCH") else None
    request = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, {k.lower(): v for k, v in response.headers.items()}, response.read()
    except urllib.error.HTTPError as error:
        with error:
            return error.code, {k.lower(): v for k, v in error.headers.items()}, error.read()


def closed_port_url():
    """Produce a base URL on which nothing listens.

    Returns:
        ``http://127.0.0.1:<port>`` where the port was free a moment ago.

    Raises:
        OSError: No socket can be created.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}"


class FakeOracle:
    """Scripted stand-in for OracleClient: ``get`` pops prepared answers in order."""

    def __init__(self, answers):
        """Prepare the script.

        Args:
            answers: List of (status, headers dict, body bytes) returned by successive get calls.

        Returns:
            None.

        Raises:
            None.
        """
        self.answers = list(answers)
        self.calls = []

    def first_url(self, repo, per_page=100, extra=None):
        """Build a recognisable URL.

        Args:
            repo: ``owner/repo``.
            per_page: Page size.
            extra: Extra query parameters (ignored).

        Returns:
            A string naming the repository and page size.

        Raises:
            None.
        """
        return f"fake://{repo}?per_page={per_page}"

    def get(self, url, headers=None):
        """Return the next scripted answer.

        Args:
            url: Requested URL (recorded).
            headers: Request headers (recorded).

        Returns:
            Tuple (status, headers, body).

        Raises:
            IndexError: The script is exhausted.
        """
        self.calls.append((url, headers))
        return self.answers.pop(0)
