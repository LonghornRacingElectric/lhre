package gateway

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"math/big"
	"sort"
	"strconv"
	"time"

	"golang.org/x/sync/errgroup"
)

type Request struct {
	WindowMS  int64    `json:"window_ms,omitempty"`
	Car       string   `json:"car"`
	Signals   []string `json:"signals"`
	Pools     []string `json:"pools,omitempty"`
	DriveDay  *int64   `json:"drive_day,omitempty"`
	T0        string   `json:"t0"`
	T1        string   `json:"t1"`
	Rate      *float64 `json:"rate"`
	Aggregate string   `json:"agg"`
	BucketMS  int64    `json:"bucket_ms,omitempty"`
	MaxPoints int      `json:"max_points,omitempty"`
	PageSize  int      `json:"page_size"`
	PageToken string   `json:"page_token"`
}
type Position struct {
	Time    int64  `json:"ts"`
	Session int64  `json:"session,string"`
	NS      int64  `json:"sampleTimeNs,string"`
	Signal  string `json:"signal"`
}
type Observation struct {
	TS       string `json:"ts"`
	NS       string `json:"sample_time_ns"`
	Session  string `json:"session_start_ms"`
	Signal   string `json:"signal"`
	Value    any    `json:"value"`
	position Position
	size     int
}
type Page struct {
	Schema      string        `json:"schema_version"`
	Data        []Observation `json:"data"`
	Next        *string       `json:"next_page_token"`
	Bucket      int64         `json:"bucket_ms,omitempty"`
	Sources     []string      `json:"sources"`
	Provisional bool          `json:"provisional"`
	Live        *LiveStatus   `json:"live,omitempty"`
}
type Engine struct {
	Feeds  *Feeds
	Schema *Schema
	Store  Store
	Cache  *Cache
	Secret []byte
}
type queryProblem struct {
	Status  int
	Code    string
	Message string
}

func (p *queryProblem) Error() string                { return p.Message }
func problem(status int, code, message string) error { return &queryProblem{status, code, message} }

func parseTimestamp(value string) (int64, error) {
	t, err := time.Parse(time.RFC3339Nano, value)
	if err != nil || t.UnixNano()%1000000 != 0 {
		return 0, problem(400, "invalid_request", "Use ISO8601 timestamps with timezone and at most millisecond precision")
	}
	return t.UnixMilli(), nil
}
func (e *Engine) bounds(ctx context.Context, r Request) (int64, int64, error) {
	if r.WindowMS > 0 && r.T1 == "live" {
		to := time.Now().UnixMilli() + 1
		return max(1, to-r.WindowMS), to, nil
	}
	var from, to int64
	var dayStart, dayEnd int64
	var err error
	if r.DriveDay != nil {
		dayStart, dayEnd, err = e.Store.Day(ctx, *r.DriveDay)
		if err != nil {
			return 0, 0, err
		}
		from, to = dayStart, dayEnd
	}
	if r.T0 != "" {
		from, err = parseTimestamp(r.T0)
		if err != nil {
			return 0, 0, err
		}
	} else if r.DriveDay == nil {
		return 0, 0, problem(400, "invalid_request", "t0 or drive_day is required")
	}
	if r.T1 == "live" {
		to = time.Now().UnixMilli() + 1
	} else if r.T1 != "" {
		to, err = parseTimestamp(r.T1)
		if err != nil {
			return 0, 0, err
		}
	} else if r.DriveDay == nil {
		return 0, 0, problem(400, "invalid_request", "t1 or drive_day is required")
	}
	if r.DriveDay != nil {
		from = max(from, dayStart)
		to = min(to, dayEnd)
	}
	if from <= 0 || to <= from {
		return 0, 0, problem(400, "invalid_request", "Time bounds must overlap the drive day and t0 must precede t1")
	}
	return from, to, nil
}
func before(a, b Position) bool {
	if a.Time != b.Time {
		return a.Time < b.Time
	}
	if a.Session != b.Session {
		return a.Session < b.Session
	}
	if a.NS != b.NS {
		return a.NS < b.NS
	}
	return a.Signal < b.Signal
}
func observation(t, ns, session int64, signal string, value any) Observation {
	return Observation{TS: time.UnixMilli(t).UTC().Format("2006-01-02T15:04:05.000Z"), NS: strconv.FormatInt(ns, 10), Session: strconv.FormatInt(session, 10), Signal: signal, Value: publicValue(value), position: Position{t, session, ns, signal}}
}

