# RoboCup@Home 2026 物品模型

收集与制作比赛仿真所需的物品类别样本，补齐贴图、碰撞体与明确记录来源的物理参数。
物品库独立于基础 Piper 场景部署；暂不包含包、篮子和可变形纺织品。

当前清单包含 56 个物品。历史生成批次和运行结果见[验收记录](verification.md)。

| 类别 | 数量 | 已收集示例 |
| --- | ---: | --- |
| 小型餐具 | 6 | YCB 和 Kenney 两套刀、叉、勺 |
| 餐饮容器 | 12 | 金属碗/杯/盘、陶瓷碗/杯/盘、水壶、简化无柄杯等 |
| 食品包装 | 12 | 麦片盒、牛奶纸盒、饼干盒、糖盒、调料瓶、罐头等 |
| 食品 | 13 | 水果样本、汉堡、三明治、饭团、寿司等 |
| 饮料 | 2 | 简化单罐饮料、饮料瓶 |
| 垃圾形状样本 | 5 | 压扁罐、开口罐、糖果包装、鱼骨、冰棍棒 |
| 通用家用物品 | 6 | 清洁剂瓶、喷瓶、木块、剪刀、马克笔、螺丝刀 |

垃圾/废弃饮品是任务语义；相同包装可用于食品、饮料或垃圾任务。
`category` 是资产组织字段，不代替比赛规则判断。剪刀当前只有固定网格，无开合关节。
YCB 水果是基准套装中的仿真水果，其参考质量不能当成真实新鲜水果的质量。

所有缓存与生成资产放在仓库外，通过 `--data-dir` 指定。
缓存不进 Git，仓库只保存清单、来源哈希、脚本与说明。

- `archives/`：校验过的下载包；收集器离线重建需要这些包，删除后须重新下载。
- `sources/<bundle-id>/`：源网格、材质、贴图及可用的上游元数据。
- `sources/<bundle-id>/normalized/`：Google 模型的贴图路径修正副本。
- `sources/<bundle-id>/provenance.json`：来源、逐文件哈希及物品检查结果。
- `inventory.json`：本次选定模型包的汇总、视觉网格路径、包围盒和物理参数状态。

## 来源与许可

