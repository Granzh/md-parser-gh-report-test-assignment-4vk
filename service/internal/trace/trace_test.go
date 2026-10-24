package trace

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// readLines reads a file and splits it into lines.
//
// Args: path is an existing file; it must end with a newline.
// Returns: the lines without terminators.
// Errors: never fails; calls t.Fatal on read errors or a missing final newline.
func readLines(t *testing.T, path string) []string {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if len(b) > 0 && b[len(b)-1] != '\n' {
		t.Fatalf("no final newline: %q", b)
	}
	return strings.Split(strings.TrimSuffix(string(b), "\n"), "\n")
}

// covers: Writer.Write
func TestWriteFormat(t *testing.T) {
	path := filepath.Join(t.TempDir(), "t.jsonl")
	w, err := New(path)
	if err != nil {
		t.Fatal(err)
	}
	defer w.Close()
	rec := Record{TS: "2026-10-14T09:00:01Z", Method: "GET", URL: "https://api.github.com/repos/cli/cli/issues?per_page=100&x=<>", Status: 200, Attempt: 1}
	if err := w.Write(rec); err != nil {
		t.Fatal(err)
	}
	// readable immediately, without Close: the line must have been flushed
	lines := readLines(t, path)
	want := `{"ts":"2026-10-14T09:00:01Z","method":"GET","url":"https://api.github.com/repos/cli/cli/issues?per_page=100&x=<>","status":200,"attempt":1,"from_cache":false,"error":null}`
	if len(lines) != 1 || lines[0] != want {
		t.Fatalf("got %q\nwant %q", lines, want)
	}
}

// covers: Writer.Write
func TestWriteOneLinePerRecordWithError(t *testing.T) {
	path := filepath.Join(t.TempDir(), "t.jsonl")
	w, _ := New(path)
	defer w.Close()
	for i, e := range []*Record{
		{TS: "t", Method: "GET", URL: "u\nwith newline", Attempt: 1, Error: Str(ErrTimeout)},
		{TS: "t", Method: "GET", URL: "u", Status: 304, Attempt: 2, FromCache: true},
		{TS: "t", Method: "GET", URL: "u", Attempt: 3, Error: Str(ErrNetwork)},
	} {
		if err := w.Write(*e); err != nil {
			t.Fatalf("%d: %v", i, err)
		}
	}
	lines := readLines(t, path)
	if len(lines) != 3 {
		t.Fatalf("got %d lines: %q", len(lines), lines)
	}
	wantErr := []any{"timeout", nil, "network"}
	for i, l := range lines {
		var m map[string]any
		if err := json.Unmarshal([]byte(l), &m); err != nil {
			t.Fatalf("line %d: %v", i, err)
		}
		v, present := m["error"]
		if !present || v != wantErr[i] {
			t.Errorf("line %d: error = %v (present=%v), want %v", i, v, present, wantErr[i])
		}
		if len(m) != 7 {
			t.Errorf("line %d has %d fields: %v", i, len(m), m)
		}
	}
}

// covers: Writer.Write
func TestWriteNilWriterIsNoop(t *testing.T) {
	var w *Writer
	if err := w.Write(Record{}); err != nil {
		t.Fatal(err)
	}
	if err := w.Close(); err != nil {
		t.Fatal(err)
	}
}

// covers: Writer.Write
func TestWriteStripsUserinfoAndHasNoAuthorization(t *testing.T) {
	path := filepath.Join(t.TempDir(), "t.jsonl")
	w, _ := New(path)
	defer w.Close()
	if err := w.Write(Record{TS: "t", Method: "GET", URL: "https://user:SECRETPW@h.example/x?y=1", Attempt: 1}); err != nil {
		t.Fatal(err)
	}
	b, _ := os.ReadFile(path)
	s := string(b)
	if strings.Contains(s, "SECRETPW") || strings.Contains(s, "user:") || strings.Contains(strings.ToLower(s), "authorization") || strings.Contains(strings.ToLower(s), "bearer") {
		t.Fatalf("leak: %s", s)
	}
	if !strings.Contains(s, `"url":"https://h.example/x?y=1"`) {
		t.Fatalf("url: %s", s)
	}
}

// covers: Writer.Write
func TestWriteAfterCloseIsNoop(t *testing.T) {
	path := filepath.Join(t.TempDir(), "t.jsonl")
	w, _ := New(path)
	if err := w.Close(); err != nil {
		t.Fatal(err)
	}
	if err := w.Write(Record{TS: "t"}); err != nil {
		t.Fatal(err)
	}
	if b, _ := os.ReadFile(path); len(b) != 0 {
		t.Fatalf("written after close: %q", b)
	}
}

// covers: New
func TestNewEmptyPathDisablesTracing(t *testing.T) {
	w, err := New("")
	if w != nil || err != nil {
		t.Fatalf("got %v, %v", w, err)
	}
}

// covers: New
func TestNewTruncatesAndCreatesDirectories(t *testing.T) {
	path := filepath.Join(t.TempDir(), "sub", "dir", "t.jsonl")
	w, err := New(path)
	if err != nil {
		t.Fatal(err)
	}
	_ = w.Write(Record{TS: "old", Method: "GET", URL: "u", Attempt: 1})
	_ = w.Close()
	if len(readLines(t, path)) != 1 {
		t.Fatal("first run must leave one line")
	}
	w2, err := New(path)
	if err != nil {
		t.Fatal(err)
	}
	if b, _ := os.ReadFile(path); len(b) != 0 {
		t.Fatalf("file not truncated: %q", b)
	}
	_ = w2.Write(Record{TS: "new", Method: "GET", URL: "u", Attempt: 1})
	_ = w2.Close()
	lines := readLines(t, path)
	if len(lines) != 1 || !strings.Contains(lines[0], `"ts":"new"`) {
		t.Fatalf("got %q", lines)
	}
}

// covers: New
func TestNewFailsWhenPathIsDirectory(t *testing.T) {
	if w, err := New(t.TempDir()); err == nil || w != nil {
		t.Fatalf("got %v, %v", w, err)
	}
}

// covers: Writer.Close
func TestCloseTwice(t *testing.T) {
	w, _ := New(filepath.Join(t.TempDir(), "t"))
	if err := w.Close(); err != nil {
		t.Fatal(err)
	}
	if err := w.Close(); err != nil {
		t.Fatalf("second Close: %v", err)
	}
}

// covers: StripUserinfo
func TestStripUserinfo(t *testing.T) {
	cases := map[string]string{
		"https://api.github.com/x?a=1": "https://api.github.com/x?a=1",
		"https://u@h/x":                "https://h/x",
		"https://u:p@h:8080/x?q=1#f":   "https://h:8080/x?q=1#f",
		"http://u:p@[::1]:80/":         "http://[::1]:80/",
		"":                             "",
		"not a url":                    "not a url",
		"http://u:p@[bad":              "http://[bad",
		"https://h/path/with@sign":     "https://h/path/with@sign",
		"https://h/?email=a@b.c":       "https://h/?email=a@b.c",
	}
	for in, want := range cases {
		if got := StripUserinfo(in); got != want {
			t.Errorf("StripUserinfo(%q) = %q, want %q", in, got, want)
		}
	}
}

// covers: Str
func TestStr(t *testing.T) {
	s := "x"
	p := Str(s)
	s = "y"
	if p == nil || *p != "x" {
		t.Fatalf("got %v", p)
	}
}
