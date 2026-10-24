package app

import (
	"bytes"
	"context"
	"errors"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ghreport/internal/args"
	"ghreport/internal/httpx"
	"ghreport/internal/issues"
	"ghreport/internal/model"
)

// scriptedGetter answers each call from a list of errors (nil means an empty successful page).
type scriptedGetter struct {
	errs  []error
	calls int
}

// Get returns the next scripted result.
//
// Args: ctx, url and inm are ignored.
// Returns: an empty JSON array response when the scripted error is nil.
// Errors: the scripted error.
func (g *scriptedGetter) Get(ctx context.Context, url, inm string) (*httpx.Response, error) {
	err := g.errs[g.calls]
	g.calls++
	if err != nil {
		return nil, err
	}
	return &httpx.Response{Status: 200, Body: []byte("[]")}, nil
}

// kind builds a typed transport error.
//
// Args: k is a httpx sentinel.
// Returns: a *httpx.RequestError of that kind.
// Errors: never fails.
func kind(k error) error { return &httpx.RequestError{Kind: k, Detail: "d"} }

// failingWriter fails every write.
type failingWriter struct{}

// Write always fails.
//
// Args: p is ignored.
// Returns: (0, error).
// Errors: always.
func (failingWriter) Write(p []byte) (int, error) { return 0, errors.New("closed") }

// covers: Scrub
func TestScrub(t *testing.T) {
	cases := []struct{ token, in, want string }{
		{"sek", "a sek b sek", "a *** b ***"},
		{"sek", "nothing", "nothing"},
		{"", "keep sek", "keep sek"},
		{"sek", "", ""},
		{"sek", "sek", "***"},
		{"a.c", "abc a.c", "abc ***"},
		{"токен", "мой токен тут", "мой *** тут"},
	}
	for _, tc := range cases {
		if got := Scrub(tc.token, tc.in); got != tc.want {
			t.Errorf("Scrub(%q, %q) = %q, want %q", tc.token, tc.in, got, tc.want)
		}
	}
}

// covers: scrubWriter.Write
func TestScrubWriterWrite(t *testing.T) {
	var buf bytes.Buffer
	w := &scrubWriter{w: &buf, token: "sek"}
	n, err := w.Write([]byte("value sek end\n"))
	if err != nil || n != len("value sek end\n") || buf.String() != "value *** end\n" {
		t.Fatalf("got %d, %v, %q", n, err, buf.String())
	}
	if _, err := (&scrubWriter{w: failingWriter{}, token: "x"}).Write([]byte("a")); err == nil {
		t.Fatal("underlying error lost")
	}
}

// covers: prepareCache
func TestPrepareCache(t *testing.T) {
	var warn bytes.Buffer
	dir := filepath.Join(t.TempDir(), "a", "b")
	s := prepareCache(dir, &warn)
	if s == nil || s.Dir != dir || warn.Len() != 0 {
		t.Fatalf("got %v, warning %q", s, warn.String())
	}
	if fi, err := os.Stat(dir); err != nil || !fi.IsDir() {
		t.Fatalf("directory not created: %v", err)
	}
	file := filepath.Join(t.TempDir(), "f")
	_ = os.WriteFile(file, nil, 0o600)
	if s := prepareCache(filepath.Join(file, "c"), &warn); s != nil || !strings.Contains(warn.String(), "предупреждение") {
		t.Fatalf("got %v, warning %q", s, warn.String())
	}
}

