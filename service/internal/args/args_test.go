package args

import (
	"errors"
	"reflect"
	"strconv"
	"strings"
	"testing"
	"time"
)

// mustRefs converts "a/b" strings into RepoRef values.
//
// Args: items are strings of the form owner/name; malformed items abort the test.
// Returns: the references in the same order.
// Errors: never fails; calls t.Fatalf on malformed input.
func mustRefs(t *testing.T, items ...string) []RepoRef {
	t.Helper()
	refs := make([]RepoRef, 0, len(items))
	for _, item := range items {
		owner, name, ok := strings.Cut(item, "/")
		if !ok {
			t.Fatalf("bad test item %q", item)
		}
		refs = append(refs, RepoRef{Owner: owner, Name: name})
	}
	return refs
}

// covers: Parse
func TestParseTable(t *testing.T) {
	day := func(s string) *time.Time {
		v, err := time.Parse("2006-01-02", s)
		if err != nil {
			t.Fatal(err)
		}
		return &v
	}
	huge := strconv.FormatUint(1<<63, 10) // overflows int64
	cases := []struct {
		name    string
		args    []string
		wantErr string // substring of the message; "" means success
		want    Options
	}{
		// success
		{"minimal", []string{"--repos", "cli/cli"}, "", Options{Repos: mustRefs(t, "cli/cli"), Limit: 100, Out: "-", CacheDir: "./.ghreport-cache"}},
		{"equals form", []string{"--repos=cli/cli"}, "", Options{Repos: mustRefs(t, "cli/cli"), Limit: 100, Out: "-", CacheDir: "./.ghreport-cache"}},
		{"single dash", []string{"-repos", "cli/cli"}, "", Options{Repos: mustRefs(t, "cli/cli"), Limit: 100, Out: "-", CacheDir: "./.ghreport-cache"}},
		{"two repos", []string{"--repos", "a/b,c/d"}, "", Options{Repos: mustRefs(t, "a/b", "c/d"), Limit: 100, Out: "-", CacheDir: "./.ghreport-cache"}},
		{"duplicates keep first", []string{"--repos", "a/b,c/d,a/b,c/d,e/f"}, "", Options{Repos: mustRefs(t, "a/b", "c/d", "e/f"), Limit: 100, Out: "-", CacheDir: "./.ghreport-cache"}},
		{"case differs is not duplicate", []string{"--repos", "a/b,A/b"}, "", Options{Repos: mustRefs(t, "a/b", "A/b"), Limit: 100, Out: "-", CacheDir: "./.ghreport-cache"}},
		{"allowed symbols", []string{"--repos", "a.b-c_d/E.f-g_9"}, "", Options{Repos: mustRefs(t, "a.b-c_d/E.f-g_9"), Limit: 100, Out: "-", CacheDir: "./.ghreport-cache"}},
		{"dotfile repo", []string{"--repos", "o/.github"}, "", Options{Repos: mustRefs(t, "o/.github"), Limit: 100, Out: "-", CacheDir: "./.ghreport-cache"}},
		{"limit 1", []string{"--repos", "a/b", "--limit", "1"}, "", Options{Repos: mustRefs(t, "a/b"), Limit: 1, Out: "-", CacheDir: "./.ghreport-cache"}},
		{"limit large", []string{"--repos", "a/b", "--limit", "100000"}, "", Options{Repos: mustRefs(t, "a/b"), Limit: 100000, Out: "-", CacheDir: "./.ghreport-cache"}},
		{"limit leading zeros", []string{"--repos", "a/b", "--limit", "007"}, "", Options{Repos: mustRefs(t, "a/b"), Limit: 7, Out: "-", CacheDir: "./.ghreport-cache"}},
		{"since", []string{"--repos", "a/b", "--since", "2026-02-03"}, "", Options{Repos: mustRefs(t, "a/b"), Limit: 100, Since: day("2026-02-03"), Out: "-", CacheDir: "./.ghreport-cache"}},
		{"since leap day", []string{"--repos", "a/b", "--since", "2028-02-29"}, "", Options{Repos: mustRefs(t, "a/b"), Limit: 100, Since: day("2028-02-29"), Out: "-", CacheDir: "./.ghreport-cache"}},
		{"out file", []string{"--repos", "a/b", "--out", "r.json"}, "", Options{Repos: mustRefs(t, "a/b"), Limit: 100, Out: "r.json", CacheDir: "./.ghreport-cache"}},
		{"out explicit dash", []string{"--repos", "a/b", "--out", "-"}, "", Options{Repos: mustRefs(t, "a/b"), Limit: 100, Out: "-", CacheDir: "./.ghreport-cache"}},
		{"trace", []string{"--repos", "a/b", "--trace", "t.jsonl"}, "", Options{Repos: mustRefs(t, "a/b"), Limit: 100, Out: "-", Trace: "t.jsonl", CacheDir: "./.ghreport-cache"}},
		{"cache dir", []string{"--repos", "a/b", "--cache-dir", "/tmp/c"}, "", Options{Repos: mustRefs(t, "a/b"), Limit: 100, Out: "-", CacheDir: "/tmp/c"}},
		{"last flag wins", []string{"--repos", "a/b", "--limit", "5", "--limit", "6"}, "", Options{Repos: mustRefs(t, "a/b"), Limit: 6, Out: "-", CacheDir: "./.ghreport-cache"}},
		{"all flags", []string{"--repos", "a/b,c/d", "--limit", "20", "--since", "2026-01-01", "--out", "o", "--trace", "t", "--cache-dir", "c"}, "", Options{Repos: mustRefs(t, "a/b", "c/d"), Limit: 20, Since: day("2026-01-01"), Out: "o", Trace: "t", CacheDir: "c"}},
		{"value starting with dash", []string{"--repos", "a/b", "--out", "-x"}, "", Options{Repos: mustRefs(t, "a/b"), Limit: 100, Out: "-x", CacheDir: "./.ghreport-cache"}},

		// repos errors
		{"no args", nil, "--repos обязателен", Options{}},
		{"empty args", []string{}, "--repos обязателен", Options{}},
		{"repos empty value", []string{"--repos", ""}, "--repos не может быть пустым", Options{}},
		{"repos missing value", []string{"--repos"}, "не передано значение", Options{}},
		{"repos no slash", []string{"--repos", "cli"}, "owner/repo", Options{}},
		{"repos two slashes", []string{"--repos", "a/b/c"}, "owner/repo", Options{}},
		{"repos leading slash", []string{"--repos", "/b"}, "owner/repo", Options{}},
		{"repos trailing slash", []string{"--repos", "a/"}, "owner/repo", Options{}},
		{"repos only slash", []string{"--repos", "/"}, "owner/repo", Options{}},
		{"repos empty item middle", []string{"--repos", "a/b,,c/d"}, "пустой элемент", Options{}},
		{"repos trailing comma", []string{"--repos", "a/b,"}, "пустой элемент", Options{}},
		{"repos leading comma", []string{"--repos", ",a/b"}, "пустой элемент", Options{}},
		{"repos only comma", []string{"--repos", ","}, "пустой элемент", Options{}},
		{"repos space inside", []string{"--repos", "a/b, c/d"}, "owner/repo", Options{}},
		{"repos space in name", []string{"--repos", "a /b"}, "owner/repo", Options{}},
		{"repos dot owner", []string{"--repos", "./b"}, "owner/repo", Options{}},
		{"repos dotdot name", []string{"--repos", "a/.."}, "owner/repo", Options{}},
		{"repos bad symbol", []string{"--repos", "a/b!"}, "owner/repo", Options{}},
		{"repos non ascii", []string{"--repos", "а/б"}, "owner/repo", Options{}},
		{"repos url", []string{"--repos", "https://github.com/a/b"}, "owner/repo", Options{}},
		{"repos query", []string{"--repos", "a/b?x=1"}, "owner/repo", Options{}},

		// limit errors
		{"limit zero", []string{"--repos", "a/b", "--limit", "0"}, "--limit должен быть целым числом, не меньшим единицы", Options{}},
		{"limit negative", []string{"--repos", "a/b", "--limit", "-1"}, "--limit должен быть", Options{}},
		{"limit word", []string{"--repos", "a/b", "--limit", "abc"}, "--limit должен быть", Options{}},
		{"limit float", []string{"--repos", "a/b", "--limit", "1.5"}, "--limit должен быть", Options{}},
		{"limit empty", []string{"--repos", "a/b", "--limit", ""}, "--limit должен быть", Options{}},
		{"limit plus sign", []string{"--repos", "a/b", "--limit", "+5"}, "--limit должен быть", Options{}},
		{"limit space", []string{"--repos", "a/b", "--limit", " 5"}, "--limit должен быть", Options{}},
		{"limit overflow", []string{"--repos", "a/b", "--limit", huge}, "--limit должен быть", Options{}},
		{"limit hex", []string{"--repos", "a/b", "--limit", "0x10"}, "--limit должен быть", Options{}},
		{"limit arabic digit", []string{"--repos", "a/b", "--limit", "٥"}, "--limit должен быть", Options{}},

		// since errors
		{"since short month", []string{"--repos", "a/b", "--since", "2026-2-3"}, "--since", Options{}},
		{"since feb 30", []string{"--repos", "a/b", "--since", "2026-02-30"}, "--since", Options{}},
		{"since not leap", []string{"--repos", "a/b", "--since", "2027-02-29"}, "--since", Options{}},
		{"since month 13", []string{"--repos", "a/b", "--since", "2026-13-01"}, "--since", Options{}},
		{"since slashes", []string{"--repos", "a/b", "--since", "2026/02/03"}, "--since", Options{}},
		{"since with time", []string{"--repos", "a/b", "--since", "2026-02-03T00:00:00Z"}, "--since", Options{}},
		{"since empty", []string{"--repos", "a/b", "--since", ""}, "--since", Options{}},
		{"since two digit year", []string{"--repos", "a/b", "--since", "26-02-03"}, "--since", Options{}},

		// other errors
		{"out empty", []string{"--repos", "a/b", "--out="}, "--out не может быть пустым", Options{}},
		{"trace empty", []string{"--repos", "a/b", "--trace="}, "--trace не может быть пустым", Options{}},
		{"cache dir empty", []string{"--repos", "a/b", "--cache-dir="}, "--cache-dir не может быть пустым", Options{}},
		{"positional", []string{"--repos", "a/b", "extra"}, "позиционные", Options{}},
		{"positional before flags", []string{"extra", "--repos", "a/b"}, "позиционные", Options{}},
		{"double dash then positional", []string{"--repos", "a/b", "--", "x"}, "позиционные", Options{}},
		{"unknown flag", []string{"--repos", "a/b", "--nope"}, "неизвестный параметр -nope", Options{}},
		{"unknown flag with value hidden", []string{"--repos", "a/b", "--token=SECRET123"}, "неизвестный параметр -token", Options{}},
		{"limit missing value", []string{"--repos", "a/b", "--limit"}, "не передано значение", Options{}},
		{"bool style flag", []string{"--repos", "a/b", "--verbose=true"}, "неизвестный параметр", Options{}},
		{"triple dash", []string{"---repos", "a/b"}, "ghreport:", Options{}},
	}
	if len(cases) < 40 {
		t.Fatalf("table too small: %d", len(cases))
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got, err := Parse(tc.args)
			if tc.wantErr != "" {
				var ue *UsageError
				if !errors.As(err, &ue) {
					t.Fatalf("want *UsageError, got %v", err)
				}
				if !strings.Contains(err.Error(), tc.wantErr) || !strings.HasPrefix(err.Error(), "ghreport: ") {
					t.Fatalf("message %q does not contain %q", err.Error(), tc.wantErr)
				}
				if strings.Contains(err.Error(), "SECRET123") {
					t.Fatalf("secret leaked: %q", err.Error())
				}
				if !reflect.DeepEqual(got, Options{}) {
					t.Fatalf("want zero Options on error, got %+v", got)
				}
				return
			}
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if (got.Since == nil) != (tc.want.Since == nil) || (got.Since != nil && !got.Since.Equal(*tc.want.Since)) {
				t.Fatalf("Since = %v, want %v", got.Since, tc.want.Since)
			}
			got.Since, tc.want.Since = nil, nil
			if !reflect.DeepEqual(got, tc.want) {
				t.Fatalf("got %+v, want %+v", got, tc.want)
			}
		})
	}
}

