"""Meta-tests of helpers.runner (the process runner) using a fake service binary."""

import json
import os

import pytest

from helpers import runner
from helpers.runner import KEEP_TOKEN, Result, RunnerError, RunnerTimeout
from support import FAKE_BINARY as FAKE

pytestmark = pytest.mark.selfcheck


def run_fake(tmp_path, *args, **kwargs):
    """Run the fake binary through run_ghreport.

    Args:
        tmp_path: Directory used for the cache path.
        args: Extra command-line arguments.
        kwargs: Keyword arguments of run_ghreport.

    Returns:
        Result of the run.

    Raises:
        RunnerError: As run_ghreport.
    """
    kwargs.setdefault("binary", FAKE)
    kwargs.setdefault("base_url", "http://127.0.0.1:1")
    return runner.run_ghreport("a/b,c/d", tmp_path / "cache", *args, **kwargs)


# covers: default_timeout
def test_default_timeout_reads_env_and_falls_back():
    assert runner.default_timeout({}) == 60.0
    assert runner.default_timeout({"GHREPORT_TIMEOUT": "7.5"}) == 7.5
    assert runner.default_timeout({"GHREPORT_TIMEOUT": "abc"}) == 60.0
    assert runner.default_timeout({"GHREPORT_TIMEOUT": "0"}) == 60.0
    assert runner.default_timeout({"GHREPORT_TIMEOUT": "-3"}) == 60.0
    assert runner.default_timeout({"GHREPORT_TIMEOUT": ""}) == 60.0


# covers: default_timeout
def test_default_timeout_uses_process_environment(monkeypatch):
    monkeypatch.setenv("GHREPORT_TIMEOUT", "12")
    assert runner.default_timeout() == 12.0


# covers: build_env
def test_build_env_sets_base_url_and_copies_input():
    source = {"GITHUB_API_BASE_URL": "http://old", "GITHUB_TOKEN": "t0", "KEEP": "1"}
    env = runner.build_env("http://new", environ=source)
    assert env["GITHUB_API_BASE_URL"] == "http://new"
    assert env["GITHUB_TOKEN"] == "t0" and env["KEEP"] == "1"
    assert source["GITHUB_API_BASE_URL"] == "http://old"


# covers: build_env
def test_build_env_token_variants():
    source = {"GITHUB_API_BASE_URL": "http://x", "GITHUB_TOKEN": "t0"}
    assert runner.build_env(environ=source)["GITHUB_TOKEN"] == "t0"
    assert runner.build_env(token=KEEP_TOKEN, environ=source)["GITHUB_TOKEN"] == "t0"
    assert "GITHUB_TOKEN" not in runner.build_env(token=None, environ=source)
    assert runner.build_env(token="t1", environ=source)["GITHUB_TOKEN"] == "t1"
    assert runner.build_env(token="t1", environ={"GITHUB_API_BASE_URL": "http://x"})["GITHUB_TOKEN"] == "t1"


# covers: build_env
def test_build_env_extra_sets_and_removes():
    source = {"GITHUB_API_BASE_URL": "http://x", "DROP": "1"}
    env = runner.build_env(env_extra={"DROP": None, "ADD": "2"}, environ=source)
    assert "DROP" not in env and env["ADD"] == "2"


# covers: build_env
def test_build_env_refuses_to_run_without_base_url():
    with pytest.raises(RunnerError, match="GITHUB_API_BASE_URL"):
        runner.build_env(environ={})
    with pytest.raises(RunnerError):
        runner.build_env(environ={"GITHUB_API_BASE_URL": ""})


# covers: build_command
def test_build_command_default_out_and_order():
    command = runner.build_command("/bin/g", "a/b", "/c", ["--limit", 5])
    assert command == ["/bin/g", "--repos", "a/b", "--out", "-", "--cache-dir", "/c", "--limit", "5"]


# covers: build_command
def test_build_command_custom_out_trace_and_prefix():
    command = runner.build_command(("py", "script.py"), "a/b", "/c", ["--out", "r.json"], trace="t.jsonl")
    assert command == ["py", "script.py", "--repos", "a/b", "--cache-dir", "/c", "--trace", "t.jsonl",
                       "--out", "r.json"]
    assert "-" not in command


# covers: build_command
def test_build_command_accepts_pathlike(tmp_path):
    command = runner.build_command(tmp_path / "bin", "a/b", tmp_path / "cache", [])
    assert command[0].endswith("bin") and command[-1].endswith("cache")


# covers: run_ghreport
def test_run_ghreport_passes_arguments_env_and_decodes_utf8(tmp_path):
    result = run_fake(tmp_path, "--limit", "3", token="secret", env_extra={"FAKE_EXTRA": "Ё"})
    assert isinstance(result, Result) and result.returncode == 0 and result.elapsed >= 0
    seen = json.loads(result.stdout)
    assert seen["argv"][:4] == ["--repos", "a/b,c/d", "--out", "-"]
    assert seen["argv"][-2:] == ["--limit", "3"]
    assert seen["base_url"] == "http://127.0.0.1:1" and seen["token"] == "secret"
    assert seen["extra"] == "Ё"
    assert result.stderr == "диагностика"


