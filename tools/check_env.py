"""Check that the toolchain required by the assignment is installed.

Usage: ``python tools/check_env.py``

Prints a table "tool / found / required / status" and exits with 0 when every
tool is present in a sufficient version, 1 otherwise.  External commands are
run through an injectable ``runner`` so that tests never depend on the host.

Known limitations:
    * C++17 support is inferred from the compiler version (g++ >= 7,
      clang++ >= 5), no test compilation is performed.
    * A version is the first dotted number sequence (``\\d+(.\\d+)+``) found in
      the tool's output; tools printing a bare integer are reported as
      "version unknown".
"""
from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, Optional, Sequence, TextIO

Runner = Callable[[Sequence[str]], Optional[str]]

HEADERS = ("инструмент", "найдено", "нужно", "статус")


@dataclass(frozen=True)
class Candidate:
    """One way of providing a tool: a command line and the minimal version."""

    argv: tuple[str, ...]
    minimum: tuple[int, ...]
    label: str


@dataclass(frozen=True)
class ToolSpec:
    """A required tool, satisfied by any one of its candidates."""

    name: str
    candidates: tuple[Candidate, ...]


@dataclass(frozen=True)
class CheckResult:
    """Outcome of checking one tool."""

    name: str
    found: str
    required: str
    ok: bool


def parse_version(text: str) -> tuple[int, ...] | None:
    """Extract the first dotted version number from command output.

    Args:
        text: Raw output of a ``--version`` style command; may be empty.

    Returns:
        The version as a tuple of ints (e.g. ``(3, 29, 1)``), or None when the
        text contains no dotted number sequence.

    Raises:
        None.
    """
    match = re.search(r"\d+(?:\.\d+)+", text)
    if match is None:
        return None
    return tuple(int(part) for part in match.group(0).split("."))


def compare_versions(left: tuple[int, ...], right: tuple[int, ...]) -> int:
    """Compare two versions numerically, padding the shorter with zeros.

    Args:
        left: First version, non-negative ints.
        right: Second version, non-negative ints.

    Returns:
        -1 if left < right, 0 if they are equal, 1 if left > right.

    Raises:
        None.
    """
    size = max(len(left), len(right))
    padded_left = left + (0,) * (size - len(left))
    padded_right = right + (0,) * (size - len(right))
    if padded_left < padded_right:
        return -1
    if padded_left > padded_right:
        return 1
    return 0


def required_text(spec: ToolSpec) -> str:
    """Describe the version requirement of a tool for the table.

    Args:
        spec: Tool specification with at least one candidate.

    Returns:
        Text such as ``"g++ >= 7 | clang++ >= 5"``, one part per candidate.

    Raises:
        None.
    """
    parts = []
    for candidate in spec.candidates:
        version = ".".join(str(part) for part in candidate.minimum)
        parts.append(f"{candidate.label} >= {version}")
    return " | ".join(parts)


def check_tool(spec: ToolSpec, runner: Runner) -> CheckResult:
    """Check one tool by running each candidate through ``runner``.

    Args:
        spec: Tool to check; candidates are tried in order.
        runner: Callable taking an argv sequence and returning the command's
            combined output, or None when it is missing or failed.

    Returns:
        A passing result for the first candidate meeting its minimum version;
        otherwise a failing result describing the best thing found
        ("not found" when no candidate ran at all).

    Raises:
        None.
    """
    required = required_text(spec)
    best: str | None = None
    for candidate in spec.candidates:
        output = runner(candidate.argv)
        if output is None:
            continue
        version = parse_version(output)
        if version is None:
            best = best or f"{candidate.label} (version unknown)"
            continue
        found = f"{candidate.label} {'.'.join(str(part) for part in version)}"
        if compare_versions(version, candidate.minimum) >= 0:
            return CheckResult(spec.name, found, required, True)
        best = found
    return CheckResult(spec.name, best or "not found", required, False)


def render_table(results: Sequence[CheckResult]) -> str:
    """Format check results as an aligned text table.

    Args:
        results: Results to show, one row each; may be empty.

    Returns:
        Multi-line string with a header, a separator and one row per result;
        the status column holds ``OK`` or ``FAIL``.  No trailing newline.

    Raises:
        None.
    """
    rows = [HEADERS]
    for result in results:
        rows.append((result.name, result.found, result.required, "OK" if result.ok else "FAIL"))
    widths = [max(len(row[col]) for row in rows) for col in range(len(HEADERS))]
    lines = []
    for index, row in enumerate(rows):
        lines.append("  ".join(cell.ljust(widths[col]) for col, cell in enumerate(row)).rstrip())
        if index == 0:
            lines.append("  ".join("-" * width for width in widths))
    return "\n".join(lines)


def default_specs() -> tuple[ToolSpec, ...]:
    """Build the list of tools required by the assignment.

    Args:
        None.

    Returns:
        Specs for the C++ compiler, cmake, go, python, pytest, docker and git.

    Raises:
        None.
    """
    return (
        ToolSpec(
            "C++ compiler (C++17)",
            (
                Candidate(("g++", "--version"), (7,), "g++"),
                Candidate(("clang++", "--version"), (5,), "clang++"),
            ),
        ),
        ToolSpec("cmake", (Candidate(("cmake", "--version"), (3, 20), "cmake"),)),
        ToolSpec("go", (Candidate(("go", "version"), (1, 22), "go"),)),
        ToolSpec("python", (Candidate((sys.executable, "--version"), (3, 10), "python"),)),
        ToolSpec("pytest", (Candidate((sys.executable, "-m", "pytest", "--version"), (7,), "pytest"),)),
        ToolSpec("docker", (Candidate(("docker", "--version"), (24,), "docker"),)),
        ToolSpec("git", (Candidate(("git", "--version"), (1,), "git"),)),
    )


def default_runner(argv: Sequence[str]) -> str | None:
    """Run a command and capture its output (the real, non-test runner).

    Args:
        argv: Command and arguments; the first item is looked up on PATH.

    Returns:
        Combined stdout and stderr, or None when the command cannot be
        started, times out (15 s) or exits with a non-zero status.

    Raises:
        None.
    """
    try:
        proc = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout + proc.stderr


def main(argv: Sequence[str], runner: Runner | None = None, out: TextIO | None = None) -> int:
    """Run all environment checks and print the result table.

    Args:
        argv: Command-line arguments without the program name; must be empty.
        runner: Command runner; defaults to :func:`default_runner`.
        out: Text stream for the report; defaults to ``sys.stdout``.

    Returns:
        0 when all tools are present and new enough, 1 when any is missing or
        too old, 2 when unexpected arguments were given.

    Raises:
        None.
    """
    stream = out if out is not None else sys.stdout
    if list(argv):
        stream.write("usage: check_env.py\n")
        return 2
    active_runner = runner if runner is not None else default_runner
    results = [check_tool(spec, active_runner) for spec in default_specs()]
    stream.write(render_table(results) + "\n")
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
