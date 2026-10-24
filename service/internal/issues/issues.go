// Package issues fetches and normalizes the open issues of one repository.
package issues

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/url"
	"os"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"ghreport/internal/args"
	"ghreport/internal/cache"
	"ghreport/internal/config"
	"ghreport/internal/httpx"
	"ghreport/internal/linkhdr"
	"ghreport/internal/model"
)

// DefaultMaxPages is the page ceiling used when Fetcher.MaxPages is 0.
const DefaultMaxPages = 10000

// ErrInvalidResponse marks a response that is not the expected JSON, a pagination loop or a page overflow.
var ErrInvalidResponse = errors.New("ghreport: некорректный ответ API")

// Getter performs conditional GET requests; *httpx.Client implements it.
type Getter interface {
	Get(ctx context.Context, url, ifNoneMatch string) (*httpx.Response, error)
}

// Fetcher downloads issues page by page.
type Fetcher struct {
	Client   Getter
	Cache    *cache.Store // may be nil: no caching
	Base     *url.URL     // API base URL, see config.FromEnv
	Warn     io.Writer    // receives cache warnings; nil means os.Stderr
	MaxPages int          // page ceiling; 0 means DefaultMaxPages
}

// invalidf wraps ErrInvalidResponse with a detail.
//
// Args: format and a form the detail; they must not contain secrets.
// Returns: an error for which errors.Is(err, ErrInvalidResponse) is true.
// Errors: never fails.
func invalidf(format string, a ...any) error {
	return fmt.Errorf("%w: %s", ErrInvalidResponse, fmt.Sprintf(format, a...))
}

// ErrorCode maps an error from FetchOpenIssues (or httpx) to the spec's codes.
//
// Args: err is any error; nil is allowed.
// Returns: (model.ErrNotFound, false) for httpx.ErrNotFound; model.ErrRateLimited for httpx.ErrRateLimited;
// model.ErrInvalidResponse for ErrInvalidResponse; model.ErrRequestFailed for httpx.ErrRequestFailed and for every
// other non-nil error; ("", true) for httpx.ErrUnauthorized, which is fatal for the whole run; ("", false) for nil.
// Errors: never fails.
func ErrorCode(err error) (code string, unauthorized bool) {
	switch {
	case err == nil:
		return "", false
	case errors.Is(err, httpx.ErrUnauthorized):
		return "", true
	case errors.Is(err, httpx.ErrNotFound):
		return model.ErrNotFound, false
	case errors.Is(err, httpx.ErrRateLimited):
		return model.ErrRateLimited, false
	case errors.Is(err, ErrInvalidResponse):
		return model.ErrInvalidResponse, false
	default:
		return model.ErrRequestFailed, false
	}
}

// FirstURL builds the address of the first page.
//
// Args: base is the API base URL; repo is a validated repository reference.
// Returns: {base}/repos/{owner}/{repo}/issues?state=open&sort=created&direction=asc&per_page=100 with escaped path
// segments and the query parameters in exactly this order.
// Errors: never fails.
func FirstURL(base *url.URL, repo args.RepoRef) string {
	u := config.JoinURL(base, "repos/"+repo.Owner+"/"+repo.Name+"/issues")
	// Escape every segment separately (JoinURL would treat a slash inside a name as a separator).
	escaped := strings.TrimRight(base.EscapedPath(), "/") + "/repos/" + url.PathEscape(repo.Owner) + "/" + url.PathEscape(repo.Name) + "/issues"
	if p, err := url.PathUnescape(escaped); err == nil {
		u.Path, u.RawPath = p, escaped
	}
	u.RawQuery = "state=open&sort=created&direction=asc&per_page=100"
	return u.String()
}

