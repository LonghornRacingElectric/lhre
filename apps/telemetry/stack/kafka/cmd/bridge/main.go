package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/IBM/sarama"
	artemis "github.com/LonghornRacingElectric/lhre/apps/telemetry/stack/kafka/proto/artemis"
	pb "github.com/LonghornRacingElectric/lhre/apps/telemetry/stack/kafka/proto/bridge"
	"google.golang.org/grpc"
	"google.golang.org/grpc/keepalive"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/reflect/protoreflect"
)

type SensorMessage struct {
	Payload []byte
	CarType string
	Format  string
}

var (
	grafanaDataChan = make(chan SensorMessage, 10000)
)

type bridgeServer struct {
	pb.UnimplementedBridgeServiceServer
	producer sarama.SyncProducer
	// schema_id of the contract this bridge was built with ("" if unknown).
	deployedSchema string
}

// Consumers pick a decoder from this header (stack/ingest/decode_router.py).
const fingerprintHeader = "schema_fingerprint"

// deployedSchemaID reads the schema_id packaged into the image at /schema.
func deployedSchemaID() string {
	path := os.Getenv("SCHEMA_CONTRACT")
	if path == "" {
		path = "/schema/schema_contract.json"
	}
	data, err := os.ReadFile(path)
	if err != nil {
		log.Printf("No schema contract at %s; records carry no default fingerprint", path)
		return ""
	}
	var contract struct {
		SchemaID string `json:"schema_id"`
	}
	if err := json.Unmarshal(data, &contract); err != nil || len(contract.SchemaID) != 64 {
		log.Printf("Unreadable schema contract at %s; records carry no default fingerprint", path)
		return ""
	}
	return contract.SchemaID
}

func (s *bridgeServer) SendSensorData(ctx context.Context, req *pb.SensorDataRequest) (*pb.SensorDataResponse, error) {
	if req.CarType != "Artemis" || req.PayloadFormat != "artemis_updates_v1" {
		return &pb.SensorDataResponse{Success: false, Message: "requires Artemis artemis_updates_v1"}, nil
	}
	if _, err := nativeSnapshot(req.Payload, &artemis.ArtemisSensorData{}, map[string]int64{}); err != nil {
		return &pb.SensorDataResponse{Success: false, Message: err.Error()}, nil
	}
	log.Printf("Received SensorDataRequest: CarType=%s, PayloadSize=%d", req.CarType, len(req.Payload))
	msg := SensorMessage{
		Payload: req.Payload,
		CarType: req.CarType,
		Format:  req.PayloadFormat,
	}
	fingerprint := req.SchemaFingerprint
	if fingerprint == "" {
		fingerprint = s.deployedSchema
	}
	headers := []sarama.RecordHeader{{Key: []byte("car_type"), Value: []byte(msg.CarType)}, {Key: []byte("payload_format"), Value: []byte(msg.Format)}}
	if fingerprint != "" {
		headers = append(headers, sarama.RecordHeader{Key: []byte(fingerprintHeader), Value: []byte(fingerprint)})
	}

	// A successful RPC means Kafka acknowledged the raw packet. The client may
	// retry an uncertain RPC; db_writer admits each packet only once.
	_, _, err := s.producer.SendMessage(&sarama.ProducerMessage{
		Topic:   "sensor_data",
		Value:   sarama.ByteEncoder(msg.Payload),
		Headers: headers,
	})
	if err != nil {
		return &pb.SensorDataResponse{Success: false, Message: err.Error()}, nil
	}

	// The live JSON feed is decoded with this bridge's own schema, so only
	// payloads written in that schema go into it. Others stay raw in
	// sensor_data for the decode router.
	if fingerprint != "" && s.deployedSchema != "" && fingerprint != s.deployedSchema {
		log.Printf("Schema %s is not this bridge's deployed schema; kept out of the live feed", fingerprint)
		return &pb.SensorDataResponse{Success: true, Message: "persisted to Kafka"}, nil
	}
	select {
	case grafanaDataChan <- msg:
	default:
		log.Println("Warning: grafanaDataChan full, dropping message")
	}

	return &pb.SensorDataResponse{Success: true, Message: "persisted to Kafka"}, nil
}

func getKafkaBroker() string {
	if broker := os.Getenv("KAFKA_BROKER"); broker != "" {
		return broker
	}
	if os.Getenv("IN_DOCKER") != "" {
		return "kafka:9092"
	}
	return "localhost:29092"
}