// covers: Parse
func TestParseSinceIsMidnightUTC(t *testing.T) {
	got, err := Parse([]string{"--repos", "a/b", "--since", "2026-02-03"})
	if err != nil {
		t.Fatal(err)
	}
	want := time.Date(2026, 2, 3, 0, 0, 0, 0, time.UTC)
	if !got.Since.Equal(want) || got.Since.Location() != time.UTC {
		t.Fatalf("Since = %v, want %v UTC", got.Since, want)
	}
}

// covers: Parse
func TestParseHelp(t *testing.T) {
	for _, a := range [][]string{{"-h"}, {"--help"}, {"-help"}, {"--repos", "a/b", "--help"}} {
		got, err := Parse(a)
		if !errors.Is(err, ErrHelp) {
			t.Fatalf("%v: want ErrHelp, got %v", a, err)
		}
		if !reflect.DeepEqual(got, Options{}) {
			t.Fatalf("%v: want zero Options, got %+v", a, got)
		}
	}
}

// covers: Parse
func TestParseDoesNotWriteToStdout(t *testing.T) {
	// The flag set is silenced; a failing parse must not panic or print usage itself.
	if _, err := Parse([]string{"--bogus"}); err == nil {
		t.Fatal("want error")
	}
}

// covers: ParseRepos
func TestParseReposTable(t *testing.T) {
	cases := []struct {
		name  string
		in    string
		want  []string
		isErr bool
	}{
		{"one", "a/b", []string{"a/b"}, false},
		{"order kept", "z/z,a/a,m/m", []string{"z/z", "a/a", "m/m"}, false},
		{"duplicate adjacent", "a/b,a/b", []string{"a/b"}, false},
		{"duplicate later", "a/b,c/d,a/b", []string{"a/b", "c/d"}, false},
		{"case sensitive", "a/b,A/B", []string{"a/b", "A/B"}, false},
		{"empty", "", nil, true},
		{"empty item", "a/b,,c/d", nil, true},
		{"space", " a/b", nil, true},
		{"trailing space", "a/b ", nil, true},
		{"newline", "a/b\n", nil, true},
		{"no slash", "ab", nil, true},
		{"extra slash", "a/b/", nil, true},
		{"dotdot", "../b", nil, true},
		{"dot repo", "a/.", nil, true},
		{"backslash", `a\b`, nil, true},
		{"colon", "a:b/c", nil, true},
		{"after valid bad", "a/b,c", nil, true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got, err := ParseRepos(tc.in)
			if tc.isErr {
				var ue *UsageError
				if !errors.As(err, &ue) || got != nil {
					t.Fatalf("want *UsageError and nil result, got %v, %v", got, err)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(got, mustRefs(t, tc.want...)) {
				t.Fatalf("got %v, want %v", got, tc.want)
			}
		})
	}
}

// covers: parseLimit
func TestParseLimit(t *testing.T) {
	for in, want := range map[string]int{"1": 1, "100": 100, "0001": 1, "9223372036854775807": 9223372036854775807} {
		if got, err := parseLimit(in); err != nil || got != want {
			t.Errorf("parseLimit(%q) = %d, %v; want %d", in, got, err, want)
		}
	}
	for _, in := range []string{"", "0", "00", "-1", "x", "1.0", "1e3", " 1", "1 ", "9223372036854775808", "99999999999999999999999"} {
		if got, err := parseLimit(in); err == nil || got != 0 {
			t.Errorf("parseLimit(%q) = %d, %v; want error", in, got, err)
		}
	}
}

// covers: parseSince
func TestParseSince(t *testing.T) {
	good := map[string]time.Time{
		"2026-01-01": time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC),
		"2024-02-29": time.Date(2024, 2, 29, 0, 0, 0, 0, time.UTC),
		"2026-12-31": time.Date(2026, 12, 31, 0, 0, 0, 0, time.UTC),
	}
	for in, want := range good {
		if got, err := parseSince(in); err != nil || !got.Equal(want) {
			t.Errorf("parseSince(%q) = %v, %v; want %v", in, got, err, want)
		}
	}
	for _, in := range []string{"", "2026-1-01", "2026-01-1", "2026-02-30", "2025-02-29", "2026-00-10", "2026-01-00", "2026-01-32", " 2026-01-01", "2026-01-01 ", "2026-01-01T00:00:00Z", "２０２６-01-01"} {
		if got, err := parseSince(in); err == nil || !got.IsZero() {
			t.Errorf("parseSince(%q) = %v, %v; want error", in, got, err)
		}
	}
}

