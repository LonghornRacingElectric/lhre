package gateway

import (
	"context"
	"net/http"
	"strconv"
	"time"

	sq "github.com/Masterminds/squirrel"
)

func (p *Postgres) CarStatus(ctx context.Context, from, to int64, limit uint64) ([]map[string]any, error) {
	q := selectSQL.Select("segment_id", "car", "state", "start_time", "end_time", "hv_soc_avg", "lv_v_avg", "active_faults").From("public.car_status_segment").Where(sq.Eq{"car": "artemis"}).Where(sq.LtOrEq{"start_time": to}).Where(sq.Or{sq.Eq{"end_time": nil}, sq.GtOrEq{"end_time": from}}).OrderBy("start_time").Limit(limit)
	query, args, err := q.ToSql()
	if err != nil {
		return nil, err
	}
	rows, err := p.DB.Query(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []map[string]any{}
	for rows.Next() {
		values, err := rows.Values()
		if err != nil {
			return nil, err
		}
		row := map[string]any{}
		for i, col := range rows.FieldDescriptions() {
			row[col.Name] = normalize(values[i])
		}
		out = append(out, row)
	}
	return out, rows.Err()
}
func (e *Engine) statusHTTP(w http.ResponseWriter, r *http.Request) {
	from, errFrom := strconv.ParseInt(r.URL.Query().Get("from"), 10, 64)
	to, errTo := strconv.ParseInt(r.URL.Query().Get("to"), 10, 64)
	limit, errLimit := strconv.ParseUint(r.URL.Query().Get("limit"), 10, 64)
	if errFrom != nil || errTo != nil || from <= 0 || to < from {
		writeError(w, problem(400, "invalid_request", "Use valid millisecond from/to bounds"))
		return
	}
	if errLimit != nil || limit == 0 {
		limit = 2000
	}
	limit = min(limit, 10000)
	store, ok := e.Store.(interface {
		CarStatus(context.Context, int64, int64, uint64) ([]map[string]any, error)
	})
	if !ok {
		writeError(w, problem(503, "source_unavailable", "Status history is unavailable"))
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
	defer cancel()
	rows, err := store.CarStatus(ctx, from, to, limit)
	if err != nil {
		writeError(w, err)
		return
	}
	writeJSON(w, map[string]any{"data": rows})
}
