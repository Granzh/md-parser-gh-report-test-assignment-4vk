package app_test

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"io/fs"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"strings"
	"testing"
	"time"

	"ghreport/internal/app"
	"ghreport/internal/clock"
	"ghreport/internal/testutil"
)

const guardToken = "test-token-DO-NOT-USE"

var fixedNow = time.Date(2026, 10, 14, 9, 0, 0, 0, time.UTC)

// result is the observable outcome of one Run.
type result struct {
	code   int
	stdout string
	stderr string
	clock  *clock.Fake
	dir    string // working directory holding the default cache
}

// opts tunes one run.
type opts struct {
	env       map[string]string
	now       time.Time
	ctx       context.Context
	http      *http.Client
	stdout    io.Writer
	dir       string // reuse a working directory
	noCacheOf bool   // do not append --cache-dir
}

// execute runs the service with fake time and an isolated working directory.
//
// Args: t is the test; argv are the arguments; o tunes the run (zero value is fine). Unless noCacheOf is set,
// "--cache-dir <dir>/cache" is appended; relative paths in argv are NOT rewritten, tests use result.dir.
// Returns: exit code, captured outputs, the fake clock and the directory.
// Errors: never fails.
func execute(t *testing.T, argv []string, o opts) result {
	t.Helper()
	dir := o.dir
	if dir == "" {
		dir = t.TempDir()
	}
	if !o.noCacheOf {
		argv = append(append([]string{}, argv...), "--cache-dir", filepath.Join(dir, "cache"))
	}
	fake := clock.NewFake(fixedNow)
	now := o.now
	if now.IsZero() {
		now = fixedNow
	}
	ctx := o.ctx
	if ctx == nil {
		ctx = context.Background()
	}
	var stdout, stderr bytes.Buffer
	var out io.Writer = &stdout
	if o.stdout != nil {
		out = o.stdout
	}
	deps := app.Deps{Clock: fake, HTTP: o.http, Now: func() time.Time { return now }, Getenv: func(k string) string { return o.env[k] }}
	code := app.Run(ctx, argv, deps, out, &stderr)
	return result{code: code, stdout: stdout.String(), stderr: stderr.String(), clock: fake, dir: dir}
}

// base returns an environment pointing at a fake server.
//
// Args: fake is the server; token may be "".
// Returns: the environment map.
// Errors: never fails.
func base(fake *testutil.FakeGitHub, token string) map[string]string {
	return map[string]string{"GITHUB_API_BASE_URL": fake.URL, "GITHUB_TOKEN": token}
}

// items returns issues 1..n created on consecutive days.
//
// Args: n is the count, 1..28.
// Returns: the items.
// Errors: never fails.
func items(n int) []testutil.Item {
	var out []testutil.Item
	for i := 1; i <= n; i++ {
		out = append(out, testutil.NewIssue(i, time.Date(2026, 1, i, 10, 0, 0, 0, time.UTC).Format(time.RFC3339)))
	}
	return out
}

// traceLines parses a JSONL file.
//
// Args: path is the trace file.
// Returns: one map per line.
// Errors: never fails; calls t.Fatal on read or parse errors.
func traceLines(t *testing.T, path string) []map[string]any {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var out []map[string]any
	for _, line := range strings.Split(strings.TrimSpace(string(b)), "\n") {
		if line == "" {
			continue
		}
		var m map[string]any
		if err := json.Unmarshal([]byte(line), &m); err != nil {
			t.Fatalf("%q: %v", line, err)
		}
		out = append(out, m)
	}
	return out
}

// exists tells whether a path exists.
//
// Args: path is any path.
// Returns: true if it exists.
// Errors: never fails.
func exists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}

