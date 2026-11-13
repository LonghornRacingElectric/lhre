package gateway

import (
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/csv"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"strings"
	"time"
)

func (e *Engine) Handler(token string) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /v1/car-status", e.statusHTTP)
	if e.Feeds != nil {
		mux.HandleFunc("GET /v1/topics/{topic}", e.Feeds.Handler)
	}
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, map[string]any{"ok": true, "schema_version": e.Schema.Version, "storage_id": e.Schema.StorageID})
	})
	mux.HandleFunc("GET /v1/signals", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, map[string]any{"car": "artemis", "schema_version": e.Schema.Version, "signals": e.Schema.Signals, "pools": e.Schema.Pools})
	})
	mux.HandleFunc("GET /v1/sessions", func(w http.ResponseWriter, r *http.Request) {
		ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
		defer cancel()
		snap, err := e.Store.Snapshot(ctx, 0, time.Now().UnixMilli()+1)
		if err != nil {
			writeError(w, err)
			return
		}
		defer snap.Close(ctx)
		writeJSON(w, map[string]any{"sessions": snap.Sessions()})
	})
	mux.HandleFunc("GET /v1/stats", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, map[string]any{"ring": e.Cache.Ring.Stats(), "reads": map[string]uint64{"ring": e.Cache.Reads[0].Load(), "L1": e.Cache.Reads[1].Load(), "L2": e.Cache.Reads[2].Load(), "postgres": e.Cache.Reads[3].Load()}})
	})
	mux.HandleFunc("POST /api/query", e.queryHTTP)
	mux.HandleFunc("POST /v1/query", e.queryHTTP)
	mux.HandleFunc("GET /v1/stream", e.streamHTTP)
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-store")
		if r.URL.Path != "/healthz" {
			expected := sha256.Sum256([]byte("Bearer " + token))
			actual := sha256.Sum256([]byte(r.Header.Get("Authorization")))
			if token == "" || subtle.ConstantTimeCompare(expected[:], actual[:]) != 1 {
				writeError(w, problem(401, "unauthorized", "A gateway bearer token is required"))
				return
			}
		}
		mux.ServeHTTP(w, r)
	})
}
func writeJSON(w http.ResponseWriter, value any) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(value)
}
func publicError(err error) *queryProblem {
	var p *queryProblem
	if !errors.As(err, &p) {
		log.Printf("query: %v", err)
		p = &queryProblem{503, "source_unavailable", "Telemetry source is unavailable"}
	}
	return p
}
func writeError(w http.ResponseWriter, err error) {
	p := publicError(err)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(p.Status)
	_ = json.NewEncoder(w).Encode(map[string]any{"error": map[string]any{"code": p.Code, "message": p.Message}})
}
func (e *Engine) queryHTTP(w http.ResponseWriter, r *http.Request) {
	var request Request
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 256*1024))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&request); err != nil {
		writeError(w, problem(400, "invalid_request", "Invalid or oversized query body"))
		return
	}
	var extra any
	if decoder.Decode(&extra) != io.EOF {
		writeError(w, problem(400, "invalid_request", "Expected one JSON request"))
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
	defer cancel()
	page, err := e.Query(ctx, request)
	if err != nil {
		writeError(w, err)
		return
	}
	w.Header().Set("X-Schema-Version", page.Schema)
	if page.Next != nil {
		w.Header().Set("X-Next-Page-Token", *page.Next)
	}
	accept := r.Header.Get("Accept")
	if strings.Contains(accept, "application/vnd.apache.arrow.stream") {
		w.Header().Set("Content-Type", "application/vnd.apache.arrow.stream")
		if err := writeArrow(w, page); err != nil {
			log.Printf("query Arrow response: %v", err)
		}
		return
	}
	if strings.Contains(accept, "text/csv") {
		w.Header().Set("Content-Type", "text/csv")
		writer := csv.NewWriter(w)
		writer.UseCRLF = true
		_ = writer.Write([]string{"ts", "sample_time_ns", "signal", "value"})
		for _, row := range page.Data {
			data, _ := json.Marshal(row.Value)
			value := string(data)
			if s, ok := row.Value.(string); ok {
				value = s
			}
			_ = writer.Write([]string{row.TS, row.NS, row.Signal, value})
		}
		writer.Flush()
		return
	}
	if accept != "" && !strings.Contains(accept, "application/json") && !strings.Contains(accept, "*/*") {
		writeError(w, problem(406, "unsupported_format", "Use application/json or text/csv"))
		return
	}
	writeJSON(w, page)
}

// The existing SSE transport carries unified query results. No per-client
// Kafka consumer or alternative streaming protocol is introduced.
func (e *Engine) streamHTTP(w http.ResponseWriter, r *http.Request) {
	var query Request
	raw := r.URL.Query().Get("query")
	if len(raw) > 16000 || json.Unmarshal([]byte(raw), &query) != nil {
		writeError(w, problem(400, "invalid_request", "query must contain a bounded JSON query"))
		return
	}
	if query.PageToken != "" {
		writeError(w, problem(400, "invalid_request", "Live subscriptions do not accept page tokens"))
		return
	}
	flusher, ok := w.(http.Flusher)
	if !ok {
		writeError(w, problem(503, "stream_unavailable", "Streaming is unavailable"))
		return
	}
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("X-Accel-Buffering", "no")
	changes, unsubscribe := e.Cache.Ring.Subscribe()
	defer unsubscribe()
	ticker := time.NewTicker(250 * time.Millisecond)
	defer ticker.Stop()
	dirty := true
	for {
		if dirty {
			ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
			page, err := e.query(ctx, query, true)
			cancel()
			var event any
			if err != nil {
				failure := publicError(err)
				event = map[string]any{"type": "gap", "code": failure.Code, "message": failure.Message}
			} else {
				event = map[string]any{"type": "data", "page": page}
			}
			data, _ := json.Marshal(event)
			if _, err = fmt.Fprintf(w, "data: %s\n\n", data); err != nil {
				return
			}
			flusher.Flush()
			dirty = false
		}
		select {
		case <-r.Context().Done():
			return
		case <-changes:
			// Coalesce native-rate notifications until the next display tick.
			select {
			case <-r.Context().Done():
				return
			case <-ticker.C:
				dirty = true
			}
		case <-ticker.C:
			if query.T1 == "live" {
				dirty = true
			}
		}
	}
}
