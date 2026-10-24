"""Meta-tests of helpers.env and of the shared fixtures in conftest.py."""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from helpers import env as envmod
from helpers.classify import ProfileCache, RepoProfile
from helpers.oracle import OracleClient
from helpers.proxy import FaultProxy
from helpers.runner import Result

pytestmark = pytest.mark.selfcheck

API_TESTS = Path(__file__).resolve().parent.parent


def nested_pytest(tmp_path, test_source, environ):
    """Run pytest on a throw-away test file that uses the real conftest.py fixtures.

    Args:
        tmp_path: Empty directory for the generated files.
        test_source: Source text of the generated test module.
        environ: Environment variables for the nested run (replaces the inherited environment).

    Returns:
        subprocess.CompletedProcess with text stdout/stderr.

    Raises:
        subprocess.TimeoutExpired: The nested run exceeded 120 seconds.
    """
    (tmp_path / "conftest.py").write_text(textwrap.dedent(f"""
        import importlib.util, sys
        sys.path.insert(0, {str(API_TESTS)!r})
        spec = importlib.util.spec_from_file_location("real_conftest", {str(API_TESTS / "conftest.py")!r})
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        globals().update({{k: v for k, v in vars(module).items() if not k.startswith("__")}})
    """), encoding="utf-8")
    (tmp_path / "test_nested.py").write_text(textwrap.dedent(test_source), encoding="utf-8")
    clean = {k: v for k, v in os.environ.items()
             if not k.startswith(("GHREPORT_", "GITHUB_", "PYTEST_"))}
    clean.update(environ)
    return subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "-rsf", "--no-header",
                           str(tmp_path / "test_nested.py")],
                          cwd=tmp_path, env=clean, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=120, stdin=subprocess.DEVNULL)


# covers: load_env
def test_load_env_reads_valid_environment(tmp_path):
    binary = tmp_path / "ghreport"
    binary.write_text("x")
    loaded = envmod.load_env({"GHREPORT_BIN": str(binary), "GITHUB_API_BASE_URL": "http://h", "GITHUB_TOKEN": "t"})
    assert loaded == envmod.Env(str(binary), "http://h", "t")
    assert envmod.load_env({"GHREPORT_BIN": str(binary), "GITHUB_API_BASE_URL": "http://h",
                            "GITHUB_TOKEN": ""}).token is None


# covers: load_env
def test_load_env_names_every_problem(tmp_path):
    with pytest.raises(envmod.EnvError) as caught:
        envmod.load_env({})
    assert "GHREPORT_BIN is not set" in str(caught.value) and "GITHUB_API_BASE_URL is not set" in str(caught.value)
    with pytest.raises(envmod.EnvError, match="missing file"):
        envmod.load_env({"GHREPORT_BIN": str(tmp_path / "nope"), "GITHUB_API_BASE_URL": "http://h"})
    with pytest.raises(envmod.EnvError, match="GITHUB_API_BASE_URL"):
        envmod.load_env({"GHREPORT_BIN": sys.executable})


# covers: load_env
def test_load_env_defaults_to_process_environment(monkeypatch):
    monkeypatch.setenv("GHREPORT_BIN", sys.executable)
    monkeypatch.setenv("GITHUB_API_BASE_URL", "http://x")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert envmod.load_env() == envmod.Env(sys.executable, "http://x", None)


# covers: parse_test_repos
def test_parse_test_repos_default_dedup_and_blanks(monkeypatch):
    assert envmod.parse_test_repos({}) == ["stub/alpha"]
    assert envmod.parse_test_repos({"GHREPORT_TEST_REPOS": " , "}) == ["stub/alpha"]
    assert envmod.parse_test_repos({"GHREPORT_TEST_REPOS": "a/b, c/d ,a/b,,e/f"}) == ["a/b", "c/d", "e/f"]
    monkeypatch.setenv("GHREPORT_TEST_REPOS", "x/y")
    assert envmod.parse_test_repos() == ["x/y"]


# covers: strict_mode
def test_strict_mode_only_for_one(monkeypatch):
    assert envmod.strict_mode({"GHREPORT_STRICT": "1"})
    assert not envmod.strict_mode({"GHREPORT_STRICT": "0"}) and not envmod.strict_mode({})
    assert not envmod.strict_mode({"GHREPORT_STRICT": "true"})
    monkeypatch.setenv("GHREPORT_STRICT", "1")
    assert envmod.strict_mode()


# covers: need
def test_need_passes_skips_or_fails():
    assert envmod.need(True, "r", {}) is None and envmod.need([1], "r", {}) is None
    with pytest.raises(pytest.skip.Exception, match="server data insufficient: not enough"):
        envmod.need(False, "not enough", {})
    with pytest.raises(pytest.fail.Exception, match=r"\(GHREPORT_STRICT=1\): not enough"):
        envmod.need(0, "not enough", {"GHREPORT_STRICT": "1"})


# covers: need
def test_need_fixture_is_the_gate(need):
    assert need is envmod.need
    need(True, "fine")


# covers: cache_dir
def test_cache_dir_is_private_and_not_precreated(cache_dir, tmp_path):
    assert cache_dir == tmp_path / "cache" and not cache_dir.exists()


