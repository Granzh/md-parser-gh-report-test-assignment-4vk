import io
import os
import subprocess

import pytest

import check_repo as cr

# Token-like values are assembled at run time so that this file itself never matches the secret patterns.
FAKE_PAT = "gh" + "p_" + "A1" * 15
FAKE_FINE_GRAINED = "github" + "_pat_" + "B2" * 15
FAKE_BEARER = "Bear" + "er " + "C3" * 15

GOOD_IGNORE = b"build/\n.ghreport-cache/\n__pycache__/\n.pytest_cache/\n*.token\n.env\n"


def commit_env():
    """Environment giving git an identity without touching any git config.

    Returns:
        Dict of environment variables for git subprocesses.

    Raises:
        None.
    """
    return dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                GIT_COMMITTER_EMAIL="t@t")


def make_repo(parent, files, subject="feat: initial"):
    """Create a repository with one commit.

    Args:
        parent: Existing directory; the repository is created in its ``repo`` subdirectory.
        files: Mapping of relative path to bytes content.
        subject: Commit subject.

    Returns:
        Path of the repository.

    Raises:
        subprocess.CalledProcessError: A git command fails.
    """
    root = parent / "repo"
    root.mkdir()
    env = commit_env()
    subprocess.run(["git", "init", "-q", str(root)], check=True, env=env)
    for name, data in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    subprocess.run(["git", "-C", str(root), "add", "-A", "-f"], check=True, env=env)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", subject], check=True, env=env)
    return root


def fresh(tmp_path, name):
    """Create an empty sibling directory so several repositories can live in one test.

    Args:
        tmp_path: Test directory.
        name: Name of the new directory.

    Returns:
        Path of the created directory.

    Raises:
        None.
    """
    directory = tmp_path / name
    directory.mkdir()
    return directory


# covers: find_secrets
def test_find_secrets_detects_all_patterns():
    text = f"a {FAKE_PAT} b {FAKE_FINE_GRAINED} c {FAKE_BEARER}"
    assert cr.find_secrets(text) == [FAKE_PAT, FAKE_FINE_GRAINED, FAKE_BEARER]


# covers: find_secrets
def test_find_secrets_ignores_sentinel_short_values_and_plain_text():
    assert cr.find_secrets("Bearer test-token-DO-NOT-USE") == []
    assert cr.find_secrets("ghp_short Bearer abc") == []
    assert cr.find_secrets("") == []


# covers: is_forbidden_path
@pytest.mark.parametrize("path,expected", [
    (".env", True), ("service/.env", True), (".env.example", False), ("a/b.token", True), ("msgfmt", False),
    ("build/x.txt", True), ("build-r/CMakeCache.txt", True), ("x/__pycache__/a.py", True), ("ghreport.exe", True),
    ("src/main.cpp", False), ("docs/build.md", False), ("service/cmd/ghreport/main.go", False),
    (".ghreport-cache/k", True), ("lib.a", True)])
def test_is_forbidden_path(path, expected):
    assert cr.is_forbidden_path(path) is expected


# covers: binary_kind
def test_binary_kind():
    assert cr.binary_kind(b"\x7fELF\x02") == "ELF"
    assert cr.binary_kind(b"\xcf\xfa\xed\xfe") == "Mach-O"
    assert cr.binary_kind(b"MZ\x90\x00") == "PE"
    assert cr.binary_kind(b"#!/b") is None
    assert cr.binary_kind(b"") is None


# covers: missing_ignores
def test_missing_ignores():
    assert cr.missing_ignores(GOOD_IGNORE.decode()) == []
    assert cr.missing_ignores("/build/\n# .env\n") == [".ghreport-cache/", "__pycache__/", ".pytest_cache/", "*.token",
                                                       ".env"]
    assert cr.missing_ignores("") == list(cr.REQUIRED_IGNORES)


# covers: nonconventional
def test_nonconventional():
    subjects = ["feat: a", "fix(api): b", "docs(root)!: c", "add spec", "feat:nospace", "Feat: x", "test(x): ok"]
    assert cr.nonconventional(subjects) == ["add spec", "feat:nospace", "Feat: x"]


# covers: run_git
def test_run_git_returns_stdout_and_raises_on_failure(tmp_path):
    git = cr.run_git(make_repo(tmp_path, {"a.txt": b"x"}))
    assert git(["rev-parse", "--is-inside-work-tree"]).strip() == b"true"
    assert git(["hash-object", "--stdin"], b"hello\n").strip() == b"ce013625030ba8dba906f756967f9e9ca394464a"
    with pytest.raises(RuntimeError):
        git(["no-such-subcommand"])


# covers: git
def test_git_error_message_names_command(tmp_path):
    git = cr.run_git(make_repo(tmp_path, {"a.txt": b"x"}))
    with pytest.raises(RuntimeError, match="cat-file"):
        git(["cat-file", "-t", "deadbeef"])


# covers: check_status
def test_check_status(tmp_path):
    root = make_repo(tmp_path, {"a.txt": b"x"})
    git = cr.run_git(root)
    assert cr.check_status(git) == []
    (root / "b.txt").write_text("y")
    assert [f.detail for f in cr.check_status(git)] == ["?? b.txt"]


