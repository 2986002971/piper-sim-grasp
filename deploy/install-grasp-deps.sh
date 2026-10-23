#!/usr/bin/env bash
# Install the selected grasp stack's amd64 dependencies, without upgrading Ubuntu.
set -eo pipefail
source_root="${1:?Usage: install-grasp-deps.sh MAIN_SOURCE DATA_DIR}"
data_root="${2:?Usage: install-grasp-deps.sh MAIN_SOURCE DATA_DIR}"
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
[[ $(dpkg --print-architecture) = amd64 ]] || { echo 'This deployment targets amd64.' >&2; exit 2; }
[[ -f /opt/ros/humble/setup.bash ]] || { echo 'Install Humble first.' >&2; exit 2; }
mkdir -p "$data_root/dependencies"
exec > >(tee -a "$data_root/dependencies/install.log") 2>&1
source /opt/ros/humble/setup.bash
export DEBIAN_FRONTEND=noninteractive
apt-get update
base_packages=(build-essential cmake ccache libeigen3-dev libyaml-cpp-dev
  ros-humble-control-msgs ros-humble-rclcpp-action ros-humble-urdf
  python3-pytest python3-numpy python3-scipy python3-opencv python3-yaml)
apt-get -s install --no-install-recommends "${base_packages[@]}" > "$data_root/dependencies/base-dry-run.txt"
if grep -q '^Remv ' "$data_root/dependencies/base-dry-run.txt"; then
  echo 'Dependency resolution would remove packages.' >&2; exit 2
fi
apt-get install -y --no-remove --no-install-recommends "${base_packages[@]}"
package_paths="$(python3 "$repo_root/deploy/main_project.py" "$source_root")"
mapfile -t paths <<< "$package_paths"
# Isaac provides the camera topics; the real-camera launch is not used.
rosdep install --simulate --from-paths "${paths[@]}" --ignore-src --rosdistro humble \
  --skip-keys realsense2_camera -y > "$data_root/dependencies/rosdep-dry-run.txt"
python3 - "$data_root/dependencies/rosdep-dry-run.txt" > "$data_root/dependencies/rosdep-packages.txt" <<'PY'
import pathlib, shlex, sys
packages = set()
for line in pathlib.Path(sys.argv[1]).read_text().splitlines():
    words = shlex.split(line)
    if 'apt-get' in words and 'install' in words:
        packages.update(w for w in words[words.index('install')+1:] if not w.startswith('-'))
    elif words and ('pip' in words[0] or 'pip3' in words[0]):
        raise RuntimeError('Unexpected pip dependency in system ROS deployment: ' + line)
print('\n'.join(sorted(packages)))
PY
mapfile -t rosdep_packages < "$data_root/dependencies/rosdep-packages.txt"
if (( ${#rosdep_packages[@]} )); then
  apt-get -s install --no-install-recommends "${rosdep_packages[@]}" > "$data_root/dependencies/rosdep-apt-dry-run.txt"
  if grep -q '^Remv ' "$data_root/dependencies/rosdep-apt-dry-run.txt"; then
    echo 'rosdep dependencies would remove packages.' >&2; exit 2
  fi
  apt-get install -y --no-remove --no-install-recommends "${rosdep_packages[@]}"
fi
rosdep check --from-paths "${paths[@]}" --ignore-src --rosdistro humble --skip-keys realsense2_camera
dpkg-query -W -f='${Package}\t${Version}\n' 'ros-humble-*' 'python3-opencv' \
  > "$data_root/dependencies/installed.tsv"
echo 'Grasp dependencies installed.'
