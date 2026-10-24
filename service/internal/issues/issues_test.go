package issues

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"ghreport/internal/args"
	"ghreport/internal/cache"
	"ghreport/internal/clock"
	"ghreport/internal/httpx"
	"ghreport/internal/model"
	"ghreport/internal/testutil"
	"ghreport/internal/trace"
)

const guardToken = "TOKEN-GUARD-77c1"

var repo = args.RepoRef{Owner: "o", Name: "r"}

// env bundles a fetcher wired to a fake server.
type env struct {
	fake      *testutil.FakeGitHub
	f         *Fetcher
	tracePath string
	warn      *bytes.Buffer
}

// newEnv creates a fetcher against a fake server.
//
// Args: t is the test; fake is the server; cacheDir is the cache directory or "" for no cache; token is the bearer
// token or "".
// Returns: the environment; the HTTP client uses a fake clock and a real trace file in a temp dir.
// Errors: never fails.
func newEnv(t *testing.T, fake *testutil.FakeGitHub, cacheDir, token string) *env {
	t.Helper()
	tracePath := filepath.Join(t.TempDir(), "trace.jsonl")
	w, err := trace.New(tracePath)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = w.Close() })
	base, _ := url.Parse(fake.URL)
	client := &httpx.Client{Clock: clock.NewFake(time.Date(2026, 10, 14, 9, 0, 0, 0, time.UTC)), Trace: w, Token: token, BaseOrigin: fake.URL, Timeout: 5 * time.Second}
	e := &env{fake: fake, tracePath: tracePath, warn: &bytes.Buffer{}}
	e.f = &Fetcher{Client: client, Base: base, Warn: e.warn}
	if cacheDir != "" {
		e.f.Cache = &cache.Store{Dir: cacheDir}
	}
	return e
}

// run fetches the default repository.
//
// Args: limit and since as in FetchOpenIssues.
// Returns: numbers of the returned issues and the error.
// Errors: reported through the second result.
func (e *env) run(limit int, since *time.Time) ([]int, error) {
	got, err := e.f.FetchOpenIssues(context.Background(), repo, limit, since)
	return numbers(got), err
}

// numbers extracts issue numbers.
//
// Args: list is any issue slice.
// Returns: the numbers in order (non-nil).
// Errors: never fails.
func numbers(list []model.Issue) []int {
	out := []int{}
	for _, is := range list {
		out = append(out, is.Number)
	}
	return out
}

// day builds the created_at text of January <n>, 2026.
//
// Args: n is the day of the month, 1..28.
// Returns: an RFC3339 UTC timestamp at 10:00.
// Errors: never fails.
func day(n int) string {
	return time.Date(2026, 1, n, 10, 0, 0, 0, time.UTC).Format(time.RFC3339)
}

// series creates issues 1..n, one per day.
//
// Args: n is the count, 1..28.
// Returns: the items in ascending order.
// Errors: never fails.
func series(n int) []testutil.Item {
	var out []testutil.Item
	for i := 1; i <= n; i++ {
		out = append(out, testutil.NewIssue(i, day(i)))
	}
	return out
}

// traceRecords parses the trace file.
//
// Args: none.
// Returns: one map per line.
// Errors: never fails; calls t.Fatal on read or parse errors.
func (e *env) traceRecords(t *testing.T) []map[string]any {
	t.Helper()
	b, err := os.ReadFile(e.tracePath)
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
			t.Fatal(err)
		}
		out = append(out, m)
	}
	return out
}

// ptr returns a pointer to a time.
//
// Args: s is an RFC3339 time.
// Returns: a pointer to the parsed instant.
// Errors: never fails.
func ptr(t *testing.T, s string) *time.Time {
	t.Helper()
	v, err := time.Parse(time.RFC3339, s)
	if err != nil {
		t.Fatal(err)
	}
	return &v
}

// covers: Fetcher.FetchOpenIssues
func TestFetchSeveralPages(t *testing.T) {
	fake := testutil.New(t)
	fake.PageSize = 2
	fake.SetRepo("o/r", series(5))
	e := newEnv(t, fake, "", guardToken)
	got, err := e.run(100, nil)
	if err != nil || !reflect.DeepEqual(got, []int{1, 2, 3, 4, 5}) {
		t.Fatalf("got %v, %v", got, err)
	}
	reqs := fake.Requests()
	if len(reqs) != 3 {
		t.Fatalf("requests: %d", len(reqs))
	}
	if reqs[0].URI != "/repos/o/r/issues?state=open&sort=created&direction=asc&per_page=100" {
		t.Fatalf("first URI %q", reqs[0].URI)
	}
	if !strings.HasSuffix(reqs[1].URI, "per_page=2&page=2") || !strings.HasSuffix(reqs[2].URI, "per_page=2&page=3") {
		t.Fatalf("next URIs come from Link: %q %q", reqs[1].URI, reqs[2].URI)
	}
	if reqs[0].Header.Get("Authorization") != "Bearer "+guardToken {
		t.Fatal("token not sent to the API origin")
	}
}

