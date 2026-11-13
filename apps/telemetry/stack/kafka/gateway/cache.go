package gateway

import (
	"context"
	"fmt"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/dgraph-io/ristretto/v2"
	"golang.org/x/sync/singleflight"
)

const slabMS int64 = 60000

func valueCost(value any) int64 {
	switch value := value.(type) {
	case string:
		return 16 + int64(len(value))
	case []byte:
		return 24 + int64(cap(value))
	case []any:
		cost := int64(24 + cap(value)*16)
		for _, item := range value {
			cost += valueCost(item)
		}
		return cost
	default:
		return 16
	}
}

func rowCost(row Row) int64 {
	cost := int64(128 + len(row.Values)*64)
	for name, value := range row.Values {
		cost += int64(len(name)) + valueCost(value)
	}
	return cost
}

func rowsCost(rows []Row) int64 {
	cost := int64(64 + cap(rows)*32)
	for _, row := range rows {
		cost += rowCost(row)
	}
	return cost
}

type cachedSlab struct {
	Rows      []Row
	Prepared  []Observation
	Cost      int64
	Offsets   Offsets
	Signature string
}
type Cache struct {
	hot              *ristretto.Cache[string, *cachedSlab]
	baseline         *ristretto.Cache[string, *cachedSlab]
	pinned           map[string]*cachedSlab
	pinBytes, pinCap int64
	hotCap           int64
	pinDays          map[string]bool
	mu               sync.Mutex
	flight           singleflight.Group
	Files            *Files
	Ring             *Ring
	Schema           *Schema
	Reads            [4]atomic.Uint64
}

func NewCache(s *Schema, r *Ring, files *Files, hotBytes, baselineBytes, pinBytes int64, pinDays []string) (*Cache, error) {
	if pinBytes > hotBytes/2 {
		return nil, fmt.Errorf("QUERY_PIN_BYTES cannot exceed half of QUERY_L1_BYTES")
	}
	newCache := func(capacity int64) (*ristretto.Cache[string, *cachedSlab], error) {
		return ristretto.NewCache(&ristretto.Config[string, *cachedSlab]{NumCounters: 100000, MaxCost: max(1, capacity), BufferItems: 64, Metrics: true})
	}
	hot, err := newCache(hotBytes - pinBytes)
	if err != nil {
		return nil, err
	}
	base, err := newCache(baselineBytes)
	if err != nil {
		hot.Close()
		return nil, err
	}
	c := &Cache{hot: hot, baseline: base, Files: files, Ring: r, Schema: s, pinned: map[string]*cachedSlab{}, pinCap: pinBytes, hotCap: hotBytes - pinBytes, pinDays: map[string]bool{}}
	for _, day := range pinDays {
		if day != "" {
			if _, err := time.Parse("2006-01-02", day); err != nil {
				return nil, fmt.Errorf("invalid pinned UTC day: %s", day)
			}
			c.pinDays[day] = true
		}
	}
	return c, nil
}
func (c *Cache) Close() { c.hot.Close(); c.baseline.Close() }

// Do waits for the shared operation before the owning snapshot can close.
// Client cancellation must not cancel the operation for other callers.
func (c *Cache) sharedRead(ctx context.Context, key string, read func(context.Context) (any, error)) (any, error) {
	value, err, _ := c.flight.Do(key, func() (any, error) {
		work, cancel := context.WithTimeout(context.WithoutCancel(ctx), 30*time.Second)
		defer cancel()
		return read(work)
	})
	return value, err
}
func externalSignature(sessions []Session) string {
	parts := []string{}
	for _, s := range sessions {
		parts = append(parts, fmt.Sprintf("%d:%d", s.ID, s.External))
	}
	sort.Strings(parts)
	return strings.Join(parts, ",")
}
func (c *Cache) admit(key string, value *cachedSlab, start int64) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.hotCap == 0 && c.pinCap == 0 {
		return
	}
	if value.Cost == 0 {
		value.Cost = rowsCost(value.Rows)
	}
	cost := value.Cost + int64(len(key)+128)
	if c.pinDays[time.UnixMilli(start).UTC().Format("2006-01-02")] && cost+c.pinBytes <= c.pinCap {
		if _, ok := c.pinned[key]; !ok {
			c.pinned[key] = value
			c.pinBytes += cost
		}
		return
	}
	// Ristretto handles frequency admission. A second gate would repeat the
	// expensive miss, including between pages of the same query.
	if cost <= c.hotCap/4 {
		c.hot.Set(key, value, cost)
		c.hot.Wait()
	}
}

