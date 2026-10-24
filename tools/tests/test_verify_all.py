import contextlib
import io
import os
import stat
import sys
from pathlib import Path

import pytest

import verify_all as va
from verify_all import FAIL, OK, SKIP, CmdResult, Context, StepOutcome, StepResult


class FakeRunner:
    """Scripted command runner: the first rule whose key is contained in the joined argv answers."""

    def __init__(self, rules=None, default=CmdResult(0, "", "")):
        """Remember the rules.

        Args:
            rules: Mapping of argv substring to CmdResult.
            default: Result when no rule matches.

        Returns:
            None.

        Raises:
            None.
        """
        self.rules = dict(rules or {})
        self.default = default
        self.calls = []

    def __call__(self, argv, cwd=None, env=None, timeout=None):
        """Record the call and answer from the rules.

        Args:
            argv: Command line.
            cwd: Working directory (recorded).
            env: Environment (recorded).
            timeout: Timeout (recorded).

        Returns:
            The matching scripted CmdResult.

        Raises:
            None.
        """
        self.calls.append((list(argv), cwd, env, timeout))
        line = " ".join(str(part) for part in argv)
        for key, result in self.rules.items():
            if key in line:
                return result
        return self.default

    def lines(self):
        """Joined command lines seen so far.

        Returns:
            List of strings.

        Raises:
            None.
        """
        return [" ".join(str(part) for part in call[0]) for call in self.calls]


@contextlib.contextmanager
def fake_stub():
    """Stand-in for the stub server.

    Returns:
        A context manager yielding a fixed URL.

    Raises:
        None.
    """
    yield "http://127.0.0.1:1"


def make_ctx(tmp_path, runner=None, platform="linux", state=None):
    """Build a Context over a temporary directory.

    Args:
        tmp_path: Directory for repo/work.
        runner: Runner, FakeRunner() when None.
        platform: Host platform string.
        state: Initial step state.

    Returns:
        Context.

    Raises:
        None.
    """
    (tmp_path / "repo").mkdir(exist_ok=True)
    (tmp_path / "work").mkdir(exist_ok=True)
    return Context(repo=tmp_path / "repo", work=tmp_path / "work", runner=runner or FakeRunner(),
                   start_stub=fake_stub, platform=platform, python="py", env={"X": "1", "GITHUB_TOKEN": "t"},
                   state=dict(state or {}))


# covers: default_runner
def test_default_runner_captures_output_and_status():
    result = va.default_runner([sys.executable, "-c", "import sys; print('o'); print('e', file=sys.stderr); sys.exit(3)"])
    assert (result.returncode, result.stdout.strip(), result.stderr.strip()) == (3, "o", "e")


# covers: default_runner
def test_default_runner_reports_missing_program_and_timeout():
    assert va.default_runner(["definitely-not-a-program-xyz"]).returncode == 127
    slow = va.default_runner([sys.executable, "-c", "import threading; threading.Event().wait()"], timeout=0.5)
    assert slow.returncode == 124 and "timeout" in slow.stderr


# covers: _decode
def test_decode_handles_none_and_bad_bytes():
    assert va._decode(None) == ""
    assert va._decode("é".encode()) == "é"
    assert va._decode(b"a\xffb") == "a�b"


# covers: output
def test_cmdresult_output_joins_streams():
    assert CmdResult(0, "out", "err").output == "outerr"


# covers: tail_lines
def test_tail_lines_keeps_last_lines():
    assert va.tail_lines("1\n2\n3\n4\n", 2) == "3\n4"
    assert va.tail_lines("", 3) == ""
    assert va.tail_lines("only", 5) == "only"


# covers: scan_sanitizer_output
def test_scan_sanitizer_output_finds_reports():
    text = "ok\nfoo.cpp:3:5: runtime error: signed integer overflow\nERROR: AddressSanitizer: heap-use-after-free\n"
    assert len(va.scan_sanitizer_output(text)) == 2
    assert va.scan_sanitizer_output("100% tests passed, 0 tests failed out of 15\n") == []
    assert va.scan_sanitizer_output("") == []


