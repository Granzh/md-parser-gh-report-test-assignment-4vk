"""Run every check of the project on a clean clone of the committed state.

Usage: ``python tools/verify_all.py [--with-mutants] [--only STEP[,STEP...]] [--keep] [--repo DIR]``

The script clones the repository with ``git clone --no-hardlinks`` into a temporary directory (so only committed files
are verified), runs the steps below one after another and prints a table ``step / status / seconds / details``.

Steps: env, msgfmt-release, msgfmt-sanitize, service, contracts, workflows, hygiene, selfchecks, suite, docker-image,
docker-run and, with ``--with-mutants``, mutants.  A step is ``ok``, ``fail`` or ``skip`` (with a reason, e.g. no Docker daemon, sanitizers
unavailable on the host compiler).  Exit code: 0 when no step failed, 1 otherwise.

All external commands go through an injectable ``runner`` and the stub server through an injectable ``start_stub``,
so the unit tests never touch the host toolchain.

Known limitations:
    * ``docker-run`` needs ``--network host`` to reach the loopback stub server, which works only on a Linux Docker
      host; on Windows/macOS Docker Desktop the step is skipped.
    * Sanitizer support is probed by compiling and running a trivial program with the host C++ compiler.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import queue
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, ContextManager, Iterator, Mapping, Optional, Sequence, TextIO

OK = "ok"
FAIL = "fail"
SKIP = "skip"

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "ghreport"
#: Substrings that mean a sanitizer printed a report.
SANITIZER_MARKERS = ("ERROR: AddressSanitizer", "ERROR: LeakSanitizer", "runtime error:", "UndefinedBehaviorSanitizer",
                     "SUMMARY: AddressSanitizer", "SUMMARY: UndefinedBehaviorSanitizer")
#: Seconds for a build or a test run; the longest legitimate step (suite via Docker) stays far below it.
STEP_TIMEOUT = 1800.0


@dataclass(frozen=True)
class CmdResult:
    """Outcome of one external command.

    Attributes:
        returncode: Exit status; 127 when the program is missing, 124 on timeout.
        stdout: Captured standard output (text).
        stderr: Captured standard error (text).
    """

    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def output(self) -> str:
        """Both streams joined, stdout first.

        Returns:
            ``stdout + stderr``.

        Raises:
            None.
        """
        return self.stdout + self.stderr


Runner = Callable[..., CmdResult]
StubFactory = Callable[[], ContextManager[str]]


@dataclass(frozen=True)
class StepOutcome:
    """What a step reports about itself.

    Attributes:
        status: One of OK, FAIL, SKIP.
        detail: One-line summary, or the failing command with the tail of its output.
    """

    status: str
    detail: str = ""


@dataclass(frozen=True)
class StepResult:
    """A finished step as shown in the table.

    Attributes:
        name: Step name.
        status: One of OK, FAIL, SKIP.
        seconds: Wall-clock duration.
        detail: Summary or failure text.
    """

    name: str
    status: str
    seconds: float
    detail: str = ""


@dataclass
class Context:
    """Everything a step needs.

    Attributes:
        repo: Root of the clean clone under test.
        work: Scratch directory for build trees and binaries (outside the clone).
        runner: Callable ``(argv, cwd=None, env=None, timeout=None) -> CmdResult``.
        start_stub: Zero-argument factory of a context manager that yields the stub server's base URL.
        platform: ``sys.platform`` value of the host.
        python: Python interpreter used for the child scripts.
        env: Base environment for child processes.
        state: Values passed between steps (``binary`` is the built service).
    """

    repo: Path
    work: Path
    runner: Runner
    start_stub: StubFactory
    platform: str = sys.platform
    python: str = sys.executable
    env: Mapping[str, str] = field(default_factory=dict)
    state: dict = field(default_factory=dict)


def default_runner(argv: Sequence[str], cwd: Optional[Path] = None, env: Optional[Mapping[str, str]] = None,
                   timeout: Optional[float] = None) -> CmdResult:
    """Run a command, capturing its output, without ever raising.

    Args:
        argv: Program and arguments, non-empty; the program is looked up on PATH.
        cwd: Working directory, or None for the current one.
        env: Complete child environment, or None to inherit.
        timeout: Seconds before the child is killed, or None for no limit.

    Returns:
        CmdResult; returncode 127 if the program is missing, 124 if the timeout expired (output so far is kept).

    Raises:
        None.
    """
    try:
        done = subprocess.run(list(argv), cwd=cwd, env=None if env is None else dict(env), capture_output=True,
                              timeout=timeout)
    except FileNotFoundError as error:
        return CmdResult(127, "", f"{argv[0]}: not found ({error})")
    except subprocess.TimeoutExpired as error:
        return CmdResult(124, _decode(error.stdout), _decode(error.stderr) + f"\ntimeout after {timeout}s")
    return CmdResult(done.returncode, _decode(done.stdout), _decode(done.stderr))


def _decode(data: Optional[bytes]) -> str:
    """Decode captured child output leniently.

    Args:
        data: Raw bytes (UTF-8 expected), or None when nothing was captured.

    Returns:
        Text with undecodable bytes replaced; "" for None.

    Raises:
        None.
    """
    return "" if data is None else data.decode("utf-8", errors="replace")


def tail_lines(text: str, count: int = 15) -> str:
    """Keep the last lines of a long output.

    Args:
        text: Any text.
        count: Number of lines to keep, >= 1.

    Returns:
        The last ``count`` non-empty-trailing lines joined by newlines.

    Raises:
        None.
    """
    return "\n".join(text.rstrip().splitlines()[-count:])


def scan_sanitizer_output(text: str) -> list:
    """Find sanitizer reports in test output.

    Args:
        text: Combined output of a test run.

    Returns:
        The lines containing a sanitizer marker, in order; empty when the run was clean.

    Raises:
        None.
    """
    return [line for line in text.splitlines() if any(marker in line for marker in SANITIZER_MARKERS)]


def count_passed(text: str) -> Optional[int]:
    """Read the number of passed tests from a pytest summary line.

    Args:
        text: Output of ``pytest``.

    Returns:
        The N of the last ``N passed`` occurrence, or None when there is none.

    Raises:
        None.
    """
    found = re.findall(r"(\d+) passed", text)
    return int(found[-1]) if found else None


def is_non_root_user(user: str) -> bool:
    """Decide whether a Dockerfile ``USER`` value is unprivileged.

    Args:
        user: ``Config.User`` of an image: "", a name, a numeric uid or ``user:group``.

    Returns:
        False for an empty value, ``root`` or uid 0 (with any group); True otherwise.

    Raises:
        None.
    """
    name = user.split(":", 1)[0].strip()
    return name not in ("", "root", "0")


def format_table(results: Sequence[StepResult]) -> str:
    """Render the summary table.

    Args:
        results: Finished steps in execution order.

    Returns:
        Text with a header, one row per step (name, status, seconds, first line of the detail) and a totals line.

    Raises:
        None.
    """
    rows = [(r.name, r.status, f"{r.seconds:.1f}", r.detail.splitlines()[0] if r.detail else "") for r in results]
    headers = ("step", "status", "sec", "details")
    widths = [max(len(headers[i]), *(len(row[i]) for row in rows)) if rows else len(headers[i]) for i in range(3)]
    lines = ["  ".join(headers[i].ljust(widths[i]) for i in range(3)) + "  " + headers[3],
             "  ".join("-" * widths[i] for i in range(3)) + "  -------"]
    lines += ["  ".join(row[i].ljust(widths[i]) for i in range(3)) + "  " + row[3] for row in rows]
    counts = {status: sum(1 for r in results if r.status == status) for status in (OK, FAIL, SKIP)}
    lines.append(f"total: {counts[OK]} ok, {counts[FAIL]} fail, {counts[SKIP]} skip, "
                 f"{sum(r.seconds for r in results):.1f} s")
    return "\n".join(lines)


def exit_code(results: Sequence[StepResult]) -> int:
    """Map step results to the process exit status.

    Args:
        results: Finished steps.

    Returns:
        1 if any step failed, else 0 (skips do not fail the run).

    Raises:
        None.
    """
    return 1 if any(r.status == FAIL for r in results) else 0


def exe_name(base: str, platform: str) -> str:
    """Give an executable file name for the platform.

    Args:
        base: Name without extension, non-empty.
        platform: ``sys.platform`` value.

    Returns:
        ``base + ".exe"`` on Windows, ``base`` elsewhere.

    Raises:
        None.
    """
    return base + ".exe" if platform.startswith("win") else base


def failure(label: str, result: CmdResult) -> StepOutcome:
    """Build the FAIL outcome of a command.

    Args:
        label: Human-readable command name.
        result: Its non-zero result.

    Returns:
        StepOutcome(FAIL) whose detail is ``"<label>: exit <code>"`` and then the tail of the output.

    Raises:
        None.
    """
    return StepOutcome(FAIL, f"{label}: exit {result.returncode}\n{tail_lines(result.output)}")


def run_sequence(ctx: Context, commands: Sequence[tuple], env: Optional[Mapping[str, str]] = None,
                 timeout: float = STEP_TIMEOUT) -> tuple:
    """Run labelled commands until one fails.

    Args:
        ctx: Step context (runner and base environment).
        commands: Items ``(label, argv, cwd)``; cwd is a Path or None.
        env: Complete environment for the children, or None for ``ctx.env``.
        timeout: Seconds allowed per command.

    Returns:
        ``(results, outcome)``: the results of the commands run so far and a FAIL StepOutcome for the first
        non-zero exit, or None when all succeeded.

    Raises:
        None.
    """
    results = []
    for label, argv, cwd in commands:
        result = ctx.runner(argv, cwd, ctx.env if env is None else env, timeout)
        results.append(result)
        if result.returncode != 0:
            return results, failure(label, result)
    return results, None


def make_clone(ctx_runner: Runner, source: Path, destination: Path) -> Optional[str]:
    """Clone the committed state of a repository without hard links.

    Args:
        ctx_runner: Command runner.
        source: Repository to clone (any git working tree or bare repo).
        destination: Directory to create; must not exist.

    Returns:
        None on success, else the failure text (command output tail).

    Raises:
        None.
    """
    result = ctx_runner(["git", "clone", "--no-hardlinks", "--quiet", str(source), str(destination)], None, None, 600)
    return None if result.returncode == 0 else f"git clone: exit {result.returncode}\n{tail_lines(result.output)}"


@contextlib.contextmanager
def default_start_stub(python: str, repo: Path) -> Iterator[str]:
    """Run ``api_tests/selfcheck/stub_server.py`` of the clone on a free port.

    Args:
        python: Interpreter path.
        repo: Root of the clone containing the stub server.

    Returns:
        A context manager yielding the base URL printed by the server (``http://127.0.0.1:<port>``); the process is
        terminated on exit.

    Raises:
        RuntimeError: The server printed no ``READY`` line within 30 seconds.
    """
    script = repo / "api_tests" / "selfcheck" / "stub_server.py"
    process = subprocess.Popen([python, str(script), "--port", "0"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               text=True, cwd=repo)
    lines: queue.Queue = queue.Queue()
    threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True).start()
    try:
        try:
            line = lines.get(timeout=30)
        except queue.Empty:
            raise RuntimeError("stub server did not report READY within 30 s") from None
        if not line.startswith("READY "):
            raise RuntimeError(f"unexpected stub server output: {line!r}")
        yield line.split(" ", 1)[1].strip()
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        process.stdout.close()


def suite_env(ctx: Context, binary: str, base_url: str) -> dict:
    """Environment of a ``pytest api_tests`` run against a binary.

    Args:
        ctx: Step context; its base environment is copied.
        binary: Absolute path of the service or of the container wrapper (GHREPORT_BIN).
        base_url: Stub server URL (GITHUB_API_BASE_URL).

    Returns:
        A new dict with GHREPORT_BIN, GITHUB_API_BASE_URL, GHREPORT_STRICT=1 (no silent skips) and
        PYTHONDONTWRITEBYTECODE=1; GITHUB_TOKEN is removed.

    Raises:
        None.
    """
    env = {key: value for key, value in ctx.env.items() if key != "GITHUB_TOKEN"}
    env.update(GHREPORT_BIN=binary, GITHUB_API_BASE_URL=base_url, GHREPORT_STRICT="1", PYTHONDONTWRITEBYTECODE="1")
    return env


def step_env(ctx: Context) -> StepOutcome:
    """Step ``env``: run tools/check_env.py.

    Args:
        ctx: Step context.

    Returns:
        OK when every required tool is present, FAIL with the table otherwise.

    Raises:
        None.
    """
    _, bad = run_sequence(ctx, [("check_env", [ctx.python, "tools/check_env.py"], ctx.repo)], timeout=300)
    return bad or StepOutcome(OK, "toolchain present")


def generator_args(platform: str, env: Mapping[str, str], which: Callable[[str], Optional[str]] = shutil.which) -> list:
    """Pick a CMake generator for hosts where the default one is unusable.

    Args:
        platform: ``sys.platform`` value.
        env: Child environment; a set ``CMAKE_GENERATOR`` always wins.
        which: Program lookup (injectable), ``shutil.which`` by default.

    Returns:
        ``[]`` on non-Windows hosts or when CMAKE_GENERATOR is set; on Windows ``["-G", "Ninja"]`` if ninja is on
        PATH, else ``["-G", "MinGW Makefiles"]`` (CMake would otherwise choose a Visual Studio generator).

    Raises:
        None.
    """
    if not platform.startswith("win") or env.get("CMAKE_GENERATOR"):
        return []
    return ["-G", "Ninja"] if which("ninja") else ["-G", "MinGW Makefiles"]


def build_msgfmt(ctx: Context, name: str, extra: Sequence[str]) -> StepOutcome:
    """Configure, build and test msgfmt in a build tree outside the clone.

    Args:
        ctx: Step context.
        name: Build directory name under ``ctx.work``.
        extra: Additional ``-D`` arguments for the configure step.

    Returns:
        FAIL for the first failing command; otherwise OK, with FAIL if a sanitizer report appears in the ``ctest -V``
        output.

    Raises:
        None.
    """
    build = ctx.work / name
    commands = [
        ("cmake configure", ["cmake", "-S", str(ctx.repo / "msgfmt"), "-B", str(build), "-DCMAKE_BUILD_TYPE=Release",
                             "-DMSGFMT_WERROR=ON", *generator_args(ctx.platform, ctx.env), *extra],
         None),
        ("cmake build", ["cmake", "--build", str(build)], None),
        ("ctest", ["ctest", "--test-dir", str(build), "-V", "--output-on-failure"], None),
    ]
    results, bad = run_sequence(ctx, commands)
    if bad:
        return bad
    reports = scan_sanitizer_output(results[-1].output)
    if reports:
        return StepOutcome(FAIL, f"{len(reports)} sanitizer message(s): {reports[0]}")
    summary = re.findall(r"\d+% tests passed.*", results[-1].output)
    return StepOutcome(OK, summary[-1] if summary else "ctest passed")


def step_msgfmt_release(ctx: Context) -> StepOutcome:
    """Step ``msgfmt-release``: Release build with -Werror and ctest.

    Args:
        ctx: Step context.

    Returns:
        StepOutcome from build_msgfmt.

    Raises:
        None.
    """
    return build_msgfmt(ctx, "build-release", [])


def probe_sanitizers(ctx: Context) -> Optional[str]:
    """Check that the host C++ compiler can build and run an ASan/UBSan program.

    Args:
        ctx: Step context (``work`` receives the probe files).

    Returns:
        None when sanitizers work, else a short reason.

    Raises:
        None.
    """
    source = ctx.work / "san_probe.cpp"
    source.write_text("int main() { int a[2] = {1, 2}; return a[0] - 1; }\n", encoding="utf-8")
    binary = ctx.work / exe_name("san_probe", ctx.platform)
    compiled = ctx.runner(["c++", "-fsanitize=address,undefined", str(source), "-o", str(binary)], None, ctx.env, 300)
    if compiled.returncode != 0:
        return f"host compiler cannot build with -fsanitize=address,undefined (exit {compiled.returncode})"
    ran = ctx.runner([str(binary)], None, ctx.env, 60)
    return None if ran.returncode == 0 else f"sanitizer runtime does not start (exit {ran.returncode})"


def step_msgfmt_sanitize(ctx: Context) -> StepOutcome:
    """Step ``msgfmt-sanitize``: build with MSGFMT_SANITIZE=ON and require zero sanitizer messages.

    Args:
        ctx: Step context.

    Returns:
        SKIP when the host compiler has no working sanitizers, else the outcome of build_msgfmt.

    Raises:
        None.
    """
    reason = probe_sanitizers(ctx)
    if reason:
        return StepOutcome(SKIP, reason)
    return build_msgfmt(ctx, "build-san", ["-DMSGFMT_SANITIZE=ON"])


def step_service(ctx: Context) -> StepOutcome:
    """Step ``service``: gofmt, vet, race tests and build of the Go service.

    Args:
        ctx: Step context; on success ``state["binary"]`` receives the built executable.

    Returns:
        OK, or FAIL for the first failing check (``gofmt -l`` printing any file name counts as failure).

    Raises:
        None.
    """
    service = ctx.repo / "service"
    binary = ctx.work / "bin" / exe_name("ghreport", ctx.platform)
    binary.parent.mkdir(parents=True, exist_ok=True)
    formatted = ctx.runner(["gofmt", "-l", "."], service, ctx.env, 300)
    if formatted.returncode != 0:
        return failure("gofmt -l", formatted)
    if formatted.stdout.strip():
        return StepOutcome(FAIL, "gofmt -l lists unformatted files:\n" + tail_lines(formatted.stdout))
    commands = [
        ("go vet", ["go", "vet", "./..."], service),
        ("go test -race", ["go", "test", "./...", "-race", "-count=1"], service),
        ("go build", ["go", "build", "-o", str(binary), "./cmd/ghreport"], service),
    ]
    results, bad = run_sequence(ctx, commands)
    if bad:
        return bad
    ctx.state["binary"] = str(binary)
    return StepOutcome(OK, f"gofmt/vet/test -race/build ok, {len(re.findall(r'^ok ', results[1].output, re.M))} packages")


def step_contracts(ctx: Context) -> StepOutcome:
    """Step ``contracts``: lint contracts and ``covers:`` labels of msgfmt, service, api_tests and tools.

    Args:
        ctx: Step context.

    Returns:
        OK or FAIL for the first directory with violations.

    Raises:
        None.
    """
    targets = (("msgfmt", "msgfmt/tests"), ("service", "service"), ("api_tests", "api_tests"),
               ("tools", "tools/tests"))
    commands = [(f"contracts {root}", [ctx.python, "tools/check_contracts.py", root, "--tests", tests,
                                      "--check-covers"], ctx.repo) for root, tests in targets]
    _, bad = run_sequence(ctx, commands, timeout=600)
    return bad or StepOutcome(OK, "msgfmt, service, api_tests, tools: no violations")


def step_hygiene(ctx: Context) -> StepOutcome:
    """Step ``hygiene``: tools/check_repo.py and tools/check_repo_hygiene.py --history on the clone.

    Args:
        ctx: Step context.

    Returns:
        OK with the summary line of check_repo.py, or FAIL with the findings of the first failing script.

    Raises:
        None.
    """
    commands = [("check_repo", [ctx.python, "tools/check_repo.py", "--repo", str(ctx.repo)], ctx.repo),
                ("check_repo_hygiene", [ctx.python, "tools/check_repo_hygiene.py", "--repo", str(ctx.repo),
                                        "--history"], ctx.repo)]
    results, bad = run_sequence(ctx, commands, timeout=900)
    return bad or StepOutcome(OK, tail_lines(results[0].stdout, 1))


def step_workflows(ctx: Context) -> StepOutcome:
    """Step ``workflows``: tools/check_workflows.py over .github/workflows of the clone.

    Args:
        ctx: Step context.

    Returns:
        OK with the summary line (files, errors, warnings), or FAIL with the findings.

    Raises:
        None.
    """
    results, bad = run_sequence(ctx, [("check_workflows", [ctx.python, "tools/check_workflows.py",
                                                          ".github/workflows"], ctx.repo)])
    return bad or StepOutcome(OK, tail_lines(results[0].stdout, 1))


def step_selfchecks(ctx: Context) -> StepOutcome:
    """Step ``selfchecks``: pytest of tools/tests and of the api_tests meta-tests.

    Args:
        ctx: Step context.

    Returns:
        OK with the test counts, or FAIL for the first failing run.

    Raises:
        None.
    """
    env = dict(ctx.env, PYTHONDONTWRITEBYTECODE="1")
    commands = [("pytest tools/tests", [ctx.python, "-m", "pytest", "tools/tests", "-q"], ctx.repo),
                ("pytest api_tests -m selfcheck",
                 [ctx.python, "-m", "pytest", "api_tests", "-m", "selfcheck", "-q"], ctx.repo)]
    results, bad = run_sequence(ctx, commands, env=env)
    if bad:
        return bad
    return StepOutcome(OK, f"tools/tests {count_passed(results[0].output)} passed, "
                           f"selfcheck {count_passed(results[1].output)} passed")


def step_suite(ctx: Context) -> StepOutcome:
    """Step ``suite``: the main api_tests suite against the built service and the stub server.

    Args:
        ctx: Step context; needs ``state["binary"]`` from step_service.

    Returns:
        SKIP if the binary was not built, FAIL if pytest fails (GHREPORT_STRICT=1 turns skips into failures), else OK.

    Raises:
        None.
    """
    binary = ctx.state.get("binary")
    if not binary:
        return StepOutcome(SKIP, "service binary was not built")
    with ctx.start_stub() as url:
        results, bad = run_sequence(ctx, [("pytest api_tests", [ctx.python, "-m", "pytest", "api_tests", "-q"],
                                          ctx.repo)], env=suite_env(ctx, binary, url))
    if bad:
        return bad
    return StepOutcome(OK, f"pytest api_tests -q: {count_passed(results[0].output)} passed (strict mode, no skips)")


def docker_available(ctx: Context) -> Optional[str]:
    """Check that a Docker daemon answers.

    Args:
        ctx: Step context.

    Returns:
        None when ``docker info`` succeeds, else the reason to skip.

    Raises:
        None.
    """
    result = ctx.runner(["docker", "info", "--format", "{{.ServerVersion}}"], None, ctx.env, 60)
    return None if result.returncode == 0 else "docker daemon is not available"


def step_docker_image(ctx: Context) -> StepOutcome:
    """Step ``docker-image``: build the image, check USER and the exit code 2 smoke run.

    Args:
        ctx: Step context.

    Returns:
        SKIP without a Docker daemon; FAIL when the build fails, USER is root/empty, or
        ``docker run ghreport --repos`` (no value) does not exit 2 with empty stdout; else OK.

    Raises:
        None.
    """
    reason = docker_available(ctx)
    if reason:
        return StepOutcome(SKIP, reason)
    _, bad = run_sequence(ctx, [("docker build", ["docker", "build", "-t", IMAGE, "service/"], ctx.repo)])
    if bad:
        return bad
    inspected = ctx.runner(["docker", "image", "inspect", "--format", "{{.Config.User}}", IMAGE], None, ctx.env, 60)
    if inspected.returncode != 0:
        return failure("docker image inspect", inspected)
    user = inspected.stdout.strip()
    if not is_non_root_user(user):
        return StepOutcome(FAIL, f"image runs as root (USER={user!r})")
    smoke = ctx.runner(["docker", "run", "--rm", IMAGE, "--repos"], None, ctx.env, 120)
    if smoke.returncode != 2 or smoke.stdout.strip():
        return StepOutcome(FAIL, f"smoke `--repos` without value: exit {smoke.returncode}, "
                                 f"stdout {smoke.stdout.strip()!r} (expected exit 2, empty stdout)")
    return StepOutcome(OK, f"image built, USER={user}, `--repos` without value exits 2 with empty stdout")


def step_docker_run(ctx: Context) -> StepOutcome:
    """Step ``docker-run``: full run from the container against the stub, then the suite through the wrapper.

    Args:
        ctx: Step context.

    Returns:
        SKIP when not on Linux (host networking), without Docker or without the image; FAIL when the report from the
        container is wrong or pytest through ``tools/ghreport-docker.sh`` fails; else OK.

    Raises:
        None.
    """
    if not ctx.platform.startswith("linux"):
        return StepOutcome(SKIP, f"--network host reaches the host loopback only on Linux (platform {ctx.platform})")
    reason = docker_available(ctx)
    if reason:
        return StepOutcome(SKIP, reason)
    if ctx.runner(["docker", "image", "inspect", IMAGE], None, ctx.env, 60).returncode != 0:
        return StepOutcome(SKIP, f"image {IMAGE} was not built")
    wrapper = ctx.repo / "tools" / "ghreport-docker.sh"
    with ctx.start_stub() as url:
        env = suite_env(ctx, str(wrapper), url)
        direct = ctx.runner([str(wrapper), "--repos", "stub/small", "--out", "-"], None, env, 120)
        if direct.returncode != 0:
            return failure("container run against stub", direct)
        try:
            report = json.loads(direct.stdout)
            counts = [(entry["repo"], entry["error"], entry["count"]) for entry in report["repos"]]
        except (ValueError, KeyError, TypeError):
            return StepOutcome(FAIL, "container output is not a report:\n" + tail_lines(direct.stdout))
        if counts != [("stub/small", None, 3)]:
            return StepOutcome(FAIL, f"unexpected report from the container: {counts}")
        results, bad = run_sequence(ctx, [("pytest via container wrapper",
                                          [ctx.python, "-m", "pytest", "api_tests", "-q"], ctx.repo)], env=env)
        if bad:
            return bad
    return StepOutcome(OK, f"container report ok; pytest through wrapper: {count_passed(results[0].output)} passed")


def step_mutants(ctx: Context) -> StepOutcome:
    """Step ``mutants``: api_tests/selfcheck/run_mutants.py (every mutant must be killed).

    Args:
        ctx: Step context.

    Returns:
        OK with the script's last line, or FAIL when it exits non-zero.

    Raises:
        None.
    """
    results, bad = run_sequence(ctx, [("run_mutants", [ctx.python, "api_tests/selfcheck/run_mutants.py"], ctx.repo)],
                                env=dict(ctx.env, PYTHONDONTWRITEBYTECODE="1"))
    if bad:
        return bad
    totals = [line for line in results[0].stdout.splitlines() if line.startswith("total ")]
    return StepOutcome(OK, totals[-1] if totals else tail_lines(results[0].stdout, 1))


def all_steps(with_mutants: bool) -> list:
    """List the steps in execution order.

    Args:
        with_mutants: Whether to append the (slow) mutation run.

    Returns:
        List of ``(name, function)`` pairs.

    Raises:
        None.
    """
    steps = [("env", step_env), ("msgfmt-release", step_msgfmt_release), ("msgfmt-sanitize", step_msgfmt_sanitize),
             ("service", step_service), ("contracts", step_contracts), ("workflows", step_workflows),
             ("hygiene", step_hygiene),
             ("selfchecks", step_selfchecks),
             ("suite", step_suite), ("docker-image", step_docker_image), ("docker-run", step_docker_run)]
    if with_mutants:
        steps.append(("mutants", step_mutants))
    return steps


def run_steps(steps: Sequence[tuple], ctx: Context, clock: Callable[[], float] = time.monotonic,
              progress: Optional[Callable[[str], None]] = None) -> list:
    """Run steps in order, timing each and turning exceptions into failures.

    Args:
        steps: ``(name, function)`` pairs; each function takes a Context and returns a StepOutcome.
        ctx: Shared context.
        clock: Monotonic clock in seconds (injectable for tests).
        progress: Called with a line before each step, or None.

    Returns:
        One StepResult per step; a step that raises becomes FAIL with the exception text, later steps still run.

    Raises:
        None.
    """
    results = []
    for name, function in steps:
        if progress:
            progress(f"==> {name}")
        started = clock()
        try:
            outcome = function(ctx)
        except Exception as error:  # a broken step must not hide the remaining ones
            outcome = StepOutcome(FAIL, f"exception: {type(error).__name__}: {error}")
        results.append(StepResult(name, outcome.status, clock() - started, outcome.detail))
    return results


def remove_tree(path: Path) -> None:
    """Delete a directory tree, including read-only git objects on Windows.

    Args:
        path: Directory to delete; a missing directory is ignored.

    Returns:
        None.

    Raises:
        None.
    """

    def make_writable(function, target, _info):
        """Retry a failed removal after clearing the read-only flag.

        Args:
            function: The os function that failed.
            target: Path it failed on.
            _info: Exception info (unused).

        Returns:
            None.

        Raises:
            OSError: The retry fails as well.
        """
        os.chmod(target, stat.S_IWRITE)
        function(target)

    if path.exists():
        shutil.rmtree(path, onerror=make_writable)


def parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    """Parse the command line.

    Args:
        argv: Arguments without the program name, or None for ``sys.argv[1:]``.

    Returns:
        Namespace with ``with_mutants`` (bool), ``only`` (list of step names, empty = all), ``keep`` (bool) and
        ``repo`` (Path).

    Raises:
        SystemExit: Malformed command line (argparse exits with 2).
    """
    parser = argparse.ArgumentParser(prog="verify_all.py", description="Verify a clean clone of the repository.")
    parser.add_argument("--with-mutants", action="store_true", help="also run api_tests/selfcheck/run_mutants.py")
    parser.add_argument("--only", default="", help="comma-separated step names to run")
    parser.add_argument("--keep", action="store_true", help="keep the temporary clone")
    parser.add_argument("--repo", type=Path, default=ROOT, help="repository to clone (default: this one)")
    args = parser.parse_args(argv)
    args.only = [name for name in args.only.split(",") if name]
    return args


def main(argv: Optional[Sequence[str]] = None, runner: Optional[Runner] = None,
         start_stub: Optional[Callable[[Path], ContextManager[str]]] = None, out: Optional[TextIO] = None) -> int:
    """Clone, run the steps and print the table.

    Args:
        argv: Command-line arguments, or None for ``sys.argv[1:]``.
        runner: Command runner; default_runner when None.
        start_stub: Function ``repo -> context manager`` yielding the stub URL; the real stub server when None.
        out: Stream for progress and the table; stdout when None.

    Returns:
        0 when no step failed, 1 when a step or the clone failed, 2 for an unknown ``--only`` step.

    Raises:
        SystemExit: Malformed command line.
    """
    args = parse_args(argv)
    stream = out if out is not None else sys.stdout
    run = runner or default_runner
    steps = all_steps(args.with_mutants)
    known = {name for name, _ in steps}
    unknown = [name for name in args.only if name not in known]
    if unknown:
        stream.write(f"unknown step(s): {', '.join(unknown)}; known: {', '.join(sorted(known))}\n")
        return 2
    if args.only:
        steps = [step for step in steps if step[0] in args.only]
    dirty = run(["git", "status", "--porcelain"], args.repo, None, 60)
    if dirty.stdout.strip():
        stream.write("note: uncommitted changes are NOT verified (only the committed state is cloned)\n")
    work = Path(tempfile.mkdtemp(prefix="verify_all_"))
    try:
        clone = work / "repo"
        problem = make_clone(run, args.repo, clone)
        if problem:
            stream.write(problem + "\n")
            return 1
        env = {key: value for key, value in os.environ.items() if key not in ("GITHUB_TOKEN", "GITHUB_API_BASE_URL")}
        factory = (lambda: start_stub(clone)) if start_stub else (lambda: default_start_stub(sys.executable, clone))
        ctx = Context(repo=clone, work=work, runner=run, start_stub=factory, env=env)
        stream.write(f"clean clone: {clone}\n")
        results = run_steps(steps, ctx, progress=lambda line: (stream.write(line + "\n"), stream.flush()))
        for result in results:
            if result.status == FAIL:
                stream.write(f"\n--- {result.name} failed ---\n{result.detail}\n")
        stream.write("\n" + format_table(results) + "\n")
        return exit_code(results)
    finally:
        if args.keep:
            stream.write(f"kept: {work}\n")
        else:
            remove_tree(work)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
