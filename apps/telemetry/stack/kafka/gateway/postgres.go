package gateway

import (
	"context"
	"encoding/base64"
	"fmt"
	"strconv"
	"sync"
	"time"

	sq "github.com/Masterminds/squirrel"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgtype"
	"github.com/jackc/pgx/v5/pgxpool"
)

type Row struct {
	Time    int64
	NS      int64
	Session int64
	Values  map[string]any
}
type Session struct {
	ID       int64 `json:"session_start_ms,string"`
	Start    int64 `json:"start_time"`
	End      int64 `json:"end_time"`
	Revision int64 `json:"data_revision"`
	External int64 `json:"external_revision"`
	Written  int64 `json:"last_write_ms"`
}
type Offsets map[int32]int64
type Snapshot interface {
	Sessions() []Session
	Offsets() Offsets
	Read(context.Context, Pool, int64, int64, *int64) ([]Row, error)
	Close(context.Context)
}
type Store interface {
	Snapshot(context.Context, int64, int64) (Snapshot, error)
	Day(context.Context, int64) (int64, int64, error)
}
type Postgres struct {
	DB, Metadata *pgxpool.Pool
	Topic        string
	StorageID    string
}

var selectSQL = sq.StatementBuilder.PlaceholderFormat(sq.Dollar)

func OpenDB(ctx context.Context, url string) (*pgxpool.Pool, error) {
	cfg, err := pgxpool.ParseConfig(url)
	if err != nil {
		return nil, err
	}
	cfg.MaxConns = 4
	cfg.ConnConfig.RuntimeParams["default_transaction_read_only"] = "on"
	cfg.ConnConfig.RuntimeParams["statement_timeout"] = "30000"
	return pgxpool.NewWithConfig(ctx, cfg)
}

type postgresSnapshot struct {
	tx         pgx.Tx
	db         *pgxpool.Pool
	mu         sync.Mutex
	exported   string
	exportOnce sync.Once
	exportErr  error
	sessions   []Session
	offsets    Offsets
}

func (s *postgresSnapshot) Sessions() []Session       { return s.sessions }
func (s *postgresSnapshot) Offsets() Offsets          { return s.offsets }
func (s *postgresSnapshot) Close(ctx context.Context) { _ = s.tx.Rollback(ctx) }

func (p *Postgres) Snapshot(ctx context.Context, from, to int64) (Snapshot, error) {
	tx, err := p.DB.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		return nil, err
	}
	s := &postgresSnapshot{tx: tx, db: p.DB, offsets: Offsets{}}
	fail := func(err error) (Snapshot, error) { s.Close(ctx); return nil, err }
	query, args, _ := selectSQL.Select("storage_id").From("public.storage_migration").Where(sq.Eq{"storage_id": p.StorageID}).ToSql()
	var id string
	if err := tx.QueryRow(ctx, query, args...).Scan(&id); err != nil {
		return fail(problem(503, "schema_unavailable", "Apply the generated Artemis storage schema before querying"))
	}
	query, args, _ = selectSQL.Select("session_start_ms", "start_time", "end_time", "data_revision", "external_revision", "last_write_ms").From("public.recording_session").Where(sq.Lt{"start_time": to}).Where(sq.GtOrEq{"end_time": from}).OrderBy("session_start_ms").ToSql()
	rows, err := tx.Query(ctx, query, args...)
	if err != nil {
		return fail(err)
	}
	for rows.Next() {
		var r Session
		if err := rows.Scan(&r.ID, &r.Start, &r.End, &r.Revision, &r.External, &r.Written); err != nil {
			rows.Close()
			return fail(err)
		}
		s.sessions = append(s.sessions, r)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return fail(err)
	}
	query, args, _ = selectSQL.Select("partition", "next_offset").From("public.ingest_progress").Where(sq.Eq{"topic": p.Topic}).ToSql()
	rows, err = tx.Query(ctx, query, args...)
	if err != nil {
		return fail(err)
	}
	for rows.Next() {
		var part int32
		var off int64
		if err := rows.Scan(&part, &off); err != nil {
			rows.Close()
			return fail(err)
		}
		s.offsets[part] = off
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return fail(err)
	}
	return s, nil
}

// Spare connections import the same MVCC snapshot. If the pool is busy, use
// the parent instead of reserving a connection while waiting for another one.
func (s *postgresSnapshot) reader(ctx context.Context) (pgx.Tx, func(), error) {
	probe, cancel := context.WithTimeout(ctx, 5*time.Millisecond)
	conn, err := s.db.Acquire(probe)
	cancel()
	if err != nil {
		if ctx.Err() != nil {
			return nil, nil, ctx.Err()
		}
		s.mu.Lock()
		return s.tx, s.mu.Unlock, nil
	}
	s.exportOnce.Do(func() {
		s.mu.Lock()
		defer s.mu.Unlock()
		q, args, _ := selectSQL.Select("pg_export_snapshot()").ToSql()
		s.exportErr = s.tx.QueryRow(ctx, q, args...).Scan(&s.exported)
	})
	id := s.exported
	if s.exportErr != nil {
		conn.Release()
		return nil, nil, s.exportErr
	}
	tx, err := conn.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		conn.Release()
		return nil, nil, err
	}
	close := func() { _ = tx.Rollback(context.Background()); conn.Release() }
	// This transaction command cannot bind parameters. The literal is an ID
	// returned by Postgres, restricted to its hex-and-hyphen representation.
	for _, ch := range id {
		if !(ch >= '0' && ch <= '9' || ch >= 'A' && ch <= 'F' || ch >= 'a' && ch <= 'f' || ch == '-') {
			close()
			return nil, nil, fmt.Errorf("invalid exported snapshot ID")
		}
	}
	if _, err = tx.Exec(ctx, "SET TRANSACTION SNAPSHOT '"+id+"'"); err != nil {
		close()
		return nil, nil, err
	}
	return tx, close, nil
}