# covers: count_passed
def test_count_passed_reads_last_summary():
    assert va.count_passed("... 78 passed, 2 skipped in 3.2s") == 78
    assert va.count_passed("5 passed in 1s\n7 passed in 2s") == 7
    assert va.count_passed("no summary") is None


# covers: is_non_root_user
@pytest.mark.parametrize("user,expected", [("", False), ("root", False), ("0", False), ("0:0", False),
                                           ("root:root", False), ("65532:65532", True), ("nonroot", True),
                                           ("1000", True)])
def test_is_non_root_user(user, expected):
    assert va.is_non_root_user(user) is expected


# covers: format_table
def test_format_table_rows_and_totals():
    table = va.format_table([StepResult("env", OK, 1.25, "fine\nmore"), StepResult("docker", SKIP, 0.0, "no daemon"),
                             StepResult("suite", FAIL, 10.0, "boom")])
    lines = table.splitlines()
    assert lines[0].split()[:3] == ["step", "status", "sec"]
    assert any(line.startswith("env") and "1.2" in line and line.endswith("fine") for line in lines)
    assert "more" not in table
    assert lines[-1] == "total: 1 ok, 1 fail, 1 skip, 11.2 s" or lines[-1] == "total: 1 ok, 1 fail, 1 skip, 11.3 s"


# covers: format_table
def test_format_table_empty():
    assert va.format_table([]).splitlines()[-1] == "total: 0 ok, 0 fail, 0 skip, 0.0 s"


# covers: exit_code
def test_exit_code_fails_only_on_fail():
    assert va.exit_code([]) == 0
    assert va.exit_code([StepResult("a", OK, 0), StepResult("b", SKIP, 0)]) == 0
    assert va.exit_code([StepResult("a", OK, 0), StepResult("b", FAIL, 0)]) == 1


# covers: exe_name
def test_exe_name():
    assert va.exe_name("ghreport", "win32") == "ghreport.exe"
    assert va.exe_name("ghreport", "linux") == "ghreport"
    assert va.exe_name("ghreport", "darwin") == "ghreport"


# covers: failure
def test_failure_contains_label_code_and_tail():
    outcome = va.failure("go vet", CmdResult(2, "a\n", "b\n"))
    assert outcome.status == FAIL
    assert outcome.detail.startswith("go vet: exit 2") and "b" in outcome.detail


# covers: run_sequence
def test_run_sequence_stops_at_first_failure(tmp_path):
    runner = FakeRunner({"second": CmdResult(1, "", "bad")})
    ctx = make_ctx(tmp_path, runner)
    results, bad = va.run_sequence(ctx, [("one", ["first"], None), ("two", ["second"], None), ("three", ["third"], None)])
    assert len(results) == 2 and bad.status == FAIL and bad.detail.startswith("two: exit 1")
    assert runner.lines() == ["first", "second"]


# covers: run_sequence
def test_run_sequence_success_uses_env_and_timeout(tmp_path):
    runner = FakeRunner()
    ctx = make_ctx(tmp_path, runner)
    results, bad = va.run_sequence(ctx, [("one", ["a"], tmp_path)], timeout=7)
    assert bad is None and len(results) == 1
    assert runner.calls[0][1:] == (tmp_path, ctx.env, 7)
    va.run_sequence(ctx, [("one", ["a"], None)], env={"K": "v"})
    assert runner.calls[1][2] == {"K": "v"}


# covers: make_clone
def test_make_clone_command_and_failure(tmp_path):
    runner = FakeRunner()
    assert va.make_clone(runner, tmp_path / "src", tmp_path / "dst") is None
    assert runner.calls[0][0][:4] == ["git", "clone", "--no-hardlinks", "--quiet"]
    bad = va.make_clone(FakeRunner(default=CmdResult(128, "", "fatal: nope")), tmp_path, tmp_path / "x")
    assert "exit 128" in bad and "fatal: nope" in bad


