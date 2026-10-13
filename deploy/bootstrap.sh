#!/usr/bin/env bash
# AutoDL minimal images may contain only Conda Python, absent from SSH's PATH.
set -euo pipefail
[[ "$EUID" = 0 ]] || { echo 'Run on the AutoDL server as root.' >&2; exit 1; }
source /etc/os-release
[[ "$ID" = ubuntu && "$VERSION_ID" = 22.04 && "$(uname -m)" = x86_64 ]] || {
  echo 'Ubuntu 22.04 x86_64 is required.' >&2; exit 1;
}
[[ "$#" = 1 ]] || { echo 'Usage: bash deploy/bootstrap.sh /absolute/machine.json' >&2; exit 1; }
if [[ ! -x /usr/bin/python3 ]]; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y --no-remove --no-install-recommends python3
fi
repo="$(cd "$(dirname "$0")/.." && pwd)"
exec /usr/bin/python3 "$repo/piper" --local --config "$1" deploy
