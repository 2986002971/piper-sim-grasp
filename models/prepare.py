#!/usr/bin/env python3
"""Download checked model inputs and build Piper X + nominal D405 geometry outside Git."""
import argparse
import hashlib
import http.cookiejar
import io
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
LOCK = json.loads((HERE / 'sources.lock.json').read_text())


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def external(path):
    path = Path(path).expanduser().resolve()
    if path.is_relative_to(ROOT) or path == Path('/'):
        raise ValueError('Use a data directory outside the source checkout')
    return path


def fetch(cache, offline=False):
    source = cache / 'sources' / LOCK['commit']
    for row in LOCK['files']:
        target = source / row['path']
        if target.is_file() and digest(target) == row['sha256']:
            continue
        if offline:
            raise ValueError('Missing or corrupt offline cache: ' + row['path'])
        target.parent.mkdir(parents=True, exist_ok=True)
        repository = row.get("repository", "agilexrobotics/piper_isaac_sim")
        commit = row.get("commit", LOCK["commit"])
        upstream_path = row.get('upstream_path', row['path'])
        url = f"https://raw.githubusercontent.com/{repository}/{commit}/{upstream_path}"
        temporary = target.with_suffix(target.suffix + '.partial')
        try:
            with urllib.request.urlopen(url, timeout=60) as response, temporary.open('wb') as output:
                shutil.copyfileobj(response, output)
            if digest(temporary) != row['sha256']:
                raise ValueError('Source checksum mismatch: ' + row['path'])
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    (source / 'provenance.json').write_text(json.dumps(LOCK, indent=2))
    return source


def fetch_bracket(data, offline=False, cookie_file=None):
    """Verified optional official attachment. Auth/session data never enters provenance."""
    info = LOCK['bracket']
    target = data / 'bracket/d405_mount_source.step'
    if target.is_file() and digest(target) == info['step_sha256']:
        return target
    if offline or not info.get('verified_attachment'):
        return None
    try:
        opener = urllib.request.build_opener()
        if cookie_file:
            jar = http.cookiejar.MozillaCookieJar(str(cookie_file))
            jar.load(ignore_discard=True, ignore_expires=True)
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        request = urllib.request.Request(info['attachment_url'], headers={
            'User-Agent': 'Mozilla/5.0', 'Referer': info['page']})
        with opener.open(request, timeout=30) as response:
            archive = response.read(4 * 1024 * 1024)
        if hashlib.sha256(archive).hexdigest() != info['attachment_sha256']:
            raise ValueError('Official bracket download requires login or checksum differs')
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            step = bundle.read(info['member'])
        if hashlib.sha256(step).hexdigest() != info['step_sha256']:
            raise ValueError('Official bracket STEP checksum mismatch')
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix('.partial')
        temporary.write_bytes(step); temporary.replace(target)
        (target.parent / 'source.json').write_text(json.dumps(info, indent=2, ensure_ascii=False))
        return target
    except (OSError, ValueError, zipfile.BadZipFile, KeyError):
        print('NOTICE: official bracket unavailable without valid authentication/cache; use --bracket or pre-download cache.', file=sys.stderr)
        return None