# covers: make_clone
def test_make_clone_real_git_copies_only_committed_state(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    git = lambda *a: va.default_runner(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], source)  # noqa: E731
    git("init", "-q")
    (source / "committed.txt").write_text("x")
    git("add", ".")
    assert git("commit", "-q", "-m", "c").returncode == 0
    (source / "untracked.txt").write_text("y")
    assert va.make_clone(va.default_runner, source, tmp_path / "clone") is None
    assert (tmp_path / "clone" / "committed.txt").exists()
    assert not (tmp_path / "clone" / "untracked.txt").exists()


# covers: default_start_stub
def test_default_start_stub_serves_and_stops(tmp_path):
    import urllib.request

    root = Path(__file__).resolve().parents[2]
    with va.default_start_stub(sys.executable, root) as url:
        assert url.startswith("http://127.0.0.1:")
        with urllib.request.urlopen(url + "/repos/stub/small/issues") as response:
            assert response.status == 200


# covers: default_start_stub
def test_default_start_stub_rejects_missing_ready(tmp_path):
    (tmp_path / "api_tests" / "selfcheck").mkdir(parents=True)
    (tmp_path / "api_tests" / "selfcheck" / "stub_server.py").write_text("print('hello', flush=True)\n")
    with pytest.raises(RuntimeError):
        with va.default_start_stub(sys.executable, tmp_path):
            pass


# covers: suite_env
def test_suite_env_sets_variables_and_drops_token(tmp_path):
    env = va.suite_env(make_ctx(tmp_path), "/bin/x", "http://h:1")
    assert env["GHREPORT_BIN"] == "/bin/x" and env["GITHUB_API_BASE_URL"] == "http://h:1"
    assert env["GHREPORT_STRICT"] == "1" and env["X"] == "1" and "GITHUB_TOKEN" not in env


# covers: step_env
def test_step_env(tmp_path):
    assert va.step_env(make_ctx(tmp_path)).status == OK
    bad = va.step_env(make_ctx(tmp_path, FakeRunner(default=CmdResult(1, "table", ""))))
    assert bad.status == FAIL and "check_env" in bad.detail


# covers: generator_args
def test_generator_args():
    assert va.generator_args("linux", {}, lambda name: "/usr/bin/ninja") == []
    assert va.generator_args("win32", {"CMAKE_GENERATOR": "Ninja"}, lambda name: None) == []
    assert va.generator_args("win32", {}, lambda name: "C:/ninja.exe") == ["-G", "Ninja"]
    assert va.generator_args("win32", {}, lambda name: None) == ["-G", "MinGW Makefiles"]


# covers: build_msgfmt
def test_build_msgfmt_ok_and_arguments(tmp_path):
    runner = FakeRunner({"ctest": CmdResult(0, "100% tests passed, 0 tests failed out of 15", "")})
    ctx = make_ctx(tmp_path, runner)
    outcome = va.build_msgfmt(ctx, "b", ["-DMSGFMT_SANITIZE=ON"])
    assert outcome == StepOutcome(OK, "100% tests passed, 0 tests failed out of 15")
    configure = runner.lines()[0]
    assert "-DCMAKE_BUILD_TYPE=Release" in configure and "-DMSGFMT_WERROR=ON" in configure
    assert "-DMSGFMT_SANITIZE=ON" in configure


# covers: build_msgfmt
def test_build_msgfmt_detects_sanitizer_messages_and_failures(tmp_path):
    dirty = FakeRunner({"ctest": CmdResult(0, "x.cpp:1: runtime error: shift\n", "")})
    outcome = va.build_msgfmt(make_ctx(tmp_path, dirty), "b", [])
    assert outcome.status == FAIL and "sanitizer" in outcome.detail
    broken = va.build_msgfmt(make_ctx(tmp_path, FakeRunner({"--build": CmdResult(2, "", "error: x")})), "b", [])
    assert broken.status == FAIL and broken.detail.startswith("cmake build")


