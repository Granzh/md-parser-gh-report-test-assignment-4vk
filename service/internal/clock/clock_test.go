package clock

import (
	"context"
	"errors"
	"reflect"
	"testing"
	"time"
)

var start = time.Date(2026, 10, 14, 9, 0, 0, 0, time.UTC)

// Compile-time proof that both implementations satisfy Clock.
var (
	_ Clock = Real{}
	_ Clock = (*Fake)(nil)
)

// cancelled returns an already cancelled context.
//
// Args: none.
// Returns: a context whose Err() is context.Canceled.
// Errors: never fails.
func cancelled() context.Context {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	return ctx
}

// covers: Fake.Sleep
func TestFakeSleepRecordsAndAdvances(t *testing.T) {
	f := NewFake(start)
	for _, d := range []time.Duration{time.Second, 2 * time.Second, 4 * time.Second, 60 * time.Second} {
		if err := f.Sleep(context.Background(), d); err != nil {
			t.Fatal(err)
		}
	}
	want := []time.Duration{time.Second, 2 * time.Second, 4 * time.Second, 60 * time.Second}
	if !reflect.DeepEqual(f.Sleeps(), want) {
		t.Fatalf("sleeps = %v, want %v", f.Sleeps(), want)
	}
	if got := f.Now(); !got.Equal(start.Add(67 * time.Second)) {
		t.Fatalf("now = %v", got)
	}
}

// covers: Fake.Sleep
func TestFakeSleepRespectsCancelledContext(t *testing.T) {
	f := NewFake(start)
	err := f.Sleep(cancelled(), time.Hour)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("err = %v", err)
	}
	if len(f.Sleeps()) != 0 || !f.Now().Equal(start) {
		t.Fatalf("cancelled sleep must not be recorded: %v, %v", f.Sleeps(), f.Now())
	}
	ctx, cancel := context.WithDeadline(context.Background(), time.Unix(0, 0))
	defer cancel()
	if err := f.Sleep(ctx, time.Second); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("deadline: err = %v", err)
	}
}

// covers: Fake.Sleep
func TestFakeSleepIgnoresNonPositive(t *testing.T) {
	f := NewFake(start)
	for _, d := range []time.Duration{0, -time.Second} {
		if err := f.Sleep(context.Background(), d); err != nil {
			t.Fatal(err)
		}
	}
	if len(f.Sleeps()) != 0 || !f.Now().Equal(start) {
		t.Fatalf("got %v, %v", f.Sleeps(), f.Now())
	}
}

// covers: Fake.Now
func TestFakeNow(t *testing.T) {
	f := NewFake(start)
	if !f.Now().Equal(start) || !f.Now().Equal(f.Now()) {
		t.Fatal("Now must be stable without Sleep/Advance")
	}
	if !NewFake(time.Time{}).Now().IsZero() {
		t.Fatal("zero start must stay zero")
	}
}

// covers: NewFake
func TestNewFake(t *testing.T) {
	f := NewFake(start)
	if f == nil || !f.Now().Equal(start) || f.Sleeps() == nil || len(f.Sleeps()) != 0 {
		t.Fatalf("got %+v", f)
	}
}

// covers: Fake.Advance
func TestFakeAdvance(t *testing.T) {
	f := NewFake(start)
	f.Advance(time.Minute)
	f.Advance(-30 * time.Second)
	if !f.Now().Equal(start.Add(30*time.Second)) || len(f.Sleeps()) != 0 {
		t.Fatalf("now = %v, sleeps = %v", f.Now(), f.Sleeps())
	}
}

// covers: Fake.Sleeps
func TestFakeSleepsReturnsCopy(t *testing.T) {
	f := NewFake(start)
	_ = f.Sleep(context.Background(), time.Second)
	s := f.Sleeps()
	s[0] = time.Hour
	if f.Sleeps()[0] != time.Second {
		t.Fatal("Sleeps exposes internal storage")
	}
}

// covers: Real.Now
func TestRealNow(t *testing.T) {
	before := time.Now()
	got := Real{}.Now()
	after := time.Now()
	if got.Before(before.Add(-time.Second)) || got.After(after.Add(time.Second)) || got.Location() != time.UTC {
		t.Fatalf("got %v outside [%v, %v] or not UTC", got, before, after)
	}
}

// covers: Real.Sleep
func TestRealSleep(t *testing.T) {
	r := Real{}
	if err := r.Sleep(context.Background(), 0); err != nil {
		t.Fatal(err)
	}
	if err := r.Sleep(context.Background(), -time.Hour); err != nil {
		t.Fatal(err)
	}
	if err := r.Sleep(context.Background(), time.Nanosecond); err != nil {
		t.Fatal(err)
	}
	// An already cancelled context returns at once, even for a very long pause and even for d <= 0.
	if err := r.Sleep(cancelled(), 24*time.Hour); !errors.Is(err, context.Canceled) {
		t.Fatalf("err = %v", err)
	}
	if err := r.Sleep(cancelled(), 0); !errors.Is(err, context.Canceled) {
		t.Fatalf("d=0: err = %v", err)
	}
}

// covers: Real.Sleep
func TestRealSleepCancelledWhileWaiting(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- Real{}.Sleep(ctx, 24*time.Hour) }()
	cancel()
	if err := <-done; !errors.Is(err, context.Canceled) {
		t.Fatalf("err = %v", err)
	}
}
