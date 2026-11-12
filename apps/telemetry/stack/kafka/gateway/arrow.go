package gateway

import (
	"encoding/json"
	"fmt"
	"io"
	"math"
	"sort"
	"strconv"
	"strings"

	"github.com/apache/arrow-go/v18/arrow"
	"github.com/apache/arrow-go/v18/arrow/array"
	"github.com/apache/arrow-go/v18/arrow/ipc"
	"github.com/apache/arrow-go/v18/arrow/memory"
)

func arrowType(f Field) arrow.DataType {
	switch f.SQLType {
	case "double precision":
		return arrow.PrimitiveTypes.Float64
	case "integer", "smallint", "bigint":
		return arrow.PrimitiveTypes.Int64
	case "boolean":
		return arrow.FixedWidthTypes.Boolean
	case "text", "numeric":
		return arrow.BinaryTypes.String
	default:
		return arrow.BinaryTypes.Binary
	}
}
func arrowSchema(p Pool) *arrow.Schema {
	fields := []arrow.Field{{Name: "time", Type: arrow.PrimitiveTypes.Int64}, {Name: "sample_time_ns", Type: arrow.PrimitiveTypes.Int64}, {Name: "session_start_ms", Type: arrow.PrimitiveTypes.Int64}}
	for _, f := range p.Fields {
		fields = append(fields, arrow.Field{Name: f.Name, Type: arrowType(f), Nullable: true})
	}
	return arrow.NewSchema(fields, nil)
}
func number(v any) (float64, error) {
	switch n := v.(type) {
	case float64:
		return n, nil
	case float32:
		return float64(n), nil
	case json.Number:
		return n.Float64()
	default:
		return strconv.ParseFloat(fmt.Sprint(v), 64)
	}
}
func recordBatch(p Pool, rows []Row) (arrow.RecordBatch, error) {
	b := array.NewRecordBuilder(memory.DefaultAllocator, arrowSchema(p))
	defer b.Release()
	for _, row := range rows {
		b.Field(0).(*array.Int64Builder).Append(row.Time)
		b.Field(1).(*array.Int64Builder).Append(row.NS)
		b.Field(2).(*array.Int64Builder).Append(row.Session)
		for i, f := range p.Fields {
			v, ok := row.Values[f.Name]
			builder := b.Field(i + 3)
			if !ok || v == nil {
				builder.AppendNull()
				continue
			}
			switch builder := builder.(type) {
			case *array.Float64Builder:
				n, err := number(v)
				if err != nil {
					return nil, err
				}
				builder.Append(n)
			case *array.Int64Builder:
				n, err := strconv.ParseInt(fmt.Sprint(v), 10, 64)
				if err != nil {
					return nil, err
				}
				builder.Append(n)
			case *array.BooleanBuilder:
				n, ok := v.(bool)
				if !ok {
					return nil, fmt.Errorf("invalid boolean")
				}
				builder.Append(n)
			case *array.StringBuilder:
				builder.Append(fmt.Sprint(v))
			case *array.BinaryBuilder:
				data, err := json.Marshal(publicValue(v))
				if err != nil {
					return nil, err
				}
				builder.Append(data)
			}
		}
	}
	return b.NewRecordBatch(), nil
}
func batchRowsRange(p Pool, rec arrow.RecordBatch, start, end int64) ([]Row, error) {
	clock := rec.Column(0).(*array.Int64)
	first := sort.Search(int(rec.NumRows()), func(i int) bool { return clock.Value(i) >= start })
	last := sort.Search(int(rec.NumRows()), func(i int) bool { return clock.Value(i) >= end })
	columns := make([]int, len(p.Fields))
	for i, field := range p.Fields {
		indices := rec.Schema().FieldIndices(field.Name)
		if len(indices) != 1 {
			return nil, fmt.Errorf("Arrow field %s is absent or ambiguous", field.Name)
		}
		columns[i] = indices[0]
	}
	result := make([]Row, 0, last-first)
	for i := first; i < last; i++ {
		present := false
		for _, column := range columns {
			if !rec.Column(column).IsNull(i) {
				present = true
				break
			}
		}
		if !present {
			continue
		}
		r := Row{Time: clock.Value(i), NS: rec.Column(1).(*array.Int64).Value(i), Session: rec.Column(2).(*array.Int64).Value(i), Values: make(map[string]any, len(p.Fields))}
		for j, f := range p.Fields {
			col := rec.Column(columns[j])
			if col.IsNull(i) {
				continue
			}
			var v any
			switch col := col.(type) {
			case *array.Float64:
				v = col.Value(i)
			case *array.Int64:
				if f.SQLType == "bigint" {
					v = strconv.FormatInt(col.Value(i), 10)
				} else {
					v = col.Value(i)
				}
			case *array.Boolean:
				v = col.Value(i)
			case *array.String:
				// Rows outlive the reader and its mmap. Arrow string slices do not.
				v = strings.Clone(col.Value(i))
			case *array.Binary:
				if err := json.Unmarshal(col.Value(i), &v); err != nil {
					return nil, err
				}
			}
			r.Values[f.Name] = v
		}
		result = append(result, r)
	}
	return result, nil
}
func publicValue(v any) any {
	switch x := v.(type) {
	case float64:
		if math.IsNaN(x) {
			return "NaN"
		}
		if math.IsInf(x, 1) {
			return "Infinity"
		}
		if math.IsInf(x, -1) {
			return "-Infinity"
		}
	case []any:
		out := make([]any, len(x))
		for i, v := range x {
			out[i] = publicValue(v)
		}
		return out
	}
	return v
}

// A stable long-form IPC schema permits signals from different pools in one
// response. JSON values retain arrays and exact integer strings losslessly.
func writeArrow(w io.Writer, page *Page) error {
	schema := arrow.NewSchema([]arrow.Field{{Name: "time", Type: arrow.FixedWidthTypes.Timestamp_ms}, {Name: "sample_time_ns", Type: arrow.PrimitiveTypes.Int64}, {Name: "session_start_ms", Type: arrow.PrimitiveTypes.Int64}, {Name: "signal", Type: arrow.BinaryTypes.String}, {Name: "value_json", Type: arrow.BinaryTypes.String}}, nil)
	b := array.NewRecordBuilder(memory.DefaultAllocator, schema)
	defer b.Release()
	for _, row := range page.Data {
		ns, err := strconv.ParseInt(row.NS, 10, 64)
		if err != nil {
			return err
		}
		session, err := strconv.ParseInt(row.Session, 10, 64)
		if err != nil {
			return err
		}
		b.Field(0).(*array.TimestampBuilder).Append(arrow.Timestamp(row.position.Time))
		b.Field(1).(*array.Int64Builder).Append(ns)
		b.Field(2).(*array.Int64Builder).Append(session)
		b.Field(3).(*array.StringBuilder).Append(row.Signal)
		value, err := json.Marshal(row.Value)
		if err != nil {
			return err
		}
		b.Field(4).(*array.StringBuilder).Append(string(value))
	}
	writer := ipc.NewWriter(w, ipc.WithSchema(schema))
	rec := b.NewRecordBatch()
	defer rec.Release()
	if err := writer.Write(rec); err != nil {
		return err
	}
	return writer.Close()
}
