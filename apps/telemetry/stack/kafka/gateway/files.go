package gateway

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"time"

	"github.com/apache/arrow-go/v18/arrow/ipc"
	"golang.org/x/sys/unix"
)

type BatchIndex struct {
	Index int   `json:"batch_idx"`
	Start int64 `json:"ts_min"`
	End   int64 `json:"ts_max"`
}
type Manifest struct {
	Storage  string       `json:"storage_id"`
	Session  int64        `json:"session_start_ms"`
	Revision int64        `json:"data_revision"`
	Pool     string       `json:"pool"`
	Rows     int64        `json:"source_row_count"`
	Min      int64        `json:"min_ts"`
	Max      int64        `json:"max_ts"`
	Offsets  Offsets      `json:"watermark"`
	Checksum string       `json:"sha256"`
	Batches  []BatchIndex `json:"batches"`
}
type Files struct {
	Dir       string
	Cap       int64
	Schema    *Schema
	mu        sync.Mutex
	queue     map[int64]bool
	frequency map[string]float64
	verified  map[string]string
	seen      map[int64]int64
}

func NewFiles(dir string, capacity int64, schema *Schema) *Files {
	return &Files{Dir: dir, Cap: capacity, Schema: schema, queue: map[int64]bool{}, frequency: map[string]float64{}, verified: map[string]string{}, seen: map[int64]int64{}}
}
func (f *Files) Queue(id int64) {
	f.mu.Lock()
	if len(f.queue) < 10000 {
		f.queue[id] = true
	}
	f.mu.Unlock()
}
func (f *Files) name(p Pool, s Session) string {
	return filepath.Join(f.Dir, fmt.Sprintf("%s_%d_%d_%s", f.Schema.StorageID, s.ID, s.Revision, p.Table))
}
func (f *Files) Read(p Pool, s Session, start, end int64) ([]Row, bool, error) {
	if f.Dir == "" || f.Cap <= 0 {
		return nil, false, nil
	}
	name := f.name(p, s)
	data, err := os.ReadFile(name + ".json")
	if os.IsNotExist(err) {
		return nil, false, nil
	}
	if err != nil {
		return nil, false, err
	}
	var manifest Manifest
	if err = json.Unmarshal(data, &manifest); err != nil {
		return nil, false, err
	}
	if manifest.Storage != f.Schema.StorageID || manifest.Session != s.ID || manifest.Revision != s.Revision || manifest.Pool != p.Table {
		return nil, false, fmt.Errorf("stale manifest")
	}
	file, err := os.Open(name + ".arrow")
	if err != nil {
		return nil, false, err
	}
	defer file.Close()
	stat, err := file.Stat()
	if err != nil {
		return nil, false, err
	}
	if stat.Size() <= 0 {
		return nil, false, fmt.Errorf("empty Arrow file")
	}
	identity := fmt.Sprintf("%s/%d/%d", manifest.Checksum, stat.Size(), stat.ModTime().UnixNano())
	f.mu.Lock()
	verified := f.verified[name] == identity
	f.mu.Unlock()
	if !verified {
		hash := sha256.New()
		if _, err = io.Copy(hash, file); err != nil {
			return nil, false, err
		}
		if hex.EncodeToString(hash.Sum(nil)) != manifest.Checksum {
			return nil, false, fmt.Errorf("Arrow checksum mismatch")
		}
		f.mu.Lock()
		f.verified[name] = identity
		f.mu.Unlock()
	}
	mapped, err := unix.Mmap(int(file.Fd()), 0, int(stat.Size()), unix.PROT_READ, unix.MAP_SHARED)
	if err != nil {
		return nil, false, err
	}
	defer unix.Munmap(mapped)
	r, err := ipc.NewMappedFileReader(mapped)
	if err != nil {
		return nil, false, err
	}
	defer r.Close()
	result := []Row{}
	first := sort.Search(len(manifest.Batches), func(i int) bool { return manifest.Batches[i].End >= start })
	for _, index := range manifest.Batches[first:] {
		if index.Start >= end {
			break
		}
		batch, err := r.RecordBatchAt(index.Index)
		if err != nil {
			return nil, false, err
		}
		rows, err := batchRows(p, batch)
		batch.Release()
		if err != nil {
			return nil, false, err
		}
		for _, row := range rows {
			if row.Time >= start && row.Time < end {
				result = append(result, row)
			}
		}
	}
	f.mu.Lock()
	f.frequency[name]++
	f.mu.Unlock()
	return result, true, nil
}

