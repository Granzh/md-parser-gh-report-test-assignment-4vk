package httpx

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"ghreport/internal/clock"
	"ghreport/internal/trace"
)

const guardToken = "TOKEN-GUARD-9f3a"

// rig bundles a client with its fake clock and trace file.
type rig struct {
	c         *Client
	fake      *clock.Fake
	tracePath string
}

// recorder is a scripted handler that counts requests and keeps their headers.
type recorder struct {
	mu    sync.Mutex
	steps []http.HandlerFunc
	hdrs  []http.Header
	paths []string
}

// ServeHTTP records the request and runs the step for its ordinal (the last step repeats).
//
// Args: w and r are the usual handler arguments.
// Returns: nothing.
// Errors: never fails.
func (rc *recorder) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	rc.mu.Lock()
	n := len(rc.hdrs)
	rc.hdrs = append(rc.hdrs, r.Header.Clone())
	rc.paths = append(rc.paths, r.URL.RequestURI())
	step := rc.steps[min(n, len(rc.steps)-1)]
	rc.mu.Unlock()
	step(w, r)
}

// count returns the number of requests served.
//
// Args: none.
// Returns: the request count.
// Errors: never fails.
func (rc *recorder) count() int {
	rc.mu.Lock()
	defer rc.mu.Unlock()
	return len(rc.hdrs)
}

// status returns a step that answers with a status, optional headers (key/value pairs) and a body.
//
// Args: code is the status; body the response text; kv header names and values.
// Returns: the handler function.
// Errors: never fails.
func status(code int, body string, kv ...string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		for i := 0; i+1 < len(kv); i += 2 {
			w.Header().Set(kv[i], kv[i+1])
		}
		w.WriteHeader(code)
		_, _ = w.Write([]byte(body))
	}
}

// serve starts a test server for the steps.
//
// Args: t is the test; steps are the scripted handlers.
// Returns: the server (closed on cleanup) and its recorder.
// Errors: never fails.
func serve(t *testing.T, steps ...http.HandlerFunc) (*httptest.Server, *recorder) {
	t.Helper()
	rc := &recorder{steps: steps}
	srv := httptest.NewServer(rc)
	t.Cleanup(srv.Close)
	return srv, rc
}

// newRig creates a client for a server.
//
// Args: t is the test; baseURL is the server URL used as BaseOrigin; token is the bearer token or "".
// Returns: the rig with a fake clock and a trace file in a temp dir.
// Errors: never fails.
func newRig(t *testing.T, baseURL, token string) *rig {
	t.Helper()
	path := filepath.Join(t.TempDir(), "trace.jsonl")
	w, err := trace.New(path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = w.Close() })
	fake := clock.NewFake(epoch)
	return &rig{
		c:         &Client{Clock: fake, Trace: w, Token: token, BaseOrigin: baseURL, Timeout: 5 * time.Second},
		fake:      fake,
		tracePath: path,
	}
}

