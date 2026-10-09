# 拆分迁移与恢复

当前部署事实仅在 [deployment.md](deployment.md) 维护。迁移顺序是备份、迁移私有配置、部署新入口、正常停止旧栈、验收、保留恢复副本。
备份、凭据、原始截图和录屏不在两个公开仓库。不要在验收前删除旧部署目录。

## 旧入口与新入口

| 旧入口 | 新入口 |
|---|---|
| `streaming/experiments/network.py --mode turn` | `server/manage.py`，默认 TURN |
| `streaming/start-isaac-stream.sh` | `server/start-isaac-stream.sh` |
| `streaming/experiments/turn_remote.py`、`remote_worker.py` | `server/` 同名文件 |
| `streaming/experiments/viewer.py` | 独立 Viewer 仓库 `viewer.py` |
| `streaming/experiments/viewer/` | 独立 Viewer 仓库 `web/` |
| `browser_check.py`、`quick_latency.py`、浏览器探针 | 独立 Viewer 仓库 `diagnostics/`、`web/public/` |
| 原 TURN `raw_dir` runtime | 管理入口 `export-client` 生成独立 `client.json` |
| vendored/generated 模型、CAD、完整 USD | 外部资产包、`models/prepare.py` 下载/生成缓存 |
| Python relay | `legacy/` 的隔离回退入口 |

## 配置迁移

在 Linux 管理机，从旧私有配置生成新的服务端配置。不会在终端打印凭据。

```bash
python3 server/migrate_config.py --source /private/old/config-turn.json \
  --server /private/new/server.json
```

需要保留 relay 时加 `--include-legacy`；默认不会携带 LAN、TLS/token 或 Ligolo 字段。
若已有有效旧 runtime，可加 `--runtime /private/old/viewer-runtime.json --client /private/new/client.json`。
没有 runtime 时，可用已检测的容器地址 `--relay-ip`，或先启动新栈再 `export-client`。
导出的客户端配置只含信令、媒体、ICE 和明确启用的 relay 客户端字段。
修改新服务端配置中的远端 `server/` 路径、`raw_dir` 以及数据盘路径；迁移器不会猜测新实例地址。

新管理机：迁移服务端 checkout 和私有配置、建立 SSH 认证并核对 host key；重新导出客户端配置。
不要复制旧 PID state、lock 或 runtime 来冒充活跃管理进程。旧管理机必须先正常 stop，然后新管理机 preflight/start。

新实例：按部署指南安装 Isaac/Vulkan/NVENC/coturn/HAProxy，保留 server/models 层级；
平台重建 6006 信令与 6008 TURN TCP 转发。复制验证过的完整 USD，或复制整个 `sources/`、`bracket/` 缓存后离线生成。
重新检测容器 IPv4，更新 SSH/公网端口，生成新 TURN 凭据；不复制依赖旧运行库路径的 HAProxy wrapper。

新同事电脑：交付独立 Viewer 已构建包和新导出的私有客户端配置，按 Viewer README 启动 Python。
不交付管理 SSH、服务端源码、服务器路径、relay 私钥或上游模型。更新配置时替换外部 `client.json`，服务每次请求会重读它。
连接新客户端前先断开旧活跃会话。Windows/macOS 实际媒体行为按验证记录标注，不能从标准库可移植性推断已实测。

## 切换、验收与恢复

1. 保存旧目录、配置、完整场景和校验表；使用旧入口正常 stop，确认端口释放。
2. 新入口先复用原 `PIPER_SCENE_USD`，验证预检/启停、视频及键鼠、断开重连、刷新、停止清理。
3. 再独立验证首次下载、离线缓存和自动生成；不要把模型生成故障误归因于 TURN。
4. 若新入口失败，先用新入口 stop 并确认无残留；用备份目录中的旧 `network.py start --config … --mode turn` 或旧 relay 入口恢复。
   恢复使用原私有配置及完整相对场景引用，不能同时启两套栈。
5. 凭据轮换后重新交付客户端配置；旧导出不会自动失效或更新。

已构建客户端包通过专用打包脚本产生，包含静态页面、标准库服务和许可声明，不包含配置或数据。
两个仓库可独立 checkout；客户端无 Linux 管理模块，服务端无客户端构建依赖。
当前仍为管理 SSH 生命周期和 75 分钟 watchdog，长期托管迁移需要另外设计。