# covers: step_msgfmt_release
def test_step_msgfmt_release_has_no_sanitizer_flag(tmp_path):
    runner = FakeRunner()
    assert va.step_msgfmt_release(make_ctx(tmp_path, runner)).status == OK
    assert "SANITIZE" not in runner.lines()[0] and "build-release" in runner.lines()[0]


# covers: probe_sanitizers
def test_probe_sanitizers_reasons(tmp_path):
    assert va.probe_sanitizers(make_ctx(tmp_path)) is None
    assert "cannot build" in va.probe_sanitizers(make_ctx(tmp_path, FakeRunner({"-fsanitize": CmdResult(1)})))
    ran_bad = va.probe_sanitizers(make_ctx(tmp_path, FakeRunner({"-fsanitize": CmdResult(0), "san_probe": CmdResult(3)})))
    assert "does not start" in ran_bad


# covers: step_msgfmt_sanitize
def test_step_msgfmt_sanitize_skips_without_support(tmp_path):
    outcome = va.step_msgfmt_sanitize(make_ctx(tmp_path, FakeRunner({"-fsanitize": CmdResult(1)})))
    assert outcome.status == SKIP and "sanitize" in outcome.detail


# covers: step_msgfmt_sanitize
def test_step_msgfmt_sanitize_builds_with_flag(tmp_path):
    runner = FakeRunner({"ctest": CmdResult(0, "100% tests passed", "")})
    assert va.step_msgfmt_sanitize(make_ctx(tmp_path, runner)).status == OK
    assert any("-DMSGFMT_SANITIZE=ON" in line for line in runner.lines())


# covers: step_service
def test_step_service_success_records_binary(tmp_path):
    runner = FakeRunner({"go test": CmdResult(0, "ok  \tghreport/internal/a\t0.1s\nok  \tghreport/internal/b\t0.1s\n")})
    ctx = make_ctx(tmp_path, runner, platform="win32")
    outcome = va.step_service(ctx)
    assert outcome.status == OK and "2 packages" in outcome.detail
    assert ctx.state["binary"].endswith("ghreport.exe")
    assert "-race" in " ".join(runner.lines()) and "-count=1" in " ".join(runner.lines())


# covers: step_service
def test_step_service_fails_on_gofmt_listing_and_vet(tmp_path):
    listing = va.step_service(make_ctx(tmp_path, FakeRunner({"gofmt": CmdResult(0, "internal/x.go\n", "")})))
    assert listing.status == FAIL and "internal/x.go" in listing.detail
    ctx = make_ctx(tmp_path, FakeRunner({"vet": CmdResult(1, "", "vet: problem")}))
    assert va.step_service(ctx).status == FAIL and "binary" not in ctx.state
    assert va.step_service(make_ctx(tmp_path, FakeRunner({"gofmt": CmdResult(2, "", "no gofmt")}))).status == FAIL


# covers: step_contracts
def test_step_contracts_checks_four_trees(tmp_path):
    runner = FakeRunner()
    assert va.step_contracts(make_ctx(tmp_path, runner)).status == OK
    joined = "\n".join(runner.lines())
    for root in ("msgfmt", "service", "api_tests", "tools"):
        assert f"check_contracts.py {root} " in joined
    assert joined.count("--check-covers") == 4
    bad = va.step_contracts(make_ctx(tmp_path, FakeRunner({"check_contracts": CmdResult(1, "a.py: no Args", "")})))
    assert bad.status == FAIL


# covers: step_hygiene
def test_step_hygiene(tmp_path):
    runner = FakeRunner({"check_repo": CmdResult(0, "0 problem(s), 2 note(s)\n", "")})
    outcome = va.step_hygiene(make_ctx(tmp_path, runner))
    assert outcome == StepOutcome(OK, "0 problem(s), 2 note(s)")
    assert "--repo" in runner.lines()[0]
    failed = va.step_hygiene(make_ctx(tmp_path, FakeRunner(default=CmdResult(1, "secret: x", ""))))
    assert failed.status == FAIL and "secret: x" in failed.detail


