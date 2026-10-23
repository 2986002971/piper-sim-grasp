FROM osrf/ros:humble-desktop

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ARG DEBIAN_FRONTEND=noninteractive
ARG PIP_INDEX_URL=https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple

ENV LANG=en_US.UTF-8 \
    LC_ALL=en_US.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility,graphics,video \
    OMNI_KIT_ACCEPT_EULA=YES \
    OMNI_KIT_ALLOW_ROOT=1 \
    UV_PYTHON_INSTALL_DIR=/opt/python

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential cmake ccache curl ca-certificates locales \
      python3-pip python3-yaml python3-numpy python3-scipy python3-opencv python3-pytest \
      ffmpeg vulkan-tools libvulkan1 libsm6 libegl1 libxt6 iproute2 \
      ros-humble-rmw-fastrtps-cpp ros-humble-tf2-ros ros-humble-robot-state-publisher \
      ros-humble-sensor-msgs ros-humble-trajectory-msgs ros-humble-launch-ros \
    && locale-gen en_US.UTF-8 \
    && rm -rf /var/lib/apt/lists/*

# Keep Humble on system Python 3.10; Isaac uses its own Python 3.12 and ROS libraries.
RUN python3 -m pip install --no-cache-dir --index-url "$PIP_INDEX_URL" uv==0.13.1 \
    && uv python install 3.12 \
    && uv venv --seed --python 3.12 /opt/isaac-env

COPY config/versions.lock.json /tmp/piper-versions.json
COPY models/requirements.txt /tmp/piper-model-requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    version="$(python3 -c 'import json; print(json.load(open("/tmp/piper-versions.json"))["isaac"])')" \
    && /opt/isaac-env/bin/python -m pip install --timeout 120 --retries 10 \
      --index-url "$PIP_INDEX_URL" --extra-index-url https://pypi.nvidia.com \
      "isaacsim[all,extscache]==$version" -r /tmp/piper-model-requirements.txt \
    && /opt/isaac-env/bin/python -m pip check

RUN apt-get update && apt-get install -y --no-install-recommends \
      openssh-server haproxy libevent-dev libsqlite3-dev libssl-dev pkg-config \
    && rm -f /etc/ssh/ssh_host_* \
    && rm -rf /var/lib/apt/lists/*

COPY deploy/install-turn.sh /tmp/piper-build/deploy/install-turn.sh
COPY config/versions.lock.json /tmp/piper-build/config/versions.lock.json
COPY runtime/streaming/nvenc_ioctl_filter.c /tmp/piper-build/nvenc_ioctl_filter.c
RUN bash /tmp/piper-build/deploy/install-turn.sh /opt/piper-sdk/turn \
    && mkdir -p /opt/piper-sdk/nvenc \
    && gcc -shared -fPIC -Wall -Wextra -O2 \
      -o /opt/piper-sdk/nvenc/libnvenc_fix.so /tmp/piper-build/nvenc_ioctl_filter.c -ldl \
    && rm -rf /opt/piper-sdk/turn/source /tmp/piper-build

WORKDIR /workspace/piper-sim-grasp
COPY . .
RUN bash runtime/ros2-env.sh python3 -c 'import rclpy, yaml, numpy, scipy, cv2' \
    && /opt/isaac-env/bin/python -I -c \
      'import importlib.metadata as m, json, sys; v=json.load(open("config/versions.lock.json")); assert m.version("isaacsim")==v["isaac"]; assert ".".join(map(str,sys.version_info[:2]))==v["isaac_python"]' \
    && bash runtime/isaac/python.sh /opt/isaac-env \
      -m unittest discover -s tests -p test_isaac_mount.py -v

ENTRYPOINT ["bash", "/workspace/piper-sim-grasp/runtime/docker-entrypoint.sh"]
CMD ["/usr/sbin/sshd", "-D", "-e", "-f", "/run/piper-sshd.conf"]
