package grafana

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"strings"

	"github.com/grafana/grafana-plugin-sdk-go/data"
)

type signal struct {
	Name       string   `json:"signal"`
	Type       string   `json:"value_type"`
	Unit       string   `json:"unit"`
	Pool       string   `json:"pool"`
	Repeated   bool     `json:"repeated"`
	Aggregates []string `json:"aggregates"`
}
type catalog struct {
	Version string   `json:"schema_version"`
	Signals []signal `json:"signals"`
}

func (a *Adapter) loadCatalog(ctx context.Context, version string) (catalog, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.catalog.Version != "" && (version == "" || version == a.catalog.Version) {
		return a.catalog, nil
	}
	response, err := a.request(ctx, "GET", "/v1/signals", nil)
	if err != nil {
		return catalog{}, err
	}
	defer response.Body.Close()
	if response.StatusCode != 200 {
		return catalog{}, fmt.Errorf("gateway catalog: HTTP %d", response.StatusCode)
	}
	var next catalog
	if err := json.NewDecoder(io.LimitReader(response.Body, 4<<20)).Decode(&next); err != nil {
		return catalog{}, err
	}
	if version != "" && version != next.Version {
		return catalog{}, fmt.Errorf("gateway schema changed; rerun the query")
	}
	a.catalog = next
	return next, nil
}

func (c catalog) chartSignals(q Query) ([]string, error) {
	known := map[string]signal{}
	for _, s := range c.Signals {
		known[s.Name] = s
	}
	names := []string{}
	seen := map[string]bool{}
	add := func(name string) error {
		s, ok := known[name]
		if !ok {
			return fmt.Errorf("unknown signal %s; refresh discovery", name)
		}
		if s.Repeated || s.Type == "string" || s.Type == "bytes" {
			return fmt.Errorf("%s is not a scalar chart signal; arrays are available through the raw API", name)
		}
		allowed := false
		for _, agg := range s.Aggregates {
			allowed = allowed || agg == q.Aggregate
		}
		if !allowed {
			return fmt.Errorf("%s does not support %s", name, q.Aggregate)
		}
		if !seen[name] {
			names = append(names, name)
			seen[name] = true
		}
		return nil
	}
	for _, name := range q.Signals {
		if name == "*" {
			return nil, fmt.Errorf("select scalar chart signals explicitly")
		}
		if _, ok := known[name]; !ok {
			return nil, fmt.Errorf("unknown signal %s; refresh discovery", name)
		}
		if len(q.Pools) > 0 {
			matches := false
			for _, pool := range q.Pools {
				matches = matches || known[name].Pool == pool
			}
			if !matches {
				continue
			}
		}
		if err := add(name); err != nil {
			return nil, err
		}
	}
	for _, pool := range q.Pools {
		found := false
		for _, s := range c.Signals {
			if s.Pool != pool {
				continue
			}
			found = true
			if len(q.Signals) == 0 {
				if err := add(s.Name); err != nil {
					return nil, err
				}
			}
		}
		if !found {
			return nil, fmt.Errorf("unknown pool %s", pool)
		}
	}
	if len(names) == 0 {
		return nil, fmt.Errorf("select scalar signals within the requested pools")
	}
	return names, nil
}

func unitID(unit string) string {
	if id := map[string]string{"V": "volt", "A": "amp", "%": "percent", "degC": "celsius", "C": "celsius", "W": "watt", "kW": "kwatt", "m/s": "velocityms", "rpm": "rotrpm", "s": "s", "ms": "ms"}[unit]; id != "" {
		return id
	}
	if unit == "" {
		return "none"
	}
	return "suffix:" + unit
}

func (c catalog) annotate(frames data.Frames, p page) {
	units := map[string]string{}
	for _, s := range c.Signals {
		units[s.Name] = s.Unit
	}
	for _, frame := range frames {
		frame.Meta = &data.FrameMeta{Custom: map[string]any{"live": p.Live, "bucket_ms": p.Bucket, "schema_version": p.Schema, "provisional": p.Provisional}}
		if p.Provisional {
			frame.Meta.Notices = append(frame.Meta.Notices, data.Notice{Severity: data.NoticeSeverityWarning, Text: "Live window is provisional; inspect sample age and Kafka lag."})
		}
		for _, field := range frame.Fields {
			if field.Config != nil && strings.Contains(field.Config.Path, ".") {
				field.Config.Unit = unitID(units[field.Config.Path])
			}
		}
	}
}
