// Package args parses and validates the ghreport command line.
package args

import (
	"errors"
	"flag"
	"fmt"
	"io"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// Defaults applied by Parse when the corresponding flag is absent.
const (
	DefaultLimit    = 100
	DefaultOut      = "-"
	DefaultCacheDir = "./.ghreport-cache"
)

// ErrHelp is returned by Parse when -h or --help was requested.
var ErrHelp = errors.New("help requested")

var (
	limitRe = regexp.MustCompile(`^[0-9]+$`)
	sinceRe = regexp.MustCompile(`^[0-9]{4}-[0-9]{2}-[0-9]{2}$`)
	partRe  = regexp.MustCompile(`^[A-Za-z0-9._-]+$`)
)

// RepoRef identifies a GitHub repository.
type RepoRef struct {
	Owner string
	Name  string
}

// Options is the validated command line.
type Options struct {
	Repos    []RepoRef
	Limit    int
	Since    *time.Time // 00:00:00 UTC of the requested day, nil when absent
	Out      string
	Trace    string
	CacheDir string
}

// UsageError reports an invalid command line; Msg is a Russian sentence for stderr.
type UsageError struct {
	Msg string
}

// Error returns the message of the usage error.
//
// Args: none.
// Returns: the Russian message, already prefixed with "ghreport: ".
// Errors: never fails.
func (e *UsageError) Error() string {
	return e.Msg
}

// usageErrorf builds a *UsageError with the "ghreport: " prefix.
//
// Args: format is a fmt format string; a are its arguments; neither may carry secret values.
// Returns: a non-nil *UsageError.
// Errors: never fails.
func usageErrorf(format string, a ...any) *UsageError {
	return &UsageError{Msg: "ghreport: " + fmt.Sprintf(format, a...)}
}

// String renders the reference as "owner/name".
//
// Args: none.
// Returns: the canonical owner/name spelling.
// Errors: never fails.
func (r RepoRef) String() string {
	return r.Owner + "/" + r.Name
}

// Usage returns the help text printed for -h/--help.
//
// Args: none.
// Returns: multi-line Russian text ending with a newline.
// Errors: never fails.
func Usage() string {
	return `Использование:
  ghreport --repos <owner/repo[,owner/repo...]>
           [--limit <N>] [--since <YYYY-MM-DD>]
           [--out <file|->] [--trace <file>] [--cache-dir <dir>]

Параметры:
  --repos      обязательный список репозиториев через запятую
  --limit      максимум issue на репозиторий, целое >= 1 (по умолчанию 100)
  --since      только issue, созданные не раньше даты (00:00:00 UTC)
  --out        файл отчёта; "-" означает стандартный вывод (по умолчанию)
  --trace      файл журнала HTTP-обмена (JSON Lines)
  --cache-dir  каталог кэша (по умолчанию ./.ghreport-cache)

Переменные окружения: GITHUB_TOKEN, GITHUB_API_BASE_URL.
`
}

// Parse converts command-line arguments into validated Options.
//
// Args: args are the arguments WITHOUT the program name; may be empty.
// Returns: Options with defaults applied; zero value when err != nil.
// Errors: *UsageError (message in Russian, no secrets) for unknown flags, missing --repos, bad values and
// positional arguments; ErrHelp when -h/--help was given. Nothing is written to disk or network.
func Parse(args []string) (Options, error) {
	fs := flag.NewFlagSet("ghreport", flag.ContinueOnError)
	fs.SetOutput(io.Discard)
	fs.Usage = func() {}
	repos := fs.String("repos", "", "")
	limit := fs.String("limit", "", "")
	since := fs.String("since", "", "")
	out := fs.String("out", DefaultOut, "")
	trace := fs.String("trace", "", "")
	cacheDir := fs.String("cache-dir", DefaultCacheDir, "")
	if err := fs.Parse(args); err != nil {
		return Options{}, translateFlagError(err)
	}
	if fs.NArg() > 0 {
		return Options{}, usageErrorf("позиционные аргументы не поддерживаются")
	}
	seen := map[string]bool{}
	fs.Visit(func(f *flag.Flag) { seen[f.Name] = true })

	opts := Options{Limit: DefaultLimit}
	if !seen["repos"] {
		return Options{}, usageErrorf("параметр --repos обязателен")
	}
	var err error
	if opts.Repos, err = ParseRepos(*repos); err != nil {
		return Options{}, err
	}
	if seen["limit"] {
		if opts.Limit, err = parseLimit(*limit); err != nil {
			return Options{}, err
		}
	}
	if seen["since"] {
		t, err := parseSince(*since)
		if err != nil {
			return Options{}, err
		}
		opts.Since = &t
	}
	for _, p := range []struct {
		name string
		val  string
		dst  *string
		set  bool
	}{
		{"--out", *out, &opts.Out, seen["out"]},
		{"--trace", *trace, &opts.Trace, seen["trace"]},
		{"--cache-dir", *cacheDir, &opts.CacheDir, seen["cache-dir"]},
	} {
		if p.set && p.val == "" {
			return Options{}, usageErrorf("%s не может быть пустым", p.name)
		}
		*p.dst = p.val
	}
	return opts, nil
}

// translateFlagError converts an error from flag.FlagSet.Parse into a Russian usage error.
//
// Args: err is a non-nil error returned by flag.FlagSet.Parse.
// Returns: ErrHelp for flag.ErrHelp, otherwise a *UsageError naming only the flag, never its value.
// Errors: never fails; the result is itself the error.
func translateFlagError(err error) error {
	if errors.Is(err, flag.ErrHelp) {
		return ErrHelp
	}
	msg := err.Error()
	if rest, ok := strings.CutPrefix(msg, "flag provided but not defined: "); ok {
		return usageErrorf("неизвестный параметр %s", rest)
	}
	if rest, ok := strings.CutPrefix(msg, "flag needs an argument: "); ok {
		return usageErrorf("параметру %s не передано значение", rest)
	}
	return usageErrorf("неверный синтаксис параметров")
}

// parseLimit validates the --limit value.
//
// Args: s is the raw value; only ASCII digits are accepted, no sign and no spaces.
// Returns: the integer, at least 1 and fitting into int.
// Errors: *UsageError for non-numeric, zero and overflowing values.
func parseLimit(s string) (int, error) {
	if limitRe.MatchString(s) {
		if n, err := strconv.Atoi(s); err == nil && n >= 1 {
			return n, nil
		}
	}
	return 0, usageErrorf("--limit должен быть целым числом, не меньшим единицы")
}

// parseSince validates the --since value.
//
// Args: s is the raw value, strictly YYYY-MM-DD with a real calendar date.
// Returns: midnight UTC of that day.
// Errors: *UsageError for any other spelling or a non-existent date.
func parseSince(s string) (time.Time, error) {
	if sinceRe.MatchString(s) {
		if t, err := time.Parse("2006-01-02", s); err == nil {
			return t.UTC(), nil
		}
	}
	return time.Time{}, usageErrorf("--since должен быть датой в формате ГГГГ-ММ-ДД")
}

// validRepoPart tells whether s is acceptable as an owner or repository name.
//
// Args: s is the candidate; may be empty.
// Returns: true for a non-empty string of [A-Za-z0-9._-] that is not "." or "..".
// Errors: never fails.
func validRepoPart(s string) bool {
	return s != "." && s != ".." && partRe.MatchString(s)
}

// ParseRepos splits a comma-separated list of owner/repo items.
//
// Args: s is the raw --repos value; items must not be empty or contain spaces or extra slashes.
// Returns: references in order of first occurrence; exact-string duplicates are dropped.
// Errors: *UsageError for an empty list, an empty item or an item not of the form owner/repo.
func ParseRepos(s string) ([]RepoRef, error) {
	if s == "" {
		return nil, usageErrorf("--repos не может быть пустым")
	}
	var refs []RepoRef
	seen := map[string]bool{}
	for _, item := range strings.Split(s, ",") {
		if item == "" {
			return nil, usageErrorf("--repos содержит пустой элемент")
		}
		owner, name, ok := strings.Cut(item, "/")
		if !ok || !validRepoPart(owner) || !validRepoPart(name) {
			return nil, usageErrorf("--repos: элемент %q должен иметь вид owner/repo (допустимы символы A-Za-z0-9._-)", item)
		}
		if seen[item] {
			continue
		}
		seen[item] = true
		refs = append(refs, RepoRef{Owner: owner, Name: name})
	}
	return refs, nil
}
