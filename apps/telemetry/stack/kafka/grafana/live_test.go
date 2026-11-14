package grafana

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"

	"github.com/grafana/grafana-plugin-sdk-go/backend"
)

type frameRecorder struct {
	packets []*backend.StreamPacket
}

func TestHistoryResolvesPoolsAndDiscoversNewSchemaSignals(t *testing.T) {
	var revision atomic.Int32
	queries := make(chan Query, 2)
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		version := revision.Load()
		if r.URL.Path == "/v1/signals" {
			if version == 0 {
				fmt.Fprint(w, `{"schema_version":"v0","signals":[{"signal":"controls.apps","pool":"controls","value_type":"float","aggregates":["last"]},{"signal":"pack.voltage","pool":"pack","value_type":"float","aggregates":["last"]}]}`)
			} else {
				fmt.Fprint(w, `{"schema_version":"v1","signals":[{"signal":"pack.power","pool":"pack","value_type":"float","unit":"kW","aggregates":["last"]}]}`)
			}
			return
		}
		var q Query
		if err := json.NewDecoder(r.Body).Decode(&q); err != nil || len(q.Signals) == 0 {
			t.Errorf("invalid history query: %+v, %v", q, err)
			http.Error(w, "invalid query", 400)
			return
		}
		queries <- q
		fmt.Fprintf(w, `{"schema_version":"v%d","data":[{"ts":"2026-11-12T12:00:00Z","signal":%q,"value":42}]}`, version, q.Signals[0])
	}))
	defer upstream.Close()
	adapter := &Adapter{url: upstream.URL, client: upstream.Client()}
	run := func(payload string) backend.DataResponse {
		t.Helper()
		response, err := adapter.QueryData(context.Background(), &backend.QueryDataRequest{
			Queries: []backend.DataQuery{{RefID: "A", JSON: json.RawMessage(payload)}},
		})
		if err != nil {
			t.Fatal(err)
		}
		result := response.Responses["A"]
		if result.Error != nil || len(result.Frames) != 1 {
			t.Fatalf("history response: %+v", result)
		}
		return result
	}
	run(`{"signals":["controls.apps","pack.voltage"],"pools":["controls"],"agg":"last"}`)
	first := <-queries
	if len(first.Signals) != 1 || first.Signals[0] != "controls.apps" || len(first.Pools) != 0 {
		t.Fatalf("unresolved history selection: %+v", first)
	}
	revision.Store(1)
	result := run(`{"signals":["pack.power"],"agg":"last"}`)
	<-queries
	if result.Frames[0].Fields[1].Config.Unit != "kwatt" {
		t.Fatal("new schema's signal unit was not applied")
	}
}

func TestChartMetadataAndUnsupportedArrays(t *testing.T) {
	queries := 0
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/v1/signals" {
			fmt.Fprint(w, `{"schema_version":"demo","signals":[{"signal":"pack.voltage","value_type":"float","unit":"V","aggregates":["last"]},{"signal":"controls.enable","value_type":"bool","unit":"","aggregates":["last"]},{"signal":"pack.cells","value_type":"float","repeated":true,"aggregates":["last"]}]}`)
			return
		}
		queries++
		fmt.Fprint(w, `{"schema_version":"demo","bucket_ms":100,"data":[{"ts":"2026-11-12T12:00:00Z","signal":"pack.voltage","value":420},{"ts":"2026-11-12T12:00:00Z","signal":"controls.enable","value":false}]}`)
	}))
	defer upstream.Close()
	a := &Adapter{url: upstream.URL, client: upstream.Client()}
	req := &backend.QueryDataRequest{Queries: []backend.DataQuery{{RefID: "A", JSON: json.RawMessage(`{"signals":["pack.voltage","controls.enable"],"agg":"last"}`)}, {RefID: "B", JSON: json.RawMessage(`{"signals":["pack.cells"],"agg":"last"}`)}}}
	response, err := a.QueryData(context.Background(), req)
	if err != nil || response.Responses["A"].Error != nil || response.Responses["B"].Error == nil || queries != 1 {
		t.Fatalf("queries=%d response=%+v err=%v", queries, response, err)
	}
	frames := response.Responses["A"].Frames
	if len(frames) != 2 || frames[0].Fields[1].Config.Unit != "volt" || *frames[1].Fields[1].At(0).(*float64) != 0 {
		t.Fatalf("frames: %+v", frames)
	}
	if frames[0].Meta.Custom.(map[string]any)["bucket_ms"] != int64(100) {
		t.Fatal("effective bucket missing")
	}
}

func (recorder *frameRecorder) Send(packet *backend.StreamPacket) error {
	recorder.packets = append(recorder.packets, packet)
	return nil
}

func TestLiveWindowsPreserveCorrectionsRemovalAndEmptyFrames(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.URL.Path == "/v1/signals" {
			fmt.Fprint(writer, `{"schema_version":"demo","signals":[{"signal":"controls.apps","value_type":"float","unit":"%","aggregates":["mean"]}]}`)
			return
		}
		writer.Header().Set("Content-Type", "text/event-stream")
		for _, observations := range []string{
			`[{"ts":"2026-11-12T12:00:00.000Z","signal":"controls.apps","value":10},{"ts":"2026-11-12T12:00:01.000Z","signal":"controls.apps","value":20}]`,
			`[{"ts":"2026-11-12T12:00:01.000Z","signal":"controls.apps","value":30}]`,
			`[]`,
		} {
			fmt.Fprintf(writer, "data: {\"type\":\"data\",\"page\":{\"data\":%s,\"live\":{\"ready\":true,\"kafka_lag_messages\":0}}}\n\n", observations)
		}
	}))
	defer upstream.Close()
	adapter := &Adapter{url: upstream.URL, token: "test", client: upstream.Client()}
	recorder := &frameRecorder{}
	err := adapter.RunStream(context.Background(), &backend.RunStreamRequest{Data: json.RawMessage(`{"car":"artemis","signals":["controls.apps"]}`)}, backend.NewStreamSender(recorder))
	if err != nil || len(recorder.packets) != 3 {
		t.Fatalf("stream: packets=%d error=%v", len(recorder.packets), err)
	}
	for index, count := range []int{2, 1, 0} {
		var frame struct {
			Schema struct {
				Meta struct {
					Custom struct {
						Live struct {
							Ready bool `json:"ready"`
						} `json:"live"`
					} `json:"custom"`
				} `json:"meta"`
			} `json:"schema"`
			Data struct {
				Values [][]any `json:"values"`
			} `json:"data"`
		}
		if err := json.Unmarshal(recorder.packets[index].Data, &frame); err != nil {
			t.Fatal(err)
		}
		if len(frame.Data.Values) != 2 || len(frame.Data.Values[0]) != count || !frame.Schema.Meta.Custom.Live.Ready {
			t.Fatalf("window %d: %s", index, recorder.packets[index].Data)
		}
		if index == 1 && frame.Data.Values[1][0] != 30.0 {
			t.Fatalf("correction: %v", frame.Data.Values)
		}
	}
}