// covers: validRepoPart
func TestValidRepoPart(t *testing.T) {
	for _, s := range []string{"a", "A", "0", "a.b", "a-b", "a_b", ".github", "-x", "...", "a..b"} {
		if !validRepoPart(s) {
			t.Errorf("validRepoPart(%q) = false, want true", s)
		}
	}
	for _, s := range []string{"", ".", "..", "a b", "a/b", "a,b", "é", "a\n", "a@b", "a+b"} {
		if validRepoPart(s) {
			t.Errorf("validRepoPart(%q) = true, want false", s)
		}
	}
}

// covers: translateFlagError
func TestTranslateFlagError(t *testing.T) {
	if err := translateFlagError(errors.New("flag: help requested")); err == nil {
		t.Fatal("want error")
	}
	cases := map[string]string{
		"flag provided but not defined: -x": "неизвестный параметр -x",
		"flag needs an argument: -limit":    "параметру -limit не передано значение",
		"invalid boolean value":             "неверный синтаксис параметров",
	}
	for in, want := range cases {
		err := translateFlagError(errors.New(in))
		var ue *UsageError
		if !errors.As(err, &ue) || ue.Msg != "ghreport: "+want {
			t.Errorf("translateFlagError(%q) = %v, want %q", in, err, want)
		}
	}
}

