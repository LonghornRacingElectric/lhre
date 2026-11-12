package gateway

import (
	"context"
	"fmt"
	"strconv"
	"strings"
	"time"

	sq "github.com/Masterminds/squirrel"
)

type aggregateSnapshot interface {
	Aggregate(context.Context, Pool, int64, int64, int64) ([]Row, error)
	Unstored(context.Context, Pool, []Row) ([]Row, error)
}

// The SQL builder selects only contract-validated identifiers. Aggregation stays
// in Timescale on a cold wide-range miss rather than shipping millions of rows.
func (s *postgresSnapshot) Aggregate(ctx context.Context, p Pool, from, to, width int64) ([]Row, error) {
	q := selectSQL.Select("session_start_ms").Column(sq.Expr(`time_bucket(?::bigint, "time") AS "time"`, width)).Column("max(sample_time_ns) AS sample_time_ns").From("public."+p.Table).Where(sq.GtOrEq{`"time"`: from}).Where(sq.Lt{`"time"`: to}).GroupBy("session_start_ms", "2").OrderBy("2", "1").Limit(20001)
	for _, f := range p.Fields {
		col := `"` + f.Name + `"`
		last := `last(` + col + `, sample_time_ns) FILTER (WHERE ` + col + ` IS NOT NULL)`
		ns := `max(sample_time_ns) FILTER (WHERE ` + col + ` IS NOT NULL)`
		q = q.Column(`count(` + col + `) AS "` + f.Name + `_count"`).Column(ns + ` AS "` + f.Name + `_last_ns"`).Column(last + ` AS "` + f.Name + `_last"`)
		if numeric(f) {
			q = q.Column(`sum(` + col + `) AS "` + f.Name + `_sum"`).Column(`min(` + col + `) AS "` + f.Name + `_min"`).Column(`max(` + col + `) AS "` + f.Name + `_max"`)
		}
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
	out := []Row{}
	for rows.Next() {
		values, err := rows.Values()
		if err != nil {
			return nil, err
		}
		r := Row{Session: values[0].(int64), Time: values[1].(int64), NS: values[2].(int64), Values: map[string]any{}}
		for i, field := range rows.FieldDescriptions()[3:] {
			if values[i+3] != nil {
				r.Values[field.Name] = normalize(values[i+3])
			}
		}
		out = append(out, r)
		if len(out) > 20000 {
			return nil, problem(413, "query_scan_limit", "Too many session buckets; narrow the range")
		}
	}
	return out, rows.Err()
}
func (s *postgresSnapshot) Unstored(ctx context.Context, p Pool, tail []Row) ([]Row, error) {
	tail = mergeRows(nil, tail, nil, p.Fields)
	if len(tail) == 0 {
		return tail, nil
	}
	times, nanos := []int64{}, []int64{}
	for _, row := range tail {
		times = append(times, row.Time)
		nanos = append(nanos, row.NS)
	}
	columns := []string{`"time"`, "sample_time_ns"}
	for _, f := range p.Fields {
		columns = append(columns, `"`+f.Name+`"`)
	}
	q := selectSQL.Select(columns...).From("public." + p.Table).Where(sq.GtOrEq{`"time"`: tail[0].Time}).Where(sq.LtOrEq{`"time"`: tail[len(tail)-1].Time}).Where(sq.Expr(`("time", sample_time_ns) IN (SELECT * FROM unnest(?::bigint[], ?::bigint[]))`, times, nanos))
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
	present := map[string]map[string]bool{}
	for rows.Next() {
		values, err := rows.Values()
		if err != nil {
			return nil, err
		}
		key := fmt.Sprintf("%d/%d", values[0], values[1])
		fields := map[string]bool{}
		for i, f := range p.Fields {
			fields[f.Name] = values[i+2] != nil
		}
		present[key] = fields
	}
	if err = rows.Err(); err != nil {
		return nil, err
	}
	out := []Row{}
	for _, row := range tail {
		values := map[string]any{}
		for name, v := range row.Values {
			if !present[fmt.Sprintf("%d/%d", row.Time, row.NS)][name] {
				values[name] = v
			}
		}
		row.Values = values
		if len(values) > 0 {
			out = append(out, row)
		}
	}
	return out, nil
}

func (c *Cache) aggregate(ctx context.Context, snap Snapshot, p Pool, from, to, width int64) ([]Row, []Row, string, error) {
	reader, ok := snap.(aggregateSnapshot)
	if !ok {
		return nil, nil, "", fmt.Errorf("aggregate source unavailable")
	}
	key := fmt.Sprintf("aggregate/%s/%s/%v/%d/%d/%d/%v", c.Schema.StorageID, p.Table, p.Fields, from, to, width, snap.Sessions())
	var rows []Row
	source := "L1"
	if entry, ok := c.hot.Get(key); ok {
		rows = entry.Rows
	}
	if rows == nil {
		source = "postgres"
		flightKey := fmt.Sprintf("%s/%v", key, snap.Offsets())
		value, err := c.sharedRead(ctx, flightKey, func(work context.Context) (any, error) { return reader.Aggregate(work, p, from, to, width) })
		if err != nil {
			return nil, nil, "", err
		}
		rows = value.([]Row)
		c.Reads[3].Add(1)
		c.admit(key, &cachedSlab{Rows: rows}, from)
	} else {
		c.Reads[1].Add(1)
	}
	tail, covered := c.Ring.Read(snap.Offsets(), p.Table, from, to)
	if !covered && to > time.Now().Add(-c.Ring.window).UnixMilli() {
		return nil, nil, "", problem(503, "tail_unavailable", "Kafka coverage is incomplete")
	}
	tail, err := reader.Unstored(ctx, p, tail)
	if err != nil {
		return nil, nil, "", err
	}
	return rows, mergeRows(nil, tail, snap.Sessions(), p.Fields), source, nil
}

func addAggregateRows(aggregates map[string]*accumulator, rows []Row, signals []Signal, agg string, width int64, summary bool) error {
	for _, row := range rows {
		for _, signal := range signals {
			name := strings.SplitN(signal.Name, ".", 2)[1]
			field := name
			if summary {
				field += "_last"
			}
			value, ok := row.Values[field]
			if !ok || value == nil {
				continue
			}
			stamp := row.Time / width * width
			key := fmt.Sprintf("%d/%d/%s", stamp, row.Session, signal.Name)
			a := aggregates[key]
			if a == nil {
				a = &accumulator{stamp: stamp, session: row.Session, signal: signal.Name, agg: agg}
				aggregates[key] = a
			}
			var err error
			if summary {
				count, _ := strconv.ParseInt(fmt.Sprint(row.Values[name+"_count"]), 10, 64)
				if count == 0 {
					continue
				}
				err = a.addSummary(name, row)
			} else {
				err = a.add(row.NS, value)
			}
			if err != nil {
				return err
			}
		}
	}
	return nil
}
