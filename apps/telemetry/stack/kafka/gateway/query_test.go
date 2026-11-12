package gateway

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"
)

type fixtureStore struct {
	sessions []Session
	offsets  Offsets
	rows     map[string][]Row
	reads    int
}
type fixtureSnapshot struct {
	store    *fixtureStore
	sessions []Session
	offsets  Offsets
}

func (f *fixtureStore) Snapshot(context.Context, int64, int64) (Snapshot, error) {
	offsets := Offsets{}
	for k, v := range f.offsets {
		offsets[k] = v
	}
	return &fixtureSnapshot{f, append([]Session{}, f.sessions...), offsets}, nil
}
func (f *fixtureStore) Day(context.Context, int64) (int64, int64, error) {
	return f.sessions[0].Start, f.sessions[0].End + 1, nil
}
func (s *fixtureSnapshot) Sessions() []Session   { return s.sessions }
func (s *fixtureSnapshot) Offsets() Offsets      { return s.offsets }
func (s *fixtureSnapshot) Close(context.Context) {}
func (s *fixtureSnapshot) Read(ctx context.Context, p Pool, from, to int64, id *int64) ([]Row, error) {
	s.store.reads++
	rows := []Row{}
	for _, r := range s.store.rows[p.Table] {
		if r.Time >= from && r.Time < to && (id == nil || r.Session == *id) {
			rows = append(rows, r)
		}
	}
	return rows, nil
}
func setup(t *testing.T) (*Engine, *fixtureStore, int64) {
	t.Helper()
	stamp := time.Now().UnixMilli()/60000*60000 - 120000
	schema := &Schema{StorageID: "storage", Version: "catalog", Pools: []Pool{{Table: "controls_100hz", Group: "controls", Frequency: 100, Fields: []Field{{Name: "apps", SQLType: "double precision"}, {Name: "enabled", SQLType: "boolean"}, {Name: "cells", SQLType: "double precision[]"}}}, {Table: "pack_1hz", Group: "pack", Frequency: 1, Fields: []Field{{Name: "temp", SQLType: "double precision"}}}}, Signals: []Signal{{Name: "controls.apps", Group: "controls"}, {Name: "controls.enabled", Group: "controls"}, {Name: "controls.cells", Group: "controls"}, {Name: "pack.temp", Group: "pack"}}}
	if err := schema.initialize(); err != nil {
		t.Fatal(err)
	}
	store := &fixtureStore{sessions: []Session{{ID: stamp, Start: stamp, End: stamp + 2000, Revision: 1, External: 0}}, offsets: Offsets{0: 10}, rows: map[string][]Row{"controls_100hz": {{Time: stamp + 10, NS: (stamp+10)*1000000 + 1, Session: stamp, Values: map[string]any{"apps": 0.0, "enabled": false, "cells": []any{}}}}}}
	ring := NewRing(15*time.Minute, 1<<20)
	ring.Start(0, 10)
	ring.Ready(true)
	files := NewFiles(t.TempDir(), 64<<20, schema)
	cache, err := NewCache(schema, ring, files, 8<<20, 8<<20, 0, nil)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(cache.Close)
	return &Engine{Schema: schema, Store: store, Cache: cache, Secret: []byte("secret")}, store, stamp
}
func request(start int64, signals ...string) Request {
	return Request{Car: "artemis", Signals: signals, T0: time.UnixMilli(start).UTC().Format(time.RFC3339Nano), T1: time.UnixMilli(start + 5000).UTC().Format(time.RFC3339Nano), PageSize: 1000}
}

