package gateway

import (
	"encoding/json"
	"fmt"
	"sort"
	"sync"
	"time"
)

type LiveRow struct {
	Pool string
	Row  Row
}
type liveRecord struct {
	partition int32
	offset    int64
	received  int64
	rows      []LiveRow
	bytes     int64
}
type Ring struct {
	mu              sync.RWMutex
	records         []liveRecord
	floor, next     Offsets
	maxBytes, bytes int64
	window          time.Duration
	ready           bool
	listeners       map[chan struct{}]bool
	gaps            uint64
}

func NewRing(window time.Duration, maxBytes int64) *Ring {
	return &Ring{window: window, maxBytes: maxBytes, floor: Offsets{}, next: Offsets{}, listeners: map[chan struct{}]bool{}}
}

func (r *Ring) Start(partition int32, offset int64) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.floor[partition], r.next[partition] = offset, offset
}
func (r *Ring) Ready(ready bool) { r.mu.Lock(); r.ready = ready; r.mu.Unlock() }

func (r *Ring) Add(partition int32, offset int64, rows []LiveRow) {
	r.add(partition, offset, rows, false)
}

func (r *Ring) Reject(partition int32, offset int64) {
	r.add(partition, offset, nil, true)
}

func (r *Ring) add(partition int32, offset int64, rows []LiveRow, rejected bool) {
	encoded, _ := json.Marshal(rows)
	r.mu.Lock()
	defer r.mu.Unlock()
	if offset < r.next[partition] {
		return
	}
	if offset != r.next[partition] {
		r.gaps++
		r.floor[partition] = offset
	}
	if rejected {
		r.gaps++
		r.floor[partition] = offset + 1
	}
	// Copy-on-read keeps callers from sharing mutable values with the consumer.
	now := time.Now().UnixMilli()
	size := int64(len(encoded)*2 + 128)
	r.records = append(r.records, liveRecord{partition, offset, now, rows, size})
	r.bytes += size
	r.next[partition] = offset + 1
	for len(r.records) > 0 && (r.bytes > r.maxBytes || r.records[0].received < now-r.window.Milliseconds()) {
		old := r.records[0]
		r.records[0] = liveRecord{}
		r.records = r.records[1:]
		r.bytes -= old.bytes
		if old.offset+1 > r.floor[old.partition] {
			r.floor[old.partition] = old.offset + 1
		}
	}
	for ch := range r.listeners {
		select {
		case ch <- struct{}{}:
		default:
		}
	}
}

func (r *Ring) covered(from Offsets) bool {
	if !r.ready {
		return false
	}
	for part, floor := range r.floor {
		off := from[part]
		if off < floor || off > r.next[part] {
			return false
		}
	}
	for part, off := range from {
		if _, ok := r.next[part]; !ok || off > r.next[part] {
			return false
		}
	}
	return true
}

func (r *Ring) Covers(from Offsets) bool {
	r.mu.RLock()
	defer r.mu.RUnlock()
	return r.covered(from)
}

func (r *Ring) Read(from Offsets, pool string, start, end int64) ([]Row, bool) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	if !r.covered(from) {
		return nil, false
	}
	result := []Row{}
	for _, record := range r.records {
		if record.offset < from[record.partition] {
			continue
		}
		for _, live := range record.rows {
			if live.Pool == pool && live.Row.Time >= start && live.Row.Time < end {
				result = append(result, live.Row)
			}
		}
	}
	return result, true
}

func (r *Ring) Subscribe() (chan struct{}, func()) {
	r.mu.Lock()
	defer r.mu.Unlock()
	ch := make(chan struct{}, 1)
	r.listeners[ch] = true
	return ch, func() { r.mu.Lock(); delete(r.listeners, ch); r.mu.Unlock() }
}
func (r *Ring) Stats() map[string]any {
	r.mu.RLock()
	defer r.mu.RUnlock()
	return map[string]any{"bytes": r.bytes, "records": len(r.records), "gaps": r.gaps, "ready": r.ready}
}

func mergeRows(base, tail []Row, sessions []Session, fields ...[]Field) []Row {
	merged := map[string]Row{}
	for _, rows := range [][]Row{tail, base} {
		for _, row := range rows {
			key := fmt.Sprintf("%d/%d", row.Time, row.NS)
			existing, ok := merged[key]
			if !ok {
				existing = Row{Time: row.Time, NS: row.NS, Values: map[string]any{}}
			}
			if len(fields) == 0 {
				for k, v := range row.Values {
					existing.Values[k] = v
				}
			} else {
				for _, f := range fields[0] {
					if v, ok := row.Values[f.Name]; ok {
						existing.Values[f.Name] = v
					}
				}
			}
			if row.Session != 0 {
				existing.Session = row.Session
			}
			merged[key] = existing
		}
	}
	result := make([]Row, 0, len(merged))
	for _, row := range merged {
		if row.Session == 0 {
			for _, s := range sessions {
				if row.Time >= s.Start-300000 && row.Time <= s.End+300000 {
					row.Session = s.ID
					break
				}
			}
		}
		result = append(result, row)
	}
	sort.Slice(result, func(i, j int) bool {
		if result[i].Time != result[j].Time {
			return result[i].Time < result[j].Time
		}
		if result[i].Session != result[j].Session {
			return result[i].Session < result[j].Session
		}
		return result[i].NS < result[j].NS
	})
	return result
}
