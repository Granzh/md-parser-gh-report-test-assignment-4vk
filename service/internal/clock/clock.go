// Package clock abstracts time so that retry and rate-limit logic can be tested without sleeping.
package clock

import (
	"context"
	"sync"
	"time"
)

// Clock provides the current time and cancellable pauses.
type Clock interface {
	// Now returns the current time.
	Now() time.Time
	// Sleep pauses for d or until ctx is done, whichever comes first.
	Sleep(ctx context.Context, d time.Duration) error
}

// Real is the wall-clock implementation of Clock.
type Real struct{}

// Now returns the current wall-clock time in UTC.
//
// Args: none.
// Returns: time.Now() converted to UTC.
// Errors: never fails.
func (Real) Now() time.Time {
	return time.Now().UTC()
}

// Sleep blocks for d, or until ctx is done.
//
// Args: ctx is the cancellation context, must not be nil; d is the pause, values <= 0 return immediately.
// Returns: nil after the pause elapsed (or d <= 0 with a live ctx).
// Errors: ctx.Err() when ctx is done before the pause ends; a ctx that is already done wins even for d <= 0.
func (Real) Sleep(ctx context.Context, d time.Duration) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	if d <= 0 {
		return nil
	}
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-timer.C:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

// Fake is a deterministic Clock: Sleep never blocks, it records the pause and advances the time.
type Fake struct {
	mu     sync.Mutex
	now    time.Time
	sleeps []time.Duration
}

// NewFake creates a fake clock.
//
// Args: start is the initial time returned by Now.
// Returns: a Fake with no recorded pauses.
// Errors: never fails.
func NewFake(start time.Time) *Fake {
	return &Fake{now: start}
}

// Now returns the fake time.
//
// Args: none.
// Returns: the start time plus all pauses taken so far and all Advance calls.
// Errors: never fails.
func (f *Fake) Now() time.Time {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.now
}

// Sleep records the pause and moves the fake time forward without waiting.
//
// Args: ctx is the cancellation context, must not be nil; d is the pause; values <= 0 are neither recorded nor
// advance the time.
// Returns: nil after recording d.
// Errors: ctx.Err() when ctx is already done; then nothing is recorded and the time does not move.
func (f *Fake) Sleep(ctx context.Context, d time.Duration) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	if d <= 0 {
		return nil
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	f.sleeps = append(f.sleeps, d)
	f.now = f.now.Add(d)
	return nil
}

// Advance moves the fake time forward without recording a pause.
//
// Args: d is the amount to add; may be negative.
// Returns: nothing.
// Errors: never fails.
func (f *Fake) Advance(d time.Duration) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.now = f.now.Add(d)
}

// Sleeps returns the recorded pauses.
//
// Args: none.
// Returns: a copy of the pauses in call order; empty (non-nil) when none were taken.
// Errors: never fails.
func (f *Fake) Sleeps() []time.Duration {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]time.Duration{}, f.sleeps...)
}
