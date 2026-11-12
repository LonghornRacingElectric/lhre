package grafana

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/grafana/grafana-plugin-sdk-go/backend"
	"github.com/grafana/grafana-plugin-sdk-go/backend/instancemgmt"
	"github.com/grafana/grafana-plugin-sdk-go/data"
)

type Adapter struct {
	url, token string
	client     *http.Client
}
type Query struct {
	RefID     string   `json:"refId"`
	Signals   []string `json:"signals"`
	Pools     []string `json:"pools"`
	Aggregate string   `json:"agg"`
	Bucket    int64    `json:"bucket_ms"`
	Alias     string   `json:"alias"`
	Live      bool     `json:"live"`
}
type observation struct {
	TS     string `json:"ts"`
	NS     string `json:"sample_time_ns"`
	Signal string `json:"signal"`
	Value  any    `json:"value"`
}
type page struct {
	Data        []observation `json:"data"`
	Next        *string       `json:"next_page_token"`
	Provisional bool          `json:"provisional"`
}

func New(ctx context.Context, s backend.DataSourceInstanceSettings) (instancemgmt.Instance, error) {
	var cfg struct {
		URL string `json:"url"`
	}
	if err := json.Unmarshal(s.JSONData, &cfg); err != nil {
		return nil, err
	}
	u, err := url.Parse(cfg.URL)
	if err != nil || u.Host == "" || (u.Scheme != "http" && u.Scheme != "https") {
		return nil, fmt.Errorf("set an HTTP(S) gateway URL")
	}
	return &Adapter{strings.TrimRight(cfg.URL, "/"), s.DecryptedSecureJSONData["apiToken"], &http.Client{Timeout: 35 * time.Second}}, nil
}
func (a *Adapter) request(ctx context.Context, method, path string, body []byte) (*http.Response, error) {
	req, err := http.NewRequestWithContext(ctx, method, a.url+path, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+a.token)
	req.Header.Set("Content-Type", "application/json")
	return a.client.Do(req)
}
func (a *Adapter) CheckHealth(ctx context.Context, req *backend.CheckHealthRequest) (*backend.CheckHealthResult, error) {
	response, err := a.request(ctx, "GET", "/v1/signals", nil)
	if err != nil {
		return &backend.CheckHealthResult{Status: backend.HealthStatusError, Message: err.Error()}, nil
	}
	defer response.Body.Close()
	if response.StatusCode != 200 {
		return &backend.CheckHealthResult{Status: backend.HealthStatusError, Message: "Gateway rejected the connection or token"}, nil
	}
	return &backend.CheckHealthResult{Status: backend.HealthStatusOk, Message: "Artemis gateway connected"}, nil
}
func (a *Adapter) CallResource(ctx context.Context, req *backend.CallResourceRequest, sender backend.CallResourceResponseSender) error {
	if req.Path != "signals" {
		return sender.Send(&backend.CallResourceResponse{Status: 404})
	}
	response, err := a.request(ctx, "GET", "/v1/signals", nil)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, 4<<20))
	if err != nil {
		return err
	}
	return sender.Send(&backend.CallResourceResponse{Status: response.StatusCode, Headers: map[string][]string{"Content-Type": {"application/json"}}, Body: body})
}