// covers: Fetcher.FetchOpenIssues
func TestFetchConvertsFields(t *testing.T) {
	fake := testutil.New(t)
	it := testutil.NewIssue(42, "2026-01-05T10:00:00Z")
	it["title"] = "Заголовок <b> & \"кавычки\""
	it["user"] = testutil.Item{"login": "octocat"}
	it["labels"] = []any{testutil.Item{"name": "bug", "color": "f00"}, testutil.Item{"name": "помощь"}, testutil.Item{"name": "bug"}}
	it["html_url"] = "https://github.com/o/r/issues/42"
	fake.SetRepo("o/r", []testutil.Item{it})
	got, err := newEnv(t, fake, "", "").f.FetchOpenIssues(context.Background(), repo, 10, nil)
	want := model.Issue{Number: 42, Title: "Заголовок <b> & \"кавычки\"", Author: "octocat", Labels: []string{"bug", "помощь", "bug"}, CreatedAt: "2026-01-05T10:00:00Z", URL: "https://github.com/o/r/issues/42"}
	if err != nil || len(got) != 1 || !reflect.DeepEqual(got[0], want) {
		t.Fatalf("got %+v, %v", got, err)
	}
}

// covers: Fetcher.FetchOpenIssues
func TestFetchRelativeLinks(t *testing.T) {
	fake := testutil.New(t)
	fake.PageSize, fake.RelativeLinks = 2, true
	fake.SetRepo("o/r", series(5))
	got, err := newEnv(t, fake, "", "").run(100, nil)
	if err != nil || !reflect.DeepEqual(got, []int{1, 2, 3, 4, 5}) {
		t.Fatalf("got %v, %v", got, err)
	}
}

// covers: Fetcher.FetchOpenIssues
func TestFetchLimitStopsRequestingPages(t *testing.T) {
	cases := []struct {
		limit    int
		want     []int
		requests int
	}{
		{1, []int{1}, 1},
		{2, []int{1, 2}, 1}, // exactly at the page boundary: page 2 must not be requested
		{3, []int{1, 2, 3}, 2},
		{4, []int{1, 2, 3, 4}, 2}, // boundary of page 2
		{5, []int{1, 2, 3, 4, 5}, 3},
		{6, []int{1, 2, 3, 4, 5}, 3}, // more than available
		{1000, []int{1, 2, 3, 4, 5}, 3},
	}
	for _, tc := range cases {
		fake := testutil.New(t)
		fake.PageSize = 2
		fake.SetRepo("o/r", series(5))
		got, err := newEnv(t, fake, "", "").run(tc.limit, nil)
		if err != nil || !reflect.DeepEqual(got, tc.want) || fake.Count() != tc.requests {
			t.Errorf("limit %d: got %v, %v, %d requests; want %v, %d", tc.limit, got, err, fake.Count(), tc.want, tc.requests)
		}
	}
}

// covers: Fetcher.FetchOpenIssues
func TestFetchLimitDoesNotValidateRestOfPage(t *testing.T) {
	fake := testutil.New(t)
	bad := testutil.NewIssue(2, "garbage")
	fake.SetRepo("o/r", []testutil.Item{testutil.NewIssue(1, day(1)), bad})
	got, err := newEnv(t, fake, "", "").run(1, nil)
	if err != nil || !reflect.DeepEqual(got, []int{1}) {
		t.Fatalf("got %v, %v", got, err)
	}
}

// covers: Fetcher.FetchOpenIssues
func TestFetchLimitBelowOne(t *testing.T) {
	fake := testutil.New(t)
	fake.SetRepo("o/r", series(3))
	got, err := newEnv(t, fake, "", "").f.FetchOpenIssues(context.Background(), repo, 0, nil)
	if err != nil || got == nil || len(got) != 0 || fake.Count() != 0 {
		t.Fatalf("got %v, %v, %d requests", got, err, fake.Count())
	}
}

// covers: Fetcher.FetchOpenIssues
func TestFetchDeduplicatesAcrossPages(t *testing.T) {
	fake := testutil.New(t)
	fake.PageSize = 2
	items := []testutil.Item{testutil.NewIssue(1, day(1)), testutil.NewIssue(2, day(2)), testutil.NewIssue(2, day(2)), testutil.NewIssue(3, day(3))}
	fake.SetRepo("o/r", items)
	got, err := newEnv(t, fake, "", "").run(100, nil)
	if err != nil || !reflect.DeepEqual(got, []int{1, 2, 3}) {
		t.Fatalf("got %v, %v", got, err)
	}
	// duplicates must not consume the limit
	fake2 := testutil.New(t)
	fake2.PageSize = 2
	fake2.SetRepo("o/r", items)
	got, err = newEnv(t, fake2, "", "").run(3, nil)
	if err != nil || !reflect.DeepEqual(got, []int{1, 2, 3}) {
		t.Fatalf("limit 3: got %v, %v", got, err)
	}
}

// covers: Fetcher.FetchOpenIssues
func TestFetchDuplicateKeepsFirstOccurrence(t *testing.T) {
	fake := testutil.New(t)
	a, b := testutil.NewIssue(5, day(1)), testutil.NewIssue(5, day(2))
	a["title"], b["title"] = "first", "second"
	fake.SetRepo("o/r", []testutil.Item{a, b})
	got, err := newEnv(t, fake, "", "").f.FetchOpenIssues(context.Background(), repo, 10, nil)
	if err != nil || len(got) != 1 || got[0].Title != "first" {
		t.Fatalf("got %+v, %v", got, err)
	}
}