func atomicJSON(path string, value any) error {
	data, err := json.Marshal(value)
	if err != nil {
		return err
	}
	file, err := os.CreateTemp(filepath.Dir(path), ".manifest-")
	if err != nil {
		return err
	}
	defer os.Remove(file.Name())
	if _, err = file.Write(data); err == nil {
		err = file.Sync()
	}
	if closeErr := file.Close(); err == nil {
		err = closeErr
	}
	if err != nil {
		return err
	}
	if err = os.Rename(file.Name(), path); err != nil {
		return err
	}
	dir, err := os.Open(filepath.Dir(path))
	if err != nil {
		return err
	}
	defer dir.Close()
	return dir.Sync()
}

func (f *Files) Seal(ctx context.Context, store Store, s Session) error {
	if f.Dir == "" || f.Cap <= 0 {
		return nil
	}
	if err := os.MkdirAll(f.Dir, 0750); err != nil {
		return err
	}
	snap, err := store.Snapshot(ctx, s.Start, s.End+1)
	if err != nil {
		return err
	}
	defer snap.Close(ctx)
	matched := false
	for _, current := range snap.Sessions() {
		if current.ID == s.ID && current.Revision == s.Revision {
			matched = true
		}
	}
	if !matched {
		return nil
	}
	for _, nativePool := range f.Schema.Pools {
		for _, overview := range []bool{false, true} {
			p := nativePool
			if overview {
				p = overviewPool(p)
			}
			name := f.name(p, s)
			if _, err := os.Stat(name + ".json"); err == nil {
				if _, found, err := f.Read(p, s, s.Start, min(s.End+1, s.Start+1)); found && err == nil {
					continue
				}
			}
			file, err := os.CreateTemp(f.Dir, ".seal-")
			if err != nil {
				return err
			}
			manifest := Manifest{Storage: f.Schema.StorageID, Session: s.ID, Revision: s.Revision, Pool: p.Table, Offsets: snap.Offsets()}
			writeErr := func() error {
				writer, err := ipc.NewFileWriter(file, ipc.WithSchema(arrowSchema(p)))
				if err != nil {
					return err
				}
				for start := s.Start / slabMS * slabMS; start <= s.End; start += slabMS {
					rows, err := snap.Read(ctx, nativePool, start, start+slabMS, &s.ID)
					if err == nil && overview {
						rows, err = summarize(nativePool, rows)
					}
					if err != nil {
						_ = writer.Close()
						return err
					}
					if len(rows) == 0 {
						continue
					}
					batch, err := recordBatch(p, rows)
					if err != nil {
						_ = writer.Close()
						return err
					}
					err = writer.Write(batch)
					batch.Release()
					if err != nil {
						_ = writer.Close()
						return err
					}
					if manifest.Rows == 0 {
						manifest.Min = rows[0].Time
					}
					manifest.Max = rows[len(rows)-1].Time
					manifest.Rows += int64(len(rows))
					manifest.Batches = append(manifest.Batches, BatchIndex{len(manifest.Batches), rows[0].Time, rows[len(rows)-1].Time})
				}
				if err = writer.Close(); err != nil {
					return err
				}
				return file.Sync()
			}()
			closeErr := file.Close()
			if writeErr == nil {
				writeErr = closeErr
			}
			if writeErr != nil {
				_ = os.Remove(file.Name())
				return writeErr
			}
			// Do not publish a snapshot that changed while its files were written.
			current, err := store.Snapshot(ctx, s.Start, s.End+1)
			if err != nil {
				_ = os.Remove(file.Name())
				return err
			}
			valid := false
			for _, session := range current.Sessions() {
				if session.ID == s.ID && session.Revision == s.Revision {
					valid = true
				}
			}
			current.Close(ctx)
			if !valid {
				_ = os.Remove(file.Name())
				return nil
			}
			input, err := os.Open(file.Name())
			if err != nil {
				return err
			}
			hash := sha256.New()
			_, err = io.Copy(hash, input)
			_ = input.Close()
			if err != nil {
				return err
			}
			manifest.Checksum = hex.EncodeToString(hash.Sum(nil))
			if err = os.Rename(file.Name(), name+".arrow"); err != nil {
				return err
			}
			if err = atomicJSON(name+".json", manifest); err != nil {
				return err
			}
		}
	}
	return f.evict()
}

