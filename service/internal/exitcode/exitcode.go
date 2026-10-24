// Package exitcode maps repository outcomes to process exit codes.
package exitcode

import "ghreport/internal/model"

// Process exit codes (spec 4.4.6).
const (
	OK      = 0 // every repository succeeded
	Partial = 1 // some repositories failed
	Usage   = 2 // invalid command line
	Fatal   = 3 // 401 or every repository failed
)

// Summarize derives the exit code from per-repository results.
//
// Args: repos are the results in any order; may be empty.
// Returns: code OK with empty fatal when no repository has an error (or repos is empty); Partial with empty fatal
// when some but not all have one; Fatal with fatal == model.FatalAllFailed when every repository has an error,
// including the case of a single repository.
// Errors: never fails.
func Summarize(repos []model.RepoReport) (code int, fatal string) {
	failed := 0
	for _, r := range repos {
		if r.Error != nil {
			failed++
		}
	}
	switch {
	case failed == 0:
		return OK, ""
	case failed == len(repos):
		return Fatal, model.FatalAllFailed
	default:
		return Partial, ""
	}
}