// records parses the trace file.
//
// Args: none.
// Returns: one map per line.
// Errors: never fails; calls t.Fatal on read or parse errors.
func (r *rig) records(t *testing.T) []map[string]any {
	t.Helper()
	b, err := os.ReadFile(r.tracePath)
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

// field projects a trace field over all records.
//
// Args: recs are parsed records; name is the field.
// Returns: the values in order (JSON numbers are float64).
// Errors: never fails.
func field(recs []map[string]any, name string) []any {
	var out []any
	for _, m := range recs {
		out = append(out, m[name])
	}
	return out
}

// secs converts seconds to durations.
//
// Args: n are seconds.
// Returns: the durations.
// Errors: never fails.
func secs(n ...int) []time.Duration {
	out := make([]time.Duration, len(n))
	for i, v := range n {
		out[i] = time.Duration(v) * time.Second
	}
	return out
}

// covers: Client.Get
func TestGetSendsAllHeaders(t *testing.T) {
	srv, rc := serve(t, status(200, "[]"))
	r := newRig(t, srv.URL, guardToken)
	resp, err := r.c.Get(context.Background(), srv.URL+"/repos/a/b/issues?state=open", `"etag-1"`)
	if err != nil {
		t.Fatal(err)
	}
	if resp.Status != 200 || string(resp.Body) != "[]" {
		t.Fatalf("got %+v", resp)
	}
	h := rc.hdrs[0]
	want := map[string]string{
		"Accept":               "application/vnd.github+json",
		"User-Agent":           DefaultUserAgent,
		"X-Github-Api-Version": "2026-03-10",
		"Authorization":        "Bearer " + guardToken,
		"If-None-Match":        `"etag-1"`,
	}
	for k, v := range want {
		if h.Get(k) != v {
			t.Errorf("%s = %q, want %q", k, h.Get(k), v)
		}
	}
	if rc.paths[0] != "/repos/a/b/issues?state=open" {
		t.Errorf("path %q", rc.paths[0])
	}
}

// covers: Client.Get
func TestGetOmitsOptionalHeaders(t *testing.T) {
	srv, rc := serve(t, status(200, "{}"))
	r := newRig(t, srv.URL, "")
	if _, err := r.c.Get(context.Background(), srv.URL+"/x", ""); err != nil {
		t.Fatal(err)
	}
	for _, k := range []string{"Authorization", "If-None-Match"} {
		if _, present := rc.hdrs[0][k]; present {
			t.Errorf("%s must be absent", k)
		}
	}
}

// covers: Client.Get
func TestGetCustomUserAgentAndVersion(t *testing.T) {
	srv, rc := serve(t, status(200, "{}"))
	r := newRig(t, srv.URL, "")
	r.c.UserAgent, r.c.APIVersion = "test-agent/1", "2030-01-01"
	if _, err := r.c.Get(context.Background(), srv.URL, ""); err != nil {
		t.Fatal(err)
	}
	if rc.hdrs[0].Get("User-Agent") != "test-agent/1" || rc.hdrs[0].Get("X-GitHub-Api-Version") != "2030-01-01" {
		t.Fatalf("headers %v", rc.hdrs[0])
	}
}

// covers: Client.Get
func TestGetTokenNotSentWhenOriginDiffers(t *testing.T) {
	srv, rc := serve(t, status(200, "{}"))
	r := newRig(t, "http://api.example.invalid", guardToken)
	if _, err := r.c.Get(context.Background(), srv.URL, ""); err != nil {
		t.Fatal(err)
	}
	if rc.hdrs[0].Get("Authorization") != "" {
		t.Fatal("token sent to a foreign origin")
	}
}

// covers: Client.Get
func TestGetRedirectToOtherOriginDropsToken(t *testing.T) {
	other, otherRC := serve(t, status(200, "[]"))
	srv, rc := serve(t, func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, other.URL+"/landing", http.StatusFound)
	})
	r := newRig(t, srv.URL, guardToken)
	resp, err := r.c.Get(context.Background(), srv.URL+"/start", "")
	if err != nil {
		t.Fatal(err)
	}
	if resp.Status != 200 {
		t.Fatalf("status %d", resp.Status)
	}
	if rc.hdrs[0].Get("Authorization") != "Bearer "+guardToken {
		t.Error("token must go to the base origin")
	}
	if otherRC.count() != 1 || otherRC.hdrs[0].Get("Authorization") != "" {
		t.Fatalf("token leaked to other origin: %v", otherRC.hdrs)
	}
	if otherRC.paths[0] != "/landing" {
		t.Errorf("path %q", otherRC.paths[0])
	}
}

// covers: Client.Get
func TestGetRedirectWithinOriginKeepsToken(t *testing.T) {
	srv, rc := serve(t, status(301, "", "Location", "/moved"), status(200, "ok"))
	r := newRig(t, srv.URL, guardToken)
	if _, err := r.c.Get(context.Background(), srv.URL+"/old", ""); err != nil {
		t.Fatal(err)
	}
	if rc.count() != 2 {
		t.Fatalf("requests: %d", rc.count())
	}
	for i, h := range rc.hdrs {
		if h.Get("Authorization") != "Bearer "+guardToken {
			t.Errorf("hop %d lacks the token", i)
		}
	}
}

// covers: Client.Get
func TestGetFollowsRedirectCodes(t *testing.T) {
	for _, code := range []int{301, 302, 307} {
		t.Run(strconv.Itoa(code), func(t *testing.T) {
			srv, rc := serve(t, status(code, "", "Location", "target?page=2"), status(200, "done"))
			r := newRig(t, srv.URL, "")
			resp, err := r.c.Get(context.Background(), srv.URL+"/dir/start", "")
			if err != nil || string(resp.Body) != "done" {
				t.Fatalf("got %+v, %v", resp, err)
			}
			if rc.paths[1] != "/dir/target?page=2" {
				t.Errorf("relative Location resolved to %q", rc.paths[1])
			}
			recs := r.records(t)
			if !reflect.DeepEqual(field(recs, "status"), []any{float64(code), float64(200)}) ||
				!reflect.DeepEqual(field(recs, "attempt"), []any{1.0, 1.0}) {
				t.Fatalf("trace: %v", recs)
			}
		})
	}
}

