package config

import (
	"errors"
	"net/url"
	"strings"
	"testing"
)

// env builds a getenv function over a fixed map.
//
// Args: m maps variable names to values; missing names yield "".
// Returns: a lookup function backed by m.
// Errors: never fails.
func env(m map[string]string) func(string) string {
	return func(k string) string { return m[k] }
}

// covers: FromEnv
func TestFromEnvBaseURLTable(t *testing.T) {
	cases := []struct {
		name string
		in   string
		want string // expected BaseURL.String(); "" means error
	}{
		{"unset", "", "https://api.github.com"},
		{"spaces only", "   ", "https://api.github.com"},
		{"plain", "https://api.github.com", "https://api.github.com"},
		{"one trailing slash", "https://api.github.com/", "https://api.github.com"},
		{"many trailing slashes", "https://api.github.com////", "https://api.github.com"},
		{"http with port", "http://127.0.0.1:8080", "http://127.0.0.1:8080"},
		{"http port slash", "http://localhost:3000/", "http://localhost:3000"},
		{"prefix kept", "https://ghe.example.com/api/v3", "https://ghe.example.com/api/v3"},
		{"prefix trailing slash", "https://ghe.example.com/api/v3/", "https://ghe.example.com/api/v3"},
		{"prefix many slashes", "https://ghe.example.com/api/v3///", "https://ghe.example.com/api/v3"},
		{"deep prefix", "http://h/a/b/c", "http://h/a/b/c"},
		{"uppercase scheme", "HTTP://Host:1", "http://Host:1"},
		{"surrounding whitespace", "  http://h:1/x/ \n", "http://h:1/x"},
		{"ipv6", "http://[::1]:9", "http://[::1]:9"},
		{"escaped prefix", "http://h/a%2Fb/", "http://h/a%2Fb"},
		{"ftp scheme", "ftp://h", ""},
		{"no scheme", "api.github.com", ""},
		{"host with port no scheme", "localhost:8080", ""},
		{"scheme only", "https://", ""},
		{"no host with path", "https:///api", ""},
		{"file scheme", "file:///tmp/x", ""},
		{"relative", "/api/v3", ""},
		{"user info", "https://user:pass@h", ""},
		{"query", "https://h/api?x=1", ""},
		{"empty query mark", "https://h/?", ""},
		{"fragment", "https://h/#f", ""},
		{"garbage", "http://[bad", ""},
		{"space inside", "http://h /x", ""},
		{"opaque", "https:h", ""},
		{"javascript", "javascript:alert(1)", ""},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			cfg, err := FromEnv(env(map[string]string{"GITHUB_API_BASE_URL": tc.in}))
			if tc.want == "" {
				if !errors.Is(err, ErrInvalidBaseURL) || cfg.BaseURL != nil || cfg.Token != "" {
					t.Fatalf("want ErrInvalidBaseURL and zero Config, got %+v, %v", cfg, err)
				}
				if strings.Contains(err.Error(), "pass") {
					t.Fatalf("error leaks the value: %v", err)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			if got := cfg.BaseURL.String(); got != tc.want {
				t.Fatalf("BaseURL = %q, want %q", got, tc.want)
			}
		})
	}
}

// covers: FromEnv
func TestFromEnvToken(t *testing.T) {
	cases := map[string]string{"": "", "  ": "", "ghp_abc": "ghp_abc", "  ghp_abc\n": "ghp_abc", "\tx y\t": "x y"}
	for in, want := range cases {
		cfg, err := FromEnv(env(map[string]string{"GITHUB_TOKEN": in}))
		if err != nil || cfg.Token != want {
			t.Errorf("token %q: got %q, %v; want %q", in, cfg.Token, err, want)
		}
	}
}

// covers: FromEnv
func TestFromEnvErrorNeverContainsToken(t *testing.T) {
	_, err := FromEnv(env(map[string]string{"GITHUB_TOKEN": "SECRET-TOKEN", "GITHUB_API_BASE_URL": "ftp://x"}))
	if err == nil || strings.Contains(err.Error(), "SECRET-TOKEN") {
		t.Fatalf("got %v", err)
	}
}

// covers: FromEnv
func TestFromEnvEmptyEnvironment(t *testing.T) {
	cfg, err := FromEnv(env(nil))
	if err != nil || cfg.BaseURL.String() != DefaultBaseURL || cfg.Token != "" {
		t.Fatalf("got %+v, %v", cfg, err)
	}
}

// covers: parseBaseURL
func TestParseBaseURL(t *testing.T) {
	u, err := parseBaseURL("https://h/p/")
	if err != nil || u.Path != "/p" || u.Host != "h" {
		t.Fatalf("got %+v, %v", u, err)
	}
	u, err = parseBaseURL("https://h/")
	if err != nil || u.Path != "" {
		t.Fatalf("bare slash: got %+v, %v", u, err)
	}
	for _, bad := range []string{"", "h", "ssh://h", "https://u@h", "https://h?x", "https://h#x"} {
		if _, err := parseBaseURL(bad); !errors.Is(err, ErrInvalidBaseURL) {
			t.Errorf("parseBaseURL(%q): want ErrInvalidBaseURL, got %v", bad, err)
		}
	}
}

// covers: JoinURL
func TestJoinURLTable(t *testing.T) {
	cases := []struct{ base, path, want string }{
		{"https://api.github.com", "/repos/a/b/issues", "https://api.github.com/repos/a/b/issues"},
		{"https://api.github.com", "repos/a/b/issues", "https://api.github.com/repos/a/b/issues"},
		{"https://api.github.com", "//repos/a", "https://api.github.com/repos/a"},
		{"https://ghe.example.com/api/v3", "/repos/a/b/issues", "https://ghe.example.com/api/v3/repos/a/b/issues"},
		{"https://ghe.example.com/api/v3", "repos/a", "https://ghe.example.com/api/v3/repos/a"},
		{"http://127.0.0.1:8080", "/x", "http://127.0.0.1:8080/x"},
		{"http://h", "", "http://h/"},
		{"http://h", "/", "http://h/"},
		{"http://h/a%2Fb", "/c", "http://h/a%2Fb/c"},
	}
	for _, tc := range cases {
		base, err := url.Parse(tc.base)
		if err != nil {
			t.Fatal(err)
		}
		if got := JoinURL(base, tc.path).String(); got != tc.want {
			t.Errorf("JoinURL(%q, %q) = %q, want %q", tc.base, tc.path, got, tc.want)
		}
	}
}

// covers: JoinURL
func TestJoinURLDoesNotMutateBase(t *testing.T) {
	base, _ := url.Parse("https://h/api")
	before := base.String()
	joined := JoinURL(base, "/repos")
	joined.Path = "/changed"
	if base.String() != before {
		t.Fatalf("base mutated: %q -> %q", before, base.String())
	}
}

// covers: JoinURL
func TestJoinURLWithTrailingSlashBase(t *testing.T) {
	base, _ := url.Parse("https://h/api/")
	if got := JoinURL(base, "/x").String(); got != "https://h/api/x" {
		t.Fatalf("got %q", got)
	}
}
