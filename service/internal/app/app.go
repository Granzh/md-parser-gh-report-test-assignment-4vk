// Package app wires the ghreport pieces together: arguments, environment, fetching and output.
package app

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"

	"ghreport/internal/args"
	"ghreport/internal/cache"
	"ghreport/internal/clock"
	"ghreport/internal/config"
	"ghreport/internal/exitcode"
	"ghreport/internal/httpx"
	"ghreport/internal/issues"
	"ghreport/internal/model"
	"ghreport/internal/output"
	"ghreport/internal/trace"
)

// Exit codes beyond the ones in package exitcode.
const (
	// ExitInterrupted is returned when the run was cancelled by SIGINT/SIGTERM (128 + SIGINT).
	ExitInterrupted = 130
	// ExitIO is returned when the final report cannot be delivered (same value as exitcode.Fatal).
	ExitIO = exitcode.Fatal
)

// Deps are the replaceable dependencies of Run.
type Deps struct {
	Clock  clock.Clock         // pauses and trace timestamps; nil means the real clock
	HTTP   *http.Client        // only its Transport is used; nil means the default transport
	Now    func() time.Time    // time for generated_at; nil means Clock.Now
	Getenv func(string) string // environment lookup; nil means an empty environment
}

// Scrub removes the token from a text.
//
// Args: token is the secret; empty means nothing to remove. s is any text.
// Returns: s with every occurrence of token replaced by "***".
// Errors: never fails.
func Scrub(token, s string) string {
	if token == "" {
		return s
	}
	return strings.ReplaceAll(s, token, "***")
}

// scrubWriter passes everything through Scrub before writing.
type scrubWriter struct {
	w     io.Writer
	token string
}

// Write scrubs p and forwards it.
//
// Args: p is the text to write; a token split across two Write calls is not detected, so diagnostics are written
// one complete message per call.
// Returns: len(p) on success (the reported length is that of the original p), or the underlying error.
// Errors: the error of the underlying writer.
func (s *scrubWriter) Write(p []byte) (int, error) {
	if _, err := io.WriteString(s.w, Scrub(s.token, string(p))); err != nil {
		return 0, err
	}
	return len(p), nil
}

// Run executes the whole service.
//
// Args: ctx is cancelled on SIGINT/SIGTERM; args are the command-line arguments without the program name; deps are
// the dependencies (nil fields get production defaults); stdout receives only the report or a fatal JSON object;
// stderr receives all diagnostics, scrubbed of the token.
// Returns: 0 all repositories succeeded; 1 some failed (report written); 2 invalid arguments or environment, or the
// trace file cannot be opened (nothing else is done); 3 fatal: HTTP 401 (stdout {"error":"unauthorized"}, no report),
// every repository failed (stdout {"error":"all_failed"}), or the report could not be written (stdout empty);
// ExitInterrupted when ctx was cancelled (stdout empty). -h/--help prints usage to stderr and returns 0.
// Errors: never fails; every problem is mapped to a code and a stderr message.
func Run(ctx context.Context, argv []string, deps Deps, stdout, stderr io.Writer) int {
	getenv := deps.Getenv
	if getenv == nil {
		getenv = func(string) string { return "" }
	}
	token := strings.TrimSpace(getenv("GITHUB_TOKEN"))
	errw := &scrubWriter{w: stderr, token: token}

	opts, err := args.Parse(argv)
	if err != nil {
		if errors.Is(err, args.ErrHelp) {
			fmt.Fprint(errw, args.Usage())
			return 0
		}
		fmt.Fprintln(errw, err)
		return exitcode.Usage
	}
	cfg, err := config.FromEnv(getenv)
	if err != nil {
		fmt.Fprintln(errw, err)
		return exitcode.Usage
	}
	tw, err := trace.New(opts.Trace)
	if err != nil {
		fmt.Fprintf(errw, "ghreport: не удалось открыть файл журнала: %v\n", err)
		return exitcode.Usage
	}
	defer tw.Close()

	clk := deps.Clock
	if clk == nil {
		clk = clock.Real{}
	}
	now := deps.Now
	if now == nil {
		now = clk.Now
	}
	client := &httpx.Client{HTTP: deps.HTTP, Clock: clk, Trace: tw, Token: cfg.Token, BaseOrigin: httpx.Origin(cfg.BaseURL)}
	fetcher := &issues.Fetcher{Client: client, Cache: prepareCache(opts.CacheDir, errw), Base: cfg.BaseURL, Warn: errw}

	repos, status := collect(ctx, fetcher, opts, errw)
	switch status {
	case statusUnauthorized:
		fmt.Fprintln(errw, "ghreport: ответ 401: токен недействителен или не передан")
		writeStdout(stdout, errw, model.FatalJSON(model.FatalUnauthorized))
		return exitcode.Fatal
	case statusInterrupted:
		fmt.Fprintln(errw, "ghreport: прервано сигналом")
		return ExitInterrupted
	}
	return finish(opts, model.Report{GeneratedAt: model.FormatTimestamp(now()), Repos: repos}, stdout, errw)
}