// covers: Client.Get
func TestGetRedirectLimit(t *testing.T) {
	// five redirects followed, then success: allowed
	steps := []http.HandlerFunc{}
	for i := 0; i < 5; i++ {
		steps = append(steps, status(302, "", "Location", "/next"))
	}
	steps = append(steps, status(200, "end"))
	srv, rc := serve(t, steps...)
	r := newRig(t, srv.URL, "")
	if resp, err := r.c.Get(context.Background(), srv.URL+"/", ""); err != nil || string(resp.Body) != "end" {
		t.Fatalf("5 redirects: %v", err)
	}
	if rc.count() != 6 {
		t.Fatalf("requests %d", rc.count())
	}
	// endless loop: 6 requests (1 + 5 hops), then an error, no retry
	srv2, rc2 := serve(t, status(302, "", "Location", "/again"))
	r2 := newRig(t, srv2.URL, "")
	_, err := r2.c.Get(context.Background(), srv2.URL+"/", "")
	if !errors.Is(err, ErrRequestFailed) {
		t.Fatalf("err = %v", err)
	}
	if rc2.count() != 6 || len(r2.fake.Sleeps()) != 0 {
		t.Fatalf("requests %d, sleeps %v", rc2.count(), r2.fake.Sleeps())
	}
	recs := r2.records(t)
	if len(recs) != 6 || !reflect.DeepEqual(field(recs, "attempt"), []any{1.0, 1.0, 1.0, 1.0, 1.0, 1.0}) {
		t.Fatalf("trace: %v", recs)
	}
}

// covers: Client.Get
func TestGetBadRedirectsAndOtherStatusesFail(t *testing.T) {
	cases := []struct {
		name string
		step http.HandlerFunc
	}{
		{"308", status(308, "", "Location", "/x")},
		{"303", status(303, "", "Location", "/x")},
		{"300", status(300, "", "Location", "/x")},
		{"301 without location", status(301, "")},
		{"301 ftp location", status(301, "", "Location", "ftp://h/x")},
		{"302 garbage location", status(302, "", "Location", "http://[bad")},
		{"204", status(204, "")},
		{"400", status(400, "bad")},
		{"403 plain", status(403, "Forbidden")},
		{"410", status(410, "")},
		{"422", status(422, "")},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv, rc := serve(t, tc.step)
			r := newRig(t, srv.URL, "")
			_, err := r.c.Get(context.Background(), srv.URL, "")
			if !errors.Is(err, ErrRequestFailed) || rc.count() != 1 || len(r.fake.Sleeps()) != 0 {
				t.Fatalf("err %v, requests %d, sleeps %v", err, rc.count(), r.fake.Sleeps())
			}
		})
	}
}

// covers: Client.Get
func TestGetUnauthorizedAndNotFound(t *testing.T) {
	for code, kind := range map[int]error{401: ErrUnauthorized, 404: ErrNotFound} {
		srv, rc := serve(t, status(code, `{"message":"x"}`))
		r := newRig(t, srv.URL, guardToken)
		_, err := r.c.Get(context.Background(), srv.URL, "")
		var re *RequestError
		if !errors.Is(err, kind) || !errors.As(err, &re) || re.Status != code {
			t.Fatalf("%d: err = %v", code, err)
		}
		if rc.count() != 1 || len(r.fake.Sleeps()) != 0 {
			t.Fatalf("%d: no retry expected: %d requests, sleeps %v", code, rc.count(), r.fake.Sleeps())
		}
	}
}

// covers: Client.Get
func TestGetServerErrorRetriesWithBackoff(t *testing.T) {
	srv, rc := serve(t, status(500, "boom "+guardToken))
	r := newRig(t, srv.URL, guardToken)
	_, err := r.c.Get(context.Background(), srv.URL, "")
	if !errors.Is(err, ErrRequestFailed) || strings.Contains(err.Error(), guardToken) {
		t.Fatalf("err = %v", err)
	}
	if rc.count() != 4 {
		t.Fatalf("requests = %d, want 4", rc.count())
	}
	if !reflect.DeepEqual(r.fake.Sleeps(), secs(1, 2, 4)) {
		t.Fatalf("sleeps = %v", r.fake.Sleeps())
	}
	recs := r.records(t)
	if !reflect.DeepEqual(field(recs, "attempt"), []any{1.0, 2.0, 3.0, 4.0}) || !reflect.DeepEqual(field(recs, "status"), []any{500.0, 500.0, 500.0, 500.0}) {
		t.Fatalf("trace: %v", recs)
	}
}

// covers: Client.Get
func TestGetSucceedsOnThirdAttempt(t *testing.T) {
	srv, rc := serve(t, status(500, ""), status(502, ""), status(200, "fine"))
	r := newRig(t, srv.URL, "")
	resp, err := r.c.Get(context.Background(), srv.URL, "")
	if err != nil || string(resp.Body) != "fine" {
		t.Fatalf("got %+v, %v", resp, err)
	}
	if rc.count() != 3 || !reflect.DeepEqual(r.fake.Sleeps(), secs(1, 2)) {
		t.Fatalf("requests %d, sleeps %v", rc.count(), r.fake.Sleeps())
	}
	if !reflect.DeepEqual(field(r.records(t), "attempt"), []any{1.0, 2.0, 3.0}) {
		t.Fatalf("trace: %v", r.records(t))
	}
}

