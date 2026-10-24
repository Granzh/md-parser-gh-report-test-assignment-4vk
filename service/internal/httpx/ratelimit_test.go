package httpx

import (
	"net/http"
	"strconv"
	"testing"
	"time"
)

var epoch = time.Date(2026, 10, 14, 9, 0, 0, 0, time.UTC)

// hdr builds a header from key/value pairs.
//
// Args: kv is an even-length list of header names and values.
// Returns: the header with canonicalized names.
// Errors: never fails.
func hdr(kv ...string) http.Header {
	h := http.Header{}
	for i := 0; i+1 < len(kv); i += 2 {
		h.Add(kv[i], kv[i+1])
	}
	return h
}

// covers: IsRateLimited
func TestIsRateLimitedTable(t *testing.T) {
	cases := []struct {
		name   string
		status int
		h      http.Header
		body   string
		want   bool
	}{
		{"403 body lower", 403, nil, "api rate limit exceeded", true},
		{"403 body mixed case", 403, nil, "API Rate Limit Exceeded for user", true},
		{"429 body", 429, nil, "You have exceeded a secondary rate limit", true},
		{"403 retry-after", 403, hdr("retry-after", "10"), "", true},
		{"429 retry-after", 429, hdr("Retry-After", "1"), "{}", true},
		{"403 retry-after empty value", 403, hdr("Retry-After", ""), "", true},
		{"403 remaining zero", 403, hdr("x-ratelimit-remaining", "0"), "", true},
		{"403 remaining zero spaces", 403, hdr("X-RateLimit-Remaining", " 0 "), "", true},
		{"403 remaining nonzero", 403, hdr("x-ratelimit-remaining", "5"), "", false},
		{"403 remaining 00", 403, hdr("x-ratelimit-remaining", "00"), "", false},
		{"403 plain", 403, nil, "Forbidden", false},
		{"403 empty header and body", 403, http.Header{}, "", false},
		{"403 word ratelimit without space", 403, nil, "ratelimit", false},
		{"429 no signs", 429, nil, "", false},
		{"200 with signs", 200, hdr("retry-after", "1", "x-ratelimit-remaining", "0"), "rate limit", false},
		{"404 with signs", 404, hdr("retry-after", "1"), "rate limit", false},
		{"500 with signs", 500, hdr("retry-after", "1"), "rate limit", false},
		{"401 with signs", 401, hdr("retry-after", "1"), "rate limit", false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := IsRateLimited(tc.status, tc.h, []byte(tc.body)); got != tc.want {
				t.Fatalf("got %v, want %v", got, tc.want)
			}
		})
	}
}