func newKafkaProducer() (sarama.SyncProducer, error) {
	config := sarama.NewConfig()
	config.Producer.Return.Successes = true
	config.Producer.RequiredAcks = sarama.WaitForAll
	config.Producer.Timeout = 5 * time.Second
	config.Net.WriteTimeout = 5 * time.Second
	config.Net.ReadTimeout = 5 * time.Second
	config.Producer.Retry.Max = 3

	broker := getKafkaBroker()
	return sarama.NewSyncProducer([]string{broker}, config)
}

// newKafkaAdmin creates a ClusterAdmin for topic management
func newKafkaAdmin() (sarama.ClusterAdmin, error) {
	config := sarama.NewConfig()
	config.Version = sarama.V3_6_0_0 // Use latest stable version
	config.Admin.Timeout = 10 * time.Second
	broker := getKafkaBroker()
	return sarama.NewClusterAdmin([]string{broker}, config)
}

func strPtr(s string) *string { return &s }

// Raw telemetry is a persistence backlog; size retention for the outage budget.
func rawRetention() map[string]*string {
	age := os.Getenv("SENSOR_DATA_RETENTION_MS")
	if age == "" {
		age = "604800000"
	}
	size := os.Getenv("SENSOR_DATA_RETENTION_BYTES")
	if size == "" {
		size = "8589934592"
	}
	return map[string]*string{"retention.ms": strPtr(age), "retention.bytes": strPtr(size)}
}

// ensureTopicExists creates the topic if it doesn't already exist
func ensureTopicExists(admin sarama.ClusterAdmin, topic string) error {
	// Retry logic for listing topics
	var topics map[string]sarama.TopicDetail
	var err error
	maxRetries := 5

	for i := 0; i < maxRetries; i++ {
		topics, err = admin.ListTopics()
		if err == nil {
			break
		}
		log.Printf("Attempt %d/%d: Failed to list topics: %v", i+1, maxRetries, err)
		time.Sleep(2 * time.Second)
	}

	if err != nil {
		return fmt.Errorf("failed to list topics after %d retries: %w", maxRetries, err)
	}

	if _, ok := topics[topic]; ok {
		if topic == "sensor_data" {
			entries := map[string]sarama.IncrementalAlterConfigsEntry{}
			for key, value := range rawRetention() {
				entries[key] = sarama.IncrementalAlterConfigsEntry{Operation: sarama.IncrementalAlterConfigsOperationSet, Value: value}
			}
			return admin.IncrementalAlterConfig(sarama.TopicResource, topic, entries, false)
		}
		log.Printf("Topic %s already exists", topic)
		return nil
	}

	log.Printf("Creating topic %s...", topic)
	detail := &sarama.TopicDetail{
		NumPartitions:     1,
		ReplicationFactor: 1,
		ConfigEntries: map[string]*string{
			"retention.ms":   strPtr("604800000"), // 7 days
			"cleanup.policy": strPtr("delete"),
		},
	}

	if topic == "sensor_data" {
		for key, value := range rawRetention() {
			detail.ConfigEntries[key] = value
		}
	}
	err = admin.CreateTopic(topic, detail, false)
	if err != nil {
		return fmt.Errorf("failed to create topic %s: %w", topic, err)
	}

	log.Printf("Successfully created topic %s", topic)

	// Verify topic was created
	time.Sleep(1 * time.Second)
	topics, err = admin.ListTopics()
	if err != nil {
		log.Printf("Warning: Could not verify topic creation: %v", err)
	} else if _, ok := topics[topic]; ok {
		log.Printf("Verified: Topic %s exists in Kafka", topic)
	} else {
		log.Printf("Warning: Topic %s not found after creation", topic)
	}

	return nil
}

// Seed the same generated fields used by the live Artemis converter.
func defaultSeedMap() map[string]interface{} {
	data := artemisToMap(artemisSeedData())
	data["time"] = time.Now().UnixMilli()
	data["car_type"] = "Artemis"
	return data
}

// Init method for kafka grafana datasource
func seedTopic(producer sarama.SyncProducer, topic string) {
	jsonData, err := json.Marshal(defaultSeedMap())
	if err != nil {
		log.Printf("Failed to marshal seed data: %v", err)
		return
	}

	log.Printf("Sending seed message to topic %s: %s", topic, string(jsonData))

	msg := &sarama.ProducerMessage{
		Topic:     topic,
		Value:     sarama.ByteEncoder(jsonData),
		Timestamp: time.Now(),
	}
	partition, offset, err := producer.SendMessage(msg)
	if err != nil {
		log.Printf("Failed to seed topic %s: %v", topic, err)
	} else {
		log.Printf("Successfully seeded topic %s (partition: %d, offset: %d)", topic, partition, offset)
	}
}

