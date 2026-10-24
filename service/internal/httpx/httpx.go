// Package httpx performs GET requests against the GitHub API with redirects, retries and tracing.
package httpx

import (
	"context"
	"errors"
	"io"
	"net"
	"net/http"
	"net/url"
	"strings"
	"time"

	"ghreport/internal/clock"
	"ghreport/internal/model"
	"ghreport/internal/trace"
)

// Request defaults.
const (
	DefaultUserAgent  = "ghreport/1.0 (+учебный проект; read-only сводка issue)"
	DefaultAPIVersion = "2026-03-10"
	DefaultTimeout    = 30 * time.Second
	DefaultMaxBody    = 64 << 20 // 64 MiB
	MaxRedirects      = 5
	MaxRetries        = 3 // retries after the first attempt, for each of the two retry kinds
)

// retryDelays are the pauses before the 1st, 2nd and 3rd retry after a network error or 5xx.
var retryDelays = [MaxRetries]time.Duration{time.Second, 2 * time.Second, 4 * time.Second}

// Sentinel error kinds; use errors.Is on the error returned by Get.
var (
	ErrUnauthorized  = errors.New("ghreport: ответ 401, токен недействителен")
	ErrNotFound      = errors.New("ghreport: ответ 404, ресурс не найден")
	ErrRateLimited   = errors.New("ghreport: исчерпан лимит запросов")
	ErrRequestFailed = errors.New("ghreport: запрос не удался")
)

// RequestError is the error type returned by Get; errors.Is matches its Kind.
type RequestError struct {
	Kind   error  // one of the sentinel errors
	Status int    // last HTTP status, 0 if none
	Detail string // short explanation without secrets
}

// Error renders the kind and the detail.
//
// Args: none.
// Returns: "<kind>" or "<kind>: <detail>".
// Errors: never fails.
func (e *RequestError) Error() string {
	if e.Detail == "" {
		return e.Kind.Error()
	}
	return e.Kind.Error() + ": " + e.Detail
}

// Unwrap exposes the sentinel kind to errors.Is.
//
// Args: none.
// Returns: e.Kind.
// Errors: never fails.
func (e *RequestError) Unwrap() error {
	return e.Kind
}

// Response is a successful (200 or 304) answer.
type Response struct {
	Status int
	Header http.Header
	Body   []byte
}

// Client sends GET requests. Requests are sequential; a Client is not meant for concurrent use.
type Client struct {
	HTTP        *http.Client // only its Transport is used (nil means http.DefaultTransport); redirects are never automatic
	Clock       clock.Clock  // nil means the real clock
	Trace       *trace.Writer
	Token       string        // empty means no Authorization header
	BaseOrigin  string        // "scheme://host[:port]" of the API base URL; the token is sent only there
	UserAgent   string        // empty means DefaultUserAgent
	APIVersion  string        // empty means DefaultAPIVersion
	Timeout     time.Duration // per HTTP request; 0 means DefaultTimeout
	MaxBodySize int64         // 0 means DefaultMaxBody
}

// outcome is the result of one logical attempt (a request plus the redirects it followed).
type outcome struct {
	status  int
	header  http.Header
	body    []byte
	failure string // "" or a trace error description when no usable response arrived
	detail  string
	err     error // non-retryable error
}

// retryState counts the retries already spent.
type retryState struct {
	failures int
	limits   int
}

// Get fetches a URL, following redirects and retrying where the spec requires.
//
// Args: ctx cancels pauses and requests, must not be nil; rawURL is an absolute http(s) URL, also the cache key and
// the URL of the first hop; ifNoneMatch is an ETag to send as If-None-Match, "" for none.
// Returns: the Response for status 200 or 304 (Status is the final one after redirects). Every HTTP attempt and
// every redirect hop is traced; the attempt number grows by one per retry of this logical request.
// Errors: *RequestError with Kind ErrUnauthorized (401, no retry), ErrNotFound (404), ErrRateLimited (limit still
// hit after MaxRetries pauses), ErrRequestFailed (network errors and 5xx after delays 1s/2s/4s, other statuses at once,
// bad redirects, oversize bodies, bad URL); ctx.Err() when ctx ends. Messages never contain the token.
func (c *Client) Get(ctx context.Context, rawURL, ifNoneMatch string) (*Response, error) {
	var st retryState
	for attempt := 1; ; attempt++ {
		o := c.fetch(ctx, rawURL, ifNoneMatch, attempt)
		resp, wait, err := c.decide(o, &st)
		if resp != nil || err != nil {
			return resp, err
		}
		if err := c.clock().Sleep(ctx, wait); err != nil {
			return nil, err
		}
	}
}