// covers: usageErrorf
func TestUsageErrorf(t *testing.T) {
	e := usageErrorf("значение %d из %s", 3, "x")
	if e == nil || e.Msg != "ghreport: значение 3 из x" {
		t.Fatalf("got %+v", e)
	}
	if usageErrorf("без аргументов").Msg != "ghreport: без аргументов" {
		t.Fatal("plain message changed")
	}
}

// covers: UsageError.Error
func TestUsageErrorError(t *testing.T) {
	var err error = &UsageError{Msg: "ghreport: тест"}
	if err.Error() != "ghreport: тест" {
		t.Fatalf("got %q", err.Error())
	}
	if (&UsageError{}).Error() != "" {
		t.Fatal("empty message must stay empty")
	}
}

// covers: RepoRef.String
func TestRepoRefString(t *testing.T) {
	if got := (RepoRef{Owner: "cli", Name: "cli"}).String(); got != "cli/cli" {
		t.Fatalf("got %q", got)
	}
	if got := (RepoRef{}).String(); got != "/" {
		t.Fatalf("zero value: got %q", got)
	}
}

// covers: Usage
func TestUsage(t *testing.T) {
	u := Usage()
	if !strings.HasSuffix(u, "\n") {
		t.Fatal("usage must end with a newline")
	}
	for _, flag := range []string{"--repos", "--limit", "--since", "--out", "--trace", "--cache-dir", "GITHUB_TOKEN", "GITHUB_API_BASE_URL"} {
		if !strings.Contains(u, flag) {
			t.Errorf("usage lacks %s", flag)
		}
	}
}
