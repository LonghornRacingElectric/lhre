# Car signal discovery

`GET /api/cars/{car}/signals` serves generated signal metadata. It uses the
existing `/api/*` middleware exclusion. Orion is the only catalog currently
generated.

The optional `schema_version` query parameter selects a current or archived
spec hash. Responses are signal arrays, with the selected version also in
`X-Schema-Version`. Errors use `{error: {code, message}}`. As with the v0 API,
API-key authentication is not implemented here.

See the [catalog reader](../../../lib/signals/README.md) for deployment and
validation rules.