// covers: Run
func TestSpecExampleB1(t *testing.T) {
	fake := testutil.New(t)
	first := testutil.NewIssue(1, "2026-01-05T10:00:00Z")
	first["title"], first["user"] = "Первый выпуск", testutil.Item{"login": "octocat"}
	first["labels"] = []any{testutil.Item{"name": "bug"}}
	first["html_url"] = "https://github.com/cli/cli/issues/1"
	second := testutil.NewIssue(2, "2026-01-06T11:00:00Z")
	second["title"], second["user"] = "Второй выпуск", testutil.Item{"login": "hubot"}
	second["html_url"] = "https://github.com/cli/cli/issues/2"
	fake.SetRepo("cli/cli", []testutil.Item{first, second})

	r := execute(t, []string{"--repos", "cli/cli,example/missing,cli/cli"}, opts{env: base(fake, "")})
	want := `{
  "generated_at": "2026-10-14T09:00:00Z",
  "repos": [
    {
      "repo": "cli/cli",
      "error": null,
      "count": 2,
      "issues": [
        {
          "number": 1,
          "title": "Первый выпуск",
          "author": "octocat",
          "labels": [
            "bug"
          ],
          "created_at": "2026-01-05T10:00:00Z",
          "url": "https://github.com/cli/cli/issues/1"
        },
        {
          "number": 2,
          "title": "Второй выпуск",
          "author": "hubot",
          "labels": [],
          "created_at": "2026-01-06T11:00:00Z",
          "url": "https://github.com/cli/cli/issues/2"
        }
      ]
    },
    {
      "repo": "example/missing",
      "error": "not_found",
      "count": 0,
      "issues": []
    }
  ]
}
`
	if r.code != 1 || r.stdout != want {
		t.Fatalf("code %d\n%s", r.code, r.stdout)
	}
	if !strings.Contains(r.stderr, "example/missing") {
		t.Fatalf("stderr lacks the failed repository: %q", r.stderr)
	}
	// duplicate in --repos removed: cli/cli requested once, example/missing once
	if fake.Count() != 2 {
		t.Fatalf("requests: %d", fake.Count())
	}
}

// covers: Run
func TestAllRepositoriesFailedIsFatal(t *testing.T) {
	cases := map[string][]string{
		"two missing":    {"--repos", "a/a,b/b"},
		"single missing": {"--repos", "a/a"},
	}
	for name, argv := range cases {
		t.Run(name, func(t *testing.T) {
			fake := testutil.New(t)
			r := execute(t, argv, opts{env: base(fake, "")})
			if r.code != 3 || r.stdout != `{"error": "all_failed"}`+"\n" || r.stderr == "" {
				t.Fatalf("code %d stdout %q stderr %q", r.code, r.stdout, r.stderr)
			}
		})
	}
}

// covers: Run
func TestAllFailedStillWritesReportFile(t *testing.T) {
	fake := testutil.New(t)
	dir := t.TempDir()
	out := filepath.Join(dir, "out", "report.json")
	r := execute(t, []string{"--repos", "a/a", "--out", out}, opts{env: base(fake, ""), dir: dir})
	if r.code != 3 || r.stdout != `{"error": "all_failed"}`+"\n" {
		t.Fatalf("code %d stdout %q", r.code, r.stdout)
	}
	b, err := os.ReadFile(out)
	if err != nil || !strings.Contains(string(b), `"error": "not_found"`) {
		t.Fatalf("report file: %v %s", err, b)
	}
}

// covers: Run
func TestUnauthorizedStopsImmediately(t *testing.T) {
	fake := testutil.New(t)
	fake.SetRepo("a/a", items(1))
	fake.SetRepo("c/c", items(1))
	fake.SetReply("b/b", testutil.Reply{Status: 401, Body: `{"message":"Bad credentials"}`})
	dir := t.TempDir()
	out := filepath.Join(dir, "report.json")
	r := execute(t, []string{"--repos", "a/a,b/b,c/c", "--out", out}, opts{env: base(fake, guardToken), dir: dir})
	if r.code != 3 || r.stdout != `{"error": "unauthorized"}`+"\n" || r.stderr == "" {
		t.Fatalf("code %d stdout %q stderr %q", r.code, r.stdout, r.stderr)
	}
	for _, req := range fake.Requests() {
		if strings.Contains(req.URI, "/c/c/") {
			t.Fatal("third repository was requested after 401")
		}
	}
	if fake.Count() != 2 || len(r.clock.Sleeps()) != 0 {
		t.Fatalf("requests %d, sleeps %v", fake.Count(), r.clock.Sleeps())
	}
	if exists(out) {
		t.Fatal("report must not be written after 401")
	}
}