func rowsInRange(rows []Row, from, to int64) []Row {
	first := sort.Search(len(rows), func(i int) bool { return rows[i].Time >= from })
	last := sort.Search(len(rows), func(i int) bool { return rows[i].Time >= to })
	return rows[first:last]
}

func (c *Cache) Rows(ctx context.Context, snap Snapshot, view *ringView, p Pool, start, end int64, bounds ...int64) ([]Row, string, error) {
	from, to := start, end
	if len(bounds) == 2 {
		from, to = max(start, bounds[0]), min(end, bounds[1])
	}
	key := fmt.Sprintf("%s/%s/%v/%d", c.Schema.StorageID, p.Table, p.Fields, start)
	signature := externalSignature(snap.Sessions())
	currentCovered := view.Covers(snap.Offsets())
	if entry, ok := c.baseline.Get(key); ok && entry.Signature == signature && currentCovered {
		if tail, covered := view.Read(entry.Offsets, p.Table, from, to); covered {
			c.Reads[0].Add(1)
			return mergeRows(rowsInRange(entry.Rows, from, to), tail, snap.Sessions(), p.Fields), "ring", nil
		}
	}
	tail, tailCovered := view.Read(snap.Offsets(), p.Table, from, to)
	// Sealed caches use data revisions; live baselines instead prove that every
	// intervening database write is represented by the contiguous Kafka tail.
	result := []Row{}
	source := "L1"
	allSealed := len(snap.Sessions()) > 0
	for _, session := range snap.Sessions() {
		if session.Start >= end || session.End < start {
			continue
		}
		sealedKey := fmt.Sprintf("%s/%s/%v/%d/%d/%d", c.Schema.StorageID, p.Table, p.Fields, session.ID, session.Revision, start)
		c.mu.Lock()
		entry, ok := c.pinned[sealedKey]
		c.mu.Unlock()
		if !ok {
			entry, ok = c.hot.Get(sealedKey)
		}
		if ok {
			result = append(result, entry.Rows...)
			continue
		}
		rows, found, err := c.Files.Read(p, session, start, end)
		if err == nil && found {
			source = "L2"
			c.admit(sealedKey, &cachedSlab{Rows: rows}, start)
			result = append(result, rows...)
			continue
		}
		allSealed = false
		c.Files.Queue(session.ID)
	}
	if allSealed && (tailCovered || end <= time.Now().Add(-c.Ring.window).UnixMilli()) {
		sort.Slice(result, func(i, j int) bool { return result[i].Time < result[j].Time })
		if source == "L1" {
			c.Reads[1].Add(1)
		} else {
			c.Reads[2].Add(1)
		}
		return mergeRows(rowsInRange(result, from, to), tail, snap.Sessions(), p.Fields), source, nil
	}
	// Coalesce the expensive fetch while each caller retains its own metadata
	// snapshot. A revision/offset signature prevents cross-snapshot reuse.
	flightKey := fmt.Sprintf("%s/%v/%v", key, snap.Sessions(), snap.Offsets())
	value, err := c.sharedRead(ctx, flightKey, func(work context.Context) (any, error) { return snap.Read(work, p, start, end, nil) })
	if err != nil {
		return nil, "", err
	}
	rows := value.([]Row)
	c.Reads[3].Add(1)
	if !tailCovered && end > time.Now().Add(-c.Ring.window).UnixMilli() {
		return nil, "", problem(503, "tail_unavailable", "Kafka coverage is incomplete; retry after the shared consumer catches up")
	}
	if end > time.Now().Add(-c.Ring.window).UnixMilli() {
		c.baseline.Set(key, &cachedSlab{Rows: rows, Offsets: snap.Offsets(), Signature: signature}, rowsCost(rows)+int64(len(key))+512)
		c.baseline.Wait()
	}
	for _, session := range snap.Sessions() {
		if time.Now().UnixMilli()-session.Written < 300000 {
			continue
		}
		selected := []Row{}
		for _, row := range rows {
			if row.Session == session.ID {
				selected = append(selected, row)
			}
		}
		sealedKey := fmt.Sprintf("%s/%s/%v/%d/%d/%d", c.Schema.StorageID, p.Table, p.Fields, session.ID, session.Revision, start)
		c.admit(sealedKey, &cachedSlab{Rows: selected}, start)
	}
	return mergeRows(rowsInRange(rows, from, to), tail, snap.Sessions(), p.Fields), "postgres", nil
}
