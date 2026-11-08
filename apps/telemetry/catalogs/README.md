# Per-car signal catalog archive

Published catalogs are runtime data, separate from replaceable Bazel outputs.
The current textproto bundle declares `car: "orion"`. Generate and publish it
from the repo root:

```bash
bazel run --config=local //tools/spec:publish_signals
```

The publisher creates `<car>/<schema_hash>.json` and atomically selects it in
`<car>/current.json`. Repeating the same publish changes no files. Old versions
are never overwritten or deleted. Reactivate one explicitly:

```bash
bazel run --config=local //tools/spec:publish_signals -- \
  --source /absolute/path/to/catalogs/orion/HASH.json
```

Use `--catalog-root /absolute/path` or `SIGNAL_CATALOG_DIR` for persistent server
storage; configure the viewer with the same root. Back up that directory: Git
ignores catalog JSON, and Bazel cannot reproduce unpublished historical input
versions by itself. No catalog is committed as a generated source file.

Each catalog contains `car`, a content-based `schema_version`, and `signals`.
The hash excludes version fields and includes car identity. Metadata changes
produce a new version; formatting and input-file paths do not. `current.json`
is an explicit selection, not a hash/timestamp ordering. Publishing an older
catalog intentionally selects it again.

Car folders are independent. A car with no published current pointer returns
503; a car with no folder returns 404. A requested version must exist inside
that car's folder, and the JSON car/version must match. Orion's definitions
are not a substitute for Artemis's missing catalog.

See the [generator](../../../lib/codegen/telemetry/README.md) and
[viewer](../analysis/database/viewer_tool/README.md#signal-discovery-v0).