func (e *Engine) Query(ctx context.Context, r Request) (*Page, error) {
	return e.query(ctx, r, false)
}

func (e *Engine) query(ctx context.Context, r Request, stream bool) (*Page, error) {
	if r.Car != "artemis" {
		return nil, problem(400, "unsupported_car", "Only Artemis is supported")
	}
	if r.Aggregate == "" {
		r.Aggregate = "raw"
	}
	if r.Aggregate == "avg" {
		r.Aggregate = "mean"
	}
	switch r.Aggregate {
	case "raw", "mean", "min", "max", "last":
	default:
		return nil, problem(400, "invalid_request", "Unsupported aggregate")
	}
	if r.PageSize <= 0 {
		r.PageSize = 1000
	}
	r.PageSize = min(r.PageSize, 10000)
	byteLimit := 2 * 1024 * 1024
	if stream {
		r.PageSize = 200000
		byteLimit = (32 << 20) - 4096
	}
	if r.MaxPoints <= 0 {
		r.MaxPoints = 1000
	}
	if r.MaxPoints > 10000 {
		return nil, problem(400, "invalid_request", "max_points cannot exceed 10000")
	}
	if len(r.Signals) > 50 && !(len(r.Signals) == 1 && r.Signals[0] == "*") {
		return nil, problem(400, "invalid_request", "Select at most 50 explicit signals, or use pools / signals: [\"*\"]")
	}
	selected, err := e.Schema.selectSignals(r.Signals, r.Pools)
	if err != nil {
		return nil, err
	}
	sort.Slice(selected, func(i, j int) bool { return selected[i].Name < selected[j].Name })
	for _, signal := range selected {
		if r.Aggregate != "raw" && r.Aggregate != "last" && !numeric(e.Schema.bySignal[signal.Name]) {
			return nil, problem(400, "unsupported_aggregate", signal.Name+" supports raw and last")
		}
	}
	from, to, err := e.bounds(ctx, r)
	if err != nil {
		return nil, err
	}
	continuation, err := e.cursor(r, &from, &to)
	if err != nil {
		return nil, err
	}
	var after *Position
	if continuation != nil {
		after = &continuation.After
	}
	width := r.BucketMS
	if r.Rate != nil {
		if *r.Rate <= 0 || *r.Rate > 1000 || width != 0 {
			return nil, problem(400, "invalid_request", "Use rate (0–1000 Hz) or bucket_ms")
		}
		width = int64(math.Ceil(1000 / *r.Rate))
	}
	if r.Aggregate == "raw" && (width != 0 || r.MaxPoints != 1000) {
		return nil, problem(400, "invalid_request", "Raw reads use pagination; omit bucket_ms, rate and max_points")
	}
	if r.Aggregate != "raw" {
		if width < 0 {
			return nil, problem(400, "invalid_request", "bucket_ms must be positive")
		}
		width = max(width, 1, (to-from+int64(r.MaxPoints)-1)/int64(r.MaxPoints))
		for _, signal := range selected {
			p := e.Schema.byPool[signal.Pool]
			if p.Frequency > 0 {
				width = max(width, int64(math.Ceil(1000/p.Frequency)))
			}
		}
		if r.BucketMS == 0 && r.Rate == nil && width >= 1000 {
			width = (width + 999) / 1000 * 1000
		}
	}
	snap, err := e.Store.Snapshot(ctx, from, to)
	if err != nil {
		return nil, err
	}
	defer snap.Close(ctx)
	view := e.Cache.Ring.View()
	dataset := datasetSignature(snap.Sessions())
	if continuation != nil && (continuation.Dataset != dataset || continuation.Live != "" && continuation.Live != view.signature()) {
		return nil, problem(409, "query_changed", "Recording or live data changed; discard previous pages and restart the query")
	}
	pools := map[string][]Signal{}
	for _, signal := range selected {
		pools[signal.Pool] = append(pools[signal.Pool], signal)
	}
	data := []Observation{}
	sources := map[string]bool{}
	names := make([]string, 0, len(pools))
	for name := range pools {
		names = append(names, name)
	}
	sort.Strings(names)
	results := make([]poolResult, len(names))
	workers, workCtx := errgroup.WithContext(ctx)
	workers.SetLimit(4)
	for i, name := range names {
		workers.Go(func() error {
			var err error
			results[i], err = e.readPool(workCtx, snap, view, e.Schema.byPool[name], pools[name], r, from, to, width, after)
			return err
		})
	}
	if err := workers.Wait(); err != nil {
		return nil, err
	}
	if continuation != nil && continuation.Live == "" && view.used.Load() {
		return nil, problem(409, "query_changed", "Pending live data changed; discard previous pages and restart the query")
	}
	for _, result := range results {
		for _, source := range result.sources {
			sources[source] = true
		}
		for _, ob := range result.data {
			if after == nil || before(*after, ob.position) {
				data = append(data, ob)
			}
		}
		if len(data) > 200000 {
			return nil, problem(413, "query_scan_limit", "Too many observations; reduce signals or max_points")
		}
	}
	sort.Slice(data, func(i, j int) bool { return before(data[i].position, data[j].position) })
	page := &Page{Schema: e.Schema.Version, Data: []Observation{}, Bucket: width, Provisional: view.used.Load() || sources["ring"] || to > time.Now().Add(-time.Second).UnixMilli()}
	live := ""
	if r.T1 == "live" || view.used.Load() || to > time.Now().Add(-e.Cache.Ring.window).UnixMilli() {
		page.Live = &view.status
		live = view.signature()
	}
	for source := range sources {
		page.Sources = append(page.Sources, source)
	}
	sort.Strings(page.Sources)
	// Reserve the envelope and cursor once; trimming one row at a time would
	// repeatedly serialize the entire page near the byte limit.
	bytes := 4096
	for _, row := range data {
		if row.size == 0 {
			encoded, err := json.Marshal(row)
			if err != nil {
				return nil, err
			}
			row.size = len(encoded)
		}
		if row.size > 65536 {
			return nil, problem(413, "sample_too_large", "A value exceeds 64 KiB")
		}
		if len(page.Data) >= r.PageSize || bytes+row.size+1 > byteLimit {
			break
		}
		page.Data = append(page.Data, row)
		bytes += row.size + 1
	}
	if len(data) > len(page.Data) && len(page.Data) > 0 {
		if stream {
			return nil, problem(413, "stream_too_large", "Reduce the live range, selected signals or max_points")
		}
		token, err := e.encodeCursor(r, from, to, page.Data[len(page.Data)-1].position, dataset, live)
		if err != nil {
			return nil, err
		}
		page.Next = &token
	}
	return page, nil
}

