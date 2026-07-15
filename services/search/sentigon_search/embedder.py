"""CLIP embedder: images and text into a shared 512-d space (open-clip ViT-B-32)."""

from __future__ import annotations

import io
import threading

import open_clip
import torch
from PIL import Image
from sentigon_common.logging import get_logger

from .config import settings

log = get_logger("search.clip")


class ClipEmbedder:
    def __init__(self) -> None:
        self.device = settings.clip_device
        self.model, _, self.preprocess = open_clip.create_model_and_transforms(
            settings.clip_model, pretrained=settings.clip_pretrained, device=self.device
        )
        self.model.eval()
        self.tokenizer = open_clip.get_tokenizer(settings.clip_model)
        self.dim = self.model.visual.output_dim
        self._lock = threading.Lock()
        log.info("clip.loaded", model=settings.clip_model, dim=self.dim, device=self.device)

    @torch.inference_mode()
    def embed_image(self, data: bytes) -> list[float] | None:
        try:
            img = Image.open(io.BytesIO(data)).convert("RGB")
        except Exception:  # noqa: BLE001
            return None
        with self._lock:
            t = self.preprocess(img).unsqueeze(0).to(self.device)
            f = self.model.encode_image(t)
            f = f / f.norm(dim=-1, keepdim=True)
            return f[0].cpu().tolist()

    @torch.inference_mode()
    def embed_text(self, text: str) -> list[float]:
        with self._lock:
            tok = self.tokenizer([text]).to(self.device)
            f = self.model.encode_text(tok)
            f = f / f.norm(dim=-1, keepdim=True)
            return f[0].cpu().tolist()
