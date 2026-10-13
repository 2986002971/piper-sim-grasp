# AutoDL 部署

## 输入与目标机器

目标为 Ubuntu 22.04、x86_64、RTX 4090。AutoDL 提供可工作的 NVIDIA 驱动；部署只安装 Toolkit，不安装或替换宿主驱动。创建 Isaac 环境需要镜像提供 Conda 或 Python 3.12。

准备本仓库、完整七喜场景包、机器配置。抓取接入还需外部主项目目录和经过主项目审核的 ONNX、PT、类别表。主项目自行同步；允许未提交代码，不要求固定提交。网络安装不属于离线模式；场景包导入不需要访问模型网站。

将 `config/machine.example.json` 复制到仓库外并设置 0600，填写服务器 `repo_path`、统一 `data_root`。基础使用保留 `main_project: null`。配置路径均指向服务器；仅 `export-client --output` 指向执行命令的管理机。

`isaac_env` 可省略，默认 `data_root/envs/isaac`。`vulkan_icd` 可显式指定已经核对的 ICD；Vulkan 不通过时先处理驱动／ICD，部署不会覆盖系统 ICD。

## 首次部署

从服务器上的仓库根目录执行：

```bash
bash deploy/bootstrap.sh /private/machine.json
./piper --local --config /private/machine.json assets import /path/to/7up-v1.tar.gz
./piper --local --config /private/machine.json build
./piper --local --config /private/machine.json doctor
./piper --local --config /private/machine.json check devices
```

bootstrap 在缺少系统 Python 的裸镜像上先安装 Python 3，再调用同一个 deploy 入口；日后直接执行 `./piper --local --config /private/machine.json deploy`。Isaac 环境可使用镜像已有的 `/root/miniconda3/bin/python3.12` 创建，不依赖交互 shell 激活 Conda。

安装与解包需数据盘空间。生成数据全部在 data_root：envs、sdk、assets、models、build、install、config、logs、reports、state、cache，以及抓取依赖安装记录 dependencies。重复安装使用包管理器和实际版本检查；失败日志保存在 logs、dependencies 或 sdk 下对应组件目录，修正原因后重跑对应命令。

本轮两台机器的 Isaac 环境约占 26–30 GB，首次安装还需下载临时空间。安装脚本禁用 pip 的重复缓存，正常结束会清理本次临时目录；不要将大型下载放进 30 GB 系统盘。

普通 PyPI 依赖默认使用清华 HTTPS 镜像 `https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple`，Isaac 专用 wheel 保留 NVIDIA 额外索引。地址按[清华镜像使用说明](https://mirrors.tuna.tsinghua.edu.cn/help/pypi/)配置。需要其他源时在机器配置填写 `pypi_index_url`；脚本只对本次安装传参，不改用户全局 pip 配置。

系统 ROS 安装保留 APT／locale 备份和官方签名；依赖解析不允许移除软件包。当前镜像的 ICU 版本差异作为显式兼容分支处理，其他不匹配版本会报错。

`piper` 和 ROS 工具使用系统 Python，Isaac 仅使用独立环境。版本清单固定 Isaac、TensorRT 等核心组件；Humble 的 APT 包从当前仓库解析，实际安装版本记录在 reports/installed-packages.tsv，Isaac Python 包版本记录在 reports/isaac-packages.json，不承诺逐字节重建旧 APT 快照。

## 接入持续开发的主项目

填写 `main_project` 为服务器上的 NankaiRoboCup2026 目录，并增加：

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

4090 engine 在当前服务器从 ONNX 生成。缓存按模型内容、GPU、TensorRT 和生成器输入区分，manifest 用主项目工具生成。参与构建的文件哈希、提交号和工作区状态保存在 reports/main-build-inputs.json。修改主项目后显式重新 build；运行中的进程不自动热更新。

M 的 `preview_only=true` 是现有节点的计算模式，输出经原 launch 接入 W，执行仍由 W 服务控制。

## 串流组件

将 `streaming.enabled` 设为 true，填写公网 host、信令和 TURN 转发端口，以及服务器私有凭据文件路径，再运行 deploy。凭据不存在时自动创建，不打印到终端。

平台 TCP 转发：公网信令 → 容器 6006；公网 TURN → 容器 loopback 6008。HAProxy 从 6008 转到 coturn 的容器 IPv4:16010；coturn 到 Isaac 使用 UDP 47998。当前网络方式保持 TURN/TCP，Viewer 独立运行。

公网端口使用每台实例已有的映射，不要求开放公网 UDP，也不修改平台网络。HAProxy 的源地址绑定及其他约束见[串流网络约束](streaming-network.md)。

## 从旧部署迁移

旧 server/manage.py、两套 Isaac 启动器、relay 和旧研究脚本已移除，不提供兼容壳。

1. 正常停止旧栈，保留其目录与私有配置作为恢复副本。
2. 从正确场景导出完整资产包，复制新的机器配置并填写当前路径和端口。
3. 新源码和 data_root 使用独立目录部署、导入、构建、验收。
4. 新入口通过后再切换日常管理命令并重新导出 Viewer 配置。

不要搬旧 PID、锁、runtime 文件或 ARM build/install。旧的若干 data 目录不需要维持原名称。
