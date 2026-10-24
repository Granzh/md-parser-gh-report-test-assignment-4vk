"""Shared fixtures of the ghreport black-box suite.

Fixtures are lazy: nothing reads the environment at import time, so collection and the
``selfcheck`` meta-tests work without GHREPORT_BIN / GITHUB_API_BASE_URL.
"""

import functools

import pytest

from helpers import env as env_module
from helpers.classify import ProfileCache
from helpers.oracle import OracleClient
from helpers.proxy import FaultProxy
from helpers.runner import run_ghreport


@pytest.fixture(scope="session")
def env():
    """Validated environment (GHREPORT_BIN, GITHUB_API_BASE_URL, GITHUB_TOKEN).

    Returns:
        helpers.env.Env.

    Raises:
        pytest.fail.Exception: A required variable is missing; reported without a traceback.
    """
    problem = None
    try:
        return env_module.load_env()
    except env_module.EnvError as error:
        problem = str(error)
    pytest.fail(f"test environment is not configured: {problem}", pytrace=False)


@pytest.fixture
def cache_dir(tmp_path):
    """Private cache directory of one test (not created: the service creates it).

    Args:
        tmp_path: pytest's per-test temporary directory.

    Returns:
        ``tmp_path / "cache"`` as a Path.

    Raises:
        None.
    """
    return tmp_path / "cache"


@pytest.fixture(scope="session")
def oracle(env):
    """Read-only client of the server under test.

    Args:
        env: Validated environment.

    Returns:
        helpers.oracle.OracleClient bound to GITHUB_API_BASE_URL and GITHUB_TOKEN.

    Raises:
        None.
    """
    return OracleClient(env.base_url, env.token)


@pytest.fixture
def proxy(env):
    """Fault proxy in front of the server, one per test.

    Args:
        env: Validated environment.

    Returns:
        Started helpers.proxy.FaultProxy (stopped after the test); use ``proxy.url`` as base_url.

    Raises:
        None.
    """
    with FaultProxy(env.base_url) as running:
        yield running


@pytest.fixture
def ghreport(env):
    """Callable that runs the service against the server with the suite's environment.

    Args:
        env: Validated environment.

    Returns:
        Function ``(repos, cache_dir, *args, **kwargs) -> Result`` (see helpers.runner.run_ghreport)
        with the binary and base URL preset; ``base_url=`` overrides the server (e.g. a proxy URL).

    Raises:
        None.
    """
    def run(repos, cache_dir, *args, **kwargs):
        """Run the service once.

        Args:
            repos: Value of --repos.
            cache_dir: Cache directory.
            args: Extra command-line arguments.
            kwargs: Keyword arguments of run_ghreport; ``base_url`` defaults to the server address.

        Returns:
            helpers.runner.Result.

        Raises:
            RunnerTimeout: The service did not finish in time.
        """
        kwargs.setdefault("base_url", env.base_url)
        kwargs.setdefault("binary", env.binary)
        return run_ghreport(repos, cache_dir, *args, **kwargs)

    return run


@pytest.fixture(scope="session")
def test_repos():
    """Repositories the suite may read, from GHREPORT_TEST_REPOS.

    Returns:
        List of ``owner/repo``; ``["stub/alpha"]`` by default.

    Raises:
        None.
    """
    return env_module.parse_test_repos()


@pytest.fixture(scope="session")
def profiles(oracle):
    """Session cache of repository profiles computed from the oracle.

    Args:
        oracle: Read-only client.

    Returns:
        helpers.classify.ProfileCache.

    Raises:
        None.
    """
    return ProfileCache(oracle)


@pytest.fixture
def need():
    """The "not enough server data" gate.

    Returns:
        Function ``need(condition, reason)``: does nothing when the condition holds, otherwise
        skips the test, or fails it when GHREPORT_STRICT=1.

    Raises:
        None.
    """
    return env_module.need


def _repo_fixture(name, predicate, description):
    """Build a fixture returning a repository profile that satisfies a condition.

    Args:
        name: Fixture name under which pytest registers it.
        predicate: Callable taking a RepoProfile and returning bool.
        description: Text for the skip/fail reason ("no repository with ...").

    Returns:
        A pytest fixture function with parameters ``profiles``, ``test_repos`` and ``need``.

    Raises:
        None.
    """
    @pytest.fixture(name=name)
    def fixture(profiles, test_repos, need):
        """Select a suitable repository.

        Args:
            profiles: Profile cache.
            test_repos: Candidate repositories.
            need: Skip/fail gate.

        Returns:
            RepoProfile of the first candidate that satisfies the condition.

        Raises:
            pytest.skip.Exception: No candidate qualifies (pytest.fail.Exception under GHREPORT_STRICT=1).
        """
        found = profiles.find(test_repos, predicate)
        need(found is not None, f"no repository in GHREPORT_TEST_REPOS with {description}")
        return found

    return fixture


multipage_repo = _repo_fixture("multipage_repo", lambda p: p.multi_page and p.eligible >= 2, "several pages of issues")
pr_repo = _repo_fixture("pr_repo", lambda p: p.has_pull_requests and p.eligible >= 1, "pull requests among the items")
since_repo = _repo_fixture("since_repo", lambda p: len(p.dates) >= 2, "issues created on at least two different dates")
limit_repo = _repo_fixture("limit_repo", lambda p: p.multi_page and p.first_page_eligible + 1 <= p.eligible,
                           "more eligible issues than fit on the first page")
plain_repo = _repo_fixture("plain_repo", lambda p: p.eligible >= 1, "at least one open issue")
