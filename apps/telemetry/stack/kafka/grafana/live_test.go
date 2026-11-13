package grafana

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/grafana/grafana-plugin-sdk-go/backend"
)

type frameRecorder struct {
	packets []*backend.StreamPacket
}

func (recorder *frameRecorder) Send(packet *backend.StreamPacket) error {
	recorder.packets = append(recorder.packets, packet)
	return nil
}

func TestLiveWindowsPreserveCorrectionsRemovalAndEmptyFrames(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
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