// covers: Client.Get
func TestGetRateLimitRetryAfter(t *testing.T) {
	srv, rc := serve(t, status(429, "slow down", "Retry-After", "7"), status(200, "ok"))
	r := newRig(t, srv.URL, "")
	if _, err := r.c.Get(context.Background(), srv.URL, ""); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(r.fake.Sleeps(), secs(7)) || rc.count() != 2 {
		t.Fatalf("sleeps %v, requests %d", r.fake.Sleeps(), rc.count())
	}
	if !reflect.DeepEqual(field(r.records(t), "attempt"), []any{1.0, 2.0}) {
		t.Fatalf("trace: %v", r.records(t))
	}
}

// covers: Client.Get
func TestGetRateLimitResetHeader(t *testing.T) {
	reset := strconv.FormatInt(epoch.Add(30*time.Second).Unix(), 10)
	srv, _ := serve(t, status(403, "", "X-RateLimit-Remaining", "0", "X-RateLimit-Reset", reset), status(200, "ok"))
	r := newRig(t, srv.URL, "")
	if _, err := r.c.Get(context.Background(), srv.URL, ""); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(r.fake.Sleeps(), secs(30)) {
		t.Fatalf("sleeps %v", r.fake.Sleeps())
	}
}

// covers: Client.Get
func TestGetRateLimitBodyOnlyWaitsDefault(t *testing.T) {
	srv, _ := serve(t, status(403, `{"message":"API rate limit exceeded for 1.2.3.4"}`), status(200, "ok"))
	r := newRig(t, srv.URL, "")
	if _, err := r.c.Get(context.Background(), srv.URL, ""); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(r.fake.Sleeps(), secs(60)) {
		t.Fatalf("sleeps %v", r.fake.Sleeps())
	}
}

// covers: Client.Get
func TestGetPlain403DoesNotRetry(t *testing.T) {
	srv, rc := serve(t, status(403, `{"message":"Resource not accessible"}`))
	r := newRig(t, srv.URL, "")
	_, err := r.c.Get(context.Background(), srv.URL, "")
	if !errors.Is(err, ErrRequestFailed) || errors.Is(err, ErrRateLimited) || rc.count() != 1 || len(r.fake.Sleeps()) != 0 {
		t.Fatalf("err %v, requests %d, sleeps %v", err, rc.count(), r.fake.Sleeps())
	}
}

// covers: Client.Get
func TestGetRateLimitGivesUpAfterThreeRetries(t *testing.T) {
	srv, rc := serve(t, status(429, "", "Retry-After", "2"))
	r := newRig(t, srv.URL, "")
	_, err := r.c.Get(context.Background(), srv.URL, "")
	if !errors.Is(err, ErrRateLimited) {
		t.Fatalf("err = %v", err)
	}
	if rc.count() != 4 || !reflect.DeepEqual(r.fake.Sleeps(), secs(2, 2, 2)) {
		t.Fatalf("requests %d, sleeps %v", rc.count(), r.fake.Sleeps())
	}
}

// covers: Client.Get
func TestGetAttemptCountsBothRetryKinds(t *testing.T) {
	srv, _ := serve(t, status(429, "", "Retry-After", "3"), status(503, ""), status(200, "ok"))
	r := newRig(t, srv.URL, "")
	if _, err := r.c.Get(context.Background(), srv.URL, ""); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(r.fake.Sleeps(), secs(3, 1)) || !reflect.DeepEqual(field(r.records(t), "attempt"), []any{1.0, 2.0, 3.0}) {
		t.Fatalf("sleeps %v, trace %v", r.fake.Sleeps(), r.records(t))
	}
}

// covers: Client.Get
func TestGetTimeoutIsTracedAndRetried(t *testing.T) {
	srv, rc := serve(t, func(w http.ResponseWriter, r *http.Request) { <-r.Context().Done() })
	r := newRig(t, srv.URL, guardToken)
	r.c.Timeout = 20 * time.Millisecond
	_, err := r.c.Get(context.Background(), srv.URL, "")
	if !errors.Is(err, ErrRequestFailed) || strings.Contains(err.Error(), guardToken) {
		t.Fatalf("err = %v", err)
	}
	if rc.count() != 4 || !reflect.DeepEqual(r.fake.Sleeps(), secs(1, 2, 4)) {
		t.Fatalf("requests %d, sleeps %v", rc.count(), r.fake.Sleeps())
	}
	recs := r.records(t)
	if !reflect.DeepEqual(field(recs, "error"), []any{"timeout", "timeout", "timeout", "timeout"}) ||
		!reflect.DeepEqual(field(recs, "status"), []any{0.0, 0.0, 0.0, 0.0}) {
		t.Fatalf("trace: %v", recs)
	}
}

