#!/usr/bin/env bash
# Run through ros2-env.sh; build the selected external grasp stack and integration.
set -eo pipefail
source_root="${1:?Usage: build-grasp.sh MAIN_SOURCE DATA_DIR}"
data_root="${2:?Usage: build-grasp.sh MAIN_SOURCE DATA_DIR}"
servo_root="${3:?isolated Servo source root required}"
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
python3 "$servo_root/src/third_party/moveit_servo_reference/build_overlay.py"
mkdir -p "$data_root/install"
ln -sfn "$servo_root/install/servo_reference" "$data_root/install/servo"
source "$(readlink -f "$data_root/install/servo")/local_setup.bash"
paths=(
  "$source_root/src/nankai_robocup_interfaces"
  "$source_root/src/perception/nankai_robocup_perception_interfaces"
  "$source_root/src/perception/nankai_robocup_perception_core"
  "$source_root/src/perception/nankai_robocup_object_perception"
  "$source_root/src/nankai_robocup_visp_pbvs"
  "$source_root/src/w_moveit_servo"
  "$source_root/src/piper/piper_x_driver_cpp"
  "$source_root/src/manipulation/nankai_robocup_grasp_geometry")
colcon --log-base "$data_root/logs/main-build" build --base-paths "${paths[@]}" \
  --build-base "$data_root/build/main" --install-base "$data_root/install/main" \
  --symlink-install --parallel-workers 2 --event-handlers console_cohesion+ \
  --cmake-args -DCMAKE_BUILD_TYPE=RelWithDebInfo \
  -DCMAKE_C_COMPILER_LAUNCHER=ccache -DCMAKE_CXX_COMPILER_LAUNCHER=ccache \
  "-DTENSORRT_INCLUDE_DIR=$sdk/usr/include/x86_64-linux-gnu" \
  "-DTENSORRT_LIBRARY=$sdk/usr/lib/x86_64-linux-gnu/libnvinfer.so" \
  "-DCUDAToolkit_ROOT=/usr/local/cuda-$cuda_version"
source "$data_root/install/main/local_setup.bash"
colcon --log-base "$data_root/logs/integration-build" build --base-paths "$repo_root/ros2/nankai_sim_integration" \
  --build-base "$data_root/build/integration" --install-base "$data_root/install/integration" --symlink-install
python3 "$servo_root/src/third_party/moveit_servo_reference/image/verify.py" \
  --root "$data_root/install/servo" --source-dir "$servo_root/src/third_party/moveit_servo_reference"
ldd "$data_root/install/main/nankai_robocup_perception_core/lib/libnankai_robocup_fixed_shape_tensorrt.so"
