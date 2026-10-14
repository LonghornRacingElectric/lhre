# Adding Grafana Dashboards
telemetry.lhre.org/grafana/ (dm any of the Telemetry members for password access)

Dashboards in `analysis/database/dashboards/<car>/<mode>/*.json` are auto-provisioned by Grafana when `stack/server_devtool.sh` starts ingest (options 1/2/3/4/Q/W/Z).

## Realtime Dashboards
1. Create a regular new dashboard
2. Select "Real Time Data" as Data Source
3. Set the Topic to "grafana_data_<car_name>" (e.g. "grafana_data_angelique")
4. If all the fields appear on the dashboard:
- Time Series / If there are no default options to select only one value:
    1. Add an Override in the dashboard settings.
    2. Select "Fields with name matching regex".
    3. Type `^(?!name_of_property$).+` (i.e. `^(?!gps_heading$).+`). This essentially selects all the fields but the one you are looking for.
    4. Add an Override Property
    5. "Series > Hide in Area". This enables us to hide all the datapoints that we don't need.

`orion/real-time/orion_live_gps.json` (Live GPS) samples the stream at
`sampleIntervalMs: 20` for the 50 Hz GPS experiment (the receiver does 10/50/100
Hz). The kafka datasource does not take a template variable there, so the
value is literal in each of the five panels; to go back to 20 Hz, replace
`"sampleIntervalMs": 20,` with `200,` across the file.

## Retrospective Dashboards
1. Create a regular new dashboard.
2. Select the adequate datasource (Angelique, Telemetry...).
3. Write the corresponding SQL query (recommend asking an LLM to generate it based on your needs).