// covers: Client.Get
func TestGetConnectionRefusedIsNetworkError(t *testing.T) {
	srv, _ := serve(t, status(200, ""))
	addr := srv.URL
	srv.Close()
	r := newRig(t, addr, guardToken)
	_, err := r.c.Get(context.Background(), addr+"/x", "")
	if !errors.Is(err, ErrRequestFailed) || strings.Contains(err.Error(), guardToken) {
		t.Fatalf("err = %v", err)
	}
	recs := r.records(t)
	if len(recs) != 4 || !reflect.DeepEqual(r.fake.Sleeps(), secs(1, 2, 4)) {
		t.Fatalf("trace %v, sleeps %v", recs, r.fake.Sleeps())
	}
	for _, rec := range recs {
		if rec["error"] != "network" || rec["status"] != 0.0 || rec["from_cache"] != false {
			t.Fatalf("record %v", rec)
		}
	}
}

// covers: Client.Get
func TestGetFromCacheFlag(t *testing.T) {
	cases := []struct {
		name   string
		inm    string
		status int
		want   bool
	}{
		{"304 with etag", `"e"`, 304, true},
		{"304 without etag", "", 304, false},
		{"200 with etag", `"e"`, 200, false},
		{"200 without etag", "", 200, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv, _ := serve(t, status(tc.status, ""))
			r := newRig(t, srv.URL, "")
			resp, err := r.c.Get(context.Background(), srv.URL, tc.inm)
			if err != nil || resp.Status != tc.status {
				t.Fatalf("got %+v, %v", resp, err)
			}
			recs := r.records(t)
			if len(recs) != 1 || recs[0]["from_cache"] != tc.want || recs[0]["error"] != nil || recs[0]["method"] != "GET" {
				t.Fatalf("trace: %v", recs)
			}
			if ts, _ := recs[0]["ts"].(string); ts != "2026-10-14T09:00:00Z" {
				t.Fatalf("ts = %q", ts)
			}
		})
	}
}

// covers: Client.Get
func TestGetContextCancellation(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	srv, rc := serve(t, func(w http.ResponseWriter, r *http.Request) {
		cancel()
		w.WriteHeader(500)
	})
	r := newRig(t, srv.URL, "")
	_, err := r.c.Get(ctx, srv.URL, "")
	if !errors.Is(err, context.Canceled) || rc.count() != 1 || len(r.fake.Sleeps()) != 0 {
		t.Fatalf("err %v, requests %d, sleeps %v", err, rc.count(), r.fake.Sleeps())
	}
	// cancelled before the request
	srv2, rc2 := serve(t, status(200, ""))
	r2 := newRig(t, srv2.URL, "")
	pre, cancel2 := context.WithCancel(context.Background())
	cancel2()
	if _, err := r2.c.Get(pre, srv2.URL, ""); !errors.Is(err, context.Canceled) || rc2.count() != 0 {
		t.Fatalf("err %v, requests %d", err, rc2.count())
	}
	if recs := r2.records(t); len(recs) != 1 || recs[0]["error"] != "canceled" {
		t.Fatalf("trace %v", recs)
	}
}

// covers: Client.Get
func TestGetBodySizeLimit(t *testing.T) {
	srv, rc := serve(t, status(200, strings.Repeat("x", 11)))
	r := newRig(t, srv.URL, "")
	r.c.MaxBodySize = 10
	_, err := r.c.Get(context.Background(), srv.URL, "")
	if !errors.Is(err, ErrRequestFailed) || rc.count() != 1 {
		t.Fatalf("err %v, requests %d", err, rc.count())
	}
	srv2, _ := serve(t, status(200, strings.Repeat("x", 10)))
	r2 := newRig(t, srv2.URL, "")
	r2.c.MaxBodySize = 10
	if resp, err := r2.c.Get(context.Background(), srv2.URL, ""); err != nil || len(resp.Body) != 10 {
		t.Fatalf("exact limit: %v", err)
	}
}

// covers: Client.Get
func TestGetRejectsBadURL(t *testing.T) {
	r := newRig(t, "http://h", "")
	for _, u := range []string{"", "ftp://h/x", "/relative", "http://", "http://[bad", "://x"} {
		if _, err := r.c.Get(context.Background(), u, ""); !errors.Is(err, ErrRequestFailed) {
			t.Errorf("Get(%q) err = %v", u, err)
		}
	}
	if len(r.records(t)) != 0 || len(r.fake.Sleeps()) != 0 {
		t.Fatal("bad URLs must not produce attempts")
	}
}

