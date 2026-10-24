// Package config reads the process environment (API base URL and token).
package config

import (
	"errors"
	"net/url"
	"strings"
)

// DefaultBaseURL is used when GITHUB_API_BASE_URL is unset or empty.
const DefaultBaseURL = "https://api.github.com"

// Config is the environment-derived configuration.
type Config struct {
	BaseURL *url.URL // no trailing slash in Path; may carry a prefix such as /api/v3
	Token   string   // empty when no token is configured
}

// ErrInvalidBaseURL is returned for an unusable GITHUB_API_BASE_URL; it never includes the value.
var ErrInvalidBaseURL = errors.New("ghreport: GITHUB_API_BASE_URL должна быть адресом http:// или https:// с указанием хоста, без параметров и фрагмента")

// FromEnv builds Config from environment variables.
//
// Args: getenv looks up a variable by name (os.Getenv in production); must not be nil, empty string means unset.
// Returns: Config with BaseURL parsed from GITHUB_API_BASE_URL (default https://api.github.com) and Token from
// GITHUB_TOKEN with surrounding whitespace removed; zero value when err != nil.
// Errors: ErrInvalidBaseURL when the base URL has a scheme other than http/https, no host, user info, a query or a
// fragment, or cannot be parsed. The token and the URL text never appear in the error.
func FromEnv(getenv func(string) string) (Config, error) {
	raw := strings.TrimSpace(getenv("GITHUB_API_BASE_URL"))
	if raw == "" {
		raw = DefaultBaseURL
	}
	base, err := parseBaseURL(raw)
	if err != nil {
		return Config{}, err
	}
	return Config{BaseURL: base, Token: strings.TrimSpace(getenv("GITHUB_TOKEN"))}, nil
}

// parseBaseURL validates and normalizes a base URL.
//
// Args: raw is a non-empty URL string.
// Returns: the parsed URL whose Path and RawPath have trailing slashes removed (a bare "/" becomes empty); any path
// prefix is preserved verbatim.
// Errors: ErrInvalidBaseURL for unparsable input, non-http(s) schemes, a missing host, user info, query or fragment.
func parseBaseURL(raw string) (*url.URL, error) {
	if strings.ContainsAny(raw, "?#") {
		return nil, ErrInvalidBaseURL
	}
	u, err := url.Parse(raw)
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Hostname() == "" || u.User != nil || u.Opaque != "" {
		return nil, ErrInvalidBaseURL
	}
	u.Path = strings.TrimRight(u.Path, "/")
	u.RawPath = strings.TrimRight(u.RawPath, "/")
	return u, nil
}

// JoinURL appends a request path to a base URL.
//
// Args: base is the configured base URL, not modified; path is an unescaped path with or without a leading slash.
// Returns: a new URL equal to base with path appended after base's path prefix, joined by exactly one slash.
// Errors: never fails.
func JoinURL(base *url.URL, path string) *url.URL {
	u := *base
	suffix := strings.TrimLeft(path, "/")
	u.Path = strings.TrimRight(base.Path, "/") + "/" + suffix
	if base.RawPath != "" {
		u.RawPath = strings.TrimRight(base.RawPath, "/") + "/" + (&url.URL{Path: suffix}).EscapedPath()
	}
	return &u
}
