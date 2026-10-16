# 日常操作

`piper` 的公共参数写在子命令之前。管理机配置包含 SSH 地址、远端 repo_path/data_root；服务器直接执行加 `--local`。

```bash
./piper --config /private/machine.json start --headless
# 或启动同一设备场景的网页串流
./piper --config /private/machine.json start --stream
```

`--stream` 要求配置 `streaming.enabled=true` 并先执行 deploy；仅启用配置不会让默认 start 自动串流，须显式传入 `--stream`。

机器配置必须明确选择 profile。启动读取远端仓库 config/profiles/<profile>.yaml，直接加载其 scene_usd，并在本次会话 logs/session-*/config 中生成运行配置；不要求先生成 data_root/config/devices.yaml。status 中的 profile、scene_usd、configuration_directory 表示实际运行的选择。首次接入和机器人装配变化需要显式 build，调初始姿态、碰撞盒、感知先验与超时只需重启；源码同步显式完成。

start 前台拥有整个会话，可在管理机 tmux 中运行。另一个终端：

```bash
./piper --config /private/machine.json status
./piper --config /private/machine.json export-client --output /private/client.json
./piper --config /private/machine.json experiment start
./piper --config /private/machine.json experiment cancel
./piper --config /private/machine.json stop
```

等到 `ros_devices_ready=true` 再启动实验；等到串流初始化完成再导出 Viewer 配置。状态分别显示 manager_active、components_started、scene_ready、ros_devices_ready、stream_initialized、experiment_running。ROS 就绪要求实际接收到图像、深度、内参、关节和时钟。串流初始化不代表浏览器已完成媒体连接；视频与交互另行验收。

实验取消通过协调器清理逻辑调用 Source cancel／W stop。实验结束或失败不关闭设备，每次实验使用独立的 experiment-* 报告目录和日志，路径由 status 的 experiment_directory 给出。实验使用所属会话的配置；在管理机切换 profile 后，须先 stop/start，再启动该场景实验。

抓取姿态约定 `source.geometry_T_tcp` 读取远端主项目几何包 `config/alignment.yaml` 中已提交的实机实验配置；其余基础参数仍来自 `alignment.example.yaml`，再覆盖场景 profile 的仿真参数，不导入实机硬件配置。主项目更新这个姿态参数后，无需编译，但必须 stop/start 整个会话重新生成配置；仅重启 experiment 会继续使用原快照。主项目程序或 launch 代码变化仍需显式 build。

doctor 的重建判断排除启动时直接从源码读取的三份配置：几何包 config 下的 alignment.yaml、alignment.example.yaml 和 d405_yolo.yaml；完整文件哈希仍保留在构建来源记录中。其他包的配置、程序和 launch 变化仍按构建输入处理。更新 detector 模型文件本身仍需 build。

## 日志与验收

服务器 data_root/logs/session-* 保存 Isaac、ROS、TURN 和实验日志及 config 配置快照；其中 experiment-* 子目录保存实验 result.json 与 samples.json，实验 launch 日志位于会话目录的 experiment-*.log。data_root/reports 保存独立 check 与部署、构建记录；GPU check 的配置在对应报告的 config 子目录。活动状态在 state。凭据和临时 TURN 配置不进仓库。

停止运行会话后，从服务器上的仓库根目录执行：

```bash
./piper --local --config /private/machine.json check unit
./piper --local --config /private/machine.json check devices
./piper --local --config /private/machine.json check control
./piper --local --config /private/machine.json check control --stream
./piper --local --config /private/machine.json check perception
```

devices 不依赖主项目。control／perception 需要主项目。`--stream` 验收使用真实串流 experience 和同一个设备运行核心，但不启动 TURN／浏览器；浏览器验收须通过正常 start --stream 另行完成。

设备测试使用会话内投影靶标，不保存资产。GPU 检查独占测试会话，拒绝与正常运行栈同时执行。不要用全局 pkill 清理。

每次实例重建、容器地址或凭据变化后重新 export-client。Viewer 配置文件含 TURN 凭据，放在私有目录。