// covers: Run
func TestUnauthorizedOnFirstRepositoryWithOutDash(t *testing.T) {
	fake := testutil.New(t)
	fake.SetReply("a/a", testutil.Reply{Status: 401})
	r := execute(t, []string{"--repos", "a/a", "--out", "-"}, opts{env: base(fake, "")})
	if r.code != 3 || r.stdout != `{"error": "unauthorized"}`+"\n" {
		t.Fatalf("code %d stdout %q", r.code, r.stdout)
	}
}

// covers: Run
func TestArgumentErrorsProduceNoStdoutAndNoFiles(t *testing.T) {
	cases := map[string][]string{
		"no args":              {},
		"missing repos value":  {"--repos"},
		"unknown flag":         {"--repos", "a/b", "--nope"},
		"bad repo":             {"--repos", "a"},
		"empty item":           {"--repos", "a/b,,c/d"},
		"bad limit zero":       {"--repos", "a/b", "--limit", "0"},
		"bad limit word":       {"--repos", "a/b", "--limit", "abc"},
		"bad since":            {"--repos", "a/b", "--since", "2026-02-30"},
		"positional":           {"--repos", "a/b", "extra"},
		"bad with trace":       {"--repos", "a/b", "--limit", "x", "--trace", "TRACE"},
		"bad with out":         {"--repos", "a/b", "--since", "x", "--out", "OUT"},
		"empty out":            {"--repos", "a/b", "--out="},
		"limit overflow":       {"--repos", "a/b", "--limit", "99999999999999999999"},
		"flag without a value": {"--repos", "a/b", "--limit"},
	}
	for name, argv := range cases {
		t.Run(name, func(t *testing.T) {
			dir := t.TempDir()
			fixed := make([]string, len(argv))
			for i, a := range argv {
				switch a {
				case "TRACE":
					a = filepath.Join(dir, "trace.jsonl")
				case "OUT":
					a = filepath.Join(dir, "out.json")
				}
				fixed[i] = a
			}
			fake := testutil.New(t)
			r := execute(t, fixed, opts{env: base(fake, guardToken), dir: dir})
			if r.code != 2 || r.stdout != "" || r.stderr == "" || !strings.HasPrefix(r.stderr, "ghreport:") {
				t.Fatalf("code %d stdout %q stderr %q", r.code, r.stdout, r.stderr)
			}
			entries, _ := os.ReadDir(dir)
			if len(entries) != 0 || fake.Count() != 0 {
				t.Fatalf("side effects: %v, %d requests", entries, fake.Count())
			}
			if strings.Contains(r.stderr, guardToken) {
				t.Fatal("token in stderr")
			}
		})
	}
}

// covers: Run
func TestEnvironmentAndTraceErrorsExitWithTwo(t *testing.T) {
	dir := t.TempDir()
	r := execute(t, []string{"--repos", "a/b"}, opts{env: map[string]string{"GITHUB_API_BASE_URL": "ftp://x"}, dir: dir})
	if r.code != 2 || r.stdout != "" || !strings.Contains(r.stderr, "GITHUB_API_BASE_URL") {
		t.Fatalf("env: code %d stdout %q stderr %q", r.code, r.stdout, r.stderr)
	}
	if exists(filepath.Join(dir, "cache")) {
		t.Fatal("cache dir created despite an environment error")
	}
	file := filepath.Join(dir, "file")
	_ = os.WriteFile(file, []byte("x"), 0o600)
	fake := testutil.New(t)
	r = execute(t, []string{"--repos", "a/b", "--trace", filepath.Join(file, "t.jsonl")}, opts{env: base(fake, ""), dir: dir})
	if r.code != 2 || r.stdout != "" || r.stderr == "" || fake.Count() != 0 {
		t.Fatalf("trace: code %d stdout %q stderr %q requests %d", r.code, r.stdout, r.stderr, fake.Count())
	}
}

// covers: Run
func TestHelpPrintsUsageToStderr(t *testing.T) {
	dir := t.TempDir()
	r := execute(t, []string{"--help"}, opts{dir: dir})
	if r.code != 0 || r.stdout != "" || !strings.Contains(r.stderr, "--repos") {
		t.Fatalf("code %d stdout %q stderr %q", r.code, r.stdout, r.stderr)
	}
	if entries, _ := os.ReadDir(dir); len(entries) != 0 {
		t.Fatalf("files created: %v", entries)
	}
}