// FetchOpenIssues returns up to limit open issues (no pull requests) of a repository.
//
// Args: ctx cancels the work; repo is the repository; limit is the maximum number of issues, >= 1 (smaller values
// return an empty result without requests); since, when non-nil, excludes issues created before that instant.
// Returns: the issues sorted by created_at (as an instant) then number, without duplicates. Pages are requested
// one at a time; the next address comes only from the Link header; requesting stops as soon as limit issues are
// collected (even in the middle of a page) or no rel="next" is left.
// Errors: errors from Getter.Get unchanged (httpx.Err*); ErrInvalidResponse for non-array bodies, malformed or
// missing required fields (number, title, html_url, created_at), a 304 without a cache entry, an address that
// repeats, or more than MaxPages pages.
func (f *Fetcher) FetchOpenIssues(ctx context.Context, repo args.RepoRef, limit int, since *time.Time) ([]model.Issue, error) {
	out := []model.Issue{}
	if limit < 1 {
		return out, nil
	}
	maxPages := f.MaxPages
	if maxPages <= 0 {
		maxPages = DefaultMaxPages
	}
	seenNumbers := map[int]bool{}
	seenURLs := map[string]bool{}
	cur := FirstURL(f.Base, repo)
	for pages := 0; ; pages++ {
		if pages >= maxPages {
			return nil, invalidf("превышено число страниц (%d)", maxPages)
		}
		if seenURLs[cur] {
			return nil, invalidf("цикл в заголовке Link")
		}
		seenURLs[cur] = true

		body, link, err := f.page(ctx, cur)
		if err != nil {
			return nil, err
		}
		var elems []json.RawMessage
		if err := json.Unmarshal(body, &elems); err != nil || elems == nil {
			return nil, invalidf("тело ответа не является JSON-массивом")
		}
		for _, raw := range elems {
			issue, skip, err := parseElement(raw, since)
			if err != nil {
				return nil, err
			}
			if skip || seenNumbers[issue.Number] {
				continue
			}
			seenNumbers[issue.Number] = true
			out = append(out, issue)
			if len(out) >= limit {
				return sortIssues(out), nil
			}
		}
		curURL, err := url.Parse(cur)
		if err != nil {
			return nil, invalidf("некорректный адрес страницы")
		}
		next, ok := linkhdr.NextURL(link, curURL)
		if !ok {
			return sortIssues(out), nil
		}
		cur = next
	}
}

// page returns the body and Link values of one page, using the cache for conditional requests.
//
// Args: ctx is the caller context; cur is the page address (also the cache key).
// Returns: the JSON body and the Link header values. For 304 the body and Link come from the cache entry; a Link
// header present in the 304 response takes precedence. For 200 the cache is updated when the response has an ETag and
// the body is a valid UTF-8 JSON array.
// Errors: errors from Get; ErrInvalidResponse for 304 without a cache entry or any other status; a failing cache
// write is not an error, a warning goes to Warn.
func (f *Fetcher) page(ctx context.Context, cur string) ([]byte, []string, error) {
	entry, have := cache.Entry{}, false
	if f.Cache != nil {
		entry, have = f.Cache.Get(cur)
	}
	inm := ""
	if have {
		inm = entry.ETag
	}
	resp, err := f.Client.Get(ctx, cur, inm)
	if err != nil {
		return nil, nil, err
	}
	link := resp.Header.Values("Link")
	switch resp.Status {
	case 304:
		if !have {
			return nil, nil, invalidf("ответ 304 без записи в кэше")
		}
		if len(link) == 0 && entry.Link != "" {
			link = []string{entry.Link}
		}
		return []byte(entry.Body), link, nil
	case 200:
		if f.Cache != nil && cacheable(resp) {
			e := cache.Entry{URL: cur, ETag: resp.Header.Get("ETag"), Link: strings.Join(link, ", "), Body: string(resp.Body)}
			if err := f.Cache.Put(e); err != nil {
				fmt.Fprintf(f.warn(), "ghreport: предупреждение: не удалось записать кэш: %v\n", err)
			}
		}
		return resp.Body, link, nil
	default:
		return nil, nil, invalidf("неожиданный статус %d", resp.Status)
	}
}

// warn returns the warning destination.
//
// Args: none.
// Returns: f.Warn, or os.Stderr when nil.
// Errors: never fails.
func (f *Fetcher) warn() io.Writer {
	if f.Warn == nil {
		return os.Stderr
	}
	return f.Warn
}

// cacheable tells whether a 200 response may be stored.
//
// Args: resp is a non-nil response.
// Returns: true when it has an ETag and a body that is valid UTF-8 and a JSON array.
// Errors: never fails.
func cacheable(resp *httpx.Response) bool {
	if resp.Header.Get("ETag") == "" || !utf8.Valid(resp.Body) {
		return false
	}
	trimmed := bytes.TrimSpace(resp.Body)
	return json.Valid(trimmed) && len(trimmed) > 0 && trimmed[0] == '['
}

