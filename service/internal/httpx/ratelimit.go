package httpx

import (
	"bytes"
	"net/http"
	"strconv"
	"strings"
	"time"
)

// Rate-limit pause bounds.
const (
	DefaultRateLimitWait = 60 * time.Second // used when the server gives no hint
	MinResetWait         = time.Second      // floor for the wait derived from x-ratelimit-reset
	MaxRateLimitWait     = time.Hour        // ceiling for any single pause
)

// IsRateLimited tells whether a response reports an exhausted rate limit.
//
// Args: status is the HTTP status; h is the response header (may be nil); body is the response body (may be nil).
// Returns: true when status is 403 or 429 AND (the body contains "rate limit" in any case, OR a retry-after header is
// present, OR x-ratelimit-remaining equals "0" after trimming spaces).
// Errors: never fails.
func IsRateLimited(status int, h http.Header, body []byte) bool {
	if status != http.StatusForbidden && status != http.StatusTooManyRequests {
		return false
	}
	if bytes.Contains(bytes.ToLower(body), []byte("rate limit")) {
		return true
	}
	if len(h.Values("Retry-After")) > 0 {
		return true
	}
	return strings.TrimSpace(h.Get("X-Ratelimit-Remaining")) == "0"
}

// RateLimitWait computes the pause before retrying a rate-limited request.
//
// Args: h is the response header (may be nil); now is the current time from the injected clock.
// Returns: the retry-after value (whole seconds, or an HTTP date minus now) when valid; otherwise
// max(x-ratelimit-reset - now, MinResetWait) where the header is epoch seconds (UTC); otherwise
// DefaultRateLimitWait. A negative, non-numeric or past-date retry-after is ignored. The result never exceeds
// MaxRateLimitWait.
// Errors: never fails.
func RateLimitWait(h http.Header, now time.Time) time.Duration {
	if d, ok := parseRetryAfter(h.Get("Retry-After"), now); ok {
		return min(d, MaxRateLimitWait)
	}
	if v := strings.TrimSpace(h.Get("X-Ratelimit-Reset")); v != "" {
		if epoch, err := strconv.ParseInt(v, 10, 64); err == nil && epoch >= 0 {
			d := time.Unix(epoch, 0).Sub(now)
			return min(max(d, MinResetWait), MaxRateLimitWait)
		}
	}
	return DefaultRateLimitWait
}

// parseRetryAfter parses a retry-after value.
//
// Args: v is the raw header value; now is the current time.
// Returns: the duration and true for digits-only seconds (values above MaxRateLimitWait are clamped) or an HTTP date
// that is not before now; (0, false) for empty, negative, fractional, garbage and past-date values.
// Errors: never fails.
func parseRetryAfter(v string, now time.Time) (time.Duration, bool) {
	v = strings.TrimSpace(v)
	if v == "" {
		return 0, false
	}
	if isDigits(v) {
		secs, err := strconv.ParseInt(v, 10, 64)
		if err != nil || secs > int64(MaxRateLimitWait/time.Second) {
			return MaxRateLimitWait, true
		}
		return time.Duration(secs) * time.Second, true
	}
	if t, err := http.ParseTime(v); err == nil {
		if d := t.Sub(now); d >= 0 {
			return d, true
		}
	}
	return 0, false
}

// isDigits tells whether s consists only of ASCII digits.
//
// Args: s is any string.
// Returns: true for a non-empty all-digit string.
// Errors: never fails.
func isDigits(s string) bool {
	if s == "" {
		return false
	}
	for i := 0; i < len(s); i++ {
		if s[i] < '0' || s[i] > '9' {
			return false
		}
	}
	return true
}
