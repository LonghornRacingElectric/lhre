package gateway

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"strings"
	"time"
)

type cursor struct {
	Version int      `json:"v"`
	Request string   `json:"request"`
	Schema  string   `json:"schema"`
	Expires int64    `json:"expires"`
	From    int64    `json:"from"`
	To      int64    `json:"to"`
	After   Position `json:"after"`
}

func fingerprint(r Request) string {
	r.PageToken = ""
	data, _ := json.Marshal(r)
	hash := sha256.Sum256(data)
	return hex.EncodeToString(hash[:])
}
func (e *Engine) encodeCursor(r Request, from, to int64, after Position) (string, error) {
	data, err := json.Marshal(cursor{4, fingerprint(r), e.Schema.Version, time.Now().Add(24 * time.Hour).UnixMilli(), from, to, after})
	if err != nil {
		return "", err
	}
	payload := base64.RawURLEncoding.EncodeToString(data)
	hash := hmac.New(sha256.New, e.Secret)
	_, _ = hash.Write([]byte(payload))
	return payload + "." + base64.RawURLEncoding.EncodeToString(hash.Sum(nil)), nil
}
func (e *Engine) cursor(r Request, from, to *int64) (*Position, error) {
	if r.PageToken == "" {
		return nil, nil
	}
	invalid := problem(400, "invalid_page_token", "Page token is invalid, expired or belongs to another query or schema")
	parts := strings.Split(r.PageToken, ".")
	if len(parts) != 2 || len(r.PageToken) > 8192 {
		return nil, invalid
	}
	signature, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		return nil, invalid
	}
	hash := hmac.New(sha256.New, e.Secret)
	_, _ = hash.Write([]byte(parts[0]))
	if !hmac.Equal(signature, hash.Sum(nil)) {
		return nil, invalid
	}
	data, err := base64.RawURLEncoding.DecodeString(parts[0])
	if err != nil {
		return nil, invalid
	}
	var c cursor
	if json.Unmarshal(data, &c) != nil || c.Version != 4 || c.Request != fingerprint(r) || c.Schema != e.Schema.Version || c.Expires <= time.Now().UnixMilli() || c.From <= 0 || c.To <= c.From {
		return nil, invalid
	}
	*from, *to = c.From, c.To
	return &c.After, nil
}