// covers: Run
func TestOutFileAndStdoutModes(t *testing.T) {
	fake := testutil.New(t)
	fake.SetRepo("o/r", items(3))
	dir := t.TempDir()
	out := filepath.Join(dir, "nested", "dir", "report.json")
	r := execute(t, []string{"--repos", "o/r", "--out", out}, opts{env: base(fake, ""), dir: dir})
	if r.code != 0 || r.stdout != "" {
		t.Fatalf("code %d stdout %q", r.code, r.stdout)
	}
	fileBytes, err := os.ReadFile(out)
	if err != nil || !json.Valid(fileBytes) || !strings.HasSuffix(string(fileBytes), "}\n") {
		t.Fatalf("file: %v %s", err, fileBytes)
	}
	r2 := execute(t, []string{"--repos", "o/r", "--out", "-"}, opts{env: base(fake, "")})
	if r2.code != 0 || r2.stdout != string(fileBytes) {
		t.Fatalf("stdout mode differs:\n%s\n%s", r2.stdout, fileBytes)
	}
	r3 := execute(t, []string{"--repos", "o/r"}, opts{env: base(fake, "")})
	if r3.stdout != r2.stdout {
		t.Fatal("default --out must be stdout")
	}
	var parsed struct {
		GeneratedAt string `json:"generated_at"`
		Repos       []struct {
			Count int `json:"count"`
		} `json:"repos"`
	}
	if err := json.Unmarshal(fileBytes, &parsed); err != nil || parsed.Repos[0].Count != 3 || parsed.GeneratedAt != "2026-10-14T09:00:00Z" {
		t.Fatalf("parsed %+v, %v", parsed, err)
	}
}

// covers: Run
func TestLimitSinceAndOrder(t *testing.T) {
	fake := testutil.New(t)
	fake.SetRepo("o/r", items(10))
	r := execute(t, []string{"--repos", "o/r", "--since", "2026-01-04", "--limit", "3"}, opts{env: base(fake, "")})
	var rep struct {
		Repos []struct {
			Issues []struct {
				Number int `json:"number"`
			} `json:"issues"`
		} `json:"repos"`
	}
	if err := json.Unmarshal([]byte(r.stdout), &rep); err != nil || r.code != 0 {
		t.Fatalf("code %d: %v", r.code, err)
	}
	var nums []int
	for _, is := range rep.Repos[0].Issues {
		nums = append(nums, is.Number)
	}
	if !reflect.DeepEqual(nums, []int{4, 5, 6}) {
		t.Fatalf("got %v", nums)
	}
}

// covers: Run
func TestTraceFileOnlyWhenRequested(t *testing.T) {
	fake := testutil.New(t)
	fake.SetRepo("o/r", items(1))
	dir := t.TempDir()
	r := execute(t, []string{"--repos", "o/r"}, opts{env: base(fake, ""), dir: dir})
	if r.code != 0 {
		t.Fatalf("code %d", r.code)
	}
	entries, _ := os.ReadDir(dir)
	if len(entries) != 1 || entries[0].Name() != "cache" {
		t.Fatalf("unexpected files: %v", entries)
	}
	tracePath := filepath.Join(dir, "logs", "trace.jsonl")
	r = execute(t, []string{"--repos", "o/r", "--trace", tracePath}, opts{env: base(fake, ""), dir: dir})
	recs := traceLines(t, tracePath)
	if r.code != 0 || len(recs) != 1 || recs[0]["status"] != 304.0 || recs[0]["from_cache"] != true || recs[0]["method"] != "GET" || recs[0]["attempt"] != 1.0 || recs[0]["error"] != nil {
		t.Fatalf("code %d trace %v", r.code, recs)
	}
	if ts, _ := recs[0]["ts"].(string); !regexp.MustCompile(`^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$`).MatchString(ts) {
		t.Fatalf("ts %q", ts)
	}
	// (the first run filled the cache, so this one is a 304); the trace is truncated at every start
	execute(t, []string{"--repos", "o/r", "--trace", tracePath}, opts{env: base(fake, ""), dir: dir})
	if len(traceLines(t, tracePath)) != 1 {
		t.Fatal("trace not truncated")
	}
}

