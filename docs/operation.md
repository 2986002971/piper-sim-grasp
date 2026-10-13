# 日常操作

`piper` 的公共参数写在子命令之前。管理机配置包含 SSH 地址、远端 repo_path/data_root；服务器直接执行加 `--local`。

```bash
./piper --config /private/machine.json start --headless
# 或启动同一设备场景的网页串流
./piper --config /private/machine.json start --stream
```

`--stream` 要求配置 `streaming.enabled=true` 并先执行 deploy；仅启用配置不会让默认 start 自动串流，须显式传入 `--stream`。

start 前台拥有整个会话，可在管理机 tmux 中运行。另一个终端：

```bash
./piper --config /private/machine.json status
./piper --config /private/machine.json export-client --output /private/client.json
./piper --config /private/machine.json experiment start
./piper --config /private/machine.json experiment cancel
./piper --config /private/machine.json stop
```

等到 `ros_devices_ready=true` 再启动实验；等到串流初始化完成再导出 Viewer 配置。状态分别显示 manager_active、components_started、scene_ready、ros_devices_ready、stream_initialized、experiment_running。ROS 就绪要求实际接收到图像、深度、内参、关节和时钟。串流初始化不代表浏览器已完成媒体连接；视频与交互另行验收。

实验取消通过协调器清理逻辑调用 Source cancel／W stop。实验结束或失败不关闭设备，每次实验使用独立的 experiment-* 报告目录和日志，路径由 status 的 experiment_directory 给出。当前起始姿态还不能稳定识别瓶子，完整预定位仍是未完成项。

## 日志与验收

服务器 data_root/logs/session-* 保存 Isaac、ROS、TURN 和实验日志；其中 experiment-* 子目录保存实验 result.json 与 samples.json，实验 launch 日志位于会话目录的 experiment-*.log。data_root/reports 保存独立 check 与部署、构建记录。配置在 config，活动状态在 state。凭据和临时 TURN 配置不进仓库。

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
