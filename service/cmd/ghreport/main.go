// Command ghreport prints a summary of open GitHub issues for the given repositories.
package main

import (
	"context"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"ghreport/internal/app"
	"ghreport/internal/clock"
)

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	deps := app.Deps{
		Clock:  clock.Real{},
		HTTP:   &http.Client{}, // no client-wide timeout: httpx applies the per-request one
		Now:    time.Now,
		Getenv: os.Getenv,
	}
	code := app.Run(ctx, os.Args[1:], deps, os.Stdout, os.Stderr)
	stop()
	os.Exit(code)
}
