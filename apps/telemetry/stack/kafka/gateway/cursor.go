package gateway

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"sort"
	"strings"
	"time"
)

type cursor struct {
	Version int      `json:"v"`
	Request string   `json:"request"`
	Schema  string   `json:"schema"`
	Storage string   `json:"storage"`
	Dataset string   `json:"dataset"`
	Live    string   `json:"live,omitempty"`
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
func datasetSignature(sessions []Session) string {
	identities := make([][2]int64, 0, len(sessions))
	for _, session := range sessions {
		identities = append(identities, [2]int64{session.ID, session.Revision})
	}
	sort.Slice(identities, func(left, right int) bool { return identities[left][0] < identities[right][0] })
	encoded, _ := json.Marshal(identities)
	hash := sha256.Sum256(encoded)
	return hex.EncodeToString(hash[:])
}

func (e *Engine) encodeCursor(r Request, from, to int64, after Position, dataset, live string) (string, error) {
	data, err := json.Marshal(cursor{Version: 5, Request: fingerprint(r), Schema: e.Schema.Version, Storage: e.Schema.StorageID, Dataset: dataset, Live: live, Expires: time.Now().Add(24 * time.Hour).UnixMilli(), From: from, To: to, After: after})
	if err != nil {
		return "", err
	}
	payload := base64.RawURLEncoding.EncodeToString(data)
	hash := hmac.New(sha256.New, e.Secret)
	_, _ = hash.Write([]byte(payload))
	return payload + "." + base64.RawURLEncoding.EncodeToString(hash.Sum(nil)), nil
}
func (e *Engine) cursor(r Request, from, to *int64) (*cursor, error) {
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
	if json.Unmarshal(data, &c) != nil || c.Version != 5 || c.Request != fingerprint(r) || c.Schema != e.Schema.Version || c.Storage != e.Schema.StorageID || c.Dataset == "" || c.Expires <= time.Now().UnixMilli() || c.From <= 0 || c.To <= c.From {
		return nil, invalid
	}
	*from, *to = c.From, c.To
	return &c, nil
}
