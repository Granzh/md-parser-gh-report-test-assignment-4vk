package exitcode

import (
	"testing"

	"ghreport/internal/model"
)

// covers: Summarize
func TestSummarizeTable(t *testing.T) {
	ok := func() model.RepoReport { return model.NewRepoReport("a/ok") }
	bad := func(code string) model.RepoReport { return model.NewFailedRepoReport("a/bad", code) }
	cases := []struct {
		name      string
		repos     []model.RepoReport
		wantCode  int
		wantFatal string
	}{
		{"nil", nil, OK, ""},
		{"empty", []model.RepoReport{}, OK, ""},
		{"single ok", []model.RepoReport{ok()}, OK, ""},
		{"all ok", []model.RepoReport{ok(), ok(), ok()}, OK, ""},
		{"single failed", []model.RepoReport{bad(model.ErrNotFound)}, Fatal, model.FatalAllFailed},
		{"single rate limited", []model.RepoReport{bad(model.ErrRateLimited)}, Fatal, model.FatalAllFailed},
		{"all failed", []model.RepoReport{bad(model.ErrNotFound), bad(model.ErrRequestFailed)}, Fatal, model.FatalAllFailed},
		{"one of two failed", []model.RepoReport{ok(), bad(model.ErrNotFound)}, Partial, ""},
		{"first failed", []model.RepoReport{bad(model.ErrInvalidResponse), ok()}, Partial, ""},
		{"one of many failed", []model.RepoReport{ok(), ok(), bad(model.ErrNotFound), ok()}, Partial, ""},
		{"all but one failed", []model.RepoReport{bad(model.ErrNotFound), bad(model.ErrNotFound), ok()}, Partial, ""},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			code, fatal := Summarize(tc.repos)
			if code != tc.wantCode || fatal != tc.wantFatal {
				t.Fatalf("got (%d, %q), want (%d, %q)", code, fatal, tc.wantCode, tc.wantFatal)
			}
		})
	}
}

// covers: Summarize
func TestExitCodeValues(t *testing.T) {
	if OK != 0 || Partial != 1 || Usage != 2 || Fatal != 3 {
		t.Fatalf("codes changed: %d %d %d %d", OK, Partial, Usage, Fatal)
	}
}
