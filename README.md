# Piper Sim Grasp

为持续开发中的 NankaiRoboCup2026 提供 AutoDL 仿真设备：Piper、D405、ROS 2 Humble 和可选 WebRTC 串流。兼容适配留在本仓库，主项目继续使用原有 ROS 接口和仿真时间支持。

首个目标平台：Ubuntu 22.04、x86_64、RTX 4090、Isaac Sim 6.1。基础环境版本见 [版本清单](config/versions.lock.json)。主项目版本由开发者选择，本仓库不负责同步、拉取或切换主项目。

以下命令从服务器上的仓库根目录执行；机器配置和场景包由使用者准备。服务器首次使用：

```bash
bash deploy/bootstrap.sh /private/machine.json
./piper --config /private/machine.json --local assets import /path/to/7up-v1.tar.gz
./piper --config /private/machine.json --local build
./piper --config /private/machine.json --local doctor
./piper --config /private/machine.json --local check devices
```

机器配置从 [示例](config/machine.example.json) 创建，放在仓库外。基础设备不需要主项目；填写 `main_project` 和推理模型输入后可构建抓取接入层。

使用串流前，将机器配置中的 `streaming.enabled` 设为 `true`，填入当前实例的公网 TCP 映射和凭据路径，再执行一次 `deploy`。基础示例默认不启用串流。

日常管理（`start` 占用前台，后续命令在另一个终端执行）：

```bash
./piper --config /private/machine.json start --stream
./piper --config /private/machine.json status
./piper --config /private/machine.json export-client --output /private/client.json
./piper --config /private/machine.json experiment start
./piper --config /private/machine.json experiment cancel
./piper --config /private/machine.json stop
```

`experiment` 命令需要先配置并构建外部主项目；仅使用基础设备时跳过它。等待 `status` 显示 ROS 设备就绪再启动实验，等待串流初始化再导出 Viewer 配置。

配置有 `ssh` 时，管理命令在 Linux 管理机通过 SSH 执行；在服务器直接运行时加 `--local`。不需要串流时使用 `start --headless`。两种模式都使用同一个 Isaac 设备运行核心。

- [架构](docs/architecture.md)：目录、进程、ROS／TF／时间边界。
- [部署](docs/deployment.md)：裸机安装、外部主项目、模型输入和旧配置迁移。
- [操作](docs/operation.md)：启停、实验、串流和日志。
- [资产](docs/assets.md)：完整场景包、相对引用和模型制作。
- [排障](docs/troubleshooting.md)、[验收状态](docs/verification.md)。

Viewer 是独立的 `piper-stream-viewer` 仓库，服务器无需其源码或 Node。当前完整预定位、夹爪 Action、瓶子接触标定与完整抓取仍未完成；设备闭环通过不代表完整抓取通过。

项目自有代码采用 [MIT](LICENSE)，第三方资产见 [声明](THIRD_PARTY_NOTICES.md)。
