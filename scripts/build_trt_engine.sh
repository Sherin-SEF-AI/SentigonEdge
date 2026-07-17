#!/usr/bin/env bash
# Build a TensorRT engine for the YOLO detector on this Jetson and expose the
# apt-installed TensorRT python bindings to the uv venv. Run AFTER:
#   sudo apt-get install -y tensorrt
# Idempotent.
#
#   bash scripts/build_trt_engine.sh [model.pt] [imgsz] [precision] [calib.yaml]
#     model     default yolo26m.pt
#     imgsz     default 640   (512 is ~1.5x faster for a small accuracy cost)
#     precision fp16 (default) | int8   (int8 is ~1.5-2x faster than fp16 on Orin)
#     calib     INT8 calibration dataset yaml (e.g. coco.yaml) — needed for good
#               INT8 accuracy; without it INT8 uses default calibration and drifts.
set -euo pipefail
cd "$(dirname "$0")/.."

# Match the system TensorRT bindings to the venv's ACTUAL python version (do not
# hardcode 3.12: linking a different minor version's C-extensions ABI-breaks import).
PYVER="$(.venv/bin/python -c 'import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
VENV_SITE="$(.venv/bin/python -c 'import site;print(site.getsitepackages()[0])')"
SYS_DIST="/usr/lib/python${PYVER}/dist-packages"

echo "== 1. expose system TensorRT bindings (python ${PYVER}) to the venv =="
if [ ! -d "$SYS_DIST" ]; then
  echo "  WARNING: $SYS_DIST not found — is 'tensorrt' apt package installed for python ${PYVER}?"
fi
for mod in tensorrt tensorrt_bindings tensorrt_libs tensorrt_lean tensorrt_dispatch; do
  if [ -e "$SYS_DIST/$mod" ] && [ ! -e "$VENV_SITE/$mod" ]; then
    ln -s "$SYS_DIST/$mod" "$VENV_SITE/$mod" && echo "  linked $mod"
  fi
done
.venv/bin/python -c "import tensorrt as trt; print('  tensorrt', trt.__version__, 'importable in venv')"

MODEL="${1:-yolo26m.pt}"
IMGSZ="${2:-640}"
PRECISION="${3:-fp16}"
CALIB="${4:-}"

echo "== 2. export $MODEL -> TensorRT engine ($PRECISION, imgsz=$IMGSZ, built for THIS GPU sm_87) =="
.venv/bin/python - "$MODEL" "$IMGSZ" "$PRECISION" "$CALIB" <<'PY'
import sys

from ultralytics import YOLO

model, imgsz, precision, calib = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
kw = dict(format="engine", imgsz=imgsz, device=0, dynamic=False, batch=1, workspace=8)
if precision == "int8":
    kw["int8"] = True
    if calib:
        kw["data"] = calib
    else:
        print("  WARNING: INT8 without a calibration dataset (arg 4) will reduce accuracy.")
else:
    kw["half"] = True  # FP16
path = YOLO(model).export(**kw)
print("ENGINE:", path)
PY
echo "== done. point perception at it:  PERCEPTION_MODEL=$(basename "${MODEL%.pt}").engine =="
