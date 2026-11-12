package gateway

import (
	"context"
	"fmt"
	"log"
	"os"
	"strconv"
	"sync"
	"time"

	"github.com/IBM/sarama"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/reflect/protodesc"
	"google.golang.org/protobuf/reflect/protoreflect"
	"google.golang.org/protobuf/types/descriptorpb"
	"google.golang.org/protobuf/types/dynamicpb"
)

type Decoder struct {
	batch  protoreflect.MessageDescriptor
	schema *Schema
}

func NewDecoder(path string, schema *Schema) (*Decoder, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	set := &descriptorpb.FileDescriptorSet{}
	if err = proto.Unmarshal(data, set); err != nil {
		return nil, err
	}
	files, err := protodesc.NewFiles(set)
	if err != nil {
		return nil, err
	}
	var batch protoreflect.MessageDescriptor
	files.RangeFiles(func(file protoreflect.FileDescriptor) bool {
		for i := 0; i < file.Messages().Len(); i++ {
			m := file.Messages().Get(i)
			if m.Name() == "TelemetryBatch" {
				batch = m
				return false
			}
		}
		return true
	})
	if batch == nil {
		return nil, fmt.Errorf("Artemis TelemetryBatch descriptor missing")
	}
	return &Decoder{batch, schema}, nil
}
func named(m protoreflect.Message, name string) protoreflect.Value {
	return m.Get(m.Descriptor().Fields().ByName(protoreflect.Name(name)))
}
func wireValue(f protoreflect.FieldDescriptor, v protoreflect.Value) any {
	if f.IsList() {
		l := v.List()
		out := make([]any, l.Len())
		for i := range out {
			out[i] = scalarValue(f, l.Get(i))
		}
		return out
	}
	return scalarValue(f, v)
}
func scalarValue(f protoreflect.FieldDescriptor, v protoreflect.Value) any {
	switch f.Kind() {
	case protoreflect.FloatKind:
		// Match protobuf JSON normalization in the existing database writer.
		var number float64
		for precision := 6; precision <= 9; precision++ {
			number, _ = strconv.ParseFloat(strconv.FormatFloat(v.Float(), 'g', precision, 64), 64)
			if float32(number) == float32(v.Float()) {
				break
			}
		}
		return number
	case protoreflect.Int64Kind, protoreflect.Sint64Kind, protoreflect.Sfixed64Kind:
		return fmt.Sprint(v.Int())
	case protoreflect.Uint32Kind, protoreflect.Uint64Kind, protoreflect.Fixed64Kind:
		return fmt.Sprint(v.Uint())
	case protoreflect.EnumKind:
		return int32(v.Enum())
	default:
		return normalize(v.Interface())
	}
}
func (d *Decoder) Decode(data []byte) ([]LiveRow, error) {
	b := dynamicpb.NewMessage(d.batch)
	if err := proto.Unmarshal(data, b); err != nil {
		return nil, err
	}
	if named(b, "format_version").Uint() != 1 {
		return nil, fmt.Errorf("unsupported native batch")
	}
	updates := named(b, "updates").List()
	if updates.Len() == 0 || updates.Len() > 64 {
		return nil, fmt.Errorf("invalid update batch size")
	}
	out := []LiveRow{}
	for i := 0; i < updates.Len(); i++ {
		u := updates.Get(i).Message()
		ns := named(u, "sample_time_ns").Int()
		if ns <= 0 {
			return nil, fmt.Errorf("invalid sample time")
		}
		values := named(u, "values").Message()
		stamp := named(values, "time").Int()
		if stamp <= 0 || ns/1000000 != stamp {
			return nil, fmt.Errorf("inconsistent sample time")
		}
		paths := named(u, "updated_fields").List()
		if paths.Len() == 0 {
			return nil, fmt.Errorf("empty update")
		}
		selected := map[string]bool{}
		for j := 0; j < paths.Len(); j++ {
			p := paths.Get(j).Message()
			key := fmt.Sprintf("%d/%d", named(p, "group").Uint(), named(p, "field").Uint())
			group := values.Descriptor().Fields().ByNumber(protoreflect.FieldNumber(named(p, "group").Uint()))
			if selected[key] || group == nil || group.Message() == nil || !values.Has(group) || group.Message().Fields().ByNumber(protoreflect.FieldNumber(named(p, "field").Uint())) == nil {
				return nil, fmt.Errorf("unknown, duplicate or missing update field")
			}
			selected[key] = true
		}
		for _, pool := range d.schema.Pools {
			r := Row{Time: stamp, NS: ns, Values: map[string]any{}}
			for _, f := range pool.Fields {
				if len(f.Path) != 2 || !selected[fmt.Sprintf("%d/%d", f.Path[0], f.Path[1])] {
					continue
				}
				group := values.Descriptor().Fields().ByNumber(protoreflect.FieldNumber(f.Path[0]))
				if group == nil {
					return nil, fmt.Errorf("unknown group")
				}
				m := values.Get(group).Message()
				field := m.Descriptor().Fields().ByNumber(protoreflect.FieldNumber(f.Path[1]))
				if field == nil {
					return nil, fmt.Errorf("unknown field")
				}
				r.Values[f.Name] = wireValue(field, m.Get(field))
			}
			if len(r.Values) > 0 {
				out = append(out, LiveRow{pool.Table, r})
			}
		}
	}
	return out, nil
}

