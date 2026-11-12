package gateway

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/IBM/sarama"
)

type FeedEvent struct {
	Topic     string            `json:"topic"`
	Partition int32             `json:"partition"`
	Offset    string            `json:"offset"`
	Timestamp string            `json:"timestamp"`
	Payload   string            `json:"payload"`
	Headers   map[string]string `json:"headers"`
}
type Feeds struct {
	mu          sync.Mutex
	allowed     map[string]bool
	last        map[string]FeedEvent
	subscribers map[string]map[chan FeedEvent]bool
}

func NewFeeds(topics []string) *Feeds {
	f := &Feeds{allowed: map[string]bool{}, last: map[string]FeedEvent{}, subscribers: map[string]map[chan FeedEvent]bool{}}
	for _, t := range topics {
		if t != "" {
			f.allowed[t] = true
		}
	}
	return f
}
func (f *Feeds) Add(m *sarama.ConsumerMessage) {
	if len(m.Value) > 1<<20 {
		return
	}
	var body map[string]any
	if json.Unmarshal(m.Value, &body) != nil {
		return
	}
	for _, name := range []string{"car", "car_type", "carType"} {
		if car, ok := body[name].(string); ok && !strings.EqualFold(car, "artemis") {
			return
		}
	}
	header := map[string]string{}
	for _, h := range m.Headers {
		header[string(h.Key)] = string(h.Value)
	}
	if car := header["car_type"]; car != "" && !strings.EqualFold(car, "artemis") {
		return
	}
	event := FeedEvent{m.Topic, m.Partition, strconv.FormatInt(m.Offset, 10), strconv.FormatInt(m.Timestamp.UnixMilli(), 10), string(m.Value), header}
	f.mu.Lock()
	defer f.mu.Unlock()
	f.last[m.Topic] = event
	for ch := range f.subscribers[m.Topic] {
		select {
		case ch <- event:
		default:
			select {
			case <-ch:
			default:
			}
			select {
			case ch <- event:
			default:
			}
		}
	}
}
func (f *Feeds) Handler(w http.ResponseWriter, r *http.Request) {
	topic := r.PathValue("topic")
	if !f.allowed[topic] {
		writeError(w, problem(404, "unknown_topic", "Topic is not an Artemis live feed"))
		return
	}
	flusher, ok := w.(http.Flusher)
	if !ok {
		return
	}
	ch := make(chan FeedEvent, 1)
	f.mu.Lock()
	if f.subscribers[topic] == nil {
		f.subscribers[topic] = map[chan FeedEvent]bool{}
	}
	f.subscribers[topic][ch] = true
	if last, ok := f.last[topic]; ok {
		ch <- last
	}
	f.mu.Unlock()
	defer func() { f.mu.Lock(); delete(f.subscribers[topic], ch); f.mu.Unlock() }()
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("X-Accel-Buffering", "no")
	_, _ = fmt.Fprint(w, ": connected\n\n")
	flusher.Flush()
	heartbeat := time.NewTicker(15 * time.Second)
	defer heartbeat.Stop()
	for {
		select {
		case <-r.Context().Done():
			return
		case event := <-ch:
			data, _ := json.Marshal(event)
			if _, err := fmt.Fprintf(w, "data: %s\n\n", data); err != nil {
				return
			}
			flusher.Flush()
		case <-heartbeat.C:
			if _, err := fmt.Fprint(w, ": heartbeat\n\n"); err != nil {
				return
			}
			flusher.Flush()
		}
	}
}
