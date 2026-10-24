// Package output delivers the finished report to stdout or a file.
package output

import (
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
)

// Stdout is the destination value that selects standard output.
const Stdout = "-"

// WriteReport writes data to its destination.
//
// Args: dest is "-" for stdout or a file path (relative or absolute, non-empty); stdout is the writer used for "-",
// must not be nil; data is the complete document, may be empty.
// Returns: nil once all bytes were delivered. A file is replaced atomically: data goes to a temporary file in the
// same directory which is then renamed over dest, so readers never see a partial report; missing parent directories
// are created (mode 0755); the final file mode is 0644. On failure the temporary file is removed and an existing
// dest is left untouched.
// Errors: an error (Russian text wrapping the cause, no data content) for an empty dest, a failing or short write
// to stdout, and any failure to create directories, write, close or rename.
func WriteReport(dest string, stdout io.Writer, data []byte) error {
	if dest == "" {
		return errors.New("ghreport: путь к файлу отчёта не может быть пустым")
	}
	if dest == Stdout {
		n, err := stdout.Write(data)
		if err == nil && n < len(data) {
			err = io.ErrShortWrite
		}
		if err != nil {
			return fmt.Errorf("ghreport: не удалось записать отчёт в стандартный вывод: %w", err)
		}
		return nil
	}
	if err := writeFileAtomic(dest, data); err != nil {
		return fmt.Errorf("ghreport: не удалось записать отчёт в файл: %w", err)
	}
	return nil
}

// writeFileAtomic replaces path with data via a temporary file and rename.
//
// Args: path is a non-empty file path; data is the content.
// Returns: nil on success; the parent directories exist afterwards and the file has mode 0644.
// Errors: the first error from MkdirAll, CreateTemp, Write, Chmod, Close or Rename; the temporary file is removed.
func writeFileAtomic(path string, data []byte) (err error) {
	dir := filepath.Dir(path)
	if err = os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(dir, ".ghreport-*.tmp")
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
	if _, err = tmp.Write(data); err != nil {
		return err
	}
	if err = tmp.Chmod(0o644); err != nil {
		return err
	}
	if err = tmp.Sync(); err != nil {
		return err
	}
	if err = tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmpName, path)
}