// decide turns an outcome into a final result or a retry pause.
//
// Args: o is the outcome; st holds the retry counters and is updated when a retry is chosen.
// Returns: (resp, 0, nil) for 200/304; (nil, wait, nil) to retry after wait; (nil, 0, err) to give up.
// Errors: as documented for Get; the third result is the error to return.
func (c *Client) decide(o outcome, st *retryState) (*Response, time.Duration, error) {
	switch {
	case o.err != nil:
		return nil, 0, o.err
	case o.failure != "":
		return c.retryFailure(st, o.status, o.failure+" "+o.detail)
	case o.status == http.StatusOK || o.status == http.StatusNotModified:
		return &Response{Status: o.status, Header: o.header, Body: o.body}, 0, nil
	case o.status == http.StatusUnauthorized:
		return nil, 0, c.newError(ErrUnauthorized, o.status, "")
	case o.status == http.StatusNotFound:
		return nil, 0, c.newError(ErrNotFound, o.status, "")
	case IsRateLimited(o.status, o.header, o.body):
		if st.limits >= MaxRetries {
			return nil, 0, c.newError(ErrRateLimited, o.status, "")
		}
		st.limits++
		return nil, RateLimitWait(o.header, c.clock().Now()), nil
	case o.status >= 500:
		return c.retryFailure(st, o.status, "ответ сервера")
	default:
		return nil, 0, c.newError(ErrRequestFailed, o.status, "неожиданный ответ")
	}
}

// retryFailure picks the next network/5xx retry pause.
//
// Args: st is the retry counters, updated on retry; status is the last HTTP status or 0; detail is a short note.
// Returns: (nil, delay, nil) with delay 1s, 2s, 4s for the first three calls; afterwards (nil, 0, *RequestError)
// of kind ErrRequestFailed.
// Errors: as described in Returns.
func (c *Client) retryFailure(st *retryState, status int, detail string) (*Response, time.Duration, error) {
	if st.failures >= MaxRetries {
		return nil, 0, c.newError(ErrRequestFailed, status, detail)
	}
	d := retryDelays[st.failures]
	st.failures++
	return nil, d, nil
}

// fetch performs one attempt: a request followed by up to MaxRedirects redirect hops.
//
// Args: ctx is the caller context; rawURL the first URL; ifNoneMatch as in Get; attempt the 1-based attempt number
// written to every trace record of this call.
// Returns: the outcome; err is set for non-retryable problems (bad URL, bad redirect, big body, canceled ctx).
// Errors: reported through outcome.err and outcome.failure, never as a Go error.
func (c *Client) fetch(ctx context.Context, rawURL, ifNoneMatch string, attempt int) outcome {
	cur := rawURL
	for followed := 0; ; followed++ {
		u, err := url.Parse(cur)
		if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" {
			return outcome{err: c.newError(ErrRequestFailed, 0, "некорректный адрес запроса")}
		}
		rctx, cancel := context.WithTimeout(ctx, c.timeout())
		req, err := http.NewRequestWithContext(rctx, http.MethodGet, cur, nil)
		if err != nil {
			cancel()
			return outcome{err: c.newError(ErrRequestFailed, 0, "некорректный адрес запроса")}
		}
		c.setHeaders(req, ifNoneMatch)
		resp, err := c.transport().RoundTrip(req)
		if err != nil {
			cancel()
			return c.networkOutcome(ctx, cur, attempt, 0, err)
		}
		body, err := readLimited(resp.Body, c.maxBody())
		_ = resp.Body.Close()
		cancel()
		if err != nil {
			if errors.Is(err, errTooLarge) {
				c.log(cur, resp.StatusCode, attempt, false, trace.ErrTooLarge)
				return outcome{err: c.newError(ErrRequestFailed, resp.StatusCode, "ответ слишком большой")}
			}
			return c.networkOutcome(ctx, cur, attempt, resp.StatusCode, err)
		}
		c.log(cur, resp.StatusCode, attempt, resp.StatusCode == http.StatusNotModified && ifNoneMatch != "", "")
		if !isFollowedRedirect(resp.StatusCode) {
			return outcome{status: resp.StatusCode, header: resp.Header, body: body}
		}
		if followed >= MaxRedirects {
			return outcome{err: c.newError(ErrRequestFailed, resp.StatusCode, "слишком много перенаправлений")}
		}
		next, ok := resolveRedirect(u, resp.Header.Get("Location"))
		if !ok {
			return outcome{err: c.newError(ErrRequestFailed, resp.StatusCode, "недопустимый адрес перенаправления")}
		}
		cur = next
	}
}

