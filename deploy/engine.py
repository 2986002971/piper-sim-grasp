"""Build a GPU-specific engine from explicit external reviewed model inputs."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from models.bundle import digest
from runtime.config import ROOT, paths, write_json
from deploy.tensorrt_resources import builder_library


def build_engine(config, log):
    from deploy.pipeline import run
    model = config.get('detector')
    if not model:
        raise ValueError('Main integration requires detector: onnx, source_pt, classes, license_reference, exporter_version')
    source = Path(config['main_project'])
    generator = source/'src/perception/nankai_robocup_perception_bringup/tools/generate_detection_manifest.py'
    versions = json.loads((ROOT/'config/versions.lock.json').read_text())
    gpu = subprocess.check_output(['nvidia-smi','--query-gpu=name,compute_cap','--format=csv,noheader'],text=True).splitlines()[0]
    capability = gpu.rsplit(',',1)[1].strip()
    receipt = {key: digest(model[key]) for key in ('onnx','source_pt','classes')}
    receipt.update(gpu=gpu, tensorrt=versions['tensorrt'], cuda=versions['cuda'], generator=digest(generator),
                   license_reference=model['license_reference'], exporter_version=model['exporter_version'])
    identity = hashlib.sha256(json.dumps(receipt,sort_keys=True).encode()).hexdigest()[:20]
    p = paths(config); destination = p['assets']/'engines'/identity
    marker = destination/'build.json'
    if marker.is_file():
        completed = json.loads(marker.read_text())
        if completed['inputs'] == receipt and all(digest(destination/name)==sha for name,sha in completed['files'].items()):
            return destination
        raise ValueError('Engine cache is corrupt: '+str(destination))
    destination.mkdir(parents=True,exist_ok=True)
    sdk = p['sdk']/'tensorrt'
    env = dict(os.environ, LD_LIBRARY_PATH=str(sdk/'usr/lib/x86_64-linux-gnu'))
    engine = destination/'model.engine'
    with builder_library(sdk, p['cache']) as resources:
        builder_env = dict(env, LD_LIBRARY_PATH=str(resources)+':'+env['LD_LIBRARY_PATH'])
        run([sdk/'usr/src/tensorrt/bin/trtexec','--onnx='+model['onnx'],'--saveEngine='+str(engine),
             '--fp16','--inputIOFormats=fp32:chw','--outputIOFormats=fp32:chw',
             '--skipInference','--memPoolSize=workspace:2048'],log,builder_env)
    manifest = destination/'model.manifest.yaml'
    manifest.unlink(missing_ok=True)  # only an incomplete, owned generation can reach here
    run(['/usr/bin/python3',generator,'--source-pt',model['source_pt'],'--onnx',model['onnx'],
         '--classes',model['classes'],'--engine',engine,'--output',manifest,
         '--license-reference',model['license_reference'],'--exporter-version',model['exporter_version'],
         '--tensorrt-version',versions['tensorrt'],'--cuda-version',versions['cuda'],
         '--gpu-compute-capability',capability],log)
    shutil.copy2(model['classes'],destination/'classes.txt')
    run([sdk/'usr/src/tensorrt/bin/trtexec','--loadEngine='+str(engine),'--duration=1'],log,env)
    write_json(marker, {'inputs':receipt,'files':{name:digest(destination/name) for name in ('model.engine','model.manifest.yaml','classes.txt')}})
    return destination
