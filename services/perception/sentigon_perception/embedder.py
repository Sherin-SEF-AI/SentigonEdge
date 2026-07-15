"""ReID appearance embedder.

Default backbone is OSNet-AIN x1_0 trained on MSMT17 (a person-ReID dataset), which
is identity-discriminative: same-person crops score far higher than different-person
crops, unlike a generic ImageNet backbone. It produces an L2-normalized 512-d vector
per crop, used for cross-camera ReID + watchlist matching (Qdrant). ResNet50-ImageNet
(2048-d) remains available as a dependency-light fallback via config. Both swap in
behind the same `embed(crops) -> vectors` contract without touching the pipeline.
"""

from __future__ import annotations

import importlib.util
import threading
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

from .config import settings

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_osnet(weights_path: Path, arch_path: Path) -> tuple[torch.nn.Module, int]:
    """Load the vendored OSNet-AIN architecture + MSMT17 weights (feature mode)."""
    spec = importlib.util.spec_from_file_location("osnet_ain_vendored", str(arch_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load OSNet architecture from {arch_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sd = torch.load(str(weights_path), map_location="cpu")
    state = sd.get("state_dict", sd)
    state = {(k[7:] if k.startswith("module.") else k): v for k, v in state.items()}
    # classifier size = number of training identities; infer it so the load is strict
    num_classes = state["classifier.weight"].shape[0] if "classifier.weight" in state else 1000
    model = mod.osnet_ain_x1_0(num_classes=num_classes, pretrained=False)
    model.load_state_dict(state, strict=True)
    return model, 512


class Embedder:
    def __init__(self, device: str = "cuda") -> None:
        self.device = device
        backbone = settings.embed_backbone
        if backbone.startswith("osnet"):
            model, self.dim = _load_osnet(
                _REPO_ROOT / settings.embed_weights, _REPO_ROOT / settings.embed_arch
            )
            self.backbone = backbone
        else:
            from torchvision.models import ResNet50_Weights, resnet50

            model = resnet50(weights=ResNet50_Weights.IMAGENET1K_V2)
            model.fc = torch.nn.Identity()  # 2048-d penultimate features
            self.dim = 2048
            self.backbone = "resnet50"
        # fp32 throughout: the embedding volume is small, and fp32 avoids the
        # half/float dtype mismatch with the normalization tensors.
        self.model = model.eval().to(device)
        self._mean = torch.tensor([0.485, 0.456, 0.406], device=device).view(1, 3, 1, 1)
        self._std = torch.tensor([0.229, 0.224, 0.225], device=device).view(1, 3, 1, 1)
        self._lock = threading.Lock()  # serialize GPU access across camera threads

    @torch.inference_mode()
    def embed(self, crops: list[np.ndarray]) -> list[list[float]]:
        """crops: list of BGR HxWx3 uint8 arrays. Returns L2-normalized vectors."""
        if not crops:
            return []
        tensors = []
        for c in crops:
            if c.size == 0:
                continue
            rgb = c[:, :, ::-1].copy()  # BGR -> RGB
            t = torch.from_numpy(rgb).permute(2, 0, 1).float() / 255.0
            tensors.append(t)
        if not tensors:
            return []
        with self._lock:
            batch = torch.stack(
                [
                    F.interpolate(
                        t.unsqueeze(0), size=(256, 128), mode="bilinear", align_corners=False
                    )[0]
                    for t in tensors
                ]
            ).to(self.device)
            batch = (batch - self._mean) / self._std
            feats = self.model(batch)
            feats = F.normalize(feats.float(), dim=1)
            return feats.cpu().tolist()