// covers: Client.Get
func TestGetTokenNeverInTraceOrErrors(t *testing.T) {
	scenarios := []http.HandlerFunc{
		status(500, guardToken), status(401, guardToken), status(404, guardToken),
		status(403, guardToken), status(429, guardToken, "Retry-After", "1"), status(302, "", "Location", "/x?t="+guardToken),
	}
	for i, step := range scenarios {
		srv, _ := serve(t, step)
		r := newRig(t, srv.URL, guardToken)
		_, err := r.c.Get(context.Background(), srv.URL+"/p", "")
		if err == nil {
			t.Fatalf("%d: want error", i)
		}
		if strings.Contains(err.Error(), guardToken) {
			t.Errorf("%d: token in error %q", i, err)
		}
		b, _ := os.ReadFile(r.tracePath)
		low := strings.ToLower(string(b))
		if strings.Contains(string(b), guardToken[:10]) && i != 5 || strings.Contains(low, "authorization") || strings.Contains(low, "bearer") {
			t.Errorf("%d: trace leak: %s", i, b)
		}
	}
}

// covers: RequestError.Error
func TestRequestErrorError(t *testing.T) {
	if got := (&RequestError{Kind: ErrNotFound}).Error(); got != ErrNotFound.Error() {
		t.Fatalf("got %q", got)
	}
	if got := (&RequestError{Kind: ErrRequestFailed, Detail: "d"}).Error(); got != ErrRequestFailed.Error()+": d" {
		t.Fatalf("got %q", got)
	}
}

// covers: RequestError.Unwrap
func TestRequestErrorUnwrap(t *testing.T) {
	e := &RequestError{Kind: ErrRateLimited}
	if e.Unwrap() != ErrRateLimited || !errors.Is(e, ErrRateLimited) || errors.Is(e, ErrNotFound) {
		t.Fatal("Unwrap/Is mismatch")
	}
}

// covers: Origin
func TestOrigin(t *testing.T) {
	cases := map[string]string{
		"https://api.github.com/repos":     "https://api.github.com",
		"HTTPS://API.GitHub.com":           "https://api.github.com",
		"https://api.github.com:443/x":     "https://api.github.com",
		"http://h:80":                      "http://h",
		"http://h:8080/p":                  "http://h:8080",
		"https://h:80":                     "https://h:80",
		"http://u:p@h/x":                   "http://h",
		"http://[::1]:9/x":                 "http://[::1]:9",
		"http://[::1]/x":                   "http://[::1]",
		"http://127.0.0.1:1234/a?b=c#frag": "http://127.0.0.1:1234",
	}
	for in, want := range cases {
		u, err := url.Parse(in)
		if err != nil {
			t.Fatal(err)
		}
		if got := Origin(u); got != want {
			t.Errorf("Origin(%q) = %q, want %q", in, got, want)
		}
	}
}

// covers: Client.sameOrigin
func TestSameOrigin(t *testing.T) {
	cases := []struct {
		base, target string
		want         bool
	}{
		{"https://api.github.com", "https://api.github.com/x", true},
		{"https://api.github.com", "https://API.github.com:443/x", true},
		{"https://api.github.com", "http://api.github.com/x", false},
		{"https://api.github.com", "https://api.github.com:8443/x", false},
		{"https://api.github.com", "https://evil.example/x", false},
		{"https://api.github.com", "https://api.github.com.evil.example/x", false},
		{"", "https://api.github.com/x", false},
		{"not a url", "https://api.github.com/x", false},
		{"http://[bad", "https://api.github.com/x", false},
	}
	for _, tc := range cases {
		c := &Client{BaseOrigin: tc.base}
		u, _ := url.Parse(tc.target)
		if got := c.sameOrigin(u); got != tc.want {
			t.Errorf("sameOrigin(%q, %q) = %v", tc.base, tc.target, got)
		}
	}
}

// covers: Client.setHeaders
func TestSetHeaders(t *testing.T) {
	c := &Client{Token: "tok", BaseOrigin: "https://h"}
	req, _ := http.NewRequest("GET", "https://h/x", nil)
	c.setHeaders(req, "")
	if req.Header.Get("Authorization") != "Bearer tok" || req.Header.Get("If-None-Match") != "" || req.Header.Get("Accept") == "" {
		t.Fatalf("headers %v", req.Header)
	}
	req2, _ := http.NewRequest("GET", "https://other/x", nil)
	c.setHeaders(req2, `W/"e"`)
	if req2.Header.Get("Authorization") != "" || req2.Header.Get("If-None-Match") != `W/"e"` {
		t.Fatalf("headers %v", req2.Header)
	}
}

// covers: Client.redact
func TestRedact(t *testing.T) {
	c := &Client{Token: "sekret"}
	if got := c.redact("a sekret b sekret"); got != "a *** b ***" {
		t.Fatalf("got %q", got)
	}
	if got := (&Client{}).redact("keep"); got != "keep" {
		t.Fatalf("no token: %q", got)
	}
}