// Collection statuses returned by collect.
const (
	statusDone = iota
	statusUnauthorized
	statusInterrupted
)

// prepareCache creates the cache directory.
//
// Args: dir is the --cache-dir value (non-empty); errw receives a warning on failure.
// Returns: a Store, or nil (work without cache) when the directory cannot be created.
// Errors: never fails; the problem is reported as a warning.
func prepareCache(dir string, errw io.Writer) *cache.Store {
	if err := os.MkdirAll(dir, 0o700); err != nil {
		fmt.Fprintf(errw, "ghreport: предупреждение: каталог кэша недоступен, работа без кэша: %v\n", err)
		return nil
	}
	return &cache.Store{Dir: dir}
}

// collect processes the repositories strictly one after another, in --repos order.
//
// Args: ctx is the run context; f is the fetcher; opts are the validated options; errw receives one diagnostic line
// per failed repository.
// Returns: the per-repository reports (failed ones carry an error code, count 0 and an empty issues list) and a
// status: statusUnauthorized stops at the first 401 (the remaining repositories are not requested, repos is nil),
// statusInterrupted when ctx was cancelled (repos is nil), otherwise statusDone.
// Errors: never fails.
func collect(ctx context.Context, f *issues.Fetcher, opts args.Options, errw io.Writer) ([]model.RepoReport, int) {
	reports := make([]model.RepoReport, 0, len(opts.Repos))
	for _, repo := range opts.Repos {
		list, err := f.FetchOpenIssues(ctx, repo, opts.Limit, opts.Since)
		if err != nil && ctx.Err() != nil {
			return nil, statusInterrupted
		}
		if err != nil {
			code, unauthorized := issues.ErrorCode(err)
			if unauthorized {
				return nil, statusUnauthorized
			}
			fmt.Fprintf(errw, "ghreport: %s: %s\n", repo, strings.TrimPrefix(err.Error(), "ghreport: "))
			reports = append(reports, model.NewFailedRepoReport(repo.String(), code))
			continue
		}
		r := model.NewRepoReport(repo.String())
		r.Issues, r.Count = list, len(list)
		reports = append(reports, r)
	}
	return reports, statusDone
}

// finish delivers the report and picks the exit code.
//
// Args: opts are the options (Out is the destination); report is the complete report; stdout and errw as in Run.
// Returns: exitcode.OK or exitcode.Partial after the report was written to opts.Out ("-" is stdout). When every
// repository failed: the report is still written to a file destination, but nothing goes to stdout except
// {"error":"all_failed"}; the code is exitcode.Fatal. ExitIO when the report cannot be rendered or written.
// Errors: never fails; problems are mapped to codes and stderr messages.
func finish(opts args.Options, report model.Report, stdout io.Writer, errw io.Writer) int {
	code, fatal := exitcode.Summarize(report.Repos)
	data, err := model.RenderReport(report)
	if err != nil {
		fmt.Fprintf(errw, "ghreport: не удалось сформировать отчёт: %v\n", err)
		return ExitIO
	}
	if fatal != "" {
		if opts.Out != output.Stdout {
			if err := output.WriteReport(opts.Out, stdout, data); err != nil {
				fmt.Fprintln(errw, err)
			}
		}
		writeStdout(stdout, errw, model.FatalJSON(fatal))
		return code
	}
	if err := output.WriteReport(opts.Out, stdout, data); err != nil {
		fmt.Fprintln(errw, err)
		return ExitIO
	}
	return code
}

// writeStdout writes a complete document to stdout, reporting failures on stderr.
//
// Args: stdout is the destination; errw receives a message on failure; data is the document.
// Returns: nothing.
// Errors: never fails; a write error becomes a stderr message.
func writeStdout(stdout, errw io.Writer, data []byte) {
	if _, err := stdout.Write(data); err != nil {
		fmt.Fprintf(errw, "ghreport: не удалось записать в стандартный вывод: %v\n", err)
	}
}
