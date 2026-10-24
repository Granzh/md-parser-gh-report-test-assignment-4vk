"""CI hygiene gate: secrets, binaries, oversized files and .gitignore, in the working tree and (optionally) history.

Usage: ``python tools/check_repo_hygiene.py [--repo DIR] [--history] [--max-bytes N]``

Checks (all read-only):
    * every file of the working tree that git knows (tracked, or untracked and not ignored) is scanned: secret
      patterns (``ghp_``, ``gh[ousr]_``, ``github_pat_``, ``Bearer <20+ characters>``; the sentinel
      ``test-token-DO-NOT-USE`` is allowed), forbidden paths (``.env``, build directories, binaries by suffix),
      executable magic numbers (ELF, Mach-O, PE) and size above ``--max-bytes`` (default 1 MiB);
    * ``.gitignore`` exists and lists the mandatory entries (build/, .ghreport-cache/, __pycache__/, .pytest_cache/,
      ``*.token``, ``.env``);
    * with ``--history``: the patches of every commit on every ref are scanned for secrets, and every blob ever
      committed is checked for forbidden path, size and magic number.

The detection primitives live in ``check_repo.py`` (shared with ``verify_all``); this script adds the working-tree
scan, the configurable size limit and a CI-friendly exit code.

Exit code: 0 when nothing is found, 1 when there are findings, 2 when git fails.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence, TextIO

import check_repo as shared
from check_repo import Finding, Git


def working_tree_paths(git: Git) -> list:
    """List the files git considers part of the working tree.

    Args:
        git: Git runner (see ``check_repo.run_git``).

    Returns:
        Repository-relative paths with forward slashes: tracked files plus untracked files that are not ignored,
        sorted, without duplicates.

    Raises:
        RuntimeError: git fails.
    """
    raw = git(["ls-files", "-z", "--cached", "--others", "--exclude-standard"]).decode("utf-8", "replace")
    return sorted({path for path in raw.split("\0") if path})


def scan_file(path: str, data: bytes, max_bytes: int) -> list:
    """Judge one file of the working tree.

    Args:
        path: Repository-relative path with forward slashes (used for the path rules and the messages).
        data: Complete file content.
        max_bytes: Size limit; a file strictly larger is reported.

    Returns:
        Findings of kinds ``path``, ``size``, ``binary`` and ``secret`` (secrets are shortened to 8 characters).

    Raises:
        None.
    """
    findings = []
    if shared.is_forbidden_path(path):
        findings.append(Finding("path", f"forbidden path: {path}"))
    if len(data) > max_bytes:
        findings.append(Finding("size", f"{path}: {len(data)} bytes (> {max_bytes})"))
    magic = shared.binary_kind(data[:4])
    if magic:
        findings.append(Finding("binary", f"{path}: looks like a {magic} executable"))
    for secret in shared.find_secrets(data.decode("utf-8", "replace")):
        findings.append(Finding("secret", f"{path}: {secret[:8]}..."))
    return findings


def scan_working_tree(root: Path, paths: Sequence[str], max_bytes: int) -> list:
    """Scan the listed files as they are on disk.

    Args:
        root: Repository directory.
        paths: Repository-relative paths (from ``working_tree_paths``); files that no longer exist are skipped.
        max_bytes: Size limit passed to ``scan_file``.

    Returns:
        All findings of ``scan_file`` over the existing regular files.

    Raises:
        OSError: A file exists but cannot be read.
    """
    findings = []
    for path in paths:
        target = root / path
        if target.is_file():
            findings += scan_file(path, target.read_bytes(), max_bytes)
    return findings


def check_gitignore_file(root: Path) -> list:
    """Check the working-tree .gitignore for the mandatory entries.

    Args:
        root: Repository directory.

    Returns:
        One ``gitignore`` finding per missing entry, or a single one when the file does not exist.

    Raises:
        OSError: The file exists but cannot be read.
    """
    target = root / ".gitignore"
    if not target.is_file():
        return [Finding("gitignore", ".gitignore is missing")]
    content = target.read_text(encoding="utf-8", errors="replace")
    return [Finding("gitignore", f"missing entry {pattern}") for pattern in shared.missing_ignores(content)]


def run_checks(root: Path, git: Git, history: bool, max_bytes: int) -> list:
    """Run the working-tree checks and, when requested, the history checks.

    Args:
        root: Repository directory.
        git: Git runner bound to the same repository.
        history: Also scan every commit and blob ever committed.
        max_bytes: Size limit for files and blobs.

    Returns:
        All findings (empty when the repository is clean).

    Raises:
        RuntimeError: git fails.
        OSError: A working-tree file cannot be read.
        ValueError: git printed output that cannot be parsed.
    """
    findings = scan_working_tree(root, working_tree_paths(git), max_bytes) + check_gitignore_file(root)
    if history:
        findings += shared.check_history_secrets(git) + shared.check_history_objects(git, max_bytes)
    return findings


def main(argv: Optional[Sequence[str]] = None, git: Optional[Git] = None, out: Optional[TextIO] = None) -> int:
    """Command-line entry point.

    Args:
        argv: Arguments without the program name, or None for ``sys.argv[1:]``.
        git: Git runner, or None to run real git in ``--repo``.
        out: Text stream for the report, or None for stdout.

    Returns:
        0 for a clean repository, 1 when findings exist, 2 when git or a file read fails.

    Raises:
        SystemExit: Malformed command line.
    """
    parser = argparse.ArgumentParser(prog="check_repo_hygiene.py", description="Secrets, binaries and size gate.")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--history", action="store_true", help="also scan the whole git history")
    parser.add_argument("--max-bytes", type=int, default=shared.MAX_BLOB_BYTES, help="size limit (default 1 MiB)")
    args = parser.parse_args(argv)
    stream = out if out is not None else sys.stdout
    try:
        findings = run_checks(args.repo, git or shared.run_git(args.repo), args.history, args.max_bytes)
    except (RuntimeError, OSError, ValueError) as error:
        stream.write(f"error: {error}\n")
        return 2
    for finding in findings:
        stream.write(f"{finding.kind}: {finding.detail}\n")
    scope = "working tree + history" if args.history else "working tree"
    stream.write(f"{len(findings)} problem(s) ({scope})\n")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
