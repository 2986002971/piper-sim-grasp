# 排障

先区分客户端配置、远端组件、Isaac 就绪及持续视频。命令和版本以 [部署指南](deployment.md) 为准。
`status` 不打印凭据；原始日志和浏览器候选数据只能保存在仓库外私有目录。

| 症状 | 检查与原因 | 处理及验证 |
|---|---|---|
| `/config.json` 返回 409 | 独立客户端配置不存在、JSON 损坏、选择了未配置 relay，或缺少 media/ICE 字段 | 从管理入口重新导出到 `--config` 的文件；HTTP 200 后仍需连接验证 |
| 页面端口占用 | 旧 Python 页面服务仍监听同一 loopback 端口 | 停止已确认归属的旧服务或选择新 `--port`；确认静态 HTML/配置接口 |
| 缺少 HAProxy | preflight 执行 configured binary 失败，或 wrapper 所需库缺失 | 安装 HAProxy/依赖并更新路径；`-vv`、preflight 及 relay candidate 都要成功 |
| Allocate 后无 relay candidate | 平台直转 coturn 的 loopback 源地址不满足路径；或认证/转发未通 | 保留 HAProxy 容器源地址绑定，检查 6008→16010；用 Viewer `turn_smoke_check.py` 检查 ICE |
| 没有任何候选、701 | ICE 注入缺失、TURN 凭据过期或公网入口错误；单条 701 不是最终判据 | 打开 `?diagnostics=1` 或 `capture=1`，核对选中候选与解码帧；TURN 必须 relay policy |
| 信令 1005/1006 | WS 连接非正常关闭、Isaac 尚未就绪、WS/WSS 地址不匹配或旧会话占用 | 核对平台信令到6006、`isaac_ready`、SDK事件；先断开旧会话再重连。1005 是无 close status，1006 是异常关闭，均不等同于明确服务端错误码 |
| NVENC 初始化失败、黑屏 | 驱动枚举宿主 GPU、过滤库未加载、Vulkan 错误、真实编码未启动 | 检查 `/dev/nvidia*`、过滤器日志、`LD_PRELOAD`、`vulkaninfo`；新GPU做编码A/B后再验证视频 |
| 会话成功但无画面 | SDK会话事件不证明持续视频；场景载入/媒体/viewport 可能失败 | 查看独立视频状态、frame callback、尺寸/解码帧；检查 `PIPER_ISAAC_READY` 与 viewport framing 日志 |
| 视频停顿超过3秒 | 实际 presentation callback 没有增长；后台页面调度可能受限 | 前台复测、检查候选对/RTT/解码计数和服务退出；不要只用 `currentTime` 或旧成功提示 |
| 看不到模型、视角错误 | 串流 viewport 的 USD context 与默认 stage 不同，或目标没有有限 bounds | 检查 Camera framing target/succeeded；核对完整场景与 default prim，实际用键鼠确认 |
| 无控制、重复连接、遗留会话 | 多个 Viewer 争用同一 Isaac，视频未聚焦或原连接未释放 | 旧页面主动断开，点击视频获取焦点；必要时刷新或正常重启栈，确认远端画面操作变化 |
| 全栈退出 | 管理机/user unit/SSH退出、组件失败、4500秒watchdog | 检查私有 run 日志、systemd状态和时间；正常重启。不得直接推断 TURN 长期性能差 |
| stop 报 stale state | PID已退出或starttime不同，保护PID复用 | 不杀不归属进程；检查旧管理机与远端端口，清理确认过的本轮配置后重新预检 |
| 模型下载/校验失败 | 固定输入缺失、缓存损坏或网络错误 | 用 download-only 预下载后复制完整缓存，离线再校验；不要跳过哈希强启 |
| 支架下载返回登录页 | 官方语雀附件需要登录 | 首次登录下载ZIP，核对ZIP/STEP哈希，置于仓库外bracket缓存；否则默认无支架 |
| STEP转换缺OCP | 有STEP缓存但未装可选CAD转换依赖 | 安装models/requirements-step-conversion.txt，或使用外部毫米STL；再核对装配位姿 |
| USD引用不完整 | 仅复制顶层USD、绝对临时路径、移动时漏payload/mesh | 保留完整场景包或重新生成；validate_usd 检查所有依赖通过后再启动 |

浏览器验证工具在独立 Viewer 仓库 `diagnostics/`。探针需显式开启，日常连接所需的 ICE/WS 适配不依赖探针。
relay 最小恢复见 [legacy](../legacy/README.md)；Ligolo 调查结论见 [技术记录](history/streaming-investigation.md)。
