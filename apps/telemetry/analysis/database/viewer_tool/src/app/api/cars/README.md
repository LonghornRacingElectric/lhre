# Car signal discovery

`GET /api/cars/{car}/signals` serves generated signal metadata. It uses the
existing `/api/*` middleware exclusion. The current spec generates Artemis's
catalog; any car with its own published archive can use the endpoint.

The optional `schema_version` query parameter selects an exact catalog-content
hash inside the requested car's folder; omission follows that car's `current.json`.
Responses are signal arrays, with the selected version also in
`X-Schema-Version`. Errors use `{error: {code, message}}`. As with the v0 API,
API-key authentication is not implemented here.

See the [catalog reader](../../../lib/signals/README.md) for deployment and
validation rules.
