package gateway

import (
	"context"
	"fmt"
	"sort"
	"strconv"
	"time"
)

func overviewPool(p Pool) Pool {
	out := Pool{Table: p.Table + "_overview", Group: p.Group, Frequency: 1}
	for _, f := range p.Fields {
		out.Fields = append(out.Fields, Field{Name: f.Name + "_count", SQLType: "bigint"}, Field{Name: f.Name + "_last_ns", SQLType: "bigint"}, Field{Name: f.Name + "_last", SQLType: f.SQLType})
		if numeric(f) {
			out.Fields = append(out.Fields, Field{Name: f.Name + "_sum", SQLType: "double precision"}, Field{Name: f.Name + "_min", SQLType: f.SQLType}, Field{Name: f.Name + "_max", SQLType: f.SQLType})
		}
	}
	return out
}
func summarize(p Pool, rows []Row) ([]Row, error) {
	type entry struct {
		row    Row
		fields map[string]*accumulator
	}
	buckets := map[string]*entry{}
	for _, row := range rows {
		stamp := row.Time / 1000 * 1000
		key := fmt.Sprintf("%d/%d", stamp, row.Session)
		e := buckets[key]
		if e == nil {
			e = &entry{Row{Time: stamp, NS: stamp * 1000000, Session: row.Session, Values: map[string]any{}}, map[string]*accumulator{}}
			buckets[key] = e
		}
		for _, f := range p.Fields {
			v, ok := row.Values[f.Name]
			if !ok || v == nil {
				continue
			}
			a := e.fields[f.Name]
			if a == nil {
				agg := "last"
				if numeric(f) {
					agg = "mean"
				}
				a = &accumulator{agg: agg}
				e.fields[f.Name] = a
			}
			if err := a.add(row.NS, v); err != nil {
				return nil, err
			}
		}
	}
	result := []Row{}
	for _, e := range buckets {
		for name, a := range e.fields {
			e.row.Values[name+"_count"] = strconv.FormatInt(a.count, 10)
			e.row.Values[name+"_last_ns"] = strconv.FormatInt(a.ns, 10)
			e.row.Values[name+"_last"] = a.last
			if a.agg != "last" {
				e.row.Values[name+"_sum"] = a.sum
				e.row.Values[name+"_min"] = a.minimum
				e.row.Values[name+"_max"] = a.maximum
			}
		}
		result = append(result, e.row)
	}
	sort.Slice(result, func(i, j int) bool { return result[i].Time < result[j].Time })
	return result, nil
}
func (a *accumulator) addSummary(name string, row Row) error {
	v, ok := row.Values[name+"_count"]
	if !ok {
		return nil
	}
	count, err := strconv.ParseInt(fmt.Sprint(v), 10, 64)
	if err != nil {
		return err
	}
	ns, err := strconv.ParseInt(fmt.Sprint(row.Values[name+"_last_ns"]), 10, 64)
	if err != nil {
		return err
	}
	if a.count == 0 || ns >= a.ns {
		a.last = row.Values[name+"_last"]
		a.ns = ns
	}
	if a.agg != "last" {
		sum, err := number(row.Values[name+"_sum"])
		if err != nil {
			return err
		}
		a.sum += sum
		minimum, maximum := row.Values[name+"_min"], row.Values[name+"_max"]
		if a.count == 0 || compareNumbers(minimum, a.minimum) < 0 {
			a.minimum = minimum
		}
		if a.count == 0 || compareNumbers(maximum, a.maximum) > 0 {
			a.maximum = maximum
		}
	}
	a.count += count
	return nil
}
func (c *Cache) Overview(ctx context.Context, snap Snapshot, p Pool, start, end int64) ([]Row, bool, error) {
	if len(snap.Sessions()) == 0 {
		return nil, false, nil
	}
	overview := overviewPool(p)
	result := []Row{}
	for _, session := range snap.Sessions() {
		if session.Start >= end || session.End < start {
			continue
		}
		key := fmt.Sprintf("%s/%s/%d/%d/%d", c.Schema.StorageID, overview.Table, session.ID, session.Revision, start)
		if entry, ok := c.hot.Get(key); ok {
			rows, err := decodeSlab(overview, entry.Data)
			if err == nil {
				result = append(result, rows...)
				continue
			}
		}
		rows, found, err := c.Files.Read(overview, session, start, end)
		if err != nil || !found {
			return nil, false, nil
		}
		encoded, err := encodeSlab(overview, rows)
		if err != nil {
			return nil, false, err
		}
		c.admit(key, &cachedSlab{Data: encoded}, start)
		result = append(result, rows...)
	}
	// A sealed session can still have a pending late update. Do not summarize
	// across that boundary until it is persisted and the revision is resealed.
	tail, covered := c.Ring.Read(snap.Offsets(), p.Table, start, end)
	if !covered && end > time.Now().Add(-c.Ring.window).UnixMilli() || len(tail) > 0 {
		return nil, false, nil
	}
	c.Reads[2].Add(1)
	return result, true, nil
}
