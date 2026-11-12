package gateway

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"math/big"
	"sort"
	"strconv"
	"strings"
	"time"
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
}
type Page struct {
	Schema      string        `json:"schema_version"`
	Data        []Observation `json:"data"`
	Next        *string       `json:"next_page_token"`
	Bucket      int64         `json:"bucket_ms,omitempty"`
	Sources     []string      `json:"sources"`
	Provisional bool          `json:"provisional"`
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
	var err error
	if r.DriveDay != nil {
		from, to, err = e.Store.Day(ctx, *r.DriveDay)
		if err != nil {
			return 0, 0, err
		}
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
		dayStart, dayEnd, err := e.Store.Day(ctx, *r.DriveDay)
		if err != nil {
			return 0, 0, err
		}
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
	return Observation{time.UnixMilli(t).UTC().Format("2006-01-02T15:04:05.000Z"), strconv.FormatInt(ns, 10), strconv.FormatInt(session, 10), signal, publicValue(value), Position{t, session, ns, signal}}
}

func (e *Engine) Query(ctx context.Context, r Request) (*Page, error) {
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
	after, err := e.cursor(r, &from, &to)
	if err != nil {
		return nil, err
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
	pools := map[string][]Signal{}
	for _, signal := range selected {
		pools[signal.Pool] = append(pools[signal.Pool], signal)
	}
	data := []Observation{}
	sources := map[string]bool{}
	aggregates := map[string]*accumulator{}
	if _, ok := snap.(aggregateSnapshot); ok && r.Aggregate != "raw" && to-from > 2*slabMS {
		for name, signals := range pools {
			p := e.Schema.byPool[name]
			if width%1000 == 0 && e.Cache.hasSealed(snap, p, from, to) {
				continue
			}
			rows, tail, source, err := e.Cache.aggregate(ctx, snap, p, from, to, width)
			if err != nil {
				return nil, err
			}
			sources[source] = true
			if err = addAggregateRows(aggregates, rows, signals, r.Aggregate, width, true); err != nil {
				return nil, err
			}
			if err = addAggregateRows(aggregates, tail, signals, r.Aggregate, width, false); err != nil {
				return nil, err
			}
			delete(pools, name)
		}
	}
	scanned := 0
	for start := max(from/slabMS*slabMS, func() int64 {
		if after != nil {
			return after.Time / slabMS * slabMS
		}
		return 0
	}()); start < to && len(pools) > 0; start += slabMS {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		for name, signals := range pools {
			var rows []Row
			source := "L2-overview"
			overview := false
			if r.Aggregate != "raw" && width%1000 == 0 && (start >= from || from%1000 == 0) && (start+slabMS <= to || to%1000 == 0) {
				rows, overview, err = e.Cache.Overview(ctx, snap, e.Schema.byPool[name], start, start+slabMS)
			}
			if err == nil && !overview {
				rows, source, err = e.Cache.Rows(ctx, snap, e.Schema.byPool[name], start, start+slabMS)
			}
			if err != nil {
				return nil, err
			}
			sources[source] = true
			scanned += len(rows)
			if scanned > 2000000 {
				return nil, problem(413, "query_scan_limit", "Too many raw samples; narrow the range")
			}
			for _, row := range rows {
				if row.Time < from || row.Time >= to {
					continue
				}
				for _, signal := range signals {
					field := strings.SplitN(signal.Name, ".", 2)[1]
					value, ok := row.Values[field]
					if overview {
						value, ok = row.Values[field+"_last"]
					}
					if !ok || value == nil {
						continue
					}
					if r.Aggregate == "raw" {
						ob := observation(row.Time, row.NS, row.Session, signal.Name, value)
						if after == nil || before(*after, ob.position) {
							data = append(data, ob)
						}
					} else {
						bucket := row.Time / width * width
						key := fmt.Sprintf("%d/%d/%s", bucket, row.Session, signal.Name)
						acc, ok := aggregates[key]
						if !ok {
							acc = &accumulator{stamp: bucket, session: row.Session, signal: signal.Name, agg: r.Aggregate}
							aggregates[key] = acc
						}
						if overview {
							err = acc.addSummary(field, row)
						} else {
							err = acc.add(row.NS, value)
						}
						if err != nil {
							return nil, err
						}
					}
				}
			}
		}
		if r.Aggregate == "raw" && len(data) > r.PageSize {
			break
		}
	}
	if r.Aggregate != "raw" {
		for _, a := range aggregates {
			ob := observation(a.stamp, a.stamp*1000000, a.session, a.signal, a.value())
			if after == nil || before(*after, ob.position) {
				data = append(data, ob)
			}
		}
	}
	sort.Slice(data, func(i, j int) bool { return before(data[i].position, data[j].position) })
	page := &Page{Schema: e.Schema.Version, Data: []Observation{}, Bucket: width, Provisional: sources["ring"] || to > time.Now().Add(-time.Second).UnixMilli()}
	for source := range sources {
		page.Sources = append(page.Sources, source)
	}
	sort.Strings(page.Sources)
	for _, row := range data {
		encoded, err := json.Marshal(row)
		if err != nil {
			return nil, err
		}
		if len(encoded) > 65536 {
			return nil, problem(413, "sample_too_large", "A value exceeds 64 KiB")
		}
		page.Data = append(page.Data, row)
		if len(page.Data) >= r.PageSize {
			break
		}
	}
	for {
		if len(data) > len(page.Data) && len(page.Data) > 0 {
			token, err := e.encodeCursor(r, from, to, page.Data[len(page.Data)-1].position)
			if err != nil {
				return nil, err
			}
			page.Next = &token
		} else {
			page.Next = nil
		}
		encoded, err := json.Marshal(page)
		if err != nil {
			return nil, err
		}
		if len(encoded) <= 2*1024*1024 {
			return page, nil
		}
		if len(page.Data) <= 1 {
			return nil, problem(413, "page_too_large", "Narrow the query")
		}
		page.Data = page.Data[:len(page.Data)-1]
	}
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