// covers: Run
func TestRateLimitRetryAfterIsHonoured(t *testing.T) {
	fake := testutil.New(t)
	fake.SetRepo("o/r", items(2))
	fake.QueueReplies("o/r", testutil.Reply{Status: 429, Body: "slow down", Header: map[string]string{"Retry-After": "7"}})
	dir := t.TempDir()
	tracePath := filepath.Join(dir, "t.jsonl")
	r := execute(t, []string{"--repos", "o/r", "--trace", tracePath}, opts{env: base(fake, ""), dir: dir})
	if r.code != 0 || !strings.Contains(r.stdout, `"count": 2`) {
		t.Fatalf("code %d stdout %s stderr %s", r.code, r.stdout, r.stderr)
	}
	if !reflect.DeepEqual(r.clock.Sleeps(), []time.Duration{7 * time.Second}) {
		t.Fatalf("sleeps %v", r.clock.Sleeps())
	}
	recs := traceLines(t, tracePath)
	if len(recs) != 2 || recs[0]["status"] != 429.0 || recs[0]["attempt"] != 1.0 || recs[1]["status"] != 200.0 || recs[1]["attempt"] != 2.0 {
		t.Fatalf("trace %v", recs)
	}
}

// covers: Run
func TestServerErrorsRetriedThenSucceed(t *testing.T) {
	fake := testutil.New(t)
	fake.SetRepo("o/r", items(1))
	fake.QueueReplies("o/r", testutil.Reply{Status: 502}, testutil.Reply{Status: 503}, testutil.Reply{Status: 500})
	r := execute(t, []string{"--repos", "o/r"}, opts{env: base(fake, "")})
	if r.code != 0 || !strings.Contains(r.stdout, `"count": 1`) {
		t.Fatalf("code %d stdout %s", r.code, r.stdout)
	}
	if want := []time.Duration{time.Second, 2 * time.Second, 4 * time.Second}; !reflect.DeepEqual(r.clock.Sleeps(), want) || fake.Count() != 4 {
		t.Fatalf("sleeps %v, requests %d", r.clock.Sleeps(), fake.Count())
	}
}

// covers: Run
func TestServerErrorsExhaustedGiveRequestFailed(t *testing.T) {
	fake := testutil.New(t)
	fake.SetRepo("good/repo", items(1))
	fake.SetReply("bad/repo", testutil.Reply{Status: 500, Body: "boom " + guardToken})
	r := execute(t, []string{"--repos", "bad/repo,good/repo"}, opts{env: base(fake, guardToken)})
	if r.code != 1 || !strings.Contains(r.stdout, `"error": "request_failed"`) || !strings.Contains(r.stdout, `"repo": "good/repo"`) {
		t.Fatalf("code %d stdout %s", r.code, r.stdout)
	}
	if want := []time.Duration{time.Second, 2 * time.Second, 4 * time.Second}; !reflect.DeepEqual(r.clock.Sleeps(), want) {
		t.Fatalf("sleeps %v", r.clock.Sleeps())
	}
	if strings.Contains(r.stdout+r.stderr, guardToken) {
		t.Fatal("token or response body leaked")
	}
}

// covers: Run
func TestInvalidResponseAndRateLimitedCodes(t *testing.T) {
	fake := testutil.New(t)
	fake.SetReply("bad/json", testutil.Reply{Body: `{"not":"an array"}`})
	fake.SetReply("lim/ited", testutil.Reply{Status: 429, Header: map[string]string{"Retry-After": "1"}})
	fake.SetRepo("ok/ok", items(1))
	r := execute(t, []string{"--repos", "bad/json,lim/ited,ok/ok"}, opts{env: base(fake, "")})
	if r.code != 1 || !strings.Contains(r.stdout, `"error": "invalid_response"`) || !strings.Contains(r.stdout, `"error": "rate_limited"`) {
		t.Fatalf("code %d stdout %s", r.code, r.stdout)
	}
	if !reflect.DeepEqual(r.clock.Sleeps(), []time.Duration{time.Second, time.Second, time.Second}) {
		t.Fatalf("sleeps %v", r.clock.Sleeps())
	}
}