# covers: step_selfchecks
def test_step_selfchecks_reports_counts(tmp_path):
    runner = FakeRunner({"tools/tests": CmdResult(0, "10 passed in 1s", ""),
                         "selfcheck": CmdResult(0, "55 passed, 70 deselected in 9s", "")})
    outcome = va.step_selfchecks(make_ctx(tmp_path, runner))
    assert outcome.status == OK and "10 passed" in outcome.detail and "55 passed" in outcome.detail
    failed = va.step_selfchecks(make_ctx(tmp_path, FakeRunner({"selfcheck": CmdResult(1, "1 failed", "")})))
    assert failed.status == FAIL


# covers: step_suite
def test_step_suite_skips_without_binary(tmp_path):
    assert va.step_suite(make_ctx(tmp_path)).status == SKIP


# covers: step_suite
def test_step_suite_runs_pytest_with_environment(tmp_path):
    runner = FakeRunner({"pytest": CmdResult(0, "78 passed in 30s", "")})
    ctx = make_ctx(tmp_path, runner, state={"binary": "/b/ghreport"})
    outcome = va.step_suite(ctx)
    assert outcome.status == OK and "78 passed" in outcome.detail
    env = runner.calls[0][2]
    assert env["GHREPORT_BIN"] == "/b/ghreport" and env["GITHUB_API_BASE_URL"] == "http://127.0.0.1:1"
    failed = va.step_suite(make_ctx(tmp_path, FakeRunner({"pytest": CmdResult(1, "1 failed", "")}),
                                    state={"binary": "/b"}))
    assert failed.status == FAIL


# covers: docker_available
def test_docker_available(tmp_path):
    assert va.docker_available(make_ctx(tmp_path)) is None
    assert "daemon" in va.docker_available(make_ctx(tmp_path, FakeRunner(default=CmdResult(1))))


# covers: step_docker_image
def test_step_docker_image_skips_without_daemon(tmp_path):
    assert va.step_docker_image(make_ctx(tmp_path, FakeRunner(default=CmdResult(1)))).status == SKIP


# covers: step_docker_image
def test_step_docker_image_ok_and_failures(tmp_path):
    good = FakeRunner({"inspect": CmdResult(0, "65532:65532\n", ""), "--repos": CmdResult(2, "", "usage")})
    outcome = va.step_docker_image(make_ctx(tmp_path, good))
    assert outcome.status == OK and "65532" in outcome.detail
    assert any(line.startswith("docker build -t ghreport service/") for line in good.lines())
    root = FakeRunner({"inspect": CmdResult(0, "\n", "")})
    assert "root" in va.step_docker_image(make_ctx(tmp_path, root)).detail
    wrong_code = FakeRunner({"inspect": CmdResult(0, "1000", ""), "--repos": CmdResult(0, "", "")})
    assert va.step_docker_image(make_ctx(tmp_path, wrong_code)).status == FAIL
    noisy = FakeRunner({"inspect": CmdResult(0, "1000", ""), "--repos": CmdResult(2, "{}", "")})
    assert va.step_docker_image(make_ctx(tmp_path, noisy)).status == FAIL
    build_broken = FakeRunner({"docker build": CmdResult(1, "", "no")})
    assert va.step_docker_image(make_ctx(tmp_path, build_broken)).status == FAIL
    inspect_broken = FakeRunner({"inspect": CmdResult(1, "", "no")})
    assert va.step_docker_image(make_ctx(tmp_path, inspect_broken)).status == FAIL


REPORT = '{"generated_at":"x","repos":[{"repo":"stub/small","error":null,"count":3,"issues":[]}]}'


