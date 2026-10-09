# 串流技术调查

以下为2026-10-08至10-09的实验结论。当前安装和管理命令以[部署指南](../deployment.md)为准，功能验证范围见[验证记录](../verification.md)。

## NVENC 容器枚举

云端容器只暴露分配给实例的 GPU 设备节点，而 NVENC RM ioctl 仍返回宿主机8张 GPU。
StreamSDK 会尝试选择不可见设备，导致 unsupported device 或 session 创建失败。

RTX 4090 上用隔离构建的 FFmpeg 8.1.3 / NVENC API 13.0 做对照：未加载过滤器时编码0帧；
加载 ioctl 过滤器后枚举从8张收窄到1张，实际编码60帧。Isaac Sim 6.1 接入同一过滤器后，
4090与5090均有浏览器画面和鼠标交互证据。设备minor随实例变化，不能写死。

这推翻了“5090太新不能编码”及“仅升级FFmpeg即可解决”的早期推断。
能力查询、空编码session及连接断开后session归零，都不能替代实际编码和画面验证。
另一独立故障是重复Vulkan ICD造成GPU重复枚举；单一ICD和关闭Kit多GPU解决枚举问题，不能单独解决NVENC问题。

## relay 与 STUN 改写

平台转发TCP，而Isaac的WebRTC媒体使用UDP。legacy relay将单会话UDP封装进TLS/TCP，
信令和relay TCP由管理机SSH本地转发承载。封装改变了服务器看到的STUN来源端点。

客户端relay按事务关联原浏览器端点，修正XOR-MAPPED-ADDRESS，并使用从ice-lite SDP得到的密码
重算MESSAGE-INTEGRITY和FINGERPRINT；Viewer通过本机16009接口提供该密码。
仅能连接信令、或ICE已connected，都不证明视频持续更新。恢复步骤见[legacy](../../legacy/README.md)。

1006曾分别伴随NVENC初始化失败和SSH本地转发退出；4001也曾发生在ICE已connected之后。
关闭码应结合组件日志、候选对及实际帧增长判断，不能当作单一根因。

## TURN 与 HAProxy 对照

coturn 4.18.0直接接收平台loopback转发时，认证Allocate成功，但Firefox154收集到0个relay candidate并报701。
coturn从TURN TCP socket peer生成mapped地址；Mozilla nICEr对该地址的默认校验拒绝loopback和wildcard。
[coturn源码](https://github.com/coturn/coturn/blob/4.18.0/src/server/ns_turn_server.c)与
[Mozilla地址校验](https://github.com/mozilla/gecko-dev/blob/master/dom/media/webrtc/transport/third_party/nICEr/src/stun/stun_client_ctx.c)
支持该解释；没有保存原始Allocate字段或nICEr直接拒绝日志，所以不能声称已逐字段确认。

HAProxy 2.4接收6008后，绑定容器IPv4源地址连接coturn的16010。
两次隔离Firefox gathering各产生1个relay candidate，后续完整Viewer均持续解码1080p120秒，重连和刷新通过。
部分gathering仍出现一条空文本701，但不阻止媒体；该事件未完全解释。
TURN路径无需STUN改写或ICE密码接口。信令平台是原始TCP，应使用WS，不能仅因SDK默认WSS就假设入口支持TLS。

旧Viewer会话占用和legacy密码接口漏掉5174的CORS来源曾干扰SDK对照；那些失败不能用于判断SDK版本兼容性。
同一Isaac实例应先交接旧会话，再验证新客户端。视角操作也须检查实际串流viewport的USD context。

## Ligolo-ng 与其他路线

Ligolo-ng v0.9.2的bind-agent控制连接、证书指纹和退出清理曾验证成功。
开发机TUN权限和后续SSH连通性阻止媒体阶段；计划的六次十分钟测试未执行。
没有WebRTC媒体、交互延迟、冻结、资源或无STUN改写的性能对照，不能判断Ligolo优于或劣于relay。
控制连接成功也不能替代媒体验收。

RTSP/x264只做过合成图案CPU编码基准，没有接入Isaac真实画面及浏览器键鼠控制。
早期Isaac6.0的RGB-D研究不代表当前Piper X+D405几何装配已配置传感器输出。

## 延迟与可靠性

relay与TURN各50次可信操作的[短测](../isaac-turn-latency-quick.md)保留方法、分位数和限制。
relay主分位数仅使用46个无迟到归属疑义样本；响应总数仍为48/50。
单轮短测、RTT采样、解码帧率及无增长采样区间不能扩展成长期可靠性或精确冻结时长结论。
