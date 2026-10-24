// Package linkhdr extracts the rel="next" target from HTTP Link headers (RFC 8288).
package linkhdr

import (
	"net/url"
	"strings"
)

// link is one parsed link-value: its target and the value of its first rel parameter.
type link struct {
	target string
	rel    string
}

// NextURL finds the page that follows the current one.
//
// Args: headers are ALL values of the Link header (each value may hold several comma-separated links); base is the
// URL the response came from, used to resolve relative targets; may be nil when all targets are absolute.
// Returns: the absolute http(s) URL of the first link whose rel contains the token "next" (case-insensitive), and
// true; ("", false) when there is none or the headers are malformed. Parsing is character by character, so commas and
// semicolons inside <...> or quoted strings are harmless; a malformed header value is skipped from the point of the
// error on, later header values are still examined. The URL is never constructed by hand.
// Errors: never fails; malformed input yields ("", false).
func NextURL(headers []string, base *url.URL) (string, bool) {
	for _, h := range headers {
		for _, l := range parseLinks(h) {
			if !hasRelToken(l.rel, "next") {
				continue
			}
			if resolved, ok := resolve(l.target, base); ok {
				return resolved, true
			}
		}
	}
	return "", false
}

// parseLinks parses one Link header value.
//
// Args: value is the raw header value; may be empty or malformed.
// Returns: the well-formed links found before the first syntax error, in order.
// Errors: never fails.
func parseLinks(value string) []link {
	var links []link
	i, n := 0, len(value)
	for {
		for i < n && (value[i] == ',' || isSpace(value[i])) {
			i++
		}
		if i >= n || value[i] != '<' {
			return links
		}
		end := strings.IndexByte(value[i+1:], '>')
		if end < 0 {
			return links
		}
		l := link{target: value[i+1 : i+1+end]}
		i += end + 2
		haveRel := false
		for {
			for i < n && isSpace(value[i]) {
				i++
			}
			if i >= n || value[i] == ',' {
				break
			}
			if value[i] != ';' {
				return links
			}
			i++
			for i < n && isSpace(value[i]) {
				i++
			}
			start := i
			for i < n && !isSpace(value[i]) && value[i] != '=' && value[i] != ';' && value[i] != ',' {
				i++
			}
			name := strings.ToLower(value[start:i])
			for i < n && isSpace(value[i]) {
				i++
			}
			param := ""
			if i < n && value[i] == '=' {
				i++
				for i < n && isSpace(value[i]) {
					i++
				}
				var ok bool
				param, i, ok = parseParamValue(value, i)
				if !ok {
					return links
				}
			}
			if name == "rel" && !haveRel {
				l.rel, haveRel = param, true
			}
		}
		links = append(links, l)
	}
}

// parseParamValue reads a parameter value starting at position i.
//
// Args: s is the header value; i is the index of the first value character (0 <= i <= len(s)).
// Returns: the unquoted value, the index just after it, and true; false when a quoted string is not terminated.
// Errors: never fails.
func parseParamValue(s string, i int) (string, int, bool) {
	if i < len(s) && s[i] == '"' {
		var b strings.Builder
		for i++; i < len(s); i++ {
			switch s[i] {
			case '\\':
				if i+1 < len(s) {
					i++
					b.WriteByte(s[i])
				}
			case '"':
				return b.String(), i + 1, true
			default:
				b.WriteByte(s[i])
			}
		}
		return "", i, false
	}
	start := i
	for i < len(s) && !isSpace(s[i]) && s[i] != ';' && s[i] != ',' {
		i++
	}
	return s[start:i], i, true
}

// isSpace tells whether c is optional whitespace.
//
// Args: c is any byte.
// Returns: true for space and tab.
// Errors: never fails.
func isSpace(c byte) bool {
	return c == ' ' || c == '\t'
}

// hasRelToken tells whether a rel value lists a relation type.
//
// Args: rel is the (unquoted) rel parameter value, a space-separated list; token is the lowercase relation to find.
// Returns: true if some whitespace-separated word of rel equals token ignoring case.
// Errors: never fails.
func hasRelToken(rel, token string) bool {
	for _, word := range strings.Fields(rel) {
		if strings.EqualFold(word, token) {
			return true
		}
	}
	return false
}

// resolve turns a link target into an absolute http(s) URL.
//
// Args: target is the text between < and >; base is the response URL or nil.
// Returns: the absolute URL string and true; ("", false) for unparsable targets, non-http(s) schemes, a missing
// host, or a relative target without base.
// Errors: never fails.
func resolve(target string, base *url.URL) (string, bool) {
	u, err := url.Parse(strings.TrimSpace(target))
	if err != nil {
		return "", false
	}
	if !u.IsAbs() {
		if base == nil {
			return "", false
		}
		u = base.ResolveReference(u)
	}
	if (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" {
		return "", false
	}
	return u.String(), true
}
