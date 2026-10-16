"""Keep inference SDKs small; acquire Linux builder resources only while building."""
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

LOCK = json.loads((Path(__file__).resolve().parents[1]/'config/versions.lock.json').read_text())
VERSION = LOCK['tensorrt']+'-1+cuda'+LOCK['cuda']
BASE = 'https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/'
RESOURCE = 'libnvinfer_builder_resource.so.'+'.'.join(LOCK['tensorrt'].split('.')[:3])


def download(package, archive):
    filename = f'{package}_{VERSION}_amd64.deb'
    subprocess.run(['curl', '--fail', '--location', '--silent', '--show-error',
                    '--retry', '4', '--retry-all-errors', '--connect-timeout', '15',
                    '--speed-time', '60', '--speed-limit', '1024', '--max-time', '1800',
                    '--continue-at', '-', '--output', str(archive), BASE+filename], check=True)
    actual = subprocess.check_output(['dpkg-deb', '-f', str(archive), 'Version'], text=True).strip()
    if actual != VERSION:
        raise RuntimeError(f'Unexpected SDK package version: {actual}')


def prune_builder_resources(prefix):
    library = Path(prefix)/'usr/lib/x86_64-linux-gnu'
    for path in library.glob('libnvinfer_builder_resource*.so*'):
        path.unlink()


def extract_builder(archive, destination):
    """Stream the package without unpacking Windows resources or duplicate runtimes."""
    destination = Path(destination)
    found = False
    with subprocess.Popen(['dpkg-deb', '--fsys-tarfile', str(archive)], stdout=subprocess.PIPE) as process:
        try:
            with tarfile.open(fileobj=process.stdout, mode='r|') as files:
                for member in files:
                    if member.name.removeprefix('./') == 'usr/lib/x86_64-linux-gnu/'+RESOURCE:
                        if not member.isfile() or found:
                            raise RuntimeError('Unexpected TensorRT builder resource entry')
                        with files.extractfile(member) as source, destination.open('wb') as output:
                            shutil.copyfileobj(source, output)
                        found = True
            if process.wait() != 0:
                raise RuntimeError('Cannot unpack TensorRT builder resources')
        finally:
            if process.poll() is None:
                process.terminate()
            process.stdout.close()
    if not found:
        raise RuntimeError('TensorRT package lacks the expected Linux builder resource')


@contextmanager
def builder_library(prefix, cache):
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='trt-build-', dir=cache) as temporary:
        directory = Path(temporary)
        resource = directory/RESOURCE
        existing = Path(prefix)/'usr/lib/x86_64-linux-gnu'/RESOURCE
        if existing.is_file():
            # Consume a resource left by an older SDK into owned temporary storage.
            shutil.move(str(existing), resource)
        else:
            archive = directory/'libnvinfer.deb'
            print('Acquiring temporary Linux TensorRT builder resources', flush=True)
            download('libnvinfer10', archive)
            extract_builder(archive, resource)
            archive.unlink()
        yield directory