# covers: step_docker_run
def test_step_docker_run_skips(tmp_path):
    assert va.step_docker_run(make_ctx(tmp_path, platform="win32")).status == SKIP
    assert va.step_docker_run(make_ctx(tmp_path, FakeRunner(default=CmdResult(1)))).status == SKIP
    no_image = FakeRunner({"image inspect": CmdResult(1)})
    outcome = va.step_docker_run(make_ctx(tmp_path, no_image))
    assert outcome.status == SKIP and "not built" in outcome.detail


# covers: step_docker_run
def test_step_docker_run_ok_and_failures(tmp_path):
    ok = FakeRunner({"ghreport-docker.sh": CmdResult(0, REPORT, ""), "pytest": CmdResult(0, "78 passed", "")})
    outcome = va.step_docker_run(make_ctx(tmp_path, ok))
    assert outcome.status == OK and "78 passed" in outcome.detail
    pytest_call = [call for call in ok.calls if "pytest" in call[0]][0]
    assert pytest_call[2]["GHREPORT_BIN"].endswith("ghreport-docker.sh")
    garbage = FakeRunner({"ghreport-docker.sh": CmdResult(0, "not json", "")})
    assert "not a report" in va.step_docker_run(make_ctx(tmp_path, garbage)).detail
    wrong = FakeRunner({"ghreport-docker.sh": CmdResult(0, REPORT.replace('"count":3', '"count":4'), "")})
    assert "unexpected report" in va.step_docker_run(make_ctx(tmp_path, wrong)).detail
    failed_run = FakeRunner({"ghreport-docker.sh": CmdResult(3, "", "boom")})
    assert va.step_docker_run(make_ctx(tmp_path, failed_run)).status == FAIL
    failed_suite = FakeRunner({"ghreport-docker.sh": CmdResult(0, REPORT, ""), "pytest": CmdResult(1, "1 failed", "")})
    assert va.step_docker_run(make_ctx(tmp_path, failed_suite)).status == FAIL


# covers: step_mutants
def test_step_mutants(tmp_path):
    runner = FakeRunner({"run_mutants": CmdResult(0, "line\nALL KILLED 43/43\n", "")})
    outcome = va.step_mutants(make_ctx(tmp_path, runner))
    assert outcome == StepOutcome(OK, "ALL KILLED 43/43")
    assert va.step_mutants(make_ctx(tmp_path, FakeRunner(default=CmdResult(1)))).status == FAIL


# covers: all_steps
def test_all_steps_order_and_mutants_flag():
    names = [name for name, _ in va.all_steps(False)]
    assert names == ["env", "msgfmt-release", "msgfmt-sanitize", "service", "contracts", "hygiene", "selfchecks", "suite",
                     "docker-image", "docker-run"]
    assert [name for name, _ in va.all_steps(True)][-1] == "mutants"


# covers: run_steps
def test_run_steps_times_and_converts_exceptions(tmp_path):
    ticks = iter(range(0, 100, 5))

    def boom(_ctx):
        """Raise.

        Args:
            _ctx: Unused.

        Returns:
            Never.

        Raises:
            ValueError: Always.
        """
        raise ValueError("bad")

    seen = []
    results = va.run_steps([("a", lambda c: StepOutcome(OK, "fine")), ("b", boom), ("c", lambda c: StepOutcome(SKIP, "r"))],
                           make_ctx(tmp_path), clock=lambda: next(ticks), progress=seen.append)
    assert [(r.name, r.status, r.seconds) for r in results] == [("a", OK, 5), ("b", FAIL, 5), ("c", SKIP, 5)]
    assert "ValueError: bad" in results[1].detail and seen == ["==> a", "==> b", "==> c"]


# covers: remove_tree
def test_remove_tree_deletes_readonly_files(tmp_path):
    target = tmp_path / "t" / "sub"
    target.mkdir(parents=True)
    locked = target / "obj"
    locked.write_text("x")
    os.chmod(locked, stat.S_IREAD)
    va.remove_tree(tmp_path / "t")
    assert not (tmp_path / "t").exists()
    va.remove_tree(tmp_path / "missing")