| 来源 | 本次使用 | 许可与归属 | 适用范围 |
| --- | --- | --- | --- |
| [YCB 官方下载目录](https://ycb-benchmarks.s3.amazonaws.com/index.html) | 28 个 Google 16k 扫描模型 | CC BY 4.0；Berk Calli、Arjun Singh、Aaron Walsman、Siddhartha Srinivasa、Pieter Abbeel、Aaron Dollar | 首选抓取几何与贴图；参考质量见原论文 |
| [Google Scanned Objects 官方介绍](https://research.google/blog/scanned-objects-by-google-research-a-dataset-of-3d-scanned-common-household-items/) / GoogleResearch Fuel | 麦片盒、陶瓷碗、陶瓷马克杯、陶瓷盘，共 4 个，固定版本 1 | CC BY 4.0；Google LLC；Open Robotics 托管。包内 SDF 和 metadata.pbtxt 也保留声明 | 补充写实包装和陶瓷餐具 |
| [Kenney Food Kit 官方页面](https://kenney.nl/assets/food-kit) | 2.0 包内筛选 24 个模型 | CC0 1.0；Kenney，包内 License.txt 保留 | 牛奶纸盒、单罐饮料、无柄杯、食品和垃圾的简化模型 |

第三方模型不适用本项目 MIT 许可证。YCB/GSO 的归属、许可和来源链接写入 catalog 与缓存 provenance，
分发模型或修改版本时保留这些记录。下载包 SHA-256 是本次从公开上游取得文件后的内容固定值，
不是上游作者另行公布的校验签名。

未采用的候选：[CGTrader “CC0 - Milk Carton”](https://www.cgtrader.com/free-3d-models/food/beverage/milk-carton-fb4f5ab1-c7a9-44bc-9795-e2f85e428fbc)。
页面标题标 CC0，但许可字段显示 Royalty Free License (no AI)，所以当前不作为已确认 CC0 资产下载。
GSO 名称含 Pepsi 或 sparkling water 的候选多有 8/12 罐整箱包装，未把名称当作单个饮料罐纳入本批。

## 下载、离线重建与检查

仅依赖 Python ≥3.10 标准库，Linux 下用文件锁避免同时写缓存。

```bash
python3 models/objects/collect.py --list
python3 models/objects/collect.py \
  --data-dir /path/outside/repo/objects-v1
# 离线收集要求 archives/ 中保留完整、校验通过的原始包：
python3 models/objects/collect.py \
  --data-dir /path/outside/repo/objects-v1 --offline
python3 models/objects/collect.py \
  --data-dir /path/outside/repo/objects \
  --only ycb_030_fork ycb_031_spoon ycb_032_knife
python3 -m unittest discover -s tests -p test_object_collection.py -v
```

脚本验证下载包哈希、拒绝路径越界和归档链接，检查必要文件、有限顶点坐标、面数据存在、
OBJ→MTL→贴图引用，记录顶点/面/UV 数量、原始坐标包围盒及 SDF 中实际存在的参数。
物理生成脚本直接使用 `sources/` 和 `inventory.json`，不需要保留原始下载包。
Kenney 只解包选中的 OBJ、MTL、贴图、预览和许可证，包/袋模型没有加入资产清单。
离线模式从校验通过的源包重新解包，损坏的归档会失败；失败时不发布汇总 inventory。
`--only` 生成该次选定子集的 inventory，不代表其他已有缓存丢失。

4 个 GSO 包的原始 MTL 写 `map_Kd texture.png`，贴图却在 `materials/textures/texture.png`。
脚本保留原始 OBJ/MTL，在 normalized 中创建修正相对路径的副本；inventory 的 visual_mesh 指向该副本。
原始 SDF 保留作来源记录，它仍引用原始 OBJ；后续导入应使用 inventory 指定的视觉网格。

收集脚本的检查仅表示素材文件与引用可解析；物理资产制作及运行验证由下文的独立脚本完成。

## 上游物理数据与制作依据

清单中的 `physics` 明确区分已知数据和缺失数据，不填伪造的默认惯量或摩擦系数。

| 字段 | 当前状态 | 下一步 |
| --- | --- | --- |
| `mass_kg` | 28 个 YCB 条目记录[原论文 Table I](https://people.eecs.berkeley.edu/~pabbeel/papers/2015-ICAR-YCB-object-set.pdf)中的参考质量；其余为 null | 对应比赛实物称重，区分空包装、满载与剩余内容物 |
| `center_of_mass_m` | 全部 null | 与规范化坐标系一致；壳体/内容物分别建模或测定 |
| `inertia_kg_m2` | 全部 null | 记录完整对称 3×3 张量、参考点和坐标系；若导出主惯量，同时记录主轴姿态 |
| `contact_material` | 全部 null | 标定静/动摩擦、恢复系数及 combine mode；同时记录夹爪与桌面材料 |
| `collision_plan` | 上游清单记录制作方案 | 生成独立凸体；保留杯碗入口，进一步检查杯把、瓶盖、口沿和罐体 |
| 单位、姿态 | 保留上游坐标；YCB/GSO 按米约定记录，Kenney 为未标定美术单位 | 检查尺寸、统一米与 Z-up，保留源到资产的变换，不把包围盒中心当作重心 |

实例：YCB 碗的网格包围盒约 161×161×55 mm，马克杯约 117×93×81 mm（含杯把）；
GSO 麦片盒约 203×56×282 mm。Kenney 牛奶盒的原始坐标跨度约 0.23×0.59×0.23，
不能直接当成比赛实物的米制尺寸，须依实物缩放。

4 个 GSO 样本的 SDF 与 metadata 实查均没有质量/惯量，且 SDF 碰撞直接引用视觉网格；
这些原始 SDF 不能代替下文生成并检查的物理 USD。YCB 的 kinbody.xml 也不能代替 Isaac Sim 的刚体参数制作。

按 [Isaac Sim 物理文档](https://docs.isaacsim.omniverse.nvidia.com/latest/physics/simulation_fundamentals.html)，
动态物体默认凸包会封闭孔洞；杯/碗等需验证凸分解、手工组合凸体或 SDF 碰撞的效果。
薄物品还需检查 contact/rest offset 与 CCD。视觉材质和物理接触材质分别配置。
官方另有 [YCB USD 使用示例](https://docs.isaacsim.omniverse.nvidia.com/latest/replicator_tutorials/tutorial_replicator_object_based_sdg.html)，
可在实际安装版本的资产根下查看 `Isaac/Props/YCB/Axis_Aligned` 和 `Axis_Aligned_Physics`；
本批采用可独立下载的源网格，尚未验证这些官方 USD 的物理参数和本机可用性。

带内容物的倾倒需要后续选择液体或颗粒仿真方案；当前收集的是容器和食品的静态几何。
写实牛奶/饮料、纸团等地面垃圾和比赛具体款式继续保留在 catalog 的 pending 中。

## 物理资产制作入口

`physics_profiles.json` 保存全部 56 个物品的首版物理制作方案，与原始来源清单分开维护。
`catalog.json` 中的 null 表示上游没有给出该物理量；实际生成的结果在每个 USD 旁的 `physics.json`。
28 个 YCB 的质量沿用原论文参考值，另外 28 个物品使用明确标记的工程假设。
这些模型用于建立仿真基线，不能把完整参数字段理解为已经完成实物标定。

### 参数与坐标约定

- 米、千克、Z-up；资产原点为 XY 包围盒中心、几何最低点高度。
  原点只用于放置，不作为重心。`source_to_asset_transform_column_vectors` 保存源到资产的完整变换。
- YCB/GSO 保留米制尺度；Kenney 将 Y-up 转为 Z-up，按 profile 指定的样本高度或最大跨度等比例缩放。
  样本尺寸是制作假设，不是比赛物品的测量值。
- 质量记录来源、内容物状态及预设不确定度。牛奶、饮料等当前采用内容物固连的刚体假设，
  不模拟液体晃动、流出或麦片颗粒；空容器和满载包装不共用同一质量假设。
  `mass_uncertainty_fraction` 是人为设定的参数敏感性实验幅度，不是统计测量误差或置信区间。
  Kenney `can-open` 的预览和几何含静态内容物，本版采用假设装载质量 250 g，不能当作空罐腔体。
  YCB `019_pitcher_base` 的扫描表面封闭壶口；本版保留为抓取/放置代理模型，
  `state=closed_scanned_surface_grasp_proxy`，不用于开口容器或倾倒验收。
- 杯、碗、盘和薄物品采用均匀三角面薄壳分布，积分求重心与完整惯量；
  其他物品优先使用水密网格的均匀体积积分。网格开边时采用凸体体积和作为近似，
  凸体交叠没有扣除，方法明确记为 `summed_convex_volume_overlap_not_subtracted`。
  对采用表面碰撞壳且原网格开边的满载包装/实体食品，质量分布另用视觉凸包的均匀体积近似，
  记为 `uniform_visual_convex_hull_mass_proxy`，避免把满载质量错误地只分布在碰撞壳上。
  薄壳方法只近似质量分布，不自动赋予碰撞壁厚，也没有测出杯把、底座或内容物各自密度。
- 惯量参考点为计算得到的重心，完整 3×3 张量在资产坐标系中表达。
  输出主惯量与 **wxyz** 主轴四元数，并验证能够重建原张量，再写入
  [OpenUSD MassAPI](https://openusd.org/dev/api/class_usd_physics_mass_a_p_i.html)。
- 按 `box`、`convex_hull`、表面薄壳或 [CoACD](https://github.com/SarahWeiii/CoACD) 生成独立的封闭凸碰撞网格；
  不对整个杯碗应用单个凸包。CoACD 固定随机种子、米制凹度阈值与处理配置，结果缓存以便离线重建。
  凸体顶点数受限，USD 中视觉网格不参与碰撞。
  凸体在 USD 的单精度坐标下重新求凸包，避免近共面面片在序列化后失去凸性；
  主惯量和主轴重建检查也按单精度存储采用随张量大小缩放的误差界。
  Kenney 的复杂美术网格使用 1 mm 三角面挤出；只合并相连且凸的共面区域，
  保留杯口、杯把与叉齿间隙。壁厚是碰撞制作假设，壳体可能比视觉表面外扩 0.5 mm。
- 摩擦、恢复系数、阻尼、接触偏移与求解迭代数均为工程初值。接触材质分为金属、陶瓷、玻璃、
  纸板、塑料、木、食品，另设桌面与橡胶夹爪 profile。静/动摩擦与恢复系数采用 average 合并；
  实际接触响应取决于两侧材质，须对夹爪–物品、桌面–物品分别标定。
  `sweep_ranges` 为建议实验范围，不是材料测量置信区间。

### 生成和离线验证

使用 Python ≥3.11，以及 Isaac Sim 自带的 `pxr` 或独立 OpenUSD 绑定。
Linux ARM64 的制作环境可使用 conda-forge OpenUSD；历史验收实际使用的版本见[验收记录](verification.md)。
网格处理依赖固定在 `requirements-physics.txt`。生成过程不需要启动 Isaac Sim，也不访问网络。
建议在独立的制作环境安装这些依赖，保持运行 Isaac Sim 的环境原样。

```bash
python -m pip install -r models/objects/requirements-physics.txt
python models/objects/build_physics.py \
  --data-dir /path/outside/repo/objects-v1 --jobs 4
python models/objects/build_physics.py \
  --data-dir /path/outside/repo/objects \
  --only 030_fork 031_spoon 032_knife 024_bowl 025_mug
python -m unittest discover -s tests -p 'test_object*.py' -v
```

生成物位于 `physics/<generation>/<object-id>/`，包含：

- `object.usdc`：独立可移动的刚体资产，内含视觉网格、UV/法线、凸碰撞网格和两种材质绑定。
- `textures/albedo.png`：相对路径贴图，移动整个物品目录即可导入；无需回链源 OBJ。
- `physics.json`：质量、重心、完整/主惯量、接触设置、变换、碰撞数量与估算方法。
- `SOURCE.json` / 可用时的 `SOURCE-LICENSE.txt`：上游归属、许可、原始文件哈希与来源。
- `complete.json`：本资产生成文件的 SHA-256。只在离线验证成功后发布资产目录。

`physics/latest.json` 指向最近一次成功生成目录；该目录的 `physics_inventory.json` 汇总该次选定资产。
`--only` 生成子集清单。完整批次失败时不发布新的 latest；已经通过检查的单件文件可供诊断。
批处理有文件锁，凸分解按源几何和参数缓存，修改质量/摩擦通常可直接复用碰撞结果。

```bash
python models/objects/validate_physics.py /path/to/generation/physics_inventory.json
```

离线验证检查完整文件哈希、贴图依赖、单位、动态刚体、凸碰撞拓扑、物理材质绑定、
重心、惯量主轴转换及尺寸。容器另用原网格的射线建立内部点，并检查凸体是否封闭中央入口。
中央探针通过只覆盖这些点，不证明杯把孔洞、口沿所有位置或碰撞近似误差都已验证。

### 物理运行检查和实物校准

可使用 NVIDIA [独立 CPU PhysX](https://nvidia-omniverse.github.io/PhysX/ovphysx/latest/tutorials/quickstart.html)
运行生成的 USD；这是 PhysX 运行检查，Isaac Sim 的集成与机器人抓放验收单独记录。

```bash
python -m pip install ovphysx==0.6.3
python models/objects/check_dynamics.py /path/to/generation/physics_inventory.json --backend ovphysx
# 在实际 Isaac Sim Python 环境中执行相同场景检查：
python models/objects/check_dynamics.py /path/to/generation/physics_inventory.json --backend isaac
```

脚本生成 `validation_<backend>.usda` 和 `dynamics_<backend>.json`。
采用 CPU、1/240 s 步长，每个物品从底部离桌面 15 cm 的姿态落下，检查有限状态、确实下落、
最终碰撞体高度、最后一秒位移和残余速度。杯碗另设固定副本，让半径 3 mm 的小球进入中央并落至内底。
Isaac 后端通过实际安装版本的
[PhysX cooking API](https://docs.omniverse.nvidia.com/kit/docs/omni_physics/107.3/extensions/runtime/source/omni.physx/docs/api/python.html)
读取烘焙后的凸体，用它检查落地高度，同时保留生成凸体的最低高度与 `cooking_max_plane_gap_m`。
该 gap 是原顶点违反烘焙凸体半空间的最大距离，仅为几何距离的下界，不能当作完整 Hausdorff 误差。
PhysX 可能简化曲面，即使提高 hullVertexLimit 也不能保证保留所有原顶点；
本批梨和橙子在首轮落地时，生成几何最低点比实际接触面低约 3.2–3.3 mm。
小球可沿弧形碗底滚动；最终必须留在容器范围内，并以 PhysX 射线确认容器副本是支撑物。
报告分别给出每个物品与每个容器小球的结果；非全部通过时脚本返回失败。
该检查不覆盖任意初始姿态、叠放、接触标定、杯把穿入或机器人实际抓放。
实际日志中部分薄凸体不能生成 GPU 兼容烘焙数据，回退到 CPU 碰撞；
[NVIDIA 碰撞文档](https://docs.omniverse.nvidia.com/kit/docs/omni_physics/latest/dev_guide/rigid_bodies_articulations/collision.html)
说明这类凸体不支持与仅在 GPU 上运行的粒子/可变形体接触。本批验收范围为 CPU 刚体接触。

依次用实物称重/尺寸测量、倾斜台滑移与水平滑动、落球/回弹及夹爪持物实验替换 profile 假设；
对满载、空载与部分装载分别保留 profile。质量或尺寸改变后重新生成惯量与 USD，
摩擦变化后重复运行检查，并在 Isaac Sim 中执行抓取、搬运和放置验收。

Isaac 验证应使用部署机器的独立 Isaac Python，并按安装许可要求设置环境。
后端先启动 SimulationApp，再导入 USD 和制作验证场景，避免混用系统 OpenUSD 与 Kit 绑定。
报告在关闭应用之前保存；应用退出码不能代替 `dynamics_isaac.json` 中的逐项结果。
