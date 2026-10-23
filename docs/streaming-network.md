# 串流网络约束

保留经过 AutoDL 实测的 TURN/TCP、HAProxy 与 coturn 链路。本地 Tailscale 接入复用相同链路，HAProxy 入口统一监听 `0.0.0.0:6008`；本地浏览器媒体连接仍需另行验收。

```text
浏览器 ── WS / TCP 入口 ──────> Isaac 信令 :6006
浏览器 ── TURN/TCP 入口 ──────> HAProxy 0.0.0.0:6008
                                  │ HAProxy，源地址绑定容器 IPv4
                                  └──> coturn 容器 IPv4:16010
                                           │ 容器内部 UDP
                                           └──> Isaac 容器 IPv4:47998
管理机 ── SSH ──> 服务器会话管理器及所属进程
```

外部访问使用 TCP 入口和管理 SSH。Viewer 使用 `iceTransportPolicy=relay` 和 `turn:…?transport=tcp`；媒体仍经 TURN/TCP 入口。coturn 与 Isaac 位于同一网络命名空间，relay 地址及 Isaac 广告地址使用该命名空间中的非回环 IPv4，无 `external-ip`。UDP relay 分配端口保持 49152–49215。

AutoDL 原先由平台转发连接容器内部回环入口；本地 Docker／Tailscale 没有这层隐式转发，因此 HAProxy 改为监听所有 IPv4 地址。bridge 模式仍需发布入口端口；host 模式直接使用宿主端口。`0.0.0.0` 表示监听地址，不写入客户端连接地址；客户端使用机器配置中的 Tailscale 地址或其他可达主机地址。

host 网络下可用 `streaming.relay_ip` 指定实际网卡 IPv4，例如本机 Tailscale 地址；启动时会校验该地址确实存在。省略时继续按默认路由自动选择。代理 TUN 可能成为默认出口，因此 Tailscale 部署应显式指定其地址，避免把代理虚拟地址用于 coturn、Isaac 媒体地址和 HAProxy 后端源地址。

HAProxy 的 `source <容器 IPv4>` 必须保留。此前平台直接从 loopback 连 coturn 时，Allocate 认证成功仍可能收集不到 Firefox relay candidate；绑定容器 IPv4 后的路线已有持续视频、重连和刷新证据。历史原因推断是浏览器校验 mapped 地址时拒绝 loopback；没有保留原始 Allocate 字段，不能把该推断说成逐字段证实。不要因看似多一跳而删除 HAProxy，也不要仅因 SDK 默认值将既有 WS 改成 WSS。

SSH 负责管理会话及断开清理；既定 TURN 路线不使用 SSH 搬运媒体、STUN 改写或 ICE 密码接口。历史自制 relay 属于已退役方案。

容器中的 NVENC 枚举过滤器仍只在串流 Isaac 子进程加载。它处理宿主多 GPU 枚举与容器设备可见性差异，不改变网络。`runtime/streaming` 保存网络配置和过滤器，`runtime/session.py` 管理所属进程。公网映射由 AutoDL 提供，部署脚本不修改平台隧道。

每次网络相关变更须用独立 Viewer 验证实际解码帧持续增长、重连和刷新；端口连通、ICE connected 或服务就绪标志均不能单独替代媒体验收。
