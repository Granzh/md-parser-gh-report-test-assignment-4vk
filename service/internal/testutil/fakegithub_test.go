package testutil

import (
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"sync"
	"testing"
)

// get performs a GET with optional headers.
//
// Args: t is the test; url is the target; kv are header name/value pairs.
// Returns: the response and its body.
// Errors: never fails; calls t.Fatal on transport errors.
func get(t *testing.T, url string, kv ...string) (*http.Response, string) {
	t.Helper()
	req, _ := http.NewRequest("GET", url, nil)
	for i := 0; i+1 < len(kv); i += 2 {
		req.Header.Set(kv[i], kv[i+1])
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(resp.Body)
	return resp, string(b)
}

// five returns issues numbered 1..5.
//
// Args: none.
// Returns: five issues with increasing created_at.
// Errors: never fails.
func five() []Item {
	var out []Item
	for i := 1; i <= 5; i++ {
		out = append(out, NewIssue(i, "2026-01-0"+string(rune('0'+i))+"T00:00:00Z"))
	}
	return out
}

// covers: NewIssue
func TestNewIssue(t *testing.T) {
	b, _ := json.Marshal(NewIssue(7, "2026-01-02T03:04:05Z"))
	for _, want := range []string{`"number":7`, `"title":"Issue 7"`, `"login":"user7"`, `"labels":[]`, `"created_at":"2026-01-02T03:04:05Z"`, `"html_url"`} {
		if !strings.Contains(string(b), want) {
			t.Errorf("%s lacks %s", b, want)
		}
	}
	if strings.Contains(string(b), "pull_request") {
		t.Fatal("plain issue has pull_request")
	}
}

// covers: NewPR
func TestNewPR(t *testing.T) {
	if _, ok := NewPR(1, "x")["pull_request"]; !ok {
		t.Fatal("missing pull_request")
	}
}

// covers: FakeGitHub.serve
func TestServePaging(t *testing.T) {
	f := New(t)
	f.PageSize = 2
	f.SetRepo("o/r", five())
	resp, body := get(t, f.URL+"/repos/o/r/issues?state=open&page=1")
	var items []Item
	if err := json.Unmarshal([]byte(body), &items); err != nil || len(items) != 2 {
		t.Fatalf("page 1: %v %s", err, body)
	}
	link := resp.Header.Get("Link")
	if !strings.Contains(link, `<`+f.URL+`/repos/o/r/issues?state=open&sort=created&direction=asc&per_page=2&page=2>; rel="next"`) || !strings.Contains(link, `page=3>; rel="last"`) {
		t.Fatalf("link %q", link)
	}
	resp, body = get(t, f.URL+"/repos/o/r/issues?page=3")
	_ = json.Unmarshal([]byte(body), &items)
	if len(items) != 1 || strings.Contains(resp.Header.Get("Link"), "next") {
		t.Fatalf("last page: %s, link %q", body, resp.Header.Get("Link"))
	}
	_, body = get(t, f.URL+"/repos/o/r/issues?page=9")
	if body != "[]" {
		t.Fatalf("beyond last page: %s", body)
	}
}

// covers: FakeGitHub.serve
func TestServePerPageQueryAndSinglePageHasNoLink(t *testing.T) {
	f := New(t)
	f.SetRepo("o/r", five())
	resp, body := get(t, f.URL+"/repos/o/r/issues?per_page=100")
	var items []Item
	_ = json.Unmarshal([]byte(body), &items)
	if len(items) != 5 || resp.Header.Get("Link") != "" {
		t.Fatalf("%d items, link %q", len(items), resp.Header.Get("Link"))
	}
	f.SetRepo("o/r", five())
	resp, _ = get(t, f.URL+"/repos/o/r/issues?per_page=2")
	if !strings.Contains(resp.Header.Get("Link"), `per_page=2&page=2>; rel="next"`) {
		t.Fatalf("link %q", resp.Header.Get("Link"))
	}
}

// covers: FakeGitHub.serve
func TestServeETagAnd304(t *testing.T) {
	f := New(t)
	f.PageSize = 2
	f.SetRepo("o/r", five())
	resp, _ := get(t, f.URL+"/repos/o/r/issues")
	etag := resp.Header.Get("ETag")
	if etag == "" || strings.HasPrefix(etag, "W/") {
		t.Fatalf("etag %q", etag)
	}
	resp2, body := get(t, f.URL+"/repos/o/r/issues", "If-None-Match", etag)
	if resp2.StatusCode != 304 || body != "" || resp2.Header.Get("Link") != "" {
		t.Fatalf("304 expected without Link, got %d %q link %q", resp2.StatusCode, body, resp2.Header.Get("Link"))
	}
	f.LinkOn304 = true
	resp3, _ := get(t, f.URL+"/repos/o/r/issues", "If-None-Match", etag)
	if resp3.StatusCode != 304 || resp3.Header.Get("Link") == "" {
		t.Fatalf("LinkOn304: %d link %q", resp3.StatusCode, resp3.Header.Get("Link"))
	}
	// a different page has a different ETag
	resp4, _ := get(t, f.URL+"/repos/o/r/issues?page=2")
	if resp4.Header.Get("ETag") == etag {
		t.Fatal("pages share an ETag")
	}
	f.WeakETag = true
	resp5, _ := get(t, f.URL+"/repos/o/r/issues")
	if !strings.HasPrefix(resp5.Header.Get("ETag"), `W/"`) {
		t.Fatalf("weak etag %q", resp5.Header.Get("ETag"))
	}
	f.WeakETag, f.DisableETag = false, true
	resp6, _ := get(t, f.URL+"/repos/o/r/issues", "If-None-Match", etag)
	if resp6.StatusCode != 200 || resp6.Header.Get("ETag") != "" {
		t.Fatalf("DisableETag: %d %q", resp6.StatusCode, resp6.Header.Get("ETag"))
	}
}

// covers: FakeGitHub.serve
func TestServeRelativeLinksAndCycle(t *testing.T) {
	f := New(t)
	f.PageSize = 2
	f.RelativeLinks = true
	f.SetRepo("o/r", five())
	resp, _ := get(t, f.URL+"/repos/o/r/issues")
	if !strings.HasPrefix(resp.Header.Get("Link"), `</repos/o/r/issues?`) {
		t.Fatalf("link %q", resp.Header.Get("Link"))
	}
	f.RelativeLinks, f.LinkCycle = false, true
	resp, _ = get(t, f.URL+"/repos/o/r/issues?page=3")
	if !strings.Contains(resp.Header.Get("Link"), `page=3>; rel="next"`) {
		t.Fatalf("cycle link %q", resp.Header.Get("Link"))
	}
}

// covers: FakeGitHub.serve
func TestServeUnknownAndReplies(t *testing.T) {
	f := New(t)
	if resp, _ := get(t, f.URL+"/repos/no/such/issues"); resp.StatusCode != 404 {
		t.Fatalf("unknown repo: %d", resp.StatusCode)
	}
	if resp, _ := get(t, f.URL+"/other"); resp.StatusCode != 404 {
		t.Fatalf("unknown path: %d", resp.StatusCode)
	}
	f.SetReply("x/y", Reply{Status: 500, Body: "boom", Header: map[string]string{"X-A": "1"}})
	resp, body := get(t, f.URL+"/repos/x/y/issues")
	if resp.StatusCode != 500 || body != "boom" || resp.Header.Get("X-A") != "1" {
		t.Fatalf("reply: %d %q", resp.StatusCode, body)
	}
	f.SetReply("x/y", Reply{Body: "{}"})
	if resp, body := get(t, f.URL+"/repos/x/y/issues"); resp.StatusCode != 200 || body != "{}" {
		t.Fatalf("default status: %d %q", resp.StatusCode, body)
	}
}

// covers: FakeGitHub.SetRepo
func TestSetRepoReplacesItems(t *testing.T) {
	f := New(t)
	f.SetRepo("o/r", five())
	f.SetRepo("o/r", nil)
	if _, body := get(t, f.URL+"/repos/o/r/issues"); body != "[]" {
		t.Fatalf("got %s", body)
	}
}

// covers: FakeGitHub.SetReply
func TestSetReplyOverridesRepo(t *testing.T) {
	f := New(t)
	f.SetRepo("o/r", five())
	f.SetReply("o/r", Reply{Status: 418})
	if resp, _ := get(t, f.URL+"/repos/o/r/issues"); resp.StatusCode != 418 {
		t.Fatalf("got %d", resp.StatusCode)
	}
}

// covers: FakeGitHub.Requests
func TestRequestsRecordsHeaders(t *testing.T) {
	f := New(t)
	f.SetRepo("o/r", five())
	get(t, f.URL+"/repos/o/r/issues?a=1", "Authorization", "Bearer x")
	reqs := f.Requests()
	if len(reqs) != 1 || reqs[0].URI != "/repos/o/r/issues?a=1" || reqs[0].Header.Get("Authorization") != "Bearer x" {
		t.Fatalf("got %+v", reqs)
	}
	reqs[0].URI = "changed"
	if f.Requests()[0].URI == "changed" {
		t.Fatal("Requests exposes internal storage")
	}
}

// covers: FakeGitHub.Count
func TestCount(t *testing.T) {
	f := New(t)
	if f.Count() != 0 {
		t.Fatal("fresh fake has requests")
	}
	get(t, f.URL+"/x")
	get(t, f.URL+"/y")
	if f.Count() != 2 {
		t.Fatalf("count %d", f.Count())
	}
}

// covers: FakeGitHub.Reset
func TestReset(t *testing.T) {
	f := New(t)
	get(t, f.URL+"/x")
	f.Reset()
	if f.Count() != 0 || len(f.Requests()) != 0 {
		t.Fatal("Reset did not clear")
	}
}

// covers: New
func TestNewServerIsReachable(t *testing.T) {
	f := New(t)
	if !strings.HasPrefix(f.URL, "http://127.0.0.1:") {
		t.Fatalf("url %q", f.URL)
	}
}

// covers: FakeGitHub.linkHeader
func TestLinkHeader(t *testing.T) {
	f := &FakeGitHub{URL: "http://h"}
	parts := []string{"repos", "o", "r", "issues"}
	if got := f.linkHeader(nil, parts, 1, 10, 5); got != "" {
		t.Fatalf("single page: %q", got)
	}
	if got := f.linkHeader(nil, parts, 2, 10, 25); !strings.Contains(got, "page=3>; rel=\"next\"") || !strings.Contains(got, "page=3>; rel=\"last\"") {
		t.Fatalf("middle page: %q", got)
	}
	if got := f.linkHeader(nil, parts, 3, 10, 25); strings.Contains(got, "next") {
		t.Fatalf("last page: %q", got)
	}
	f.RelativeLinks = true
	if got := f.linkHeader(nil, parts, 1, 10, 25); !strings.HasPrefix(got, "</repos/o/r/issues?") {
		t.Fatalf("relative: %q", got)
	}
}

// covers: FakeGitHub.MaxConcurrent
func TestMaxConcurrent(t *testing.T) {
	f := New(t)
	if f.MaxConcurrent() != 0 {
		t.Fatal("fresh fake")
	}
	get(t, f.URL+"/x")
	get(t, f.URL+"/y")
	if f.MaxConcurrent() != 1 {
		t.Fatalf("sequential requests: %d", f.MaxConcurrent())
	}
	// two overlapping requests: the first one is held until the second one arrives
	release := make(chan struct{})
	var once sync.Once
	first := make(chan struct{})
	f.OnRequest = func() {
		held := false
		once.Do(func() { held = true; close(first) })
		if held {
			<-release
			return
		}
		close(release)
	}
	done := make(chan struct{})
	go func() { get(t, f.URL+"/a"); close(done) }()
	<-first
	get(t, f.URL+"/b")
	<-done
	if f.MaxConcurrent() != 2 {
		t.Fatalf("overlapping requests: %d", f.MaxConcurrent())
	}
}

// covers: FakeGitHub.QueueReplies
func TestQueueReplies(t *testing.T) {
	f := New(t)
	f.SetRepo("o/r", five())
	f.QueueReplies("o/r", Reply{Status: 503}, Reply{Status: 429, Header: map[string]string{"Retry-After": "3"}})
	if resp, _ := get(t, f.URL+"/repos/o/r/issues"); resp.StatusCode != 503 {
		t.Fatalf("first: %d", resp.StatusCode)
	}
	if resp, _ := get(t, f.URL+"/repos/o/r/issues"); resp.StatusCode != 429 || resp.Header.Get("Retry-After") != "3" {
		t.Fatalf("second: %d", resp.StatusCode)
	}
	if resp, body := get(t, f.URL+"/repos/o/r/issues"); resp.StatusCode != 200 || !strings.HasPrefix(body, "[") {
		t.Fatalf("after queue: %d %s", resp.StatusCode, body)
	}
}