# covers: check_tree_secrets
def test_check_tree_secrets(tmp_path):
    root = make_repo(tmp_path, {"ok.txt": b"Bearer test-token-DO-NOT-USE", "leak/cfg.txt": FAKE_PAT.encode()})
    findings = cr.check_tree_secrets(cr.run_git(root))
    assert [f.kind for f in findings] == ["secret"] and "leak/cfg.txt" in findings[0].detail
    assert FAKE_PAT not in findings[0].detail


# covers: check_history_secrets
def test_check_history_secrets_sees_deleted_content(tmp_path):
    root = make_repo(tmp_path, {"cfg.txt": FAKE_PAT.encode()})
    (root / "cfg.txt").write_text("clean")
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-am", "fix: remove"], check=True, env=commit_env())
    git = cr.run_git(root)
    assert cr.check_tree_secrets(git) == []
    findings = cr.check_history_secrets(git)
    assert len(findings) == 1 and FAKE_PAT not in findings[0].detail


# covers: parse_batch
def test_parse_batch_reads_records_and_skips_missing():
    data = b"aaa blob 3\nabc\nbbb tree 0\n\nccc missing\n"
    assert cr.parse_batch(data) == [("aaa", "blob", b"abc"), ("bbb", "tree", b"")]
    assert cr.parse_batch(b"") == []
    assert cr.parse_batch(b"aaa blob 3\nx\nb\n") == [("aaa", "blob", b"x\nb")]


# covers: parse_batch
def test_parse_batch_rejects_bad_input():
    with pytest.raises(ValueError):
        cr.parse_batch(b"aaa blob 10\nabc\n")
    with pytest.raises(ValueError):
        cr.parse_batch(b"garbage line here now\n")


# covers: check_history_objects
def test_check_history_objects_flags_binaries_paths_and_sizes(tmp_path):
    files = {"bin/tool": b"\x7fELF" + b"\0" * 10, "build/out.txt": b"x", "big.dat": b"z" * (cr.MAX_BLOB_BYTES + 1),
             "fine.txt": b"hello"}
    details = [f.detail for f in cr.check_history_objects(cr.run_git(make_repo(tmp_path, files)))]
    assert any("bin/tool" in d and "ELF" in d for d in details)
    assert any("forbidden path" in d and "build/out.txt" in d for d in details)
    assert any("big.dat" in d and "> 1048576" in d for d in details)
    assert not any("fine.txt" in d for d in details)


# covers: check_history_objects
def test_check_history_objects_clean_repo(tmp_path):
    assert cr.check_history_objects(cr.run_git(make_repo(tmp_path, {"a/b.txt": b"x", "c.py": b"print(1)"}))) == []


# covers: check_gitignore
def test_check_gitignore(tmp_path):
    assert cr.check_gitignore(cr.run_git(make_repo(fresh(tmp_path, "one"), {".gitignore": GOOD_IGNORE}))) == []
    partial = make_repo(fresh(tmp_path, "two"), {".gitignore": b"build/\n"})
    assert len(cr.check_gitignore(cr.run_git(partial))) == 5
    none = make_repo(fresh(tmp_path, "three"), {"a": b"x"})
    assert cr.check_gitignore(cr.run_git(none)) == [cr.Finding("gitignore", ".gitignore is missing")]


# covers: check_subjects
def test_check_subjects(tmp_path):
    bad = make_repo(fresh(tmp_path, "one"), {"a": b"x"}, subject="add stuff")
    assert cr.check_subjects(cr.run_git(bad)) == [cr.Finding("note", "not Conventional Commits: add stuff")]
    assert cr.check_subjects(cr.run_git(make_repo(fresh(tmp_path, "two"), {"a": b"x"}))) == []


# covers: run_checks
def test_run_checks_clean_repo_has_no_findings(tmp_path):
    root = make_repo(tmp_path, {".gitignore": GOOD_IGNORE, "a.py": b"print(1)\n"})
    assert cr.run_checks(cr.run_git(root)) == []


# covers: run_checks
def test_run_checks_collects_findings_of_every_kind(tmp_path):
    root = make_repo(tmp_path, {"leak.txt": FAKE_BEARER.encode()}, subject="oops")
    (root / "dirty").write_text("x")
    kinds = {f.kind for f in cr.run_checks(cr.run_git(root))}
    assert kinds == {"status", "secret", "gitignore", "note"}


# covers: main
def test_main_exit_codes_and_output(tmp_path):
    root = make_repo(tmp_path, {".gitignore": GOOD_IGNORE})
    out = io.StringIO()
    assert cr.main(["--repo", str(root)], out=out) == 0 and "0 problem(s), 0 note(s)" in out.getvalue()
    (root / "x").write_text("y")
    out = io.StringIO()
    assert cr.main(["--repo", str(root)], out=out) == 1 and "status: ?? x" in out.getvalue()


# covers: main
def test_main_notes_do_not_fail_and_git_errors_exit_two(tmp_path):
    root = make_repo(tmp_path, {".gitignore": GOOD_IGNORE}, subject="add things")
    out = io.StringIO()
    assert cr.main(["--repo", str(root)], out=out) == 0 and "1 note(s)" in out.getvalue()

    def broken(args, data=b""):
        """Fail like a missing repository.

        Args:
            args: Ignored.
            data: Ignored.

        Returns:
            Never.

        Raises:
            RuntimeError: Always.
        """
        raise RuntimeError("not a repo")

    out = io.StringIO()
    assert cr.main([], git=broken, out=out) == 2 and "not a repo" in out.getvalue()
