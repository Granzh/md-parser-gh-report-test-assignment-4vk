package model

import (
	"encoding/json"
	"strings"
	"testing"
	"time"
)

// covers: NewRepoReport
func TestNewRepoReport(t *testing.T) {
	r := NewRepoReport("a/b")
	if r.Repo != "a/b" || r.Error != nil || r.Count != 0 || r.Issues == nil || len(r.Issues) != 0 {
		t.Fatalf("got %+v", r)
	}
	if e := NewRepoReport(""); e.Repo != "" || e.Issues == nil {
		t.Fatalf("empty repo: %+v", e)
	}
}

// covers: NewFailedRepoReport
func TestNewFailedRepoReport(t *testing.T) {
	for _, code := range []string{ErrNotFound, ErrRateLimited, ErrRequestFailed, ErrInvalidResponse} {
		r := NewFailedRepoReport("a/b", code)
		if r.Error == nil || *r.Error != code || r.Count != 0 || r.Issues == nil {
			t.Fatalf("%s: got %+v", code, r)
		}
	}
	// the pointer must not alias the caller's variable
	code := ErrNotFound
	r := NewFailedRepoReport("a/b", code)
	code = "changed"
	if *r.Error != ErrNotFound {
		t.Fatalf("aliasing: %q", *r.Error)
	}
}

// covers: FormatTimestamp
func TestFormatTimestamp(t *testing.T) {
	msk := time.FixedZone("MSK", 3*3600)
	cases := []struct {
		in   time.Time
		want string
	}{
		{time.Date(2026, 10, 14, 9, 0, 0, 0, time.UTC), "2026-10-14T09:00:00Z"},
		{time.Date(2026, 10, 14, 12, 0, 0, 0, msk), "2026-10-14T09:00:00Z"},
		{time.Date(2026, 10, 14, 9, 0, 0, 999999999, time.UTC), "2026-10-14T09:00:00Z"},
		{time.Date(2026, 1, 1, 0, 30, 0, 0, msk), "2025-12-31T21:30:00Z"},
		{time.Date(1, 1, 1, 0, 0, 0, 0, time.UTC), "0001-01-01T00:00:00Z"},
	}
	for _, tc := range cases {
		if got := FormatTimestamp(tc.in); got != tc.want {
			t.Errorf("FormatTimestamp(%v) = %q, want %q", tc.in, got, tc.want)
		}
	}
}

// covers: Normalize
func TestNormalizeNilSlices(t *testing.T) {
	in := Report{GeneratedAt: "t", Repos: []RepoReport{
		{Repo: "a/b", Count: 99},
		{Repo: "c/d", Issues: []Issue{{Number: 1}, {Number: 2, Labels: []string{"x"}}}},
	}}
	got := Normalize(in)
	if got.Repos[0].Issues == nil || got.Repos[0].Count != 0 {
		t.Fatalf("repo 0: %+v", got.Repos[0])
	}
	if got.Repos[1].Count != 2 || got.Repos[1].Issues[0].Labels == nil || len(got.Repos[1].Issues[1].Labels) != 1 {
		t.Fatalf("repo 1: %+v", got.Repos[1])
	}
	if in.Repos[0].Issues != nil || in.Repos[1].Issues[0].Labels != nil || in.Repos[0].Count != 99 {
		t.Fatal("input was modified")
	}
	if n := Normalize(Report{}); n.Repos == nil || len(n.Repos) != 0 {
		t.Fatalf("nil repos: %+v", n)
	}
}

// covers: Normalize
func TestNormalizeCopiesDeeply(t *testing.T) {
	code := ErrNotFound
	in := Report{Repos: []RepoReport{{Repo: "a/b", Error: &code, Issues: []Issue{{Labels: []string{"x"}}}}}}
	out := Normalize(in)
	*out.Repos[0].Error = "changed"
	out.Repos[0].Issues[0].Labels[0] = "changed"
	if code != ErrNotFound || in.Repos[0].Issues[0].Labels[0] != "x" {
		t.Fatal("Normalize shares memory with its input")
	}
}

