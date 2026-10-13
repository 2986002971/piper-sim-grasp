#!/usr/bin/env bash
set -eo pipefail
config_file="${1:?device config required}"; shift
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
isaac_env="$(/usr/bin/python3 - "$config_file" <<'PY'
import pathlib,sys,yaml
p=pathlib.Path(sys.argv[1]).resolve()
c=yaml.safe_load(p.read_text())
print((p.parent/pathlib.Path(c['isaac_env'])).resolve())
PY
)"
# python.sh removes arbitrary inherited preload; explicitly authorize only our
# freshly built NVENC filter when starting the streaming experience.
exec bash "$script_dir/python.sh" "$isaac_env" "$script_dir/launch.py" --config "$config_file" "$@"
