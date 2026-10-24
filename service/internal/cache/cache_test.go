package cache

import (
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

const testURL = "https://api.github.com/repos/o/r/issues?state=open&per_page=100"

// sample returns a valid entry for testURL.
//
// Args: none.
// Returns: an Entry with all fields set.
// Errors: never fails.
func sample() Entry {
	return Entry{URL: testURL, ETag: `"abc"`, Link: `<https://x/p2>; rel="next"`, Body: `[{"number":1}]`}
}

// covers: Key
func TestKey(t *testing.T) {
	// SHA-256("abc")
	if got := Key("abc"); got != "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad" {
		t.Fatalf("got %s", got)
	}
	if len(Key("")) != 64 || Key("a") == Key("b") || Key(testURL) != Key(testURL) {
		t.Fatal("key properties broken")
	}
}

// covers: Store.path
func TestStorePath(t *testing.T) {
	s := &Store{Dir: "d"}
	if got := s.path("u"); got != filepath.Join("d", Key("u")+".json") {
		t.Fatalf("got %q", got)
	}
}

// covers: Store.Put
func TestPutThenGetRoundTrip(t *testing.T) {
	s := &Store{Dir: filepath.Join(t.TempDir(), "nested", "cache")}
	if err := s.Put(sample()); err != nil {
		t.Fatal(err)
	}
	got, ok := s.Get(testURL)
	want := sample()
	want.Version = Version
	if !ok || got != want {
		t.Fatalf("got %+v, %v", got, ok)
	}
	raw, err := os.ReadFile(filepath.Join(s.Dir, Key(testURL)+".json"))
	if err != nil {
		t.Fatal(err)
	}
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil || len(m) != 5 || m["version"] != 1.0 || m["link"] != want.Link || m["body"] != want.Body {
		t.Fatalf("file content %s (%v)", raw, err)
	}
}

// covers: Store.Put
func TestPutPermissionsAndNoTemporaryFiles(t *testing.T) {
	s := &Store{Dir: filepath.Join(t.TempDir(), "c")}
	if err := s.Put(sample()); err != nil {
		t.Fatal(err)
	}
	entries, _ := os.ReadDir(s.Dir)
	if len(entries) != 1 || !strings.HasSuffix(entries[0].Name(), ".json") {
		t.Fatalf("directory content: %v", entries)
	}
	if runtime.GOOS != "windows" {
		fi, _ := os.Stat(filepath.Join(s.Dir, entries[0].Name()))
		di, _ := os.Stat(s.Dir)
		if fi.Mode().Perm() != 0o600 || di.Mode().Perm() != 0o700 {
			t.Fatalf("modes: file %v, dir %v", fi.Mode().Perm(), di.Mode().Perm())
		}
	}
}

// covers: Store.Put
func TestPutOverwrites(t *testing.T) {
	s := &Store{Dir: t.TempDir()}
	e := sample()
	_ = s.Put(e)
	e.ETag, e.Body = `W/"weak"`, `[]`
	if err := s.Put(e); err != nil {
		t.Fatal(err)
	}
	got, ok := s.Get(testURL)
	if !ok || got.ETag != `W/"weak"` || got.Body != `[]` {
		t.Fatalf("got %+v, %v", got, ok)
	}
}

// covers: Store.Put
func TestPutWithoutETagWritesNothing(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "never")
	s := &Store{Dir: dir}
	e := sample()
	e.ETag = ""
	if err := s.Put(e); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(dir); !os.IsNotExist(err) {
		t.Fatalf("directory was created: %v", err)
	}
}

// covers: Store.Put
func TestPutErrors(t *testing.T) {
	if err := (&Store{}).Put(sample()); err == nil {
		t.Fatal("empty Dir must fail")
	}
	var nilStore *Store
	if err := nilStore.Put(sample()); err == nil {
		t.Fatal("nil store must fail")
	}
	file := filepath.Join(t.TempDir(), "f")
	_ = os.WriteFile(file, []byte("x"), 0o644)
	if err := (&Store{Dir: filepath.Join(file, "sub")}).Put(sample()); err == nil {
		t.Fatal("file as parent must fail")
	}
	// target path is a directory: rename fails, no temp files remain
	dir := t.TempDir()
	s := &Store{Dir: dir}
	if err := os.Mkdir(s.path(testURL), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := s.Put(sample()); err == nil {
		t.Fatal("rename over directory must fail")
	}
	entries, _ := os.ReadDir(dir)
	if len(entries) != 1 {
		t.Fatalf("leftovers: %v", entries)
	}
}

// covers: Store.Get
func TestGetMisses(t *testing.T) {
	dir := t.TempDir()
	s := &Store{Dir: dir}
	if _, ok := s.Get(testURL); ok {
		t.Fatal("empty store hit")
	}
	write := func(content string) {
		if err := os.WriteFile(s.path(testURL), []byte(content), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	good := func(mut func(m map[string]any)) string {
		m := map[string]any{"version": 1, "url": testURL, "etag": `"e"`, "link": "", "body": "[]"}
		mut(m)
		b, _ := json.Marshal(m)
		return string(b)
	}
	cases := map[string]string{
		"empty file":      "",
		"garbage":         "not json",
		"truncated":       `{"version":1,"url":"` + testURL,
		"json array":      "[]",
		"json null":       "null",
		"wrong version":   good(func(m map[string]any) { m["version"] = 2 }),
		"zero version":    good(func(m map[string]any) { delete(m, "version") }),
		"foreign url":     good(func(m map[string]any) { m["url"] = testURL + "&x=1" }),
		"empty etag":      good(func(m map[string]any) { m["etag"] = "" }),
		"missing etag":    good(func(m map[string]any) { delete(m, "etag") }),
		"etag wrong type": good(func(m map[string]any) { m["etag"] = 5 }),
		"body wrong type": good(func(m map[string]any) { m["body"] = []int{1} }),
		"binary":          "\x00\xff\xfe",
	}
	for name, content := range cases {
		write(content)
		if e, ok := s.Get(testURL); ok || e != (Entry{}) {
			t.Errorf("%s: want miss, got %+v", name, e)
		}
	}
	write(good(func(m map[string]any) {}))
	if _, ok := s.Get(testURL); !ok {
		t.Fatal("control entry must hit")
	}
	if err := os.Remove(s.path(testURL)); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(s.path(testURL), 0o755); err != nil {
		t.Fatal(err)
	}
	if _, ok := s.Get(testURL); ok {
		t.Fatal("directory in place of a file must miss")
	}
}

// covers: Store.Get
func TestGetNilAndEmptyStore(t *testing.T) {
	var nilStore *Store
	if _, ok := nilStore.Get(testURL); ok {
		t.Fatal("nil store hit")
	}
	if _, ok := (&Store{}).Get(testURL); ok {
		t.Fatal("empty Dir hit")
	}
}

// covers: Store.Get
func TestGetOtherURLSameDirIsMiss(t *testing.T) {
	s := &Store{Dir: t.TempDir()}
	_ = s.Put(sample())
	if _, ok := s.Get(testURL + "&page=2"); ok {
		t.Fatal("different URL must miss")
	}
	// a file copied under another URL's key must be rejected by the stored-URL check
	other := testURL + "&page=2"
	data, _ := os.ReadFile(s.path(testURL))
	_ = os.WriteFile(s.path(other), data, 0o600)
	if _, ok := s.Get(other); ok {
		t.Fatal("foreign entry accepted")
	}
}
