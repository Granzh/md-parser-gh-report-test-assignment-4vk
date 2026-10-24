// Package trace writes the JSON Lines log of HTTP attempts (--trace).
package trace

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sync"
)

// Error descriptions used in Record.Error.
const (
	ErrTimeout  = "timeout"
	ErrNetwork  = "network"
	ErrCanceled = "canceled"
	ErrTooLarge = "too_large"
)

var userinfoRe = regexp.MustCompile(`^([A-Za-z][A-Za-z0-9+.-]*://)[^/?#]*@`)

// Record describes one HTTP attempt. Fields are serialized in the order of the spec (4.6).
type Record struct {
	TS        string  `json:"ts"` // RFC3339, UTC
	Method    string  `json:"method"`
	URL       string  `json:"url"`
	Status    int     `json:"status"` // 0 when no response was received
	Attempt   int     `json:"attempt"`
	FromCache bool    `json:"from_cache"`
	Error     *string `json:"error"` // always present: null or a short description
}

// Writer appends records to a file. A nil *Writer is valid and discards everything.
type Writer struct {
	mu sync.Mutex
	f  *os.File
}

// New opens the trace file.
//
// Args: path is the --trace value; "" means tracing is off.
// Returns: (nil, nil) for an empty path; otherwise a Writer on a newly created or truncated file (missing parent
// directories are created).
// Errors: the error of MkdirAll or OpenFile.
func New(path string) (*Writer, error) {
	if path == "" {
		return nil, nil
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return nil, err
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o644)
	if err != nil {
		return nil, err
	}
	return &Writer{f: f}, nil
}

// Write appends one record as exactly one JSON line and flushes it to disk.
//
// Args: r is the record; its URL has any user info removed before writing; r is not modified.
// Returns: nil on success and on a nil or closed Writer (no-op).
// Errors: a JSON, write or sync error.
func (w *Writer) Write(r Record) error {
	if w == nil {
		return nil
	}
	r.URL = StripUserinfo(r.URL)
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(r); err != nil {
		return err
	}
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.f == nil {
		return nil
	}
	if _, err := w.f.Write(buf.Bytes()); err != nil {
		return fmt.Errorf("запись в журнал: %w", err)
	}
	return w.f.Sync()
}

// Close closes the file.
//
// Args: none.
// Returns: nil on a nil or already closed Writer; later Write calls become no-ops.
// Errors: the error of closing the file.
func (w *Writer) Close() error {
	if w == nil {
		return nil
	}
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.f == nil {
		return nil
	}
	err := w.f.Close()
	w.f = nil
	return err
}

// StripUserinfo removes "user:password@" from a URL.
//
// Args: raw is a URL string; may be unparsable or empty.
// Returns: raw without the user info part; unparsable input is cleaned with a pattern, other input is unchanged.
// Errors: never fails.
func StripUserinfo(raw string) string {
	u, err := url.Parse(raw)
	if err != nil {
		return userinfoRe.ReplaceAllString(raw, "$1")
	}
	if u.User == nil {
		return raw
	}
	u.User = nil
	return u.String()
}

// Str returns a pointer to a copy of s, for Record.Error.
//
// Args: s is any string.
// Returns: a non-nil pointer.
// Errors: never fails.
func Str(s string) *string {
	return &s
}
