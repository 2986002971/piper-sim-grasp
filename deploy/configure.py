"""Generate deployment-specific paths from a verified bundle and scene profile."""
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import yaml
from models.bundle import verify
from runtime.config import ROOT


def configure(config, main=False):
    data = Path(config['data_root'])
    assets = data/'assets'/config['profile']
    current = json.loads((assets/'current.json').read_text())
    bundle = assets/current['directory']
    manifest = verify(bundle)
    profile = yaml.safe_load((ROOT/'config/profiles'/f"{config['profile']}.yaml").read_text())
    directory = data/'config'
    directory.mkdir(parents=True, exist_ok=True)
    # RSP needs portable bundle-relative meshes resolved for this deployment.
    tree = ET.parse(bundle/manifest['robot_urdf'])
    for mesh in tree.iter('mesh'):
        mesh.set('filename', (bundle/mesh.get('filename')).resolve().as_uri())
    urdf = directory/'robot.urdf'
    tree.write(urdf, encoding='utf-8', xml_declaration=True)
    profile.update(isaac_env=config['isaac_env'], scene_usd=str(bundle/manifest['scene']),
                   urdf_file=str(data/'models/piper/robot.urdf') if main else str(urdf),
                   output_dir=str(data/'state/devices'), runtime_lock=str(data/'state/session.lock'),
                   cache_root=str(data/'cache'),
                   gpu_lock='/tmp/piper-sim-gpu0.lock',
                   ros_domain_id=config['ros_domain_id'])
    # Align against the bundled reviewed URDF or the current main-generated model.
    profile['align_kinematics_to_ros_model'] = True
    path = directory/'devices.yaml'
    path.write_text(yaml.safe_dump(profile, sort_keys=False))
    return path, bundle, manifest
