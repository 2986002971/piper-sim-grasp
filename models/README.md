# Piper X + D405 模型准备

运行 `prepare.py`，将所需上游描述/网格下载到指定仓库外 `--data-dir`，逐文件校验并保存来源；
`--download-only` 仅准备缓存，`--offline` 禁止模型/支架网络访问，`--urdf-only` 不启动 Isaac。
日常部署使用完成的场景包，见 [部署说明](../docs/deployment.md)。支架制作与认证见下文。

上游固定为 [AgileX](https://github.com/agilexrobotics/piper_isaac_sim/tree/8e1f88fdb7afca49c40e9a0c1c01cc588e86f0d2)：

- `piper_x_description/urdf/piper_x_description.urdf`：六轴机械臂，joint7/8 各 50 mm 行程，合计 100 mm。
- `realsense2_description/urdf/_d405.urdf.xacro` 和 `meshes/d405.stl`：D405 壳体及名义框架，毫米网格缩放 0.001。
- [官方 `USD/piper_x_v1.usd`](https://github.com/agilexrobotics/piper_isaac_sim/blob/8e1f88fdb7afca49c40e9a0c1c01cc588e86f0d2/USD/piper_x_v1.usd) 确实存在，引用 `piper_x_description_d435/piper_x_description_d435.usd`，
  并提供 D435 的 Camera/ROS2 camera helper；它不是 D405 装配。完整引用层也在上游仓库，不应仅复制顶层 USD。

本入口复用上游机器人 URDF，展开上游 D405 xacro，添加可选支架并装配 D405。
RealSense 描述来源提交 `bafc21080c5c8e259dadbb309797949aee0dd950` 已记录；
源文件哈希与现有描述核对，适用上游 Apache-2.0 许可证从该提交根目录 `LICENSE` 下载。

`handeye.example.yaml` 来自项目原标定记录；它是示例而非任意设备的通用标定。
有支架时默认按机械安装孔和安装面定位相机，不采用手眼矩阵来决定最终相机位置。
`--camera-placement handeye` 可恢复按手眼矩阵定位，`--handeye` 可替换该 4×4 刚体矩阵；
无支架时使用手眼位置。脚本保留 xacro 中相机光学框架与壳体之间的名义变换，
生成后独立验证最终 Link6→`d405_color_optical_frame`。
支架在原 STEP 拟合姿态上绕默认姿态的 J5 轴方向原地翻转 180°，再按夹爪安装孔中点定位，
将翻转后的支架顶面放在夹爪安装板下侧，保留 0.2 mm 间隙，避免将支架安装板放入夹爪本体。
不使用 J5 关节的平移。
该位置来自固定版本网格测量，不是物理测量；覆盖不同 CAD 后也须重新核对安装位置。
现有夹爪网格孔距约 12.54 mm，支架对应孔距 12 mm；相机侧两者孔距均为 20 mm，
默认将 D405 壳体背面两个孔（网格坐标 X=±10 mm、Y=0、Z=−23 mm）分别对准支架相机侧两孔，
并使相机背面贴合支架朝向夹爪的安装面。相机光轴沿安装面外法线，继续朝向夹爪。
`assembly.json` 记录最终光学变换、支架姿态、相机装配依据及孔位偏差；夹爪侧原始孔距差仍然存在。

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
RGB、光轴深度与 CameraInfo 由同一传感器发布，相机预览复用该渲染产品；WebRTC 串流显示当前 Isaac 视口。完整资产的导出、导入和迁移见
[资产管理](../docs/assets.md)。原始模型制作与日常部署相互独立，默认部署使用验证完成的场景包。

Isaac Sim 6.1 使用 `URDFImporter` / `URDFImporterConfig`，旧 `URDFParseAndImportFile` 已移除；
见 [官方迁移说明](https://docs.isaacsim.omniverse.nvidia.com/latest/migration_guides/isaac_sim_6_0/urdf_mjcf_importer_exporter_pipeline.html)。
生成入口要求实际存在 USD 输出，并在搬到外部缓存后再次验证引用与光学变换；退出码为零本身不能证明生成成功。

其他制作工具见 [物品库](objects/README.md) 与 [家电库](appliances/README.md)。
