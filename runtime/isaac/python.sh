#!/usr/bin/env bash
# Use this boundary for all Isaac/OpenUSD work, including asset export and offline scene checks.
set -eo pipefail
isaac_env="${1:?Isaac environment required}"; shift
unset PYTHONPATH PYTHONHOME LD_LIBRARY_PATH LD_PRELOAD
unset AMENT_PREFIX_PATH CMAKE_PREFIX_PATH COLCON_PREFIX_PATH ROS_PACKAGE_PATH
unset CONDA_PREFIX CONDA_DEFAULT_ENV VIRTUAL_ENV
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
export ROS_DISTRO=humble RMW_IMPLEMENTATION=rmw_fastrtps_cpp PYTHONNOUSERSITE=1
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"
export LD_LIBRARY_PATH="$isaac_env/lib/python3.12/site-packages/isaacsim/exts/isaacsim.ros2.core/humble/lib"
export OMNI_KIT_ACCEPT_EULA=YES OMNI_KIT_ALLOW_ROOT=1
mdl="$isaac_env/lib/python3.12/site-packages/isaacsim/kit/mdl/core/Base"
export PXR_AR_DEFAULT_SEARCH_PATH="${PXR_AR_DEFAULT_SEARCH_PATH:+$PXR_AR_DEFAULT_SEARCH_PATH:}$mdl"
exec "$isaac_env/bin/python" "$@"
