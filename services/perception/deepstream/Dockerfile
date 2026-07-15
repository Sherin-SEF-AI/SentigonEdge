# DeepStream 8 perception image (RunPod / dGPU target). Not built on the local dev
# box. Requires the NVIDIA Container Toolkit and an Ampere+ / Blackwell GPU.
#
#   docker build -t sentigon/perception-ds services/perception/deepstream
#   docker run --gpus all --network sentigon_default \
#     -e KAFKA_BOOTSTRAP=redpanda:9092 \
#     -e DS_URIS='["rtsp://mediamtx:8554/cam_lobby", ...]' \
#     -e DS_SOURCE_IDS='["<uuid-lobby>", ...]' \
#     -v $(pwd)/models:/models sentigon/perception-ds

FROM nvcr.io/nvidia/deepstream:8.0-triton-multiarch

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3-pip python3-gi python3-gst-1.0 gir1.2-gst-rtsp-server-1.0 \
    && rm -rf /var/lib/apt/lists/*

# DeepStream Python bindings (pyds) ship with the SDK; install the kafka client.
RUN pip3 install --no-cache-dir confluent-kafka==2.6.0

WORKDIR /app/deepstream
COPY . /app/deepstream

# The YOLO custom bbox parser (NvDsInferParseYoloE2E) and the TensorRT engine are
# built from the ONNX on first run against the target GPU; see models/export.md.
ENTRYPOINT ["python3", "deepstream_perception.py"]
