#!/usr/bin/env bash

set -euo pipefail

loader="$1"
image="$2"
executable="$3"

"$loader"
docker image inspect "$image" >/dev/null
docker run --rm --entrypoint /bin/sh "$image" -c "test -x '$executable'"
