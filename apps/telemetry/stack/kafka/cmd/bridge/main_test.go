package main

import (
	"context"
	"errors"
	"testing"

	"github.com/IBM/sarama"
	pb "github.com/LonghornRacingElectric/lhre/apps/telemetry/stack/kafka/proto/bridge"
)

type recordingProducer struct {
	sarama.SyncProducer
	message *sarama.ProducerMessage
	failure error
}

func (p *recordingProducer) SendMessage(message *sarama.ProducerMessage) (int32, int64, error) {
	p.message = message
	return 0, 1, p.failure
}

func TestRawFailureIsReported(t *testing.T) {
	producer := &recordingProducer{failure: errors.New("broker unavailable")}
	server := &bridgeServer{producer: producer}
	response, err := server.SendSensorData(context.Background(), &pb.SensorDataRequest{Payload: []byte{1}, CarType: "Orion"})
	if err != nil || response.Success {
		t.Fatalf("unacknowledged raw publication reported success: %v %v", response, err)
	}
}

func TestRawAcknowledgedEvenWhenGrafanaQueueFull(t *testing.T) {
	old := grafanaDataChan
	grafanaDataChan = make(chan SensorMessage)
	defer func() { grafanaDataChan = old }()
	producer := &recordingProducer{}
	server := &bridgeServer{producer: producer}
	response, err := server.SendSensorData(context.Background(), &pb.SensorDataRequest{Payload: []byte{1, 2}, CarType: "Orion"})
	if err != nil || !response.Success || producer.message.Topic != "sensor_data" {
		t.Fatalf("acknowledged raw publication failed: %v %v", response, err)
	}
	payload, _ := producer.message.Value.Encode()
	if string(payload) != string([]byte{1, 2}) || string(producer.message.Headers[0].Value) != "Orion" {
		t.Fatal("raw protobuf or car metadata changed")
	}
}

type recordingAdmin struct {
	sarama.ClusterAdmin
	entries map[string]sarama.IncrementalAlterConfigsEntry
}

func (a *recordingAdmin) ListTopics() (map[string]sarama.TopicDetail, error) {
	return map[string]sarama.TopicDetail{"sensor_data": {}}, nil
}

func (a *recordingAdmin) IncrementalAlterConfig(resource sarama.ConfigResourceType, name string, entries map[string]sarama.IncrementalAlterConfigsEntry, validate bool) error {
	a.entries = entries
	return nil
}

func TestExistingRawTopicRetentionIsUpdated(t *testing.T) {
	t.Setenv("SENSOR_DATA_RETENTION_MS", "9999999")
	t.Setenv("SENSOR_DATA_RETENTION_BYTES", "1234567")
	admin := &recordingAdmin{}
	if err := ensureTopicExists(admin, "sensor_data"); err != nil {
		t.Fatal(err)
	}
	if *admin.entries["retention.ms"].Value != "9999999" || *admin.entries["retention.bytes"].Value != "1234567" {
		t.Fatal("existing raw topic did not receive configured retention")
	}
}
