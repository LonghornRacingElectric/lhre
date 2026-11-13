# Car signal discovery

`GET /api/cars/artemis/signals` serves the gateway's deployed signal metadata.
It requires a viewer session or the query API bearer token. The optional
`schema_version` must match the deployed catalog. The viewer returns the signal
array and identifies its version in `X-Schema-Version`.

Historical car catalogs retain the filesystem reader: `schema_version` selects
an exact archived hash, otherwise `current.json` selects it. Those metadata-only
reads retain their existing public access. Errors use `{error: {code, message}}`.

See the [catalog reader](../../../lib/signals/README.md) for deployment and
validation rules.
