#!/usr/bin/env bash
# Install the system ROS environment; Isaac keeps its bundled Python 3.12 ROS libraries.
set -euo pipefail

if [[ "${1:-}" = --help ]]; then
  echo 'Usage: install-ros2-humble.sh [/absolute/ros-data-directory]'
  exit 0
fi
[[ "$EUID" = 0 ]] || { echo 'Run this installer as root on the remote server.' >&2; exit 1; }
source /etc/os-release
[[ "$ID" = ubuntu && "$VERSION_ID" = 22.04 ]] || { echo 'Ubuntu 22.04 is required.' >&2; exit 1; }
root="${1:?installation report directory required}"
[[ "$root" = /* && "$root" != / ]] || { echo 'Use an absolute data directory.' >&2; exit 1; }
umask 077
mkdir -p "$root/backups" "$root/ws/src"
backup="$root/backups/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$backup"
cp -a /etc/apt/sources.list /etc/apt/sources.list.d "$backup/"
for file in /etc/default/locale /etc/locale.gen; do
  [[ ! -f "$file" ]] || cp -a "$file" "$backup/"
done
exec > >(tee -a "$root/install.log") 2>&1
echo "APT/locale backup: $backup"
dpkg-query -W > "$root/packages-before.tsv"

# This GPU image ships with Ubuntu's proposed testing pocket enabled.
shopt -s nullglob
for file in /etc/apt/sources.list /etc/apt/sources.list.d/*.list; do
  sed -i '/^[[:space:]]*deb\(-src\)\?[[:space:]].*[[:space:]]jammy-proposed[[:space:]]/s/^/# Disabled for ROS 2 Humble: /' "$file"
done
# Keep the official embedded signing key, using the reachable domestic mirror.
# sed creates an administrator-owned copy instead of editing the package's symlink target.
use_ros_mirror() {
  if [[ -f /etc/apt/sources.list.d/ros2.sources ]]; then
    sed -i -e 's|^URIs: .*|URIs: https://mirrors.tuna.tsinghua.edu.cn/ros2/ubuntu|' \
      -e 's|^Types: .*|Types: deb|' \
      /etc/apt/sources.list.d/ros2.sources
  fi
}
use_ros_mirror
export DEBIAN_FRONTEND=noninteractive
apt-get -o APT::Update::Error-Mode=any update
apt-get install -y --no-remove --no-install-recommends curl ca-certificates locales
locale-gen en_US.UTF-8
update-locale LANG=en_US.UTF-8
export LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8

if ! dpkg-query -W -f='${Status}' ros2-apt-source 2>/dev/null | grep -q 'install ok installed'; then
  release="$(/usr/bin/python3 - "$(dirname "$0")/../config/versions.lock.json" <<'PYLOCK'
import json,sys
print(json.load(open(sys.argv[1]))['ros_apt_source'])
PYLOCK
)"
  curl -fL --retry 3 --connect-timeout 10 --max-time 120 \
    "https://github.com/ros-infrastructure/ros-apt-source/releases/download/$release/ros2-apt-source_$release.jammy_all.deb" \
    -o "$root/ros2-apt-source.deb"
  dpkg -i "$root/ros2-apt-source.deb"
fi
use_ros_mirror
apt-get -o APT::Update::Error-Mode=any update
packages=(ros-humble-ros-base ros-dev-tools ros-humble-rmw-fastrtps-cpp
          ros-humble-demo-nodes-cpp ros-humble-demo-nodes-py)
# Fail rather than allowing the resolver to remove system packages.
apt-get --simulate --no-remove install --no-install-recommends "${packages[@]}" \
  > "$root/install-dry-run.txt"
cat "$root/install-dry-run.txt"
apt-get install -y --no-remove --no-install-recommends "${packages[@]}"
if [[ ! -f /etc/ros/rosdep/sources.list.d/20-default.list ]]; then
  /usr/bin/rosdep init
fi
# Existing usable cache is part of the environment; avoid making every rebuild
# depend on GitHub availability. First deployment retries transient source timeouts.
if [[ ! -s /root/.ros/rosdep/sources.cache/index ]]; then
  for attempt in 1 2 3; do
    /usr/bin/rosdep update --rosdistro humble && break
    [[ "$attempt" != 3 ]] || exit 1
  done
fi
dpkg-query -W > "$root/packages-after.tsv"
dpkg-query -W -f='${Package} ${Version}\n' ros2-apt-source "${packages[@]}" \
  > "$root/ros-packages.txt"
/usr/bin/python3 --version
cat "$root/ros-packages.txt"
echo "ROS 2 Humble installed. Workspace: $root/ws"
echo 'Run commands through runtime/ros2-env.sh; leave the Isaac environment separate.'
