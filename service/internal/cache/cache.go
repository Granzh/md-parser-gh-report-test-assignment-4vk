// Package cache stores successful API responses on disk for conditional requests.
package cache

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
)

// Version is the only supported file format version.
const Version = 1

// Entry is one cached response. Only response data is stored: never request headers or the token.
type Entry struct {
	Version int    `json:"version"`
	URL     string `json:"url"`  // the original request URL (the cache key source)
	ETag    string `json:"etag"` // as sent by the server, weak validators (W/"...") included
	Link    string `json:"link"` // all Link header values joined by ", "; needed to find the next page on 304
	Body    string `json:"body"` // JSON text of the response
}

// Store is a directory of cache files.
type Store struct {
	Dir string
}

// Key derives the file key for a URL.
//
// Args: url is the full original request URL.
// Returns: the lower-case hex SHA-256 of url (64 characters).
// Errors: never fails.
func Key(url string) string {
	sum := sha256.Sum256([]byte(url))
	return hex.EncodeToString(sum[:])
}

// path returns the file path of the entry for a URL.
//
// Args: url is the full request URL.
// Returns: Dir/<key>.json.
// Errors: never fails.
func (s *Store) path(url string) string {
	return filepath.Join(s.Dir, Key(url)+".json")
}

// Get looks up the entry for a URL.
//
// Args: url is the full original request URL.
// Returns: the entry and true on a hit. Everything else is a miss (zero Entry, false): nil Store, missing or
// unreadable file, malformed JSON, version other than Version, stored URL different from url, empty ETag.
// Errors: never fails and never panics.
func (s *Store) Get(url string) (Entry, bool) {
	if s == nil || s.Dir == "" {
		return Entry{}, false
	}
	data, err := os.ReadFile(s.path(url))
	if err != nil {
		return Entry{}, false
	}
	var e Entry
	if err := json.Unmarshal(data, &e); err != nil {
		return Entry{}, false
	}
	if e.Version != Version || e.URL != url || e.ETag == "" {
		return Entry{}, false
	}
	return e, true
}

// Put stores an entry.
//
// Args: e is the entry; its Version is forced to Version; e.URL is the key source.
// Returns: nil after the file was replaced atomically (temporary file in the same directory, then rename; the
// directory is created with mode 0700, the file has mode 0600). Also nil, without writing anything, when e.ETag is
// empty (responses without ETag are not cached).
// Errors: an error for a nil Store, an empty Dir, or any failure to create the directory, write or rename; the
// temporary file is removed and a previous entry stays intact.
func (s *Store) Put(e Entry) (err error) {
	if e.ETag == "" {
		return nil
	}
	if s == nil || s.Dir == "" {
		return errors.New("ghreport: каталог кэша не задан")
	}
	e.Version = Version
	data, err := json.Marshal(e)
	if err != nil {
		return err
	}
	if err = os.MkdirAll(s.Dir, 0o700); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(s.Dir, ".cache-*.tmp")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	defer func() {
		if err != nil {
			_ = tmp.Close()
			_ = os.Remove(tmpName)
		}
	}()
	if err = tmp.Chmod(0o600); err != nil {
		return err
	}
	if _, err = tmp.Write(data); err != nil {
		return err
	}
	if err = tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmpName, s.path(e.URL))
}
