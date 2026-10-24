// Package testutil provides an in-process fake of the GitHub issues endpoint for tests.
package testutil

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync"
	"testing"
)

// Item is one element of the issues array, as generic JSON.
type Item = map[string]any

// Request is a recorded incoming request.
type Request struct {
	URI    string      // path and query as received
	Header http.Header // a copy of the request headers
}

// Reply is a canned response that replaces the normal behavior for one repository.
type Reply struct {
	Status int
	Body   string
	Header map[string]string
}

// FakeGitHub serves GET /repos/{owner}/{repo}/issues from in-memory data.
type FakeGitHub struct {
	URL string // base URL of the httptest server, without trailing slash

	// PageSize is the number of items per page; 0 means the per_page query value, or 100.
	PageSize int
	// RelativeLinks makes Link targets relative ("/repos/...") instead of absolute.
	RelativeLinks bool
	// DisableETag omits the ETag header (and never answers 304).
	DisableETag bool
	// LinkOn304 repeats the Link header in 304 responses (off by default, like many servers).
	LinkOn304 bool
	// LinkCycle makes every page's rel="next" point back at the same page.
	LinkCycle bool
	// WeakETag emits ETag values as W/"...".
	WeakETag bool
	// OnRequest, when set, is called for every request after it was counted as in flight and before the response
	// is produced; tests use it to hold requests open.
	OnRequest func()

	srv      *httptest.Server
	mu       sync.Mutex
	repos    map[string][]Item
	replies  map[string]Reply
	queues   map[string][]Reply
	requests []Request
	inflight int
	maxSeen  int
}

// NewIssue creates a plain open issue.
//
// Args: number is the issue number; createdAt is the created_at text, copied verbatim.
// Returns: an Item with number, title "Issue <n>", user.login "user<n>", empty labels, html_url and created_at.
// Errors: never fails.
func NewIssue(number int, createdAt string) Item {
	return Item{
		"number":     number,
		"title":      fmt.Sprintf("Issue %d", number),
		"user":       Item{"login": fmt.Sprintf("user%d", number)},
		"labels":     []any{},
		"html_url":   fmt.Sprintf("https://github.com/o/r/issues/%d", number),
		"created_at": createdAt,
	}
}

// NewPR creates a pull request item (an issue with a pull_request key).
//
// Args: number is the number; createdAt is the created_at text.
// Returns: NewIssue(number, createdAt) plus "pull_request": {}.
// Errors: never fails.
func NewPR(number int, createdAt string) Item {
	it := NewIssue(number, createdAt)
	it["pull_request"] = Item{"url": "https://api.github.com/pulls/x"}
	return it
}

// New starts a fake server that is closed when the test ends.
//
// Args: t is the running test.
// Returns: a FakeGitHub with no repositories (every repository answers 404) and default settings.
// Errors: never fails.
func New(t *testing.T) *FakeGitHub {
	t.Helper()
	f := &FakeGitHub{repos: map[string][]Item{}, replies: map[string]Reply{}, queues: map[string][]Reply{}}
	f.srv = httptest.NewServer(http.HandlerFunc(f.serve))
	f.URL = f.srv.URL
	t.Cleanup(f.srv.Close)
	return f
}

// SetRepo defines the issues of a repository, in the order they are served.
//
// Args: repo is "owner/name"; items are served as given (no sorting, duplicates and PRs allowed).
// Returns: nothing.
// Errors: never fails.
func (f *FakeGitHub) SetRepo(repo string, items []Item) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.repos[repo] = items
}

// SetReply makes every request for a repository return a canned response.
//
// Args: repo is "owner/name"; r is the response; Status 0 means 200.
// Returns: nothing.
// Errors: never fails.
func (f *FakeGitHub) SetReply(repo string, r Reply) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.replies[repo] = r
}

// QueueReplies makes the next requests for a repository return the given canned responses, one per request, in
// order; afterwards the repository behaves normally again (or as set by SetReply).
//
// Args: repo is "owner/name"; replies are appended to the queue; Status 0 means 200.
// Returns: nothing.
// Errors: never fails.
func (f *FakeGitHub) QueueReplies(repo string, replies ...Reply) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.queues[repo] = append(f.queues[repo], replies...)
}

// Requests returns all requests received so far.
//
// Args: none.
// Returns: a copy of the request log in arrival order.
// Errors: never fails.
func (f *FakeGitHub) Requests() []Request {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]Request{}, f.requests...)
}

// Count returns the number of requests received so far.
//
// Args: none.
// Returns: len(Requests()).
// Errors: never fails.
func (f *FakeGitHub) Count() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.requests)
}