// covers: RenderReport
func TestRenderReportSpecExample(t *testing.T) {
	r := Report{GeneratedAt: "2026-10-14T09:00:00Z", Repos: []RepoReport{
		{Repo: "cli/cli", Issues: []Issue{
			{Number: 1, Title: "Первый выпуск", Author: "octocat", Labels: []string{"bug"}, CreatedAt: "2026-01-05T10:00:00Z", URL: "https://github.com/cli/cli/issues/1"},
			{Number: 2, Title: "Второй выпуск", Author: "hubot", CreatedAt: "2026-01-06T11:00:00Z", URL: "https://github.com/cli/cli/issues/2"},
		}},
		NewFailedRepoReport("example/missing", ErrNotFound),
	}}
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
	got, err := RenderReport(r)
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != want {
		t.Fatalf("got:\n%s\nwant:\n%s", got, want)
	}
}

// covers: RenderReport
func TestRenderReportEmpty(t *testing.T) {
	got, err := RenderReport(Report{})
	if err != nil {
		t.Fatal(err)
	}
	want := "{\n  \"generated_at\": \"\",\n  \"repos\": []\n}\n"
	if string(got) != want {
		t.Fatalf("got %q, want %q", got, want)
	}
}

// covers: RenderReport
func TestRenderReportNoEscaping(t *testing.T) {
	title := `<b>Tom & "Jerry"</b> → 日本語 🎉 é`
	r := Report{Repos: []RepoReport{{Repo: "a/b", Issues: []Issue{{Number: 1, Title: title, URL: "https://x/?a=1&b=2"}}}}}
	got, err := RenderReport(r)
	if err != nil {
		t.Fatal(err)
	}
	s := string(got)
	for _, bad := range []string{`\u003c`, `\u003e`, `\u0026`, `\u00e9`, `\ud83c`, `\u65e5`} {
		if strings.Contains(s, bad) {
			t.Errorf("output contains %s:\n%s", bad, s)
		}
	}
	for _, good := range []string{"<b>Tom & ", "→ 日本語 🎉 é", "?a=1&b=2", `\"Jerry\"`} {
		if !strings.Contains(s, good) {
			t.Errorf("output lacks %q:\n%s", good, s)
		}
	}
	var back Report
	if err := json.Unmarshal(got, &back); err != nil || back.Repos[0].Issues[0].Title != title {
		t.Fatalf("round trip: %v, %+v", err, back)
	}
}

// covers: RenderReport
func TestRenderReportControlAndInvalidUTF8(t *testing.T) {
	r := Report{Repos: []RepoReport{{Repo: "a/b", Issues: []Issue{{Title: "a\tb\nc\x01\xff\u2028"}}}}}
	got, err := RenderReport(r)
	if err != nil {
		t.Fatal(err)
	}
	s := string(got)
	for _, want := range []string{`a\tb\nc\u0001`, `\ufffd`, `\u2028`} {
		if !strings.Contains(s, want) {
			t.Errorf("output lacks %s: %s", want, s)
		}
	}
	if !json.Valid(got) {
		t.Fatal("invalid JSON")
	}
}

// covers: RenderReport
func TestRenderReportEndsWithSingleNewlineAndNoCR(t *testing.T) {
	got, _ := RenderReport(Report{Repos: []RepoReport{NewRepoReport("a/b")}})
	if !strings.HasSuffix(string(got), "}\n") || strings.HasSuffix(string(got), "\n\n") || strings.Contains(string(got), "\r") {
		t.Fatalf("bad framing: %q", got)
	}
}

// covers: FatalJSON
func TestFatalJSON(t *testing.T) {
	cases := map[string]string{
		FatalUnauthorized: `{"error":"unauthorized"}` + "\n",
		FatalAllFailed:    `{"error":"all_failed"}` + "\n",
		"":                `{"error":""}` + "\n",
		`<&">`:            `{"error":"<&\">"}` + "\n",
	}
	for in, want := range cases {
		if got := string(FatalJSON(in)); got != want {
			t.Errorf("FatalJSON(%q) = %q, want %q", in, got, want)
		}
	}
}
