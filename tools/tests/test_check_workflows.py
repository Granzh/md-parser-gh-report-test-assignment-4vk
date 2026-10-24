import io

import pytest

import check_workflows as cw

SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"

GOOD = f"""\
name: CI   # a comment
on:
  push:
    branches: [main]
  pull_request:
permissions:
  contents: read
jobs:
  build:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@{SHA} # v7.0.1
      - name: Show
        env:
          REF: ${{{{ github.head_ref }}}}
        run: |
          echo "$REF"
          echo "plain"
  second:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - run: echo hi
"""


def levels(findings):
    """Reduce findings to (line, level) pairs for compact assertions.

    Args:
        findings: List of Finding.

    Returns:
        List of (line, level) tuples in the original order.

    Raises:
        None.
    """
    return [(f.line, f.level) for f in findings]


def messages(findings):
    """Join the messages of all findings.

    Args:
        findings: List of Finding.

    Returns:
        One string with every message, for substring assertions.

    Raises:
        None.
    """
    return "\n".join(f.message for f in findings)


# covers: check_workflow
def test_check_workflow_accepts_a_good_file():
    assert cw.check_workflow("ci.yml", GOOD) == []


# covers: format_finding
def test_format_finding():
    assert cw.format_finding(cw.Finding("a.yml", 7, cw.ERROR, "boom")) == "a.yml:7: error: boom"


# covers: strip_comment
@pytest.mark.parametrize("line,expected", [
    ("key: value # note", "key: value"),
    ("# whole line", ""),
    ("url: http://x/#anchor", "url: http://x/#anchor"),
    ("name: 'a # b' # c", "name: 'a # b'"),
    ('name: "a # b"', 'name: "a # b"'),
    ("plain   ", "plain"),
    ("", ""),
])
def test_strip_comment(line, expected):
    assert cw.strip_comment(line) == expected


# covers: indent_of
def test_indent_of():
    assert cw.indent_of("    x") == 4
    assert cw.indent_of("x") == 0
    assert cw.indent_of("") == 0
    assert cw.indent_of("\tx") == 0


# covers: parse_key
def test_parse_key_variants():
    assert cw.parse_key("name: CI") == (0, "name", "CI")
    assert cw.parse_key("  - run: echo hi") == (4, "run", "echo hi")
    assert cw.parse_key("  jobs:") == (2, "jobs", "")
    assert cw.parse_key("  timeout-minutes: 5") == (2, "timeout-minutes", "5")
    assert cw.parse_key("  echo hello") is None
    assert cw.parse_key("") is None
    assert cw.parse_key("- plain item") is None


# covers: code_lines
def test_code_lines_keeps_numbers_and_blanks_comments():
    assert cw.code_lines("a: 1\n# c\n\nb: 2 # x\n") == [(1, "a: 1"), (2, ""), (3, ""), (4, "b: 2")]
    assert cw.code_lines("") == []


# covers: top_level_keys
def test_top_level_keys_ignores_nested_and_list_keys():
    lines = cw.code_lines("name: x\non:\n  push:\npermissions: {}\nlist:\n- permissions: no\n")
    assert cw.top_level_keys(lines) == {"name": 1, "on": 2, "permissions": 4, "list": 5}
    assert cw.top_level_keys([]) == {}


# covers: split_jobs
def test_split_jobs_names_and_bodies():
    jobs = cw.split_jobs(cw.code_lines(GOOD))
    assert list(jobs) == ["build", "second"]
    first, body = jobs["build"]
    assert first == 9 and body[0] == (9, "  build:") and body[-1][0] < jobs["second"][0]
    assert cw.split_jobs(cw.code_lines("name: x\n")) == {}


# covers: split_jobs
def test_split_jobs_stops_at_next_top_level_key_and_accepts_four_space_indent():
    text = "jobs:\n    a:\n        timeout-minutes: 1\nother:\n    b:\n        x: 1\n"
    jobs = cw.split_jobs(cw.code_lines(text))
    assert list(jobs) == ["a"]


# covers: check_permissions
def test_check_permissions():
    assert cw.check_permissions("w.yml", cw.code_lines("on: push\npermissions:\n  contents: read\n")) == []
    missing = cw.check_permissions("w.yml", cw.code_lines("on: push\njobs:\n  a:\n    permissions: read-all\n"))
    assert levels(missing) == [(1, cw.ERROR)] and "permissions" in messages(missing)