func queryBody(q Query, query backend.DataQuery) map[string]any {
	body := map[string]any{"car": "artemis", "signals": q.Signals, "pools": q.Pools, "agg": q.Aggregate, "bucket_ms": q.Bucket, "t0": query.TimeRange.From.UTC().Truncate(time.Millisecond).Format(time.RFC3339Nano), "t1": query.TimeRange.To.UTC().Truncate(time.Millisecond).Format(time.RFC3339Nano), "max_points": min(max(query.MaxDataPoints, 1), 10000), "page_size": 10000}
	if q.Aggregate == "raw" {
		delete(body, "max_points")
		delete(body, "bucket_ms")
	}
	return body
}
func (a *Adapter) QueryData(ctx context.Context, req *backend.QueryDataRequest) (*backend.QueryDataResponse, error) {
	response := backend.NewQueryDataResponse()
	for _, query := range req.Queries {
		var q Query
		if err := json.Unmarshal(query.JSON, &q); err != nil {
			response.Responses[query.RefID] = backend.DataResponse{Error: err}
			continue
		}
		if q.Aggregate == "" {
			q.Aggregate = "mean"
		}
		if q.Live {
			body := queryBody(q, query)
			body["t1"] = "live"
			body["window_ms"] = query.TimeRange.To.Sub(query.TimeRange.From).Milliseconds()
			body["alias"] = q.Alias
			payload, _ := json.Marshal(body)
			frame := data.NewFrame(query.RefID)
			frame.Meta = &data.FrameMeta{Channel: "ds/" + req.PluginContext.DataSourceInstanceSettings.UID + "/query/" + query.RefID}
			frame.Meta.Custom = json.RawMessage(payload)
			response.Responses[query.RefID] = backend.DataResponse{Frames: data.Frames{frame}}
			continue
		}
		body := queryBody(q, query)
		all := []observation{}
		var err error
		for {
			encoded, _ := json.Marshal(body)
			result, requestErr := a.request(ctx, "POST", "/v1/query", encoded)
			if requestErr != nil {
				err = requestErr
				break
			}
			if result.StatusCode != 200 {
				message, _ := io.ReadAll(io.LimitReader(result.Body, 4096))
				result.Body.Close()
				err = fmt.Errorf("gateway: %s", message)
				break
			}
			var p page
			err = json.NewDecoder(io.LimitReader(result.Body, 2<<20)).Decode(&p)
			result.Body.Close()
			if err != nil {
				break
			}
			all = append(all, p.Data...)
			if len(all) > 200000 {
				err = fmt.Errorf("too many observations; reduce max points or signals")
				break
			}
			if p.Next == nil {
				break
			}
			body["page_token"] = *p.Next
		}
		if err != nil {
			response.Responses[query.RefID] = backend.DataResponse{Error: err}
		} else {
			response.Responses[query.RefID] = backend.DataResponse{Frames: frames(all, q.Alias, query.RefID)}
		}
	}
	return response, nil
}
func frames(rows []observation, alias, ref string) data.Frames {
	bySignal := map[string][]observation{}
	order := []string{}
	for _, row := range rows {
		if _, ok := bySignal[row.Signal]; !ok {
			order = append(order, row.Signal)
		}
		bySignal[row.Signal] = append(bySignal[row.Signal], row)
	}
	out := data.Frames{}
	for _, name := range order {
		rows := bySignal[name]
		times := []time.Time{}
		values := []*float64{}
		texts := []*string{}
		numeric := true
		for _, row := range rows {
			stamp, err := time.Parse(time.RFC3339Nano, row.TS)
			if err != nil {
				continue
			}
			times = append(times, stamp)
			var n float64
			switch v := row.Value.(type) {
			case float64:
				n = v
			case string:
				n, err = strconv.ParseFloat(v, 64)
			case bool:
				if v {
					n = 1
				}
			default:
				err = fmt.Errorf("non-numeric")
			}
			if err != nil {
				numeric = false
				values = append(values, nil)
				value := fmt.Sprint(row.Value)
				texts = append(texts, &value)
			} else {
				values = append(values, &n)
				texts = append(texts, nil)
			}
		}
		label := name
		if alias != "" {
			label = strings.ReplaceAll(alias, "{{signal}}", name)
		}
		var field *data.Field
		if numeric {
			field = data.NewField(label, nil, values)
		} else {
			field = data.NewField(label, nil, texts)
		}
		frame := data.NewFrame(label, data.NewField("time", nil, times), field)
		frame.RefID = ref
		out = append(out, frame)
	}
	return out
}
func (a *Adapter) SubscribeStream(context.Context, *backend.SubscribeStreamRequest) (*backend.SubscribeStreamResponse, error) {
	return &backend.SubscribeStreamResponse{Status: backend.SubscribeStreamStatusOK}, nil
}
func (a *Adapter) PublishStream(context.Context, *backend.PublishStreamRequest) (*backend.PublishStreamResponse, error) {
	return &backend.PublishStreamResponse{Status: backend.PublishStreamStatusPermissionDenied}, nil
}
func (a *Adapter) RunStream(ctx context.Context, req *backend.RunStreamRequest, sender *backend.StreamSender) error {
	var body map[string]any
	if err := json.Unmarshal(req.Data, &body); err != nil {
		return err
	}
	alias, _ := body["alias"].(string)
	delete(body, "alias")
	window, _ := body["window_ms"].(float64)
	if window > 0 {
		body["t0"] = time.Now().Add(-time.Duration(window) * time.Millisecond).UTC().Truncate(time.Millisecond).Format(time.RFC3339Nano)
	}
	encoded, _ := json.Marshal(body)
	reqHTTP, err := http.NewRequestWithContext(ctx, "GET", a.url+"/v1/stream?query="+url.QueryEscape(string(encoded)), nil)
	if err != nil {
		return err
	}
	reqHTTP.Header.Set("Authorization", "Bearer "+a.token)
	streamClient := &http.Client{Transport: a.client.Transport}
	response, err := streamClient.Do(reqHTTP)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode != 200 {
		return fmt.Errorf("gateway stream: HTTP %d", response.StatusCode)
	}
	scanner := bufio.NewScanner(response.Body)
	scanner.Buffer(make([]byte, 65536), 32<<20)
	for scanner.Scan() {
		line := scanner.Text()
		if !strings.HasPrefix(line, "data: ") {
			continue
		}
		var event struct {
			Type    string `json:"type"`
			Page    page   `json:"page"`
			Message string `json:"message"`
		}
		if err := json.Unmarshal([]byte(line[6:]), &event); err != nil {
			return err
		}
		if event.Type == "gap" {
			return fmt.Errorf("live gap: %s", event.Message)
		}
		for _, frame := range wideFrames(event.Page.Data, alias, body) {
			if err := sender.SendFrame(frame, data.IncludeAll); err != nil {
				return err
			}
		}
	}
	return scanner.Err()
}
