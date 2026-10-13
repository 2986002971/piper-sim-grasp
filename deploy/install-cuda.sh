#!/usr/bin/env bash
# Install the development toolkit, never the container host driver.
set -euo pipefail
cache="${1:?cache directory required}"
toolkit="$(/usr/bin/python3 - "$(dirname "$0")/../config/versions.lock.json" <<'PY'
import json,sys
print(json.load(open(sys.argv[1]))['cuda'].replace('.','-'))
PY
)"
mkdir -p "$cache"
curl -fL --retry 3 https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/cuda-keyring_1.1-1_all.deb -o "$cache/cuda-keyring.deb"
dpkg -i "$cache/cuda-keyring.deb"
apt-get update
apt-get install -y --no-remove --no-install-recommends "cuda-toolkit-$toolkit"