func (s *postgresSnapshot) Read(ctx context.Context, p Pool, from, to int64, session *int64) ([]Row, error) {
	columns := []string{`"time"`, "sample_time_ns", "session_start_ms"}
	for _, f := range p.Fields {
		columns = append(columns, `"`+f.Name+`"`)
	}
	q := selectSQL.Select(columns...).From("public."+p.Table).Where(sq.GtOrEq{`"time"`: from}).Where(sq.Lt{`"time"`: to}).OrderBy(`"time"`, "session_start_ms", "sample_time_ns").Limit(100001)
	if session != nil {
		q = q.Where(sq.Eq{"session_start_ms": *session})
	}
	query, args, err := q.ToSql()
	if err != nil {
		return nil, err
	}
	tx, close, err := s.reader(ctx)
	if err != nil {
		return nil, err
	}
	defer close()
	rows, err := tx.Query(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := []Row{}
	for rows.Next() {
		v, err := rows.Values()
		if err != nil {
			return nil, err
		}
		r := Row{Time: v[0].(int64), NS: v[1].(int64), Session: v[2].(int64), Values: map[string]any{}}
		for i, f := range p.Fields {
			if v[i+3] != nil {
				r.Values[f.Name] = normalize(v[i+3])
			}
		}
		result = append(result, r)
		if len(result) > 100000 {
			return nil, problem(413, "query_scan_limit", "Pool slab exceeds 100000 rows; narrow the range")
		}
	}
	return result, rows.Err()
}

func (s *postgresSnapshot) Window(ctx context.Context, p Pool, from, to, width int64, aggregate string, after *Position, limit int) ([]Row, error) {
	q := selectSQL.Select("session_start_ms").From("public." + p.Table).
		Where(sq.GtOrEq{`"time"`: from}).Where(sq.Lt{`"time"`: to})
	if aggregate == "raw" {
		q = q.Column(`"time"`).Column("sample_time_ns")
		if after != nil {
			q = q.Where(sq.Expr(`("time", session_start_ms, sample_time_ns) >= (?, ?, ?)`, after.Time, after.Session, after.NS))
		}
	} else {
		q = q.Column(sq.Expr(`time_bucket(?::bigint, "time") AS "time"`, width)).Column("max(sample_time_ns) AS sample_time_ns").GroupBy("session_start_ms", "2")
	}
	present := sq.Or{}
	for _, f := range p.Fields {
		col := `"` + f.Name + `"`
		present = append(present, sq.NotEq{col: nil})
		var expression string
		switch aggregate {
		case "raw":
			expression = col
		case "last":
			expression = "last(" + col + ", sample_time_ns) FILTER (WHERE " + col + " IS NOT NULL)"
		case "mean":
			expression = "avg(" + col + ")"
		case "min", "max":
			expression = aggregate + "(" + col + ")"
		}
		q = q.Column(expression + " AS " + col)
	}
	q = q.Where(present).OrderBy(`"time"`, "session_start_ms", "sample_time_ns").Limit(uint64(limit))
	query, args, err := q.ToSql()
	if err != nil {
		return nil, err
	}
	tx, close, err := s.reader(ctx)
	if err != nil {
		return nil, err
	}
	defer close()
	rows, err := tx.Query(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := []Row{}
	for rows.Next() {
		v, err := rows.Values()
		if err != nil {
			return nil, err
		}
		r := Row{Session: v[0].(int64), Time: v[1].(int64), NS: v[2].(int64), Values: map[string]any{}}
		if aggregate != "raw" {
			r.NS = r.Time * 1000000
		}
		for i, f := range p.Fields {
			if v[i+3] != nil {
				r.Values[f.Name] = normalize(v[i+3])
			}
		}
		result = append(result, r)
	}
	return result, rows.Err()
}

func normalize(v any) any {
	switch v := v.(type) {
	case float32:
		return float64(v)
	case []byte:
		return base64.StdEncoding.EncodeToString(v)
	case int64:
		return strconv.FormatInt(v, 10)
	case pgtype.Numeric:
		value, _ := v.Value()
		return value
	case []any:
		out := make([]any, len(v))
		for i, x := range v {
			out[i] = normalize(x)
		}
		return out
	default:
		return v
	}
}

func (p *Postgres) Day(ctx context.Context, id int64) (int64, int64, error) {
	if p.Metadata == nil {
		return 0, 0, problem(503, "metadata_unavailable", "TELEMETRY_DATABASE_URL is required for drive_day selection")
	}
	q, a, _ := selectSQL.Select("start_time", "end_time").From("drive_day").Where(sq.Eq{"day_id": id}).ToSql()
	var start, end *int64
	if err := p.Metadata.QueryRow(ctx, q, a...).Scan(&start, &end); err != nil {
		return 0, 0, problem(404, "unknown_drive_day", fmt.Sprint(id))
	}
	if start == nil {
		return 0, 0, problem(400, "invalid_drive_day", "Drive day has no start time")
	}
	to := time.Now().UnixMilli()
	if end != nil {
		to = *end
	}
	return *start, to, nil
}
