# 场景资产与制作

正常运行直接打开远端源 USD。USD、网格、贴图和 URDF 放在仓库外；场景 YAML 在本仓库统一管理。准备完整依赖目录，避免只复制顶层 USD 而遗漏其引用文件。绝对引用需要在目标机器保持有效；相对引用适合整体搬运。Isaac 内置 MDL 模块由固定版本的安装环境提供。

## 选择场景

管理机的机器 JSON 必须明确填写 profile，例如 `"profile": "7up"` 对应远端仓库的 `config/profiles/7up.yaml`。启动按这份 YAML 选择 USD 和配套参数，不拷贝管理机 YAML，不读取资产包 current.json。

profile 中的三个服务器绝对路径分别是：

| 字段 | 作用 |
|---|---|
| scene_usd | Isaac 直接打开的源场景，修改后重启生效 |
| robot_urdf | 基础设备使用的 ROS 模型，含机械臂 joint1–joint6、夹爪 joint7/8、link6 及关节限位；网格必须可解析 |
| assembly_urdf | 含相机、支架的装配模型，显式 build 时用于生成 ROS 碰撞模型 |

当前七喜 profile 的基础 ROS URDF 位于装配修复目录，源 USD 和装配 URDF 位于模型制作目录。三者都是普通文件引用，不要求重新导入或登记。换机器时准备完整资源、按实际路径修改 profile。

初始关节角、运行频率、相机安装契约和物理增益断言也在 profile 中。光学参数由 USD 的实际镜头属性发布；增益是核对，不覆盖原厂物理参数。`scene_boxes` 是 base_link 坐标下手工估测的 MoveIt 障碍物，可将桌子简化成一个大方块；它不会修改 USD 中的桌子。七喜 profile 沿用 table_top 作为固定底座的安装接触 ID。

瓶子高度和半径是手工维护的感知先验，不包含目标真值位置。正常运行不读取制作时的场景 JSON；该文件仍可用于离线资产验收。

## 修改与新增

修改源 USD 的布景、材质，或者 profile 中的初始姿态、碰撞盒、尺寸先验与超时后，stop/start 会生成一套新运行配置，无需重新打包或编译。运行中的会话保留自己的配置快照。

新增兼容 Piper＋D405 的场景时，在 config/profiles 新建 YAML、填入对应源 USD 和模型路径，再在机器 JSON 中选择其名称。不同 profile 可以引用同一个 USD。基础设备需要已有设备程序；接入主项目时，首次为该 profile 执行 build，模型产物保存在 data_root/models/<profile>。修改机器人装配、引用网格或程序后显式重建。

接入主项目时，启动前检查会比较 robot_urdf、assembly_urdf 的解析后路径和文件 SHA-256，以及 main_project 路径；这些值与构建记录不一致时会要求重新 build。该检查不递归计算 URDF 引用网格的哈希，也不检测主项目源码内容变化。因此，仅修改 STL／DAE 内容或主项目程序时，仍须主动 build，不能依赖启动自动拒绝。基础设备模式不使用这份模型构建记录；两种模式都会在 Isaac 启动时只读校验 USD 与所用 ROS 模型的八个运动关节。

## 可选离线打包

需要迁移到另一台机器时，可以整体复制依赖目录；也可使用已有导出工具收集引用、改成包内相对路径并生成校验清单：

```bash
./piper --local --config /private/machine.json assets export \
  --scene /external/scenes/piper_7up_ros_assembly.usda \
  --robot-urdf /external/ros-model/robot.urdf \
  --assembly-urdf /external/assembly/piper_x_d405.urdf \
  --scene-metadata /external/scenes/piper_7up_grasp_scene.json \
  --output /external/bundles/7up-v1.tar.gz
```

导出只修改临时副本，输入文件保持原样。在目标机器将自己准备的归档解包到资源目录，profile 直接引用其中的 scene.usda、robot.urdf 和 assembly.urdf。没有 assets import／自动登记步骤，也不要求 runtime 依赖打包工具。bundle.json 仅是离线校验清单。

USD 无法枚举 MDL 源码内部的导入，因此导出器把自定义 MDL 所在的材质目录作为一个完整依赖单元复制，保留模块文件名、相对目录、templates 和贴图。自定义材质宜放在独立目录，归档输出必须位于这些目录之外。Isaac 内置 MDL 由安装环境提供。导出时还会检查改写后的 USD 依赖，拒绝缺失引用或残留的包外资产路径；此检查不等于 GPU 材质编译或渲染验收。

## 制作与验收工具

机器人源模型来源、来源提交与哈希见 models/sources.lock.json；装配生成工具与说明见 models/README.md。[物品库](../models/objects/README.md)和[家电库](../models/appliances/README.md)提供独立制作工具，不是启动依赖。第三方资产按各自许可使用和再分发。

七喜场景可用以下离线检查核对瓶子包围盒、桌面高度和参数清单，不启动 GPU：

```bash
bash runtime/isaac/python.sh /path/to/isaac-env checks/check_scene_contract.py \
  --scene /external/scenes/piper_7up_ros_assembly.usda \
  --manifest /external/scenes/piper_7up_grasp_scene.json \
  --output /external/reports/scene-contract.json
```

该检查面向当前七喜场景的固定 prim 和参数格式；场景真值只用于资产验收。瓶子质量、惯量与接触验收尚未完成；原厂机器人动力学已在源 USD 中。
