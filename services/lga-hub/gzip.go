package main

import (
	"compress/gzip"
	"io"
)

// gzipNewReader is a seam so tests can swap the decoder if needed.
func gzipNewReader(r io.Reader) (*gzip.Reader, error) {
	return gzip.NewReader(r)
}