// covers: Run
func TestSecondRunUsesCache(t *testing.T) {
	fake := testutil.New(t)
	fake.PageSize = 2
	fake.SetRepo("o/r", items(5))
	dir := t.TempDir()
	t1, t2 := filepath.Join(dir, "t1.jsonl"), filepath.Join(dir, "t2.jsonl")
	first := execute(t, []string{"--repos", "o/r", "--trace", t1}, opts{env: base(fake, ""), dir: dir})
	second := execute(t, []string{"--repos", "o/r", "--trace", t2}, opts{env: base(fake, ""), dir: dir, now: fixedNow.Add(time.Hour)})
	if first.code != 0 || second.code != 0 {
		t.Fatalf("codes %d %d", first.code, second.code)
	}
	for _, rec := range traceLines(t, t1) {
		if rec["from_cache"] != false || rec["status"] != 200.0 {
			t.Fatalf("first run: %v", rec)
		}
	}
	recs := traceLines(t, t2)
	if len(recs) != 3 {
		t.Fatalf("second run trace: %v", recs)
	}
	for _, rec := range recs {
		if rec["from_cache"] != true || rec["status"] != 304.0 {
			t.Fatalf("second run: %v", rec)
		}
	}
	strip := regexp.MustCompile(`"generated_at": "[^"]*"`)
	if strip.ReplaceAllString(first.stdout, "") != strip.ReplaceAllString(second.stdout, "") || first.stdout == second.stdout {
		t.Fatal("reports must be identical except generated_at")
	}
	if !strings.Contains(second.stdout, "2026-10-14T10:00:00Z") {
		t.Fatalf("generated_at not from Now: %s", second.stdout)
	}
}

// roundTripFunc adapts a function to http.RoundTripper.
type roundTripFunc func(*http.Request) (*http.Response, error)

// RoundTrip calls f.
//
// Args: r is the request.
// Returns: f's result.
// Errors: f's error.
func (f roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

// covers: Run
func TestBaseURLVariants(t *testing.T) {
	cases := []struct {
		name     string
		env      string
		wantHost string
		wantPath string
	}{
		{"unset uses api.github.com", "", "https://api.github.com", "/repos/o/r/issues"},
		{"trailing slash", "https://ghe.example.com/", "https://ghe.example.com", "/repos/o/r/issues"},
		{"many trailing slashes", "https://ghe.example.com///", "https://ghe.example.com", "/repos/o/r/issues"},
		{"path prefix", "https://ghe.example.com/api/v3", "https://ghe.example.com", "/api/v3/repos/o/r/issues"},
		{"path prefix with slash", "https://ghe.example.com/api/v3/", "https://ghe.example.com", "/api/v3/repos/o/r/issues"},
		{"explicit default", "https://api.github.com", "https://api.github.com", "/repos/o/r/issues"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			var seen []*http.Request
			client := &http.Client{Transport: roundTripFunc(func(r *http.Request) (*http.Response, error) {
				seen = append(seen, r)
				return &http.Response{StatusCode: 200, Header: http.Header{}, Body: io.NopCloser(strings.NewReader("[]")), Request: r}, nil
			})}
			env := map[string]string{"GITHUB_API_BASE_URL": tc.env, "GITHUB_TOKEN": guardToken}
			r := execute(t, []string{"--repos", "o/r"}, opts{env: env, http: client})
			if r.code != 0 || len(seen) != 1 {
				t.Fatalf("code %d, requests %d, stderr %s", r.code, len(seen), r.stderr)
			}
			got := seen[0]
			if got.URL.Scheme+"://"+got.URL.Host != tc.wantHost || got.URL.Path != tc.wantPath {
				t.Fatalf("requested %s", got.URL)
			}
			if got.Header.Get("Authorization") != "Bearer "+guardToken {
				t.Fatal("token must go to the configured API origin")
			}
		})
	}
}

