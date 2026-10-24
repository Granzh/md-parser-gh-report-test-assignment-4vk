"""Static security lint of GitHub Actions workflow files, without third-party libraries.

Usage: ``python tools/check_workflows.py [DIR_OR_FILE ...]`` (default: ``.github/workflows``).

The files are analysed line by line using indentation; no YAML parser is involved.  Limitations (by design):
    * flow-style mappings (``jobs: {a: {...}}``) and anchors/aliases are not understood;
    * a job is a key found exactly one indentation level below ``jobs:``;
    * a ``run:`` value is either the rest of its line or a block scalar (``|``, ``>`` with optional modifiers);
    * a ``#`` starts a comment when it is preceded by whitespace and is outside quotes;
    * reusable-workflow jobs (``uses:`` at job level) are not required to have ``timeout-minutes``.

Rules (error unless noted):
    * a top-level ``permissions`` key exists;
    * every job has ``timeout-minutes``;
    * no ``pull_request_target`` trigger;
    * no ``${{ github.event.* }}``, ``github.head_ref`` or ``github.ref_name`` expression inside ``run:``
      (script injection); pass such values through ``env:`` instead;
    * every external ``uses:`` is pinned to a 40-hex commit SHA; a version tag ``@vN[.M[.P]]`` is a *warning*; any
      other reference (``@main``, ``@master``, a branch, no reference) is an error;
    * ``secrets.`` appears only in ``release.yml`` / ``release.yaml``.

Output: ``file:line: error|warning: message`` per finding.  Exit code: 0 when there are no errors, 1 otherwise,
2 when a path cannot be read.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import NamedTuple, Optional, Sequence, TextIO

ERROR = "error"
WARNING = "warning"
#: Workflow files that may use the ``secrets`` context.
SECRETS_ALLOWED = ("release.yml", "release.yaml")

SHA_REF = re.compile(r"[0-9a-f]{40}")
VERSION_REF = re.compile(r"v\d+(\.\d+){0,2}")
KEY_LINE = re.compile(r"^(?P<indent>\s*)(?:-\s+)?(?P<key>[A-Za-z_][\w.-]*)\s*:(?:\s+(?P<value>.*))?$")
USES_LINE = re.compile(r"^\s*(?:-\s+)?uses\s*:\s*(?P<value>.+?)\s*$")
BLOCK_SCALAR = re.compile(r"^[|>][+-]?\d*$")
UNTRUSTED_EXPRESSION = re.compile(r"\$\{\{[^}]*\bgithub\.(?:event\.|head_ref\b|ref_name\b)")


class Finding(NamedTuple):
    """One lint result.

    Attributes:
        path: File the finding belongs to, as given to the checker.
        line: 1-based line number (1 for whole-file findings).
        level: ``ERROR`` or ``WARNING``.
        message: Human-readable explanation.
    """

    path: str
    line: int
    level: str
    message: str


def format_finding(finding: Finding) -> str:
    """Render a finding as one output line.

    Args:
        finding: The finding to render.

    Returns:
        ``path:line: level: message``.

    Raises:
        None.
    """
    return f"{finding.path}:{finding.line}: {finding.level}: {finding.message}"


def strip_comment(line: str) -> str:
    """Remove a trailing YAML comment.

    Args:
        line: One physical line without the line break.

    Returns:
        The line up to (not including) the first ``#`` that is preceded by whitespace or starts the line and lies
        outside single or double quotes; trailing whitespace is removed.

    Raises:
        None.
    """
    quote = ""
    for index, char in enumerate(line):
        if quote:
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char == "#" and (index == 0 or line[index - 1].isspace()):
            return line[:index].rstrip()
    return line.rstrip()


def indent_of(line: str) -> int:
    """Count the leading spaces of a line.

    Args:
        line: One physical line.

    Returns:
        Number of leading space characters (tabs are not valid YAML indentation and count as non-indent).

    Raises:
        None.
    """
    return len(line) - len(line.lstrip(" "))


def parse_key(line: str) -> Optional[tuple]:
    """Parse a ``key: value`` line (also ``- key: value``).

    Args:
        line: One comment-free physical line.

    Returns:
        ``(column, key, value)`` where ``column`` is the zero-based column of the key itself (after ``- `` for list
        items) and ``value`` is the stripped text after the colon (empty string if none), or None when the line is
        not a simple key line.

    Raises:
        None.
    """
    match = KEY_LINE.match(line)
    if not match:
        return None
    column = line.index(match.group("key"), len(match.group("indent")))
    return column, match.group("key"), (match.group("value") or "").strip()


def code_lines(text: str) -> list:
    """Split a file into numbered lines with comments removed.

    Args:
        text: Workflow file content.

    Returns:
        List of ``(line_number, line)`` with 1-based numbers; comment-only and blank lines keep their number but
        become empty strings, so indentation analysis can skip them.

    Raises:
        None.
    """
    return [(number, strip_comment(line)) for number, line in enumerate(text.splitlines(), start=1)]


def top_level_keys(lines: Sequence[tuple]) -> dict:
    """Find the keys written at column 0.

    Args:
        lines: Output of ``code_lines``.

    Returns:
        Mapping key -> line number of its first occurrence.

    Raises:
        None.
    """
    found: dict = {}
    for number, line in lines:
        parsed = parse_key(line) if line and not line[0].isspace() and not line.startswith("-") else None
        if parsed and parsed[0] == 0:
            found.setdefault(parsed[1], number)
    return found


def split_jobs(lines: Sequence[tuple]) -> dict:
    """Cut the ``jobs:`` section into per-job line lists.

    Args:
        lines: Output of ``code_lines``.

    Returns:
        Mapping job id -> ``(first_line_number, job_lines)`` where ``job_lines`` are the ``(number, line)`` pairs of
        the job body (the header line included).  Empty when there is no ``jobs:`` key.

    Raises:
        None.
    """
    start = next((i for i, (_, line) in enumerate(lines) if line.rstrip() == "jobs:"), None)
    if start is None:
        return {}
    jobs: dict = {}
    job_indent: Optional[int] = None
    current: Optional[str] = None
    for number, line in lines[start + 1:]:
        if not line.strip():
            continue
        indent = indent_of(line)
        if indent == 0:
            break
        if job_indent is None:
            job_indent = indent
        parsed = parse_key(line)
        if indent == job_indent and parsed and parsed[0] == indent:
            current = parsed[1]
            jobs[current] = (number, [])
        if current is not None:
            jobs[current][1].append((number, line))
    return jobs


def check_permissions(path: str, lines: Sequence[tuple]) -> list:
    """Require a top-level ``permissions`` key.

    Args:
        path: File name for the findings.
        lines: Output of ``code_lines``.

    Returns:
        One error if no ``permissions`` key exists at column 0, else an empty list.

    Raises:
        None.
    """
    if "permissions" in top_level_keys(lines):
        return []
    return [Finding(path, 1, ERROR, "no top-level `permissions:` (set `permissions: contents: read`)")]


def check_timeouts(path: str, lines: Sequence[tuple]) -> list:
    """Require ``timeout-minutes`` on every job.

    Args:
        path: File name for the findings.
        lines: Output of ``code_lines``.

    Returns:
        One error per job whose body has no ``timeout-minutes`` key directly under the job id.

    Raises:
        None.
    """
    findings = []
    for job, (first, body) in split_jobs(lines).items():
        child_indent = indent_of(body[1][1]) if len(body) > 1 else 0  # indentation of the first job property
        direct = [parse_key(line) for _, line in body[1:] if indent_of(line) == child_indent]
        if any(parsed and parsed[1] == "uses" for parsed in direct):
            continue  # a reusable-workflow call has no steps to bound
        if not any(parsed and parsed[1] == "timeout-minutes" for parsed in direct):
            findings.append(Finding(path, first, ERROR, f"job `{job}` has no `timeout-minutes`"))
    return findings


def check_triggers(path: str, lines: Sequence[tuple]) -> list:
    """Forbid the ``pull_request_target`` trigger.

    Args:
        path: File name for the findings.
        lines: Output of ``code_lines``.

    Returns:
        One error per code line mentioning ``pull_request_target``.

    Raises:
        None.
    """
    return [Finding(path, number, ERROR, "`pull_request_target` runs with secrets on untrusted code; do not use it")
            for number, line in lines if re.search(r"\bpull_request_target\b", line)]


def run_block_lines(lines: Sequence[tuple]) -> list:
    """Collect the text lines that belong to ``run:`` values.

    Args:
        lines: Output of ``code_lines``.

    Returns:
        List of ``(line_number, text)``: the inline value of ``run: cmd`` and every line of a ``run: |`` block scalar
        (lines indented deeper than the ``run`` key).

    Raises:
        None.
    """
    collected = []
    index = 0
    while index < len(lines):
        number, line = lines[index]
        parsed = parse_key(line) if line.strip() else None
        index += 1
        if not parsed or parsed[1] != "run":
            continue
        column, _, value = parsed
        if value and not BLOCK_SCALAR.match(value):
            collected.append((number, value))
            continue
        while index < len(lines):
            next_number, next_line = lines[index]
            if next_line.strip() and indent_of(next_line) <= column:
                break
            if next_line.strip():
                collected.append((next_number, next_line.strip()))
            index += 1
    return collected


def check_run_injection(path: str, lines: Sequence[tuple]) -> list:
    """Forbid untrusted context values interpolated into shell code.

    Args:
        path: File name for the findings.
        lines: Output of ``code_lines``.

    Returns:
        One error per ``run:`` line containing ``${{ ... github.event.*|github.head_ref|github.ref_name ... }}``.

    Raises:
        None.
    """
    return [Finding(path, number, ERROR,
                    "untrusted `${{ github.* }}` value inside `run:`; pass it via `env:` and use \"$VAR\"")
            for number, text in run_block_lines(lines) if UNTRUSTED_EXPRESSION.search(text)]


def classify_uses(value: str) -> Optional[tuple]:
    """Judge one ``uses:`` value.

    Args:
        value: The text after ``uses:`` (quotes allowed, comments already removed).

    Returns:
        None when the value is acceptable or is not an external action (local ``./path``, ``docker://``); otherwise
        ``(level, message)``: ERROR for a missing or mutable reference, WARNING for a version tag.

    Raises:
        None.
    """
    reference = value.strip().strip("\"'")
    if reference.startswith(("./", "docker://")):
        return None
    name, separator, ref = reference.partition("@")
    if not separator or not ref:
        return ERROR, f"`{name}` has no `@ref`; pin to a commit SHA"
    if SHA_REF.fullmatch(ref):
        return None
    if VERSION_REF.fullmatch(ref):
        return WARNING, f"`{name}@{ref}` is pinned to a movable version tag; pin to the full commit SHA"
    return ERROR, f"`{name}@{ref}` uses a mutable reference; pin to a commit SHA"


def check_uses(path: str, lines: Sequence[tuple]) -> list:
    """Check the pinning of every external action.

    Args:
        path: File name for the findings.
        lines: Output of ``code_lines``.

    Returns:
        One finding per ``uses:`` line judged by ``classify_uses``.

    Raises:
        None.
    """
    findings = []
    for number, line in lines:
        match = USES_LINE.match(line)
        verdict = classify_uses(match.group("value")) if match else None
        if verdict:
            findings.append(Finding(path, number, verdict[0], verdict[1]))
    return findings


def check_secrets(path: str, lines: Sequence[tuple]) -> list:
    """Forbid the ``secrets`` context outside the release workflow.

    Args:
        path: File name (its base name decides whether secrets are allowed).
        lines: Output of ``code_lines``.

    Returns:
        One error per code line containing ``secrets.`` unless the file is in ``SECRETS_ALLOWED``.

    Raises:
        None.
    """
    if Path(path).name in SECRETS_ALLOWED:
        return []
    return [Finding(path, number, ERROR, "`secrets.` is only allowed in release.yml; CI needs no secrets")
            for number, line in lines if re.search(r"\bsecrets\.", line)]


def check_workflow(path: str, text: str) -> list:
    """Run every rule on one workflow.

    Args:
        path: File name used in findings and for the ``secrets`` rule.
        text: File content.

    Returns:
        All findings ordered by line number.

    Raises:
        None.
    """
    lines = code_lines(text)
    findings = []
    for check in (check_permissions, check_timeouts, check_triggers, check_run_injection, check_uses, check_secrets):
        findings += check(path, lines)
    return sorted(findings, key=lambda finding: finding.line)


def workflow_files(targets: Sequence[str]) -> list:
    """Expand files and directories into workflow files.

    Args:
        targets: Paths of files or directories.

    Returns:
        Sorted paths of ``*.yml`` / ``*.yaml`` files: a file is taken as is, a directory contributes its direct
        children.

    Raises:
        FileNotFoundError: A target does not exist.
    """
    files = []
    for target in targets:
        path = Path(target)
        if path.is_dir():
            files += [child for child in path.iterdir() if child.suffix in (".yml", ".yaml")]
        elif path.is_file():
            files.append(path)
        else:
            raise FileNotFoundError(target)
    return sorted(files)


def main(argv: Optional[Sequence[str]] = None, out: Optional[TextIO] = None) -> int:
    """Command-line entry point.

    Args:
        argv: Arguments without the program name, or None for ``sys.argv[1:]``.
        out: Text stream for the report, or None for stdout.

    Returns:
        0 when there are no errors (warnings allowed), 1 when errors exist, 2 when a path cannot be read or no
        workflow file was found.

    Raises:
        SystemExit: Malformed command line.
    """
    parser = argparse.ArgumentParser(prog="check_workflows.py", description="Security lint of workflow files.")
    parser.add_argument("targets", nargs="*", default=[".github/workflows"], help="workflow files or directories")
    args = parser.parse_args(argv)
    stream = out if out is not None else sys.stdout
    try:
        files = workflow_files(args.targets)
        findings = []
        for file in files:
            findings += check_workflow(file.as_posix(), file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as error:
        stream.write(f"error: {error}\n")
        return 2
    if not files:
        stream.write("error: no workflow files found\n")
        return 2
    for finding in findings:
        stream.write(format_finding(finding) + "\n")
    errors = sum(1 for finding in findings if finding.level == ERROR)
    stream.write(f"{len(files)} file(s), {errors} error(s), {len(findings) - errors} warning(s)\n")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
