# 本地 Docker 基础仿真

镜像基于 `osrf/ros:humble-desktop`，保留系统 Python 3.10 和 ROS Humble，
在 `/opt/isaac-env` 安装独立 Python 3.12 与版本清单指定的 Isaac Sim。
Isaac 的 PyTorch 依赖会在独立 Python 环境中安装其选定的 CUDA 用户态库。
此镜像包含基础设备、SSH、HAProxy、coturn 和 NVENC 兼容库；
外部主项目另需 CUDA 12.8 开发工具链与 TensorRT SDK。
串流地址与凭据按[串流网络文档](streaming-network.md)配置。
当前启动器使用 headless 模式，Desktop 镜像不会自动打开 Isaac 窗口。

## 构建

从仓库根目录执行：

```bash
docker build --network=host --progress=plain -t piper-sim-grasp:humble-isaac6.1 .
```

构建会联网下载数十 GB 软件。普通 Python 包默认使用清华源，可通过
`--build-arg PIP_INDEX_URL=https://pypi.org/simple` 替换，Isaac wheel 保留 NVIDIA 索引。
pip 下载缓存由 BuildKit 保存，失败重试可复用；构建阶段不访问 GPU 或启动 Isaac。
镜像使用 Isaac 所需的 EULA 与 root 运行环境变量，运行前须接受 NVIDIA 对应许可。

## GPU 与容器

创建容器前，从仓库配置准备本机副本：

```bash
mkdir -p local
cp config/machine.docker.example.json local/machine.json
cp config/profiles/7up.yaml local/7up.docker.yaml
chmod 600 local/machine.json
```

机器配置保留 `"profile": "7up"`。在 `local/7up.docker.yaml` 中，将以下三个字段
改为容器内的实际资源路径；下文按新版离线导出包的目录结构使用：

```yaml
scene_usd: /data/piper/source/7up/scene.usda
robot_urdf: /data/piper/source/7up/robot.urdf
assembly_urdf: /data/piper/source/7up/assembly.urdf
```

保留 profile 的其余模型、相机与运行参数。容器将这个本机副本只读挂载到
`config/profiles/7up.yaml`，仓库中的原始 profile 保持远程版本。

宿主机需要可用的 NVIDIA 驱动和
[NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)。
以下命令使用 Toolkit 生成的 CDI 设备，可先用 `nvidia-ctk cdi list` 核对。
先验证 Docker 能访问 GPU；`nvidia-smi` 成功后还需单独验证 Vulkan。

```bash
docker volume create piper-sim-data
docker run -d --name piper-sim-local --init --network=host \
  --device=nvidia.com/gpu=all --shm-size=2g \
  --mount type=volume,source=piper-sim-data,target=/data/piper \
  --mount "type=bind,source=$PWD/local/machine.json,target=/private/machine.json,readonly" \
  --mount "type=bind,source=$PWD/local/7up.docker.yaml,target=/workspace/piper-sim-grasp/config/profiles/7up.yaml,readonly" \
  piper-sim-grasp:humble-isaac6.1
docker exec piper-sim-local nvidia-smi
docker exec piper-sim-local vulkaninfo --summary
docker exec piper-sim-local ./piper --local --config /private/machine.json check unit
```

容器中的项目源码来自构建时的副本。源码修改后重新构建镜像；依赖层可复用。
持久化数据在 `piper-sim-data` 卷，独立于源码和镜像中的 Isaac 环境。
容器使用宿主网络，SSH 监听宿主的 `0.0.0.0:2222`；Isaac 信令和 TURN/TCP
也直接使用宿主端口，启动前需确保配置的端口空闲。ROS 节点使用机器配置中的 domain ID。
容器默认以前台 sshd 为主进程；停止仿真请先执行 `piper stop`，再停止容器。

## SSH 管理

首次启动自动生成独立 SSH 主机密钥，保存在数据卷的 `/data/piper/private/ssh`；
重新创建容器继续使用同一个卷时保留主机身份。镜像不包含主机密钥或客户端私钥。
将已有管理客户端的**公钥文件**写入授权文件；下列首次配置命令会替换该授权文件：

```bash
docker cp /absolute/path/management_key.pub \
  piper-sim-local:/data/piper/private/ssh/authorized_keys
docker exec piper-sim-local chown root:root /data/piper/private/ssh/authorized_keys
docker exec piper-sim-local chmod 600 /data/piper/private/ssh/authorized_keys
docker exec piper-sim-local ssh-keygen -lf /data/piper/private/ssh/ssh_host_ed25519_key.pub
ssh -i /absolute/path/management_key -p 2222 root@127.0.0.1 \
  /workspace/piper-sim-grasp/piper --local --config /private/machine.json status
```

