package main

import (
	"context"
	"testing"

	"github.com/IBM/sarama"
	pb "github.com/LonghornRacingElectric/lhre/apps/telemetry/stack/kafka/proto/bridge"
	"google.golang.org/grpc/metadata"
)

type captureProducer struct {
	sarama.SyncProducer
	messages []*sarama.ProducerMessage
}

func (p *captureProducer) SendMessage(message *sarama.ProducerMessage) (int32, int64, error) {
	p.messages = append(p.messages, message)
	return 0, 0, nil
}

func TestConnectionIdentityReachesRawKafkaHeader(t *testing.T) {
	previousRaw, previousGrafana := rawDataChan, grafanaDataChan
	defer func() { rawDataChan, grafanaDataChan = previousRaw, previousGrafana }()
	rawDataChan = make(chan SensorMessage, 2)
	grafanaDataChan = make(chan SensorMessage, 2)
	server := bridgeServer{}
	for _, session := range []string{"first", "reconnected"} {
		ctx := metadata.NewIncomingContext(context.Background(), metadata.Pairs("csv-session-id", session))
		_, err := server.SendSensorData(ctx, &pb.SensorDataRequest{Payload: []byte(session), CarType: "Orion"})
		if err != nil {
			t.Fatal(err)
		}
	}
	close(rawDataChan)
	producer := &captureProducer{}
	rawDataWorker(producer)
	if len(producer.messages) != 2 {
		t.Fatalf("got %d messages", len(producer.messages))
	}
	for index, session := range []string{"first", "reconnected"} {
		message := producer.messages[index]
		headers := map[string]string{}
		for _, header := range message.Headers {
			headers[string(header.Key)] = string(header.Value)
		}
		if message.Topic != "sensor_data" || headers["car_type"] != "Orion" || headers["csv_session_id"] != session {
			t.Fatalf("incorrect raw record: topic=%s headers=%v", message.Topic, headers)
		}
		payload, err := message.Value.Encode()
		if err != nil || string(payload) != session {
			t.Fatalf("payload changed: %q, %v", payload, err)
		}
	}
}