// MaxConcurrent returns the highest number of requests that were being served at the same time.
//
// Args: none.
// Returns: 0 before the first request, 1 for strictly sequential clients.
// Errors: never fails.
func (f *FakeGitHub) MaxConcurrent() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.maxSeen
}

// Reset forgets the recorded requests.
//
// Args: none.
// Returns: nothing.
// Errors: never fails.
func (f *FakeGitHub) Reset() {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.requests = nil
}

// serve handles one request.
//
// Args: w and r are the usual handler arguments.
// Returns: nothing. Serves /repos/{owner}/{repo}/issues with paging (query "page", 1-based), ETag/If-None-Match,
// Link headers; 404 for unknown repositories and paths.
// Errors: never fails.
func (f *FakeGitHub) serve(w http.ResponseWriter, r *http.Request) {
	f.mu.Lock()
	f.requests = append(f.requests, Request{URI: r.URL.RequestURI(), Header: r.Header.Clone()})
	f.inflight++
	f.maxSeen = max(f.maxSeen, f.inflight)
	hook := f.OnRequest
	f.mu.Unlock()
	defer func() {
		f.mu.Lock()
		f.inflight--
		f.mu.Unlock()
	}()
	if hook != nil {
		hook()
	}

	parts := strings.Split(strings.Trim(r.URL.Path, "/"), "/")
	if r.Method != http.MethodGet || len(parts) != 4 || parts[0] != "repos" || parts[3] != "issues" {
		http.Error(w, `{"message":"Not Found"}`, http.StatusNotFound)
		return
	}
	repo := parts[1] + "/" + parts[2]
	f.mu.Lock()
	items, known := f.repos[repo]
	reply, replied := f.replies[repo]
	if q := f.queues[repo]; len(q) > 0 {
		reply, replied = q[0], true
		f.queues[repo] = q[1:]
	}
	f.mu.Unlock()
	if replied {
		for k, v := range reply.Header {
			w.Header().Set(k, v)
		}
		status := reply.Status
		if status == 0 {
			status = http.StatusOK
		}
		w.WriteHeader(status)
		_, _ = w.Write([]byte(reply.Body))
		return
	}
	if !known {
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(`{"message":"Not Found"}`))
		return
	}

	q := r.URL.Query()
	size := f.PageSize
	if size <= 0 {
		size, _ = strconv.Atoi(q.Get("per_page"))
	}
	if size <= 0 {
		size = 100
	}
	page, _ := strconv.Atoi(q.Get("page"))
	if page < 1 {
		page = 1
	}
	lo := min((page-1)*size, len(items))
	hi := min(page*size, len(items))
	pageItems := items[lo:hi]
	if pageItems == nil {
		pageItems = []Item{}
	}
	body, _ := json.Marshal(pageItems)

	link := f.linkHeader(r, parts, page, size, len(items))
	if link != "" {
		w.Header().Set("Link", link)
	}
	notModified := false
	if !f.DisableETag {
		sum := sha256.Sum256(body)
		etag := `"` + hex.EncodeToString(sum[:8]) + `"`
		if f.WeakETag {
			etag = "W/" + etag
		}
		w.Header().Set("ETag", etag)
		notModified = r.Header.Get("If-None-Match") == etag
	}
	if notModified {
		if !f.LinkOn304 {
			w.Header().Del("Link")
		}
		w.WriteHeader(http.StatusNotModified)
		return
	}
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	_, _ = w.Write(body)
}

// linkHeader builds the Link header of a page.
//
// Args: r is the request; parts are its path segments; page is the 1-based page number; size the page size; total
// the number of items.
// Returns: a Link value with rel="next" (when more items follow, or always when LinkCycle is set) and rel="last",
// or "" for a single page. Targets are absolute or relative according to RelativeLinks.
// Errors: never fails.
func (f *FakeGitHub) linkHeader(r *http.Request, parts []string, page, size, total int) string {
	lastPage := max((total+size-1)/size, 1)
	target := func(p int) string {
		path := "/" + strings.Join(parts, "/")
		query := fmt.Sprintf("state=open&sort=created&direction=asc&per_page=%d&page=%d", size, p)
		if f.RelativeLinks {
			return path + "?" + query
		}
		return f.URL + path + "?" + query
	}
	var links []string
	switch {
	case f.LinkCycle:
		links = append(links, fmt.Sprintf(`<%s>; rel="next"`, target(page)))
	case page < lastPage:
		links = append(links, fmt.Sprintf(`<%s>; rel="next"`, target(page+1)))
	}
	if lastPage > 1 {
		links = append(links, fmt.Sprintf(`<%s>; rel="last"`, target(lastPage)))
	}
	return strings.Join(links, ", ")
}
