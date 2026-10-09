# 服务端部署

本文是当前安装、版本、端口、配置与管理生命周期的唯一来源。历史结论见 [调查记录](history/streaming-investigation.md)，迁移见 [migration](migration.md)。
远端使用 Ubuntu、NVIDIA RTX GPU、Isaac Sim `6.1.0.0` / Python 3.12；管理机使用 Linux、Python ≥3.10、OpenSSH。
远端不需要 Node 或 Viewer 源码。当前验证使用 RTX 4090；原有 4090/5090 结果是历史证据，不能替代新实例验收。

## 1. 数据盘与 Isaac

以下命令在远端执行。把安装环境、模型缓存和日志放到仓库外的数据盘；安装及临时 wheel 解包会占用数十 GB，先用 `df -h` 确认空间。
示例假设仓库位于 `/root/autodl-tmp/piper-sim-grasp`，数据位于 `/root/autodl-tmp/piper-data`。换位置时同时更新私有配置。

```bash
apt-get update
apt-get install -y --no-install-recommends build-essential curl ca-certificates openssl \
  ffmpeg vulkan-tools libvulkan1 libsm6 libegl1 libxt6 iproute2 haproxy \
  libevent-dev libsqlite3-dev libssl-dev pkg-config
mkdir -p /root/autodl-tmp/piper-data/{pip-cache,pip-tmp,stream}
export PIP_CACHE_DIR=/root/autodl-tmp/piper-data/pip-cache
export TMPDIR=/root/autodl-tmp/piper-data/pip-tmp
conda create -y -p /root/autodl-tmp/isaacsim-6.1-env python=3.12 pip
/root/autodl-tmp/isaacsim-6.1-env/bin/python -m pip install \
  'isaacsim[all,extscache]==6.1.0.0' --extra-index-url https://pypi.nvidia.com
/root/autodl-tmp/isaacsim-6.1-env/bin/python -m pip install \
  -r /root/autodl-tmp/piper-sim-grasp/models/requirements.txt
```

`conda` 由当前 GPU 镜像提供；其他环境可用已安装的 Python 3.12 `-m venv` 创建同一路径。
可选离线抓取工具还需要 PyTorch/NumPy、PyYAML、目标瓶体 USD 和外部拟合包，见 [simulation](../simulation/README.md)。
日常串流不以这些研究工具为依赖。Isaac 和 NVIDIA EULA 适用；启动脚本沿用已接受许可的非交互启动环境。

```bash
nvidia-smi
ls -l /dev/nvidia*
VK_ICD_FILENAMES=/etc/vulkan/icd.d/nvidia_icd.json vulkaninfo --summary
```

若容器 NVIDIA ICD 指向 GLX 且无头 Vulkan 初始化失败，在仓库外创建 EGL ICD：

```bash
cat > /root/autodl-tmp/piper-data/nvidia-egl.json <<'JSON'
{"file_format_version":"1.0.0","ICD":{"library_path":"libEGL_nvidia.so.0","api_version":"1.3.0"}}
JSON
VK_ICD_FILENAMES=/root/autodl-tmp/piper-data/nvidia-egl.json vulkaninfo --summary
```

验证成功后，把这个绝对路径写入 `isaac.env.VK_ICD_FILENAMES`。不要覆盖系统 ICD；`nvidia-smi` 的 CUDA 版本也不是安装的 Toolkit 版本。

## 2. NVENC、coturn、HAProxy

```bash
cd /root/autodl-tmp/piper-sim-grasp
gcc -shared -fPIC -O2 -o /root/autodl-tmp/piper-data/stream/libnvenc_fix.so server/nvenc_ioctl_filter.c -ldl
bash server/install-turn.sh /root/autodl-tmp/piper-data/turn
/usr/sbin/haproxy -vv
```

coturn 固定 `4.18.0`，安装脚本校验源包 SHA-256，不注册系统 turnserver daemon。
HAProxy 使用系统安装的可执行文件；若 apt 自动启用了默认 daemon，确认它没有占用本项目端口。
NVENC 过滤器根据容器可见 GPU 设备节点过滤宿主机枚举，不能写死设备 minor；新实例仍要验证实际编码。
原有 HAProxy 私有 wrapper 可以复用，但其运行库路径属于该实例；迁移优先使用系统包并记录实际版本。

