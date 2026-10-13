# 排障

先运行 `piper --config ... doctor`，再查看它指出的文件与本轮会话日志。doctor 只读，不会重新安装或启动 Isaac。

| 症状 | 检查与处理 |
|---|---|
| SSH 能登录，但管理失败 | 查看远端错误输出和路径；SSH 可登录不代表依赖、资产或端口可用 |
| 缺少 NVENC、TURN、HAProxy | 启用 streaming 后运行 deploy；coturn 与 NVENC 在 data_root/sdk，HAProxy 是系统软件包；不复用旧绝对路径 |
| Vulkan 初始化失败 | 核对容器驱动、ICD；可通过机器配置 vulkan_icd 指定已验证文件 |
| Another session / GPU 0 occupied | 用拥有该会话的入口正常 stop；检查 session 状态，不全局 kill |
| 场景缺文件或移动后失效 | 重新导入完整资产包；不能只搬顶层 USD |
| OmniPBR.mdl 未解析 | 使用本工程 Isaac 环境边界运行；内置 MDL 搜索路径由固定 Isaac 安装派生 |
| 设备进程活着但 ROS 不就绪 | 核对 ROS_DOMAIN_ID、设备日志与实际五类数据；不要混用系统和 Isaac Python |
| 裸机首次启动长时间没有图像 | 首次渲染会生成缓存，本轮裸机约需 2.5 分钟；先查看 Isaac 日志和 CPU 活动，保留 data_root/cache 后再判断是否卡死 |
| TF 与可见几何不一致 | 核对当前生成 URDF、session 运动学对齐报告与相机静态安装；不要加第二套机械臂 TF |
| CameraInfo fy 被改成 fx | 使用统一相机发布器，它显式保留独立 fx/fy；不要再接旧 helper |
| 深度几何拟合超时 | 核对真实图像、K/D、采集时间 TF；ROS 环境已限制 BLAS 线程，不使用真值绕过检测 |
| 主项目更新后构建失败 | 查看明确缺失的包／工具／接口，更新本工程接入层；不自动回退主项目提交 |
| M 设置 preview_only=false 后退出 | 保持主项目支持的计算模式 true，由 W 服务控制执行 |
| 浏览器 TURN permission 失败 | 实例重建后重新 export-client，容器 IPv4 可能变化 |
| 75 分钟后正常关闭 | 检查 watchdog_seconds，默认 4500；需要无限会话可设为 0 |

设备与 W 闭环验收通过不意味着每个初始视角都能被 YOLO 识别。当前完整预定位和抓取不能作为安装验收的已通过项。