// One consumer group fans out into the shared ring. Its durable restart point is
// the writer's database checkpoint, since Kafka commits cannot persist RAM.
func Consume(ctx context.Context, brokers []string, group, topic string, topics []string, store Store, ring *Ring, decoder *Decoder, feeds *Feeds) {
	for ctx.Err() == nil {
		cfg := sarama.NewConfig()
		cfg.Metadata.AllowAutoTopicCreation = true
		cfg.Consumer.Offsets.Initial = sarama.OffsetOldest
		cfg.Consumer.Offsets.AutoCommit.Enable = false
		cfg.Consumer.Return.Errors = true
		client, err := sarama.NewClient(brokers, cfg)
		if err == nil {
			var consumer sarama.ConsumerGroup
			consumer, err = sarama.NewConsumerGroupFromClient(group, client)
			if err == nil {
				handler := &consumerHandler{client: client, store: store, ring: ring, decoder: decoder, topic: topic, feeds: feeds}
				go func() {
					for err := range consumer.Errors() {
						log.Printf("query Kafka: %v", err)
					}
				}()
				for ctx.Err() == nil {
					if err = consumer.Consume(ctx, append([]string{topic}, topics...), handler); err != nil {
						break
					}
				}
				ring.Ready(false)
				_ = consumer.Close()
			}
			_ = client.Close()
		}
		if err != nil {
			log.Printf("query Kafka unavailable: %v", err)
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(time.Second):
		}
	}
}

type consumerHandler struct {
	client     sarama.Client
	topic      string
	feeds      *Feeds
	store      Store
	ring       *Ring
	decoder    *Decoder
	mu         sync.Mutex
	checkpoint Offsets
}

func (h *consumerHandler) Setup(s sarama.ConsumerGroupSession) error {
	snap, err := h.store.Snapshot(s.Context(), 0, time.Now().UnixMilli()+1)
	if err != nil {
		return err
	}
	defer snap.Close(s.Context())
	h.mu.Lock()
	h.checkpoint = snap.Offsets()
	h.mu.Unlock()
	for topic, parts := range s.Claims() {
		for _, part := range parts {
			if topic != h.topic {
				newest, err := h.client.GetOffset(topic, part, sarama.OffsetNewest)
				if err != nil {
					return err
				}
				s.MarkOffset(topic, part, newest, "")
				s.ResetOffset(topic, part, newest, "")
				continue
			}
			if off, ok := h.checkpoint[part]; ok {
				s.MarkOffset(topic, part, off, "")
				s.ResetOffset(topic, part, off, "")
			}
		}
	}
	return nil
}
func (h *consumerHandler) Cleanup(sarama.ConsumerGroupSession) error { h.ring.Ready(false); return nil }
func (h *consumerHandler) ConsumeClaim(s sarama.ConsumerGroupSession, c sarama.ConsumerGroupClaim) error {
	if c.Topic() == h.topic {
		h.ring.Start(c.Partition(), max(0, c.InitialOffset()))
		h.ring.Ready(true)
	}
	for {
		select {
		case <-s.Context().Done():
			return nil
		case m, ok := <-c.Messages():
			if !ok {
				return nil
			}
			if c.Topic() != h.topic {
				if h.feeds != nil {
					h.feeds.Add(m)
				}
				s.MarkMessage(m, "")
				continue
			}
			format, car := "", ""
			for _, header := range m.Headers {
				switch string(header.Key) {
				case "car_type":
					car = string(header.Value)
				case "payload_format":
					format = string(header.Value)
				}
			}
			if car != "Artemis" {
				h.ring.Add(m.Partition, m.Offset, nil)
				s.MarkMessage(m, "")
				continue
			}
			if format != "artemis_updates_v1" {
				log.Printf("query Kafka: rejecting unsupported Artemis format %q at %s/%d/%d", format, m.Topic, m.Partition, m.Offset)
				h.ring.Reject(m.Partition, m.Offset)
				s.MarkMessage(m, "")
				continue
			}
			rows, err := h.decoder.Decode(m.Value)
			if err != nil {
				log.Printf("query Kafka: rejecting undecodable record %s/%d/%d: %v", m.Topic, m.Partition, m.Offset, err)
				h.ring.Reject(m.Partition, m.Offset)
				s.MarkMessage(m, "")
				continue
			}
			h.ring.Add(m.Partition, m.Offset, rows)
			s.MarkMessage(m, "")
		}
	}
}