// covers: Client.newError
func TestNewError(t *testing.T) {
	c := &Client{Token: "sekret"}
	e := c.newError(ErrNotFound, 404, "  has sekret inside ")
	if e.Kind != ErrNotFound || e.Status != 404 || e.Detail != "has *** inside" {
		t.Fatalf("got %+v", e)
	}
}

// covers: Client.decide
func TestDecide(t *testing.T) {
	c := &Client{Clock: clock.NewFake(epoch)}
	var st retryState
	resp, wait, err := c.decide(outcome{status: 200, body: []byte("b")}, &st)
	if resp == nil || resp.Status != 200 || wait != 0 || err != nil {
		t.Fatalf("200: %v %v %v", resp, wait, err)
	}
	_, wait, err = c.decide(outcome{status: 500}, &st)
	if wait != time.Second || err != nil || st.failures != 1 {
		t.Fatalf("500: %v %v %+v", wait, err, st)
	}
	_, wait, err = c.decide(outcome{status: 429, header: hdr("Retry-After", "9")}, &st)
	if wait != 9*time.Second || err != nil || st.limits != 1 {
		t.Fatalf("429: %v %v %+v", wait, err, st)
	}
	_, _, err = c.decide(outcome{err: context.Canceled}, &st)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("err outcome: %v", err)
	}
	_, _, err = c.decide(outcome{failure: "timeout", detail: "x"}, &retryState{failures: MaxRetries})
	if !errors.Is(err, ErrRequestFailed) {
		t.Fatalf("exhausted: %v", err)
	}
}

// covers: Client.retryFailure
func TestRetryFailure(t *testing.T) {
	c := &Client{}
	var st retryState
	for i, want := range secs(1, 2, 4) {
		resp, wait, err := c.retryFailure(&st, 500, "d")
		if resp != nil || err != nil || wait != want {
			t.Fatalf("call %d: %v %v %v", i, resp, wait, err)
		}
	}
	_, wait, err := c.retryFailure(&st, 502, "last")
	var re *RequestError
	if wait != 0 || !errors.As(err, &re) || re.Status != 502 || !errors.Is(err, ErrRequestFailed) {
		t.Fatalf("4th call: %v %v", wait, err)
	}
}

// covers: Client.fetch
func TestFetchReportsOutcome(t *testing.T) {
	srv, _ := serve(t, status(404, "nope", "X-Test", "1"))
	r := newRig(t, srv.URL, "")
	o := r.c.fetch(context.Background(), srv.URL, "", 3)
	if o.status != 404 || string(o.body) != "nope" || o.header.Get("X-Test") != "1" || o.err != nil || o.failure != "" {
		t.Fatalf("got %+v", o)
	}
	if recs := r.records(t); len(recs) != 1 || recs[0]["attempt"] != 3.0 {
		t.Fatalf("trace %v", recs)
	}
}

