# 架构与接口

本工程提供仿真设备，主项目消费现有 ROS 接口。主项目以外部源码目录接入，所有仿真配置、模型适配、启动组合和协调代码留在本工程。

## 目录

| 目录 | 职责 |
|---|---|
| deploy | 系统依赖、外置编译、机器人模型和 engine 构建 |
| runtime/isaac | 唯一 Isaac 场景、物理、相机和 ROS Bridge |
| runtime/streaming | TURN 配置和 NVENC 支持 |
| runtime | 命令、SSH 所有权、进程生命周期、profile 解析与本次运行配置生成 |
| ros2/piper_sim_devices | 标准 ROS 机械臂转接与相机安装静态 TF |
| ros2/nankai_sim_integration | 现有主项目 launch 组合、碰撞安装接触、实验协调 |
| models | 机器人装配、物品制作和完整场景打包 |
| config | 基础版本、机器示例、纳入 Git 管理的场景 profiles |
| checks / tests | GPU／ROS 验收与不需要 GPU 的测试 |

## 进程与数据流

```mermaid
flowchart LR
  Manager[会话管理器] --> Isaac[一个 Isaac 进程]
  Manager --> Adapter[ROS 设备适配]
  Manager --> Experiment[按需启动实验]
  Isaac -->|RGB / 深度 / CameraInfo| Perception[原 YOLO 与几何节点]
  Experiment --> Perception
  Experiment --> W[原 M / W / Servo]
  W -->|JointTrajectory| Adapter
  Adapter -->|JointState 目标| Isaac
  Isaac -->|物理关节反馈| Adapter
  Adapter --> RSP[现有 RSP]
  Isaac --> Stream[可选 WebRTC]
```

机器配置可以让源码、SDK 和构建位于系统盘，独立 Isaac 环境和大型资产位于数据盘；不要求所有目录都使用数据盘。

机器配置通过必填 profile 选择远端仓库的场景 YAML。YAML 显式绑定源 USD、基础 ROS URDF 和装配 URDF，也包含设备运行参数、手工粗略碰撞盒与感知先验；USD 不通过资产包登记选择。同一 USD 可以对应多个 profile。

start 在会话锁内读取 profile、校验既有构建产物，将 profile 快照及 devices 配置写入 data_root/logs/session-*/config；接入主项目时另生成 scene/alignment/detector 配置。实验始终使用该会话的文件，运行中修改源 YAML 在下一次 start 生效。GPU check 使用相同生成逻辑、写入自己的报告目录；doctor 只读校验输入。启动不同步源码、不构建 engine、不编译程序。

普通 ROS 节点使用系统 Python 3.10，Isaac 使用独立 Python 3.12。环境包装器清除相互污染的 Python／动态库路径，通过同机 DDS 交换标准消息。

## 设备契约

- command：现有 namespace 下单点六轴 `JointTrajectory`，关节名 joint1–joint6，位置为弧度。不新增轨迹队列。
- joint_states：实际物理状态，不镜像位置目标。手指关节使用米。
- 相机：640×480 RGB、同一渲染产品的 `32FC1` 米制光轴 Z 深度、对应原始畸变的 CameraInfo；RGB／深度／内参采集时间一致。
- 机械臂动态 TF 和 TCP 来自现有 RSP；仿真仅发布 link6 到 optical frame 的安装静态 TF。
- 设备 launch 不启动 RSP；主项目实验 launch 或独立设备验收启动 RSP，避免重复发布动态 TF。
- `/clock` 由 Isaac 发布，业务和设备 ROS 节点使用 `use_sim_time=true`。
- 源 USD 提供质量、惯量、增益、限位和求解设置。当前机器人由统一的官方 ROS 模型生成，并在替换时保留场景驱动增益与求解设置；增益配置是断言，不是覆盖值。运行时只读校验 USD 与 ROS 的八个运动关节，不匹配就报错；不再修改关节锚点，结果记录为 device-model.json 的 kinematic_validation。
- 感知业务只接收渲染与 ROS 数据。USD 真值只用于离线验收。

基础设备使用 profile 指定的已验证 ROS URDF；接入主项目后，显式 build 使用其当前模型生成器生成仿真侧模型，并加入相机、支架碰撞。模型按 profile 存于 data_root/models/<profile>，记录两个 URDF 的路径和文件哈希；引用网格不纳入启动时的变更检测，装配或网格变化仍需显式 build，详见[资产说明](assets.md#修改与新增)。桌面等环境碰撞盒由 profile 手工维护、实验启动时注入 W，独立于 USD 物理碰撞体。

碰撞规则允许固定底座与桌面的安装接触。生成的 SRDF 保留固定装配、直接相邻部分的豁免，并针对当前已确认的安装方式额外将 link4—sim_d405_bracket 标为 Never，跳过该对的距离减速和接触检查。支架几何保留，其余非豁免碰撞对继续检查；更换支架或安装方式需重新核对，依据见[模型说明](../models/README.md)。

## 生命周期

串流采用既定的 AutoDL TCP／TURN／HAProxy 链路，SSH 管理会话。网络拓扑及不能简化的环节见[串流网络约束](streaming-network.md)，本轮只整理所属代码与生命周期。

管理器拥有设备和可选串流进程；ROS launch 不再创建 Isaac。实验有独立进程组，取消或实验结束不关闭设备。关闭设备同时清理实验。

实验组合通过独立的 `ros2 launch` 进程调用主项目原有 `grasp_alignment.launch.py`。其全局退出检查只观察自身业务节点，仿真协调器正常完成或取消不会被误判为主项目故障；这个边界由仿真侧处理。

同一数据目录的运行、GPU 验收、部署和构建共用排他锁，Isaac 另有 GPU 0 排他锁。离线 assets export 不使用会话锁，assets import 已移除。清理只针对本会话进程组，以 PID starttime 避免 PID 复用。管理 SSH 断开会终止所属服务器管理器及子进程。`watchdog_seconds` 默认 4500，设为 0 可关闭会话时限。

本轮不提供独立于管理 SSH 的后台 supervisor，不保留旧 relay 路线。