// covers: Fetcher.FetchOpenIssues
func TestFetchSkipsPullRequests(t *testing.T) {
	fake := testutil.New(t)
	nullPR := testutil.NewIssue(2, day(2))
	nullPR["pull_request"] = nil
	brokenPR := testutil.Item{"number": "x", "pull_request": testutil.Item{}} // not validated at all
	fake.SetRepo("o/r", []testutil.Item{testutil.NewIssue(1, day(1)), nullPR, testutil.NewPR(3, day(3)), brokenPR, testutil.NewIssue(4, day(4))})
	got, err := newEnv(t, fake, "", "").run(100, nil)
	if err != nil || !reflect.DeepEqual(got, []int{1, 4}) {
		t.Fatalf("got %v, %v", got, err)
	}
	// pull requests do not consume the limit
	fake2 := testutil.New(t)
	fake2.SetRepo("o/r", []testutil.Item{testutil.NewPR(1, day(1)), testutil.NewIssue(2, day(2)), testutil.NewPR(3, day(3)), testutil.NewIssue(4, day(4))})
	got, err = newEnv(t, fake2, "", "").run(2, nil)
	if err != nil || !reflect.DeepEqual(got, []int{2, 4}) {
		t.Fatalf("limit with PRs: %v, %v", got, err)
	}
}

// covers: Fetcher.FetchOpenIssues
func TestFetchSinceBoundaries(t *testing.T) {
	since := ptr(t, "2026-03-01T00:00:00Z")
	cases := []struct {
		name    string
		created string
		want    bool
	}{
		{"exact midnight", "2026-03-01T00:00:00Z", true},
		{"one second before", "2026-02-28T23:59:59Z", false},
		{"one second after", "2026-03-01T00:00:01Z", true},
		{"+03:00 equals midnight UTC", "2026-03-01T03:00:00+03:00", true},
		{"+03:00 one second before midnight UTC", "2026-03-01T02:59:59+03:00", false},
		{"+03:00 local midnight is earlier", "2026-03-01T00:00:00+03:00", false},
		{"-05:00 same date later in UTC", "2026-02-28T19:00:00-05:00", true},
		{"-05:00 one second before", "2026-02-28T18:59:59-05:00", false},
		{"fraction", "2026-03-01T00:00:00.5Z", true},
		{"far future", "2030-01-01T00:00:00Z", true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			fake := testutil.New(t)
			fake.SetRepo("o/r", []testutil.Item{testutil.NewIssue(7, tc.created)})
			got, err := newEnv(t, fake, "", "").f.FetchOpenIssues(context.Background(), repo, 10, since)
			if err != nil || (len(got) == 1) != tc.want {
				t.Fatalf("got %v, %v; want included=%v", got, err, tc.want)
			}
			if tc.want && got[0].CreatedAt != tc.created {
				t.Fatalf("created_at changed: %q", got[0].CreatedAt)
			}
		})
	}
}

// covers: Fetcher.FetchOpenIssues
func TestFetchSinceFilteredIssuesDoNotConsumeLimit(t *testing.T) {
	fake := testutil.New(t)
	fake.SetRepo("o/r", series(6))
	got, err := newEnv(t, fake, "", "").run(2, ptr(t, "2026-01-04T00:00:00Z"))
	if err != nil || !reflect.DeepEqual(got, []int{4, 5}) {
		t.Fatalf("got %v, %v", got, err)
	}
}

// covers: Fetcher.FetchOpenIssues
func TestFetchSortsResult(t *testing.T) {
	fake := testutil.New(t)
	fake.SetRepo("o/r", []testutil.Item{
		testutil.NewIssue(3, "2026-01-03T00:00:00Z"),
		testutil.NewIssue(9, "2026-01-01T00:00:00Z"),
		testutil.NewIssue(2, "2026-01-01T00:00:00Z"),      // same instant as 9: number decides
		testutil.NewIssue(8, "2026-01-01T03:00:00+03:00"), // 00:00Z as well
		testutil.NewIssue(1, "2026-01-02T00:30:00+03:00"), // 2026-01-01T21:30Z
		testutil.NewIssue(4, "2026-01-01T12:00:00Z"),
	})
	got, err := newEnv(t, fake, "", "").run(100, nil)
	// instants: 2,8,9 at 01-01T00:00Z; 4 at 12:00Z; 1 at 21:30Z; 3 at 01-03
	if err != nil || !reflect.DeepEqual(got, []int{2, 8, 9, 4, 1, 3}) {
		t.Fatalf("got %v, %v", got, err)
	}
}

