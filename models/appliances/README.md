# RoboCup@Home 2026 家电模型集合

收集与制作洗碗机、洗衣机的仿真资产，保留来源、贴图与物理参数依据。
数据与生成资产放在仓库外，使用 `--data-dir` 指定，例如 `/path/appliances-v1`。
本集合独立于基础 Piper 场景部署，网格与物理制作依赖见
[物品制作说明](../objects/README.md#生成和离线验证)。

收集到 8 个网络候选：4 个洗碗机、4 个洗衣机。
sources/ 保存可用源模型、贴图和许可证，normalized/ 保存整数顶点展开为
float32 的 GLB 兼容版。保留源 GLB 是为追溯及保留原动画，ZIP 下载包不保留。
GLB 兼容版没有改变实际坐标、节点变换或动画目标。Trimesh 4.11.2 直接读取
KHR_mesh_quantization 时会忽略归一化标记，必须使用 normalized/ 进行处理。

许可：3DAssets.dev 5 个候选声明 CC0；Scopia 2 个候选为 CC BY 3.0，必须
保留其 LICENSE.TXT，并署名 Space Mushrooms 与 Scopia Visual Interfaces
Systems, s.l.；MoonlakeAI 洗碗机按固定提交的仓库 Apache-2.0 许可记录。
catalog.json 固定来源、版本、下载哈希与许可依据。源模型均未确认对应官方
比赛指定设备；manufacturer 参考产品也不等于下载模型对应的真机。

physics-v1/ 中的主资产：
  dishwasher.usda：引用 sources/moonlake_dishwasher/moonlake_dishwasher.usdz，
    保留原贴图和 11 个关节（含世界固定关节）。门 0~90°，上下碗架 0~0.35 m。
    以 55.7 kg 同类机器为参考约束整机质量，替换上游统一 100 kg/m³ 密度。
    门主面按双层 0.6 mm 钢板 + 1.5 mm ABS 内衬 + 0.5 kg 五金估重，控制面板
    和洗涤剂盒按 ABS 几何体积估重。碗架按闭合钢丝网格体积估重。
    门质量使用表面薄壳分布，其余小部件使用均匀体积分布；主机剩余质量分配
    给外壳和假设 8 kg 的下部机械单元。分配依据与结果见 inventory.json。
    可移动部件的质量、重心、主惯量及主轴均已明确写入 USD。
    碰撞使用独立凸部件和 0.5 mm 表面薄壳；保留 U 形钢丝和内部通道。
    近似凸圆角采用最多 128 个支持顶点，记录源点到代理平面的最大间隙。
    这一数值是半空间诊断值，不等于完整 Hausdorff 距离或最终接触误差。

  washer.usda：新建参数化通用滚筒洗衣机，代码/新几何采用项目 MIT 许可。
    网络开门 GLB 的机身中心射线仍被前表面遮挡，原文件仅作为视觉候选保留。
    主版本有真实前开口、空心滚筒、门铰链、滚筒旋转关节、抽屉关节。
    参考外形 0.597 x 0.588 x 0.845 m、净重 73.9 kg、开门范围 0~160°、滚筒
    70 L。采用 R=0.25 m，由 L=0.070/(pi*R²) 计算滚筒长度，前开口半径
    0.165 m 属于设计估计。钢筒壁厚 0.7 mm，另加 1.5 kg 轮毂；门按 35%
    填充的 PC 圆环、4 mm 玻璃及 0.25 kg 五金估重。主机按 22 kg 配重、
    8 kg 电机与余量外壳估计质量分布。60/64 段圆形离散会有亚毫米轮廓误差。
    门、滚筒和抽屉都明确写入部件质量、重心和完整惯量的主轴表示。
    抽屉仅为外形碰撞代理，不模拟洗涤剂流体或完整抽屉内腔。

共同计算方法：
  各组件质量先求重心，再用平行轴定理汇总 I=sum(I_i+m_i*(|d_i|²E-d_i*d_i^T))。
  三角面薄壳使用精确表面二阶矩积分，封闭实体使用网格体积积分。
  主惯量通过对称张量特征分解计算，检查正定性和惯量三角不等式。
  铰链惯量包含重心偏置项，不能直接用重心处惯量充当铰链惯量。
  洗碗机门用 k=1.05*m*g*l 的保守线性关门弹簧近似非线性平衡机构，
    c=2*0.35*sqrt(k*I_hinge)，并由关闭姿态重心的重力矩 tau0 计算
    弹簧预紧角 theta_rest=-tau0/k。这不是实测弹簧刚度，也未模拟机械锁扣。
  洗衣机门用 c=2*I_hinge/0.5 的假设黏性阻尼，关闭位置无主动电机。
  USD 角驱动刚度/阻尼按 SI 值乘 pi/180，避免弧度/角度单位错误。
  通用接触初值：静摩擦 0.35、动摩擦 0.25、恢复系数 0.02；
    contactOffset=1 mm、restOffset=0。接触系数为工程估计，不能由材料密度推导。

依据：
  整机/门角/滚筒容量：
    https://media3.bosch-home.com/Documents/specsheet/en-GB/SMS88UW06G.pdf
    https://media3.bosch-home.com/Documents/specsheet/en-GB/WGG245S1GB.pdf
  钢 7900 kg/m³：Outokumpu Core 304 数据表。
    https://otke-cdn.outokumpu.com/-/media/files/products/core/outokumpu-core-range-datasheet.pdf
  ABS 1040 kg/m³：INEOS Styrolution Terluran GP-35。
    https://eshop.ineos-styrolution.com/Product/Terluran_Terluran-GP-35_SKU300600120829.html
  PC 1200 kg/m³：SABIC LEXAN 产品表。
    https://www.sabic.com/en/Images/Polymers-All-Products_tcm1010-42650.pdf
  玻璃 2500 kg/m³：AGC 玻璃物理性质表。
    https://www.agc-yourglass.com/sites/default/files/brochures/original/Sandblasted_InfoSheet_EN_1019.PDF
  驱动单位换算：
    https://docs.isaacsim.omniverse.nvidia.com/latest/robot_setup_tutorials/tutorial_rig_legged_robot.html

检查报告：
  inspection.json：逐个网络候选的实际尺寸、节点、动画和依赖。
    开门洗衣机 GLB 动画只控制抽屉，门为静态打开的独立几何。
    Scopia OBJ 单位没有元数据，厘米及 Y-up 仅根据尺寸推测，未自动应用缩放。
  physics-v1/validation_offline.json：质量、惯量、铰链对齐及内腔点检查。
  physics-v1/dynamics_isaac.json：实际运行结果；仅 all_passed=true 才表示通过。
    验证器单独增加 5 Hz PD 测试驱动，用于开关、限位及加载检查；源资产没有
    这些主动驱动。低/中/高三个组合同时改变门质量、被动阻尼/弹簧、接触系数，
    属于组合边界检查，不是每个参数独立的全面敏感性分析。
    洗衣机检查 9 条入口射线，并让小球从设备外部穿过实际烘焙开口进入滚筒。
    洗碗机先开门再拉碗架，先收回碗架再关门；同时推动会产生实际碰撞卡阻。
    验证使用 CPU PhysX。部分细长薄壳会触发 GPU-compatible cooking 的 CPU
    回退；这些网格不能据此宣称支持粒子或可变形物体碰撞。

摆放：两台设备的主机通过世界固定关节锚定。把引用实例平移到其他位置时，
需要同步更新该固定关节的 physics:localPos0，验证脚本 create_scene 有示例。
当前检查范围包含设备内部交互，主机固定，不评估整机倾覆或地脚滑动。

## 复现

以下命令在仓库根目录执行，需要 `models/objects/build_physics.py` 的辅助函数：

```bash
  python models/appliances/collect.py --data-dir /path/appliances-v1
  python models/appliances/collect.py --data-dir /path/appliances-v1 --offline
  python models/appliances/inspect_models.py --data-dir /path/appliances-v1
  python models/appliances/build_physics.py --data-dir /path/appliances-v1
  python models/appliances/validate_physics.py /path/appliances-v1/physics-v1
  OMNI_KIT_ACCEPT_EULA=YES <IsaacPython> models/appliances/check_dynamics.py /path/appliances-v1/physics-v1
  python -m unittest discover -s tests -p 'test_appliance_*.py'
```

估算模型尚需真机标定，适用空载干燥设备的开门、抽屉/碗架移动与刚体放置。
尚未验证机器人抓门把手，不模拟机械锁、水、洗衣机悬挂、脱水或可变形纺织品。
物品库与家电库分别生成，迁移时需各自打包完整的资产及依赖。