def multiply(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def inverse(a):
    return [[a[j][i] if j < 3 else -sum(a[k][i]*a[k][3] for k in range(3))
             for j in range(4)] for i in range(3)] + [[0, 0, 0, 1]]


def transform(xyz, rpy):
    x,y,z = xyz; r,p,w = rpy
    cr,sr,cp,sp,cw,sw = math.cos(r),math.sin(r),math.cos(p),math.sin(p),math.cos(w),math.sin(w)
    return [[cw*cp, cw*sp*sr-sw*cr, cw*sp*cr+sw*sr,x],
            [sw*cp, sw*sp*sr+cw*cr, sw*sp*cr-cw*sr,y],[-sp,cp*sr,cp*cr,z],[0,0,0,1]]


def origin_matrix(joint):
    o=joint.find('origin')
    if o is None: return transform([0,0,0],[0,0,0])
    return transform([float(x) for x in o.get('xyz','0 0 0').split()],
                     [float(x) for x in o.get('rpy','0 0 0').split()])


def chain(root, parent, child):
    joints={j.find('child').get('link'):j for j in root.findall('joint')}
    if child == parent: return transform([0,0,0],[0,0,0])
    j=joints[child]
    return multiply(chain(root,parent,j.find('parent').get('link')),origin_matrix(j))


def pose(matrix):
    return ([matrix[i][3] for i in range(3)],
            [math.atan2(matrix[2][1],matrix[2][2]),
             math.atan2(-matrix[2][0],math.hypot(matrix[0][0],matrix[1][0])),
             math.atan2(matrix[1][0],matrix[0][0])])


def robot_description(source):
    """Use the same official arm and 100 mm gripper geometry as ROS/MoveIt."""
    robot=ET.parse(source/'piper_x/urdf/piper_x_description.urdf').getroot()
    # The USD importer fixes base_link itself; do not introduce a second world.
    robot.remove(robot.find("link[@name='world']"))
    robot.remove(robot.find("joint[@name='world_to_base_link']"))
    gripper=ET.parse(source/'piper_x/urdf/piper_x_with_gripper_description.xacro').getroot()
    for element in gripper:
        if element.tag not in ('link','joint') or element.get('name') in ('gripper','gripper_link'):
            continue
        if element.tag=='joint' and element.get('name') in ('gripper_joint1','gripper_joint2'):
            element.set('name','joint7' if element.get('name')=='gripper_joint1' else 'joint8')
            element.remove(element.find('mimic'))
        robot.append(element)
    return robot


def bracket_mount():
    """Mate the unscaled STEP bracket to the official ROS gripper's 12 mm holes.

    Coordinates below are metres in link6, measured on the pinned meshes.
    The old Isaac gripper had a 1.0447952 scale and a different visual origin;
    its mounting-hole midpoint (z=44.907 mm) must not be reused here.
    Registering the four shared CAD solids gives the corresponding underside
    midpoint z=50.498 mm. The bracket is rigidly reseated, never scaled.
    See models/README.md for the registration and collision evidence.
    """
    xyz=[0.0006157431537362377,0.13258499564864346,-0.137835126783945]
    rpy=[-3.129917939077873,-0.004944862834077357,-0.0017364652118565527]
    return xyz,rpy,{
        'pose':'official ROS gripper underside; rigid STEP bracket; 0.2 mm clearance',
        'xyz_m':xyz,'rpy_rad':rpy,
        'gripper_contact_hole_midpoint_m':[-0.0002055523379475621,-0.037982677289828716,0.05049776957080897],
        'gripper_contact_face':'underside','seating_clearance_m':0.0002,
        'hole_pitch_mm':{'bracket_gripper':12.0,'gripper_mesh':12.0,'bracket_camera':20.0,'d405_rear':20.0},
        'camera_mount_holes_in_link6_m':[
            [0.009707316728345352,-0.08829408799194591,0.023124521198220744],
            [-0.010292653254265241,-0.08825943695432208,0.02312450525291853]],
        'camera_mount_normal_in_link6':[0.0005905321704794716,0.34127752852424786,0.9399623562566594],
        'fit_status':'12 mm gripper holes and 20 mm camera holes mated on pinned CAD',
    }


def seat_camera(robot, bracket_info):
    """Mate the D405 mesh rear plane and 20 mm hole pair to the bracket.

    The pinned camera mesh is in millimeters: rear holes are (+/-10,0,-23),
    and the shell occupies -23 <= Z <= 0. The bracket face normal points out
    of its solid toward the gripper, so the camera optical forward stays +Z.
    """
    holes=bracket_info['camera_mount_holes_in_link6_m']
    def unit(v):
        length=math.sqrt(sum(x*x for x in v))
        return [x/length for x in v]
    def cross(a,b):
        return [a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]]
    x=unit([holes[1][i]-holes[0][i] for i in range(3)])
    y=unit(cross(bracket_info['camera_mount_normal_in_link6'],x))
    z=cross(x,y)
    midpoint=[(holes[0][i]+holes[1][i])/2 for i in range(3)]
    mesh=[[x[i],y[i],z[i],midpoint[i]+.023*z[i]] for i in range(3)]+[[0,0,0,1]]
    joint=robot.find("joint[@name='d405_joint']")
    parent=joint.find('child').get('link')
    nominal=multiply(chain(robot,parent,'d405_link'),
                     origin_matrix(robot.find("link[@name='d405_link']/visual")))
    mount=multiply(mesh,inverse(nominal));xyz,rpy=pose(mount)
    origin=joint.find('origin')
    origin.set('xyz',' '.join(f'{v:.14g}' for v in xyz))
    origin.set('rpy',' '.join(f'{v:.14g}' for v in rpy))
    actual=multiply(chain(robot,'link6','d405_link'),
                    origin_matrix(robot.find("link[@name='d405_link']/visual")))
    rear=[[actual[i][3]+sign*.010*actual[i][0]-.023*actual[i][2]
           for i in range(3)] for sign in (-1,1)]
    residual=max(math.sqrt(sum((rear[j][i]-holes[j][i])**2 for i in range(3))) for j in range(2))
    if residual>1e-7 or z[2]<=0:
        raise ValueError('D405 mechanical mounting failed: rear holes or forward direction')
    return chain(robot,'link6','d405_color_optical_frame'),{
        'placement':'bracket','reference':'pinned CAD rear holes and mating planes',
        'rear_holes_in_link6_m':rear,'rear_hole_residual_mm':1000*residual,
        'rear_plane_clearance_m':0.0,'optical_forward_in_link6':z,
    }


