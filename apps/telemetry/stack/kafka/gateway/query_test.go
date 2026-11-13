package gateway

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

type fixtureStore struct {
	sessions []Session
	offsets  Offsets
	rows     map[string][]Row
	reads    atomic.Int64
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
	s.store.reads.Add(1)
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
	reads := store.reads.Load()
	e.Cache.Ring.Add(0, 10, []LiveRow{{Pool: "controls_100hz", Row: Row{Time: start + 20, NS: (start + 20) * 1000000, Values: map[string]any{"apps": 50.0}}}})
	live, err := e.Query(ctx, r)
	if err != nil {
		t.Fatal(err)
	}
	if len(live.Data) != 2 || store.reads.Load() != reads {
		t.Fatalf("live=%+v DB reads=%d", live, store.reads.Load())
	}
	store.rows["controls_100hz"] = append(store.rows["controls_100hz"], Row{Time: start + 20, NS: (start + 20) * 1000000, Session: start, Values: map[string]any{"apps": 50.0}})
	store.offsets[0] = 11
	store.sessions[0].Revision++
	persisted, err := e.Query(ctx, r)
	if err != nil {
		t.Fatal(err)
	}
	if len(persisted.Data) != 2 || store.reads.Load() != reads {
		t.Fatalf("Persistence duplicated/refetched data: %+v reads=%d", persisted, store.reads.Load())
	}
}

func TestSharedReadSurvivesLeaderCancellation(t *testing.T) {
	e, _, _ := setup(t)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	started, release := make(chan struct{}), make(chan struct{})
	leader := make(chan error, 1)
	go func() {
		_, err := e.Cache.sharedRead(ctx, "cancelled-leader", func(work context.Context) (any, error) {
			close(started)
			<-release
			return "complete", work.Err()
		})
		leader <- err
	}()
	<-started
	// DoChan registers the follower before cancellation, without a timing race.
	follower := e.Cache.flight.DoChan("cancelled-leader", func() (any, error) { return "unexpected second read", nil })
	cancel()
	close(release)
	result := <-follower
	if result.Err != nil || result.Val != "complete" || !result.Shared {
		t.Fatalf("follower: %+v", result)
	}
	if err := <-leader; err != nil {
		t.Fatal(err)
	}
}

