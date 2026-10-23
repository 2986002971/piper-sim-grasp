#!/usr/bin/env bash
# Run through ros2-env.sh; build the external capability and our simulated driver.
set -eo pipefail
source_root="${1:?Usage: build-grasp.sh MAIN_SOURCE DATA_DIR}"
data_root="${2:?Usage: build-grasp.sh MAIN_SOURCE DATA_DIR}"
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cuda_version="$(/usr/bin/python3 - "$repo_root/config/versions.lock.json" <<'PY'
import json, sys
print(json.load(open(sys.argv[1]))['cuda'])
PY
)"
sdk="$data_root/sdk/tensorrt"
[[ -f "$sdk/sdk.json" ]] || { echo 'Install the private TensorRT SDK first.' >&2; exit 2; }
export LD_LIBRARY_PATH="$sdk/usr/lib/x86_64-linux-gnu:${LD_LIBRARY_PATH:-}"
export CMAKE_BUILD_PARALLEL_LEVEL=2 MAKEFLAGS=-j2
export CCACHE_DIR="$data_root/cache/ccache"
package_paths="$(python3 "$repo_root/deploy/main_project.py" "$source_root")"
mapfile -t paths <<< "$package_paths"
colcon --log-base "$data_root/logs/main-build" build --base-paths "${paths[@]}" \
  --build-base "$data_root/build/main" --install-base "$data_root/install/main" \
  --symlink-install --parallel-workers 2 --event-handlers console_cohesion+ \
  --cmake-args -DCMAKE_BUILD_TYPE=RelWithDebInfo \
  -DCMAKE_C_COMPILER_LAUNCHER=ccache -DCMAKE_CXX_COMPILER_LAUNCHER=ccache \
  "-DTENSORRT_INCLUDE_DIR=$sdk/usr/include/x86_64-linux-gnu" \
  "-DTENSORRT_LIBRARY=$sdk/usr/lib/x86_64-linux-gnu/libnvinfer.so" \
  "-DCUDAToolkit_ROOT=/usr/local/cuda-$cuda_version"
source "$data_root/install/main/local_setup.bash"
colcon --log-base "$data_root/logs/devices-build" build --base-paths "$repo_root/ros2/piper_sim_devices" \
  --build-base "$data_root/build/devices" --install-base "$data_root/install/devices" \
  --symlink-install --cmake-args "-DNANKAI_MAIN_SOURCE=$source_root" -DCMAKE_BUILD_TYPE=RelWithDebInfo
source "$data_root/install/devices/local_setup.bash"
colcon --log-base "$data_root/logs/integration-build" build --base-paths "$repo_root/ros2/nankai_sim_integration" \
  --build-base "$data_root/build/integration" --install-base "$data_root/install/integration" --symlink-install
ldd "$data_root/install/main/nankai_robocup_perception_core/lib/libnankai_robocup_fixed_shape_tensorrt.so"
