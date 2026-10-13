"""Set the optional NVENC preload before replacing this process with Kit."""
import os
from pathlib import Path
import sys
import yaml

config = yaml.safe_load(Path(sys.argv[sys.argv.index('--config')+1]).read_text())
cache = config.get('cache_root')
if cache:
    for key, name in [('XDG_CACHE_HOME','kit'), ('XDG_CONFIG_HOME','kit-config'), ('CUDA_CACHE_PATH','cuda')]:
        folder = Path(cache)/name
        folder.mkdir(parents=True, exist_ok=True)
        os.environ[key] = str(folder)

if '--stream' in sys.argv:
    library = Path(os.environ['PIPER_NVENC_FILTER'])
    if not library.is_file(): raise FileNotFoundError(library)
    os.environ['LD_PRELOAD'] = str(library)
os.execv(sys.executable, [sys.executable, str(Path(__file__).with_name('run_isaac_ros.py')), *sys.argv[1:]])
