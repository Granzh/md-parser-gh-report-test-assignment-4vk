import io
import subprocess
import sys

import check_env
from check_env import Candidate, CheckResult, ToolSpec

GCC = ToolSpec("cxx", (Candidate(("g++",), (7,), "g++"), Candidate(("clang++",), (5,), "clang++")))


def fake_runner(outputs):
    """Build a runner answering from a dict keyed by argv[0].

    Args:
        outputs: Mapping of command name to output text; missing names mean "not installed".

    Returns:
        A runner callable compatible with check_env.Runner.

    Raises:
        None.
    """

    def run(argv):
        """Look up the canned output for a command.

        Args:
            argv: Command line; argv[0] is the key, or "pytest" when "-m" is present.

        Returns:
            Canned text or None.

        Raises:
            None.
        """
        return outputs.get("pytest" if "-m" in argv else argv[0])

    return run


# covers: parse_version
def test_parse_version_extracts_dotted_number():
    assert check_env.parse_version("cmake version 3.29.1\nmore") == (3, 29, 1)
    assert check_env.parse_version("go version go1.22.1 windows/amd64") == (1, 22, 1)


# covers: parse_version
def test_parse_version_ignores_arch_digits_and_missing():
    text = "g++ (MinGW-W64 x86_64-ucrt-posix-seh, built by X) 13.1.0"
    assert check_env.parse_version(text) == (13, 1, 0)
    assert check_env.parse_version("no digits here") is None
    assert check_env.parse_version("") is None
    assert check_env.parse_version("only 24") is None


# covers: compare_versions
def test_compare_versions_orders_and_pads():
    assert check_env.compare_versions((3, 20), (3, 20, 0)) == 0
    assert check_env.compare_versions((3, 19, 9), (3, 20)) == -1
    assert check_env.compare_versions((4,), (3, 99)) == 1
    assert check_env.compare_versions((), ()) == 0


# covers: required_text
def test_required_text_lists_candidates():
    assert check_env.required_text(GCC) == "g++ >= 7 | clang++ >= 5"
    single = ToolSpec("cmake", (Candidate(("cmake",), (3, 20), "cmake"),))
    assert check_env.required_text(single) == "cmake >= 3.20"


# covers: check_tool
def test_check_tool_accepts_first_sufficient_candidate():
    runner = fake_runner({"g++": "g++ 13.1.0"})
    result = check_env.check_tool(GCC, runner)
    assert result == CheckResult("cxx", "g++ 13.1.0", "g++ >= 7 | clang++ >= 5", True)


# covers: check_tool
def test_check_tool_falls_back_to_second_candidate():
    runner = fake_runner({"g++": "g++ 5.4.0", "clang++": "clang version 18.1.8"})
    result = check_env.check_tool(GCC, runner)
    assert result.ok and result.found == "clang++ 18.1.8"


# covers: check_tool
def test_check_tool_reports_too_old_missing_and_unknown():
    old = check_env.check_tool(GCC, fake_runner({"g++": "g++ 5.4.0"}))
    assert not old.ok and old.found == "g++ 5.4.0"
    missing = check_env.check_tool(GCC, fake_runner({}))
    assert not missing.ok and missing.found == "not found"
    unknown = check_env.check_tool(GCC, fake_runner({"g++": "weird"}))
    assert not unknown.ok and unknown.found == "g++ (version unknown)"


# covers: check_tool
def test_check_tool_boundary_version_is_accepted():
    spec = ToolSpec("cmake", (Candidate(("cmake",), (3, 20), "cmake"),))
    assert check_env.check_tool(spec, fake_runner({"cmake": "cmake version 3.20.0"})).ok
    assert not check_env.check_tool(spec, fake_runner({"cmake": "cmake version 3.19.9"})).ok


# covers: render_table
def test_render_table_has_header_separator_and_rows():
    rows = [CheckResult("git", "git 2.43.0", "git >= 1", True), CheckResult("go", "not found", "go >= 1.22", False)]
    lines = check_env.render_table(rows).split("\n")
    assert lines[0].startswith("инструмент")
    assert set(lines[1]) <= {"-", " "}
    assert lines[2].rstrip().endswith("OK")
    assert lines[3].rstrip().endswith("FAIL")


# covers: render_table
def test_render_table_empty_has_only_header():
    assert len(check_env.render_table([]).split("\n")) == 2


# covers: default_specs
def test_default_specs_cover_required_tools():
    names = [spec.name for spec in check_env.default_specs()]
    assert names == ["C++ compiler (C++17)", "cmake", "go", "python", "pytest", "docker", "git"]
    assert all(spec.candidates for spec in check_env.default_specs())


# covers: default_runner
def test_default_runner_captures_output():
    out = check_env.default_runner([sys.executable, "-c", "print('hello')"])
    assert out is not None and "hello" in out


# covers: default_runner
def test_default_runner_none_on_missing_or_failing_command():
    assert check_env.default_runner(["definitely-not-a-real-command-xyz"]) is None
    assert check_env.default_runner([sys.executable, "-c", "raise SystemExit(3)"]) is None


# covers: default_runner
def test_default_runner_none_on_timeout(monkeypatch):
    def boom(*args, **kwargs):
        """Simulate a hanging command.

        Args:
            args: Ignored positional arguments.
            kwargs: Ignored keyword arguments.

        Raises:
            subprocess.TimeoutExpired: always.
        """
        raise subprocess.TimeoutExpired(cmd="x", timeout=1)

    monkeypatch.setattr(check_env.subprocess, "run", boom)
    assert check_env.default_runner(["anything"]) is None


# covers: main
def test_main_returns_zero_when_everything_present():
    outputs = {
        "g++": "g++ 13.1.0",
        "cmake": "cmake version 3.29.1",
        "go": "go version go1.22.1 linux/amd64",
        sys.executable: "Python 3.11.9",
        "pytest": "pytest 9.0.2",
        "docker": "Docker version 24.0.7, build x",
        "git": "git version 2.43.0",
    }
    out = io.StringIO()
    assert check_env.main([], fake_runner(outputs), out) == 0
    assert "FAIL" not in out.getvalue()


# covers: main
def test_main_returns_one_when_something_missing():
    out = io.StringIO()
    assert check_env.main([], fake_runner({}), out) == 1
    assert out.getvalue().count("FAIL") == 7


# covers: main
def test_main_rejects_unexpected_arguments():
    out = io.StringIO()
    assert check_env.main(["--bogus"], fake_runner({}), out) == 2
    assert "usage" in out.getvalue()


# covers: main
def test_main_uses_default_runner_and_stdout(capsys):
    code = check_env.main([])
    assert code in (0, 1)
    assert "инструмент" in capsys.readouterr().out
