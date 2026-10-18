#!/usr/bin/env bash

set -euo pipefail

source "$(dirname "$0")/server_devtool.sh"

OS="Darwin"
HOME="/Users/test"

df() {
    printf '%s\n' \
        "Filesystem 1024-blocks Used Available Capacity Mounted on" \
        "/dev/disk 20971520 8388608 12582912 40% /System/Volumes/Data"
}

[[ "$(docker_root)" == "$HOME" ]]
[[ "$(free_gb)" == "12" ]]

OS="Linux"
docker_root() {
    printf '/var/lib/docker\n'
}
[[ "$(free_gb)" == "12" ]]
