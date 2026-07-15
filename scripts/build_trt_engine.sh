#!/usr/bin/env bash
# Build a TensorRT FP16 engine for the YOLO detector on this Jetson and expose the
# apt-installed TensorRT python bindings to the uv venv. Run AFTER: sudo apt-get
# install -y tensorrt. Idempotent.
set -euo pipefail
cd "$(dirname "$0")/.."
VENV_SITE="$(.venv/bin/python -c 'import site;print(site.getsitepackages()[0])')"
SYS_DIST=/usr/lib/python3.12/dist-packages

echo "== 1. expose system TensorRT bindings to the venv =="
for mod in tensorrt tensorrt_bindings tensorrt_libs tensorrt_lean tensorrt_dispatch; do
  if [ -e "$SYS_DIST/$mod" ] && [ ! -e "$VENV_SITE/$mod" ]; then
    ln -s "$SYS_DIST/$mod" "$VENV_SITE/$mod" && echo "  linked $mod"
  fi
done
# some apt layouts ship a single tensorrt-*.dist-info + tensorrt.py; link the .so-backed pkg dir
.venv/bin/python -c "import tensorrt as trt; print('  tensorrt', trt.__version__, 'importable in venv')"

echo "== 2. export yolo26m -> TensorRT FP16 engine (built for THIS GPU, sm_87) =="
MODEL="${1:-yolo26m.pt}"
IMGSZ="${2:-640}"
.venv/bin/python - "$MODEL" "$IMGSZ" <<'PY'
import sys
from ultralytics import YOLO
model, imgsz = sys.argv[1], int(sys.argv[2])
m = YOLO(model)
path = m.export(format="engine", half=True, imgsz=imgsz, device=0, dynamic=False, batch=1, workspace=8)
print("ENGINE:", path)
PY
echo "== done. engine written next to the .pt =="