def validate_urdf(path, expected):
    root=ET.parse(path).getroot()
    for mesh in root.iter('mesh'):
        if not (path.parent / mesh.get('filename')).is_file():
            raise ValueError('Unresolved mesh: '+mesh.get('filename'))
    for name in ('joint7','joint8'):
        joint=root.find(f"joint[@name='{name}']")
        if joint is None or joint.get('type') != 'prismatic': raise ValueError('Missing 100 mm gripper')
        limit=joint.find('limit')
        if abs(float(limit.get('upper'))-float(limit.get('lower'))-.05)>1e-8:
            raise ValueError('Unexpected gripper stroke')
    actual=chain(root,'link6','d405_color_optical_frame')
    if max(abs(actual[i][j]-expected[i][j]) for i in range(4) for j in range(4)) > 1e-7:
        raise ValueError('D405 optical frame does not match hand-eye transform')
    return actual


def assembly(source, output, handeye, bracket=None, camera_placement='bracket'):
    import xacro
    import yaml
    matrix=yaml.safe_load(handeye.read_text())['tcp_T_camera']
    if len(matrix)!=4 or any(len(row)!=4 for row in matrix) or not all(math.isfinite(float(x)) for row in matrix for x in row):
        raise ValueError('Hand-eye must be a finite 4x4 matrix')
    if matrix[3] != [0,0,0,1]: raise ValueError('Invalid homogeneous transform')
    # Reject non-rigid calibration; precision in the supplied example is ~1e-10.
    for i in range(3):
        for j in range(3):
            if abs(sum(matrix[k][i]*matrix[k][j] for k in range(3))-(1 if i==j else 0)) > 1e-6:
                raise ValueError('Hand-eye rotation is not orthonormal')
    output.mkdir(parents=True,exist_ok=True)
    robot=robot_description(source)
    robot.set('name','piper_x_d405_assembly')
    links={node.get('name') for node in robot.findall('link')}
    if 'link6' not in links: raise ValueError('Expected case-sensitive wrist link6')
    for mesh in robot.iter('mesh'):
        relative=mesh.get('filename').removeprefix('package://agx_arm_description/agx_arm_urdf/')
        src=source/relative
        dst=output/'meshes/piper_x'/src.name
        dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
        mesh.set('filename',str(dst.relative_to(output)))
    # Resolve $(find) for xacro without ROS or ament. Source remains unmodified.
    camera=output/'camera-source';shutil.copytree(source/'realsense2_description',camera,dirs_exist_ok=True)
    for p in (camera/'urdf').glob('*.xacro'):
        p.write_text(p.read_text().replace('$(find realsense2_description)',str(camera)))
    wrapper=output/'camera.xacro'
    wrapper.write_text(f'''<robot name="d405" xmlns:xacro="http://ros.org/wiki/xacro">
<xacro:include filename="{camera}/urdf/_d405.urdf.xacro"/>
<xacro:sensor_d405 parent="link6" name="d405" use_nominal_extrinsics="true"><origin xyz="0 0 0" rpy="0 0 0"/></xacro:sensor_d405>
</robot>''')
    cam=ET.fromstring(xacro.process_file(str(wrapper)).toxml())
    nominal=chain(cam,'link6','d405_color_optical_frame')
    mount=multiply(matrix,inverse(nominal));xyz,rpy=pose(mount)
    origin=cam.find("joint[@name='d405_joint']/origin")
    origin.set('xyz',' '.join(f'{v:.14g}' for v in xyz)); origin.set('rpy',' '.join(f'{v:.14g}' for v in rpy))
    for mesh in cam.iter('mesh'):
        mesh.set('filename','meshes/realsense2/d405.stl')
    dest=output/'meshes/realsense2/d405.stl';dest.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(source/'realsense2_description/meshes/d405.stl',dest)
    for node in cam: robot.append(node)
    bracket_info=None
    camera_info={'placement':'handeye','reference':'supplied link6 optical transform'}
    if bracket:
        bracket=Path(bracket).resolve()
        dest=output/'meshes/d405_mount.stl'
        if bracket.suffix.lower() in ('.step','.stp'):
            subprocess.run([sys.executable,str(HERE/'convert_step.py'),str(bracket),str(dest)],check=True)
        elif bracket.suffix.lower()=='.stl': shutil.copy2(bracket,dest)
        else: raise ValueError('Bracket override must be STEP or STL (millimeters)')
        bracket_xyz,bracket_rpy,bracket_info=bracket_mount()
        bracket_info['source_sha256']=digest(bracket)
        joint=ET.SubElement(robot,'joint',name='d405_bracket_joint',type='fixed')
        ET.SubElement(joint,'origin',xyz=' '.join(f'{v:.14g}' for v in bracket_xyz),
                      rpy=' '.join(f'{v:.14g}' for v in bracket_rpy))
        ET.SubElement(joint,'parent',link='link6'); ET.SubElement(joint,'child',link='d405_bracket')
        link=ET.SubElement(robot,'link',name='d405_bracket');vis=ET.SubElement(link,'visual');geometry=ET.SubElement(vis,'geometry')
        ET.SubElement(geometry,'mesh',filename='meshes/d405_mount.stl',scale='0.001 0.001 0.001')
        if camera_placement=='bracket':
            matrix,camera_info=seat_camera(robot,bracket_info)
    else:
        print('NOTICE: no verified bracket cache/override; generating geometry without bracket.',file=sys.stderr)
    urdf=output/'piper_x_d405.urdf';ET.indent(robot);ET.ElementTree(robot).write(urdf,encoding='utf-8',xml_declaration=True)
    actual=validate_urdf(urdf,matrix)
    (output/'assembly.json').write_text(json.dumps({'sources':LOCK,'handeye_sha256':digest(handeye),
        'optical_transform':actual,'bracket':bracket_info,'camera_mount':camera_info,
        'robot_model':'agx_arm_urdf/piper_x','meters_per_unit':1,'camera_sensor_enabled':False},indent=2))
    shutil.copy2(source/'LICENSE',output/'PIPER-LICENSE')
    shutil.copy2(source/'realsense2_description/LICENSE',output/'REALSENSE-LICENSE')
    original=(source/'realsense2_description/urdf/_d405.urdf.xacro').read_text()
    notices=re.findall(r'<!--(.*?)-->',original,re.S)
    (output/'REALSENSE-NOTICE.txt').write_text(
        'D405 description: RealSense ROS; source and commit in assembly.json.\n' +
        '\n'.join(notice for notice in notices if 'Copyright' in notice or 'Licensed' in notice))
    return urdf


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-dir',type=Path,required=True)
    p.add_argument('--offline',action='store_true')
    p.add_argument('--download-only',action='store_true')
    p.add_argument('--urdf-only',action='store_true')
    p.add_argument('--scene',type=Path,help='Use an existing complete USD directly')
    p.add_argument('--handeye',type=Path,default=HERE/'handeye.example.yaml')
    p.add_argument('--camera-placement',choices=('bracket','handeye'),default='bracket',
                   help='Mate camera to bracket by default; handeye preserves the supplied optical pose')
    p.add_argument('--bracket',type=Path)
    p.add_argument('--bracket-cookie-file',type=Path,help='Optional private Netscape cookies for official attachment download')
    p.add_argument('--isaac-python',type=Path,default=Path(sys.executable))
    args=p.parse_args();data=external(args.data_dir);data.mkdir(parents=True,exist_ok=True)
    import fcntl
    with (data/'prepare.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        prepare(args,data)


def prepare(args,data):
    if args.scene:
        if not args.scene.is_file(): raise ValueError('External scene missing')
        subprocess.run([str(args.isaac_python),str(HERE/'validate_usd.py'),str(args.scene.resolve())],check=True)
        print(args.scene.resolve());return
    source=fetch(data,args.offline)
    bracket=args.bracket or fetch_bracket(data,args.offline,args.bracket_cookie_file)
    if args.download_only: print(source);return
    # Content-address generation inputs, including generator code, to avoid stale output reuse.
    signature=hashlib.sha256((json.dumps(LOCK,sort_keys=True)+digest(args.handeye)+
              (digest(bracket) if bracket else 'no-bracket')+args.camera_placement+str(args.urdf_only)+digest(__file__)+
              ''.join(digest(HERE/name) for name in ('import_usd.py','validate_usd.py','d405_camera.py','convert_step.py',
                                                     'requirements.txt','requirements-step-conversion.txt'))).encode()).hexdigest()[:24]
    output=data/'generated'/signature
    if not args.urdf_only and (output/'complete.json').is_file():
        checks=json.loads((output/'complete.json').read_text())
        if {'robot.usd','piper_x_d405.usda'}.issubset(checks) and all((output/name).is_file() and digest(output/name)==value for name,value in checks.items()):
            print(output/'piper_x_d405.usda');return
        raise ValueError('Generated scene checksum mismatch; remove the corrupt generated directory and retry')
    with tempfile.TemporaryDirectory(dir=data,prefix='assembly-') as temp:
        temporary=Path(temp);urdf=assembly(source,temporary,args.handeye,bracket,args.camera_placement)
        if not args.urdf_only:
            subprocess.run([str(args.isaac_python),str(HERE/'import_usd.py'),str(urdf)],check=True)
            for name in ('robot.usd','piper_x_d405.usda'):
                if not (temporary/name).is_file():
                    raise ValueError('Isaac importer did not produce required output: '+name)
            # Validation succeeds before the scene is eligible for launching.
            subprocess.run([str(args.isaac_python),str(HERE/'validate_usd.py'),str(temporary/'piper_x_d405.usda')],check=True)
        # The xacro intermediates contain temporary absolute paths; remove them.
        shutil.rmtree(temporary/'camera-source');(temporary/'camera.xacro').unlink()
        if not args.urdf_only:
            hashes={str(f.relative_to(temporary)):digest(f) for f in temporary.rglob('*') if f.is_file() and f.name!='complete.json'}
            (temporary/'complete.json').write_text(json.dumps(hashes,indent=2))
        if output.exists(): shutil.rmtree(output)
        output.parent.mkdir(parents=True,exist_ok=True);shutil.copytree(temporary,output)
        if not args.urdf_only:
            try:
                subprocess.run([str(args.isaac_python),str(HERE/'validate_usd.py'),str(output/'piper_x_d405.usda')],check=True)
            except BaseException:
                shutil.rmtree(output)
                raise
    print(output/('piper_x_d405.urdf' if args.urdf_only else 'piper_x_d405.usda'))


if __name__=='__main__':
    main()
