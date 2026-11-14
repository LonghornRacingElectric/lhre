package grafana

import (
	"fmt"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/grafana/grafana-plugin-sdk-go/data"
)

// One fixed wide frame per subscription keeps the Grafana Live schema stable
// as sparse pools arrive. Each message replaces the bounded plotting window.
func wideFrames(rows []observation, alias string, body map[string]any) data.Frames {
	names := []string{}
	switch selected := body["signals"].(type) {
	case []string:
		names = append(names, selected...)
	case []any:
		for _, name := range selected {
			names = append(names, fmt.Sprint(name))
		}
	}
	if len(names) == 0 || len(names) == 1 && names[0] == "*" {
		seen := map[string]bool{}
		for _, r := range rows {
			if !seen[r.Signal] {
				names = append(names, r.Signal)
				seen[r.Signal] = true
			}
		}
		sort.Strings(names)
	}
	byTime := map[string]map[string]any{}
	stamps := []string{}
	for _, r := range rows {
		if _, ok := byTime[r.TS]; !ok {
			stamps = append(stamps, r.TS)
			byTime[r.TS] = map[string]any{}
		}
		byTime[r.TS][r.Signal] = r.Value
	}
	sort.Strings(stamps)
	times := []time.Time{}
	for _, stamp := range stamps {
		t, err := time.Parse(time.RFC3339Nano, stamp)
		if err != nil {
			return nil
		}
		times = append(times, t)
	}
	frame := data.NewFrame("Artemis", data.NewField("time", nil, times))
	for _, name := range names {
		values := make([]*float64, len(stamps))
		for i, stamp := range stamps {
			value, ok := byTime[stamp][name]
			if !ok {
				continue
			}
			var n float64
			switch v := value.(type) {
			case float64:
				n = v
			case bool:
				if v {
					n = 1
				}
			case string:
				parsed, err := strconv.ParseFloat(v, 64)
				if err != nil {
					continue
				}
				n = parsed
			default:
				continue
			}
			values[i] = &n
		}
		label := name
		if alias != "" {
			label = strings.ReplaceAll(alias, "{{signal}}", name)
		}
		field := data.NewField(label, nil, values)
		field.Config = &data.FieldConfig{Path: name}
		frame.Fields = append(frame.Fields, field)
	}
	return data.Frames{frame}
}
