#!/usr/bin/env python3
"""Render calibrated single-view RGB-D samples for the A2 bottle fitter.

Run with the Isaac Sim 6 Python environment. The robot is reduced to the
factory PiperX flange and 100 mm gripper meshes; the camera pose is derived
from the measured link6-to-D405 hand-eye transform.
"""

import argparse
import itertools
import json
import os
from pathlib import Path
import re
import struct
import time
import traceback

import numpy as np
import yaml


HERE = Path(__file__).resolve().parent
DEFAULT_GRIPPER_MESH_DIR = Path(os.environ.get("PIPER_GRIPPER_MESH_DIR", "/path/to/external/gripper-meshes"))


def _expanded_path(value):
    expanded = os.path.expandvars(str(value))
    if re.search(r"\$\{[^}]+\}", expanded):
        raise ValueError(f"unexpanded environment variable in path: {value}")
    return Path(expanded).expanduser().resolve()


def _read_stl(path):
    """Return STL triangle vertices and facet normals without extra packages."""
    raw = Path(path).read_bytes()
    if len(raw) >= 84:
        count = struct.unpack_from("<I", raw, 80)[0]
        if 84 + count * 50 == len(raw):
            dtype = np.dtype([
                ("normal", "<f4", (3,)),
                ("vertices", "<f4", (3, 3)),
                ("attribute", "<u2"),
            ])
            triangles = np.frombuffer(raw, dtype=dtype, count=count, offset=84)
            return triangles["vertices"].astype(np.float64), triangles["normal"].astype(np.float64)

    text = raw.decode("ascii", errors="strict")
    vertices = np.asarray([
        [float(v) for v in row]
        for row in re.findall(
            r"vertex\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)", text,
            flags=re.IGNORECASE,
        )
    ], dtype=np.float64)
    if not len(vertices) or len(vertices) % 3:
        raise ValueError(f"invalid STL triangle data: {path}")
    triangles = vertices.reshape((-1, 3, 3))
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    normals /= np.maximum(lengths[:, None], 1e-12)
    return triangles, normals


def _add_stl_mesh(stage, parent_path, name, mesh_path):
    from pxr import Gf, UsdGeom

    triangles, normals = _read_stl(mesh_path)
    mesh = UsdGeom.Mesh.Define(stage, f"{parent_path}/{name}")
    mesh.CreatePointsAttr([Gf.Vec3f(*map(float, p)) for p in triangles.reshape((-1, 3))])
    mesh.CreateFaceVertexCountsAttr([3] * len(triangles))
    mesh.CreateFaceVertexIndicesAttr(list(range(len(triangles) * 3)))
    mesh.CreateNormalsAttr([
        Gf.Vec3f(*map(float, n)) for n in np.repeat(normals, 3, axis=0)
    ])
    mesh.SetNormalsInterpolation(UsdGeom.Tokens.faceVarying)
    mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
    mesh.CreateDoubleSidedAttr(True)
    return mesh.GetPrim()


def _set_xform(prim, translation, rotation_xyz_deg):
    from pxr import Gf, UsdGeom

    api = UsdGeom.XformCommonAPI(prim)
    api.SetTranslate(Gf.Vec3d(*map(float, translation)))
    api.SetRotate(Gf.Vec3f(*map(float, rotation_xyz_deg)), UsdGeom.XformCommonAPI.RotationOrderXYZ)


def _add_urdf_mesh(stage, parent_path, name, mesh_path, translation, rotation_xyz_deg):
    from pxr import UsdGeom

    prim = UsdGeom.Xform.Define(stage, f"{parent_path}/{name}").GetPrim()
    _set_xform(prim, translation, rotation_xyz_deg)
    _add_stl_mesh(stage, str(prim.GetPath()), "Mesh", mesh_path)
    return prim