# covers: make_writable
def test_make_writable_retries_removal(tmp_path):
    locked = tmp_path / "f"
    locked.write_text("x")
    os.chmod(locked, stat.S_IREAD)
    calls = []
    # the nested helper is reached through remove_tree; emulate a failing first removal
    real_rmtree = va.shutil.rmtree

    def fake_rmtree(path, onerror):
        """Invoke the error handler as shutil would after a failed unlink.

        Args:
            path: Directory being removed.
            onerror: Handler under test.

        Returns:
            None.

        Raises:
            OSError: If the handler's retry fails.
        """
        calls.append(path)
        onerror(os.unlink, str(locked), None)

    va.shutil.rmtree = fake_rmtree
    try:
        va.remove_tree(tmp_path)
    finally:
        va.shutil.rmtree = real_rmtree
    assert calls and not locked.exists()


# covers: parse_args
def test_parse_args_defaults_and_options(tmp_path):
    defaults = va.parse_args([])
    assert (defaults.with_mutants, defaults.only, defaults.keep, defaults.repo) == (False, [], False, va.ROOT)
    args = va.parse_args(["--with-mutants", "--only", "env,suite", "--keep", "--repo", str(tmp_path)])
    assert args.with_mutants and args.keep and args.only == ["env", "suite"] and args.repo == tmp_path
    with pytest.raises(SystemExit):
        va.parse_args(["--bogus"])


# covers: main
def test_main_runs_selected_steps_and_prints_table(tmp_path):
    runner = FakeRunner({"check_env": CmdResult(0, "", "")})
    out = io.StringIO()
    code = va.main(["--only", "env", "--repo", str(tmp_path)], runner=runner, out=out)
    text = out.getvalue()
    assert code == 0 and "env" in text and "total: 1 ok, 0 fail, 0 skip" in text
    assert runner.lines()[1].startswith("git clone --no-hardlinks")


# covers: main
def test_main_returns_one_when_a_step_fails_and_shows_details(tmp_path):
    out = io.StringIO()
    code = va.main(["--only", "env", "--repo", str(tmp_path)],
                   runner=FakeRunner({"check_env": CmdResult(1, "missing go", "")}), out=out)
    assert code == 1 and "--- env failed ---" in out.getvalue() and "missing go" in out.getvalue()


# covers: main
def test_main_unknown_step_and_clone_failure(tmp_path):
    out = io.StringIO()
    assert va.main(["--only", "nope"], runner=FakeRunner(), out=out) == 2 and "unknown step" in out.getvalue()
    out = io.StringIO()
    runner = FakeRunner({"git clone": CmdResult(128, "", "fatal")})
    assert va.main(["--repo", str(tmp_path)], runner=runner, out=out) == 1 and "git clone" in out.getvalue()


# covers: main
def test_main_warns_about_uncommitted_changes_and_keeps_clone(tmp_path):
    out = io.StringIO()
    runner = FakeRunner({"status --porcelain": CmdResult(0, " M file\n", "")})
    va.main(["--only", "env", "--keep", "--repo", str(tmp_path)], runner=runner, out=out)
    text = out.getvalue()
    assert "uncommitted changes are NOT verified" in text and "kept:" in text
    va.remove_tree(Path(text.split("kept: ")[1].strip()))


# covers: main
def test_main_uses_injected_stub_factory(tmp_path):
    used = []

    @contextlib.contextmanager
    def stub(clone):
        """Record the clone path and yield a URL.

        Args:
            clone: Clone directory passed by main.

        Returns:
            Context manager yielding a URL.

        Raises:
            None.
        """
        used.append(clone)
        yield "http://127.0.0.1:2"

    runner = FakeRunner({"pytest": CmdResult(0, "3 passed", "")})
    # suite needs a built binary, so run service first (all commands succeed in the fake)
    code = va.main(["--only", "service,suite", "--repo", str(tmp_path)], runner=runner, start_stub=stub,
                   out=io.StringIO())
    assert code == 0 and len(used) == 1 and used[0].name == "repo"
