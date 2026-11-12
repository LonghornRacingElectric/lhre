package gateway

import (
	"encoding/json"
	"fmt"
	"os"
	"regexp"
	"strings"
)

type Field struct {
	Name    string  `json:"name"`
	SQLType string  `json:"sql_type"`
	Path    []int32 `json:"path"`
}
type Pool struct {
	Table     string  `json:"table"`
	Group     string  `json:"group"`
	Frequency float64 `json:"frequency_hz"`
	Fields    []Field `json:"fields"`
}
type Signal struct {
	Name        string   `json:"signal"`
	Group       string   `json:"group"`
	Type        string   `json:"value_type"`
	Repeated    bool     `json:"repeated"`
	Unit        *string  `json:"unit"`
	Rate        *float64 `json:"rate_hz"`
	Module      *string  `json:"module"`
	ChannelType string   `json:"channel_type"`
	Aggregates  []string `json:"aggregates"`
	Pool        string   `json:"pool"`
}
type Schema struct {
	StorageID string   `json:"storage_id"`
	Pools     []Pool   `json:"pools"`
	Version   string   `json:"schema_version"`
	Signals   []Signal `json:"signals"`
	bySignal  map[string]Field
	byPool    map[string]Pool
}

var identifier = regexp.MustCompile(`^[a-z][a-z0-9_]*$`)

func LoadSchema(contract, catalog string) (*Schema, error) {
	s := &Schema{}
	for _, name := range []string{contract, catalog} {
		data, err := os.ReadFile(name)
		if err != nil {
			return nil, err
		}
		if err := json.Unmarshal(data, s); err != nil {
			return nil, err
		}
	}
	return s, s.initialize()
}

func (s *Schema) initialize() error {
	s.bySignal, s.byPool = map[string]Field{}, map[string]Pool{}
	for _, p := range s.Pools {
		if !identifier.MatchString(p.Table) || !identifier.MatchString(p.Group) {
			return fmt.Errorf("invalid generated pool")
		}
		s.byPool[p.Table] = p
		for _, f := range p.Fields {
			if !identifier.MatchString(f.Name) {
				return fmt.Errorf("invalid generated field")
			}
			s.bySignal[p.Group+"."+f.Name] = f
		}
	}
	for i := range s.Signals {
		sig := &s.Signals[i]
		f, ok := s.bySignal[sig.Name]
		if !ok {
			return fmt.Errorf("catalog signal %s has no storage mapping", sig.Name)
		}
		sig.Aggregates = []string{"raw", "last"}
		if numeric(f) {
			sig.Aggregates = append(sig.Aggregates, "mean", "min", "max")
		}
		for _, p := range s.Pools {
			if p.Group == sig.Group {
				for _, field := range p.Fields {
					if field.Name == f.Name {
						sig.Pool = p.Table
					}
				}
			}
		}
	}
	return nil
}

func numeric(f Field) bool {
	return !strings.HasSuffix(f.SQLType, "[]") && (f.SQLType == "double precision" || f.SQLType == "integer" || f.SQLType == "bigint" || f.SQLType == "numeric" || f.SQLType == "smallint")
}

func (s *Schema) selectSignals(names, pools []string) ([]Signal, error) {
	wanted, selectedPools := map[string]bool{}, map[string]bool{}
	all := len(names) == 1 && names[0] == "*"
	for _, p := range pools {
		if _, ok := s.byPool[p]; !ok {
			return nil, problem(404, "unknown_pool", p)
		}
		selectedPools[p] = true
	}
	for _, n := range names {
		wanted[n] = true
	}
	result := []Signal{}
	for _, sig := range s.Signals {
		if (all || wanted[sig.Name] || len(names) == 0 && len(pools) > 0) && (len(pools) == 0 || selectedPools[sig.Pool]) {
			result = append(result, sig)
			delete(wanted, sig.Name)
		}
	}
	delete(wanted, "*")
	if !all && len(wanted) > 0 {
		return nil, problem(404, "unknown_signal", "Signal is absent or excluded by pools")
	}
	if len(result) == 0 {
		return nil, problem(400, "invalid_request", "Select signals, pools, or signals: [\"*\"]")
	}
	return result, nil
}
