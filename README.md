# Piper Sim Grasp

为持续开发中的 NankaiRoboCup2026 提供 AutoDL 仿真设备：Piper、D405、ROS 2 Humble 和可选 WebRTC 串流。兼容适配留在本仓库，主项目继续使用原有 ROS 接口和仿真时间支持。

首个目标平台：Ubuntu 22.04、x86_64、RTX 4090、Isaac Sim 6.1。基础环境版本见 [版本清单](config/versions.lock.json)。主项目版本由开发者选择，本仓库不负责同步、拉取或切换主项目。

以下命令从服务器上的仓库根目录执行；先准备机器配置、完整 USD 场景及配套 URDF，并填写仓库中的场景 profile。服务器首次使用：

```bash
bash deploy/bootstrap.sh /private/machine.json
./piper --config /private/machine.json --local build
./piper --config /private/machine.json --local doctor
./piper --config /private/machine.json --local check devices
```

机器配置从 [示例](config/machine.example.json) 创建，放在仓库外。示例将源码与运行数据放在系统盘，Isaac 环境放在数据盘，可按实际容量调整。基础设备不需要主项目；填写 `main_project` 和推理模型输入后可构建抓取接入层。

机器配置的 `profile` 明确选择远端仓库的 `config/profiles/<名称>.yaml`。该 YAML 指定源 USD、基础 ROS URDF、装配 URDF、初始关节角、瓶子物理基线和感知先验；示例路径对应当前远端资源，换机器按实际位置修改。大型模型与贴图留在仓库外。

每次 `start` 都重新读取远端 profile，并在本次会话日志目录生成运行配置。修改 USD 的布景、材质，或 profile 中的初始姿态、感知先验和现有实验参数后重启即可；程序、机器人装配（含引用网格）、瓶子物理基线和推理模型变更时显式 `build`，输入检查范围见[资产说明](docs/assets.md#修改与新增)。源码同步由使用者完成，启动不拷贝 YAML、不拉取 Git、不编译。预定位与完整 Pick 均已有实际成功记录，范围见[验收记录](docs/verification.md)。

使用串流前，将机器配置中的 `streaming.enabled` 设为 `true`，填入当前实例的公网 TCP 映射和凭据路径，再执行一次 `deploy`。基础示例默认不启用串流。

日常管理（`start` 占用前台，后续命令在另一个终端执行）：

```bash
./piper --config /private/machine.json start --stream
./piper --config /private/machine.json status
./piper --config /private/machine.json export-client --output /private/client.json
./piper --config /private/machine.json experiment start
./piper --config /private/machine.json experiment start --task pick
./piper --config /private/machine.json experiment cancel
./piper --config /private/machine.json stop
```

`experiment start` 运行预定位，`--task pick` 调用主项目完整 Pick（夹瓶、抬升、携带及持物确认）。两种实验按需选择，共用取消入口；从初始场景独立重跑前先 stop/start 复位。`experiment` 命令需要先配置并构建外部主项目；仅使用基础设备时跳过它。等待 `status` 显示 ROS 设备就绪再启动实验，等待串流初始化再导出 Viewer 配置。

配置有 `ssh` 时，管理命令在 Linux 管理机通过 SSH 执行；在服务器直接运行时加 `--local`。不需要串流时使用 `start --headless`。两种模式都使用同一个 Isaac 设备运行核心。

- [架构](docs/architecture.md)：目录、进程、ROS／TF／时间边界。
- [部署](docs/deployment.md)：裸机安装、外部主项目、模型输入和旧配置迁移。
- [操作](docs/operation.md)：启停、实验、串流和日志。
- [资产](docs/assets.md)：源场景与 profile、可选离线打包和模型制作。
- [排障](docs/troubleshooting.md)、[验收状态](docs/verification.md)。

Viewer 是独立的 `piper-stream-viewer` 仓库，服务器无需其源码或 Node。仿真驱动复用主项目 TCP／夹爪控制核心，RSP 基于实测关节发布 TF。验证结果和未覆盖范围见[验收记录](docs/verification.md)；瓶子参数为联调基线，尚未作实物标定。

项目自有代码采用 [MIT](LICENSE)，第三方资产见 [声明](THIRD_PARTY_NOTICES.md)。
