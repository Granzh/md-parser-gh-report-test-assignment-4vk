"""Meta-tests of run_mutants.py: patching, building, verdicts and the overall exit code."""

import subprocess
import urllib.request

import pytest

import run_mutants as rm
from run_mutants import Edit, Outcome, ServiceMutant, StaleMutant, SuiteResult
from support import http_request

pytestmark = pytest.mark.selfcheck


def fake_runner(results, seen=None):
    """Build a stand-in for run_with_stub that returns scripted results.

    Args:
        results: Function ``(binary, flags) -> SuiteResult``.
        seen: Optional list collecting (binary name, flags) pairs.

    Returns:
        Function with the signature of run_mutants.run_with_stub.

    Raises:
        None.
    """
    def run(binary, flags, workdir, runner=None):
        """Return the scripted result.

        Args:
            binary: Executable path.
            flags: Stub flags.
            workdir: Working directory (unused).
            runner: Unused.

        Returns:
            SuiteResult.

        Raises:
            None.
        """
        if seen is not None:
            seen.append((binary.name, tuple(flags)))
        return results(binary, tuple(flags))

    return run


# covers: apply_mutation
def test_apply_mutation_replaces_exactly_one_match():
    assert rm.apply_mutation("a = 1\nb = 2\n", r"b = 2", "b = 3") == "a = 1\nb = 3\n"
    assert rm.apply_mutation("x y z", r"y", r"\1 \n") == r"x \1 \n z"
    assert rm.apply_mutation("one\ntwo\n", r"^two$", "2") == "one\n2\n"


# covers: apply_mutation
def test_apply_mutation_rejects_zero_and_multiple_matches():
    with pytest.raises(StaleMutant, match="matched 0 times"):
        rm.apply_mutation("abc", r"zzz", "x")
    with pytest.raises(StaleMutant, match="matched 2 times"):
        rm.apply_mutation("abab", r"ab", "x")


# covers: lit
def test_lit_escapes_regex_metacharacters():
    import re
    text = 'if (a.b) && c[0] {'
    assert re.fullmatch(rm.lit(text), text) and not re.fullmatch(rm.lit(text), "if (aXb) && c[0] {")


# covers: apply_mutant
def test_apply_mutant_patches_files_and_reports_stale_edits(tmp_path):
    (tmp_path / "a.go").write_text("one\ntwo\n", encoding="utf-8")
    (tmp_path / "b.go").write_text("x x\n", encoding="utf-8")
    good = ServiceMutant("good", "d", (Edit("a.go", "one", "1"), Edit("a.go", "two", "2")))
    rm.apply_mutant(good, tmp_path)
    assert (tmp_path / "a.go").read_text(encoding="utf-8") == "1\n2\n"
    with pytest.raises(StaleMutant, match=r"stale_one \(b.go\).*matched 2 times"):
        rm.apply_mutant(ServiceMutant("stale_one", "d", (Edit("b.go", "x", "y"),)), tmp_path)
    with pytest.raises(StaleMutant, match="missing.go does not exist"):
        rm.apply_mutant(ServiceMutant("gone", "d", (Edit("missing.go", "x", "y"),)), tmp_path)


# covers: copy_service
def test_copy_service_skips_tests_and_binaries(tmp_path):
    source = tmp_path / "src"
    (source / "cmd" / "ghreport").mkdir(parents=True)
    for name in ("go.mod", "cmd/main.go", "cmd/main_test.go", "ghreport.exe", "ghreport", "cmd/ghreport/m.go"):
        (source / name).write_text("x", encoding="utf-8")
    rm.copy_service(source, tmp_path / "dst")
    kept = sorted(p.relative_to(tmp_path / "dst").as_posix() for p in (tmp_path / "dst").rglob("*") if p.is_file())
    assert kept == ["cmd/ghreport/m.go", "cmd/main.go", "go.mod"]


