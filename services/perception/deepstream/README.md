# DeepStream 8 perception (RunPod / dGPU path)

This is the GPU-accelerated, high-density production perception backend. It is the
RunPod-target path; the **local development path is the PyTorch/TensorRT worker**
(`sentigon_perception`) which runs on the RTX 5080 dev box and is what Phase 2 was
built and verified against.

## Why two paths

DeepStream 8 supports Blackwell, but standing up the full DeepStream runtime +
NVIDIA Container Toolkit on a brand-new consumer card is high-integration-risk. The
PyTorch worker is the reliable local path. **Both emit the identical Kafka
contract** (`ObjectDetectionMsg` on `perception.objects`), so the Context service
downstream does not care which backend produced the metadata. Swap by deployment.

## Contract parity

| | Local PyTorch worker | DeepStream pipeline |
|---|---|---|
| Detector | YOLO26 (Ultralytics, TensorRT FP16) | YOLO26 ONNX -> TensorRT, nvinfer |
| Tracker | ByteTrack (per-worker) | NvDCF / BoT-SORT (nvtracker) |
| Zones | point-in-polygon (zones.py) | nvdsanalytics ROI/line/overcrowding |
| Output | `perception.objects` JSON | `perception.objects` JSON (same schema) |
| Density | ~4 streams / this GPU | tens of streams / A100-H100 |

## Build + run (on the RunPod host)

```bash
docker build -t sentigon/perception-ds services/perception/deepstream
docker run --gpus all --network sentigon_default \
  -e KAFKA_BOOTSTRAP=redpanda:9092 \
  -e DS_URIS='["rtsp://mediamtx:8554/cam_lobby","rtsp://mediamtx:8554/cam_dock"]' \
  -e DS_SOURCE_IDS='["<camera-uuid-lobby>","<camera-uuid-dock>"]' \
  -v $(pwd)/models:/models \
  sentigon/perception-ds
```

`DS_SOURCE_IDS` maps nvstreammux source index -> the DB camera UUID so downstream
metadata carries the right `camera_id`.

## Files

- `deepstream_perception.py` nvstreammux -> nvinfer(YOLO26) -> nvtracker -> nvdsanalytics -> Kafka probe
- `config_infer_primary_yolo26.txt` nvinfer (YOLO26, end-to-end parser, FP16)
- `config_tracker_nvdcf.yml` NvDCF tracker (BoT-SORT by swapping the state estimator)
- `config_nvdsanalytics.txt` ROI/line-crossing/overcrowding (zones pushed from the DB at deploy)
- `Dockerfile` `nvcr.io/nvidia/deepstream:8.0-triton-multiarch`

## Model export

Export YOLO26 to ONNX and build the TensorRT engine on the target GPU:

```bash
yolo export model=yolo26x.pt format=onnx opset=17 dynamic=True simplify=True
# engine is built by nvinfer on first run from the onnx-file in the config
```