// grafanaDataWorker deserializes protobuf and publishes JSON to car-specific grafana_data topics
func grafanaDataWorker(producer sarama.SyncProducer) {
	state := &artemis.ArtemisSensorData{}
	clocks := map[string]int64{}
	for msg := range grafanaDataChan {
		var jsonData []byte
		var err error
		var snapshot *artemis.ArtemisSensorData
		snapshot, err = nativeSnapshot(msg.Payload, state, clocks)
		if err == nil {
			data := artemisToMap(snapshot)
			data["car_type"] = "Artemis"
			jsonData, err = json.Marshal(data)
		}
		if err != nil {
			log.Printf("Error deserializing protobuf for car %s: %v", msg.CarType, err)
			continue
		}

		topic := "grafana_data_artemis"

		kafkaMsg := &sarama.ProducerMessage{
			Topic: topic,
			Value: sarama.ByteEncoder(jsonData),
			Headers: []sarama.RecordHeader{
				{Key: []byte("car_type"), Value: []byte(msg.CarType)},
			},
		}

		partition, offset, err := producer.SendMessage(kafkaMsg)
		if err != nil {
			log.Printf("Error sending to topic %s: %v", topic, err)
		} else {
			log.Printf("Successfully sent message to topic %s (partition: %d, offset: %d)", topic, partition, offset)
		}
	}
}

// Copy only observed fields, including protobuf defaults and empty lists.
func nativeSnapshot(payload []byte, state *artemis.ArtemisSensorData, clocks map[string]int64) (*artemis.ArtemisSensorData, error) {
	batch := &artemis.TelemetryBatch{}
	if err := proto.Unmarshal(payload, batch); err != nil {
		return nil, err
	}
	if batch.FormatVersion != 1 || len(batch.Updates) == 0 || len(batch.Updates) > 64 {
		return nil, fmt.Errorf("invalid Artemis batch")
	}
	for _, update := range batch.Updates {
		if update.Values == nil || update.SampleTimeNs <= 0 || update.Values.Time != update.SampleTimeNs/1000000 || len(update.UpdatedFields) == 0 {
			return nil, fmt.Errorf("invalid Artemis timestamp or presence")
		}
		root := update.Values.ProtoReflect()
		seen := map[string]bool{}
		for _, path := range update.UpdatedFields {
			group := root.Descriptor().Fields().ByNumber(protoreflect.FieldNumber(path.Group))
			if group == nil || group.Message() == nil || !root.Has(group) {
				return nil, fmt.Errorf("invalid Artemis group")
			}
			field := group.Message().Fields().ByNumber(protoreflect.FieldNumber(path.Field))
			key := fmt.Sprintf("%d.%d", path.Group, path.Field)
			if field == nil || seen[key] {
				return nil, fmt.Errorf("invalid Artemis field")
			}
			seen[key] = true
			if update.SampleTimeNs >= clocks[key] {
				target := state.ProtoReflect().Mutable(group).Message()
				value := root.Get(group).Message().Get(field)
				if field.IsList() {
					list := target.Mutable(field).List()
					list.Truncate(0)
					source := value.List()
					for i := 0; i < source.Len(); i++ {
						list.Append(source.Get(i))
					}
				} else {
					target.Set(field, value)
				}
				clocks[key] = update.SampleTimeNs
			}
		}
		if update.Values.Time > state.Time {
			state.Time = update.Values.Time
		}
	}
	return state, nil
}

// statsFromInt32Slice returns average, min and max for a slice of int32.
// If the slice is empty the returned avg will be 0 and min/max will be 0.
func statsFromInt32Slice(vals []int32) (avg float64, min int32, max int32) {
	if len(vals) == 0 {
		return 0, 0, 0
	}
	var sum int64
	min = vals[0]
	max = vals[0]
	for _, v := range vals {
		sum += int64(v)
		if v < min {
			min = v
		}
		if v > max {
			max = v
		}
	}
	avg = float64(sum) / float64(len(vals))
	return avg, min, max
}

// statsFromFloat32Slice returns average, min and max for a slice of float32.
// If the slice is empty the returned avg will be 0 and min/max will be 0.
func statsFromFloat32Slice(vals []float32) (avg float64, min float32, max float32) {
	if len(vals) == 0 {
		return 0, 0, 0
	}
	var sum float64
	min = vals[0]
	max = vals[0]
	for _, v := range vals {
		sum += float64(v)
		if v < min {
			min = v
		}
		if v > max {
			max = v
		}
	}
	avg = sum / float64(len(vals))
	return avg, min, max
}

