"""Sentigon perception service.

GPU detect/segment/pose + tracking + ReID appearance embeddings over the RTSP
restreams. Emits object metadata to Kafka `perception.objects` and appearance
embeddings to `perception.embeddings` (indexed in Qdrant). Pixels never touch the
bus. A WebSocket relays the latest detections per camera to the console overlay.

The local path is a PyTorch/TensorRT worker (reliable on brand-new Blackwell
silicon). The DeepStream 8 pipeline in deepstream/ is the RunPod-target path and
emits the identical Kafka contract.
"""

__version__ = "0.1.0"