func (f *Files) evict() error {
	entries, err := filepath.Glob(filepath.Join(f.Dir, "*.arrow"))
	if err != nil {
		return err
	}
	type candidate struct {
		name  string
		bytes int64
		score float64
	}
	items := []candidate{}
	var total int64
	f.mu.Lock()
	defer f.mu.Unlock()
	for _, path := range entries {
		stat, err := os.Stat(path)
		if err != nil {
			continue
		}
		name := path[:len(path)-6]
		size := stat.Size()
		total += size
		score := f.frequency[name] / float64(max(size, 1))
		items = append(items, candidate{name, size, score})
		f.frequency[name] *= .5
	}
	sort.Slice(items, func(i, j int) bool {
		if items[i].score == items[j].score {
			return items[i].name < items[j].name
		}
		return items[i].score < items[j].score
	})
	for _, item := range items {
		if total <= f.Cap {
			break
		}
		if err = os.Remove(item.name + ".json"); err != nil && !os.IsNotExist(err) {
			return err
		}
		if err = os.Remove(item.name + ".arrow"); err != nil && !os.IsNotExist(err) {
			return err
		}
		delete(f.verified, item.name)
		delete(f.frequency, item.name)
		total -= item.bytes
	}
	return nil
}

// The single worker also discovers old sessions after all derived files vanish.
func (f *Files) Run(ctx context.Context, store Store, heads func(context.Context) (Offsets, error)) {
	tick := time.NewTicker(30 * time.Second)
	defer tick.Stop()
	for {
		if f.Dir != "" && f.Cap > 0 {
			func() {
				boundary, err := heads(ctx)
				if err != nil {
					return
				}
				snap, err := store.Snapshot(ctx, 0, time.Now().UnixMilli()+1)
				if err != nil {
					return
				}
				sessions := snap.Sessions()
				progress := snap.Offsets()
				snap.Close(ctx)
				for part, end := range boundary {
					if progress[part] < end {
						return
					}
				}
				for _, s := range sessions {
					if time.Now().UnixMilli()-s.Written < 300000 {
						continue
					}
					f.mu.Lock()
					queued := f.queue[s.ID]
					revision, seen := f.seen[s.ID]
					f.mu.Unlock()
					if !queued && seen && revision == s.Revision {
						continue
					}
					// Eviction must not immediately cause an eager rebuild loop.
					var used int64
					paths, _ := filepath.Glob(filepath.Join(f.Dir, "*.arrow"))
					for _, path := range paths {
						if stat, err := os.Stat(path); err == nil {
							used += stat.Size()
						}
					}
					if !queued && used >= f.Cap {
						f.mu.Lock()
						f.seen[s.ID] = s.Revision
						f.mu.Unlock()
						continue
					}
					if err := f.Seal(ctx, store, s); err != nil {
						log.Printf("query seal session %d: %v", s.ID, err)
					}
					f.mu.Lock()
					delete(f.queue, s.ID)
					f.seen[s.ID] = s.Revision
					f.mu.Unlock()
					if ctx.Err() != nil {
						return
					}
				}
			}()
		}
		select {
		case <-ctx.Done():
			return
		case <-tick.C:
		}
	}
}
