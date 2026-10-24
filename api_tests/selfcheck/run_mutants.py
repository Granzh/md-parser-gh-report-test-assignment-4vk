"""Mutation check of the test suite (spec 5.4: the suite must detect contract violations).

For every mutant the main suite (`pytest api_tests -m "not selfcheck" -x`) must FAIL:

* server mutants - the stub server runs with one violation flag (SERVER VIOLATION or a mismatch with the oracle);
* service mutants - a copy of service/ is patched (exactly one regex match per edit), built and run against the
  compliant stub server;
* compatible deviations (stub flag ``dup_items``) must NOT be killed: the suite has to stay green.

The working repository is never modified: mutated sources and binaries live in a temporary directory.

Usage: python api_tests/selfcheck/run_mutants.py [--only ID] [--service-dir service] [--skip-baseline] [--validate]
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
API_TESTS = HERE.parent
REPO_ROOT = API_TESTS.parent
sys.path.insert(0, str(HERE))

import stub_server  # noqa: E402  (needs HERE on sys.path)

#: Seconds allowed for one suite run (the slowest legitimate run takes about 40 s).
SUITE_TIMEOUT = 600
#: Seconds allowed for one `go build`.
BUILD_TIMEOUT = 300
#: Per-run timeout of the service inside the suite (GHREPORT_TIMEOUT) while mutants run.
SERVICE_TIMEOUT = "30"


class StaleMutant(Exception):
    """A mutation pattern did not match exactly once, so the mutant no longer fits the service code."""


class BuildError(Exception):
    """`go build` failed (an invalid mutant or a broken toolchain)."""


@dataclass(frozen=True)
class Edit:
    """One regex substitution in one source file.

    Attributes:
        file: Path relative to the service directory.
        pattern: Regular expression that must match exactly once.
        replacement: Literal replacement text (no group references).
    """

    file: str
    pattern: str
    replacement: str


@dataclass(frozen=True)
class ServiceMutant:
    """A deliberate bug in the service.

    Attributes:
        id: Short unique name.
        description: What the bug does and which contract rule it breaks.
        edits: Substitutions that implement it.
        equivalent: Non-empty text explaining why the bug cannot change observable behaviour (such a mutant
            is allowed to survive); empty for ordinary mutants.
    """

    id: str
    description: str
    edits: tuple
    equivalent: str = ""


@dataclass
class SuiteResult:
    """Outcome of one suite run.

    Attributes:
        returncode: pytest exit code, or -1 after a timeout.
        output: Combined stdout/stderr.
        timed_out: True when the run exceeded its timeout.
        seconds: Wall-clock duration.
    """

    returncode: int
    output: str
    timed_out: bool = False
    seconds: float = 0.0


@dataclass
class Outcome:
    """Verdict for one mutant.

    Attributes:
        id: Mutant name.
        kind: "server", "service" or "compatible".
        status: "killed", "survived", "equivalent" (survived, documented), "error" (tool problem) or "green"
            (a compatible deviation that correctly stayed green).
        first_failure: First failing test id, or "".
        seconds: Duration of the suite run.
        note: Extra explanation (error text, equivalence reason).
    """

    id: str
    kind: str
    status: str
    first_failure: str = ""
    seconds: float = 0.0
    note: str = ""


def lit(text):
    """Escape a literal for use as a regex pattern.

    Args:
        text: Literal source text.

    Returns:
        Regex that matches exactly that text.

    Raises:
        None.
    """
    return re.escape(text)


SERVER_MUTANTS = {
    "server:drop_item_between_pages": "drop_item_between_pages",
    "server:pr_without_flag": "pr_without_flag",
    "server:always_304": "always_304",
    "server:broken_json": "broken_json",
    "server:reversed_sort": "reversed_sort",
    "server:no_link": "no_link",
}

COMPATIBLE_DEVIATIONS = {"compatible:dup_items": "dup_items"}

_ISSUES = "internal/issues/issues.go"
_HTTPX = "internal/httpx/httpx.go"
_RATE = "internal/httpx/ratelimit.go"
_PR_LINE = lit('if _, isPR := obj["pull_request"]; isPR {')

SERVICE_MUTANTS = (
    ServiceMutant("skip_last_item_of_page", "the last element of every page is dropped",
                  (Edit(_ISSUES, lit("for _, raw := range elems {"),
                        "for idx, raw := range elems {\n\t\t\tif idx == len(elems)-1 && len(elems) > 1 {\n"
                        "\t\t\t\tcontinue\n\t\t\t}"),)),
    ServiceMutant("limit_plus_one", "limit is exceeded by one item (`>` instead of `>=`)",
                  (Edit(_ISSUES, lit("if len(out) >= limit {"), "if len(out) > limit {"),)),
    ServiceMutant("pr_not_filtered", "pull requests are not filtered out",
                  (Edit(_ISSUES, _PR_LINE, 'if _, isPR := obj["pull_request"]; isPR && false {'),)),
    ServiceMutant("pr_null_counts_as_issue", "a pull_request key with value null does not mark a PR",
                  (Edit(_ISSUES, _PR_LINE,
                        'if v, isPR := obj["pull_request"]; isPR && string(v) != "null" {'),)),
    ServiceMutant("since_boundary_excluded", "--since compares with `<=` (the instant of 00:00:00 is lost)",
                  (Edit(_ISSUES, lit("created.Before(*since)"), "!created.After(*since)"),)),
    ServiceMutant("since_inverted", "--since keeps the OLD issues instead of the new ones",
                  (Edit(_ISSUES, lit("created.Before(*since)"), "created.After(*since)"),)),
    ServiceMutant("since_sent_to_api", "the API parameter since= is added to the first request",
                  (Edit(_ISSUES, lit("cur := FirstURL(f.Base, repo)"),
                        'cur := FirstURL(f.Base, repo)\n\tif since != nil {\n'
                        '\t\tcur += "&since=" + since.Format(time.RFC3339)\n\t}'),)),
    ServiceMutant("sort_reversed", "issues are sorted newest first",
                  (Edit(_ISSUES, lit("return ti.Before(tj)"), "return tj.Before(ti)"),)),
    ServiceMutant("sort_by_number_only", "created_at is ignored, issues are sorted by number",
                  (Edit(_ISSUES, lit("if !ti.Equal(tj) {"), "if false {"),)),
    ServiceMutant("sort_without_tiebreak", "equal created_at keeps the server order (no number tie-break)",
                  (Edit(_ISSUES, lit("return list[i].Number < list[j].Number"), "return false"),)),
    ServiceMutant("dedup_disabled", "items with a repeated number are reported again",
                  (Edit(_ISSUES, lit("if skip || seenNumbers[issue.Number] {"), "if skip {"),)),
    ServiceMutant("pages_built_by_hand", "the next page address is constructed (page=N) instead of taken from Link",
                  (Edit(_ISSUES, lit("cur = next\n"),
                        'cur = FirstURL(f.Base, repo) + "&page=" + strconv.Itoa(pages+2)\n\t\t_ = next\n'),)),
    ServiceMutant("link_next_ignored", "the Link header is never consulted: only the first page is read",
                  (Edit(_ISSUES, lit("next, ok := linkhdr.NextURL(link, curURL)"),
                        "next, ok := linkhdr.NextURL(nil, curURL)"),)),
    ServiceMutant("if_none_match_never_sent", "the stored ETag is not sent as If-None-Match",
                  (Edit(_ISSUES, lit("inm = entry.ETag"), 'inm = ""'),)),
    ServiceMutant("from_cache_always_false", "the trace never reports from_cache: true",
                  (Edit(_HTTPX, lit('resp.StatusCode == http.StatusNotModified && ifNoneMatch != ""'), "false"),)),
    ServiceMutant("cache_never_written", "nothing is ever stored in the cache",
                  (Edit(_ISSUES, lit("if f.Cache != nil && cacheable(resp) {"),
                        "if f.Cache != nil && cacheable(resp) && false {"),)),
    ServiceMutant("cache_not_refreshed", "an existing cache entry is never updated by a new 200 answer",
                  (Edit(_ISSUES, lit("if f.Cache != nil && cacheable(resp) {"),
                        "if f.Cache != nil && !have && cacheable(resp) {"),)),
    ServiceMutant("cache_drops_link", "a 304 answer without Link loses the stored Link (later pages vanish)",
                  (Edit(_ISSUES, lit('if len(link) == 0 && entry.Link != "" {'), "if false {"),)),
    ServiceMutant("retry_after_no_pause", "retry-after is parsed but the pause is zero",
                  (Edit(_RATE, lit("return min(d, MaxRateLimitWait)"), "return 0"),)),
    ServiceMutant("rate_limit_403_not_recognised", "403 is never treated as a rate limit",
                  (Edit(_RATE, lit("if status != http.StatusForbidden && status != http.StatusTooManyRequests {"),
                        "if status != http.StatusTooManyRequests {"),)),
    ServiceMutant("plain_403_is_rate_limit", "every 403/429 is a rate limit (a plain 403 is retried)",
                  (Edit(_RATE, lit('return strings.TrimSpace(h.Get("X-Ratelimit-Remaining")) == "0"'),
                        "return true"),)),
    ServiceMutant("unauthorized_is_request_failed", "401 is an ordinary repository error",
                  (Edit(_HTTPX, lit('c.newError(ErrUnauthorized, o.status, "")'),
                        'c.newError(ErrRequestFailed, o.status, "")'),)),
    ServiceMutant("not_found_is_request_failed", "404 is reported as request_failed",
                  (Edit(_HTTPX, lit('c.newError(ErrNotFound, o.status, "")'),
                        'c.newError(ErrRequestFailed, o.status, "")'),)),
    ServiceMutant("all_failed_not_fatal", "when every repository failed the exit code stays 1 and no all_failed",
                  (Edit("internal/exitcode/exitcode.go", lit("return Fatal, model.FatalAllFailed"),
                        'return Partial, ""'),)),
    ServiceMutant("labels_null", "an issue without labels is serialised with labels: null",
                  (Edit(_ISSUES, lit("names := []string{}"), "var names []string"),
                   Edit("internal/model/model.go", lit("issue.Labels = append([]string{}, issue.Labels...)"),
                        "issue.Labels = issue.Labels"))),
    ServiceMutant("author_dropped", "the author field is always empty",
                  (Edit(_ISSUES, lit("return *user.Login, nil"), 'return "", nil'),)),
    ServiceMutant("authorization_in_trace", "the token is appended to every trace url",
                  (Edit("internal/trace/trace.go", lit("r.URL = StripUserinfo(r.URL)"),
                        'r.URL = StripUserinfo(r.URL) + "#authorization=" + os.Getenv("GITHUB_TOKEN")'),)),
    ServiceMutant("token_printed_to_stderr", "the token is written to stderr unscrubbed",
                  (Edit("internal/app/app.go", lit("errw := &scrubWriter{w: stderr, token: token}"),
                        'errw := &scrubWriter{w: stderr, token: token}\n\tif token != "" {\n'
                        '\t\tfmt.Fprintln(stderr, "debug token:", token)\n\t}'),)),
    ServiceMutant("token_sent_cross_origin", "Authorization is sent to any origin (also after a redirect)",
                  (Edit(_HTTPX, lit('if c.Token != "" && c.sameOrigin(req.URL) {'), 'if c.Token != "" {'),)),
    ServiceMutant("trace_attempt_zero", "the trace always records attempt 0",
                  (Edit(_HTTPX, lit("Attempt:   attempt,"), "Attempt:   0,"),)),
    ServiceMutant("out_stdout_also_file", "`--out -` also writes a file named \"-\" in the working directory",
                  (Edit("internal/output/output.go", lit("n, err := stdout.Write(data)"),
                        '_ = os.WriteFile("-", data, 0o644)\n\t\tn, err := stdout.Write(data)'),)),
    ServiceMutant("repos_duplicates_kept", "a repeated repository in --repos is processed twice",
                  (Edit("internal/args/args.go", lit("if seen[item] {"), "if false {"),)),
    ServiceMutant("user_agent_missing", "no User-Agent header is set (Go's default is sent)",
                  (Edit(_HTTPX, lit('req.Header.Set("User-Agent", orDefault(c.UserAgent, DefaultUserAgent))'),
                        "_ = orDefault(c.UserAgent, DefaultUserAgent)"),)),
    ServiceMutant("api_version_wrong", "X-GitHub-Api-Version carries an old version",
                  (Edit(_HTTPX, lit('DefaultAPIVersion = "2026-03-10"'), 'DefaultAPIVersion = "2022-11-28"'),)),
    ServiceMutant("retries_reduced", "only one retry after a network error / 5xx instead of three",
                  (Edit(_HTTPX, lit("if st.failures >= MaxRetries {"), "if st.failures >= 1 {"),)),
    ServiceMutant("redirect_307_not_followed", "307 is not followed",
                  (Edit(_HTTPX, lit(" || status == http.StatusTemporaryRedirect"), ""),)),
    ServiceMutant("invalid_json_accepted", "a body that is not a JSON array is silently treated as empty",
                  (Edit(_ISSUES, lit("err != nil || elems == nil {"), "err != nil && false {"),)),
)

#: Ideas without a simple, behaviour-changing edit (documented in api_tests/README.md).
NOT_MUTATED = ("parallel requests: the fetch loop is sequential by construction; no one-line edit introduces "
               "concurrency without restructuring",)


def apply_mutation(text, pattern, replacement):
    """Apply one regex substitution that must match exactly once.

    Args:
        text: Source text.
        pattern: Regular expression (searched with re.MULTILINE).
        replacement: Literal replacement (backslashes are not interpreted).

    Returns:
        The text with the single match replaced.

    Raises:
        StaleMutant: The pattern matches zero or several times.
    """
    matches = list(re.finditer(pattern, text, re.MULTILINE))
    if len(matches) != 1:
        raise StaleMutant(f"pattern {pattern!r} matched {len(matches)} times, expected exactly 1")
    match = matches[0]
    return text[:match.start()] + replacement + text[match.end():]


def apply_mutant(mutant, root):
    """Patch a copy of the service in place.

    Args:
        mutant: ServiceMutant to apply.
        root: Directory holding the copied service sources.

    Returns:
        None.

    Raises:
        StaleMutant: An edit does not match exactly once or its file is missing (the message names the mutant).
    """
    for edit in mutant.edits:
        path = Path(root) / edit.file
        if not path.is_file():
            raise StaleMutant(f"{mutant.id}: file {edit.file} does not exist")
        text = path.read_text(encoding="utf-8")
        try:
            patched = apply_mutation(text, edit.pattern, edit.replacement)
        except StaleMutant as error:
            raise StaleMutant(f"{mutant.id} ({edit.file}): {error}") from None
        path.write_text(patched, encoding="utf-8", newline="")


def copy_service(source, destination):
    """Copy the service sources without tests and build artefacts.

    Args:
        source: Path of the service directory.
        destination: Non-existing directory to create.

    Returns:
        None.

    Raises:
        OSError: The copy fails.
    """
    def ignore(directory, names):
        """Choose the entries that are not copied.

        Args:
            directory: Directory being copied.
            names: Its entries.

        Returns:
            Names of test files, executables and the ``.git`` directory (a directory called ``ghreport`` is kept).

        Raises:
            None.
        """
        skipped = set(shutil.ignore_patterns("*_test.go", "*.exe", ".git")(directory, names))
        skipped |= {n for n in names if n == "ghreport" and not (Path(directory) / n).is_dir()}
        return skipped

    shutil.copytree(source, destination, ignore=ignore)


def build_service(directory, output):
    """Compile the service.

    Args:
        directory: Directory with go.mod.
        output: Path of the binary to create.

    Returns:
        None.

    Raises:
        BuildError: The compiler fails (message holds its output) or exceeds BUILD_TIMEOUT.
    """
    try:
        done = subprocess.run(["go", "build", "-o", str(output), "./cmd/ghreport"], cwd=directory,
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=BUILD_TIMEOUT, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BuildError(f"go build could not run: {error}") from None
    if done.returncode != 0:
        raise BuildError(f"go build failed:\n{done.stdout}{done.stderr}")


def first_failure(output):
    """Extract the first failing test from pytest's short summary.

    Args:
        output: Combined pytest output produced with ``-rfE``.

    Returns:
        The node id of the first FAILED/ERROR line (``file.py::test`` without directories or message), or "".

    Raises:
        None.
    """
    match = re.search(r"^(?:FAILED|ERROR) (\S+)", output, re.MULTILINE)
    if not match:
        return ""
    return re.sub(r"^.*[\\/]", "", match.group(1))


def classify(result):
    """Turn a suite result into a verdict.

    Args:
        result: SuiteResult of a mutant run.

    Returns:
        "killed" when the suite reported failing tests (exit code 1), "survived" when it passed (0),
        "error" for timeouts and other pytest exit codes (internal error, usage, no tests, interruption).

    Raises:
        None.
    """
    if result.timed_out:
        return "error"
    if result.returncode == 1:
        return "killed"
    if result.returncode == 0:
        return "survived"
    return "error"


def run_suite(binary, base_url, workdir, timeout=SUITE_TIMEOUT):
    """Run the main suite once (stop at the first failure).

    Args:
        binary: Path of the ghreport executable under test.
        base_url: Address of the stub server.
        workdir: Empty working directory for the pytest process (stray files of mutants land there).
        timeout: Seconds before the run is killed.

    Returns:
        SuiteResult; ``timed_out`` is True when the timeout hit.

    Raises:
        None.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GHREPORT_", "GITHUB_", "PYTEST_"))}
    env.update(GHREPORT_BIN=str(binary), GITHUB_API_BASE_URL=base_url, GHREPORT_TIMEOUT=SERVICE_TIMEOUT)
    command = [sys.executable, "-m", "pytest", str(API_TESTS), "-q", "-x", "-m", "not selfcheck", "-rfE",
               "--tb=line", "-p", "no:cacheprovider"]
    started = time.monotonic()
    try:
        done = subprocess.run(command, cwd=workdir, env=env, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as error:
        partial = error.stdout.decode("utf-8", "replace") if isinstance(error.stdout, bytes) else (error.stdout or "")
        return SuiteResult(-1, partial, True, time.monotonic() - started)
    return SuiteResult(done.returncode, done.stdout + done.stderr, False, time.monotonic() - started)


def run_with_stub(binary, flags, workdir, runner=run_suite):
    """Run the suite against a stub server started with the given flags.

    Args:
        binary: Path of the ghreport executable.
        flags: Iterable of stub flag names (may be empty).
        workdir: Working directory for pytest.
        runner: Function ``(binary, base_url, workdir) -> SuiteResult`` (replaceable in tests).

    Returns:
        SuiteResult of the run; the stub process is always stopped.

    Raises:
        RuntimeError: The stub server does not start.
    """
    process, url = stub_server.spawn_process(list(flags))
    try:
        return runner(binary, url, workdir)
    finally:
        process.kill()
        process.wait()
        process.stdout.close()


def judge(mutant_id, kind, result, equivalent=""):
    """Build the Outcome of one mutant from its suite result.

    Args:
        mutant_id: Mutant name.
        kind: "server", "service" or "compatible".
        result: SuiteResult.
        equivalent: Documented equivalence reason (service mutants only).

    Returns:
        Outcome. Compatible deviations must stay green ("green" is good, a failing suite is "survived"
        meaning the suite wrongly rejects a compliant server); other kinds are good when "killed", or
        "equivalent" when they survive with a documented reason.

    Raises:
        None.
    """
    verdict = classify(result)
    failure = first_failure(result.output)
    seconds = round(result.seconds, 1)
    if verdict == "error":
        tail = "timeout" if result.timed_out else f"pytest exit code {result.returncode}"
        return Outcome(mutant_id, kind, "error", failure, seconds, tail)
    if kind == "compatible":
        status = "green" if verdict == "survived" else "survived"
        note = "" if status == "green" else "the suite rejects a compliant server"
        return Outcome(mutant_id, kind, status, failure, seconds, note)
    if verdict == "killed":
        return Outcome(mutant_id, kind, "killed", failure, seconds)
    if equivalent:
        return Outcome(mutant_id, kind, "equivalent", "", seconds, equivalent)
    return Outcome(mutant_id, kind, "survived", "", seconds, "the suite stayed green")


def is_success(outcomes):
    """Decide the overall verdict.

    Args:
        outcomes: List of Outcome.

    Returns:
        True when there is at least one outcome and every one is "killed", "equivalent" or "green".

    Raises:
        None.
    """
    return bool(outcomes) and all(o.status in ("killed", "equivalent", "green") for o in outcomes)


def format_table(outcomes):
    """Render the result table.

    Args:
        outcomes: List of Outcome.

    Returns:
        Multi-line text: header, one row per mutant (id, kind, status, seconds, first failing test or note)
        and a summary line with the counts.

    Raises:
        None.
    """
    rows = [(o.id, o.kind, o.status.upper(), f"{o.seconds:.1f}", o.first_failure or o.note or "-")
            for o in outcomes]
    header = ("mutant", "kind", "status", "sec", "first failing test / note")
    widths = [max(len(str(row[i])) for row in [header] + rows) for i in range(4)]
    lines = ["  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row[:4])) + "  " + row[4]
             for row in [header] + rows]
    counts = {}
    for outcome in outcomes:
        counts[outcome.status] = counts.get(outcome.status, 0) + 1
    summary = ", ".join(f"{name}: {count}" for name, count in sorted(counts.items()))
    return "\n".join(lines + ["", f"total {len(outcomes)} - {summary}"])


def select(only):
    """Pick the mutants to run.

    Args:
        only: Mutant id (exact, or without the "server:"/"compatible:" prefix) or None for all.

    Returns:
        Tuple (server ids, compatible ids, service mutants) restricted to ``only``.

    Raises:
        KeyError: ``only`` names no mutant.
    """
    servers, compatible, services = list(SERVER_MUTANTS), list(COMPATIBLE_DEVIATIONS), list(SERVICE_MUTANTS)
    if only is None:
        return servers, compatible, services
    servers = [i for i in servers if only in (i, i.split(":", 1)[1])]
    compatible = [i for i in compatible if only in (i, i.split(":", 1)[1])]
    services = [m for m in services if m.id == only]
    if not (servers or compatible or services):
        raise KeyError(f"unknown mutant id: {only}")
    return servers, compatible, services


def validate(service_dir):
    """Check that every mutation pattern still matches exactly once (no build, no tests).

    Args:
        service_dir: Path of the service directory.

    Returns:
        None.

    Raises:
        StaleMutant: Some mutant is stale (the message names it).
    """
    with tempfile.TemporaryDirectory(prefix="ghreport-validate-") as scratch:
        for mutant in SERVICE_MUTANTS:
            target = Path(scratch) / mutant.id
            copy_service(service_dir, target)
            apply_mutant(mutant, target)


def run_all(service_dir, only=None, skip_baseline=False, log=print):
    """Execute the whole mutation check.

    Args:
        service_dir: Path of the service directory (read only).
        only: Run just this mutant id, or None.
        skip_baseline: Skip the unmutated run (development aid; the final check must not skip it).
        log: Function used for progress lines.

    Returns:
        Tuple (exit code, list of Outcome): 0 when every mutant was killed (documented equivalents and
        compatible deviations staying green count as good), 1 when some survived or errored, 2 when the
        baseline is not green or a mutant is stale/does not compile.

    Raises:
        KeyError: ``only`` names no mutant.
    """
    servers, compatible, services = select(only)
    outcomes = []
    with tempfile.TemporaryDirectory(prefix="ghreport-mutants-") as scratch:
        scratch = Path(scratch)
        base_binary = scratch / "ghreport-base.exe"
        try:
            build_service(service_dir, base_binary)
        except BuildError as error:
            log(f"baseline build failed: {error}")
            return 2, outcomes
        if not skip_baseline:
            log("baseline: unmutated service + compliant stub ...")
            work = scratch / "work-baseline"
            work.mkdir()
            result = run_with_stub(base_binary, [], work)
            if result.returncode != 0:
                log("baseline is NOT green, mutation check stopped:\n" + result.output[-3000:])
                return 2, outcomes
            log(f"baseline green ({result.seconds:.1f} s)")
        for ident in servers + compatible:
            flag = {**SERVER_MUTANTS, **COMPATIBLE_DEVIATIONS}[ident]
            kind = "server" if ident in SERVER_MUTANTS else "compatible"
            work = scratch / f"work-{flag}"
            work.mkdir()
            outcome = judge(ident, kind, run_with_stub(base_binary, [flag], work))
            outcomes.append(outcome)
            log(f"{ident}: {outcome.status}")
        for mutant in services:
            source = scratch / f"src-{mutant.id}"
            copy_service(service_dir, source)
            try:
                apply_mutant(mutant, source)
                binary = scratch / f"ghreport-{mutant.id}.exe"
                build_service(source, binary)
            except (StaleMutant, BuildError) as error:
                log(f"{mutant.id}: INVALID MUTANT - {error}")
                return 2, outcomes
            work = scratch / f"work-{mutant.id}"
            work.mkdir()
            outcome = judge(mutant.id, "service", run_with_stub(binary, [], work), mutant.equivalent)
            outcomes.append(outcome)
            log(f"{mutant.id}: {outcome.status}")
    return (0 if is_success(outcomes) else 1), outcomes


def main(argv=None):
    """Command-line entry point.

    Args:
        argv: Argument list without the program name; None means sys.argv[1:].

    Returns:
        0 when all mutants were killed (see run_all), 1 when some survived, 2 for a failing baseline or an
        invalid mutant.

    Raises:
        SystemExit: argparse exits on malformed command lines.
    """
    parser = argparse.ArgumentParser(description="Mutation check of the ghreport test suite")
    parser.add_argument("--only", help="run a single mutant (id from the table)")
    parser.add_argument("--service-dir", default=str(REPO_ROOT / "service"), help="path of the Go service")
    parser.add_argument("--skip-baseline", action="store_true", help="skip the unmutated run (development only)")
    parser.add_argument("--validate", action="store_true", help="only check that every pattern matches once")
    args = parser.parse_args(argv)
    service_dir = Path(args.service_dir).resolve()
    if args.validate:
        try:
            validate(service_dir)
        except StaleMutant as error:
            print(f"STALE MUTANT: {error}", file=sys.stderr)
            return 2
        print(f"all {len(SERVICE_MUTANTS)} service mutation patterns match exactly once")
        return 0
    try:
        code, outcomes = run_all(service_dir, args.only, args.skip_baseline)
    except KeyError as error:
        print(error, file=sys.stderr)
        return 2
    if outcomes:
        print()
        print(format_table(outcomes))
    for note in NOT_MUTATED:
        print(f"not mutated: {note}")
    return code


if __name__ == "__main__":
    sys.exit(main())