func TestRejectedRecordRequiresPersistenceProofBeforeServingRecentData(t *testing.T) {
	e, store, start := setup(t)
	r := request(start, "controls.apps")
	if _, err := e.Query(context.Background(), r); err != nil {
		t.Fatal(err)
	}
	e.Cache.Ring.Reject(0, 10)
	row := Row{Time: start + 20, NS: (start + 20) * 1000000, Values: map[string]any{"apps": 50.0}}
	e.Cache.Ring.Add(0, 11, []LiveRow{{Pool: "controls_100hz", Row: row}})
	_, err := e.Query(context.Background(), r)
	if p, ok := err.(*queryProblem); !ok || p.Code != "tail_unavailable" {
		t.Fatalf("incomplete coverage: %v", err)
	}
	row.Session = start
	store.rows["controls_100hz"] = append(store.rows["controls_100hz"], row)
	store.offsets[0] = 12
	store.sessions[0].Revision++
	page, err := e.Query(context.Background(), r)
	if err != nil || len(page.Data) != 2 || page.Data[1].Value != 50.0 {
		t.Fatalf("recovery: %+v %v", page, err)
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
	if page.Data[0].Value != 99.0 || store.reads.Load() != 2 {
		t.Fatalf("stale import: %+v reads=%d", page, store.reads.Load())
	}
}

func TestPreparedHistorySeparatesSelectionsAndRefreshesAfterImport(t *testing.T) {
	e, store, start := setup(t)
	shift := int64(time.Hour / time.Millisecond)
	start -= shift
	store.sessions[0].ID, store.sessions[0].Start, store.sessions[0].End = start, start, start+2000
	row := &store.rows["controls_100hz"][0]
	row.Time, row.NS, row.Session = row.Time-shift, row.NS-shift*1000000, start
	r := request(start, "controls.apps")
	for i := 0; i < 2; i++ {
		page, err := e.Query(context.Background(), r)
		if err != nil || len(page.Data) != 1 || page.Data[0].Value != 0.0 {
			t.Fatalf("cached history: %+v %v", page, err)
		}
	}
	other, err := e.Query(context.Background(), request(start, "controls.enabled"))
	if err != nil || len(other.Data) != 1 || other.Data[0].Value != false {
		t.Fatalf("selection: %+v %v", other, err)
	}
	row.Values["apps"] = 99.0
	store.sessions[0].Revision++
	store.sessions[0].External++
	page, err := e.Query(context.Background(), r)
	if err != nil || len(page.Data) != 1 || page.Data[0].Value != 99.0 {
		t.Fatalf("import: %+v %v", page, err)
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

func TestPaginationRejectsChangedRecordingsAndLiveFrontiers(t *testing.T) {
	for _, historical := range []bool{true, false} {
		t.Run(fmt.Sprint(historical), func(t *testing.T) {
			engine, store, start := setup(t)
			if historical {
				start -= int64(time.Hour / time.Millisecond)
				store.sessions[0] = Session{ID: start, Start: start, End: start + 2000, Revision: 1}
			}
			store.rows["controls_100hz"] = []Row{{Time: start + 10, NS: (start + 10) * 1000000, Session: start, Values: map[string]any{"apps": 1.0}}, {Time: start + 20, NS: (start + 20) * 1000000, Session: start, Values: map[string]any{"apps": 2.0}}}
			query := request(start, "controls.apps")
			query.PageSize = 1
			first, err := engine.Query(context.Background(), query)
			if err != nil || first.Next == nil {
				t.Fatalf("first page: %+v %v", first, err)
			}
			query.PageToken = *first.Next
			second, err := engine.Query(context.Background(), query)
			if err != nil || len(second.Data) != 1 || second.Data[0].Value != 2.0 {
				t.Fatalf("unchanged page: %+v %v", second, err)
			}
			if historical {
				store.sessions[0].Revision++
			} else {
				engine.Cache.Ring.Add(0, 10, []LiveRow{{Pool: "controls_100hz", Row: Row{Time: start + 15, NS: (start + 15) * 1000000, Values: map[string]any{"apps": 1.5}}}})
			}
			_, err = engine.Query(context.Background(), query)
			if failure, ok := err.(*queryProblem); !ok || failure.Status != 409 || failure.Code != "query_changed" {
				t.Fatalf("changed query: %v", err)
			}
		})
	}
}

func TestKafkaBoundaryExcludesArrivalsAcrossPoolReads(t *testing.T) {
	engine, store, start := setup(t)
	ring := engine.Cache.Ring
	ring.Add(0, 10, []LiveRow{{Pool: "controls_100hz", Row: Row{Time: start + 10, NS: (start + 10) * 1000000, Values: map[string]any{"apps": 50.0}}}, {Pool: "pack_1hz", Row: Row{Time: start + 10, NS: (start + 10) * 1000000, Values: map[string]any{"temp": 60.0}}}})
	view := ring.View()
	controls, covered := view.Read(store.offsets, "controls_100hz", start, start+5000)
	if !covered || len(controls) != 1 {
		t.Fatalf("first pool: %+v covered=%v", controls, covered)
	}
	ring.Add(0, 11, []LiveRow{{Pool: "controls_100hz", Row: Row{Time: start + 20, NS: (start + 20) * 1000000, Values: map[string]any{"apps": 70.0}}}, {Pool: "pack_1hz", Row: Row{Time: start + 20, NS: (start + 20) * 1000000, Values: map[string]any{"temp": 80.0}}}})
	pack, covered := view.Read(store.offsets, "pack_1hz", start, start+5000)
	if !covered || len(pack) != 1 || pack[0].Values["temp"] != 60.0 {
		t.Fatalf("second pool: %+v covered=%v", pack, covered)
	}
	for _, pool := range []string{"controls_100hz", "pack_1hz"} {
		rows, covered := ring.View().Read(store.offsets, pool, start, start+5000)
		if !covered || len(rows) != 2 {
			t.Fatalf("next boundary %s: %+v covered=%v", pool, rows, covered)
		}
	}
	ring.Assign([]int32{0})
	if _, covered := view.Read(store.offsets, "controls_100hz", start, start+5000); covered {
		t.Fatal("reassigned ring accepted an old boundary")
	}
}

func TestFreshnessDistinguishesBacklogFromQuietSamples(t *testing.T) {
	engine, store, start := setup(t)
	ring := engine.Cache.Ring
	ring.Assign([]int32{0, 1})
	ring.Start(0, 10)
	ring.Observe(0, 12)
	ring.Ready(true)
	if ring.View().status.Ready {
		t.Fatal("uninitialized partition reported ready")
	}
	ring.Start(1, 20)
	ring.Observe(1, 20)
	ring.Ready(true)
	store.offsets[1] = 20
	query := request(start, "controls.apps")
	page, err := engine.Query(context.Background(), query)
	if err != nil || page.Live == nil || !page.Live.Ready || page.Live.KafkaLagMessages == nil || *page.Live.KafkaLagMessages != 2 {
		t.Fatalf("backlogged query: %+v %v", page, err)
	}
	ring.Add(0, 10, []LiveRow{{Pool: "controls_100hz", Row: Row{Time: start + 20, NS: (start + 20) * 1000000, Values: map[string]any{"apps": 50.0}}}})
	ring.Add(0, 11, nil)
	page, err = engine.Query(context.Background(), query)
	if err != nil || len(page.Data) != 2 || *page.Live.KafkaLagMessages != 0 || page.Live.LatestSampleMS == nil || *page.Live.LatestSampleMS != start+20 || page.Live.ObservedAtMS == nil {
		t.Fatalf("quiet caught-up query: %+v %v", page, err)
	}
}

func TestRingEvictionReportsGapUntilPersistenceRecovers(t *testing.T) {
	engine, store, start := setup(t)
	query := request(start, "controls.apps")
	if _, err := engine.Query(context.Background(), query); err != nil {
		t.Fatal(err)
	}
	engine.Cache.Ring.maxBytes = 1
	row := Row{Time: start + 20, NS: (start + 20) * 1000000, Session: start, Values: map[string]any{"apps": 50.0}}
	engine.Cache.Ring.Add(0, 10, []LiveRow{{Pool: "controls_100hz", Row: row}})
	_, err := engine.Query(context.Background(), query)
	if failure, ok := err.(*queryProblem); !ok || failure.Code != "tail_unavailable" {
		t.Fatalf("evicted pending sample: %v", err)
	}
	store.rows["controls_100hz"] = append(store.rows["controls_100hz"], row)
	store.offsets[0] = 11
	store.sessions[0].Revision++
	page, err := engine.Query(context.Background(), query)
	if err != nil || len(page.Data) != 2 {
		t.Fatalf("recovered persistence: %+v %v", page, err)
	}
}

func TestStreamBuildsCompleteWindowWithoutPublicPagination(t *testing.T) {
	engine, store, start := setup(t)
	store.rows["controls_100hz"] = nil
	for index := int64(0); index < 10001; index++ {
		stamp := start + index/10
		store.rows["controls_100hz"] = append(store.rows["controls_100hz"], Row{Time: stamp, NS: stamp*1000000 + index%10, Session: start, Values: map[string]any{"apps": float64(index)}})
	}
	page, err := engine.query(context.Background(), request(start, "controls.apps"), true)
	if err != nil {
		t.Fatal(err)
	}
	if page.Next != nil || len(page.Data) != 10001 {
		t.Fatalf("complete stream: count=%d next=%v", len(page.Data), page.Next)
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

func TestNonfiniteSamplesRemainQueryableAndRespectMemoryBudget(t *testing.T) {
	engine, store, start := setup(t)
	store.rows["controls_100hz"][0].Values["apps"] = math.NaN()
	store.rows["controls_100hz"][0].Values["cells"] = []any{math.Inf(1), math.Inf(-1)}
	query := request(start, "controls.apps", "controls.cells")
	for attempt := 0; attempt < 2; attempt++ {
		page, err := engine.Query(context.Background(), query)
		if err != nil || len(page.Data) != 2 {
			t.Fatalf("nonfinite query: %+v %v", page, err)
		}
		if page.Data[0].Value != "NaN" || page.Data[1].Value.([]any)[0] != "Infinity" || page.Data[1].Value.([]any)[1] != "-Infinity" {
			t.Fatalf("nonfinite values: %+v", page.Data)
		}
		if _, err := json.Marshal(page); err != nil {
			t.Fatal(err)
		}
	}
	if store.reads.Load() != 1 {
		t.Fatalf("baseline was not reused: reads=%d", store.reads.Load())
	}
	ring := NewRing(15*time.Minute, 4096)
	ring.Start(0, 0)
	ring.Ready(true)
	values := make([]any, 5000)
	values[0] = math.NaN()
	ring.Add(0, 0, []LiveRow{{Pool: "controls_100hz", Row: Row{Time: start, NS: start * 1000000, Values: map[string]any{"cells": values}}}})
	if ring.View().Covers(Offsets{0: 0}) {
		t.Fatal("oversized nonfinite array escaped the ring budget")
	}
}

func TestRejectedSealWaitsForCapacityOrNewRevision(t *testing.T) {
	engine, store, _ := setup(t)
	files := engine.Cache.Files
	files.Cap = 1
	files.sealIdle(context.Background(), store)
	reads := store.reads.Load()
	if reads == 0 {
		t.Fatal("initial seal did not read the source")
	}
	for attempt := 0; attempt < 2; attempt++ {
		files.Queue(store.sessions[0].ID)
		files.sealIdle(context.Background(), store)
		if store.reads.Load() != reads {
			t.Fatal("unchanged rejected session was rebuilt")
		}
	}
	paths, err := filepath.Glob(filepath.Join(files.Dir, "*.arrow"))
	if err != nil || len(paths) != 0 {
		t.Fatalf("disk cap exceeded: files=%v error=%v", paths, err)
	}
	store.sessions[0].Revision++
	files.Queue(store.sessions[0].ID)
	files.sealIdle(context.Background(), store)
	if store.reads.Load() == reads {
		t.Fatal("new revision was not reconsidered")
	}
	files.Cap = 64 << 20
	files.Queue(store.sessions[0].ID)
	files.sealIdle(context.Background(), store)
	if !files.Available(engine.Schema.Pools[0], store.sessions[0]) {
		t.Fatal("session was not sealed after capacity became available")
	}
}

type unavailableStore struct{ Store }

func (unavailableStore) Snapshot(context.Context, int64, int64) (Snapshot, error) {
	return nil, fmt.Errorf("connection to private-db.internal failed: secret SQL details")
}

func TestStreamMasksSourceErrors(t *testing.T) {
	engine, store, start := setup(t)
	engine.Store = unavailableStore{store}
	server := httptest.NewServer(engine.Handler("test-token"))
	defer server.Close()
	query, _ := json.Marshal(request(start, "controls.apps"))
	request, err := http.NewRequest("GET", server.URL+"/v1/stream?query="+url.QueryEscape(string(query)), nil)
	if err != nil {
		t.Fatal(err)
	}
	request.Header.Set("Authorization", "Bearer test-token")
	client := &http.Client{Timeout: 5 * time.Second}
	response, err := client.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	line, err := bufio.NewReader(response.Body).ReadString('\n')
	if err != nil {
		t.Fatal(err)
	}
	var event struct {
		Type, Code, Message string
	}
	if err := json.Unmarshal([]byte(strings.TrimPrefix(line, "data: ")), &event); err != nil {
		t.Fatal(err)
	}
	if event.Type != "gap" || event.Code != "source_unavailable" || event.Message != "Telemetry source is unavailable" {
		t.Fatalf("source error escaped masking: %s", line)
	}
}
