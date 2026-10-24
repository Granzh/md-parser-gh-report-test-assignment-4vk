import io
import subprocess

import pytest

import check_repo_hygiene as ch
from test_check_repo import FAKE_BEARER, FAKE_PAT, GOOD_IGNORE, commit_env, fresh, make_repo


def kinds(findings):
    """Reduce findings to their kinds.

    Args:
        findings: List of check_repo.Finding.

    Returns:
        List of the ``kind`` strings in order.

    Raises:
        None.
    """
    return [finding.kind for finding in findings]


def git_for(root):
    """Make a real git runner for a test repository.

    Args:
        root: Repository path.

    Returns:
        A callable accepted as the ``git`` argument of the module under test.

    Raises:
        None.
    """
    return ch.shared.run_git(root)


# covers: working_tree_paths
def test_working_tree_paths_lists_tracked_and_untracked_but_not_ignored(tmp_path):
    root = make_repo(tmp_path, {".gitignore": GOOD_IGNORE, "a.txt": b"1", "d/b.txt": b"2"})
    (root / "new.txt").write_text("3")
    (root / "build").mkdir()
    (root / "build" / "x.o").write_bytes(b"x")
    assert ch.working_tree_paths(git_for(root)) == [".gitignore", "a.txt", "d/b.txt", "new.txt"]


# covers: scan_file
def test_scan_file_clean_file_has_no_findings():
    assert ch.scan_file("src/a.py", b"print('hi')\n", 1024) == []
    assert ch.scan_file("empty.txt", b"", 0) == []


# covers: scan_file
def test_scan_file_reports_every_kind():
    assert kinds(ch.scan_file("build/x.txt", b"hello", 100)) == ["path"]
    assert kinds(ch.scan_file("big.dat", b"z" * 101, 100)) == ["size"]
    assert kinds(ch.scan_file("tool", b"\x7fELF\x02\x01", 100)) == ["binary"]
    secret = ch.scan_file("notes.md", f"token {FAKE_PAT}".encode(), 1000)
    assert kinds(secret) == ["secret"] and FAKE_PAT not in secret[0].detail and "ghp_A1A1" in secret[0].detail


# covers: scan_file
def test_scan_file_size_limit_is_inclusive_and_sentinel_is_allowed():
    assert ch.scan_file("a.txt", b"x" * 100, 100) == []
    assert ch.scan_file("a.txt", b"Bearer test-token-DO-NOT-USE", 1000) == []


# covers: scan_working_tree
def test_scan_working_tree_reads_disk_and_skips_missing_files(tmp_path):
    (tmp_path / "ok.txt").write_text("fine")
    (tmp_path / "leak.txt").write_text(FAKE_BEARER)
    (tmp_path / "sub").mkdir()
    found = ch.scan_working_tree(tmp_path, ["ok.txt", "leak.txt", "gone.txt", "sub"], 1000)
    assert kinds(found) == ["secret"] and "leak.txt" in found[0].detail
    assert ch.scan_working_tree(tmp_path, [], 1000) == []


# covers: check_gitignore_file
def test_check_gitignore_file(tmp_path):
    good = fresh(tmp_path, "good")
    (good / ".gitignore").write_bytes(GOOD_IGNORE)
    assert ch.check_gitignore_file(good) == []
    partial = fresh(tmp_path, "partial")
    (partial / ".gitignore").write_text("build/\n")
    assert [f.detail for f in ch.check_gitignore_file(partial)] == [
        "missing entry .ghreport-cache/", "missing entry __pycache__/", "missing entry .pytest_cache/",
        "missing entry *.token", "missing entry .env"]
    absent = fresh(tmp_path, "absent")
    assert [f.detail for f in ch.check_gitignore_file(absent)] == [".gitignore is missing"]


# covers: run_checks
def test_run_checks_clean_repository(tmp_path):
    root = make_repo(tmp_path, {".gitignore": GOOD_IGNORE, "src/a.py": b"x = 1\n"})
    assert ch.run_checks(root, git_for(root), True, 1024) == []


# covers: run_checks
def test_run_checks_history_finds_a_secret_removed_from_the_tree(tmp_path):
    root = make_repo(tmp_path, {".gitignore": GOOD_IGNORE, "leak.txt": FAKE_PAT.encode()})
    env = commit_env()
    subprocess.run(["git", "-C", str(root), "rm", "-q", "leak.txt"], check=True, env=env)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "fix: remove"], check=True, env=env)
    assert ch.run_checks(root, git_for(root), False, 1024) == []
    assert kinds(ch.run_checks(root, git_for(root), True, 1024)) == ["secret"]


# covers: run_checks
def test_run_checks_history_honours_the_size_limit(tmp_path):
    root = make_repo(tmp_path, {".gitignore": GOOD_IGNORE, "data.bin": b"d" * 200})
    assert ch.run_checks(root, git_for(root), True, 200) == []
    assert kinds(ch.run_checks(root, git_for(root), True, 199)) == ["size", "object"]


# covers: main
def test_main_clean_and_dirty(tmp_path):
    root = make_repo(fresh(tmp_path, "clean"), {".gitignore": GOOD_IGNORE, "a.txt": b"ok"})
    out = io.StringIO()
    assert ch.main(["--repo", str(root), "--history"], out=out) == 0
    assert out.getvalue() == "0 problem(s) (working tree + history)\n"

    dirty = make_repo(fresh(tmp_path, "dirty"), {".gitignore": GOOD_IGNORE})
    (dirty / "token.txt").write_text(FAKE_PAT)
    (dirty / "prog").write_bytes(b"MZ\x90\x00")
    out = io.StringIO()
    assert ch.main(["--repo", str(dirty)], out=out) == 1
    text = out.getvalue()
    assert "secret: token.txt" in text and "binary: prog" in text and text.endswith("2 problem(s) (working tree)\n")


# covers: main
def test_main_max_bytes_option_and_git_failure(tmp_path):
    root = make_repo(fresh(tmp_path, "sized"), {".gitignore": GOOD_IGNORE, "a.txt": b"1" * 100})
    assert ch.main(["--repo", str(root), "--max-bytes", "100"], out=io.StringIO()) == 0
    out = io.StringIO()
    assert ch.main(["--repo", str(root), "--max-bytes", "99"], out=out) == 1 and "size: a.txt" in out.getvalue()

    not_a_repo = fresh(tmp_path, "plain")
    out = io.StringIO()
    assert ch.main(["--repo", str(not_a_repo)], out=out) == 2
    assert out.getvalue().startswith("error:")


# covers: main
def test_main_rejects_malformed_arguments():
    with pytest.raises(SystemExit):
        ch.main(["--max-bytes", "many"], out=io.StringIO())
