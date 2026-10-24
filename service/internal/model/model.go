// Package model defines the report data structures and their JSON rendering.
package model

import (
	"bytes"
	"encoding/json"
	"time"
)

// Repository error codes (spec 4.4.6).
const (
	ErrNotFound        = "not_found"
	ErrRateLimited     = "rate_limited"
	ErrRequestFailed   = "request_failed"
	ErrInvalidResponse = "invalid_response"
)

// Fatal error codes printed as {"error": "..."} (spec 4.4.6).
const (
	FatalUnauthorized = "unauthorized"
	FatalAllFailed    = "all_failed"
)

// Issue is one open issue of a repository.
type Issue struct {
	Number    int      `json:"number"`
	Title     string   `json:"title"`
	Author    string   `json:"author"`
	Labels    []string `json:"labels"`
	CreatedAt string   `json:"created_at"` // RFC3339, exactly as returned by the API
	URL       string   `json:"url"`
}

// RepoReport is the outcome for one repository.
type RepoReport struct {
	Repo   string  `json:"repo"`
	Error  *string `json:"error"` // nil on success, otherwise one of the Err* codes
	Count  int     `json:"count"`
	Issues []Issue `json:"issues"`
}

// Report is the complete output document.
type Report struct {
	GeneratedAt string       `json:"generated_at"` // UTC, RFC3339
	Repos       []RepoReport `json:"repos"`
}

// NewRepoReport creates a successful, empty report for a repository.
//
// Args: repo is the "owner/repo" string.
// Returns: a RepoReport with nil Error, Count 0 and a non-nil empty Issues slice.
// Errors: never fails.
func NewRepoReport(repo string) RepoReport {
	return RepoReport{Repo: repo, Issues: []Issue{}}
}

// NewFailedRepoReport creates an empty report marked with an error code.
//
// Args: repo is the "owner/repo" string; code is one of the Err* constants.
// Returns: a RepoReport with Error pointing to a copy of code, Count 0 and a non-nil empty Issues slice.
// Errors: never fails.
func NewFailedRepoReport(repo, code string) RepoReport {
	r := NewRepoReport(repo)
	r.Error = &code
	return r
}

// FormatTimestamp renders an instant as UTC RFC3339 with second precision.
//
// Args: t is any time value.
// Returns: a string such as "2026-10-14T09:00:00Z".
// Errors: never fails.
func FormatTimestamp(t time.Time) string {
	return t.UTC().Format("2006-01-02T15:04:05Z")
}

// Normalize returns a copy of the report that is safe to serialize.
//
// Args: r is the report; it is not modified.
// Returns: a deep copy in which Repos, every Issues and every Labels are non-nil and every Count equals len(Issues).
// Errors: never fails.
func Normalize(r Report) Report {
	out := Report{GeneratedAt: r.GeneratedAt, Repos: make([]RepoReport, len(r.Repos))}
	for i, repo := range r.Repos {
		c := repo
		if repo.Error != nil {
			code := *repo.Error
			c.Error = &code
		}
		c.Issues = make([]Issue, len(repo.Issues))
		for j, issue := range repo.Issues {
			issue.Labels = append([]string{}, issue.Labels...)
			c.Issues[j] = issue
		}
		c.Count = len(c.Issues)
		out.Repos[i] = c
	}
	return out
}

// RenderReport serializes a report as the final document.
//
// Args: r is the report; it is normalized first.
// Returns: UTF-8 JSON indented by two spaces and ending with a newline; <, > and & are not escaped, non-ASCII text is
// kept as is (only U+2028/U+2029 and control characters become \u escapes, invalid UTF-8 becomes U+FFFD).
// Errors: a json encoding error, which cannot happen for these types.
func RenderReport(r Report) ([]byte, error) {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	enc.SetIndent("", "  ")
	if err := enc.Encode(Normalize(r)); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

// FatalJSON renders the fatal error document.
//
// Args: code is one of the Fatal* constants.
// Returns: {"error":"<code>"} followed by a newline.
// Errors: never fails.
func FatalJSON(code string) []byte {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	// A map of strings cannot fail to encode.
	_ = enc.Encode(map[string]string{"error": code})
	return buf.Bytes()
}
