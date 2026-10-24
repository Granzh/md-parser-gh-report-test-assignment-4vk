package linkhdr

import (
	"net/url"
	"testing"
)

// mustURL parses a URL or aborts the test.
//
// Args: s is a URL string.
// Returns: the parsed URL.
// Errors: never fails; calls t.Fatal on a parse error.
func mustURL(t *testing.T, s string) *url.URL {
	t.Helper()
	u, err := url.Parse(s)
	if err != nil {
		t.Fatal(err)
	}
	return u
}

// covers: NextURL
func TestNextURLTable(t *testing.T) {
	base := mustURL(t, "https://api.github.com/repos/o/r/issues?per_page=100")
	cases := []struct {
		name    string
		headers []string
		base    *url.URL
		want    string // "" means no next
	}{
		{"spec example", []string{`<https://api.github.com/repos/o/r/issues?per_page=100&page=2>; rel="next", <https://api.github.com/repos/o/r/issues?per_page=100&page=7>; rel="last"`}, base, "https://api.github.com/repos/o/r/issues?per_page=100&page=2"},
		{"next after last", []string{`<https://h/p7>; rel="last", <https://h/p2>; rel="next"`}, base, "https://h/p2"},
		{"no space after comma", []string{`<https://h/a>; rel="prev",<https://h/b>; rel="next"`}, base, "https://h/b"},
		{"only last", []string{`<https://h/p7>; rel="last"`}, base, ""},
		{"only prev first", []string{`<https://h/p1>; rel="prev", <https://h/p1>; rel="first"`}, base, ""},
		{"several headers", []string{`<https://h/a>; rel="prev"`, `<https://h/b>; rel="next"`}, base, "https://h/b"},
		{"first header garbage second good", []string{`garbage`, `<https://h/b>; rel="next"`}, base, "https://h/b"},
		{"comma inside url", []string{`<https://h/search?q=a,b&page=2>; rel="next"`}, base, "https://h/search?q=a,b&page=2"},
		{"semicolon inside url", []string{`<https://h/x;y?page=2>; rel="next"`}, base, "https://h/x;y?page=2"},
		{"angle bracket inside url", []string{`<https://h/?a=,<b>; rel="next"`}, base, "https://h/?a=,<b"},
		{"relative path", []string{`</repos/o/r/issues?page=2>; rel="next"`}, base, "https://api.github.com/repos/o/r/issues?page=2"},
		{"relative query only", []string{`<?page=2>; rel="next"`}, base, "https://api.github.com/repos/o/r/issues?page=2"},
		{"relative without base", []string{`</x?page=2>; rel="next"`}, nil, ""},
		{"absolute without base", []string{`<https://h/x>; rel="next"`}, nil, "https://h/x"},
		{"rel unquoted", []string{`<https://h/n>; rel=next`}, base, "https://h/n"},
		{"rel upper case value", []string{`<https://h/n>; rel="NEXT"`}, base, "https://h/n"},
		{"rel upper case name", []string{`<https://h/n>; REL="next"`}, base, "https://h/n"},
		{"rel list", []string{`<https://h/n>; rel="next last"`}, base, "https://h/n"},
		{"rel list reversed", []string{`<https://h/n>; rel="last   next"`}, base, "https://h/n"},
		{"rel is nextish", []string{`<https://h/n>; rel="nextpage"`}, base, ""},
		{"extra params", []string{`<https://h/n>; title="a;b,c"; rel="next"; type="x"`}, base, "https://h/n"},
		{"params before rel unquoted", []string{`<https://h/n>;foo=bar;rel=next`}, base, "https://h/n"},
		{"escaped quote in param", []string{`<https://h/n>; title="a\"b"; rel="next"`}, base, "https://h/n"},
		{"whitespace everywhere", []string{"  <https://h/n> \t;  rel = \"next\"  "}, base, "https://h/n"},
		{"first rel wins", []string{`<https://h/n>; rel="last"; rel="next"`}, base, ""},
		{"no rel", []string{`<https://h/n>`}, base, ""},
		{"empty header", []string{""}, base, ""},
		{"no headers", nil, base, ""},
		{"only commas", []string{",,,"}, base, ""},
		{"unterminated url", []string{`<https://h/n; rel="next"`}, base, ""},
		{"unterminated quote", []string{`<https://h/n>; rel="next`}, base, ""},
		{"missing angle", []string{`https://h/n; rel="next"`}, base, ""},
		{"junk after url", []string{`<https://h/n> rel="next"`}, base, ""},
		{"good link before garbage", []string{`<https://h/n>; rel="next", ???`}, base, "https://h/n"},
		{"garbage before good link in same header", []string{`???, <https://h/n>; rel="next"`}, base, ""},
		{"non http scheme", []string{`<ftp://h/n>; rel="next"`}, base, ""},
		{"javascript scheme", []string{`<javascript:alert(1)>; rel="next"`}, base, ""},
		{"no host", []string{`<https:///n>; rel="next"`}, base, ""},
		{"unparsable url", []string{`<http://[bad>; rel="next"`}, base, ""},
		{"bad next then good next", []string{`<ftp://x>; rel="next", <https://h/ok>; rel="next"`}, base, "https://h/ok"},
		{"empty target", []string{`<>; rel="next"`}, base, "https://api.github.com/repos/o/r/issues?per_page=100"},
		{"binary garbage", []string{"\x00\xff<\x01"}, base, ""},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got, ok := NextURL(tc.headers, tc.base)
			if (tc.want == "") == ok || got != tc.want {
				t.Fatalf("NextURL(%q) = (%q, %v), want %q", tc.headers, got, ok, tc.want)
			}
		})
	}
}