func TestRecentWindowUsesBaselineAndKafkaAcrossPersistence(t *testing.T) {
	e, store, start := setup(t)
	ctx := context.Background()
	r := request(start, "controls.apps")
	first, err := e.Query(ctx, r)
	if err != nil {
		t.Fatal(err)
	}
	if len(first.Data) != 1 || first.Data[0].Value != 0.0 {
		t.Fatalf("first: %+v", first)
	}
	reads := store.reads
	e.Cache.Ring.Add(0, 10, []LiveRow{{Pool: "controls_100hz", Row: Row{Time: start + 20, NS: (start + 20) * 1000000, Values: map[string]any{"apps": 50.0}}}})
	live, err := e.Query(ctx, r)
	if err != nil {
		t.Fatal(err)
	}
	if len(live.Data) != 2 || store.reads != reads {
		t.Fatalf("live=%+v DB reads=%d", live, store.reads)
	}
	store.rows["controls_100hz"] = append(store.rows["controls_100hz"], Row{Time: start + 20, NS: (start + 20) * 1000000, Session: start, Values: map[string]any{"apps": 50.0}})
	store.offsets[0] = 11
	store.sessions[0].Revision++
	persisted, err := e.Query(ctx, r)
	if err != nil {
		t.Fatal(err)
	}
	if len(persisted.Data) != 2 || store.reads != reads {
		t.Fatalf("Persistence duplicated/refetched data: %+v reads=%d", persisted, store.reads)
	}
}
func TestImportInvalidatesRecentBaseline(t *testing.T) {
	e, store, start := setup(t)
	r := request(start, "controls.apps")
	if _, err := e.Query(context.Background(), r); err != nil {
		t.Fatal(err)
	}
	store.rows["controls_100hz"][0].Values["apps"] = 99.0
	store.sessions[0].External++
	store.sessions[0].Revision++
	page, err := e.Query(context.Background(), r)
	if err != nil {
		t.Fatal(err)
	}
	if page.Data[0].Value != 99.0 || store.reads != 2 {
		t.Fatalf("stale import: %+v reads=%d", page, store.reads)
	}
}
func TestBucketsAndPaginationAcrossPools(t *testing.T) {
	e, store, start := setup(t)
	store.rows["controls_100hz"] = append(store.rows["controls_100hz"], Row{Time: start + 50, NS: (start + 50) * 1000000, Session: start, Values: map[string]any{"apps": 80.0}})
	store.rows["pack_1hz"] = []Row{{Time: start + 40, NS: (start + 40) * 1000000, Session: start, Values: map[string]any{"temp": 45.0}}}
	r := request(start, "controls.apps", "pack.temp")
	r.Aggregate = "max"
	r.BucketMS = 1000
	r.PageSize = 1
	page, err := e.Query(context.Background(), r)
	if err != nil {
		t.Fatal(err)
	}
	if len(page.Data) != 1 || page.Data[0].Value != 80.0 || page.Next == nil {
		t.Fatalf("max: %+v", page)
	}
	r.PageToken = *page.Next
	second, err := e.Query(context.Background(), r)
	if err != nil {
		t.Fatal(err)
	}
	if len(second.Data) != 1 || second.Data[0].Signal != "pack.temp" || second.Data[0].Value != 45.0 {
		t.Fatalf("page2: %+v", second)
	}
}
func TestSealedArrowFallsBackAfterDeletionAndCorruption(t *testing.T) {
	e, store, start := setup(t)
	e.Schema.Pools[0].Fields = append(e.Schema.Pools[0].Fields, Field{Name: "counter", SQLType: "numeric"})
	store.rows["controls_100hz"][0].Values["counter"] = "18446744073709551615"
	ctx := context.Background()
	pool := e.Schema.Pools[0]
	session := store.sessions[0]
	if err := e.Cache.Files.Seal(ctx, store, session); err != nil {
		t.Fatal(err)
	}
	rows, found, err := e.Cache.Files.Read(pool, session, start, start+5000)
	if err == nil && found && len(rows) > 0 && rows[0].Values["counter"] != "18446744073709551615" {
		t.Fatal("mmap numeric value changed after the file closed")
	}
	if err != nil || !found || len(rows) != 1 || rows[0].Values["enabled"] != false || len(rows[0].Values["cells"].([]any)) != 0 {
		t.Fatalf("Arrow data: %+v %v %v", rows, found, err)
	}
	name := e.Cache.Files.name(pool, session)
	if err = os.WriteFile(name+".arrow", []byte("corrupt"), 0600); err != nil {
		t.Fatal(err)
	}
	r := request(start, "controls.apps")
	page, err := e.Query(ctx, r)
	if err != nil || len(page.Data) != 1 {
		t.Fatalf("corrupt fallback: %+v %v", page, err)
	}
	for _, ext := range []string{".arrow", ".json"} {
		if err = os.Remove(name + ext); err != nil {
			t.Fatal(err)
		}
	}
	if err = e.Cache.Files.Seal(ctx, store, session); err != nil {
		t.Fatal(err)
	}
	if _, err = os.Stat(filepath.Join(e.Cache.Files.Dir, fmt.Sprintf("storage_%d_1_controls_100hz.arrow", start))); err != nil {
		t.Fatal(err)
	}
}

func TestOverviewPreservesWeightedMeanPeaksAndPartialBounds(t *testing.T) {
	e, store, start := setup(t)
	pool := e.Schema.Pools[0]
	store.rows[pool.Table] = append(store.rows[pool.Table], Row{Time: start + 20, NS: (start + 20) * 1000000, Session: start, Values: map[string]any{"apps": 100.0}}, Row{Time: start + 1010, NS: (start + 1010) * 1000000, Session: start, Values: map[string]any{"apps": 20.0}})
	if err := e.Cache.Files.Seal(context.Background(), store, store.sessions[0]); err != nil {
		t.Fatal(err)
	}
	r := request(start, "controls.apps")
	r.Aggregate = "mean"
	r.BucketMS = 5000
	page, err := e.Query(context.Background(), r)
	if err != nil {
		t.Fatal(err)
	}
	if page.Data[0].Value != 40.0 {
		t.Fatalf("weighted mean: %+v", page.Data)
	}
	r.Aggregate = "max"
	page, err = e.Query(context.Background(), r)
	if err != nil || page.Data[0].Value != 100.0 {
		t.Fatalf("peak: %+v %v", page, err)
	}
	r.T0 = time.UnixMilli(start + 15).UTC().Format(time.RFC3339Nano)
	r.Aggregate = "mean"
	page, err = e.Query(context.Background(), r)
	if err != nil || page.Data[0].Value != 60.0 {
		t.Fatalf("partial bucket: %+v %v", page, err)
	}
}
