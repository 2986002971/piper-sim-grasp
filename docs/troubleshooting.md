# 排障

先运行 `piper --config ... doctor`，再查看它指出的文件与本轮会话日志。doctor 只读，不会重新安装或启动 Isaac。

| 症状 | 检查与处理 |
|---|---|
| SSH 能登录，但管理失败 | 查看远端错误输出和路径；SSH 可登录不代表依赖、资产或端口可用 |
| 缺少 NVENC、TURN、HAProxy | 启用 streaming 后运行 deploy；coturn 与 NVENC 在 data_root/sdk，HAProxy 是系统软件包；不复用旧绝对路径 |
| Vulkan 初始化失败 | 核对容器驱动、ICD；可通过机器配置 vulkan_icd 指定已验证文件 |
| Another session / GPU 0 occupied | 用拥有该会话的入口正常 stop；检查 session 状态，不全局 kill |
| 场景缺文件或移动后失效 | 检查 profile 中的源 USD、URDF 和全部引用路径；搬运完整依赖目录，不能只搬顶层 USD |
| 修改 YAML 后没有变化 | 重启设备会话；检查 status 的 profile、scene_usd 和 configuration_directory，本次会话使用自己的快照 |
| Robot inputs changed / 缺少 profile 模型 | 新 profile 首次接入主项目，或基础／装配 URDF 路径、内容及主项目路径变化时，需要显式 build；仅改引用网格不会触发该提示，仍须主动重建；改初始角度不需要 |
| OmniPBR.mdl 未解析 | 使用本工程 Isaac 环境边界运行；内置 MDL 搜索路径由固定 Isaac 安装派生 |
| 设备进程活着但 ROS 不就绪 | 核对 ROS_DOMAIN_ID、设备日志与实际五类数据；不要混用系统和 Isaac Python |
| 裸机首次启动长时间没有图像 | 首次渲染会生成缓存，本轮裸机约需 2.5 分钟；先查看 Isaac 日志和 CPU 活动，保留 data_root/cache 后再判断是否卡死 |
| TF 与可见几何不一致 | 核对当前使用的 ROS URDF、设备输出 device-model.json 中的 kinematic_validation 与相机静态安装。运行时只读校验八个运动关节；若日志报 ROS/USD joint geometry differs，应修复并重建装配，不会自动调整 USD 锚点 |
| CameraInfo fy 被改成 fx | 使用统一相机发布器，它显式保留独立 fx/fy；不要再接旧 helper |
| 深度几何拟合超时 | 核对真实图像、K/D、采集时间 TF；ROS 环境已限制 BLAS 线程，不使用真值绕过检测 |
| 主项目更新后构建失败 | 查看明确缺失的包／工具／接口，更新本工程接入层；不自动回退主项目提交 |
| M 设置 preview_only=false 后退出 | 保持主项目支持的计算模式 true，由 W 服务控制执行 |
| W 已限位停止，实验仍显示 PREPOSITION | 查看 servo_status 和 reference_tracking_state。协调器当前记录 Servo 状态，但未直接把 W 的参考跟踪 FAULT 作为中止条件；可用 experiment cancel 清理，否则会等待 Source 的执行超时。恢复前先处理限位等根因 |
| 浏览器 TURN permission 失败 | 实例重建后重新 export-client，容器 IPv4 可能变化 |
| 75 分钟后正常关闭 | 检查 watchdog_seconds，默认 4500；需要无限会话可设为 0 |

设备与 W 闭环验收通过不意味着每个初始视角都能被 YOLO 识别。当前已有一次完整预定位成功记录，仍需验证重复性和视角覆盖；完整抓取尚未验收，见[验收记录](verification.md)。