// covers: Client.networkOutcome
func TestNetworkOutcome(t *testing.T) {
	r := newRig(t, "http://h", "tok")
	uerr := &url.Error{Op: "Get", URL: "http://h/?t=tok", Err: errors.New("dial tcp: tok refused")}
	o := r.c.networkOutcome(context.Background(), "http://h/", 2, 0, uerr)
	if o.failure != "network" || strings.Contains(o.detail, "tok") || strings.Contains(o.detail, "http://") {
		t.Fatalf("got %+v", o)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	o = r.c.networkOutcome(ctx, "http://h/", 1, 0, uerr)
	if !errors.Is(o.err, context.Canceled) || o.failure != "" {
		t.Fatalf("cancel: %+v", o)
	}
	recs := r.records(t)
	if recs[0]["error"] != "network" || recs[1]["error"] != "canceled" {
		t.Fatalf("trace %v", recs)
	}
}

// timeoutErr is a net.Error that reports a timeout.
type timeoutErr struct{}

// Error returns a fixed text.
//
// Args: none.
// Returns: "timeout".
// Errors: never fails.
func (timeoutErr) Error() string { return "timeout" }

// Timeout reports true.
//
// Args: none.
// Returns: true.
// Errors: never fails.
func (timeoutErr) Timeout() bool { return true }

// Temporary reports false.
//
// Args: none.
// Returns: false.
// Errors: never fails.
func (timeoutErr) Temporary() bool { return false }

// covers: classify
func TestClassify(t *testing.T) {
	live := context.Background()
	done, cancel := context.WithCancel(live)
	cancel()
	cases := []struct {
		ctx  context.Context
		err  error
		want string
	}{
		{live, context.DeadlineExceeded, "timeout"},
		{live, &url.Error{Op: "Get", Err: context.DeadlineExceeded}, "timeout"},
		{live, timeoutErr{}, "timeout"},
		{live, errors.New("connection refused"), "network"},
		{live, context.Canceled, "network"},
		{done, context.Canceled, "canceled"},
		{done, timeoutErr{}, "canceled"},
	}
	for i, tc := range cases {
		if got := classify(tc.ctx, tc.err); got != tc.want {
			t.Errorf("case %d: %q, want %q", i, got, tc.want)
		}
	}
}

// covers: readLimited
func TestReadLimited(t *testing.T) {
	if b, err := readLimited(strings.NewReader("abc"), 3); err != nil || string(b) != "abc" {
		t.Fatalf("exact: %q %v", b, err)
	}
	if _, err := readLimited(strings.NewReader("abcd"), 3); !errors.Is(err, errTooLarge) {
		t.Fatalf("over: %v", err)
	}
	if b, err := readLimited(strings.NewReader(""), 3); err != nil || len(b) != 0 {
		t.Fatalf("empty: %q %v", b, err)
	}
	if _, err := readLimited(errReader{}, 3); err == nil || errors.Is(err, errTooLarge) {
		t.Fatalf("read error: %v", err)
	}
}

// errReader always fails.
type errReader struct{}

// Read fails immediately.
//
// Args: p is ignored.
// Returns: (0, error).
// Errors: always.
func (errReader) Read(p []byte) (int, error) { return 0, errors.New("broken") }

// covers: isFollowedRedirect
func TestIsFollowedRedirect(t *testing.T) {
	for code := 100; code < 600; code++ {
		want := code == 301 || code == 302 || code == 307
		if isFollowedRedirect(code) != want {
			t.Errorf("isFollowedRedirect(%d) = %v", code, !want)
		}
	}
}

// covers: resolveRedirect
func TestResolveRedirect(t *testing.T) {
	from, _ := url.Parse("https://h/a/b?x=1")
	cases := []struct {
		loc  string
		want string
		ok   bool
	}{
		{"/c", "https://h/c", true},
		{"d", "https://h/a/d", true},
		{"?y=2", "https://h/a/b?y=2", true},
		{"http://other/z", "http://other/z", true},
		{" https://h/trim ", "https://h/trim", true},
		{"", "", false},
		{"   ", "", false},
		{"ftp://h/x", "", false},
		{"mailto:x@y", "", false},
		{"http://[bad", "", false},
	}
	for _, tc := range cases {
		got, ok := resolveRedirect(from, tc.loc)
		if got != tc.want || ok != tc.ok {
			t.Errorf("resolveRedirect(%q) = (%q, %v)", tc.loc, got, ok)
		}
	}
}

// covers: Client.log
func TestLog(t *testing.T) {
	r := newRig(t, "http://h", "")
	r.c.log("http://u:p@h/x", 304, 2, true, "")
	r.c.log("http://h/y", 0, 1, false, trace.ErrTimeout)
	recs := r.records(t)
	if recs[0]["url"] != "http://h/x" || recs[0]["from_cache"] != true || recs[0]["attempt"] != 2.0 || recs[0]["error"] != nil {
		t.Fatalf("first %v", recs[0])
	}
	if recs[1]["error"] != "timeout" || recs[1]["status"] != 0.0 {
		t.Fatalf("second %v", recs[1])
	}
	(&Client{}).log("http://h", 200, 1, false, "") // nil Trace and nil Clock must not panic
}

// covers: Client.transport
func TestClientTransport(t *testing.T) {
	rt := &http.Transport{}
	if (&Client{HTTP: &http.Client{Transport: rt}}).transport() != http.RoundTripper(rt) {
		t.Fatal("configured transport ignored")
	}
	if (&Client{HTTP: &http.Client{}}).transport() != http.DefaultTransport || (&Client{}).transport() != http.DefaultTransport {
		t.Fatal("default transport expected")
	}
}

// covers: Client.clock
func TestClientClock(t *testing.T) {
	if _, ok := (&Client{}).clock().(clock.Real); !ok {
		t.Fatal("default must be the real clock")
	}
	f := clock.NewFake(epoch)
	if (&Client{Clock: f}).clock() != clock.Clock(f) {
		t.Fatal("configured clock ignored")
	}
}

// covers: Client.timeout
func TestClientTimeout(t *testing.T) {
	if (&Client{}).timeout() != 30*time.Second || (&Client{Timeout: -1}).timeout() != DefaultTimeout || (&Client{Timeout: time.Second}).timeout() != time.Second {
		t.Fatal("timeout selection wrong")
	}
}

// covers: Client.maxBody
func TestClientMaxBody(t *testing.T) {
	if (&Client{}).maxBody() != 64<<20 || (&Client{MaxBodySize: -5}).maxBody() != DefaultMaxBody || (&Client{MaxBodySize: 7}).maxBody() != 7 {
		t.Fatal("limit selection wrong")
	}
}

// covers: orDefault
func TestOrDefault(t *testing.T) {
	if orDefault("", "d") != "d" || orDefault("v", "d") != "v" {
		t.Fatal("orDefault wrong")
	}
}
