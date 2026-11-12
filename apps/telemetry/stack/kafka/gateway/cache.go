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

type cachedSlab struct {
	Data      []byte
	Offsets   Offsets
	Signature string
}
type Cache struct {
	hot              *ristretto.Cache[string, *cachedSlab]
	baseline         *ristretto.Cache[string, *cachedSlab]
	pinned           map[string]*cachedSlab
	pinBytes, pinCap int64
	pinDays          map[string]bool
	mu               sync.Mutex
	hits             map[string]uint8
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
	c := &Cache{hot: hot, baseline: base, Files: files, Ring: r, Schema: s, pinned: map[string]*cachedSlab{}, pinCap: pinBytes, pinDays: map[string]bool{}, hits: map[string]uint8{}}
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
	cost := int64(len(value.Data) + len(key) + 128)
	if c.pinDays[time.UnixMilli(start).UTC().Format("2006-01-02")] && cost+c.pinBytes <= c.pinCap {
		if _, ok := c.pinned[key]; !ok {
			c.pinned[key] = value
			c.pinBytes += cost
		}
		return
	}
	if len(c.hits) > 100000 {
		clear(c.hits)
	}
	c.hits[key]++
	if c.hits[key] >= 2 {
		c.hot.Set(key, value, cost)
	}
}

func (c *Cache) Rows(ctx context.Context, snap Snapshot, p Pool, start, end int64) ([]Row, string, error) {
	key := fmt.Sprintf("%s/%s/%d", c.Schema.StorageID, p.Table, start)
	signature := externalSignature(snap.Sessions())
	_, currentCovered := c.Ring.Read(snap.Offsets(), p.Table, start, end)
	if entry, ok := c.baseline.Get(key); ok && entry.Signature == signature && currentCovered {
		if tail, covered := c.Ring.Read(entry.Offsets, p.Table, start, end); covered {
			base, err := decodeSlab(p, entry.Data)
			if err == nil {
				c.Reads[0].Add(1)
				return mergeRows(base, tail, snap.Sessions()), "ring", nil
			}
		}
	}
	tail, tailCovered := c.Ring.Read(snap.Offsets(), p.Table, start, end)
	// Sealed caches use data revisions; live baselines instead prove that every
	// intervening database write is represented by the contiguous Kafka tail.
	result := []Row{}
	source := "L1"
	allSealed := len(snap.Sessions()) > 0
	for _, session := range snap.Sessions() {
		if session.Start >= end || session.End < start {
			continue
		}
		sealedKey := fmt.Sprintf("%s/%s/%d/%d/%d", c.Schema.StorageID, p.Table, session.ID, session.Revision, start)
		c.mu.Lock()
		entry, ok := c.pinned[sealedKey]
		c.mu.Unlock()
		if !ok {
			entry, ok = c.hot.Get(sealedKey)
		}
		if ok {
			rows, err := decodeSlab(p, entry.Data)
			if err == nil {
				result = append(result, rows...)
				continue
			}
		}
		rows, found, err := c.Files.Read(p, session, start, end)
		if err == nil && found {
			source = "L2"
			data, err := encodeSlab(p, rows)
			if err != nil {
				return nil, "", err
			}
			c.admit(sealedKey, &cachedSlab{Data: data}, start)
			result = append(result, rows...)
			continue
		}
		allSealed = false
		c.Files.Queue(session.ID)
	}
	if allSealed && (tailCovered || end <= time.Now().Add(-c.Ring.window).UnixMilli()) {
		if source == "L1" {
			c.Reads[1].Add(1)
		} else {
			c.Reads[2].Add(1)
		}
		return mergeRows(result, tail, snap.Sessions()), source, nil
	}
	// Coalesce the expensive fetch while each caller retains its own metadata
	// snapshot. A revision/offset signature prevents cross-snapshot reuse.
	flightKey := fmt.Sprintf("%s/%v/%v", key, snap.Sessions(), snap.Offsets())
	value, err, _ := c.flight.Do(flightKey, func() (any, error) { return snap.Read(ctx, p, start, end, nil) })
	if err != nil {
		return nil, "", err
	}
	rows := value.([]Row)
	c.Reads[3].Add(1)
	if !tailCovered && end > time.Now().Add(-c.Ring.window).UnixMilli() {
		return nil, "", problem(503, "tail_unavailable", "Kafka coverage is incomplete; retry after the shared consumer catches up")
	}
	if end > time.Now().Add(-c.Ring.window).UnixMilli() {
		data, err := encodeSlab(p, rows)
		if err != nil {
			return nil, "", err
		}
		c.baseline.Set(key, &cachedSlab{Data: data, Offsets: snap.Offsets(), Signature: signature}, int64(len(data)+len(key)+512))
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
		data, err := encodeSlab(p, selected)
		if err != nil {
			return nil, "", err
		}
		sealedKey := fmt.Sprintf("%s/%s/%d/%d/%d", c.Schema.StorageID, p.Table, session.ID, session.Revision, start)
		c.admit(sealedKey, &cachedSlab{Data: data}, start)
	}
	return mergeRows(rows, tail, snap.Sessions()), "postgres", nil
}
