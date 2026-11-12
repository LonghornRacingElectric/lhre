package main

import (
	"github.com/LonghornRacingElectric/lhre/apps/telemetry/stack/kafka/grafana"
	"github.com/grafana/grafana-plugin-sdk-go/backend/datasource"
	"log"
)

func main() {
	if err := datasource.Manage("lhre-artemis-datasource", grafana.New, datasource.ManageOpts{}); err != nil {
		log.Fatal(err)
	}
}
