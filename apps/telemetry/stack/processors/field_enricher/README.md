# Field enricher

The field enricher consumes a car's Grafana JSON topic, evaluates configured
derived fields, and publishes an enriched topic. It is part of the core because
downstream dashboards depend on those stable derived names.

Targets follow the standard `field_enricher_binary`, `_image`, `_load`,
`_push`, and `_smoke_test` contract. The image packages `config/*.yaml` at the
absolute path used by `ENRICHER_CONFIG`; add new car configurations to that
directory rather than mounting an untracked file.

Run it with `./apps/telemetry/stack/server_devtool.sh build field_enricher` or
load it directly with
`bazel run //apps/telemetry/stack/processors/field_enricher:field_enricher_load`.
