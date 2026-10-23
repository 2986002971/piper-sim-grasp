# Piper X + D405 模型准备

运行 `prepare.py`，将所需上游描述/网格下载到指定仓库外 `--data-dir`，逐文件校验并保存来源；
`--download-only` 仅准备缓存，`--offline` 禁止模型/支架网络访问，`--urdf-only` 不启动 Isaac。
日常部署通过 profile 直接引用已准备的源 USD 和配套 URDF，见 [部署说明](../docs/deployment.md)。离线打包是可选迁移工具；支架制作与认证见下文。

机械臂和 100 mm 夹爪固定使用 [AgileX 官方 ROS 模型](https://github.com/agilexrobotics/agx_arm_urdf/tree/f6642ce0d7872c686f29c99e9e10cd23d1d49313/piper_x)，
与主工程 `pyagxarm` 的 `reference_piper_x` 来源一致。`joint7/8` 各行程 50 mm，去除虚拟宽度关节和 mimic 后，使用 `joint7=width/2`、`joint8=-width/2`。
显示 DAE 和碰撞 STL 取自该模型。接入当前主项目时，部署以宏驱动的 SDK MDH 名义模型为关节基准（腕部 35.26 mm，J6 ±π），生成匹配的 ROS 模型和 USD 派生层，通过 data_root/models/<profile> 访问。
运行时只检查 USD 和 ROS 的八个运动关节，不再修改 USD 关节锚点来适配另一套模型。

碰撞体的形状和尺寸来自原装配。新的控制派生层继承源 USD 的机器人质量、惯量、驱动和碰撞过滤，只显式对齐关节坐标及位置限位。RSP 使用派生 ROS 模型，基座固定别名提供 piper_base_link；相机光学安装保持单独的静态 TF。

旧 W 路线曾使用 SRDF 的 link4—sim_d405_bracket 豁免。新宏驱动不运行 MoveIt 规划场景或距离减速，该 SRDF 不再生成或参与运行；PhysX 的实际碰撞配置保留。模型历史依据仍见下文。

D405 壳体和名义框架沿用锁文件中的 RealSense 描述，毫米网格缩放 0.001。
RealSense 描述来源提交 `bafc21080c5c8e259dadbb309797949aee0dd950`、Apache-2.0 许可证和逐文件来源均在 `sources.lock.json` 中记录。
生成目录分别保留机械臂和 RealSense 的许可证。

`handeye.example.yaml` 来自项目原标定记录，是示例而非通用标定。
有支架时默认按机械安装孔和安装面定位相机；`--camera-placement handeye` 改为按 `--handeye` 指定的刚体矩阵定位。
无支架时使用手眼位置。生成器保留相机光学框架与壳体之间的名义变换，并验证最终 `link6` → `d405_color_optical_frame`。

支架保持 STEP 的实际尺寸，按官方 ROS 夹爪底面 12 mm 孔距重新安装，保留 0.2 mm 安装面间隙。
夹爪安装孔中点在 `link6` 下为 `[-0.205552, -37.982677, 50.497770]` mm。
相机侧 20 mm 孔距与 D405 背面两孔相配，背面贴合支架安装面。
`assembly.json` 记录最终支架姿态、光学变换和相机孔位残差。这些位置来自固定版本 CAD，仍需以实体测量确认。

## 2026-10 装配差异及修复依据

这里的“旧版/新版”指模型描述来源，不是已确认的机器人硬件版本：

- 原仿真使用 [piper_isaac_sim 的 CAD 模型](https://github.com/agilexrobotics/piper_isaac_sim/tree/8e1f88fdb7afca49c40e9a0c1c01cc588e86f0d2)。其 `gripper_base_100_v2.dae` 节点缩放为 0.001305994，再乘 URDF 的 0.8，相对毫米原始几何仍为 1.0447952 倍。因此不能把 0.8 解释为整个实际夹爪缩小了 20%，也不能直接去掉它。
- ROS 官方模型的对应夹爪 CAD 部件使用 0.001，孔距为 12 mm；原仿真模型为约 12.5375 mm。四个共有 CAD 实体逆缩放并刚体配准后，18,332 个唯一顶点的最大残差约 0.000077 mm。
- 两套末端关节也不完全一致：J5 原点从 `[76.857, 0.62395, 0]` mm 变成 `[74.66, 0, 0]` mm；原来运行时将后者写入前者，形成混合装配。
- 原夹爪安装孔中点为 `[-0.150584, -40.170493, 44.907265]` mm。复制该安装变换到 ROS 夹爪会将支架沿腕部 Z 方向放近约 5.59 mm；修复使用对应孔位刚体重装，支架不缩放。
- 离线三角网格碰撞复现：初始 J5=-0.17006、J6=-0.7 rad 时，混合装配的 link4 与支架相交；正确重装后距离约 4.133 mm。该数值仅代表测试姿态，不是全工作空间最小距离。旧 W 路线此后单独豁免了 link4—sim_d405_bracket；该历史 SRDF 规则已随 W 退出当前运行，见[验收记录](../docs/verification.md)。

主工程在 2026-09-23 的模型校核中记录采用官方 ROS 模型，并于 09-26 提交；仿真于 10-10 仍使用 CAD 模型，10-13 增加了关节映射。此次统一模型并重新装配，避免混用。
制作新的基础装配并替换整个机器人时，先用 `prepare.py` 生成新资产，再运行 `replace_robot.py --scene OLD --robot-usd ASSEMBLY --output NEW`。
该脚本保留场景环境、机器人基座位置和驱动增益，移除旧机器人上的质量、关节位置等覆盖，采用新 URDF 的定义。输出必须是新文件；在 profile 中更新场景、装配 URDF 和 `camera_mount`，重新 build/check 后使用。

源缓存有效时直接复用；缺失或损坏的离线缓存会失败，下载/哈希失败不会启动不完整场景。
生成器使用缓存锁和临时目录，只有导入/验证成功后才发布完整场景。USD 检查米单位、夹爪/相机框架、
相机光学变换、几何及所有引用；生成目录有 `complete.json` 文件校验表，输出检查失败要修复缓存后再启动。
`--scene /path/to/existing.usda` 直接验证使用外部完整 USD，绕过下载、xacro 和 STEP 转换。

官方支架 ZIP 内 `405相机支架.STEP` 与原项目 STEP SHA-256 相同：
`09e16217aae82f9f4c349b1a563d972f6b6164c7b178209bd0582149059fa02e`。
稳定官方附件需要登录；无有效缓存/认证时默认无支架并提示。
可用 `--bracket-cookie-file` 指定仓库外的 Netscape/Mozilla cookie 文件，或用 `--bracket` 提供已有 STL。
STEP 转换依赖 `requirements-step-conversion.txt` 的 OpenCascade wheel；外部 STL 可直接 `--bracket`（毫米）。
生成资产提供 D405 的 optical frame 与 Camera。正式运行统一通过根目录 `piper` 入口；
当前主项目接入使用 deploy/prepare-grasp-model.py 与 models/prepare_control_scene.py，在 data_root/models 下制作模型和引用源场景的派生层；不会覆盖源 USD。
RGB、光轴深度与 CameraInfo 由同一传感器发布，相机预览复用该渲染产品；WebRTC 串流显示当前 Isaac 视口。源场景的选择、可选离线打包和迁移见
[资产管理](../docs/assets.md)。原始模型制作与日常部署相互独立；运行时由 profile 选择源场景，启动不执行模型制作或资产导入。

Isaac Sim 6.1 使用 `URDFImporter` / `URDFImporterConfig`，旧 `URDFParseAndImportFile` 已移除；
见 [官方迁移说明](https://docs.isaacsim.omniverse.nvidia.com/latest/migration_guides/isaac_sim_6_0/urdf_mjcf_importer_exporter_pipeline.html)。
生成入口要求实际存在 USD 输出，并在搬到外部缓存后再次验证引用与光学变换；退出码为零本身不能证明生成成功。

其他制作工具见 [物品库](objects/README.md) 与 [家电库](appliances/README.md)。