// statsFromFloat32SliceNonZero is like statsFromFloat32Slice but skips zero
// entries. The firmware uses 0 as a sentinel for cells that did not report in
// this packet (cells_v is a fixed-length, zero-padded array), so a naive min
// would otherwise collapse to 0. If there are no non-zero values it returns 0, 0, 0.
func statsFromFloat32SliceNonZero(vals []float32) (avg float64, min float32, max float32) {
	var sum float64
	n := 0
	for _, v := range vals {
		if v == 0 {
			continue
		}
		if n == 0 || v < min {
			min = v
		}
		if n == 0 || v > max {
			max = v
		}
		sum += float64(v)
		n++
	}
	if n == 0 {
		return 0, 0, 0
	}
	return sum / float64(n), min, max
}

func artemisToMap(msg *artemis.ArtemisSensorData) map[string]interface{} {
	output := schemaFields(msg.ProtoReflect())
	if dynamics := msg.Dynamics; dynamics != nil && len(dynamics.Gps) >= 2 {
		output["latitude"] = dynamics.Gps[0]
		output["longitude"] = dynamics.Gps[1]
	}
	if pack := msg.Pack; pack != nil {
		output["power_kw"] = float64(pack.HvPackV) * float64(pack.HvC) / 1000.0
		if len(pack.CellsV) > 0 {
			average, minimum, maximum := statsFromFloat32SliceNonZero(pack.CellsV)
			output["avg_cell_v_stat"] = average
			output["max_cell_v"] = maximum
			output["min_cell_v"] = minimum
			output["cell_v_delta"] = float64(maximum - minimum)
		}
		if len(pack.CellsTemps) > 0 {
			average, minimum, maximum := statsFromFloat32Slice(pack.CellsTemps)
			output["avg_cell_temp_stat"] = average
			output["max_cell_temp"] = maximum
			output["min_cell_temp"] = minimum
		}
	}
	return output
}

func main() {
	log.Println("Starting Kafka Bridge Service...")

	cars := []string{"artemis"}

	// Create admin and ensure topics exist
	admin, err := newKafkaAdmin()
	if err != nil {
		log.Fatalf("Failed to create Kafka admin: %v", err)
	}
	defer admin.Close()

	for _, car := range cars {
		topic := fmt.Sprintf("grafana_data_%s", car)
		if err := ensureTopicExists(admin, topic); err != nil {
			log.Fatalf("Failed to ensure %s topic exists: %v", topic, err)
		}
	}

	if err := ensureTopicExists(admin, "sensor_data"); err != nil {
		log.Fatalf("Failed to ensure sensor_data topic exists: %v", err)
	}

	// List all topics for verification
	allTopics, err := admin.ListTopics()
	if err != nil {
		log.Printf("Warning: Could not list all topics: %v", err)
	} else {
		log.Printf("Available Kafka topics (%d):", len(allTopics))
		for topicName := range allTopics {
			log.Printf("  - %s", topicName)
		}
	}

	// Give Kafka a moment to propagate topic metadata
	log.Println("Waiting for topic metadata to propagate...")
	time.Sleep(3 * time.Second)

	producer, err := newKafkaProducer()
	if err != nil {
		log.Fatalf("Failed to create Kafka producer: %v", err)
	}
	defer producer.Close()

	// Seed topics to ensure they are viewable in grafana
	for _, car := range cars {
		seedTopic(producer, fmt.Sprintf("grafana_data_%s", car))
	}

	// Start worker goroutines
	go grafanaDataWorker(producer)

	// Start gRPC server
	listenAddr := os.Getenv("BRIDGE_ADDR")
	if listenAddr == "" {
		listenAddr = ":50051"
	}

	lis, err := net.Listen("tcp", listenAddr)
	if err != nil {
		log.Fatalf("Failed to listen: %v", err)
	}

	grpcServer := grpc.NewServer(grpc.KeepaliveEnforcementPolicy(keepalive.EnforcementPolicy{
		MinTime:             10 * time.Second,
		PermitWithoutStream: true,
	}))
	pb.RegisterBridgeServiceServer(grpcServer, &bridgeServer{producer: producer, deployedSchema: deployedSchemaID()})

	// Graceful shutdown
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)

	go func() {
		<-sigChan
		log.Println("Shutting down...")
		grpcServer.GracefulStop()
		close(grafanaDataChan)
	}()

	log.Printf("gRPC server listening on %s", listenAddr)
	if err := grpcServer.Serve(lis); err != nil {
		log.Fatalf("Failed to serve: %v", err)
	}
}
