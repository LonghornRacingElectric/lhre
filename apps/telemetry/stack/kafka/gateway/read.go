package gateway

import (
	"context"
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"time"
)

type windowSnapshot interface {
	Window(context.Context, Pool, int64, int64, int64, string, *Position, int) ([]Row, error)
}

type poolResult struct {
	data    []Observation
	sources []string
}

func projectPool(p Pool, signals []Signal) Pool {
	wanted := map[string]bool{}
	for _, sig := range signals {
		wanted[strings.SplitN(sig.Name, ".", 2)[1]] = true
	}
	fields := []Field{}
	for _, field := range p.Fields {
		if wanted[field.Name] {
			fields = append(fields, field)
		}
	}
	p.Fields = fields
	return p
}

func prepareObservations(data []Observation, limit int) (*cachedSlab, error) {
	sort.Slice(data, func(i, j int) bool { return before(data[i].position, data[j].position) })
	if limit > 0 && len(data) > limit {
		page := make([]Observation, limit)
		copy(page, data[:limit])
		data = page
	}
	cost := int64(256)
	for i := range data {
		encoded, err := json.Marshal(data[i])
		if err != nil {
			return nil, err
		}
		if len(encoded) > 65536 {
			return nil, problem(413, "sample_too_large", "A value exceeds 64 KiB")
		}
		data[i].size = len(encoded)
		cost += int64(len(encoded) + 224)
	}
	return &cachedSlab{Prepared: data, Cost: cost}, nil
}

func (e *Engine) historicalPool(ctx context.Context, snap Snapshot, p Pool, signals []Signal, r Request, from, to, width int64, after *Position) (poolResult, error) {
	pageKey := ""
	if r.Aggregate == "raw" {
		pageKey = fmt.Sprintf("/%d/%v", r.PageSize, after)
	}
	key := fmt.Sprintf("prepared/%s/%s/%v/%d/%d/%d/%s/%v%s", e.Schema.StorageID, p.Table, p.Fields, from, to, width, r.Aggregate, snap.Sessions(), pageKey)
	e.Cache.mu.Lock()
	entry, ok := e.Cache.pinned[key]
	e.Cache.mu.Unlock()
	if !ok {
		entry, ok = e.Cache.hot.Get(key)
	}
	if ok {
		e.Cache.Reads[1].Add(1)
		return poolResult{entry.Prepared, []string{"L1"}}, nil
	}
	value, err, _ := e.Cache.flight.Do(key, func() (any, error) {
		result := poolResult{}
		sealed := len(snap.Sessions()) > 0
		for _, session := range snap.Sessions() {
			if !e.Cache.Files.Available(p, session) || r.Aggregate != "raw" && width%1000 == 0 && !e.Cache.Files.Available(overviewPool(p), session) {
				sealed = false
				e.Cache.Files.Queue(session.ID)
			}
		}
		if sealed {
			var err error
			result, err = e.filePool(ctx, snap, p, signals, r, from, to, width, after)
			if err != nil {
				sealed = false
				for _, session := range snap.Sessions() {
					e.Cache.Files.Queue(session.ID)
				}
			}
		}
		if !sealed {
			if reader, ok := snap.(windowSnapshot); ok {
				limit := 20001
				if r.Aggregate == "raw" {
					limit = r.PageSize + 1
					// The seek includes the boundary row for remaining fields;
					// it may already be exhausted for this pool.
					if after != nil {
						limit++
					}
				}
				rows, err := reader.Window(ctx, p, from, to, width, r.Aggregate, after, limit)
				if err != nil {
					return nil, err
				}
				if r.Aggregate != "raw" && len(rows) == limit {
					return nil, problem(413, "query_scan_limit", "Too many session buckets; narrow the range")
				}
				result = poolResult{data: []Observation{}, sources: []string{"postgres"}}
				for _, row := range rows {
					for _, sig := range signals {
						if v := row.Values[strings.SplitN(sig.Name, ".", 2)[1]]; v != nil {
							ob := observation(row.Time, row.NS, row.Session, sig.Name, v)
							if r.Aggregate != "raw" || after == nil || before(*after, ob.position) {
								result.data = append(result.data, ob)
							}
						}
					}
				}
				e.Cache.Reads[3].Add(1)
			} else {
				var err error
				result, err = e.slabPool(ctx, snap, p, signals, r, from, to, width, after)
				if err != nil {
					return nil, err
				}
			}
		}
		limit := 0
		if r.Aggregate == "raw" {
			limit = r.PageSize + 1
		}
		if r.Aggregate != "raw" && len(result.data) > 200000 {
			return nil, problem(413, "query_scan_limit", "Too many observations; reduce signals or max_points")
		}
		prepared, err := prepareObservations(result.data, limit)
		if err != nil {
			return nil, err
		}
		result.data = prepared.Prepared
		e.Cache.admit(key, prepared, from)
		return result, nil
	})
	if err != nil {
		return poolResult{}, err
	}
	return value.(poolResult), nil
}

func poolObservations(aggregates map[string]*accumulator) []Observation {
	data := make([]Observation, 0, len(aggregates))
	for _, a := range aggregates {
		data = append(data, observation(a.stamp, a.stamp*1000000, a.session, a.signal, a.value()))
	}
	return data
}

