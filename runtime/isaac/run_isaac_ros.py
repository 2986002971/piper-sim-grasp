#!/usr/bin/env python3
"""Run the supplied scene with native Isaac ROS camera, clock and arm devices."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import select
import signal
import sys
import time
import traceback

import yaml


def calibration_fixture(camera):
    """Temporary optical-frame target; never used by business perception."""
    from pxr import Gf, Sdf, UsdGeom, UsdShade
    stage = camera.GetPrim().GetStage()
    root = str(camera.GetPrim().GetParent().GetPath())+'/CalibrationFixture'
    fixture = UsdGeom.Xform.Define(stage, root)
    # Keep the target ahead of the gripper and table, so it is unoccluded.
    fixture.AddScaleOp().Set(Gf.Vec3f(.1, .1, .1))
    def block(name, position, size, color):
        cube = UsdGeom.Cube.Define(stage, root+'/'+name)
        cube.CreateSizeAttr(1.)
        cube.AddTranslateOp().Set(Gf.Vec3d(*position))
        cube.AddScaleOp().Set(Gf.Vec3f(*size))
        material = UsdShade.Material.Define(stage, root+'/Materials/'+name)
        shader = UsdShade.Shader.Define(stage, str(material.GetPath())+'/Shader')
        shader.CreateIdAttr('UsdPreviewSurface')
        shader.CreateInput('diffuseColor', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color))
        shader.CreateInput('emissiveColor', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color))
        material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), 'surface')
        UsdShade.MaterialBindingAPI.Apply(cube.GetPrim()).Bind(material)
    block('background', [0., 0., .46], [.7, .54, .001], [.15, .15, .15])
    points = [[-.25, -.15, .4495], [.25, -.15, .4495],
              [-.25, .15, .4495], [.25, .15, .4495]]
    for i, (point, color) in enumerate(zip(points, [(1., 0., 0.), (0., 1., 0.),
                                                  (0., 0., 1.), (1., 0., 1.)])):
        block('marker'+str(i), point[:2]+[.45], [.012, .012, .001], color)
    return [[value*.1 for value in point] for point in points]


def camera_writers(camera, config):
    import omni.replicator.core as rep
    from isaacsim.sensors.experimental.rtx import RtxCamera
    from pxr import Usd

    prim = camera.GetPrim()
    prefix = 'omni:lensdistortion:opencvPinhole:'
    if prim.GetAttribute('omni:lensdistortion:model').Get() != 'opencvPinhole':
        raise ValueError('The simulated D405 requires an authored OpenCV pinhole lens')
    width, height = map(int, prim.GetAttribute(prefix+'imageSize').Get())
    lens = {name: float(prim.GetAttribute(prefix+name).Get())
            for name in ('fx', 'fy', 'cx', 'cy', 'k1', 'k2', 'p1', 'p2', 'k3')}
    fx, fy, cx, cy = (lens[name] for name in ('fx', 'fy', 'cx', 'cy'))
    with Usd.EditContext(prim.GetStage(), prim.GetStage().GetSessionLayer()):
        RtxCamera(str(camera.GetPath()), tick_rate=config.get('camera_hz', 30.),
                  reset_xform_op_properties=False)
        product = rep.create.render_product(str(camera.GetPath()), (width, height))
        common = dict(frameId=config.get('optical_frame', 'd405_color_optical_frame'),
                      nodeNamespace='', context=0, queueSize=1,
                      qosProfile=json.dumps({'history': 'keepLast', 'depth': 1,
                                             'reliability': 'bestEffort', 'durability': 'volatile',
                                             'deadline': 0., 'lifespan': 0.,
                                             'liveliness': 'systemDefault', 'leaseDuration': 0.}))
        writers = []
        for name, topic in [('LdrColorSDROS2PublishImage', config['rgb_topic']),
                            ('DistanceToImagePlaneSDROS2PublishImage', config['depth_topic'])]:
            writer = rep.writers.get(name)
            writer.initialize(topicName=topic, **common)
            writer.attach([product.path])
            writers.append(writer)
        info = rep.writers.get('ROS2PublishCameraInfo')
        info.initialize(topicName=config['camera_info_topic'], width=width, height=height,
                        projectionType='plumb_bob',
                        k=[fx, 0., cx, 0., fy, cy, 0., 0., 1.],
                        r=[1., 0., 0., 0., 1., 0., 0., 0., 1.],
                        p=[fx, 0., cx, 0., 0., fy, cy, 0., 0., 0., 1., 0.],
                        physicalDistortionModel='plumb_bob',
                        physicalDistortionCoefficients=[lens[n] for n in ('k1', 'k2', 'p1', 'p2', 'k3')],
                        **common)
        info.attach([product.path])
        writers.append(info)
    return writers, product, dict(lens, width=width, height=height)


def create_joint_graph(root, camera, config):
    import omni.graph.core as og
    from pxr import UsdGeom

    cache = UsdGeom.XformCache()
    optical = camera.GetPrim().GetParent()
    wrist = optical
    while wrist and wrist.GetName() != 'link6':
        wrist = wrist.GetParent()
    if not wrist:
        raise ValueError('D405 optical frame must belong to Piper link6')
    mount = cache.GetLocalToWorldTransform(optical) * cache.GetLocalToWorldTransform(wrist).GetInverse()
    quaternion = mount.ExtractRotationQuat()
    rotation = [*map(float, quaternion.GetImaginary()), float(quaternion.GetReal())]
    translation = list(map(float, mount.ExtractTranslation()))
    keys = og.Controller.Keys
    graph, _, _, _ = og.Controller.edit(
        {'graph_path': '/PiperRosDevices', 'evaluator_name': 'execution',
         'pipeline_stage': og.GraphPipelineStage.GRAPH_PIPELINE_STAGE_ONDEMAND}, {
            keys.CREATE_NODES: [
                ('Tick', 'isaacsim.core.nodes.OnPhysicsStep'),
                ('Time', 'isaacsim.core.nodes.IsaacReadSimulationTime'),
                ('Context', 'isaacsim.ros2.bridge.ROS2Context'),
                ('Clock', 'isaacsim.ros2.bridge.ROS2PublishClock'),
                ('Feedback', 'isaacsim.ros2.bridge.ROS2PublishJointState'),
                ('Command', 'isaacsim.ros2.bridge.ROS2SubscribeJointState'),
                ('Control', 'isaacsim.core.nodes.IsaacArticulationController')],
            keys.CONNECT: [
                ('Tick.outputs:step', 'Clock.inputs:execIn'),
                ('Tick.outputs:step', 'Feedback.inputs:execIn'),
                ('Tick.outputs:step', 'Command.inputs:execIn'),
                ('Command.outputs:execOut', 'Control.inputs:execIn'),
                ('Command.outputs:jointNames', 'Control.inputs:jointNames'),
                ('Command.outputs:positionCommand', 'Control.inputs:positionCommand'),
                ('Time.outputs:simulationTime', 'Clock.inputs:timeStamp'),
                ('Time.outputs:simulationTime', 'Feedback.inputs:timeStamp'),
                ('Context.outputs:context', 'Clock.inputs:context'),
                ('Context.outputs:context', 'Feedback.inputs:context'),
                ('Context.outputs:context', 'Command.inputs:context')],
            keys.SET_VALUES: [
                ('Context.inputs:useDomainIDEnvVar', True), ('Time.inputs:resetOnStop', False),
                ('Clock.inputs:topicName', '/clock'),
                ('Feedback.inputs:topicName', config['bridge_feedback_topic']),
                ('Feedback.inputs:targetPrim', [str(root.GetPath())]),
                ('Command.inputs:topicName', config['bridge_command_topic']),
                ('Control.inputs:robotPath', str(root.GetPath()))]})
    return graph, dict(parent_frame='link6', child_frame=config.get('optical_frame', 'd405_color_optical_frame'),
                       translation=translation, rotation_xyzw=rotation)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--control-stdin', action='store_true', help='Owned acceptance-test control pipe')
    parser.add_argument('--camera-calibration', action='store_true', help='Session-only projection test target')
    parser.add_argument('--stream', action='store_true', help='Expose this same device scene over WebRTC')
    args = parser.parse_args()
    config_path = args.config.resolve()
    config = yaml.safe_load(config_path.read_text())
    for key in ('scene_usd', 'output_dir', 'urdf_file'):
        path = Path(config[key])
        config[key] = str(path if path.is_absolute() else config_path.parent/path)
    output = Path(config['output_dir'])
    output.mkdir(parents=True, exist_ok=True)
    gpu_lock = open(config.get('gpu_lock', '/tmp/piper-sim-gpu0.lock'), 'a')
    try:
        fcntl.flock(gpu_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError('Another Piper Isaac session already owns GPU 0') from None
    from isaacsim.simulation_app import SimulationApp
    options = {'headless': True, 'width': 640, 'height': 480,
                         'active_gpu': 0, 'physics_gpu': 0, 'multi_gpu': False,
                         'open_usd': config['scene_usd'], 'renderer': 'RayTracedLighting',
                         'extra_args': ['--/isaac/startup/ros_bridge_extension=isaacsim.ros2.bridge']}
    experience = None
    if args.stream:
        options.update(hide_ui=False, width=1920, height=1080, limit_cpu_threads=16)
        options['extra_args'] += [
            '--/exts/omni.kit.livestream.app/primaryStream/publicIp='+os.environ['STREAM_PUBLIC_IP'],
            '--/exts/omni.kit.livestream.app/primaryStream/signalPort=6006',
            '--/exts/omni.kit.livestream.app/primaryStream/streamPort=47998',
            '--/exts/omni.kit.livestream.app/primaryStream/allowDynamicResize=false']
        experience = str(Path(config['isaac_env'])/'lib/python3.12/site-packages/isaacsim/apps/isaacsim.exp.full.streaming.kit')
    app = SimulationApp(options, **({'experience': experience} if experience else {}))
    writers = []
    preview = None
    exit_code = 0
    stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        import numpy as np
        import omni.graph.core as og
        import omni.kit.app
        import omni.timeline
        import omni.usd
        from isaacsim.core.api import SimulationContext
        from isaacsim.core.prims import SingleArticulation
        from isaacsim.core.utils.types import ArticulationAction
        from pxr import Usd, UsdGeom, UsdPhysics, UsdLux

        omni.kit.app.get_app().get_extension_manager().set_extension_enabled_immediate('isaacsim.ros2.bridge', True)
        for _ in range(30): app.update()
        context = omni.usd.get_context()
        stage = context.get_stage()
        # Full streaming experience may replace open_usd with its default
        # World0 during startup. Open explicitly after extensions initialize.
        if stage is None or os.path.realpath(stage.GetRootLayer().identifier) != os.path.realpath(config['scene_usd']):
            if not context.open_stage(config['scene_usd']):
                raise RuntimeError('Could not open configured device scene')
            for _ in range(30): app.update()
            stage = context.get_stage()
        viewport = None
        if args.stream:
            from omni.kit.viewport.utility import get_active_viewport, frame_viewport_prims
            viewport = get_active_viewport()
            if viewport is None:
                raise RuntimeError('Streaming experience has no active viewport')
            # The physics APIs use the default USD context. Never initialize
            # another stage merely because it has the same root layer path.
            if viewport.stage != stage:
                raise RuntimeError('Viewport and physics use different USD contexts')
        stage.SetEditTarget(stage.GetSessionLayer())
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'models'))
        from d405_camera import ensure_d405_camera, ensure_d405_depth_camera
        ensure_d405_camera(stage)
        if not any((p.IsA(UsdLux.DomeLight) or p.IsA(UsdLux.DistantLight) or
                    p.IsA(UsdLux.RectLight) or p.IsA(UsdLux.SphereLight)) and
                   UsdGeom.Imageable(p).ComputeVisibility() != 'invisible' for p in stage.Traverse()):
            light = UsdLux.DomeLight.Define(stage, '/SimulationEnvironmentLight')
            light.CreateIntensityAttr(1000.)
        if UsdGeom.GetStageMetersPerUnit(stage) != 1. or UsdGeom.GetStageUpAxis(stage) != 'Z':
            raise ValueError('Scene must use meters and Z up')
        # The 6.1 importer leaves its Physics variant unselected in the CAD
        # assembly. Activate PhysX in this run's session layer.
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            for prim in list(stage.Traverse()):
                variants = prim.GetVariantSets().GetVariantSet('Physics')
                if 'physx' in variants.GetVariantNames():
                    variants.SetVariantSelection('physx')
        roots = ([stage.GetPrimAtPath(config['articulation_prim'])] if config.get('articulation_prim')
                 else [p for p in stage.Traverse() if p.HasAPI(UsdPhysics.ArticulationRootAPI)])
        cameras = ([stage.GetPrimAtPath(config['camera_prim'])] if config.get('camera_prim')
                   else [p for p in stage.Traverse() if p.GetName() == 'D405_RGB' and p.IsA(UsdGeom.Camera)])
        if len(roots) != 1 or len(cameras) != 1:
            raise ValueError(f'Expected one Piper articulation and D405_RGB: {len(roots)}, {len(cameras)}')
        root, camera = roots[0], UsdGeom.Camera(cameras[0])
        from validate_ros_kinematics import validate
        kinematics = validate(stage, root, config['urdf_file'])
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            physics_scenes = [p for p in stage.Traverse() if p.IsA(UsdPhysics.Scene)]
            if len(physics_scenes) > 1:
                raise ValueError('Expected a single authored physics scene')
            physics_path = str(physics_scenes[0].GetPath()) if physics_scenes else '/SimulationPhysicsScene'
            simulation = SimulationContext(physics_dt=1./config.get('physics_hz', 100.),
                                           rendering_dt=1./config.get('camera_hz', 30.), stage_units_in_meters=1.,
                                           physics_prim_path=physics_path, set_defaults=not physics_scenes)
            simulation.get_physics_context().enable_fabric(False)
            simulation.initialize_physics()
            robot = SingleArticulation(prim_path=str(root.GetPath()), name='piper_ros')
            robot.initialize()
            controller = robot.get_articulation_controller()
            names = [f'joint{i}' for i in range(1, 9)]
            indices = np.array([robot.get_dof_index(name) for name in names])
            kps, kds = controller.get_gains()
            np.testing.assert_allclose(kps[indices],
                [config['arm_drive_stiffness']]*6 + [config['gripper_drive_stiffness']]*2, rtol=1e-5)
            np.testing.assert_allclose(kds[indices],
                [config['arm_drive_damping']]*6 + [config['gripper_drive_damping']]*2, rtol=1e-5)
            width = config.get('gripper_width_m', .05)
            positions = np.array(config['initial_joint_positions'] + [width/2., -width/2.])
            robot.set_joint_positions(positions, joint_indices=indices)
            controller.apply_action(ArticulationAction(
                joint_positions=positions, joint_indices=indices))
            graph, mount = create_joint_graph(root, camera, config)
            np.testing.assert_allclose(config['camera_mount'], mount['translation']+mount['rotation_xyzw'], atol=1e-6)
            points = calibration_fixture(camera) if args.camera_calibration else []
            writers, product, lens = camera_writers(camera, config)
            if viewport is not None:
                from preview import Preview
                preview = Preview(viewport, camera, ensure_d405_depth_camera(stage, camera), product)
                frame_viewport_prims(viewport, prims=[str(root.GetPath().GetParentPath())])
        timeline = omni.timeline.get_timeline_interface()
        timeline.play()
        (output/'device-model.json').write_text(json.dumps(
            dict(scene_usd=config['scene_usd'], articulation_prim=str(root.GetPath()),
                 camera_prim=str(camera.GetPath()), lens=lens, mount=mount,
                 physics_scene=physics_path,
                 calibration_points_optical_m=points, kinematic_validation=kinematics,
                 drive_stiffness=kps[indices].tolist(), drive_damping=kds[indices].tolist()), indent=2)+'\n')
        print('PIPER_ISAAC_READY', flush=True)
        if args.stream: print('PIPER_STREAM_INITIALIZED', flush=True)
        print('PIPER_ROS_DEVICES_READY', flush=True)
        last_status = 0.
        while app.is_running() and not stopping:
            if args.control_stdin and select.select([sys.stdin], [], [], 0)[0]:
                command = sys.stdin.readline().strip()
                if command in ('', 'stop'): break
                if command == 'pause': timeline.pause()
                elif command == 'resume': timeline.play()
            app.update()
            if preview is not None: preview.update()
            now = time.monotonic()
            if now-last_status >= 1.:
                cache = UsdGeom.XformCache()
                optical = camera.GetPrim().GetParent()
                state = dict(playing=timeline.is_playing(), simulation_time_s=simulation.current_time,
                             joint_positions=robot.get_joint_positions(joint_indices=indices).tolist(),
                             world_T_articulation_root=np.array(cache.GetLocalToWorldTransform(root)).T.tolist(),
                             world_T_optical=np.array(cache.GetLocalToWorldTransform(optical)).T.tolist())
                temporary = output/'device-state.json.tmp'
                temporary.write_text(json.dumps(state)+'\n')
                temporary.replace(output/'device-state.json')
                last_status = now
    except Exception:
        exit_code = 1
        traceback.print_exc()
        sys.stderr.flush()
        raise
    finally:
        if preview is not None: preview.close()
        for writer in writers:
            writer.detach()
        app.close(exit_code=exit_code)


if __name__ == '__main__':
    main()