// covers: collect
func TestCollect(t *testing.T) {
	base, _ := url.Parse("http://h")
	repos := []args.RepoRef{{Owner: "a", Name: "a"}, {Owner: "b", Name: "b"}, {Owner: "c", Name: "c"}}
	run := func(ctx context.Context, errs ...error) ([]model.RepoReport, int, *scriptedGetter, string) {
		g := &scriptedGetter{errs: errs}
		var warn bytes.Buffer
		f := &issues.Fetcher{Client: g, Base: base}
		reports, status := collect(ctx, f, args.Options{Repos: repos, Limit: 10}, &warn)
		return reports, status, g, warn.String()
	}
	reports, status, g, warn := run(context.Background(), nil, kind(httpx.ErrNotFound), kind(httpx.ErrRateLimited))
	if status != statusDone || g.calls != 3 || len(reports) != 3 {
		t.Fatalf("status %d calls %d reports %v", status, g.calls, reports)
	}
	if reports[0].Error != nil || reports[0].Issues == nil || *reports[1].Error != "not_found" || *reports[2].Error != "rate_limited" {
		t.Fatalf("reports %+v", reports)
	}
	if strings.Contains(warn, "ghreport: b/b: ghreport:") || !strings.Contains(warn, "ghreport: b/b: ") {
		t.Fatalf("diagnostic format: %q", warn)
	}
	if reports[1].Count != 0 || reports[1].Issues == nil || !strings.Contains(warn, "b/b") || !strings.Contains(warn, "c/c") {
		t.Fatalf("failed report/diagnostics: %+v %q", reports[1], warn)
	}
	reports, status, g, _ = run(context.Background(), nil, kind(httpx.ErrUnauthorized), nil)
	if status != statusUnauthorized || g.calls != 2 || reports != nil {
		t.Fatalf("401: status %d calls %d", status, g.calls)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	reports, status, g, _ = run(ctx, context.Canceled, nil, nil)
	if status != statusInterrupted || g.calls != 1 || reports != nil {
		t.Fatalf("cancel: status %d calls %d", status, g.calls)
	}
	_, status, _, _ = run(context.Background(), kind(httpx.ErrRequestFailed), errors.New("odd"), kind(httpx.ErrNotFound))
	if status != statusDone {
		t.Fatalf("status %d", status)
	}
}

// covers: finish
func TestFinish(t *testing.T) {
	ok, bad := model.NewRepoReport("a/a"), model.NewFailedRepoReport("b/b", model.ErrNotFound)
	cases := []struct {
		name     string
		repos    []model.RepoReport
		out      string // "" means stdout
		code     int
		stdout   string // "" means: the full report; otherwise exact
		fileHas  bool
		fileText string
	}{
		{"all ok to stdout", []model.RepoReport{ok}, "", 0, "", false, ""},
		{"partial to stdout", []model.RepoReport{ok, bad}, "", 1, "", false, ""},
		{"all ok to file", []model.RepoReport{ok}, "f", 0, "NONE", true, `"error": null`},
		{"all failed to stdout", []model.RepoReport{bad}, "", 3, `{"error": "all_failed"}` + "\n", false, ""},
		{"all failed to file", []model.RepoReport{bad}, "f", 3, `{"error": "all_failed"}` + "\n", true, `"error": "not_found"`},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			var stdout, errw bytes.Buffer
			out := "-"
			if tc.out != "" {
				out = filepath.Join(t.TempDir(), "r.json")
			}
			code := finish(args.Options{Out: out}, model.Report{GeneratedAt: "t", Repos: tc.repos}, &stdout, &errw)
			if code != tc.code || errw.Len() != 0 {
				t.Fatalf("code %d stderr %q", code, errw.String())
			}
			switch tc.stdout {
			case "":
				if !strings.Contains(stdout.String(), `"generated_at": "t"`) {
					t.Fatalf("stdout %q", stdout.String())
				}
			case "NONE":
				if stdout.Len() != 0 {
					t.Fatalf("stdout %q", stdout.String())
				}
			default:
				if stdout.String() != tc.stdout {
					t.Fatalf("stdout %q", stdout.String())
				}
			}
			if tc.fileHas {
				b, err := os.ReadFile(out)
				if err != nil || !strings.Contains(string(b), tc.fileText) {
					t.Fatalf("file: %v %s", err, b)
				}
			}
		})
	}
	var errw bytes.Buffer
	if code := finish(args.Options{Out: "-"}, model.Report{Repos: []model.RepoReport{ok}}, failingWriter{}, &errw); code != ExitIO || errw.Len() == 0 {
		t.Fatalf("stdout failure: code %d stderr %q", code, errw.String())
	}
	errw.Reset()
	file := filepath.Join(t.TempDir(), "f")
	_ = os.WriteFile(file, nil, 0o600)
	if code := finish(args.Options{Out: filepath.Join(file, "x")}, model.Report{Repos: []model.RepoReport{bad}}, &bytes.Buffer{}, &errw); code != 3 || errw.Len() == 0 {
		t.Fatalf("all_failed with unwritable file: code %d stderr %q", code, errw.String())
	}
}

// covers: writeStdout
func TestWriteStdout(t *testing.T) {
	var out, errw bytes.Buffer
	writeStdout(&out, &errw, []byte("doc\n"))
	if out.String() != "doc\n" || errw.Len() != 0 {
		t.Fatalf("out %q err %q", out.String(), errw.String())
	}
	writeStdout(failingWriter{}, &errw, []byte("doc\n"))
	if !strings.Contains(errw.String(), "closed") {
		t.Fatalf("err %q", errw.String())
	}
}
