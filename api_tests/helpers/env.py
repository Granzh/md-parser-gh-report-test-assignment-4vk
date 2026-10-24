"""Test-environment configuration: required variables and the "not enough server data" policy."""

import os
from dataclasses import dataclass

import pytest

DEFAULT_TEST_REPOS = ("stub/alpha",)


class EnvError(Exception):
    """The environment lacks variables the suite cannot run without."""


@dataclass(frozen=True)
class Env:
    """Validated environment of the suite.

    Attributes:
        binary: Absolute path to the ghreport executable.
        base_url: GITHUB_API_BASE_URL of the server under test.
        token: GITHUB_TOKEN or None when the variable is absent or empty.
    """

    binary: str
    base_url: str
    token: str = None


def load_env(environ=None):
    """Read and validate the environment variables of the suite.

    Args:
        environ: Mapping to read from; defaults to os.environ.

    Returns:
        Env with binary, base_url and token.

    Raises:
        EnvError: GHREPORT_BIN or GITHUB_API_BASE_URL is missing, or GHREPORT_BIN is not an
            existing file. The message names every problem.
    """
    source = os.environ if environ is None else environ
    problems = []
    binary = source.get("GHREPORT_BIN", "")
    if not binary:
        problems.append("GHREPORT_BIN is not set (absolute path to the built ghreport binary)")
    elif not os.path.isfile(binary):
        problems.append(f"GHREPORT_BIN points to a missing file: {binary}")
    base_url = source.get("GITHUB_API_BASE_URL", "")
    if not base_url:
        problems.append("GITHUB_API_BASE_URL is not set (base address of a contract-compliant server)")
    if problems:
        raise EnvError("; ".join(problems))
    return Env(binary, base_url, source.get("GITHUB_TOKEN") or None)


def parse_test_repos(environ=None):
    """Read the list of repositories the suite may use.

    Args:
        environ: Mapping to read from; defaults to os.environ.

    Returns:
        List of ``owner/repo`` strings from GHREPORT_TEST_REPOS (comma-separated, blanks ignored,
        duplicates removed keeping the first); ``["stub/alpha"]`` when the variable is unset or empty.

    Raises:
        None.
    """
    source = os.environ if environ is None else environ
    raw = source.get("GHREPORT_TEST_REPOS", "")
    repos = []
    for part in raw.split(","):
        name = part.strip()
        if name and name not in repos:
            repos.append(name)
    return repos or list(DEFAULT_TEST_REPOS)


def strict_mode(environ=None):
    """Tell whether missing server data must fail instead of skip.

    Args:
        environ: Mapping to read from; defaults to os.environ.

    Returns:
        True when GHREPORT_STRICT equals "1".

    Raises:
        None.
    """
    source = os.environ if environ is None else environ
    return source.get("GHREPORT_STRICT") == "1"


def need(condition, reason, environ=None):
    """Require a property of the server's data, or skip/fail the test.

    Args:
        condition: Truthy when the server provides the data the test needs.
        reason: Explanation shown in the skip/fail message.
        environ: Mapping used to read GHREPORT_STRICT; defaults to os.environ.

    Returns:
        None when the condition holds.

    Raises:
        pytest.skip.Exception: The condition is false and GHREPORT_STRICT is not "1".
        pytest.fail.Exception: The condition is false and GHREPORT_STRICT is "1".
    """
    if condition:
        return
    if strict_mode(environ):
        pytest.fail(f"server data insufficient (GHREPORT_STRICT=1): {reason}", pytrace=False)
    pytest.skip(f"server data insufficient: {reason}")
