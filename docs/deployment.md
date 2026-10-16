# AutoDL 部署

## 输入与目标机器

目标为 Ubuntu 22.04、x86_64、RTX 4090。AutoDL 提供可工作的 NVIDIA 驱动；部署只安装 Toolkit，不安装或替换宿主驱动。创建 Isaac 环境需要镜像提供 Conda 或 Python 3.12。

准备本仓库、完整 USD 场景及其依赖、基础 ROS URDF、装配 URDF 和机器配置。抓取接入还需外部主项目目录和经过主项目审核的 ONNX、PT、类别表。主项目可从 `git@github.com:Limoncello-Zoey/NankaiRoboCup2026.git` 获取 `dev` 分支，维护时显式更新并重新构建；启动和 deploy 不自动切换源码。主项目自行同步；允许未提交代码，不要求固定提交。网络安装不属于离线模式。

将 `config/machine.example.json` 复制到仓库外并设置 0600，填写服务器 `repo_path`、统一 `data_root`。基础使用保留 `main_project: null`。配置路径均指向服务器；仅 `export-client --output` 指向执行命令的管理机。

必须填写 `profile`，例如 `7up`，并在远端仓库编辑 `config/profiles/7up.yaml`：`scene_usd` 直接指向源场景，`robot_urdf` 指向已有的 ROS 机器人模型，`assembly_urdf` 指向包含相机和支架的装配 URDF；三者使用服务器绝对路径。基础 ROS 模型应包含机械臂 joint1–joint6、夹爪 joint7/8、link6 等现有 ROS 定义，网格引用必须已解析；不要将采用 CAD 连杆坐标的装配 URDF 当成基础 ROS 模型。资源整理见[资产说明](assets.md)。

`isaac_env` 可省略，默认 `data_root/envs/isaac`。也可以将源码与 `data_root` 放在系统盘（例如 `/root/piper-sim-grasp`、`/root/piper-data`），显式设置 `isaac_env` 为数据盘上的 `/root/autodl-tmp/isaacsim-6.1-env`。这样 SDK、构建和配置使用系统盘，大型 Isaac 环境与独立资产制作数据使用数据盘；跨盘迁移后重新生成配置与构建，不直接复用带旧路径的 install。`vulkan_icd` 可显式指定已经核对的 ICD；Vulkan 不通过时先处理驱动／ICD，部署不会覆盖系统 ICD。

## 首次部署

从服务器上的仓库根目录执行：

```bash
bash deploy/bootstrap.sh /private/machine.json
./piper --local --config /private/machine.json build
./piper --local --config /private/machine.json doctor
./piper --local --config /private/machine.json check devices
```

bootstrap 在缺少系统 Python 的裸镜像上先安装 Python 3，再调用同一个 deploy 入口；日后直接执行 `./piper --local --config /private/machine.json deploy`。Isaac 环境可使用镜像已有的 `/root/miniconda3/bin/python3.12` 创建，不依赖交互 shell 激活 Conda。build 编译 ROS 程序、构建或复用 engine、生成机器人模型，不编译 USD 场景。start/check 另外执行轻量配置生成。

按实际路径所在的磁盘预留安装与解包空间。私有 SDK、engine、生成的 ROS 模型、构建产物和运行记录集中在 data_root 下的 sdk、assets、models、build、install、logs、reports、state、cache，以及抓取依赖安装记录 dependencies。Isaac 环境使用 isaac_env 指定的路径，默认才是 data_root/envs/isaac；源 USD、URDF 和制作缓存可以位于其他外部目录。系统依赖由包管理器安装到系统目录。

运行配置由 start 写入 data_root/logs/session-*/config，GPU check 写入 data_root/reports/<检查类型>-*/config；旧 data_root/config 目录不再用于运行配置。重复安装使用包管理器和实际版本检查；失败日志保存在 logs、dependencies 或 sdk 下对应组件目录，修正原因后重跑对应命令。

本轮两台机器的 Isaac 环境约占 26–30 GB，首次安装还需下载临时空间。安装脚本禁用 pip 的重复缓存，正常结束会清理本次临时目录。临时下载使用 data_root/cache；采用双盘布局时它可在系统盘，但要按实际剩余容量预留下载与构建空间。约 30 GB 的 Isaac 环境仍放在数据盘，安装包不长期保留。

