# GG plot processor

The optional GG plot processor decodes raw sensor packets, filters lateral and
longitudinal acceleration, and publishes points for the live GG visualization.

Targets follow the standard `gg_plot_binary`, `_image`, `_load`, `_push`, and
`_smoke_test` contract. It consumes the same selected schema bundle as ingest,
so a schema-changing board branch must use a matching server image until
version negotiation exists.