type accumulator struct {
	stamp, session, ns, count int64
	signal, agg               string
	sum                       float64
	minimum, maximum, last    any
}

func (a *accumulator) add(ns int64, v any) error {
	if a.count == 0 {
		a.minimum, a.maximum = v, v
	}
	if a.count == 0 || ns >= a.ns {
		a.last = v
		a.ns = ns
	}
	if a.agg != "last" {
		n, err := number(v)
		if err != nil {
			return problem(400, "unsupported_aggregate", "Aggregate requires numeric samples")
		}
		a.sum += n
		if compareNumbers(v, a.minimum) < 0 {
			a.minimum = v
		}
		if compareNumbers(v, a.maximum) > 0 {
			a.maximum = v
		}
	}
	a.count++
	return nil
}
func (a *accumulator) value() any {
	switch a.agg {
	case "mean":
		return a.sum / float64(a.count)
	case "min":
		return a.minimum
	case "max":
		return a.maximum
	default:
		return a.last
	}
}
func compareNumbers(a, b any) int {
	if x, ok := a.(float64); ok {
		if y, ok := b.(float64); ok {
			if math.IsNaN(x) {
				if math.IsNaN(y) {
					return 0
				}
				return 1
			}
			if math.IsNaN(y) || x < y {
				return -1
			}
			if x > y {
				return 1
			}
			return 0
		}
	}
	xr, xok := new(big.Rat).SetString(fmt.Sprint(a))
	yr, yok := new(big.Rat).SetString(fmt.Sprint(b))
	if xok && yok {
		return xr.Cmp(yr)
	}
	x, _ := number(a)
	y, _ := number(b)
	if math.IsNaN(x) {
		if math.IsNaN(y) {
			return 0
		}
		return 1
	}
	if math.IsNaN(y) {
		return -1
	}
	if x < y {
		return -1
	}
	if x > y {
		return 1
	}
	return 0
}