def _camera_orientation(camera_position, target):
    """Return a scalar-first quaternion for the USD camera basis."""
    from scipy.spatial.transform import Rotation

    forward = np.asarray(target, dtype=float) - np.asarray(camera_position, dtype=float)
    forward /= np.linalg.norm(forward)
    up = np.array([0.0, 0.0, 1.0])
    right = np.cross(forward, up)
    right /= np.linalg.norm(right)
    down = np.cross(forward, right)
    # ROS optical: +X right, +Y down, +Z forward. USD Camera: +X right,
    # +Y up, -Z forward; the conversion is a 180 degree local X rotation.
    optical_to_usd = np.diag([1.0, -1.0, -1.0])
    world_from_usd = np.column_stack((right, down, forward)) @ optical_to_usd
    xyzw = Rotation.from_matrix(world_from_usd).as_quat()
    return np.array([xyzw[3], xyzw[0], xyzw[1], xyzw[2]])


def _add_gripper(stage, mesh_dir):
    """Build the URDF flange/base/finger chain from the checked-in STL meshes."""
    from pxr import UsdGeom

    mesh_dir = Path(mesh_dir)
    required = ("flange.stl", "gripper_base.stl", "gripper_link1.stl", "gripper_link2.stl")
    missing = [name for name in required if not (mesh_dir / name).is_file()]
    if missing:
        raise FileNotFoundError(f"missing PiperX gripper meshes in {mesh_dir}: {missing}")

    link6 = UsdGeom.Xform.Define(stage, "/World/Link6").GetPrim()
    _add_urdf_mesh(stage, "/World/Link6", "Flange", mesh_dir / "flange.stl", (0, 0, 0), (0, 0, 0))

    base = UsdGeom.Xform.Define(stage, "/World/Link6/GripperBase").GetPrim()
    _set_xform(base, (0, 0, 0.0045), (0, 0, 90.0))
    _add_stl_mesh(stage, str(base.GetPath()), "Mesh", mesh_dir / "gripper_base.stl")

    # URDF: both finger joints originate at z=0.138 m, with opposing
    # prismatic axes and +/- half of the configured total opening.
    for side, filename, rpy in (
        ("Finger1", "gripper_link1.stl", (90.0, 0.0, 0.0)),
        ("Finger2", "gripper_link2.stl", (90.0, 0.0, -180.0)),
    ):
        _add_urdf_mesh(stage, str(base.GetPath()), side, mesh_dir / filename,
                       (0.0, 0.0, 0.138), rpy)
    return link6, base


def _set_opening(base, opening_m):
    """Apply the PiperX URDF mimic joints for a total jaw opening in metres."""
    from pxr import UsdGeom
    from scipy.spatial.transform import Rotation

    half = opening_m / 2.0
    joints = (
        ("Finger1", np.array([0.0, 0.0, 1.0]), half, np.array([90.0, 0.0, 0.0])),
        ("Finger2", np.array([0.0, 0.0, -1.0]), -half, np.array([90.0, 0.0, -180.0])),
    )
    for name, axis, q, rpy in joints:
        rotation = Rotation.from_euler("xyz", rpy, degrees=True)
        joint_translation = np.array([0.0, 0.0, 0.138]) + rotation.apply(axis * q)
        _set_xform(base.GetChild(name), joint_translation, rpy)


def _bbox_xyxy(payload, width, height):
    if isinstance(payload, dict):
        payload = payload.get("data", payload)
    data = np.asarray(payload)
    visible_fractions = []
    if data.dtype.names:
        boxes = []
        for row in data.reshape(-1):
            names = data.dtype.names
            if not all(k in names for k in ("x_min", "y_min", "x_max", "y_max")):
                continue
            box = [int(row[k]) for k in ("x_min", "y_min", "x_max", "y_max")]
            if box[2] > box[0] and box[3] > box[1]:
                boxes.append(box)
                if "occlusionRatio" in names:
                    visible_fractions.append(float(np.clip(1.0 - row["occlusionRatio"], 0.0, 1.0)))
    else:
        boxes = []
    if not boxes:
        raise ValueError("bottle is not visible to the tight-box annotator")
    merged = [min(b[0] for b in boxes), min(b[1] for b in boxes),
              max(b[2] for b in boxes), max(b[3] for b in boxes)]
    merged[0] = max(0, min(width, merged[0]))
    merged[1] = max(0, min(height, merged[1]))
    merged[2] = max(0, min(width, merged[2]))
    merged[3] = max(0, min(height, merged[3]))
    if merged[2] <= merged[0] or merged[3] <= merged[1]:
        raise ValueError("visible bottle bounding box is empty")
    return np.asarray(merged, dtype=np.int32), min(visible_fractions, default=1.0)


