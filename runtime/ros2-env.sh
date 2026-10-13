#!/usr/bin/env bash
# Execute a command in system Humble, or open an isolated interactive ROS shell.
set -eo pipefail

[[ -f /opt/ros/humble/setup.bash ]] || { echo 'Install system ROS 2 Humble first.' >&2; exit 2; }
unset PYTHONHOME PYTHONPATH LD_LIBRARY_PATH LD_PRELOAD
unset AMENT_PREFIX_PATH COLCON_PREFIX_PATH CMAKE_PREFIX_PATH ROS_PACKAGE_PATH
unset CONDA_PREFIX CONDA_DEFAULT_ENV CONDA_PROMPT_MODIFIER CONDA_SHLVL VIRTUAL_ENV
unset ROS_DISTRO ROS_VERSION ROS_PYTHON_VERSION
unset FASTRTPS_DEFAULT_PROFILES_FILE FASTDDS_DEFAULT_PROFILES_FILE ROS_DISCOVERY_SERVER CYCLONEDDS_URI
export PATH=/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/usr/local/sbin
export LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8
export PYTHONNOUSERSITE=1
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"
source /opt/ros/humble/setup.bash

if [[ "$#" = 0 ]]; then
  exec /bin/bash --noprofile --norc -i
fi
exec "$@"