## 3. 模型、支架与首次生成

机械臂、100 mm 夹爪、D405 描述和网格来自 [AgileX 官方仓库](https://github.com/agilexrobotics/piper_isaac_sim)，固定提交和逐文件哈希在 [sources.lock.json](../models/sources.lock.json)。
RealSense 的来源提交及许可证单独记录。仅下载需要的文件到仓库外，失败或校验不符会阻止场景启动。

**第一次下载 D405 官方支架需要登录语雀。** 打开 [官方模型页面](https://agilexsupport.yuque.com/staff-hso6mo/tetraz/km95una15ctk4b17?singleDoc)，
登录并下载 `intel realsense D405标准相机支架.zip`，解出 `405相机支架.STEP`。
官方附件内 STEP 已与原场景源文件核对为逐字节一致；ZIP/STEP 哈希、稳定附件地址均记录在 source lock 中。
匿名附件请求会返回登录页；不要把登录页当作 ZIP，也不要把 Cookie、账户或临时签名地址提交到仓库。

```bash
mkdir -p /root/autodl-tmp/piper-data/models/bracket
cp '/path/to/405相机支架.STEP' /root/autodl-tmp/piper-data/models/bracket/d405_mount_source.step
python3 - <<'PY'
import hashlib
from pathlib import Path
p=Path('/root/autodl-tmp/piper-data/models/bracket/d405_mount_source.step')
assert hashlib.sha256(p.read_bytes()).hexdigest() == '09e16217aae82f9f4c349b1a563d972f6b6164c7b178209bd0582149059fa02e'
PY
/root/autodl-tmp/isaacsim-6.1-env/bin/python -m pip install \
  -r /root/autodl-tmp/piper-sim-grasp/models/requirements-step-conversion.txt
```

也可用 `models/prepare.py --data-dir … --download-only --bracket-cookie-file /private/cookies.txt` 下载官方支架；
Cookie 文件须在仓库外、权限 0600，下载后自行删除。Cookie 不会写入 provenance。
正常首次启动默认自动准备模型；支架无有效缓存且未能下载时明确提示并生成无支架场景。
缓存有 STEP 时需要上述 OpenCascade 转换依赖。外部 `PIPER_BRACKET=/path/to/mount.step` 或 `.stl` 可以覆盖（网格按毫米处理，装配位姿仍是旧几何拟合估计）。

预下载及离线生成：

```bash
export OMNI_KIT_ACCEPT_EULA=YES OMNI_KIT_ALLOW_ROOT=1
/root/autodl-tmp/isaacsim-6.1-env/bin/python models/prepare.py \
  --data-dir /root/autodl-tmp/piper-data/models --download-only
/root/autodl-tmp/isaacsim-6.1-env/bin/python models/prepare.py \
  --data-dir /root/autodl-tmp/piper-data/models --offline
```

`--download-only` 不需要启动 Isaac。跨机器迁移整个 `sources/` 和 `bracket/` 缓存。
生成目录按源文件、生成器、标定及支架哈希区分；发布前检查完整引用，成功的 USD 才写 `complete.json`，重用前逐文件校验哈希。
手眼为注明来源的示例，可用 `PIPER_HANDEYE` 替换。几何和坐标检查不代表 D405 RGB-D 模拟传感器已接通。
显式设置 `PIPER_SCENE_USD` 时直接验证并使用原完整 USD，无需重新下载或装配；payload/mesh 目录要完整。

## 4. 配置与平台入口

在平台建立 TCP 转发：公网信令端口 → 容器 `6006`；公网 TURN 端口 → 容器 loopback `6008`。
HAProxy 在 loopback `6008` 接收平台连接，使用容器源地址连接 coturn 的 `CONTAINER_IPV4:16010`。
浏览器与 coturn 间承载 TCP；coturn allocation 到同容器 Isaac 是 UDP `47998`，allocation 范围 `49152–49215`。
没有媒体 SSH 隧道，也不使用 PROXY protocol 或 STUN 重写。

将 [server.example.json](../config/server.example.json) 复制到管理机仓库外，设 0600：

```bash
umask 077
mkdir -p /path/to/private
cp config/server.example.json /path/to/private/server.json
chmod 600 /path/to/private/server.json
```

更新 SSH、远端源码、Isaac 环境、数据盘及平台 host/port。用 `secrets.token_hex(16)`、`secrets.token_hex(32)` 在私有配置中生成 TURN 用户名/密码，避免打印完整配置。
默认 `tcp_ingress: true`；TURN 不需要客户端 LAN、relay token 或证书。
`turn.relay_ip` 可选，由远端网卡/路由检测；不得沿用旧容器地址。`STREAM_PUBLIC_IP` 由管理入口覆盖为检测地址。
coturn 限制 peer 为同容器 IPv4，`no-udp` 关闭浏览器 UDP 监听，`no-tcp-relay` 禁止 peer TCP allocation；它们没有关闭 allocation 到 Isaac 的 UDP。
allocation quota 为 4，不表示四个人可以同时控制同一个 Isaac。

把服务端整个 checkout 上传远端；`server/` 和 `models/` 的相对层级要保留。管理机也持有服务端 checkout，使用 SSH 控制远端。

## 5. 完整启停、状态与导出

在 Linux 管理机执行：

```bash
python3 server/manage.py preflight --config /path/to/private/server.json
python3 server/manage.py start --config /path/to/private/server.json
```

`start` 保持前台管理，不自行 daemonize。可用持久终端，或管理机用户服务：

```bash
systemd-run --user --unit=piper-turn --collect --working-directory="$PWD" \
  /usr/bin/python3 "$PWD/server/manage.py" start --config /path/to/private/server.json
journalctl --user -u piper-turn.service -n 30 --no-pager
python3 server/manage.py status --config /path/to/private/server.json
python3 server/manage.py export-client --config /path/to/private/server.json --output /path/to/private/client.json
```

预检要求待启动端口空闲，不在运行栈上重复执行。状态分别显示 `configuration_present`、`components_started`、`isaac_ready` 和 `manager_active`。
配置在 Isaac 就绪前就能存在；`PIPER_ISAAC_READY` 表示场景/应用初始化完成，视频及控制仍须浏览器验收。
导出只含信令、媒体和 ICE 配置；包括 TURN 凭据，须可信交付。它不含管理 SSH、服务器路径或 relay 私钥。
配置副本不会随服务停止自动删除，也不是服务器就绪证明。实例地址或凭据变动后重新导出并交付。

`raw_dir/run-*` 中有本轮 Isaac/coturn/HAProxy 日志，远端 `PIPER_STREAM_ROOT/model-prepare.log` 有模型准备日志。
日志与原始浏览器数据都在仓库外。不要全局 `pkill`；用拥有 state/starttime 的管理入口正常停止：

```bash
python3 server/manage.py stop --config /path/to/private/server.json
python3 server/manage.py status --config /path/to/private/server.json
```

停止或启动失败时先关闭 SSH stdin，remote worker 清理自己的进程组，再删本轮配置、runtime/state；保留历史私有日志。
管理机锁防止同一个 `raw_dir` 重复启动；远端预检拒绝端口冲突。管理迁移仍须先由旧管理机正常停止，不能用多个 raw_dir 争用实例。

## 6. 客户端、生命周期与验收

客户端运行方式在独立 `piper-stream-viewer` 仓库的 README；仅 Python 本机页面服务与普通浏览器，无需管理 SSH、Node、TUN 或 Python 媒体转发。
同一 Isaac 只交接一份活跃会话；旧客户端先断开，再连接新客户端。
验收包括持续视频、实际远端键鼠响应、主动断开、重连、刷新、停止清理及 [relay 回退](../legacy/README.md)。
历史延迟结论见 [短测](isaac-turn-latency-quick.md)，不等于长期可靠性保证。

当前服务依赖 Linux 管理进程及 SSH，失联、管理进程退出或任一子进程退出会清理栈。远端 worker 固定 4500 秒（75 分钟）watchdog，
HAProxy 空闲超时也是 75 分钟；systemd 不会取消 watchdog。用户临时 unit 不等同于开机自启或无人值守持久服务。
页面是本机 HTTP、信令 WS、TURN 无 TLS；WebRTC DTLS/SRTP 不意味着所有入口都有 HTTPS/TLS。
统一托管及 HTTPS/WSS 网关、鉴权、容器 supervisor、长期可靠性验收留作后续工作。