// covers: parseLinks
func TestParseLinks(t *testing.T) {
	got := parseLinks(`<a>; rel="x y", <b,c>;rel=z;title="q", <d>`)
	want := []link{{"a", "x y"}, {"b,c", "z"}, {"d", ""}}
	if len(got) != len(want) {
		t.Fatalf("got %v", got)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("link %d = %v, want %v", i, got[i], want[i])
		}
	}
	if l := parseLinks(""); len(l) != 0 {
		t.Fatalf("empty: %v", l)
	}
	if l := parseLinks(`<a>; rel="x`); len(l) != 0 {
		t.Fatalf("unterminated: %v", l)
	}
}

// covers: parseParamValue
func TestParseParamValue(t *testing.T) {
	cases := []struct {
		in   string
		at   int
		want string
		next int
		ok   bool
	}{
		{`"a b"; x`, 0, "a b", 5, true},
		{`"a\"b"`, 0, `a"b`, 6, true},
		{`"a\\b"`, 0, `a\b`, 6, true},
		{`tok;x`, 0, "tok", 3, true},
		{`tok,x`, 0, "tok", 3, true},
		{`tok x`, 0, "tok", 3, true},
		{``, 0, "", 0, true},
		{`"open`, 0, "", 5, false},
		{`"trailing\`, 0, "", 10, false},
		{`x=y`, 2, "y", 3, true},
	}
	for _, tc := range cases {
		got, next, ok := parseParamValue(tc.in, tc.at)
		if got != tc.want || next != tc.next || ok != tc.ok {
			t.Errorf("parseParamValue(%q, %d) = (%q, %d, %v), want (%q, %d, %v)", tc.in, tc.at, got, next, ok, tc.want, tc.next, tc.ok)
		}
	}
}

// covers: isSpace
func TestIsSpace(t *testing.T) {
	for c := 0; c < 256; c++ {
		if want := c == ' ' || c == '\t'; isSpace(byte(c)) != want {
			t.Errorf("isSpace(%d) = %v", c, !want)
		}
	}
}

// covers: hasRelToken
func TestHasRelToken(t *testing.T) {
	cases := map[string]bool{"next": true, "NEXT": true, "last next": true, "  next\t": true, "nexts": false, "": false, "prev": false, "x-next": false}
	for in, want := range cases {
		if got := hasRelToken(in, "next"); got != want {
			t.Errorf("hasRelToken(%q) = %v, want %v", in, got, want)
		}
	}
}

// covers: resolve
func TestResolve(t *testing.T) {
	base := mustURL(t, "http://h:81/a/b?x=1")
	cases := []struct {
		in   string
		base *url.URL
		want string
		ok   bool
	}{
		{"https://x/y", base, "https://x/y", true},
		{"/z", base, "http://h:81/z", true},
		{"c", base, "http://h:81/a/c", true},
		{"../d", base, "http://h:81/d", true},
		{"//other/p", base, "http://other/p", true},
		{" https://x/y ", nil, "https://x/y", true},
		{"/z", nil, "", false},
		{"mailto:a@b", base, "", false},
		{"http://", base, "", false},
		{"%zz", base, "", false},
	}
	for _, tc := range cases {
		got, ok := resolve(tc.in, tc.base)
		if got != tc.want || ok != tc.ok {
			t.Errorf("resolve(%q) = (%q, %v), want (%q, %v)", tc.in, got, ok, tc.want, tc.ok)
		}
	}
}