# covers: env
def test_env_fixture_override_points_at_the_stub(env, stub):
    assert env.base_url == stub.url


# covers: oracle
def test_oracle_fixture_reads_the_stub(oracle, stub):
    assert isinstance(oracle, OracleClient) and oracle.base == stub.url
    assert len(oracle.fetch_pages("stub/small")[0]["items"]) == 3


# covers: proxy
def test_proxy_fixture_is_per_test_and_forwards(proxy, stub, oracle):
    assert isinstance(proxy, FaultProxy) and proxy.requests == []
    OracleClient(proxy.url).fetch_pages("stub/small")
    assert len(proxy.requests) == 1 and proxy.upstream == stub.url


# covers: proxy
def test_proxy_fixture_has_fresh_state_again(proxy):
    assert proxy.requests == [] and proxy.rules == []


# covers: ghreport, run
def test_ghreport_fixture_runs_the_binary_against_the_server(ghreport, cache_dir, stub):
    import json
    result = ghreport("a/b", cache_dir, "--limit", "2")
    assert isinstance(result, Result) and result.returncode == 0
    seen = json.loads(result.stdout)
    assert seen["base_url"] == stub.url and seen["argv"][-2:] == ["--limit", "2"]
    other = json.loads(ghreport("a/b", cache_dir, base_url="http://elsewhere", token="T").stdout)
    assert other["base_url"] == "http://elsewhere" and other["token"] == "T"


# covers: test_repos
def test_test_repos_fixture_default(test_repos, monkeypatch):
    assert test_repos == envmod.parse_test_repos()


# covers: profiles
def test_profiles_fixture_is_a_cache_over_the_oracle(profiles):
    assert isinstance(profiles, ProfileCache)
    assert profiles.get("stub/alpha") is profiles.get("stub/alpha")


# covers: _repo_fixture, fixture
def test_repo_fixtures_select_alpha(multipage_repo, pr_repo, since_repo, limit_repo, plain_repo):
    for chosen in (multipage_repo, pr_repo, since_repo, limit_repo, plain_repo):
        assert isinstance(chosen, RepoProfile) and chosen.repo == "stub/alpha"


# covers: _repo_fixture, fixture, need, env, test_repos
def test_repo_fixture_skips_when_no_repository_qualifies(tmp_path, stub):
    environ = {"GHREPORT_BIN": sys.executable, "GITHUB_API_BASE_URL": stub.url,
               "GHREPORT_TEST_REPOS": "stub/small,stub/empty"}
    source = """
        def test_needs_pages(multipage_repo):
            raise AssertionError("must not run")

        def test_has_issues(plain_repo):
            assert plain_repo.repo == "stub/small"
    """
    done = nested_pytest(tmp_path, source, environ)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "1 passed, 1 skipped" in done.stdout and "server data insufficient" in done.stdout


# covers: _repo_fixture, fixture, need
def test_repo_fixture_fails_in_strict_mode(tmp_path, stub):
    environ = {"GHREPORT_BIN": sys.executable, "GITHUB_API_BASE_URL": stub.url,
               "GHREPORT_TEST_REPOS": "stub/small", "GHREPORT_STRICT": "1"}
    done = nested_pytest(tmp_path, "def test_needs_pages(multipage_repo):\n    pass\n", environ)
    assert done.returncode == 1 and "1 error" in done.stdout
    assert "GHREPORT_STRICT=1" in done.stdout and "no repository in GHREPORT_TEST_REPOS" in done.stdout
    assert "Traceback" not in done.stdout


# covers: env
def test_missing_environment_fails_with_a_clear_message_and_no_traceback(tmp_path):
    done = nested_pytest(tmp_path, "def test_x(env):\n    pass\n\ndef test_y(oracle):\n    pass\n", {})
    assert done.returncode == 1 and "2 errors" in done.stdout
    assert "GHREPORT_BIN is not set" in done.stdout and "GITHUB_API_BASE_URL is not set" in done.stdout
    assert "Traceback" not in done.stdout


# covers: env
def test_environment_error_for_a_missing_binary(tmp_path, stub):
    environ = {"GHREPORT_BIN": str(tmp_path / "absent"), "GITHUB_API_BASE_URL": stub.url}
    done = nested_pytest(tmp_path, "def test_x(env):\n    pass\n", environ)
    assert done.returncode == 1 and "missing file" in done.stdout


# covers: env, oracle, proxy, ghreport, cache_dir, profiles
def test_real_fixtures_work_together_in_a_nested_session(tmp_path, stub):
    environ = {"GHREPORT_BIN": sys.executable, "GITHUB_API_BASE_URL": stub.url}
    source = """
        def test_real(env, oracle, proxy, cache_dir, profiles, test_repos):
            assert env.base_url == oracle.base and proxy.url != env.base_url
            assert cache_dir.name == "cache" and test_repos == ["stub/alpha"]
            assert profiles.get("stub/alpha").multi_page
    """
    done = nested_pytest(tmp_path, source, environ)
    assert done.returncode == 0, done.stdout + done.stderr
