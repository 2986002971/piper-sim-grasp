#!/usr/bin/env bash
# Install the selected grasp stack's amd64 dependencies, without upgrading Ubuntu.
set -eo pipefail
source_root="${1:?Usage: install-grasp-deps.sh MAIN_SOURCE DATA_DIR}"
data_root="${2:?Usage: install-grasp-deps.sh MAIN_SOURCE DATA_DIR}"
servo_root="${3:?isolated Servo source root required}"
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
[[ $(dpkg --print-architecture) = amd64 ]] || { echo 'This deployment targets amd64.' >&2; exit 2; }
[[ -f /opt/ros/humble/setup.bash ]] || { echo 'Install Humble first.' >&2; exit 2; }
mkdir -p "$data_root/dependencies"
exec > >(tee -a "$data_root/dependencies/install.log") 2>&1
source /opt/ros/humble/setup.bash
export DEBIAN_FRONTEND=noninteractive
apt-get update
# This image retained ICU's proposed runtime after the test source was disabled.
# Install its matching development files; keep the runtime used by Isaac intact.
icu_version="$(dpkg-query -W -f='${Version}' libicu70)"
icu_candidate="$(apt-cache policy libicu-dev | sed -n 's/^  Candidate: //p')"
icu_packages=()
if [[ "$icu_version" != "$icu_candidate" ]]; then
  [[ "$icu_version" = 70.1-2ubuntu1 ]] || { echo "Unexpected ICU version: $icu_version" >&2; exit 2; }
  for icu_package in icu-devtools libicu-dev; do
    archive="$data_root/dependencies/${icu_package}_${icu_version}_amd64.deb"
    curl --fail --location --retry 3 --retry-all-errors --output "$archive" \
      "https://repo.huaweicloud.com/ubuntu/pool/main/i/icu/${icu_package}_${icu_version}_amd64.deb"
    [[ "$(dpkg-deb -f "$archive" Version)" = "$icu_version" ]] || exit 2
    icu_packages+=("$archive")
  done
fi
base_packages=(build-essential cmake ccache libssl-dev libyaml-cpp-dev
  ros-humble-moveit-servo ros-humble-visp python3-pytest python3-numpy
  python3-scipy python3-opencv python3-yaml "${icu_packages[@]}")
apt-get -s install --no-install-recommends "${base_packages[@]}" > "$data_root/dependencies/base-dry-run.txt"
if grep -q '^Remv ' "$data_root/dependencies/base-dry-run.txt"; then
  echo 'Dependency resolution would remove packages.' >&2; exit 2
fi
apt-get install -y --no-remove --no-install-recommends "${base_packages[@]}"
python3 "$servo_root/src/third_party/moveit_servo_reference/build_overlay.py" --prepare-only
package_paths="$(python3 "$repo_root/deploy/main_project.py" "$source_root")"
mapfile -t paths <<< "$package_paths"
paths+=("$servo_root/build/servo_reference/src/moveit_servo")
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
