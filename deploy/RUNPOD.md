# Sentigon V2 - RunPod GPU tier (Qwen3-VL-32B + DeepStream 8)

This is the production model-serving tier. It is **config-swapped**, not a fork:
the exact services that run locally on the dev box run here, pointed at bigger
models on datacenter GPUs.

| Capability | Local dev box (RTX 5080, 16 GB) | RunPod (A100/H100 80 GB) |
|---|---|---|
| Reasoning VLM | Qwen2.5-VL-7B via **Ollama** | Qwen3-VL-32B via **vLLM** |
| Detector | YOLO26m, **PyTorch/TensorRT** worker | YOLO26x, **DeepStream 8** pipeline |
| Switch | `REASON_BACKEND=ollama` | `REASON_BACKEND=vllm` + Helm `values-runpod.yaml` |

> Status: this tier is authored and locally validated as far as a box without a
> datacenter GPU allows (config parses, the reason backend switch is exercised
> live, the DeepStream configs are complete). Qwen3-VL-32B needs ~65 GB of VRAM
> and DeepStream 8 targets a dGPU, so **running it requires a rented A100/H100** -
> that is the one genuinely blocked step, and it is blocked on hardware, not code.

## What is already proven locally

- **Reason is config-pluggable and the switch is real.** The verifier calls a
  plain OpenAI-compatible `POST {REASON_ENDPOINT}/chat/completions` with
  `REASON_MODEL` (see [verifier.py](../services/reason/sentigon_reason/verifier.py)).
  On the dev box it runs live against Ollama:
  ```json
  {"backend":"ollama","model":"qwen2.5vl:7b","endpoint":"http://localhost:11434/v1",
   "verified":447,"confirmed":181,"rejected":266,"avg_latency_ms":1496.0}
  ```
  Switching to the 32B tier changes only three env vars - no code change.
- **The GPU serving stack config validates:**
  `docker compose -f docker-compose.yml -f docker-compose.gpu.yml --profile gpu config`
  renders both `vllm` and `triton` services with `nvidia` GPU reservations.
- **DeepStream pipeline is complete:** infer/tracker/analytics configs +
  `deepstream_perception.py` in [services/perception/deepstream](../services/perception/deepstream).

## A. Reasoning: Qwen3-VL-32B on vLLM

Option 1 - compose (single RunPod pod with a GPU):
```bash
REASON_MODEL=Qwen/Qwen3-VL-32B-Instruct HF_TOKEN=hf_... \
  docker compose -f docker-compose.yml -f docker-compose.gpu.yml --profile gpu up -d vllm
# vLLM serves the OpenAI API on :8050 -> point the reason service at it:
export REASON_BACKEND=vllm
export REASON_MODEL=Qwen/Qwen3-VL-32B-Instruct
export REASON_ENDPOINT=http://vllm:8000/v1
```

Option 2 - Kubernetes (Helm), the `vllm` Deployment from `values-runpod.yaml`
schedules onto a GPU node and the reason service resolves it via
`reason.endpoint=http://sentigon-vllm:8000/v1`. See [README.md](README.md).

Smoke test once it is up (same call the verifier makes):
```bash
curl -s http://<vllm>:8000/v1/chat/completions -H 'Content-Type: application/json' -d '{
  "model":"Qwen/Qwen3-VL-32B-Instruct",
  "messages":[{"role":"user","content":"Respond with the single word: ready"}]}'
```

## B. Perception: DeepStream 8 (YOLO26x, hardware-decoded, multi-stream)

```bash
# 1. Export the ONNX + build the TensorRT engine ON the target GPU
#    (see models/export.md; DeepStream builds the .engine from .onnx on first run).
# 2. Build and run the DeepStream image:
docker build -t sentigon/perception-ds services/perception/deepstream
docker run --gpus all --network sentigon_default \
  -e KAFKA_BOOTSTRAP=redpanda:9092 \
  -e DS_URIS='["rtsp://mediamtx:8554/cam_lobby","rtsp://mediamtx:8554/cam_dock"]' \
  -e DS_SOURCE_IDS='["<uuid-lobby>","<uuid-dock>"]' \
  -v $(pwd)/models:/models sentigon/perception-ds
```
It emits the identical `perception.objects` / `perception.embeddings` Kafka
contract the local PyTorch worker emits, so context/reason/search downstream are
unchanged. Config: [config_infer_primary_yolo26.txt](../services/perception/deepstream/config_infer_primary_yolo26.txt)
(NMS-free E2E parser, fp16, batch-8), NvDCF tracker, nvdsanalytics ROI/line-crossing.

## C. Provisioning notes

- RunPod pod or Secure Cloud with **A100 80 GB** (32B fits at ~65 GB with
  `--gpu-memory-utilization 0.9`) or **H100** for headroom + throughput.
- Data services (Postgres/Redis/Redpanda/Qdrant/MinIO) either as RunPod pods or
  managed; put their URLs in the Helm `infra.*` values.
- The 32B needs a Hugging Face token with access to the Qwen3-VL weights.

## Verification checklist (on the rented GPU)

1. `vllm` pod healthy; the chat/completions smoke test returns `ready`.
2. Reason `/stats` shows `backend=vllm`, `model=Qwen/Qwen3-VL-32B-Instruct`.
3. DeepStream container decodes the RTSP inputs and `perception.objects` flows on
   Kafka (`rpk topic consume perception.objects`).
4. End-to-end: a real incident is verified by the 32B and lands in the console -
   same pipeline as the local 7B tier, higher accuracy.