// networkOutcome classifies a transport or body-read error, logs it and builds the outcome.
//
// Args: ctx is the caller context; target is the URL being fetched; attempt the attempt number; status the HTTP
// status already received (0 if none); err the non-nil error.
// Returns: an outcome with err = ctx.Err() when the caller cancelled, otherwise failure "timeout" or "network".
// Errors: reported through the outcome.
func (c *Client) networkOutcome(ctx context.Context, target string, attempt, status int, err error) outcome {
	kind := classify(ctx, err)
	c.log(target, status, attempt, false, kind)
	if kind == trace.ErrCanceled {
		return outcome{err: ctx.Err()}
	}
	var ue *url.Error
	if errors.As(err, &ue) {
		err = ue.Err
	}
	return outcome{failure: kind, detail: c.redact(err.Error())}
}

// classify names a transport error for the trace.
//
// Args: ctx is the caller context; err is a non-nil error from the HTTP client or body read.
// Returns: trace.ErrCanceled if ctx is done, trace.ErrTimeout for deadline/timeout errors, else trace.ErrNetwork.
// Errors: never fails.
func classify(ctx context.Context, err error) string {
	if ctx.Err() != nil {
		return trace.ErrCanceled
	}
	var ne net.Error
	if errors.Is(err, context.DeadlineExceeded) || (errors.As(err, &ne) && ne.Timeout()) {
		return trace.ErrTimeout
	}
	return trace.ErrNetwork
}

// errTooLarge marks a body that exceeded the size limit.
var errTooLarge = errors.New("body too large")

// readLimited reads a whole body but at most limit bytes.
//
// Args: r is the body; limit is the maximum allowed size in bytes, > 0.
// Returns: the content.
// Errors: errTooLarge if the body is longer than limit; the read error otherwise.
func readLimited(r io.Reader, limit int64) ([]byte, error) {
	b, err := io.ReadAll(io.LimitReader(r, limit+1))
	if err != nil {
		return nil, err
	}
	if int64(len(b)) > limit {
		return nil, errTooLarge
	}
	return b, nil
}

// isFollowedRedirect tells whether a status is one of the redirects the service follows.
//
// Args: status is an HTTP status code.
// Returns: true for 301, 302 and 307 only (303, 308 and others are treated as failures).
// Errors: never fails.
func isFollowedRedirect(status int) bool {
	return status == http.StatusMovedPermanently || status == http.StatusFound || status == http.StatusTemporaryRedirect
}

// resolveRedirect resolves a Location header.
//
// Args: from is the URL that was requested; location is the raw Location value.
// Returns: the absolute http(s) URL and true; ("", false) for an empty, unparsable or non-http(s) target.
// Errors: never fails.
func resolveRedirect(from *url.URL, location string) (string, bool) {
	if strings.TrimSpace(location) == "" {
		return "", false
	}
	l, err := url.Parse(strings.TrimSpace(location))
	if err != nil {
		return "", false
	}
	l = from.ResolveReference(l)
	if (l.Scheme != "http" && l.Scheme != "https") || l.Host == "" {
		return "", false
	}
	return l.String(), true
}

// setHeaders fills the request headers.
//
// Args: req is the request to modify; ifNoneMatch is the ETag or "".
// Returns: nothing. Sets Accept, User-Agent, X-GitHub-Api-Version, If-None-Match (when non-empty) and
// Authorization: Bearer (only when a token is set and the request origin equals BaseOrigin).
// Errors: never fails.
func (c *Client) setHeaders(req *http.Request, ifNoneMatch string) {
	req.Header.Set("Accept", "application/vnd.github+json")
	req.Header.Set("User-Agent", orDefault(c.UserAgent, DefaultUserAgent))
	req.Header.Set("X-GitHub-Api-Version", orDefault(c.APIVersion, DefaultAPIVersion))
	if ifNoneMatch != "" {
		req.Header.Set("If-None-Match", ifNoneMatch)
	}
	if c.Token != "" && c.sameOrigin(req.URL) {
		req.Header.Set("Authorization", "Bearer "+c.Token)
	}
}