// covers: RateLimitWait
func TestRateLimitWaitTable(t *testing.T) {
	at := func(d time.Duration) string { return epochString(epoch.Add(d)) }
	date := func(d time.Duration) string { return epoch.Add(d).UTC().Format(http.TimeFormat) }
	cases := []struct {
		name string
		h    http.Header
		want time.Duration
	}{
		{"nil header", nil, 60 * time.Second},
		{"empty header", http.Header{}, 60 * time.Second},
		{"retry-after seconds", hdr("retry-after", "7"), 7 * time.Second},
		{"retry-after zero", hdr("retry-after", "0"), 0},
		{"retry-after spaces", hdr("retry-after", " 12 "), 12 * time.Second},
		{"retry-after date", hdr("retry-after", date(90*time.Second)), 90 * time.Second},
		{"retry-after date now", hdr("retry-after", date(0)), 0},
		{"retry-after past date falls to default", hdr("retry-after", date(-time.Minute)), 60 * time.Second},
		{"retry-after past date falls to reset", hdr("retry-after", date(-time.Minute), "x-ratelimit-reset", at(20*time.Second)), 20 * time.Second},
		{"retry-after negative ignored", hdr("retry-after", "-5"), 60 * time.Second},
		{"retry-after negative uses reset", hdr("retry-after", "-5", "x-ratelimit-reset", at(15*time.Second)), 15 * time.Second},
		{"retry-after garbage ignored", hdr("retry-after", "soon"), 60 * time.Second},
		{"retry-after fraction ignored", hdr("retry-after", "1.5"), 60 * time.Second},
		{"retry-after plus ignored", hdr("retry-after", "+5"), 60 * time.Second},
		{"retry-after wins over reset", hdr("retry-after", "3", "x-ratelimit-reset", at(100*time.Second)), 3 * time.Second},
		{"retry-after huge clamped", hdr("retry-after", "99999999999999999999999"), time.Hour},
		{"retry-after over cap clamped", hdr("retry-after", "7200"), time.Hour},
		{"reset in future", hdr("x-ratelimit-reset", at(42*time.Second)), 42 * time.Second},
		{"reset spaces", hdr("x-ratelimit-reset", " "+at(42*time.Second)+" "), 42 * time.Second},
		{"reset now floors to 1s", hdr("x-ratelimit-reset", at(0)), time.Second},
		{"reset in past floors to 1s", hdr("x-ratelimit-reset", at(-time.Hour)), time.Second},
		{"reset half second ahead floors", hdr("x-ratelimit-reset", at(0)), time.Second},
		{"reset far future clamped", hdr("x-ratelimit-reset", at(48*time.Hour)), time.Hour},
		{"reset garbage", hdr("x-ratelimit-reset", "tomorrow"), 60 * time.Second},
		{"reset negative", hdr("x-ratelimit-reset", "-1"), 60 * time.Second},
		{"reset float", hdr("x-ratelimit-reset", "1.5e9"), 60 * time.Second},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := RateLimitWait(tc.h, epoch); got != tc.want {
				t.Fatalf("got %v, want %v", got, tc.want)
			}
		})
	}
	// sub-second now: reset 0.5s ahead rounds up to the 1s floor
	now := epoch.Add(500 * time.Millisecond)
	if got := RateLimitWait(hdr("x-ratelimit-reset", epochString(epoch.Add(time.Second))), now); got != time.Second {
		t.Fatalf("sub-second: got %v", got)
	}
}

// epochString renders a time as epoch seconds.
//
// Args: t is any time.
// Returns: decimal Unix seconds.
// Errors: never fails.
func epochString(t time.Time) string {
	return strconv.FormatInt(t.Unix(), 10)
}

// covers: epochString
func TestEpochString(t *testing.T) {
	if got := epochString(time.Unix(1700000000, 999)); got != "1700000000" {
		t.Fatalf("got %q", got)
	}
}

// covers: hdr
func TestHdr(t *testing.T) {
	h := hdr("retry-after", "1", "X-Test", "a", "x-test", "b", "odd")
	if h.Get("Retry-After") != "1" || len(h.Values("X-Test")) != 2 || len(h) != 2 {
		t.Fatalf("got %v", h)
	}
}

// covers: parseRetryAfter
func TestParseRetryAfter(t *testing.T) {
	cases := []struct {
		in   string
		want time.Duration
		ok   bool
	}{
		{"0", 0, true}, {"5", 5 * time.Second, true}, {"3600", time.Hour, true}, {"3601", time.Hour, true},
		{"", 0, false}, {"  ", 0, false}, {"-1", 0, false}, {"1s", 0, false}, {"1,5", 0, false}, {"٣", 0, false},
		{epoch.Add(5 * time.Second).Format(http.TimeFormat), 5 * time.Second, true},
		{epoch.Add(-time.Second).Format(http.TimeFormat), 0, false},
	}
	for _, tc := range cases {
		got, ok := parseRetryAfter(tc.in, epoch)
		if got != tc.want || ok != tc.ok {
			t.Errorf("parseRetryAfter(%q) = (%v, %v), want (%v, %v)", tc.in, got, ok, tc.want, tc.ok)
		}
	}
}

// covers: isDigits
func TestIsDigits(t *testing.T) {
	for s, want := range map[string]bool{"": false, "0": true, "0123456789": true, "-1": false, "1 ": false, "1a": false, "٣": false} {
		if got := isDigits(s); got != want {
			t.Errorf("isDigits(%q) = %v", s, got)
		}
	}
}
