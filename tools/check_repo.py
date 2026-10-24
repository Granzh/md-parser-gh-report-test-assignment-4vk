"""Repository hygiene check: secrets, binaries, build directories, .gitignore and commit subjects.

Usage: ``python tools/check_repo.py [--repo DIR]``

Checks (all read-only):
    * the working tree is clean (``git status --porcelain`` prints nothing);
    * no secret-looking string in any tracked file or in the whole history (``git log --all -p``): ``ghp_...``,
      ``gh[ousr]_...``, ``github_pat_...``, ``Bearer <20+ token characters>``; the test sentinel
      ``test-token-DO-NOT-USE`` is allowed;
    * no object anywhere in history is a forbidden path (``.env``, ``*.token``, binaries, build or cache
      directories), larger than 1 MiB, or starts with an ELF / Mach-O / PE magic number;
    * ``.gitignore`` lists build/, .ghreport-cache/, __pycache__/, .pytest_cache/, *.token and .env;
    * commit subjects follow Conventional Commits (reported as notes only: history is never rewritten).

Exit code: 0 when there are no findings, 1 otherwise.  Git is reached through an injectable ``git`` callable so that
the tests use throw-away repositories or canned output.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Callable, NamedTuple, Optional, Sequence, TextIO

Git = Callable[..., bytes]

SECRET_PATTERNS = (
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"gh[ousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"Bearer [A-Za-z0-9_-]{20,}"),
)
#: Test-only values that look like tokens on purpose.
ALLOWED_SENTINELS = ("test-token-DO-NOT-USE",)
MAX_BLOB_BYTES = 1024 * 1024
REQUIRED_IGNORES = ("build/", ".ghreport-cache/", "__pycache__/", ".pytest_cache/", "*.token", ".env")
FORBIDDEN_SUFFIXES = (".exe", ".o", ".a", ".so", ".dll", ".dylib", ".obj", ".out", ".token", ".pyc")
FORBIDDEN_DIRS = ("build", "cmake-build-debug", "cmake-build-release", "__pycache__", ".pytest_cache",
                  ".ghreport-cache", "ghreport-cache")
CONVENTIONAL = re.compile(r"^(feat|fix|test|docs|build|chore|refactor|perf|style|ci|revert)(\([^)]+\))?!?: \S")
MAGIC = ((b"\x7fELF", "ELF"), (b"\xfe\xed\xfa\xce", "Mach-O"), (b"\xfe\xed\xfa\xcf", "Mach-O"),
         (b"\xce\xfa\xed\xfe", "Mach-O"), (b"\xcf\xfa\xed\xfe", "Mach-O"), (b"\xca\xfe\xba\xbe", "Mach-O/Java"),
         (b"MZ", "PE"))


class Finding(NamedTuple):
    """One problem found.

    Attributes:
        kind: Short category, e.g. ``secret``, ``object``, ``gitignore``, ``status`` or ``note``.
        detail: Human-readable description.
    """

    kind: str
    detail: str


def find_secrets(text: str) -> list:
    """Find secret-looking strings.

    Args:
        text: Any text (a file or a diff).

    Returns:
        The matched strings in order of appearance; matches that contain an allowed sentinel are skipped.

    Raises:
        None.
    """
    found = []
    for pattern in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            if not any(sentinel in match.group(0) for sentinel in ALLOWED_SENTINELS):
                found.append(match.group(0))
    return found


def is_forbidden_path(path: str) -> bool:
    """Decide whether a tracked path must never be committed.

    Args:
        path: Repository-relative path with forward slashes.

    Returns:
        True for ``.env`` (but not ``.env.example``), build outputs, binaries, secrets files and cache or build
        directories (``build``, ``build-*``, ``cmake-build-*``, ``__pycache__``, ...) at any depth.

    Raises:
        None.
    """
    pure = PurePosixPath(path)
    name = pure.name
    if name == ".env" or name.endswith(FORBIDDEN_SUFFIXES):
        return True
    return any(part in FORBIDDEN_DIRS or part.startswith(("build-", "cmake-build-")) for part in pure.parts[:-1])


def binary_kind(head: bytes) -> Optional[str]:
    """Recognise executable file formats by their magic number.

    Args:
        head: The first bytes of a file (at least 4 for a reliable answer); may be empty.

    Returns:
        ``"ELF"``, ``"Mach-O"``, ``"Mach-O/Java"`` or ``"PE"``, or None for anything else.

    Raises:
        None.
    """
    for magic, kind in MAGIC:
        if head.startswith(magic):
            return kind
    return None


def missing_ignores(gitignore: str) -> list:
    """List required patterns absent from a .gitignore.

    Args:
        gitignore: File content.

    Returns:
        The members of REQUIRED_IGNORES that appear in no line (``build/`` is also satisfied by ``/build/``).

    Raises:
        None.
    """
    lines = {line.strip().lstrip("/") for line in gitignore.splitlines() if not line.lstrip().startswith("#")}
    return [pattern for pattern in REQUIRED_IGNORES if pattern not in lines]


def nonconventional(subjects: Sequence[str]) -> list:
    """Select commit subjects that do not follow Conventional Commits.

    Args:
        subjects: First lines of commit messages.

    Returns:
        The subjects not matching ``type(scope)?: text`` with a known type.

    Raises:
        None.
    """
    return [subject for subject in subjects if not CONVENTIONAL.match(subject)]


def run_git(root: Path) -> Git:
    """Make a git runner bound to a repository.

    Args:
        root: Repository directory.

    Returns:
        A callable ``(args, data=b"") -> stdout bytes`` (``data`` goes to stdin) that raises RuntimeError on a non-zero
        exit.

    Raises:
        None.
    """

    def git(args: Sequence[str], data: bytes = b"") -> bytes:
        """Run ``git <args>`` in the repository.

        Args:
            args: Git arguments without the program name.
            data: Bytes written to git's stdin.

        Returns:
            Captured stdout bytes.

        Raises:
            RuntimeError: git exits non-zero (the message holds stderr).
        """
        done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, input=data, timeout=600)
        if done.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)}: {done.stderr.decode('utf-8', 'replace').strip()}")
        return done.stdout

    return git


def check_status(git: Git) -> list:
    """Check that the working tree has no uncommitted or untracked files.

    Args:
        git: Git runner.

    Returns:
        One Finding per line of ``git status --porcelain``.

    Raises:
        RuntimeError: git fails.
    """
    lines = git(["status", "--porcelain"]).decode("utf-8", "replace").splitlines()
    return [Finding("status", line) for line in lines]


def check_tree_secrets(git: Git) -> list:
    """Scan every tracked file in HEAD for secrets.

    Args:
        git: Git runner.

    Returns:
        One Finding per file containing a secret-looking string (the secret itself is shortened).

    Raises:
        RuntimeError: git fails.
    """
    findings = []
    for path in git(["ls-tree", "-r", "--name-only", "-z", "HEAD"]).decode("utf-8", "replace").split("\0"):
        if not path:
            continue
        content = git(["show", f"HEAD:{path}"]).decode("utf-8", "replace")
        for secret in find_secrets(content):
            findings.append(Finding("secret", f"{path}: {secret[:8]}..."))
    return findings


def check_history_secrets(git: Git) -> list:
    """Scan the patches of every commit on every ref for secrets.

    Args:
        git: Git runner.

    Returns:
        One Finding per distinct secret-looking string found (shortened).

    Raises:
        RuntimeError: git fails.
    """
    text = git(["log", "--all", "-p", "--no-color"]).decode("utf-8", "replace")
    return [Finding("secret", f"history: {secret[:8]}...") for secret in dict.fromkeys(find_secrets(text))]


def parse_batch(data: bytes) -> list:
    """Split the output of ``git cat-file --batch``.

    Args:
        data: Output made of records ``<sha> <type> <size>``, a newline, the content and a newline.

    Returns:
        List of ``(sha, type, content)``; ``missing`` records (``<name> missing``) are skipped.

    Raises:
        ValueError: The data is truncated or a header is malformed.
    """
    records, position = [], 0
    while position < len(data):
        end = data.index(b"\n", position)
        header = data[position:end].split()
        position = end + 1
        if len(header) == 2 and header[1] == b"missing":
            continue
        if len(header) != 3:
            raise ValueError("malformed cat-file header")
        size = int(header[2])
        if position + size > len(data):
            raise ValueError("truncated cat-file output")
        records.append((header[0].decode(), header[1].decode(), data[position:position + size]))
        position += size + 1
    return records


def check_history_objects(git: Git, max_bytes: int = MAX_BLOB_BYTES) -> list:
    """Inspect every blob ever committed.

    Args:
        git: Git runner.
        max_bytes: Size limit in bytes; a blob strictly larger than this is reported.

    Returns:
        Findings for blobs whose path is forbidden, whose size exceeds ``max_bytes`` or that start with an executable magic
        number.

    Raises:
        RuntimeError: git fails.
        ValueError: git printed output that cannot be parsed.
    """
    listing = git(["rev-list", "--objects", "--all"]).decode("utf-8", "replace").splitlines()
    paths = {}
    for line in listing:
        sha, _, path = line.partition(" ")
        if path:
            paths[sha] = path
    findings = []
    for sha, kind, data in parse_batch(git(["cat-file", "--batch"], ("\n".join(paths) + "\n").encode())):
        if kind != "blob":
            continue
        path = paths[sha]
        if is_forbidden_path(path):
            findings.append(Finding("object", f"forbidden path in history: {path}"))
        if len(data) > max_bytes:
            findings.append(Finding("object", f"{path}: {len(data)} bytes (> {max_bytes})"))
        magic = binary_kind(data[:4])
        if magic:
            findings.append(Finding("object", f"{path}: looks like a {magic} executable"))
    return findings


def check_gitignore(git: Git) -> list:
    """Check the .gitignore of HEAD for the required patterns.

    Args:
        git: Git runner.

    Returns:
        One Finding per missing pattern; one Finding if there is no .gitignore.

    Raises:
        None.
    """
    try:
        content = git(["show", "HEAD:.gitignore"]).decode("utf-8", "replace")
    except RuntimeError:
        return [Finding("gitignore", ".gitignore is missing")]
    return [Finding("gitignore", f"missing pattern {pattern}") for pattern in missing_ignores(content)]


def check_subjects(git: Git) -> list:
    """Report commit subjects that are not Conventional Commits.

    Args:
        git: Git runner.

    Returns:
        One Finding of kind ``note`` per offending subject (history is not rewritten, so these never fail the run).

    Raises:
        RuntimeError: git fails.
    """
    subjects = git(["log", "--all", "--format=%h %s"]).decode("utf-8", "replace").splitlines()
    bad = nonconventional([line.partition(" ")[2] for line in subjects])
    return [Finding("note", f"not Conventional Commits: {subject}") for subject in bad]


def run_checks(git: Git) -> list:
    """Run every check.

    Args:
        git: Git runner for the repository under test.

    Returns:
        All findings; those of kind ``note`` are informational.

    Raises:
        RuntimeError: git fails.
    """
    findings = []
    for check in (check_status, check_tree_secrets, check_history_secrets, check_history_objects, check_gitignore,
                  check_subjects):
        findings += check(git)
    return findings


def main(argv: Optional[Sequence[str]] = None, git: Optional[Git] = None, out: Optional[TextIO] = None) -> int:
    """Command-line entry point.

    Args:
        argv: Arguments without the program name, or None for ``sys.argv[1:]``.
        git: Git runner, or None to run real git in ``--repo``.
        out: Text stream for the report, or None for stdout.

    Returns:
        0 when only notes (or nothing) were found, 1 when real findings exist, 2 when git fails.

    Raises:
        SystemExit: Malformed command line.
    """
    parser = argparse.ArgumentParser(prog="check_repo.py", description="Repository hygiene check.")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    stream = out if out is not None else sys.stdout
    try:
        findings = run_checks(git or run_git(args.repo))
    except RuntimeError as error:
        stream.write(f"error: {error}\n")
        return 2
    for finding in findings:
        stream.write(f"{finding.kind}: {finding.detail}\n")
    problems = [finding for finding in findings if finding.kind != "note"]
    stream.write(f"{len(problems)} problem(s), {len(findings) - len(problems)} note(s)\n")
    return 1 if problems else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
