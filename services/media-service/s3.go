// S3 backend (ADR-0027): SigV4-signed requests against any S3-compatible
// object store (MinIO-class). Stdlib only — the AWS SDK would dwarf the
// rest of this service combined. Meta (audit, checksums, tombstones) lives
// in local sidecars exactly as the local backend; S3 owns the bytes.
package main

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"sort"
	"strings"
	"time"
)

type S3Store struct {
	endpoint  string // e.g. http://minio:9000
	bucket    string
	accessKey string
	secretKey string
	region    string
	metaDir   string // sidecar dir, same format as LocalStore
	client    *http.Client
	seq       int
	meta      map[string]Meta
}

func NewS3Store(endpoint, bucket, accessKey, secretKey, metaDir string) (*S3Store, error) {
	if endpoint == "" || bucket == "" || accessKey == "" || secretKey == "" {
		return nil, errors.New("s3 backend needs endpoint, bucket, and credentials")
	}
	if err := os.MkdirAll(metaDir, 0o750); err != nil {
		return nil, err
	}
	s := &S3Store{endpoint: strings.TrimSuffix(endpoint, "/"), bucket: bucket,
		accessKey: accessKey, secretKey: secretKey, region: "us-east-1",
		metaDir: metaDir, client: &http.Client{Timeout: 30 * time.Second},
		meta: map[string]Meta{}}
	return s, scanSidecars(metaDir, &s.seq, s.meta)
}

func (s *S3Store) sign(req *http.Request, payloadHash string) {
	now := time.Now().UTC()
	date := now.Format("20060102")
	amz := now.Format("20060102T150405Z")
	req.Header.Set("X-Amz-Date", amz)
	req.Header.Set("X-Amz-Content-Sha256", payloadHash)

	signedHeaders := []string{"host", "x-amz-content-sha256", "x-amz-date"}
	canonicalHeaders := "host:" + req.URL.Host + "\n" +
		"x-amz-content-sha256:" + payloadHash + "\n" +
		"x-amz-date:" + amz + "\n"
	canonical := strings.Join([]string{req.Method, req.URL.EscapedPath(),
		req.URL.RawQuery, canonicalHeaders, strings.Join(signedHeaders, ";"),
		payloadHash}, "\n")
	cHash := sha256.Sum256([]byte(canonical))
	scope := date + "/" + s.region + "/s3/aws4_request"
	toSign := "AWS4-HMAC-SHA256\n" + amz + "\n" + scope + "\n" + hex.EncodeToString(cHash[:])

	kDate := hmacSHA256([]byte("AWS4"+s.secretKey), date)
	kRegion := hmacSHA256(kDate, s.region)
	kService := hmacSHA256(kRegion, "s3")
	kSigning := hmacSHA256(kService, "aws4_request")
	sig := hex.EncodeToString(hmacSHA256(kSigning, toSign))

	req.Header.Set("Authorization", fmt.Sprintf(
		"AWS4-HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, Signature=%s",
		s.accessKey, scope, strings.Join(signedHeaders, ";"), sig))
}

func hmacSHA256(key []byte, data string) []byte {
	m := hmac.New(sha256.New, key)
	m.Write([]byte(data))
	return m.Sum(nil)
}

func (s *S3Store) do(method, key string, body []byte, contentType string) (*http.Response, []byte, error) {
	hash := sha256.Sum256(body)
	req, err := http.NewRequest(method,
		fmt.Sprintf("%s/%s/%s", s.endpoint, s.bucket, key), bytes.NewReader(body))
	if err != nil {
		return nil, nil, err
	}
	if contentType != "" {
		req.Header.Set("Content-Type", contentType)
	}
	s.sign(req, hex.EncodeToString(hash[:]))
	resp, err := s.client.Do(req)
	if err != nil {
		return nil, nil, err
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, nil, err
	}
	if resp.StatusCode >= 300 {
		return nil, nil, fmt.Errorf("s3 %s %s: %d", method, key, resp.StatusCode)
	}
	return resp, data, nil
}

func (s *S3Store) Put(contentType string, data []byte, actor string) (Meta, error) {
	if actor == "" {
		return Meta{}, errors.New("uploads name an actor")
	}
	if len(data) > MaxBlobBytes {
		return Meta{}, ErrTooLarge
	}
	if contentType == "" {
		contentType = "application/octet-stream"
	}
	sum := sha256.Sum256(data)
	s.seq++
	m := Meta{ID: fmt.Sprintf("BLOB-%08d", s.seq), ContentType: contentType,
		Size: int64(len(data)), SHA256: hex.EncodeToString(sum[:]),
		UploadedBy: actor, UploadedAt: time.Now()}
	if _, _, err := s.do("PUT", m.ID+".bin", data, contentType); err != nil {
		return Meta{}, err
	}
	s.meta[m.ID] = m
	return m, writeSidecarTo(s.metaDir, m)
}

func (s *S3Store) Get(id string) (Meta, []byte, error) {
	m, err := s.Stat(id)
	if err != nil {
		return Meta{}, nil, err
	}
	_, data, err := s.do("GET", id+".bin", nil, "")
	if err != nil {
		return Meta{}, nil, err
	}
	sum := sha256.Sum256(data)
	if hex.EncodeToString(sum[:]) != m.SHA256 {
		return Meta{}, nil, fmt.Errorf("integrity failure on %s", id)
	}
	return m, data, nil
}

func (s *S3Store) Stat(id string) (Meta, error) {
	m, ok := s.meta[id]
	if !ok {
		return Meta{}, ErrNotFound
	}
	if m.Deleted {
		return m, ErrDeleted
	}
	return m, nil
}

func (s *S3Store) Tombstone(id, actor, reason string) (Meta, error) {
	if actor == "" || reason == "" {
		return Meta{}, errors.New("deletes name who and why")
	}
	m, ok := s.meta[id]
	if !ok {
		return Meta{}, ErrNotFound
	}
	m.Deleted, m.DeletedBy, m.DeleteWhy = true, actor, reason
	s.meta[id] = m
	return m, writeSidecarTo(s.metaDir, m)
}

func (s *S3Store) List() []Meta {
	out := make([]Meta, 0, len(s.meta))
	for _, m := range s.meta {
		out = append(out, m)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out
}