# covers: build_service
def test_build_service_compiles_a_tiny_module_and_reports_errors(tmp_path):
    (tmp_path / "cmd" / "ghreport").mkdir(parents=True)
    (tmp_path / "go.mod").write_text("module tiny\n\ngo 1.22\n", encoding="utf-8")
    main = tmp_path / "cmd" / "ghreport" / "main.go"
    main.write_text("package main\n\nfunc main() {}\n", encoding="utf-8")
    rm.build_service(tmp_path, tmp_path / "out.exe")
    assert (tmp_path / "out.exe").is_file()
    main.write_text("package main\n\nfunc main() { undefined() }\n", encoding="utf-8")
    with pytest.raises(rm.BuildError, match="undefined"):
        rm.build_service(tmp_path, tmp_path / "out2.exe")


# covers: build_service
def test_build_service_wraps_a_missing_toolchain(tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        """Pretend `go` cannot be started.

        Args:
            args: Ignored.
            kwargs: Ignored.

        Returns:
            Never returns.

        Raises:
            FileNotFoundError: Always.
        """
        raise FileNotFoundError("go")

    monkeypatch.setattr(rm.subprocess, "run", broken)
    with pytest.raises(rm.BuildError, match="could not run"):
        rm.build_service(tmp_path, tmp_path / "x")


# covers: first_failure
def test_first_failure_reads_the_short_summary():
    output = "....\nFAILED api_tests/test_a.py::test_x - assert 1\nERROR api_tests/test_b.py::test_y\n"
    assert rm.first_failure(output) == "test_a.py::test_x"
    assert rm.first_failure(r"ERROR ..\..\api_tests\t.py::fixture_problem") == "t.py::fixture_problem"
    assert rm.first_failure("5 passed") == ""


# covers: classify
@pytest.mark.parametrize("code,timed_out,expected", [
    (0, False, "survived"), (1, False, "killed"), (2, False, "error"), (3, False, "error"),
    (4, False, "error"), (5, False, "error"), (-1, True, "error"), (1, True, "error"),
])
def test_classify_exit_codes(code, timed_out, expected):
    assert rm.classify(SuiteResult(code, "", timed_out)) == expected


def make_suite(tmp_path, passing):
    """Create a throw-away "suite" directory.

    Args:
        tmp_path: Parent directory.
        passing: True for a passing test, False for a failing one.

    Returns:
        Path of the new directory.

    Raises:
        None.
    """
    suite = tmp_path / ("ok" if passing else "bad")
    suite.mkdir()
    body = "def test_sample():\n    assert True\n" if passing else "def test_sample():\n    assert False\n"
    (suite / "test_sample.py").write_text(body, encoding="utf-8")
    return suite


# covers: run_suite
def test_run_suite_runs_pytest_and_classifies_on_substitute_suites(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setattr(rm, "API_TESTS", make_suite(tmp_path, True))
    survived = rm.run_suite("bin", "http://x", work, timeout=120)
    assert survived.returncode == 0 and rm.classify(survived) == "survived"
    monkeypatch.setattr(rm, "API_TESTS", make_suite(tmp_path, False))
    killed = rm.run_suite("bin", "http://x", work, timeout=120)
    assert killed.returncode == 1 and rm.classify(killed) == "killed"
    assert rm.first_failure(killed.output).endswith("test_sample.py::test_sample")


# covers: run_suite
def test_run_suite_passes_environment_and_stops_on_timeout(tmp_path, monkeypatch):
    calls = []

    def fake(command, **kwargs):
        """Record the call and simulate a hang.

        Args:
            command: Argument vector.
            kwargs: Keyword arguments of subprocess.run.

        Returns:
            Never returns.

        Raises:
            subprocess.TimeoutExpired: Always.
        """
        calls.append((command, kwargs))
        raise subprocess.TimeoutExpired(command, kwargs["timeout"], output=b"partial")

    monkeypatch.setattr(rm.subprocess, "run", fake)
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    result = rm.run_suite("/bin/ghreport", "http://stub", tmp_path, timeout=7)
    command, kwargs = calls[0]
    assert result.timed_out and result.returncode == -1 and result.output == "partial"
    assert "-x" in command and command[command.index("-q") + 3] == "not selfcheck"
    env = kwargs["env"]
    assert env["GHREPORT_BIN"] == "/bin/ghreport" and env["GITHUB_API_BASE_URL"] == "http://stub"
    assert "GITHUB_TOKEN" not in env and kwargs["cwd"] == tmp_path and kwargs["timeout"] == 7


# covers: run_with_stub
def test_run_with_stub_starts_the_flagged_server_and_stops_it(tmp_path):
    seen = {}

    def runner(binary, url, workdir):
        """Probe the stub and remember what it looks like.

        Args:
            binary: Executable path (unused).
            url: Stub base URL.
            workdir: Working directory (unused).

        Returns:
            A passing SuiteResult.

        Raises:
            OSError: The stub is unreachable.
        """
        seen["url"] = url
        seen["headers"] = http_request(url + "/repos/stub/alpha/issues?per_page=100")[1]
        return SuiteResult(0, "ok")

    result = rm.run_with_stub("bin", ["no_link"], tmp_path, runner=runner)
    assert result.returncode == 0 and "link" not in seen["headers"]
    with pytest.raises(OSError):
        urllib.request.urlopen(seen["url"] + "/repos/stub/small/issues", timeout=3)
    plain = {}
    rm.run_with_stub("bin", [], tmp_path,
                     runner=lambda b, u, w: plain.update(h=http_request(u + "/repos/stub/alpha/issues?per_page=100")[1])
                     or SuiteResult(0, ""))
    assert "link" in plain["h"]


# covers: judge
def test_judge_verdicts_for_every_kind():
    killed = SuiteResult(1, "FAILED t.py::test_a - x", False, 2.04)
    passed = SuiteResult(0, "ok", False, 1.0)
    assert rm.judge("m", "service", killed) == Outcome("m", "service", "killed", "t.py::test_a", 2.0, "")
    assert rm.judge("m", "service", passed).status == "survived"
    assert rm.judge("m", "service", passed, equivalent="same behaviour").status == "equivalent"
    assert rm.judge("m", "server", killed).status == "killed"
    assert rm.judge("d", "compatible", passed).status == "green"
    rejected = rm.judge("d", "compatible", killed)
    assert rejected.status == "survived" and "rejects a compliant server" in rejected.note
    timeout = rm.judge("m", "service", SuiteResult(-1, "", True, 600))
    assert timeout.status == "error" and timeout.note == "timeout"
    assert rm.judge("m", "service", SuiteResult(5, "", False)).note == "pytest exit code 5"


# covers: is_success
def test_is_success_requires_every_mutant_killed():
    def outcome(status):
        """Build an outcome with a status.

        Args:
            status: Status text.

        Returns:
            Outcome.

        Raises:
            None.
        """
        return Outcome("m", "service", status)

    assert rm.is_success([outcome("killed"), outcome("equivalent"), outcome("green")])
    assert not rm.is_success([outcome("killed"), outcome("survived")])
    assert not rm.is_success([outcome("killed"), outcome("error")])
    assert not rm.is_success([])


# covers: format_table
def test_format_table_lists_every_mutant_and_a_summary():
    table = rm.format_table([Outcome("alpha", "service", "killed", "t.py::test_a", 1.5),
                             Outcome("b", "server", "survived", "", 2.0, "the suite stayed green")])
    lines = table.splitlines()
    assert lines[0].split()[:3] == ["mutant", "kind", "status"]
    assert "alpha" in lines[1] and "KILLED" in lines[1] and lines[1].endswith("t.py::test_a")
    assert "SURVIVED" in lines[2] and lines[2].endswith("the suite stayed green")
    assert lines[-1] == "total 2 - killed: 1, survived: 1"
    assert rm.format_table([]).endswith("total 0 - ")


# covers: select
def test_select_filters_by_id_with_or_without_prefix():
    servers, compatible, services = rm.select(None)
    assert len(servers) == 6 and compatible == ["compatible:dup_items"] and len(services) == len(rm.SERVICE_MUTANTS)
    assert rm.select("no_link") == (["server:no_link"], [], [])
    assert rm.select("server:no_link")[0] == ["server:no_link"]
    assert rm.select("dup_items") == ([], ["compatible:dup_items"], [])
    assert rm.select("sort_reversed")[2][0].id == "sort_reversed"
    with pytest.raises(KeyError, match="unknown mutant id"):
        rm.select("nope")


# covers: validate
def test_validate_accepts_the_real_service_and_rejects_stale_patterns(monkeypatch, tmp_path):
    rm.validate(rm.REPO_ROOT / "service")
    stale = ServiceMutant("stale", "d", (Edit("internal/issues/issues.go", "no such text anywhere", "x"),))
    monkeypatch.setattr(rm, "SERVICE_MUTANTS", (stale,))
    with pytest.raises(StaleMutant, match="stale"):
        rm.validate(rm.REPO_ROOT / "service")


def table_fixture(tmp_path, monkeypatch, mutants=None):
    """Prepare a fake service directory and a fake mutant list for run_all tests.

    Args:
        tmp_path: Temporary directory.
        monkeypatch: pytest monkeypatch fixture.
        mutants: Optional replacement for SERVICE_MUTANTS.

    Returns:
        Path of the fake service directory.

    Raises:
        None.
    """
    service = tmp_path / "svc"
    service.mkdir(exist_ok=True)
    (service / "a.go").write_text("bug-site\n", encoding="utf-8")
    monkeypatch.setattr(rm, "SERVICE_MUTANTS",
                        mutants or (ServiceMutant("m1", "d", (Edit("a.go", "bug-site", "mutated"),)),
                                    ServiceMutant("m2", "d", (Edit("a.go", "bug-site", "other"),))))
    monkeypatch.setattr(rm, "build_service", lambda directory, output: None)
    return service


# covers: run_all
def test_run_all_returns_zero_when_everything_is_killed(tmp_path, monkeypatch):
    service = table_fixture(tmp_path, monkeypatch)
    seen = []

    def results(binary, flags):
        """Green for the baseline and compliant servers, red for mutants.

        Args:
            binary: Executable path.
            flags: Stub flags.

        Returns:
            Scripted SuiteResult.

        Raises:
            None.
        """
        compliant = binary.name == "ghreport-base.exe" and flags in ((), ("dup_items",))
        return SuiteResult(0, "ok") if compliant else SuiteResult(1, "FAILED t.py::test_x - boom")

    monkeypatch.setattr(rm, "run_with_stub", fake_runner(results, seen))
    code, outcomes = rm.run_all(service, log=lambda text: None)
    assert code == 0 and len(outcomes) == 6 + 1 + 2
    assert [o.status for o in outcomes if o.kind == "service"] == ["killed", "killed"]
    assert [o.status for o in outcomes if o.kind == "compatible"] == ["green"]
    assert seen[0] == ("ghreport-base.exe", ())


# covers: run_all
def test_run_all_fails_when_a_mutant_survives_or_the_baseline_is_red(tmp_path, monkeypatch):
    service = table_fixture(tmp_path, monkeypatch)

    def survivor(binary, flags):
        """Everything stays green except the server mutants.

        Args:
            binary: Executable path.
            flags: Stub flags.

        Returns:
            Scripted SuiteResult.

        Raises:
            None.
        """
        return SuiteResult(1, "FAILED t.py::t") if flags and flags != ("dup_items",) else SuiteResult(0, "ok")

    monkeypatch.setattr(rm, "run_with_stub", fake_runner(survivor))
    code, outcomes = rm.run_all(service, log=lambda text: None)
    assert code == 1 and [o.id for o in outcomes if o.status == "survived"] == ["m1", "m2"]
    monkeypatch.setattr(rm, "run_with_stub", fake_runner(lambda b, f: SuiteResult(1, "FAILED t.py::t")))
    messages = []
    code, outcomes = rm.run_all(service, log=messages.append)
    assert code == 2 and outcomes == [] and any("baseline is NOT green" in m for m in messages)
    code, outcomes = rm.run_all(service, skip_baseline=True, log=lambda text: None)
    assert code == 1 and any(o.status == "survived" for o in outcomes)


# covers: run_all
def test_run_all_stops_on_stale_or_uncompilable_mutants_and_honours_only(tmp_path, monkeypatch):
    stale = (ServiceMutant("old", "d", (Edit("a.go", "not-there", "x"),)),)
    service = table_fixture(tmp_path, monkeypatch, stale)
    monkeypatch.setattr(rm, "run_with_stub", fake_runner(lambda b, f: SuiteResult(0, "ok")))
    messages = []
    code, _ = rm.run_all(service, skip_baseline=True, log=messages.append)
    assert code == 2 and any("INVALID MUTANT" in m and "old" in m for m in messages)

    def failing_build(directory, output):
        """Fail every build of a mutant.

        Args:
            directory: Source directory.
            output: Binary path.

        Returns:
            None for the baseline.

        Raises:
            BuildError: For mutant builds.
        """
        if "base" not in output.name:
            raise rm.BuildError("compile error")

    service = table_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(rm, "build_service", failing_build)
    code, _ = rm.run_all(service, skip_baseline=True, log=messages.append)
    assert code == 2 and any("compile error" in m for m in messages)
    monkeypatch.setattr(rm, "build_service", lambda d, o: None)
    code, outcomes = rm.run_all(service, only="m2", skip_baseline=True, log=lambda text: None)
    assert [o.id for o in outcomes] == ["m2"]
    monkeypatch.setattr(rm, "build_service", failing_build)
    code, outcomes = rm.run_all(service, only="m2", log=messages.append)
    assert code == 2


# covers: main
def test_main_validate_and_unknown_id_and_table_output(tmp_path, monkeypatch, capsys):
    assert rm.main(["--validate"]) == 0
    assert f"all {len(rm.SERVICE_MUTANTS)} service mutation patterns" in capsys.readouterr().out
    assert rm.main(["--only", "nope"]) == 2
    assert "unknown mutant id" in capsys.readouterr().err
    monkeypatch.setattr(rm, "run_all", lambda *a, **k: (1, [Outcome("m", "service", "survived", "", 1.0, "x")]))
    assert rm.main([]) == 1
    printed = capsys.readouterr().out
    assert "SURVIVED" in printed and "not mutated: parallel requests" in printed
    monkeypatch.setattr(rm, "validate", lambda directory: (_ for _ in ()).throw(StaleMutant("zzz")))
    assert rm.main(["--validate"]) == 2
    assert "STALE MUTANT: zzz" in capsys.readouterr().err


# covers: _flush_print
def test_flush_print_writes_the_line(capsys):
    rm._flush_print("progress")
    assert capsys.readouterr().out == "progress\n"


# covers: compile_all
def test_compile_all_reports_stale_and_uncompilable_mutants(tmp_path, monkeypatch, capsys):
    service = table_fixture(tmp_path, monkeypatch)
    assert rm.compile_all(service) == []
    assert rm.main(["--compile", "--service-dir", str(service)]) == 0
    assert "service mutants compile" in capsys.readouterr().out
    stale = ServiceMutant("old", "d", (Edit("a.go", "not-there", "x"),))
    monkeypatch.setattr(rm, "SERVICE_MUTANTS", (stale,))
    problems = rm.compile_all(service)
    assert [name for name, _ in problems] == ["old"] and "matched 0 times" in problems[0][1]
    assert rm.main(["--compile", "--service-dir", str(service)]) == 2
    assert "INVALID MUTANT old" in capsys.readouterr().err