def _to_numpy(payload):
    if payload is None:
        raise ValueError("camera annotator returned no data")
    try:
        import warp as wp
        if isinstance(payload, wp.array):
            return payload.numpy()
    except ImportError:
        pass
    return np.asarray(payload)


def _gt_center_for_fit_band(depth, segmentation, segmentation_info, bbox, config,
                            camera_pose):
    """Use visible bottle pixels in the fitter's image band for the axial GT."""
    import cv2

    labels = segmentation_info.get("idToLabels", {})
    bottle_ids = [int(key) for key, value in labels.items()
                  if value.get("class") == "bottle"]
    if not bottle_ids:
        raise ValueError("bottle semantic id missing from segmentation annotator")
    mask = np.isin(_to_numpy(segmentation).squeeze(), bottle_ids)
    depth = _to_numpy(depth).squeeze().astype(np.float32)
    x0, y0, x1, y1 = map(float, bbox)
    fitting = config.get("fitting", {})
    horizontal_margin = float(fitting.get("horizontal_margin", 0.10))
    band_top = float(fitting.get("band_top", 0.48))
    band_bottom = float(fitting.get("band_bottom", 0.62))
    left = max(0, int(np.ceil(x0 + (x1 - x0) * horizontal_margin)))
    right = min(depth.shape[1], int(np.floor(x1 - (x1 - x0) * horizontal_margin)))
    top = max(0, int(np.ceil(y0 + (y1 - y0) * band_top)))
    bottom = min(depth.shape[0], int(np.floor(y0 + (y1 - y0) * band_bottom)))
    region = mask[top:bottom, left:right] & np.isfinite(depth[top:bottom, left:right])
    region &= (depth[top:bottom, left:right] >= 0.08) & (depth[top:bottom, left:right] <= 1.5)
    rows, columns = np.nonzero(region)
    if len(rows) < int(fitting.get("min_points", 30)):
        raise ValueError("insufficient visible bottle pixels in the fitting band")
    pixels = np.column_stack((columns + left, rows + top)).astype(np.float32)
    camera_cfg = config["camera"]
    K = np.asarray(camera_cfg["K"], dtype=np.float64).reshape(3, 3)
    D = np.asarray(camera_cfg.get("D", []), dtype=np.float64)
    rays = cv2.undistortPoints(pixels.reshape(-1, 1, 2), K, D).reshape(-1, 2)
    z = depth[rows + top, columns + left].astype(np.float64)
    points_camera = np.column_stack((rays * z[:, None], z))
    points_base = points_camera @ camera_pose[:3, :3].T + camera_pose[:3, 3]
    return np.array([0.0, 0.0, float(np.median(points_base[:, 2]))])