第一次连接时将 SSH 提示的主机指纹与上一步输出核对。仅允许 root 公钥认证，
密码和键盘交互认证均关闭；从其他机器连接时把 `127.0.0.1` 换成宿主可达地址。
客户端私钥始终留在客户端。统一管理使用项目机器配置的 `ssh` 字段，具体命令见
[操作文档](operation.md)。

## Tailscale 远程会话

本机管理配置保存在 `local/machine.remote.json`，可将副本交给已授权的管理端。
`repo_path`、`data_root`、`isaac_env` 均为远端容器路径；SSH 连接到宿主可达地址的
2222 端口。管理端先核对主机指纹并完成公钥授权；统一入口使用非交互 SSH。
每个管理端的公钥在 `authorized_keys` 中各占一行，增加管理端时保留已有授权。

本机 `streaming.host` 使用 Tailscale 域名，`streaming.relay_ip` 使用 Tailscale IPv4。
后者须存在于 host 网络中，用于 coturn、Isaac 媒体广告和 HAProxy 后端源地址；
避免默认路由选中代理 TUN 地址。HAProxy 固定监听 `0.0.0.0:6008`，Isaac 信令监听 6006。
保留 TURN/TCP 媒体链路，具体边界见[串流网络约束](streaming-network.md)。

管理端从本项目根目录操作，远程命令不加 `--local`：

```bash
./piper --config local/machine.remote.json doctor
./piper --config local/machine.remote.json start --stream
```

`start` 占用前台并拥有 SSH 会话，另一个终端等待 `status` 的
`ros_devices_ready` 和 `stream_initialized` 均为 true 后导出 Viewer 配置：

```bash
./piper --config local/machine.remote.json status
./piper --config local/machine.remote.json export-client --output local/client.json
./piper --config local/machine.remote.json stop
```

Viewer 在客户端读取导出的 `client.json`，页面仍由客户端 loopback HTTP 服务提供。
`client.json` 包含 TURN 凭据，存放在私有目录，不放进静态页面目录或版本控制。

coturn 和 NVENC 兼容库预构建在镜像 `/opt/piper-sdk`，入口脚本在
`/data/piper/sdk` 建立链接，所以挂载数据卷不会遮蔽镜像组件，重复启动也可复用。
如果已有同名真实 SDK 目录，入口脚本会报错保留原目录，需先核对旧部署再迁移。

## 场景与启动

准备由当前 [assets export 流程](assets.md#可选离线打包)生成的完整场景包，
包含 `scene.usda`、`robot.urdf`、`assembly.urdf`、`bundle.json` 及所有引用依赖。
场景必须与当前 profile 的 ROS 模型、`link6` 命名、相机安装变换和初始姿态配套；
旧版仅含 `scene/` 的迁移包不能直接代替新版模型资源。运行时只读校验 USD 与 ROS
运动学及相机变换，不再自动修正旧模型。

将以下归档源路径替换为本机实际路径，解包到新资源目录并核对清单。
`--keep-old-files` 拒绝覆盖已有资源；已有部署应先核对目录，避免混用不同装配。
新版不再提供 `assets import` 或自动登记，build/start/check 直接读取 profile 的源文件路径：

```bash
docker cp /absolute/path/7up-v1.tar.gz piper-sim-local:/tmp/7up-v1.tar.gz
docker exec piper-sim-local mkdir -p /data/piper/source/7up
docker exec piper-sim-local tar -xzf /tmp/7up-v1.tar.gz -C /data/piper/source/7up --keep-old-files
docker exec piper-sim-local python3 -c 'from models.bundle import verify; verify("/data/piper/source/7up")'
docker exec piper-sim-local ./piper --local --config /private/machine.json build
docker exec piper-sim-local ./piper --local --config /private/machine.json doctor
docker exec piper-sim-local ./piper --local --config /private/machine.json check devices
docker exec -it piper-sim-local ./piper --local --config /private/machine.json start --headless
```

基础设备模式保持 `main_project: null`，build 只构建设备程序。启动和验收各自生成
配置快照；运行中的会话不随 profile 修改而变化，修改后需 stop/start。

另一个终端查看状态或停止项目：

```bash
docker exec piper-sim-local ./piper --local --config /private/machine.json status
docker exec piper-sim-local ./piper --local --config /private/machine.json stop
```

镜像已包含基础依赖，无需再次执行裸机 `bootstrap.sh` 或 `piper deploy`。
首次启动会生成渲染缓存。RTX 4080 Laptop 的 12 GB 显存低于 Isaac Sim 6.1
官方规格表的 16 GB，具体场景的渲染和设备验收需在当前机器实测。