// parseElement validates and converts one array element.
//
// Args: raw is the element JSON; since is the optional lower bound for created_at.
// Returns: the issue; skip is true for pull requests (a pull_request key is present, even
// with a null value; nothing else is validated for them) and for issues created before since. Checks run in this
// order: pull request, required fields, since. Duplicate numbers are handled by the caller.
// Errors: ErrInvalidResponse when the element is not an object or number (integer), title (string), html_url
// (string) or created_at (RFC3339 string) is missing or malformed, or when user/labels have the wrong shape.
func parseElement(raw json.RawMessage, since *time.Time) (issue model.Issue, skip bool, err error) {
	var obj map[string]json.RawMessage
	if json.Unmarshal(raw, &obj) != nil || obj == nil {
		return issue, false, invalidf("элемент ответа не является объектом")
	}
	if _, isPR := obj["pull_request"]; isPR {
		return issue, true, nil
	}
	number, err := intField(obj, "number")
	if err != nil {
		return issue, false, err
	}
	title, err := stringField(obj, "title")
	if err != nil {
		return issue, false, err
	}
	htmlURL, err := stringField(obj, "html_url")
	if err != nil {
		return issue, false, err
	}
	createdText, err := stringField(obj, "created_at")
	if err != nil {
		return issue, false, err
	}
	created, perr := time.Parse(time.RFC3339, createdText)
	if perr != nil {
		return issue, false, invalidf("поле created_at не является датой RFC3339")
	}
	author, err := authorOf(obj["user"])
	if err != nil {
		return issue, false, err
	}
	labels, err := labelsOf(obj["labels"])
	if err != nil {
		return issue, false, err
	}
	if since != nil && created.Before(*since) {
		return issue, true, nil
	}
	return model.Issue{Number: number, Title: title, Author: author, Labels: labels, CreatedAt: createdText, URL: htmlURL}, false, nil
}

// intField reads a required integer field.
//
// Args: obj is the element; name is the key.
// Returns: the value as int.
// Errors: ErrInvalidResponse when the key is absent, null, negative, not an integer literal (fractions, exponents and
// strings are rejected) or overflows int.
func intField(obj map[string]json.RawMessage, name string) (int, error) {
	raw, ok := obj[name]
	text := strings.TrimSpace(string(raw))
	if !ok || text == "" || text[0] == '"' {
		return 0, invalidf("поле %s отсутствует или не число", name)
	}
	n, err := strconv.Atoi(text)
	if err != nil || n < 0 {
		return 0, invalidf("поле %s не является целым числом", name)
	}
	return n, nil
}

// stringField reads a required string field.
//
// Args: obj is the element; name is the key.
// Returns: the decoded string (may be empty).
// Errors: ErrInvalidResponse when the key is absent, null or not a string.
func stringField(obj map[string]json.RawMessage, name string) (string, error) {
	raw, ok := obj[name]
	var s *string
	if !ok || json.Unmarshal(raw, &s) != nil || s == nil {
		return "", invalidf("поле %s отсутствует или не строка", name)
	}
	return *s, nil
}

// authorOf extracts user.login.
//
// Args: raw is the "user" value; empty when the key is absent.
// Returns: the login, or "" when user is absent, null, or has no login.
// Errors: ErrInvalidResponse when user is not an object or login is neither a string nor null.
func authorOf(raw json.RawMessage) (string, error) {
	if len(raw) == 0 || string(bytes.TrimSpace(raw)) == "null" {
		return "", nil
	}
	var user struct {
		Login *string `json:"login"`
	}
	if json.Unmarshal(raw, &user) != nil {
		return "", invalidf("поле user имеет неверный формат")
	}
	if user.Login == nil {
		return "", nil
	}
	return *user.Login, nil
}

// labelsOf extracts labels[].name.
//
// Args: raw is the "labels" value; empty when the key is absent.
// Returns: the names in response order; an empty non-nil slice when labels is absent, null or empty.
// Errors: ErrInvalidResponse when labels is not an array of objects whose "name" is a string.
func labelsOf(raw json.RawMessage) ([]string, error) {
	names := []string{}
	if len(raw) == 0 || string(bytes.TrimSpace(raw)) == "null" {
		return names, nil
	}
	var items []struct {
		Name *string `json:"name"`
	}
	if json.Unmarshal(raw, &items) != nil {
		return nil, invalidf("поле labels имеет неверный формат")
	}
	for _, it := range items {
		if it.Name == nil {
			return nil, invalidf("у метки нет имени")
		}
		names = append(names, *it.Name)
	}
	return names, nil
}

// sortIssues orders issues by creation instant, then number.
//
// Args: list is the issue slice; it is sorted in place and may be empty.
// Returns: the same slice. The sort is stable; created_at is compared as an instant (time zones are honored), and
// strings that fail to parse cannot occur because parseElement validated them.
// Errors: never fails.
func sortIssues(list []model.Issue) []model.Issue {
	instants := make(map[int]time.Time, len(list))
	for _, is := range list {
		instants[is.Number], _ = time.Parse(time.RFC3339, is.CreatedAt)
	}
	sort.SliceStable(list, func(i, j int) bool {
		ti, tj := instants[list[i].Number], instants[list[j].Number]
		if !ti.Equal(tj) {
			return ti.Before(tj)
		}
		return list[i].Number < list[j].Number
	})
	return list
}