def _write_sample(path, frame, config, camera_pose, gt_center_base, bbox, visible_fraction,
                  pose_id, opening_mm):
    camera_cfg = config["camera"]
    rgb = _to_numpy(frame["rgb"])
    depth = _to_numpy(frame["distance_to_image_plane"]).astype(np.float32)
    if rgb.ndim != 3 or rgb.shape[0:2] != (camera_cfg["height"], camera_cfg["width"]):
        raise ValueError(f"unexpected RGB shape: {rgb.shape}")
    if depth.ndim == 3:
        depth = depth[..., 0]
    if depth.shape != (camera_cfg["height"], camera_cfg["width"]):
        raise ValueError(f"unexpected depth shape: {depth.shape}")
    np.savez_compressed(
        path,
        rgb=rgb[..., :3].astype(np.uint8),
        depth_m=depth,
        bbox_xyxy=bbox,
        K=np.asarray(camera_cfg["K"], dtype=np.float64).reshape(3, 3),
        D=np.asarray(camera_cfg["D"], dtype=np.float64),
        P=np.asarray(camera_cfg["P"], dtype=np.float64).reshape(3, 4),
        base_T_camera=camera_pose,
        base_roi=np.asarray(camera_cfg["base_roi"], dtype=np.float64),
        bottle_axis_base=np.array([0.0, 0.0, 1.0]),
        camera_position_base=camera_pose[:3, 3],
        gt_center_base=gt_center_base,
        gripper_opening_mm=np.float32(opening_mm),
        visible_fraction=np.float32(visible_fraction),
        pose_id=np.asarray(pose_id),
        fit_config=np.asarray(json.dumps(config.get("fitting", {}))),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE.parent / "config/sim.example.yaml")
    parser.add_argument("--bottle-usd", help="meter-scaled textured bottle USD reference")
    parser.add_argument("--gripper-mesh-dir", type=Path, default=DEFAULT_GRIPPER_MESH_DIR)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--limit", type=int, help="capture only the first N poses")
    parser.add_argument("--continuous", action="store_true",
                        help="capture a timed approach route, including frames with no bottle box")
    parser.add_argument("--save-stage", type=Path,
                        help="optionally export the configured first-pose USD stage")
    args = parser.parse_args()

    config = yaml.safe_load(args.config.read_text())
    bottle_value = args.bottle_usd or config["object"].get("usd") or config["object"].get("mesh")
    if not bottle_value:
        parser.error("set --bottle-usd or object.usd in the config")
    bottle_usd = _expanded_path(bottle_value)
    if bottle_usd.suffix.lower() != ".usd" and not bottle_usd.name.endswith(".usda"):
        parser.error("the scene builder expects a pre-converted meter-scaled USD")
    if not bottle_usd.is_file():
        parser.error(f"bottle USD does not exist: {bottle_usd}")
    output_dir = (args.output_dir or Path(config["render"]["output_dir"])).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    # Isaac Sim documents OMNI_KIT_ACCEPT_EULA for non-interactive startup.
    os.environ.setdefault("OMNI_KIT_ACCEPT_EULA", "YES")
    from isaacsim import SimulationApp

    app = SimulationApp({"headless": True, "renderer": "RayTracedLighting",
                         "limit_cpu_threads": 16})
    try:
        import omni.usd
        import isaacsim.core.experimental.utils.app as app_utils
        from isaacsim.core.utils.semantics import add_labels
        from isaacsim.core.utils.stage import add_reference_to_stage
        from isaacsim.sensors.experimental.rtx import CameraSensor, RtxCamera
        from pxr import Gf, Usd, UsdGeom, UsdLux
        from scipy.spatial.transform import Rotation

        omni.usd.get_context().new_stage()
        stage = omni.usd.get_context().get_stage()
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)

        table_z = float(config.get("table", {}).get("top_z_m", 0.0))
        table = UsdGeom.Cube.Define(stage, "/World/Table")
        table.CreateSizeAttr(1.0)
        _set_xform(table.GetPrim(), (0.0, 0.0, table_z - 0.01), (0, 0, 0))
        UsdGeom.XformCommonAPI(table.GetPrim()).SetScale(Gf.Vec3f(1.2, 1.2, 0.02))
        dome = UsdLux.DomeLight.Define(stage, "/World/DomeLight")
        dome.CreateIntensityAttr(900.0)

        bottle_prim = add_reference_to_stage(str(bottle_usd), "/World/Bottle")
        print("[scene] bottle reference added", flush=True)
        app.update()
        bounds = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
        local_range = bounds.ComputeWorldBound(bottle_prim).GetRange()
        lo, hi = np.array(local_range.GetMin()), np.array(local_range.GetMax())
        if not np.isfinite(np.r_[lo, hi]).all() or np.any(hi <= lo):
            raise ValueError("bottle USD has empty bounds")
        source_up = np.asarray(
            config.get("object", {}).get("upright_axis_mesh", [0, 0, 1]), dtype=float
        ).reshape(3)
        source_up_norm = np.linalg.norm(source_up)
        if source_up_norm < 1e-9:
            raise ValueError("object.upright_axis_mesh must be a non-zero 3D vector")
        upright_rotation = Rotation.align_vectors(
            [[0.0, 0.0, 1.0]], [source_up / source_up_norm]
        )[0].as_euler("xyz", degrees=True)
        _set_xform(bottle_prim, (0, 0, 0), upright_rotation)
        bounds = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
        local_range = bounds.ComputeWorldBound(bottle_prim).GetRange()
        lo, hi = np.array(local_range.GetMin()), np.array(local_range.GetMax())
        object_translation = np.array([-0.5 * (lo[0] + hi[0]),
                                       -0.5 * (lo[1] + hi[1]), table_z - lo[2]])
        _set_xform(bottle_prim, object_translation, upright_rotation)
        height = float(hi[2] - lo[2])
        print(f"[scene] bottle upright axis: {source_up.tolist()}, height={height:.3f} m",
              flush=True)
        object_cfg = config.get("object", {})
        target_fraction = float(object_cfg.get(
            "camera_aim_height_fraction", object_cfg.get("fit_center_height_fraction", 0.55)
        ))
        target_base = np.array([0.0, 0.0, table_z + height * target_fraction])

        add_labels(bottle_prim, labels=["bottle"], instance_name="class")
        for prim in Usd.PrimRange(bottle_prim):
            if prim.IsA(UsdGeom.Mesh):
                add_labels(prim, labels=["bottle"], instance_name="class")

        link6_prim, gripper_base = _add_gripper(stage, args.gripper_mesh_dir)
        print("[scene] factory gripper meshes added", flush=True)
        camera_cfg = config["camera"]
        width, height_px = int(camera_cfg["width"]), int(camera_cfg["height"])
        distortion = list(map(float, camera_cfg.get("D", [])))
        distortion += [0.0] * (5 - len(distortion))
        pinhole_prefix = "omni:lensdistortion:opencvPinhole:"
        camera_authoring = RtxCamera(
            "/World/D405",
            tick_rate=0,
            schemas=["OmniLensDistortionOpenCvPinholeAPI"],
            attributes={
                pinhole_prefix + "fx": float(camera_cfg["K"][0]),
                pinhole_prefix + "fy": float(camera_cfg["K"][4]),
                pinhole_prefix + "cx": float(camera_cfg["K"][2]),
                pinhole_prefix + "cy": float(camera_cfg["K"][5]),
                pinhole_prefix + "imageSize": Gf.Vec2i(width, height_px),
                pinhole_prefix + "k1": distortion[0],
                pinhole_prefix + "k2": distortion[1],
                pinhole_prefix + "p1": distortion[2],
                pinhole_prefix + "p2": distortion[3],
                pinhole_prefix + "k3": distortion[4],
            },
        )
        pixel_size_um = float(camera_cfg.get("pixel_size_um", 3.0))
        # Experimental camera setters use centimetres and convert to USD mm.
        focal_cm = pixel_size_um * 1e-4 * (
            float(camera_cfg["K"][0]) + float(camera_cfg["K"][4])) / 2
        camera_authoring.camera.set_focal_lengths(focal_cm)
        camera_authoring.camera.set_apertures(
            horizontal_apertures=pixel_size_um * 1e-4 * width,
            vertical_apertures=pixel_size_um * 1e-4 * height_px,
        )
        camera_authoring.camera.set_clipping_ranges(0.05, 2.0)
        camera = CameraSensor(
            camera_authoring,
            resolution=(height_px, width),
            annotators=["rgb", "distance_to_image_plane", "bounding_box_2d_tight",
                        "semantic_segmentation"],
        )
        print("[scene] D405 camera and annotators initialized", flush=True)

        link6_T_camera = np.asarray(camera_cfg["link6_T_camera"], dtype=float).reshape(4, 4)
        openings = config["gripper"]["opening_mm"]
        distances = config["object"]["pose_grid"]["distance_m"]
        azimuths = config["object"]["pose_grid"]["azimuth_deg"]
        elevations = config["object"]["pose_grid"]["elevation_deg"]
        continuous = config.get("continuous", {}) if args.continuous else None
        if continuous:
            frame_count = int(continuous.get("frames", 90))
            if frame_count < 2:
                parser.error("continuous.frames must be at least 2")
            poses = [(i, frame_count, float(continuous.get("start_distance_m", .45)),
                      float(continuous.get("end_distance_m", .18)),
                      float(continuous.get("azimuth_deg", 0.)),
                      float(continuous.get("elevation_deg", 20.)),
                      float(continuous.get("opening_mm", 50.))) for i in range(frame_count)]
        else:
            poses = list(itertools.product(distances, azimuths, elevations, openings))
        if args.limit is not None:
            poses = poses[:max(0, args.limit)]

        records = []
        sequence_started = time.monotonic()
        for index, pose_data in enumerate(poses):
            if continuous:
                sequence_index, frame_count, distance0, distance1, azimuth, elevation, opening_mm = pose_data
                u = sequence_index / (frame_count-1)
                progress = 10*u**3-15*u**4+6*u**5
                distance = distance0 + progress*(distance1-distance0)
                pose_id = f"approach_{sequence_index:05d}"
            else:
                distance, azimuth, elevation, opening_mm = pose_data
                pose_id = f"d{distance:.3f}_a{azimuth:+.0f}_e{elevation:+.0f}_g{int(opening_mm):03d}"
            az, el = np.deg2rad([azimuth, elevation])
            direction = np.array([np.cos(el) * np.cos(az),
                                  np.cos(el) * np.sin(az), np.sin(el)])
            camera_position = target_base + float(distance) * direction
            if camera_position[2] <= table_z:
                reason = "camera is at or below tabletop height"
                records.append({"pose_id": pose_id, "status": "rejected", "reason": reason})
                print(f"[{index + 1}/{len(poses)}] {pose_id}: rejected: {reason}", flush=True)
                continue
            forward = target_base - camera_position
            forward /= np.linalg.norm(forward)
            right = np.cross(forward, [0.0, 0.0, 1.0])
            right /= np.linalg.norm(right)
            down = np.cross(forward, right)
            world_R_camera = np.column_stack((right, down, forward))
            camera_pose = np.eye(4)
            camera_pose[:3, :3] = world_R_camera
            camera_pose[:3, 3] = camera_position
            world_T_link6 = camera_pose @ np.linalg.inv(link6_T_camera)
            link6_rotation = Rotation.from_matrix(world_T_link6[:3, :3])
            _set_xform(link6_prim, world_T_link6[:3, 3],
                       link6_rotation.as_euler("xyz", degrees=True))
            _set_opening(gripper_base, float(opening_mm) / 1000.0)
            camera_position_usd = camera_position
            camera_orientation_usd = _camera_orientation(camera_position, target_base)
            camera_authoring.set_world_poses(
                positions=[camera_position_usd],
                orientations=[camera_orientation_usd],
            )
            if index == 0 and args.save_stage:
                stage_path = args.save_stage.expanduser().resolve()
                stage_path.parent.mkdir(parents=True, exist_ok=True)
                stage.GetRootLayer().Export(str(stage_path))
                print(f"[scene] USD stage saved: {stage_path}", flush=True)

            if index == 0:
                app_utils.play(commit=True)
            # RTX annotators are asynchronous; allow their render products to
            # arrive before reading the frame. Wall capture cadence is recorded.
            for _ in range(8):
                app.update()
            frame = {}
            annotator_info = {}
            for name in ("rgb", "distance_to_image_plane", "bounding_box_2d_tight",
                         "semantic_segmentation"):
                frame[name], annotator_info[name] = camera.get_data(name)
            sample_path = output_dir / f"sample_{index:06d}.npz"
            try:
                bbox, visible_fraction = _bbox_xyxy(
                    frame.get("bounding_box_2d_tight"), width, height_px)
                if continuous:
                    gt_center_base = np.array([0., 0., table_z +
                        float(continuous.get("grasp_height_m", .115))])
                else:
                    gt_center_base = _gt_center_for_fit_band(
                        frame["distance_to_image_plane"], frame["semantic_segmentation"],
                        annotator_info["semantic_segmentation"], bbox, config, camera_pose)
                _write_sample(sample_path, frame, config, camera_pose, gt_center_base,
                              bbox, visible_fraction, pose_id, float(opening_mm))
                records.append({"pose_id": pose_id, "status": "captured",
                                "sample": str(sample_path),
                                "bbox_xyxy": bbox.tolist(),
                                "visible_fraction": visible_fraction})
                print(f"[{index + 1}/{len(poses)}] {pose_id}: {sample_path}", flush=True)
            except (KeyError, ValueError) as exc:
                if continuous and "not visible" in str(exc):
                    depth = _to_numpy(frame["distance_to_image_plane"]).squeeze().astype(np.float32)
                    np.savez_compressed(sample_path, rgb=_to_numpy(frame["rgb"])[..., :3].astype(np.uint8),
                        depth_m=depth, bbox_xyxy=np.empty(0, dtype=np.int32),
                        K=np.asarray(camera_cfg["K"]).reshape(3,3), D=np.asarray(camera_cfg["D"]),
                        P=np.asarray(camera_cfg["P"]).reshape(3,4), base_T_camera=camera_pose,
                        base_roi=np.asarray(camera_cfg["base_roi"]), bottle_axis_base=[0.,0.,1.],
                        camera_position_base=camera_position,
                        gt_center_base=[0.,0.,table_z+float(continuous.get("grasp_height_m", .115))],
                        gripper_opening_mm=np.float32(opening_mm), visible_fraction=np.float32(0.),
                        pose_id=np.asarray(pose_id), source_time_s=np.float64(sequence_index/
                            float(continuous.get("fps", 30.))), fit_config=np.asarray(json.dumps(config.get("fitting", {}))))
                    records.append({"pose_id":pose_id,"status":"captured_unseen",
                                    "sample":str(sample_path),"visible_fraction":0.})
                else:
                    records.append({"pose_id": pose_id, "status": "rejected", "reason": str(exc)})
                    print(f"[{index + 1}/{len(poses)}] {pose_id}: rejected: {exc}", flush=True)
            if continuous:
                records[-1]["capture_wall_s"] = time.monotonic()-sequence_started
                next_frame = (sequence_index+1)/float(continuous.get("fps", 30.))
                time.sleep(max(0., next_frame-(time.monotonic()-sequence_started)))
        manifest = {"bottle_usd": str(bottle_usd), "requested": len(poses),
                    "continuous": bool(continuous),
                    "nominal_fps": float(continuous.get("fps", 30.)) if continuous else None,
                    "capture_wall_seconds": (time.monotonic()-sequence_started) if continuous else None,
                    "captured": sum(r["status"].startswith("captured") for r in records),
                    "rejected": sum(not r["status"].startswith("captured") for r in records),
                    "records": records}
        (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    except Exception:
        traceback.print_exc()
        raise
    finally:
        app.close()


if __name__ == "__main__":
    main()