// covers: Fetcher.FetchOpenIssues
func TestFetchInvalidResponses(t *testing.T) {
	good := func() testutil.Item { return testutil.NewIssue(1, day(1)) }
	mutate := func(f func(it testutil.Item)) []testutil.Item {
		it := good()
		f(it)
		return []testutil.Item{it}
	}
	bodies := map[string]string{
		"object instead of array": `{"message":"hello"}`,
		"null":                    `null`,
		"string":                  `"x"`,
		"broken json":             `[{"number":1,`,
		"empty body":              ``,
		"html":                    `<html></html>`,
		"element is number":       `[1]`,
		"element is null":         `[null]`,
		"element is array":        `[[]]`,
		"trailing garbage":        `[] x`,
	}
	for name, body := range bodies {
		t.Run(name, func(t *testing.T) {
			fake := testutil.New(t)
			fake.SetReply("o/r", testutil.Reply{Body: body})
			_, err := newEnv(t, fake, "", "").run(10, nil)
			if !errors.Is(err, ErrInvalidResponse) {
				t.Fatalf("err = %v", err)
			}
		})
	}
	items := map[string][]testutil.Item{
		"missing number":     mutate(func(it testutil.Item) { delete(it, "number") }),
		"null number":        mutate(func(it testutil.Item) { it["number"] = nil }),
		"string number":      mutate(func(it testutil.Item) { it["number"] = "1" }),
		"fractional number":  mutate(func(it testutil.Item) { it["number"] = 1.5 }),
		"negative number":    mutate(func(it testutil.Item) { it["number"] = -1 }),
		"huge number":        mutate(func(it testutil.Item) { it["number"] = 1e30 }),
		"missing title":      mutate(func(it testutil.Item) { delete(it, "title") }),
		"null title":         mutate(func(it testutil.Item) { it["title"] = nil }),
		"numeric title":      mutate(func(it testutil.Item) { it["title"] = 5 }),
		"missing html_url":   mutate(func(it testutil.Item) { delete(it, "html_url") }),
		"missing created_at": mutate(func(it testutil.Item) { delete(it, "created_at") }),
		"bad created_at":     mutate(func(it testutil.Item) { it["created_at"] = "2026-01-01" }),
		"empty created_at":   mutate(func(it testutil.Item) { it["created_at"] = "" }),
		"numeric created_at": mutate(func(it testutil.Item) { it["created_at"] = 20260101 }),
		"user is string":     mutate(func(it testutil.Item) { it["user"] = "octocat" }),
		"login is number":    mutate(func(it testutil.Item) { it["user"] = testutil.Item{"login": 5} }),
		"labels is object":   mutate(func(it testutil.Item) { it["labels"] = testutil.Item{} }),
		"label without name": mutate(func(it testutil.Item) { it["labels"] = []any{testutil.Item{"color": "f00"}} }),
		"label is string":    mutate(func(it testutil.Item) { it["labels"] = []any{"bug"} }),
		"bad item after good": {good(), func() testutil.Item {
			it := testutil.NewIssue(2, day(2))
			delete(it, "number")
			return it
		}()},
	}
	for name, list := range items {
		t.Run(name, func(t *testing.T) {
			fake := testutil.New(t)
			fake.SetRepo("o/r", list)
			if _, err := newEnv(t, fake, "", "").run(10, nil); !errors.Is(err, ErrInvalidResponse) {
				t.Fatalf("err = %v", err)
			}
		})
	}
}

// covers: Fetcher.FetchOpenIssues
func TestFetchOptionalFields(t *testing.T) {
	fake := testutil.New(t)
	noUser := testutil.NewIssue(1, day(1))
	delete(noUser, "user")
	nullUser := testutil.NewIssue(2, day(2))
	nullUser["user"] = nil
	noLogin := testutil.NewIssue(3, day(3))
	noLogin["user"] = testutil.Item{}
	nullLabels := testutil.NewIssue(4, day(4))
	nullLabels["labels"] = nil
	noLabels := testutil.NewIssue(5, day(5))
	delete(noLabels, "labels")
	emptyTitle := testutil.NewIssue(6, day(6))
	emptyTitle["title"] = ""
	fake.SetRepo("o/r", []testutil.Item{noUser, nullUser, noLogin, nullLabels, noLabels, emptyTitle})
	got, err := newEnv(t, fake, "", "").f.FetchOpenIssues(context.Background(), repo, 10, nil)
	if err != nil || len(got) != 6 {
		t.Fatalf("got %v, %v", got, err)
	}
	for i, is := range got {
		if i < 3 && is.Author != "" {
			t.Errorf("issue %d author %q", is.Number, is.Author)
		}
		if is.Labels == nil || len(is.Labels) != 0 {
			t.Errorf("issue %d labels %#v", is.Number, is.Labels)
		}
	}
	if got[5].Title != "" {
		t.Errorf("empty title changed: %q", got[5].Title)
	}
}

// covers: Fetcher.FetchOpenIssues
func TestFetchLinkCycleAndPageCeiling(t *testing.T) {
	fake := testutil.New(t)
	fake.PageSize, fake.LinkCycle = 2, true
	fake.SetRepo("o/r", series(5))
	_, err := newEnv(t, fake, "", "").run(100, nil)
	if !errors.Is(err, ErrInvalidResponse) {
		t.Fatalf("cycle: %v", err)
	}
	if fake.Count() > 3 {
		t.Fatalf("cycle detection took %d requests", fake.Count())
	}
	fake2 := testutil.New(t)
	fake2.PageSize = 1
	fake2.SetRepo("o/r", series(6))
	e := newEnv(t, fake2, "", "")
	e.f.MaxPages = 3
	if _, err := e.run(100, nil); !errors.Is(err, ErrInvalidResponse) || fake2.Count() != 3 {
		t.Fatalf("ceiling: %v after %d requests", err, fake2.Count())
	}
	// exactly MaxPages pages is fine
	fake3 := testutil.New(t)
	fake3.PageSize = 1
	fake3.SetRepo("o/r", series(3))
	e3 := newEnv(t, fake3, "", "")
	e3.f.MaxPages = 3
	if got, err := e3.run(100, nil); err != nil || len(got) != 3 {
		t.Fatalf("at the ceiling: %v, %v", got, err)
	}
}

// covers: Fetcher.FetchOpenIssues
func TestFetchPropagatesTransportErrors(t *testing.T) {
	fake := testutil.New(t)
	e := newEnv(t, fake, "", "")
	_, err := e.run(10, nil)
	if !errors.Is(err, httpx.ErrNotFound) {
		t.Fatalf("404: %v", err)
	}
	fake.SetReply("o/r", testutil.Reply{Status: 401})
	if _, err := e.run(10, nil); !errors.Is(err, httpx.ErrUnauthorized) {
		t.Fatalf("401: %v", err)
	}
	fake.SetReply("o/r", testutil.Reply{Status: 403, Body: "rate limit", Header: map[string]string{"Retry-After": "1"}})
	if _, err := e.run(10, nil); !errors.Is(err, httpx.ErrRateLimited) {
		t.Fatalf("403 limit: %v", err)
	}
}

