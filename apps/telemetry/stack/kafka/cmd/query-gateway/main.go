package main

import (
	"context"
	"crypto/rand"
	"fmt"
	"log"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/LonghornRacingElectric/lhre/apps/telemetry/stack/kafka/gateway"
)

func env(name, fallback string) string {
	if v := os.Getenv(name); v != "" {
		return v
	}
	return fallback
}
func bytesEnv(name string) int64 {
	v := os.Getenv(name)
	if v == "" {
		return 0
	}
	n, err := strconv.ParseInt(v, 10, 64)
	if err != nil || n < 0 {
		log.Fatalf("%s must be a nonnegative byte count", name)
	}
	return n
}
func databaseURL(prefix, name string) string {
	if v := os.Getenv(prefix + "_DATABASE_URL"); v != "" {
		return v
	}
	password := env(prefix+"_DB_PASSWORD", os.Getenv("POSTGRES_PASSWORD"))
	u := url.URL{Scheme: "postgres", Host: env(prefix+"_DB_HOST", "db") + ":" + env(prefix+"_DB_PORT", "5432"), Path: "/" + name, User: url.UserPassword(env(prefix+"_DB_USER", "electric"), password)}
	return u.String() + "?sslmode=" + env(prefix+"_DB_SSLMODE", "disable")
}
func main() {
	if len(os.Args) > 1 && os.Args[1] == "--help" {
		fmt.Println("Artemis query gateway: QUERY_API_TOKEN, ARTEMIS_DATABASE_URL, TELEMETRY_DATABASE_URL; cache budgets QUERY_L1_BYTES, QUERY_RING_BYTES, QUERY_BASELINE_BYTES, QUERY_L2_BYTES; QUERY_CACHE_DIR, QUERY_PIN_BYTES, QUERY_PIN_DAYS")
		return
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	schema, err := gateway.LoadSchema(env("QUERY_STORAGE_CONTRACT", "/schema/storage_contract.json"), env("QUERY_SIGNAL_CATALOG", "/catalog/signals.json"))
	if err != nil {
		log.Fatal(err)
	}
	decoder, err := gateway.NewDecoder(env("QUERY_DESCRIPTOR", "/schema/schema_descriptor.pb"), schema)
	if err != nil {
		log.Fatal(err)
	}
	db, err := gateway.OpenDB(ctx, databaseURL("ARTEMIS", env("ARTEMIS_DB_NAME", "artemis")))
	if err != nil {
		log.Fatal(err)
	}
	defer db.Close()
	metadata, err := gateway.OpenDB(ctx, databaseURL("TELEMETRY", env("TELEMETRY_DB_NAME", "telemetry")))
	if err != nil {
		log.Fatal(err)
	}
	defer metadata.Close()
	token := os.Getenv("QUERY_API_TOKEN")
	if token == "" {
		log.Fatal("QUERY_API_TOKEN is required")
	}
	secret := []byte(os.Getenv("QUERY_CURSOR_SECRET"))
	if len(secret) == 0 {
		secret = make([]byte, 32)
		if _, err = rand.Read(secret); err != nil {
			log.Fatal(err)
		}
		log.Print("QUERY_CURSOR_SECRET unset; page tokens expire on restart")
	}
	topic := env("KAFKA_INPUT_TOPIC", "sensor_data")
	store := &gateway.Postgres{DB: db, Metadata: metadata, Topic: topic, StorageID: schema.StorageID}
	ringBytes := bytesEnv("QUERY_RING_BYTES")
	if ringBytes == 0 {
		log.Fatal("Set QUERY_RING_BYTES to a measured nonzero memory budget")
	}
	baselineBytes := bytesEnv("QUERY_BASELINE_BYTES")
	if baselineBytes == 0 {
		log.Fatal("Set QUERY_BASELINE_BYTES to a measured nonzero memory budget")
	}
	ring := gateway.NewRing(15*time.Minute, ringBytes)
	files := gateway.NewFiles(env("QUERY_CACHE_DIR", "/cache"), bytesEnv("QUERY_L2_BYTES"), schema)
	cache, err := gateway.NewCache(schema, ring, files, bytesEnv("QUERY_L1_BYTES"), baselineBytes, bytesEnv("QUERY_PIN_BYTES"), strings.Split(os.Getenv("QUERY_PIN_DAYS"), ","))
	if err != nil {
		log.Fatal(err)
	}
	defer cache.Close()
	liveTopics := strings.Split(env("QUERY_LIVE_TOPICS", "grafana_data_artemis,grafana_data_artemis_derived,car_status,board_health_state,board_health,fault_events"), ",")
	feeds := gateway.NewFeeds(liveTopics)
	engine := &gateway.Engine{Schema: schema, Store: store, Cache: cache, Secret: secret, Feeds: feeds}
	brokers := strings.Split(env("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092"), ",")
	go gateway.Consume(ctx, brokers, env("QUERY_KAFKA_GROUP", "artemis-query-gateway-v1"), topic, liveTopics, store, ring, decoder, feeds)
	go files.Run(ctx, store)
	server := &http.Server{Addr: env("QUERY_ADDR", ":8090"), Handler: engine.Handler(token), ReadTimeout: 30 * time.Second, ReadHeaderTimeout: 5 * time.Second, IdleTimeout: 60 * time.Second}
	shutdownDone := make(chan struct{})
	go func() {
		<-ctx.Done()
		shutdown, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = server.Shutdown(shutdown)
		_ = server.Close()
		close(shutdownDone)
	}()
	log.Printf("Artemis query gateway on %s", server.Addr)
	if err = server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Fatal(err)
	}
	<-shutdownDone
}
