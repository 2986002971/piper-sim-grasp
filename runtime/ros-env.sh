#!/usr/bin/env bash
# System Python 3.10 + selected external build outputs; no Isaac Python libraries.
set -eo pipefail
data_root="${1:?data root required}"; shift
scope="${1:?devices or main required}"; shift
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ "${PIPER_CLEAN_ROS:-}" != 1 ]]; then
  export PIPER_CLEAN_ROS=1
  exec bash "$script_dir/ros2-env.sh" bash "$0" "$data_root" "$scope" "$@"
fi
if [[ "$scope" = main ]]; then
  source "$(readlink -f "$data_root/install/servo")/local_setup.bash"
  source "$data_root/install/main/local_setup.bash"
  export LD_LIBRARY_PATH="$data_root/sdk/tensorrt/usr/lib/x86_64-linux-gnu:${LD_LIBRARY_PATH:-}"
fi
source "$data_root/install/devices/local_setup.bash"
if [[ "$scope" = main ]]; then source "$data_root/install/integration/local_setup.bash"; fi
export PIPER_SIM_REPO="$(cd -- "$script_dir/.." && pwd)"
export PIPER_ROS_DATA="$data_root"
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
export PYTHONDONTWRITEBYTECODE=1
exec "$@"
