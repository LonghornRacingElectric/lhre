package gateway

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"sort"
	"sync"
	"sync/atomic"
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
	epoch           string
	generation      uint64
	assigned        map[int32]bool
	heads           Offsets
	observed        map[int32]int64
	latestSample    int64
}

type LiveStatus struct {
	Ready            bool   `json:"ready"`
	KafkaLagMessages *int64 `json:"kafka_lag_messages"`
	LatestSampleMS   *int64 `json:"latest_sample_ms"`
	ObservedAtMS     *int64 `json:"observed_at_ms"`
}

type ringView struct {
	ring       *Ring
	generation uint64
	next       Offsets
	status     LiveStatus
	used       atomic.Bool
}

func NewRing(window time.Duration, maxBytes int64) *Ring {
	identity := make([]byte, 16)
	if _, err := rand.Read(identity); err != nil {
		panic(err)
	}
	return &Ring{window: window, maxBytes: maxBytes, epoch: hex.EncodeToString(identity), assigned: map[int32]bool{}, heads: Offsets{}, observed: map[int32]int64{}, floor: Offsets{}, next: Offsets{}, listeners: map[chan struct{}]bool{}}
}

func (r *Ring) Assign(partitions []int32) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.generation++
	r.ready = false
	r.records, r.bytes = nil, 0
	r.floor, r.next, r.heads = Offsets{}, Offsets{}, Offsets{}
	r.assigned, r.observed = map[int32]bool{}, map[int32]int64{}
	for _, partition := range partitions {
		r.assigned[partition] = false
	}
}

func (r *Ring) Start(partition int32, offset int64) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.generation++
	r.floor[partition], r.next[partition] = offset, offset
	r.assigned[partition] = true
}
func (r *Ring) Ready(ready bool) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if !ready {
		r.generation++
	}
	r.ready = ready && len(r.assigned) > 0
	for _, initialized := range r.assigned {
		r.ready = r.ready && initialized
	}
}

func (r *Ring) Observe(partition int32, head int64) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.heads[partition] = head
	r.observed[partition] = time.Now().UnixMilli()
}

func (r *Ring) viewLocked() *ringView {
	view := &ringView{ring: r, generation: r.generation, next: Offsets{}, status: LiveStatus{Ready: r.ready}}
	lag, observed := int64(0), int64(0)
	known := r.ready
	for partition, offset := range r.next {
		view.next[partition] = offset
		head, ok := r.heads[partition]
		known = known && ok
		lag += max(0, head-offset)
		stamp := r.observed[partition]
		if observed == 0 || stamp < observed {
			observed = stamp
		}
	}
	if known {
		view.status.KafkaLagMessages, view.status.ObservedAtMS = &lag, &observed
	}
	if r.latestSample > 0 {
		latest := r.latestSample
		view.status.LatestSampleMS = &latest
	}
	return view
}

func (r *Ring) View() *ringView {
	r.mu.RLock()
	defer r.mu.RUnlock()
	return r.viewLocked()
}

func (view *ringView) signature() string {
	encoded, _ := json.Marshal(view.next)
	return fmt.Sprintf("%s/%d/%s", view.ring.epoch, view.generation, encoded)
}

func (r *Ring) Add(partition int32, offset int64, rows []LiveRow) {
	r.add(partition, offset, rows, false)
}

func (r *Ring) Reject(partition int32, offset int64) {
	r.add(partition, offset, nil, true)
}

func (r *Ring) add(partition int32, offset int64, rows []LiveRow, rejected bool) {
	size := int64(128)
	for _, live := range rows {
		size += int64(len(live.Pool)) + rowCost(live.Row)
	}
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
	r.records = append(r.records, liveRecord{partition, offset, now, rows, size})
	r.bytes += size
	r.next[partition] = offset + 1
	for _, live := range rows {
		r.latestSample = max(r.latestSample, live.Row.Time)
	}
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

func (view *ringView) covered(from Offsets) bool {
	r := view.ring
	if !view.status.Ready || !r.ready || r.generation != view.generation {
		return false
	}
	for part, floor := range r.floor {
		off := from[part]
		if off < floor || off > view.next[part] {
			return false
		}
	}
	for part, off := range from {
		if _, ok := view.next[part]; !ok || off > view.next[part] {
			return false
		}
	}
	return true
}

func (view *ringView) Covers(from Offsets) bool {
	r := view.ring
	r.mu.RLock()
	defer r.mu.RUnlock()
	return view.covered(from)
}

func (view *ringView) Read(from Offsets, pool string, start, end int64) ([]Row, bool) {
	r := view.ring
	r.mu.RLock()
	defer r.mu.RUnlock()
	if !view.covered(from) {
		return nil, false
	}
	result := []Row{}
	for _, record := range r.records {
		if record.offset < from[record.partition] || record.offset >= view.next[record.partition] {
			continue
		}
		for _, live := range record.rows {
			if live.Pool == pool && live.Row.Time >= start && live.Row.Time < end {
				result = append(result, live.Row)
			}
		}
	}
	if len(result) > 0 {
		view.used.Store(true)
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
	status := r.viewLocked().status
	return map[string]any{"bytes": r.bytes, "records": len(r.records), "gaps": r.gaps, "ready": status.Ready, "kafka_lag_messages": status.KafkaLagMessages, "latest_sample_ms": status.LatestSampleMS, "observed_at_ms": status.ObservedAtMS}
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