// covers: Fetcher.FetchOpenIssues
func TestFetchFailureOnLaterPageFailsRepository(t *testing.T) {
	fake := testutil.New(t)
	fake.PageSize = 2
	fake.SetRepo("o/r", series(5))
	e := newEnv(t, fake, "", "")
	e.f.Client = &failingOnSecond{inner: e.f.Client}
	if got, err := e.f.FetchOpenIssues(context.Background(), repo, 100, nil); err == nil || got != nil {
		t.Fatalf("got %v, %v", got, err)
	}
}

// failingOnSecond delegates the first Get and fails the second one.
type failingOnSecond struct {
	inner Getter
	n     int
}

// Get delegates the first call and fails every later one.
//
// Args: ctx, url, inm as in Getter.
// Returns: the inner response for the first call.
// Errors: httpx.ErrRequestFailed from the second call on.
func (g *failingOnSecond) Get(ctx context.Context, url, inm string) (*httpx.Response, error) {
	g.n++
	if g.n > 1 {
		return nil, &httpx.RequestError{Kind: httpx.ErrRequestFailed}
	}
	return g.inner.Get(ctx, url, inm)
}

// covers: Fetcher.FetchOpenIssues
func TestFetchCacheSecondRunUsesConditionalRequests(t *testing.T) {
	fake := testutil.New(t)
	fake.PageSize = 2
	fake.SetRepo("o/r", series(5))
	dir := filepath.Join(t.TempDir(), "cache")

	first := newEnv(t, fake, dir, guardToken)
	got1, err := first.f.FetchOpenIssues(context.Background(), repo, 100, nil)
	if err != nil {
		t.Fatal(err)
	}
	for i, r := range fake.Requests() {
		if _, present := r.Header["If-None-Match"]; present {
			t.Fatalf("first run, request %d sent If-None-Match", i)
		}
	}
	for _, rec := range first.traceRecords(t) {
		if rec["from_cache"] != false || rec["status"] != 200.0 {
			t.Fatalf("first run trace: %v", rec)
		}
	}
	files, _ := filepath.Glob(filepath.Join(dir, "*.json"))
	if len(files) != 3 {
		t.Fatalf("cache files: %v", files)
	}

	fake.Reset()
	second := newEnv(t, fake, dir, guardToken)
	got2, err := second.f.FetchOpenIssues(context.Background(), repo, 100, nil)
	if err != nil || !reflect.DeepEqual(got1, got2) {
		t.Fatalf("second run: %v, %v", got2, err)
	}
	reqs := fake.Requests()
	if len(reqs) != 3 {
		t.Fatalf("second run requests: %d", len(reqs))
	}
	for i, r := range reqs {
		if r.Header.Get("If-None-Match") == "" {
			t.Errorf("second run, request %d lacks If-None-Match", i)
		}
	}
	recs := second.traceRecords(t)
	if len(recs) != 3 {
		t.Fatalf("trace: %v", recs)
	}
	for _, rec := range recs {
		if rec["status"] != 304.0 || rec["from_cache"] != true {
			t.Fatalf("second run trace: %v", rec)
		}
	}
	for _, f := range files {
		b, _ := os.ReadFile(f)
		if strings.Contains(string(b), guardToken) || strings.Contains(strings.ToLower(string(b)), "authorization") {
			t.Fatalf("secret in cache file %s", f)
		}
	}
}

// covers: Fetcher.FetchOpenIssues
func TestFetchCacheLinkIsStoredAndUsedOn304(t *testing.T) {
	fake := testutil.New(t) // LinkOn304 is false: only the cache knows where the next page is
	fake.PageSize = 2
	fake.RelativeLinks = true
	fake.SetRepo("o/r", series(5))
	dir := t.TempDir()
	if _, err := newEnv(t, fake, dir, "").run(100, nil); err != nil {
		t.Fatal(err)
	}
	store := &cache.Store{Dir: dir}
	e, ok := store.Get(FirstURL(mustParse(t, fake.URL), repo))
	if !ok || !strings.Contains(e.Link, `rel="next"`) {
		t.Fatalf("link not stored: %+v", e)
	}
	fake.Reset()
	got, err := newEnv(t, fake, dir, "").run(100, nil)
	if err != nil || !reflect.DeepEqual(got, []int{1, 2, 3, 4, 5}) || fake.Count() != 3 {
		t.Fatalf("got %v, %v, %d requests", got, err, fake.Count())
	}
}

// mustParse parses a URL.
//
// Args: s is a URL string.
// Returns: the parsed URL.
// Errors: never fails; calls t.Fatal on a parse error.
func mustParse(t *testing.T, s string) *url.URL {
	t.Helper()
	u, err := url.Parse(s)
	if err != nil {
		t.Fatal(err)
	}
	return u
}

// covers: Fetcher.FetchOpenIssues
func TestFetchCacheWeakETagRoundTrip(t *testing.T) {
	fake := testutil.New(t)
	fake.WeakETag = true
	fake.SetRepo("o/r", series(2))
	dir := t.TempDir()
	if _, err := newEnv(t, fake, dir, "").run(10, nil); err != nil {
		t.Fatal(err)
	}
	e, ok := (&cache.Store{Dir: dir}).Get(FirstURL(mustParse(t, fake.URL), repo))
	if !ok || !strings.HasPrefix(e.ETag, `W/"`) {
		t.Fatalf("entry %+v", e)
	}
	fake.Reset()
	if _, err := newEnv(t, fake, dir, "").run(10, nil); err != nil {
		t.Fatal(err)
	}
	if got := fake.Requests()[0].Header.Get("If-None-Match"); got != e.ETag {
		t.Fatalf("If-None-Match %q, want %q", got, e.ETag)
	}
}