# covers: check_timeouts
def test_check_timeouts_flags_the_job_without_one():
    text = GOOD.replace("    timeout-minutes: 5\n", "")
    found = cw.check_timeouts("w.yml", cw.code_lines(text))
    assert levels(found) == [(20, cw.ERROR)] and "`second`" in messages(found)


# covers: check_timeouts
def test_check_timeouts_ignores_step_level_key_and_reusable_calls():
    nested = "jobs:\n  a:\n    steps:\n      - run: x\n        timeout-minutes: 3\n"
    assert len(cw.check_timeouts("w.yml", cw.code_lines(nested))) == 1
    reusable = "jobs:\n  a:\n    uses: ./.github/workflows/other.yml\n"
    assert cw.check_timeouts("w.yml", cw.code_lines(reusable)) == []
    assert cw.check_timeouts("w.yml", cw.code_lines("on: push\n")) == []


# covers: check_triggers
def test_check_triggers():
    assert cw.check_triggers("w.yml", cw.code_lines(GOOD)) == []
    bad = cw.check_triggers("w.yml", cw.code_lines("on:\n  pull_request_target:\n    types: [opened]\n"))
    assert levels(bad) == [(2, cw.ERROR)]
    assert cw.check_triggers("w.yml", cw.code_lines("# pull_request_target is banned\non: push\n")) == []


# covers: run_block_lines
def test_run_block_lines_inline_block_and_folded():
    text = ("steps:\n  - run: echo one\n  - name: x\n    run: |\n      line a\n\n      line b\n    env:\n      A: b\n"
            "  - run: >-\n      folded\n  - run:\n      echo deep\n")
    got = cw.run_block_lines(cw.code_lines(text))
    assert [t for _, t in got] == ["echo one", "line a", "line b", "folded", "echo deep"]
    assert got[0][0] == 2


# covers: run_block_lines
def test_run_block_lines_ignores_env_values_that_look_like_expressions():
    text = "steps:\n  - env:\n      X: ${{ github.event.pull_request.title }}\n    run: echo \"$X\"\n"
    assert [t for _, t in cw.run_block_lines(cw.code_lines(text))] == ['echo "$X"']


# covers: check_run_injection
@pytest.mark.parametrize("expression", [
    "github.event.pull_request.title", "github.head_ref", "github.ref_name", "github.event.issue.body"])
def test_check_run_injection_flags_untrusted_expressions(expression):
    text = f"steps:\n  - run: |\n      echo ok\n      echo ${{{{ {expression} }}}}\n"
    found = cw.check_run_injection("w.yml", cw.code_lines(text))
    assert levels(found) == [(4, cw.ERROR)]


# covers: check_run_injection
def test_check_run_injection_allows_env_and_safe_contexts():
    assert cw.check_run_injection("w.yml", cw.code_lines(GOOD)) == []
    safe = "steps:\n  - run: echo ${{ matrix.mode }} ${{ runner.temp }} ${{ github.sha }}\n"
    assert cw.check_run_injection("w.yml", cw.code_lines(safe)) == []
    inline = "steps:\n  - run: echo ${{ github.event.pull_request.title }}\n"
    assert levels(cw.check_run_injection("w.yml", cw.code_lines(inline))) == [(2, cw.ERROR)]


# covers: classify_uses
def test_classify_uses_pinned_and_local_are_fine():
    assert cw.classify_uses(f"actions/checkout@{SHA}") is None
    assert cw.classify_uses(f"'actions/checkout@{SHA}'") is None
    assert cw.classify_uses("./.github/actions/local") is None
    assert cw.classify_uses("docker://alpine:3") is None


# covers: classify_uses
@pytest.mark.parametrize("value,level", [
    ("actions/checkout@v4", cw.WARNING), ("actions/checkout@v4.1.7", cw.WARNING),
    ("actions/checkout@main", cw.ERROR), ("actions/checkout@master", cw.ERROR),
    ("actions/checkout", cw.ERROR), ("actions/checkout@", cw.ERROR), ("actions/checkout@3d3c42e", cw.ERROR),
    ("actions/checkout@V4", cw.ERROR), ("actions/checkout@release/v1", cw.ERROR)])