func (e *Engine) filePool(ctx context.Context, snap Snapshot, p Pool, signals []Signal, r Request, from, to, width int64, after *Position) (poolResult, error) {
	result := poolResult{data: []Observation{}, sources: []string{"L2"}}
	aggregates := map[string]*accumulator{}
	scanned := 0
	start := from
	if r.Aggregate == "raw" && after != nil {
		start = max(start, after.Time)
	}
	for start < to {
		if ctx.Err() != nil {
			return result, ctx.Err()
		}
		end := min(to, (start/slabMS+1)*slabMS)
		for _, session := range snap.Sessions() {
			if session.Start >= end || session.End < start {
				continue
			}
			middleFrom, middleTo := start, start
			if r.Aggregate != "raw" && width%1000 == 0 {
				middleFrom = min(end, (start+999)/1000*1000)
				middleTo = max(middleFrom, end/1000*1000)
			}
			segments := []struct {
				from, to int64
				summary  bool
			}{{start, middleFrom, false}, {middleFrom, middleTo, true}, {middleTo, end, false}}
			for _, segment := range segments {
				if segment.to <= segment.from {
					continue
				}
				pool := p
				if segment.summary {
					pool = overviewPool(p)
					result.sources = []string{"L2-overview"}
				}
				rows, found, err := e.Cache.Files.Read(pool, session, segment.from, segment.to)
				if err != nil {
					return result, err
				}
				if !found {
					return result, fmt.Errorf("sealed pool disappeared")
				}
				scanned += len(rows)
				if scanned > 2000000 {
					return result, problem(413, "query_scan_limit", "Too many samples; narrow the range")
				}
				if r.Aggregate != "raw" {
					if err := addAggregateRows(aggregates, rows, signals, r.Aggregate, width, segment.summary); err != nil {
						return result, err
					}
				} else {
					for _, row := range rows {
						for _, sig := range signals {
							if v := row.Values[strings.SplitN(sig.Name, ".", 2)[1]]; v != nil {
								ob := observation(row.Time, row.NS, row.Session, sig.Name, v)
								if after == nil || before(*after, ob.position) {
									result.data = append(result.data, ob)
								}
							}
						}
					}
				}
			}
		}
		if r.Aggregate == "raw" && len(result.data) > r.PageSize {
			break
		}
		start = end
	}
	if r.Aggregate != "raw" {
		result.data = poolObservations(aggregates)
	}
	e.Cache.Reads[2].Add(1)
	return result, nil
}

// Recent reads retain a persisted baseline plus contiguous Kafka coverage.
func (e *Engine) slabPool(ctx context.Context, snap Snapshot, p Pool, signals []Signal, r Request, from, to, width int64, after *Position) (poolResult, error) {
	result := poolResult{data: []Observation{}, sources: []string{}}
	aggregates := map[string]*accumulator{}
	if _, ok := snap.(aggregateSnapshot); ok && r.Aggregate != "raw" && to-from > 2*slabMS {
		rows, tail, source, err := e.Cache.aggregate(ctx, snap, p, from, to, width)
		if err != nil {
			return result, err
		}
		if err = addAggregateRows(aggregates, rows, signals, r.Aggregate, width, true); err != nil {
			return result, err
		}
		if err = addAggregateRows(aggregates, tail, signals, r.Aggregate, width, false); err != nil {
			return result, err
		}
		return poolResult{poolObservations(aggregates), []string{source}}, nil
	}
	start := from / slabMS * slabMS
	if r.Aggregate == "raw" && after != nil {
		start = max(start, after.Time/slabMS*slabMS)
	}
	scanned := 0
	for ; start < to; start += slabMS {
		if ctx.Err() != nil {
			return result, ctx.Err()
		}
		rows, source, err := e.Cache.Rows(ctx, snap, p, start, start+slabMS, from, to)
		if err != nil {
			return result, err
		}
		result.sources = append(result.sources, source)
		scanned += len(rows)
		if scanned > 2000000 {
			return result, problem(413, "query_scan_limit", "Too many raw samples; narrow the range")
		}
		if r.Aggregate != "raw" {
			if err := addAggregateRows(aggregates, rows, signals, r.Aggregate, width, false); err != nil {
				return result, err
			}
		} else {
			for _, row := range rows {
				for _, sig := range signals {
					if v := row.Values[strings.SplitN(sig.Name, ".", 2)[1]]; v != nil {
						ob := observation(row.Time, row.NS, row.Session, sig.Name, v)
						if after == nil || before(*after, ob.position) {
							result.data = append(result.data, ob)
						}
					}
				}
			}
			if len(result.data) > r.PageSize {
				break
			}
		}
	}
	if r.Aggregate != "raw" {
		result.data = poolObservations(aggregates)
	}
	return result, nil
}

func (e *Engine) readPool(ctx context.Context, snap Snapshot, p Pool, signals []Signal, r Request, from, to, width int64, after *Position) (poolResult, error) {
	p = projectPool(p, signals)
	if to <= time.Now().Add(-e.Cache.Ring.window).UnixMilli() {
		tail, _ := e.Cache.Ring.Read(snap.Offsets(), p.Table, from, to)
		if len(tail) == 0 {
			return e.historicalPool(ctx, snap, p, signals, r, from, to, width, after)
		}
	}
	return e.slabPool(ctx, snap, p, signals, r, from, to, width, after)
}
