"""Author the D405 viewport camera at its existing ROS optical frame."""

from pxr import Gf, Sdf, UsdGeom

# Same example intrinsics used for the 640x480 simulated RGB capture.
# These describe the lens, independently of the camera mounting transform.
RGB_INTRINSICS = {
    'width': 640, 'height': 480,
    'fx': 393.4726257, 'fy': 392.9162903,
    'cx': 314.7101440, 'cy': 235.8206177,
    'k1': -0.05203464, 'k2': 0.05406765,
    'p1': -0.00045569, 'p2': 0.00029751, 'k3': -0.01748787,
}


def ensure_d405_camera(stage):
    """Create a Camera prim once; caller chooses the persistent/session layer."""
    frames = [p for p in stage.Traverse() if p.GetName() == 'd405_color_optical_frame']
    if len(frames) != 1:
        raise ValueError('Expected exactly one D405 color optical frame')
    path = frames[0].GetPath().AppendChild('D405_RGB')
    existing = stage.GetPrimAtPath(path)
    if existing:
        if not existing.IsA(UsdGeom.Camera):
            raise ValueError(f'D405 camera path is occupied by {existing.GetTypeName()}: {path}')
        return UsdGeom.Camera(existing)

    camera = UsdGeom.Camera.Define(stage, path)
    prim = camera.GetPrim()
    prim.SetDisplayName('D405 RGB')
    # ROS optical: X right, Y down, Z forward. USD Camera: X right,
    # Y up, -Z forward. Rotate locally by pi about X, with zero translation.
    camera.AddOrientOp(UsdGeom.XformOp.PrecisionDouble).Set(Gf.Quatd(0, 1, 0, 0))
    camera.CreateProjectionAttr(UsdGeom.Tokens.perspective)
    camera.CreateClippingRangeAttr(Gf.Vec2f(0.01, 2.0))
    camera.CreateFStopAttr(0.0)
    camera.CreateFocusDistanceAttr(0.2)

    k = RGB_INTRINSICS
    # Aperture and focal length are in tenths of the stage's length unit.
    # A 3 micrometer pixel corresponds to 3e-5 camera units in a meter stage.
    pixel = 3e-5 / UsdGeom.GetStageMetersPerUnit(stage)
    focal = pixel * k['fx']
    camera.CreateFocalLengthAttr(focal)
    camera.CreateHorizontalApertureAttr(pixel * k['width'])
    camera.CreateVerticalApertureAttr(focal / k['fy'] * k['height'])
    camera.CreateHorizontalApertureOffsetAttr(pixel * (k['width']/2-k['cx']))
    camera.CreateVerticalApertureOffsetAttr(focal / k['fy'] * (k['cy']-k['height']/2))

    # RTX reads these OpenCV lens attributes. Author the model explicitly so
    # the saved USD also works before its lens schema plugin has been loaded.
    prim.AddAppliedSchema('OmniLensDistortionOpenCvPinholeAPI')
    prim.CreateAttribute('omni:lensdistortion:model', Sdf.ValueTypeNames.Token, custom=False).Set('opencvPinhole')
    prefix = 'omni:lensdistortion:opencvPinhole:'
    prim.CreateAttribute(prefix+'imageSize', Sdf.ValueTypeNames.Int2, custom=False).Set(
        Gf.Vec2i(k['width'], k['height']))
    for name in ('fx', 'fy', 'cx', 'cy', 'k1', 'k2', 'p1', 'p2', 'k3'):
        prim.CreateAttribute(prefix+name, Sdf.ValueTypeNames.Float, custom=False).Set(k[name])
    # Protect the mounted camera from viewport navigation; Kit exposes its
    # normal lock control if the user wants to edit it.
    prim.CreateAttribute('omni:kit:cameraLock', Sdf.ValueTypeNames.Bool).Set(True)
    return camera


def ensure_d405_depth_camera(stage, rgb_camera):
    """Expose a second menu selection with the same optics and pose as RGB."""
    path = rgb_camera.GetPath().GetParentPath().AppendChild('D405_Depth')
    existing = stage.GetPrimAtPath(path)
    if existing:
        if not existing.IsA(UsdGeom.Camera):
            raise ValueError(f'D405 depth camera path is occupied: {path}')
        return UsdGeom.Camera(existing)
    camera = UsdGeom.Camera.Define(stage, path)
    # Both siblings share the optical frame; inheritance keeps their local
    # transforms and lens attributes identical even if RGB optics are edited.
    camera.GetPrim().GetInherits().AddInherit(rgb_camera.GetPath())
    camera.GetPrim().SetDisplayName('D405 Depth')
    return camera