// covers: Run
func TestBaseURLWithTrailingSlashAgainstServer(t *testing.T) {
	fake := testutil.New(t)
	fake.SetRepo("o/r", items(1))
	env := map[string]string{"GITHUB_API_BASE_URL": fake.URL + "/"}
	if r := execute(t, []string{"--repos", "o/r"}, opts{env: env}); r.code != 0 || fake.Count() != 1 {
		t.Fatalf("code %d, requests %d", r.code, fake.Count())
	}
}

// covers: Run
func TestAuthorizationHeaderOnlyWithToken(t *testing.T) {
	fake := testutil.New(t)
	fake.SetRepo("o/r", items(1))
	execute(t, []string{"--repos", "o/r"}, opts{env: base(fake, "")})
	execute(t, []string{"--repos", "o/r"}, opts{env: base(fake, "   ")})
	for _, req := range fake.Requests() {
		if _, present := req.Header["Authorization"]; present {
			t.Fatal("Authorization sent without a token")
		}
	}
	fake.Reset()
	execute(t, []string{"--repos", "o/r"}, opts{env: base(fake, "  "+guardToken+"\n")})
	if got := fake.Requests()[0].Header.Get("Authorization"); got != "Bearer "+guardToken {
		t.Fatalf("Authorization %q", got)
	}
}

// covers: Run
func TestTokenNeverLeaks(t *testing.T) {
	other := testutil.New(t)
	other.SetRepo("x/landing", items(1))
	fake := testutil.New(t)
	fake.PageSize = 2
	fake.SetRepo("ok/ok", items(5))
	fake.SetRepo("redirect/same", items(1))
	fake.SetReply("redirect/same", testutil.Reply{Status: 302, Header: map[string]string{"Location": "/repos/ok/ok/issues?per_page=100"}})
	fake.SetReply("redirect/away", testutil.Reply{Status: 301, Header: map[string]string{"Location": other.URL + "/repos/x/landing/issues"}})
	fake.SetReply("err/500", testutil.Reply{Status: 500, Body: "boom " + guardToken, Header: map[string]string{"X-Echo": guardToken}})
	fake.SetReply("err/403", testutil.Reply{Status: 403, Body: "forbidden " + guardToken})
	fake.SetReply("err/json", testutil.Reply{Body: "<" + guardToken + ">"})
	// the unauthorized repository is last, so that every other scenario runs first
	dir := t.TempDir()
	repos := "ok/ok,redirect/same,redirect/away,err/500,err/403,err/json,missing/one"
	r := execute(t, []string{"--repos", repos, "--out", filepath.Join(dir, "out.json"), "--trace", filepath.Join(dir, "trace.jsonl")}, opts{env: base(fake, guardToken), dir: dir})
	if r.code != 1 {
		t.Fatalf("code %d stderr %s", r.code, r.stderr)
	}
	r2 := execute(t, []string{"--repos", "ok/ok", "--trace", filepath.Join(dir, "trace2.jsonl")}, opts{env: base(fake, guardToken), dir: dir})
	fake.SetReply("err/401", testutil.Reply{Status: 401, Body: guardToken})
	r3 := execute(t, []string{"--repos", "err/401"}, opts{env: base(fake, guardToken), dir: dir})
	if r2.code != 0 || r3.code != 3 {
		t.Fatalf("codes %d %d", r2.code, r3.code)
	}
	for _, req := range other.Requests() {
		if req.Header.Get("Authorization") != "" {
			t.Fatal("token sent to a foreign origin after redirect")
		}
	}
	if other.Count() == 0 {
		t.Fatal("redirect target was never requested")
	}
	for name, text := range map[string]string{"stdout": r.stdout + r2.stdout + r3.stdout, "stderr": r.stderr + r2.stderr + r3.stderr} {
		if strings.Contains(text, guardToken) {
			t.Errorf("token in %s", name)
		}
	}
	files := 0
	err := filepath.WalkDir(dir, func(path string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() {
			return err
		}
		files++
		b, rerr := os.ReadFile(path)
		if rerr != nil {
			return rerr
		}
		if strings.Contains(string(b), guardToken) {
			t.Errorf("token in file %s", path)
		}
		return nil
	})
	if err != nil || files < 4 {
		t.Fatalf("walk: %v, %d files", err, files)
	}
}