def test_classify_uses_unpinned(value, level):
    verdict = cw.classify_uses(value)
    assert verdict is not None and verdict[0] == level


# covers: check_uses
def test_check_uses_reports_line_and_level():
    text = (f"steps:\n  - uses: actions/checkout@{SHA}\n  - uses: actions/setup-go@v5 # v5\n"
            "  - uses: actions/cache@main\n  - run: echo uses actions/x@main\n")
    assert levels(cw.check_uses("w.yml", cw.code_lines(text))) == [(3, cw.WARNING), (4, cw.ERROR)]
    assert cw.check_uses("w.yml", cw.code_lines(GOOD)) == []


# covers: check_secrets
def test_check_secrets_only_allowed_in_release():
    text = "steps:\n  - run: echo\n    env:\n      T: ${{ secrets.GITHUB_TOKEN }}\n"
    lines = cw.code_lines(text)
    assert levels(cw.check_secrets(".github/workflows/ci.yml", lines)) == [(4, cw.ERROR)]
    assert cw.check_secrets(".github/workflows/release.yml", lines) == []
    assert cw.check_secrets("ci.yml", cw.code_lines("# secrets.X is mentioned in a comment\n")) == []


# covers: check_workflow
@pytest.mark.parametrize("mutation,expected", [
    (lambda t: t.replace("permissions:\n  contents: read\n", ""), "permissions"),
    (lambda t: t.replace("    timeout-minutes: 10\n", ""), "timeout-minutes"),
    (lambda t: t.replace("pull_request:", "pull_request_target:"), "pull_request_target"),
    (lambda t: t.replace('echo "plain"', "echo ${{ github.event.pull_request.title }}"), "untrusted"),
    (lambda t: t.replace(SHA, "main"), "mutable reference"),
    (lambda t: t.replace("echo hi", "echo ${{ secrets.TOKEN }}"), "secrets."),
])
def test_check_workflow_reports_each_violation(mutation, expected):
    found = cw.check_workflow("ci.yml", mutation(GOOD))
    assert found and expected in messages(found)
    assert [f.line for f in found] == sorted(f.line for f in found)


# covers: workflow_files
def test_workflow_files(tmp_path):
    (tmp_path / "a.yml").write_text("x", encoding="utf-8")
    (tmp_path / "b.yaml").write_text("x", encoding="utf-8")
    (tmp_path / "c.txt").write_text("x", encoding="utf-8")
    single = tmp_path / "c.txt"
    assert [p.name for p in cw.workflow_files([str(tmp_path)])] == ["a.yml", "b.yaml"]
    assert [p.name for p in cw.workflow_files([str(single)])] == ["c.txt"]
    with pytest.raises(FileNotFoundError):
        cw.workflow_files([str(tmp_path / "missing")])


# covers: main
def test_main_exit_codes_and_output(tmp_path):
    good = tmp_path / "good"
    good.mkdir()
    (good / "ci.yml").write_text(GOOD, encoding="utf-8")
    out = io.StringIO()
    assert cw.main([str(good)], out) == 0
    assert out.getvalue().endswith("1 file(s), 0 error(s), 0 warning(s)\n")

    warned = tmp_path / "warn"
    warned.mkdir()
    (warned / "ci.yml").write_text(GOOD.replace(SHA, "v7"), encoding="utf-8")
    out = io.StringIO()
    assert cw.main([str(warned)], out) == 0
    assert "ci.yml:13: warning:" in out.getvalue() and "1 warning(s)" in out.getvalue()

    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "ci.yml").write_text(GOOD.replace(SHA, "main"), encoding="utf-8")
    out = io.StringIO()
    assert cw.main([str(bad)], out) == 1
    assert "ci.yml:13: error:" in out.getvalue()


# covers: main
def test_main_missing_path_and_empty_directory(tmp_path):
    out = io.StringIO()
    assert cw.main([str(tmp_path / "nope")], out) == 2
    assert out.getvalue().startswith("error:")
    empty = tmp_path / "empty"
    empty.mkdir()
    out = io.StringIO()
    assert cw.main([str(empty)], out) == 2
    assert "no workflow files" in out.getvalue()


# covers: main
def test_main_reports_undecodable_file(tmp_path):
    (tmp_path / "x.yml").write_bytes(b"\xff\xfe\x00bad")
    out = io.StringIO()
    assert cw.main([str(tmp_path)], out) == 2