// covers: Fetcher.FetchOpenIssues
func TestFetchCorruptCacheFileIsMiss(t *testing.T) {
	fake := testutil.New(t)
	fake.SetRepo("o/r", series(2))
	dir := t.TempDir()
	if _, err := newEnv(t, fake, dir, "").run(10, nil); err != nil {
		t.Fatal(err)
	}
	files, _ := filepath.Glob(filepath.Join(dir, "*.json"))
	for _, f := range files {
		if err := os.WriteFile(f, []byte("{broken"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	fake.Reset()
	got, err := newEnv(t, fake, dir, "").run(10, nil)
	if err != nil || !reflect.DeepEqual(got, []int{1, 2}) {
		t.Fatalf("got %v, %v", got, err)
	}
	if _, present := fake.Requests()[0].Header["If-None-Match"]; present {
		t.Fatal("corrupt entry must not produce If-None-Match")
	}
	if e, ok := (&cache.Store{Dir: dir}).Get(FirstURL(mustParse(t, fake.URL), repo)); !ok || e.Body == "" {
		t.Fatal("corrupt entry should be replaced by a fresh one")
	}
}

// covers: Fetcher.FetchOpenIssues
func TestFetchResponseWithoutETagIsNotCached(t *testing.T) {
	fake := testutil.New(t)
	fake.DisableETag = true
	fake.SetRepo("o/r", series(2))
	dir := filepath.Join(t.TempDir(), "cache")
	if _, err := newEnv(t, fake, dir, "").run(10, nil); err != nil {
		t.Fatal(err)
	}
	if files, _ := filepath.Glob(filepath.Join(dir, "*")); len(files) != 0 {
		t.Fatalf("cache files: %v", files)
	}
	fake.Reset()
	if _, err := newEnv(t, fake, dir, "").run(10, nil); err != nil {
		t.Fatal(err)
	}
	if _, present := fake.Requests()[0].Header["If-None-Match"]; present {
		t.Fatal("unexpected If-None-Match")
	}
}

// covers: Fetcher.FetchOpenIssues
func TestFetchInvalidBodyIsNotCached(t *testing.T) {
	fake := testutil.New(t)
	fake.SetReply("o/r", testutil.Reply{Body: `{"not":"array"}`, Header: map[string]string{"ETag": `"x"`}})
	dir := filepath.Join(t.TempDir(), "cache")
	if _, err := newEnv(t, fake, dir, "").run(10, nil); !errors.Is(err, ErrInvalidResponse) {
		t.Fatalf("err = %v", err)
	}
	if files, _ := filepath.Glob(filepath.Join(dir, "*")); len(files) != 0 {
		t.Fatalf("cache files: %v", files)
	}
}

// covers: Fetcher.FetchOpenIssues
func TestFetch304WithoutCacheEntryIsInvalid(t *testing.T) {
	fake := testutil.New(t)
	fake.SetReply("o/r", testutil.Reply{Status: 304})
	for _, dir := range []string{"", t.TempDir()} {
		_, err := newEnv(t, fake, dir, "").run(10, nil)
		if !errors.Is(err, ErrInvalidResponse) {
			t.Fatalf("dir %q: err = %v", dir, err)
		}
	}
}

// covers: Fetcher.FetchOpenIssues
func TestFetchCacheWriteFailureIsWarning(t *testing.T) {
	fake := testutil.New(t)
	fake.SetRepo("o/r", series(2))
	file := filepath.Join(t.TempDir(), "afile")
	_ = os.WriteFile(file, []byte("x"), 0o600)
	e := newEnv(t, fake, filepath.Join(file, "cache"), guardToken)
	got, err := e.run(10, nil)
	if err != nil || !reflect.DeepEqual(got, []int{1, 2}) {
		t.Fatalf("got %v, %v", got, err)
	}
	if !strings.Contains(e.warn.String(), "предупреждение") || strings.Contains(e.warn.String(), guardToken) {
		t.Fatalf("warning: %q", e.warn.String())
	}
}

// stubGetter returns canned responses.
type stubGetter struct {
	resp *httpx.Response
	err  error
	inm  []string
}

// Get records the If-None-Match value and returns the canned result.
//
// Args: ctx and url are ignored; inm is recorded.
// Returns: the configured response and error.
// Errors: the configured error.
func (s *stubGetter) Get(ctx context.Context, url, inm string) (*httpx.Response, error) {
	s.inm = append(s.inm, inm)
	return s.resp, s.err
}

// covers: Fetcher.page
func TestPageLinkFrom304TakesPrecedence(t *testing.T) {
	dir := t.TempDir()
	store := &cache.Store{Dir: dir}
	_ = store.Put(cache.Entry{URL: "http://h/p1", ETag: `"e"`, Link: `<http://h/old>; rel="next"`, Body: `[]`})
	stub := &stubGetter{resp: &httpx.Response{Status: 304, Header: http.Header{"Link": {`<http://h/new>; rel="next"`}}}}
	f := &Fetcher{Client: stub, Cache: store}
	body, link, err := f.page(context.Background(), "http://h/p1")
	if err != nil || string(body) != "[]" || !reflect.DeepEqual(link, []string{`<http://h/new>; rel="next"`}) || stub.inm[0] != `"e"` {
		t.Fatalf("got %q, %v, %v, inm %v", body, link, err, stub.inm)
	}
	stub.resp = &httpx.Response{Status: 304, Header: http.Header{}}
	_, link, err = f.page(context.Background(), "http://h/p1")
	if err != nil || !reflect.DeepEqual(link, []string{`<http://h/old>; rel="next"`}) {
		t.Fatalf("cached link: %v, %v", link, err)
	}
}

// covers: Fetcher.page
func TestPageErrorsAndStatuses(t *testing.T) {
	boom := errors.New("boom")
	f := &Fetcher{Client: &stubGetter{err: boom}}
	if _, _, err := f.page(context.Background(), "http://h/p"); !errors.Is(err, boom) {
		t.Fatalf("err = %v", err)
	}
	f = &Fetcher{Client: &stubGetter{resp: &httpx.Response{Status: 202, Header: http.Header{}}}}
	if _, _, err := f.page(context.Background(), "http://h/p"); !errors.Is(err, ErrInvalidResponse) {
		t.Fatalf("202: %v", err)
	}
	f = &Fetcher{Client: &stubGetter{resp: &httpx.Response{Status: 304, Header: http.Header{}}}}
	if _, _, err := f.page(context.Background(), "http://h/p"); !errors.Is(err, ErrInvalidResponse) {
		t.Fatalf("304 without cache: %v", err)
	}
}

// covers: ErrorCode
func TestErrorCode(t *testing.T) {
	wrap := func(kind error) error { return &httpx.RequestError{Kind: kind, Detail: "d"} }
	cases := []struct {
		name         string
		err          error
		code         string
		unauthorized bool
	}{
		{"nil", nil, "", false},
		{"unauthorized", wrap(httpx.ErrUnauthorized), "", true},
		{"wrapped unauthorized", errors.Join(errors.New("x"), wrap(httpx.ErrUnauthorized)), "", true},
		{"not found", wrap(httpx.ErrNotFound), "not_found", false},
		{"rate limited", wrap(httpx.ErrRateLimited), "rate_limited", false},
		{"request failed", wrap(httpx.ErrRequestFailed), "request_failed", false},
		{"invalid", invalidf("x"), "invalid_response", false},
		{"bare invalid", ErrInvalidResponse, "invalid_response", false},
		{"unknown error", errors.New("???"), "request_failed", false},
		{"context canceled", context.Canceled, "request_failed", false},
	}
	for _, tc := range cases {
		code, unauthorized := ErrorCode(tc.err)
		if code != tc.code || unauthorized != tc.unauthorized {
			t.Errorf("%s: got (%q, %v), want (%q, %v)", tc.name, code, unauthorized, tc.code, tc.unauthorized)
		}
	}
}

// covers: invalidf
func TestInvalidf(t *testing.T) {
	err := invalidf("поле %s", "x")
	if !errors.Is(err, ErrInvalidResponse) || !strings.HasSuffix(err.Error(), "поле x") {
		t.Fatalf("got %v", err)
	}
}

// covers: FirstURL
func TestFirstURL(t *testing.T) {
	cases := []struct{ base, want string }{
		{"https://api.github.com", "https://api.github.com/repos/cli/cli/issues?state=open&sort=created&direction=asc&per_page=100"},
		{"https://ghe.example.com/api/v3", "https://ghe.example.com/api/v3/repos/cli/cli/issues?state=open&sort=created&direction=asc&per_page=100"},
		{"http://127.0.0.1:8080", "http://127.0.0.1:8080/repos/cli/cli/issues?state=open&sort=created&direction=asc&per_page=100"},
	}
	for _, tc := range cases {
		if got := FirstURL(mustParse(t, tc.base), args.RepoRef{Owner: "cli", Name: "cli"}); got != tc.want {
			t.Errorf("got %q\nwant %q", got, tc.want)
		}
	}
	if got := FirstURL(mustParse(t, "http://h"), args.RepoRef{Owner: "a.b-c", Name: "_x"}); !strings.Contains(got, "/repos/a.b-c/_x/issues?") {
		t.Errorf("special characters: %q", got)
	}
	if got := FirstURL(mustParse(t, "http://h"), args.RepoRef{Owner: "a b", Name: "c/d"}); !strings.Contains(got, "/repos/a%20b/c%2Fd/issues?") {
		t.Errorf("segments must be escaped: %q", got)
	}
}

// covers: Fetcher.warn
func TestWarnDestination(t *testing.T) {
	if (&Fetcher{}).warn() != os.Stderr {
		t.Fatal("default must be stderr")
	}
	var b bytes.Buffer
	if (&Fetcher{Warn: &b}).warn() != &b {
		t.Fatal("configured writer ignored")
	}
}

// covers: cacheable
func TestCacheable(t *testing.T) {
	mk := func(etag, body string) *httpx.Response {
		h := http.Header{}
		if etag != "" {
			h.Set("ETag", etag)
		}
		return &httpx.Response{Status: 200, Header: h, Body: []byte(body)}
	}
	cases := []struct {
		resp *httpx.Response
		want bool
	}{
		{mk(`"e"`, `[]`), true},
		{mk(`W/"e"`, ` [{"a":1}] `), true},
		{mk("", `[]`), false},
		{mk(`"e"`, `{}`), false},
		{mk(`"e"`, `null`), false},
		{mk(`"e"`, ``), false},
		{mk(`"e"`, `[1,`), false},
		{mk(`"e"`, "[\"\xff\"]"), false},
	}
	for i, tc := range cases {
		if got := cacheable(tc.resp); got != tc.want {
			t.Errorf("case %d: %v", i, got)
		}
	}
}

// covers: parseElement
func TestParseElement(t *testing.T) {
	since := ptr(t, "2026-01-02T00:00:00Z")
	ok := `{"number":3,"title":"t","html_url":"u","created_at":"2026-01-02T00:00:00Z","user":{"login":"l"},"labels":[{"name":"a"}]}`
	is, skip, err := parseElement(json.RawMessage(ok), since)
	if err != nil || skip || is.Number != 3 || is.Author != "l" || !reflect.DeepEqual(is.Labels, []string{"a"}) {
		t.Fatalf("ok: %+v %v %v", is, skip, err)
	}
	old := strings.Replace(ok, "2026-01-02", "2026-01-01", 1)
	if _, skip, err := parseElement(json.RawMessage(old), since); err != nil || !skip {
		t.Fatalf("old: %v %v", skip, err)
	}
	// order: PR check first, then validation, then since
	if _, skip, err := parseElement(json.RawMessage(`{"pull_request":null}`), since); err != nil || !skip {
		t.Fatalf("PR: %v %v", skip, err)
	}
	if _, _, err := parseElement(json.RawMessage(`{"number":1,"title":"t","html_url":"u","created_at":"bad"}`), since); !errors.Is(err, ErrInvalidResponse) {
		t.Fatalf("invalid beats since: %v", err)
	}
	if _, _, err := parseElement(json.RawMessage(`[]`), nil); !errors.Is(err, ErrInvalidResponse) {
		t.Fatalf("array: %v", err)
	}
}

// covers: intField
func TestIntField(t *testing.T) {
	good := map[string]int{`0`: 0, `7`: 7, ` 12 `: 12}
	for in, want := range good {
		if got, err := intField(map[string]json.RawMessage{"n": json.RawMessage(in)}, "n"); err != nil || got != want {
			t.Errorf("%q: %d, %v", in, got, err)
		}
	}
	for _, in := range []string{`null`, `"1"`, `1.5`, `1e2`, `-1`, `true`, `[]`, `99999999999999999999`, ``} {
		if _, err := intField(map[string]json.RawMessage{"n": json.RawMessage(in)}, "n"); !errors.Is(err, ErrInvalidResponse) {
			t.Errorf("%q: err = %v", in, err)
		}
	}
	if _, err := intField(map[string]json.RawMessage{}, "n"); !errors.Is(err, ErrInvalidResponse) {
		t.Errorf("absent: %v", err)
	}
}

// covers: stringField
func TestStringField(t *testing.T) {
	if got, err := stringField(map[string]json.RawMessage{"s": json.RawMessage(`"aé"`)}, "s"); err != nil || got != "aé" {
		t.Fatalf("got %q, %v", got, err)
	}
	if got, err := stringField(map[string]json.RawMessage{"s": json.RawMessage(`""`)}, "s"); err != nil || got != "" {
		t.Fatalf("empty: %q, %v", got, err)
	}
	for _, in := range []string{`null`, `1`, `[]`, `{}`, `true`} {
		if _, err := stringField(map[string]json.RawMessage{"s": json.RawMessage(in)}, "s"); !errors.Is(err, ErrInvalidResponse) {
			t.Errorf("%q: %v", in, err)
		}
	}
	if _, err := stringField(nil, "s"); !errors.Is(err, ErrInvalidResponse) {
		t.Errorf("absent: %v", err)
	}
}

// covers: authorOf
func TestAuthorOf(t *testing.T) {
	good := map[string]string{``: "", `null`: "", ` null `: "", `{}`: "", `{"login":null}`: "", `{"login":"x"}`: "x", `{"login":"x","id":1}`: "x"}
	for in, want := range good {
		if got, err := authorOf(json.RawMessage(in)); err != nil || got != want {
			t.Errorf("%q: %q, %v", in, got, err)
		}
	}
	for _, in := range []string{`"x"`, `1`, `[]`, `{"login":1}`} {
		if _, err := authorOf(json.RawMessage(in)); !errors.Is(err, ErrInvalidResponse) {
			t.Errorf("%q: %v", in, err)
		}
	}
}

// covers: labelsOf
func TestLabelsOf(t *testing.T) {
	good := map[string][]string{``: {}, `null`: {}, `[]`: {}, `[{"name":"a"},{"name":"b"}]`: {"a", "b"}, `[{"name":""}]`: {""}}
	for in, want := range good {
		got, err := labelsOf(json.RawMessage(in))
		if err != nil || got == nil || !reflect.DeepEqual(got, want) {
			t.Errorf("%q: %#v, %v", in, got, err)
		}
	}
	for _, in := range []string{`{}`, `"a"`, `["a"]`, `[{}]`, `[null]`, `[{"name":null}]`, `[{"name":1}]`} {
		if _, err := labelsOf(json.RawMessage(in)); !errors.Is(err, ErrInvalidResponse) {
			t.Errorf("%q: %v", in, err)
		}
	}
}

// covers: sortIssues
func TestSortIssues(t *testing.T) {
	mk := func(n int, c string) model.Issue { return model.Issue{Number: n, CreatedAt: c} }
	in := []model.Issue{mk(5, "2026-01-02T00:00:00Z"), mk(3, "2026-01-01T03:00:00+03:00"), mk(1, "2026-01-01T00:00:00Z"), mk(4, "2025-12-31T23:59:59Z")}
	got := numbers(sortIssues(in))
	if !reflect.DeepEqual(got, []int{4, 1, 3, 5}) {
		t.Fatalf("got %v", got)
	}
	if len(sortIssues(nil)) != 0 {
		t.Fatal("nil input")
	}
}
