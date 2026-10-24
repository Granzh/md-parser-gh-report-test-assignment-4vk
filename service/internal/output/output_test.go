package output

import (
	"bytes"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// failWriter is an io.Writer that fails or writes only part of the data.
type failWriter struct {
	short bool
}

// Write fails with an error, or reports a short write when short is set.
//
// Args: p is the data to write.
// Returns: (0, error) by default, or (len(p)-1, nil) in short mode.
// Errors: always an error in the default mode.
func (w failWriter) Write(p []byte) (int, error) {
	if w.short {
		return len(p) - 1, nil
	}
	return 0, errors.New("disk on fire")
}

// readFile reads a file or aborts the test.
//
// Args: path is an existing file.
// Returns: its content.
// Errors: never fails; calls t.Fatal when the file cannot be read.
func readFile(t *testing.T, path string) []byte {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return b
}

// leftovers lists directory entries that look like temporary files.
//
// Args: dir is an existing directory.
// Returns: names of entries ending in ".tmp".
// Errors: never fails; calls t.Fatal when the directory cannot be read.
func leftovers(t *testing.T, dir string) []string {
	t.Helper()
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	var out []string
	for _, e := range entries {
		if strings.HasSuffix(e.Name(), ".tmp") {
			out = append(out, e.Name())
		}
	}
	return out
}

// covers: WriteReport
func TestWriteReportStdout(t *testing.T) {
	var buf bytes.Buffer
	if err := WriteReport("-", &buf, []byte("{}\n")); err != nil || buf.String() != "{}\n" {
		t.Fatalf("got %q, %v", buf.String(), err)
	}
	buf.Reset()
	if err := WriteReport("-", &buf, nil); err != nil || buf.Len() != 0 {
		t.Fatalf("empty data: %q, %v", buf.String(), err)
	}
}

// covers: WriteReport
func TestWriteReportStdoutErrors(t *testing.T) {
	if err := WriteReport("-", failWriter{}, []byte("x")); err == nil || !strings.Contains(err.Error(), "disk on fire") {
		t.Fatalf("got %v", err)
	}
	if err := WriteReport("-", failWriter{short: true}, []byte("xy")); err == nil {
		t.Fatal("short write must be an error")
	}
}

// covers: WriteReport
func TestWriteReportFile(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "report.json")
	var stdout bytes.Buffer
	if err := WriteReport(path, &stdout, []byte("one")); err != nil {
		t.Fatal(err)
	}
	if string(readFile(t, path)) != "one" || stdout.Len() != 0 {
		t.Fatalf("content %q, stdout %q", readFile(t, path), stdout.String())
	}
	// overwrite
	if err := WriteReport(path, &stdout, []byte("two-longer")); err != nil {
		t.Fatal(err)
	}
	if string(readFile(t, path)) != "two-longer" {
		t.Fatalf("content %q", readFile(t, path))
	}
	if err := WriteReport(path, &stdout, []byte("x")); err != nil || string(readFile(t, path)) != "x" {
		t.Fatalf("shrink: %q, %v", readFile(t, path), err)
	}
	if l := leftovers(t, dir); len(l) != 0 {
		t.Fatalf("temporary files left: %v", l)
	}
}

// covers: WriteReport
func TestWriteReportEmptyFile(t *testing.T) {
	path := filepath.Join(t.TempDir(), "empty")
	if err := WriteReport(path, nil, nil); err != nil || len(readFile(t, path)) != 0 {
		t.Fatalf("got %q, %v", readFile(t, path), err)
	}
}

// covers: WriteReport
func TestWriteReportCreatesParentDirectories(t *testing.T) {
	path := filepath.Join(t.TempDir(), "a", "b", "c", "report.json")
	if err := WriteReport(path, nil, []byte("deep")); err != nil {
		t.Fatal(err)
	}
	if string(readFile(t, path)) != "deep" {
		t.Fatalf("content %q", readFile(t, path))
	}
}

// covers: WriteReport
func TestWriteReportRelativePath(t *testing.T) {
	old, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Chdir(t.TempDir()); err != nil {
		t.Fatal(err)
	}
	defer func() { _ = os.Chdir(old) }()
	if err := WriteReport("out.json", nil, []byte("rel")); err != nil || string(readFile(t, "out.json")) != "rel" {
		t.Fatalf("relative path: %v", err)
	}
}

// covers: WriteReport
func TestWriteReportErrors(t *testing.T) {
	dir := t.TempDir()
	var stdout bytes.Buffer
	if err := WriteReport("", &stdout, []byte("x")); err == nil {
		t.Fatal("empty dest must fail")
	}
	// parent "directory" is a regular file
	blocker := filepath.Join(dir, "file")
	if err := os.WriteFile(blocker, []byte("keep"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := WriteReport(filepath.Join(blocker, "sub", "r.json"), &stdout, []byte("x")); err == nil {
		t.Fatal("file as parent must fail")
	}
	if string(readFile(t, blocker)) != "keep" {
		t.Fatal("blocker modified")
	}
	// destination is an existing directory: rename fails, temp file must be cleaned up and the directory kept
	target := filepath.Join(dir, "target")
	if err := os.Mkdir(target, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(target, "inner"), []byte("i"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := WriteReport(target, &stdout, []byte("x")); err == nil {
		t.Fatal("directory destination must fail")
	}
	if l := leftovers(t, dir); len(l) != 0 {
		t.Fatalf("temporary files left: %v", l)
	}
	if stdout.Len() != 0 {
		t.Fatalf("stdout polluted: %q", stdout.String())
	}
}

// covers: writeFileAtomic
func TestWriteFileAtomic(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "n", "f")
	if err := writeFileAtomic(path, []byte("abc")); err != nil {
		t.Fatal(err)
	}
	if string(readFile(t, path)) != "abc" {
		t.Fatalf("content %q", readFile(t, path))
	}
	if l := leftovers(t, filepath.Dir(path)); len(l) != 0 {
		t.Fatalf("temporary files left: %v", l)
	}
	if err := writeFileAtomic(filepath.Join(dir, "n"), []byte("x")); err == nil {
		t.Fatal("replacing a directory must fail")
	}
}