// covers: Run
func TestRequestsAreSequential(t *testing.T) {
	fake := testutil.New(t)
	fake.PageSize = 2
	for _, name := range []string{"a/a", "b/b", "c/c"} {
		fake.SetRepo(name, items(7))
	}
	r := execute(t, []string{"--repos", "a/a,b/b,c/c"}, opts{env: base(fake, "")})
	if r.code != 0 || fake.Count() != 12 || fake.MaxConcurrent() != 1 {
		t.Fatalf("code %d, requests %d, max concurrent %d", r.code, fake.Count(), fake.MaxConcurrent())
	}
	var order []string
	for _, req := range fake.Requests() {
		order = append(order, strings.Split(req.URI, "/")[2])
	}
	if want := []string{"a", "a", "a", "a", "b", "b", "b", "b", "c", "c", "c", "c"}; !reflect.DeepEqual(order, want) {
		t.Fatalf("order %v", order)
	}
}

// covers: Run
func TestCancelledContext(t *testing.T) {
	fake := testutil.New(t)
	fake.SetRepo("a/a", items(1))
	fake.SetRepo("b/b", items(1))
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	r := execute(t, []string{"--repos", "a/a,b/b"}, opts{env: base(fake, ""), ctx: ctx})
	if r.code != app.ExitInterrupted || r.stdout != "" || !strings.Contains(r.stderr, "прервано") || fake.Count() != 0 {
		t.Fatalf("code %d stdout %q stderr %q requests %d", r.code, r.stdout, r.stderr, fake.Count())
	}
}

// covers: Run
func TestCancelledDuringRun(t *testing.T) {
	fake := testutil.New(t)
	fake.SetRepo("a/a", items(1))
	fake.SetRepo("b/b", items(1))
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	fake.OnRequest = cancel
	dir := t.TempDir()
	out := filepath.Join(dir, "out.json")
	r := execute(t, []string{"--repos", "a/a,b/b", "--out", out}, opts{env: base(fake, ""), ctx: ctx, dir: dir})
	if r.code != app.ExitInterrupted || r.stdout != "" || exists(out) {
		t.Fatalf("code %d stdout %q", r.code, r.stdout)
	}
	if fake.Count() != 1 {
		t.Fatalf("second repository requested after cancellation: %d", fake.Count())
	}
}

// failingWriter fails every write.
type failingWriter struct{}

// Write always fails.
//
// Args: p is ignored.
// Returns: (0, error).
// Errors: always.
func (failingWriter) Write(p []byte) (int, error) { return 0, errors.New("pipe closed") }

// covers: Run
func TestOutputWriteFailuresExitWithThree(t *testing.T) {
	fake := testutil.New(t)
	fake.SetRepo("o/r", items(1))
	r := execute(t, []string{"--repos", "o/r"}, opts{env: base(fake, ""), stdout: failingWriter{}})
	if r.code != app.ExitIO || !strings.Contains(r.stderr, "pipe closed") {
		t.Fatalf("stdout failure: code %d stderr %q", r.code, r.stderr)
	}
	dir := t.TempDir()
	blocker := filepath.Join(dir, "blocker")
	_ = os.WriteFile(blocker, []byte("x"), 0o600)
	r = execute(t, []string{"--repos", "o/r", "--out", filepath.Join(blocker, "r.json")}, opts{env: base(fake, ""), dir: dir})
	if r.code != app.ExitIO || r.stdout != "" || r.stderr == "" {
		t.Fatalf("file failure: code %d stdout %q stderr %q", r.code, r.stdout, r.stderr)
	}
}

// covers: Run
func TestUnusableCacheDirIsOnlyAWarning(t *testing.T) {
	fake := testutil.New(t)
	fake.SetRepo("o/r", items(2))
	dir := t.TempDir()
	blocker := filepath.Join(dir, "blocker")
	_ = os.WriteFile(blocker, []byte("x"), 0o600)
	r := execute(t, []string{"--repos", "o/r", "--cache-dir", filepath.Join(blocker, "cache")}, opts{env: base(fake, ""), dir: dir, noCacheOf: true})
	if r.code != 0 || !strings.Contains(r.stdout, `"count": 2`) || !strings.Contains(r.stderr, "предупреждение") {
		t.Fatalf("code %d stderr %q", r.code, r.stderr)
	}
}