// sameOrigin tells whether a URL belongs to the configured API origin.
//
// Args: u is the request URL.
// Returns: true if Origin(u) equals the normalized BaseOrigin; false when BaseOrigin is empty or unparsable.
// Errors: never fails.
func (c *Client) sameOrigin(u *url.URL) bool {
	base, err := url.Parse(c.BaseOrigin)
	if err != nil || base.Host == "" {
		return false
	}
	return Origin(u) == Origin(base)
}

// Origin normalizes scheme and host of a URL.
//
// Args: u is a non-nil URL.
// Returns: "scheme://host[:port]" in lower case with default ports (80 for http, 443 for https) removed; user info,
// path and query are dropped.
// Errors: never fails.
func Origin(u *url.URL) string {
	scheme := strings.ToLower(u.Scheme)
	host := strings.ToLower(u.Hostname())
	port := u.Port()
	if (scheme == "http" && port == "80") || (scheme == "https" && port == "443") {
		port = ""
	}
	if strings.Contains(host, ":") {
		host = "[" + host + "]"
	}
	if port != "" {
		host += ":" + port
	}
	return scheme + "://" + host
}

// log writes one trace record.
//
// Args: target is the requested URL; status the HTTP status or 0; attempt the attempt number; fromCache the
// from_cache flag; errText the trace error description or "" for none.
// Returns: nothing. Trace write failures are ignored on purpose: tracing is best effort and must not fail requests.
// Errors: never fails.
func (c *Client) log(target string, status, attempt int, fromCache bool, errText string) {
	rec := trace.Record{
		TS:        model.FormatTimestamp(c.clock().Now()),
		Method:    http.MethodGet,
		URL:       target,
		Status:    status,
		Attempt:   attempt,
		FromCache: fromCache,
	}
	if errText != "" {
		rec.Error = trace.Str(errText)
	}
	_ = c.Trace.Write(rec)
}

// newError builds a *RequestError with the token removed from the detail.
//
// Args: kind is a sentinel error; status the HTTP status or 0; detail a short explanation, may be empty.
// Returns: a non-nil *RequestError.
// Errors: never fails.
func (c *Client) newError(kind error, status int, detail string) *RequestError {
	return &RequestError{Kind: kind, Status: status, Detail: c.redact(strings.TrimSpace(detail))}
}

// redact removes the token from a string.
//
// Args: s is any text.
// Returns: s with every occurrence of the token replaced by "***"; s itself when no token is set.
// Errors: never fails.
func (c *Client) redact(s string) string {
	if c.Token == "" {
		return s
	}
	return strings.ReplaceAll(s, c.Token, "***")
}

// transport returns the round tripper used for requests.
//
// Args: none.
// Returns: c.HTTP.Transport, or http.DefaultTransport when c.HTTP or its Transport is nil. Using the transport
// directly means net/http never follows or parses redirects itself, so every hop is handled (and traced) by Get;
// this is equivalent to CheckRedirect returning http.ErrUseLastResponse but also tolerates malformed Location values.
// Errors: never fails.
func (c *Client) transport() http.RoundTripper {
	if c.HTTP != nil && c.HTTP.Transport != nil {
		return c.HTTP.Transport
	}
	return http.DefaultTransport
}

// clock returns the configured clock or the real one.
//
// Args: none.
// Returns: c.Clock, or clock.Real{} when nil.
// Errors: never fails.
func (c *Client) clock() clock.Clock {
	if c.Clock == nil {
		return clock.Real{}
	}
	return c.Clock
}

// timeout returns the per-request timeout.
//
// Args: none.
// Returns: c.Timeout, or DefaultTimeout when it is not positive.
// Errors: never fails.
func (c *Client) timeout() time.Duration {
	if c.Timeout <= 0 {
		return DefaultTimeout
	}
	return c.Timeout
}

// maxBody returns the response size limit.
//
// Args: none.
// Returns: c.MaxBodySize, or DefaultMaxBody when it is not positive.
// Errors: never fails.
func (c *Client) maxBody() int64 {
	if c.MaxBodySize <= 0 {
		return DefaultMaxBody
	}
	return c.MaxBodySize
}

// orDefault picks a fallback for an empty string.
//
// Args: v is the configured value; def is the fallback.
// Returns: v if non-empty, else def.
// Errors: never fails.
func orDefault(v, def string) string {
	if v == "" {
		return def
	}
	return v
}