# covers: run_ghreport
def test_run_ghreport_token_removed_and_exit_code_returned(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "from-environment")
    kept = json.loads(run_fake(tmp_path).stdout)
    removed = json.loads(run_fake(tmp_path, token=None).stdout)
    assert kept["token"] == "from-environment" and removed["token"] is None
    assert run_fake(tmp_path, env_extra={"FAKE_EXIT": "3"}).returncode == 3


# covers: run_ghreport
def test_run_ghreport_trace_argument_and_trace_file(tmp_path):
    trace = tmp_path / "t.jsonl"
    run_fake(tmp_path, trace=trace)
    assert runner.trace_of(trace) == [{"status": 200}, {"status": 304}]


# covers: run_ghreport
def test_run_ghreport_kills_hanging_process_on_timeout(tmp_path):
    with pytest.raises(RunnerTimeout) as caught:
        run_fake(tmp_path, env_extra={"FAKE_MODE": "hang"}, timeout=1.0)
    assert "did not finish within 1.0" in str(caught.value)
    assert caught.value.stdout == "" and isinstance(caught.value, AssertionError)


# covers: run_ghreport
def test_run_ghreport_requires_binary_and_base_url(tmp_path, monkeypatch):
    monkeypatch.delenv("GHREPORT_BIN", raising=False)
    with pytest.raises(RunnerError, match="GHREPORT_BIN"):
        runner.run_ghreport("a/b", tmp_path, base_url="http://x")
    monkeypatch.delenv("GITHUB_API_BASE_URL", raising=False)
    with pytest.raises(RunnerError, match="GITHUB_API_BASE_URL"):
        runner.run_ghreport("a/b", tmp_path, binary=FAKE)


# covers: run_ghreport
def test_run_ghreport_uses_ghreport_bin_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("GHREPORT_BIN", FAKE[0])
    result = runner.run_ghreport("a/b", tmp_path, base_url="http://x", timeout=20)
    assert result.returncode == 2 and result.stdout == ""  # the python interpreter rejects --repos


# covers: __init__
def test_runner_timeout_keeps_partial_output():
    error = RunnerTimeout("boom", "out", "err")
    assert (str(error), error.stdout, error.stderr) == ("boom", "out", "err")
    assert RunnerTimeout("x").stdout == ""


# covers: _decode
def test_decode_handles_bytes_text_and_none():
    assert runner._decode(None) == ""
    assert runner._decode("уже текст") == "уже текст"
    assert runner._decode("Ё".encode("utf-8")) == "Ё"
    assert runner._decode(b"\xff\xfe") == "��"


# covers: report_of
def test_report_of_parses_json_on_success():
    result = Result(0, '{"repos": []}', "", 0.1)
    assert runner.report_of(result) == {"repos": []}


# covers: report_of
def test_report_of_rejects_unexpected_exit_code_and_shows_stderr():
    result = Result(3, '{"error": "all_failed"}', "ghreport: boom", 0.1)
    with pytest.raises(AssertionError, match="ghreport: boom"):
        runner.report_of(result)
    assert runner.report_of(result, allowed=(0, 3)) == {"error": "all_failed"}
    assert runner.report_of(Result(1, "{}", "", 0), allowed=(0, 1)) == {}


# covers: report_of
def test_report_of_rejects_non_json_stdout():
    with pytest.raises(AssertionError, match="not valid JSON"):
        runner.report_of(Result(0, "not json", "", 0))
    with pytest.raises(AssertionError):
        runner.report_of(Result(0, "", "", 0))


# covers: trace_of
def test_trace_of_reads_jsonl_skipping_blank_lines(tmp_path):
    path = tmp_path / "t"
    path.write_text('{"a": 1}\n\n  \n{"b": "Ё"}\n', encoding="utf-8")
    assert runner.trace_of(path) == [{"a": 1}, {"b": "Ё"}]
    assert runner.trace_of(str(path)) == [{"a": 1}, {"b": "Ё"}]


# covers: trace_of
def test_trace_of_reports_bad_lines_and_missing_file(tmp_path):
    bad = tmp_path / "bad"
    bad.write_text('{"a": 1}\nnot json\n', encoding="utf-8")
    with pytest.raises(AssertionError, match="line 2"):
        runner.trace_of(bad)
    scalar = tmp_path / "scalar"
    scalar.write_text("[1]\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="not an object"):
        runner.trace_of(scalar)
    with pytest.raises(FileNotFoundError):
        runner.trace_of(tmp_path / "missing")


# covers: main
def test_fake_binary_main_modes(tmp_path):
    result = run_fake(tmp_path, env_extra={"FAKE_EXIT": "2"})
    assert result.returncode == 2 and os.path.exists(FAKE[1])