普通 PyPI 依赖默认使用清华 HTTPS 镜像 `https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple`，Isaac 专用 wheel 保留 NVIDIA 额外索引。地址按[清华镜像使用说明](https://mirrors.tuna.tsinghua.edu.cn/help/pypi/)配置。需要其他源时在机器配置填写 `pypi_index_url`；脚本只对本次安装传参，不改用户全局 pip 配置。

系统 ROS 安装保留 APT／locale 备份和官方签名；依赖解析不允许移除软件包。当前镜像的 ICU 版本差异作为显式兼容分支处理，其他不匹配版本会报错。

`piper` 和 ROS 工具使用系统 Python，Isaac 仅使用独立环境。版本清单固定 Isaac、TensorRT 等核心组件；Humble 的 APT 包从当前仓库解析，实际安装版本记录在 reports/installed-packages.tsv，Isaac Python 包版本记录在 reports/isaac-packages.json，不承诺逐字节重建旧 APT 快照。

## 接入持续开发的主项目

例如，首次在服务器获取主项目：

```bash
git clone --single-branch --branch dev \
  git@github.com:Limoncello-Zoey/NankaiRoboCup2026.git /root/NankaiRoboCup2026
```

该命令是部署者的源码准备步骤；本工程的 deploy／start 不执行 Git 同步。填写 `main_project` 为服务器上的 NankaiRoboCup2026 目录，并增加：

```json
"detector": {
  "onnx": "/external/models/yolo11s-detect-480x640.onnx",
  "source_pt": "/external/models/yolo11s.pt",
  "classes": "/external/models/yolo11s-detect-classes.txt",
  "license_reference": "NKU-RC2026-MODEL-LICENSE-007",
  "exporter_version": "8.4.120"
}
```

这些模型信息对应当前主项目的 manifest 生成器；主项目调整审核输入时更新外部模型配置，不绕过其校验。在服务器的仓库根目录依次执行：

```bash
./piper --local --config /private/machine.json deploy
./piper --local --config /private/machine.json build
./piper --local --config /private/machine.json doctor
./piper --local --config /private/machine.json check control
```

构建只选取接入所需包，build/install/log 均在仿真数据目录。Servo 原脚本按目录结构写输出，因此复制其构建材料到独立暂存目录后调用原脚本。主项目源码、现有构建目录不写入。

生成的机器人模型按 `data_root/models/<profile>` 隔离，记录基础／装配 URDF 的路径、文件哈希和 engine 目录。新增 profile 首次接入主项目、变更机器人装配或引用网格时显式 build；启动前的输入检查不覆盖网格内容变化，详见[检查范围](assets.md#修改与新增)。仅调整初始角度、桌子碰撞盒、瓶子高度／半径或执行超时，重启会话即可。主项目原有配置模板仍由其源码提供，仿真端负责注入本次场景参数。

TensorRT 常驻 SDK 保留运行库、插件、头文件和工具，去除 Linux／Windows 的 engine 构建资源库。只有 engine 缓存缺失、确需重建时，构建脚本才临时获取匹配版本的 Linux 构建资源，完成或失败后清理；有效缓存复用与普通推理不下载它们。

4090 engine 在当前服务器从 ONNX 生成。缓存按模型内容、GPU、TensorRT 和生成器输入区分，manifest 用主项目工具生成。参与构建的文件哈希、提交号和工作区状态保存在 reports/main-build-inputs.json。修改主项目程序、launch 或构建输入后显式重新 build；会话直接读取的三份几何包 YAML 只需重启，具体边界见[日常操作](operation.md)。运行中的进程不自动热更新。

M 的 `preview_only=true` 是现有节点的计算模式，输出经原 launch 接入 W，执行仍由 W 服务控制。

## 串流组件

将 `streaming.enabled` 设为 true，填写公网 host、信令和 TURN 转发端口，以及服务器私有凭据文件路径，再运行 deploy。凭据不存在时自动创建，不打印到终端。

平台 TCP 转发：公网信令 → 容器 6006；公网 TURN → 容器 loopback 6008。HAProxy 从 6008 转到 coturn 的容器 IPv4:16010；coturn 到 Isaac 使用 UDP 47998。当前网络方式保持 TURN/TCP，Viewer 独立运行。

公网端口使用每台实例已有的映射，不要求开放公网 UDP，也不修改平台网络。HAProxy 的源地址绑定及其他约束见[串流网络约束](streaming-network.md)。

## 从旧部署迁移

旧 server/manage.py、两套 Isaac 启动器、relay 和旧研究脚本已移除，不提供兼容壳。

1. 正常停止旧栈，保留私有机器配置，明确填写 profile、当前路径和端口。
2. 在仓库 profile 中填写源 USD 和配套 URDF 路径。已有解包资源可直接作为普通文件使用，不必移动或重新登记；旧 current.json 不再参与运行。
3. 更新源码并显式 build 一次，生成该 profile 的机器人模型；随后执行 doctor/check 验收。
4. 从管理机重新 start 并导出 Viewer 配置。运行配置现在位于本次会话的 config 子目录，旧 data_root/config 文件不再使用。

不要搬旧 PID、锁、runtime 文件或 ARM build/install。旧的若干 data 目录不需要维持原名称。
