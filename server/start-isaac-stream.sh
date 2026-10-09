#!/usr/bin/env bash
set -euo pipefail

STREAM_ROOT="${PIPER_STREAM_ROOT:-$HOME/piper-stream}"
ISAAC_ENV="${ISAAC_ENV:?Set ISAAC_ENV to the Isaac Sim 6.1 virtual environment path}"
export STREAM_PUBLIC_IP="${STREAM_PUBLIC_IP:?Set STREAM_PUBLIC_IP to the browser-reachable media address}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$STREAM_ROOT"
MODEL_ARGS=(--data-dir "${PIPER_DATA_DIR:-$STREAM_ROOT/model-data}" --isaac-python "$ISAAC_ENV/bin/python")
[[ "${PIPER_OFFLINE:-0}" = 1 ]] && MODEL_ARGS+=(--offline)
[[ -n "${PIPER_BRACKET:-}" ]] && MODEL_ARGS+=(--bracket "$PIPER_BRACKET")
[[ -n "${PIPER_HANDEYE:-}" ]] && MODEL_ARGS+=(--handeye "$PIPER_HANDEYE")
[[ -n "${PIPER_SCENE_USD:-}" ]] && MODEL_ARGS+=(--scene "$PIPER_SCENE_USD")
export OMNI_KIT_ACCEPT_EULA=YES OMNI_KIT_ALLOW_ROOT=1
export VK_ICD_FILENAMES="${VK_ICD_FILENAMES:-/etc/vulkan/icd.d/nvidia_icd.json}"
[[ -f "$STREAM_ROOT/libnvenc_fix.so" ]] || { echo 'Missing NVENC filter; compile it first' >&2; exit 2; }
export LD_PRELOAD="$STREAM_ROOT/libnvenc_fix.so"
"$ISAAC_ENV/bin/python" "$SCRIPT_DIR/../models/prepare.py" "${MODEL_ARGS[@]}" > "$STREAM_ROOT/model-prepare.log"
PIPER_SCENE_USD="$(tail -n 1 "$STREAM_ROOT/model-prepare.log")"
[[ -f "$PIPER_SCENE_USD" ]] || { echo 'Model preparation did not produce a valid scene' >&2; exit 2; }

export PIPER_STREAM_ROOT="$STREAM_ROOT"
export ISAAC_ENV
export PIPER_SCENE_USD
export CUDA_CACHE_PATH="$STREAM_ROOT/cuda-cache"
export ROS_DISTRO=humble
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
export LD_LIBRARY_PATH="$ISAAC_ENV/lib/python3.12/site-packages/isaacsim/exts/isaacsim.ros2.core/humble/lib:${LD_LIBRARY_PATH:-}"
export NVENC_FIX_DEBUG="$STREAM_ROOT/webrtc/nvenc-enumeration.log"

mkdir -p "$STREAM_ROOT/home" "$STREAM_ROOT/cuda-cache" "$STREAM_ROOT/webrtc"
export XDG_CACHE_HOME="$STREAM_ROOT/home/.cache"
export XDG_CONFIG_HOME="$STREAM_ROOT/home/.config"

exec "$ISAAC_ENV/bin/python" "$SCRIPT_DIR/run_isaac_stream.py"
