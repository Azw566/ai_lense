"""CLIP ViT-B/32 image embedding (roadmap P1-T4).

The user-facing path (Phase 2) and the catalog path (Phase 1) both encode images
with the same CLIP backbone, so this module owns the encoder. We load the
HuggingFace `openai/clip-vit-base-patch32` model once, run inference with the
bundled `CLIPProcessor` (never roll-your-own preprocessing — the resize/center-
crop/normalize triplet has gotchas that silently degrade retrieval), and return
L2-normalized `float32` vectors so Qdrant `Cosine` distance equals dot product
(arch §3.3).

`download_image` filters out the failure modes that would otherwise poison the
batch: timeouts, 404s, non-image responses, malformed bytes, too-small thumbs,
and animated GIFs (only the first frame is meaningful for our task). One bad
url must never crash the surrounding batch (arch §5.1).
"""

from __future__ import annotations

import hashlib
import io
import threading
from dataclasses import dataclass
from typing import Any

import httpx
import numpy as np
from PIL import Image, ImageFile, UnidentifiedImageError

from app.core.logging import get_logger

logger = get_logger(__name__)

# HuggingFace model id; pin only the major contract (512-d L2-normalized image
# embeddings). Bumping this is a re-index event — bump `MODEL_VERSION` too so
# the embedding cache invalidates instead of mixing two encoders.
MODEL_NAME = "openai/clip-vit-base-patch32"
MODEL_VERSION = "clip-vit-base-patch32-v1"
EMBED_DIM = 512
BATCH_SIZE = 32

# Minimum useful side length. Awin sometimes ships 50×50 thumbs that resize up
# to garbage at CLIP's 224×224 input. Below this we'd rather skip than embed.
MIN_IMAGE_SIDE = 80

# Cap on bytes we'll pull per image — guards against a malicious feed pointing
# at a multi-GB asset. ~5 MB covers any legitimate product photo.
MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024

# PIL refuses truncated JPEGs by default; many real product CDNs serve them.
# Allow truncated decode so a half-streamed image still embeds.
ImageFile.LOAD_TRUNCATED_IMAGES = True


def sha256_of(text: str) -> str:
    """sha256 hex of a string (used as the embedding cache key on `image_url`)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def embedding_to_bytes(vector: np.ndarray) -> bytes:
    """Pack a (512,) float32 vector for `embedding_cache.embedding`."""
    if vector.shape != (EMBED_DIM,):
        raise ValueError(f"expected ({EMBED_DIM},), got {vector.shape}")
    return np.ascontiguousarray(vector, dtype=np.float32).tobytes()


def embedding_from_bytes(blob: bytes) -> np.ndarray:
    """Unpack an `embedding_cache.embedding` blob back to a (512,) float32 vector."""
    return np.frombuffer(blob, dtype=np.float32).reshape(EMBED_DIM).copy()


@dataclass(slots=True)
class DownloadResult:
    """Outcome of one image download attempt. `image` is None on any failure."""

    image: Image.Image | None
    reason: str | None  # e.g. "timeout", "http_404", "decode_error", "too_small"


def download_image(
    url: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = 10.0,
) -> DownloadResult:
    """Fetch `url`, return a sanitized RGB PIL image or a typed failure reason.

    Network, transport, and decode errors are all caught — the caller never has
    to wrap this in try/except for batch isolation.
    """
    if not url:
        return DownloadResult(None, "empty_url")

    owns_client = client is None
    client = client or httpx.Client(timeout=timeout, follow_redirects=True)
    try:
        try:
            response = client.get(url)
        except httpx.TimeoutException:
            return DownloadResult(None, "timeout")
        except httpx.HTTPError as exc:
            return DownloadResult(None, f"http_error:{type(exc).__name__}")

        if response.status_code != 200:
            return DownloadResult(None, f"http_{response.status_code}")

        body = response.content
        if not body:
            return DownloadResult(None, "empty_body")
        if len(body) > MAX_DOWNLOAD_BYTES:
            return DownloadResult(None, "too_large")

        return _decode_image(body)
    finally:
        if owns_client:
            client.close()


def _decode_image(body: bytes) -> DownloadResult:
    """Parse `body` to an RGB PIL image; rejects animated frames and tiny thumbs."""
    try:
        # PIL's verify() consumes the file pointer, so we open twice: once for
        # verify, once for actual decode (PIL's recommended pattern).
        Image.open(io.BytesIO(body)).verify()
        opened = Image.open(io.BytesIO(body))
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        return DownloadResult(None, f"decode_error:{type(exc).__name__}")

    width, height = opened.size
    if width < MIN_IMAGE_SIDE or height < MIN_IMAGE_SIDE:
        return DownloadResult(None, "too_small")

    # Animated GIFs / multi-page TIFFs: take frame 0 only. CLIP can't see motion
    # and frame 1+ is sometimes a transparent overlay that ruins the embedding.
    if getattr(opened, "is_animated", False):
        opened.seek(0)

    # Flatten transparency onto white. RGBA → RGB without `.convert("RGB")`
    # alone produces black backgrounds, which biases CLIP toward "dark photo".
    rgb: Image.Image
    if opened.mode in ("RGBA", "LA") or (opened.mode == "P" and "transparency" in opened.info):
        background = Image.new("RGB", opened.size, (255, 255, 255))
        rgba = opened.convert("RGBA")
        background.paste(rgba, mask=rgba.split()[3])
        rgb = background
    else:
        rgb = opened.convert("RGB")

    return DownloadResult(rgb, None)


class CLIPEmbedder:
    """Wraps the HuggingFace CLIP image encoder. Lazy-loads on first call so
    importing this module is cheap (tests don't pay the ~150 MB model load)."""

    def __init__(self, *, model_name: str = MODEL_NAME, device: str | None = None) -> None:
        self._model_name = model_name
        self._device = device
        # Typed `Any`: HF model classes are dynamic and stubs lag the runtime API.
        self._model: Any = None
        self._processor: Any = None
        self._load_lock = threading.Lock()

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is not None:
                return
            # Imported lazily so `import app.services.embedding` doesn't drag
            # ~500 MB of torch + transformers into every test process.
            import torch
            from transformers import CLIPModel, CLIPProcessor

            device = self._device or ("cuda" if torch.cuda.is_available() else "cpu")
            logger.info("clip.loading", model=self._model_name, device=device)
            # HF runtime accepts an HF-Hub model id string here even though
            # the v5 stubs annotate the first parameter as `PreTrainedModel`.
            model: Any = CLIPModel.from_pretrained(self._model_name)
            model.eval()
            model.to(device)
            self._model = model
            self._processor = CLIPProcessor.from_pretrained(self._model_name)
            self._device = device

    @property
    def model_version(self) -> str:
        return MODEL_VERSION

    def embed_pil_images(self, images: list[Image.Image]) -> np.ndarray:
        """Encode `images` and return an (N, 512) float32 L2-normalized array.

        Internally chunks into `BATCH_SIZE` batches — batching is where the bulk
        of CPU throughput comes from (PyTorch's per-forward overhead dwarfs the
        compute for a single 224² image)."""
        if not images:
            return np.zeros((0, EMBED_DIM), dtype=np.float32)
        self._ensure_loaded()

        import torch

        assert self._model is not None and self._processor is not None
        outputs: list[np.ndarray] = []
        for start in range(0, len(images), BATCH_SIZE):
            chunk = images[start : start + BATCH_SIZE]
            inputs = self._processor(images=chunk, return_tensors="pt")
            pixel_values = inputs["pixel_values"].to(self._device)
            # `inference_mode` is stricter than `no_grad` — disables view tracking
            # too, ~5% faster on CPU at this model size. Going through
            # vision_model + visual_projection explicitly (rather than the
            # `get_image_features` helper) is portable across transformers
            # major versions, which have shuffled that helper's return type.
            with torch.inference_mode():
                vision_outputs = self._model.vision_model(pixel_values=pixel_values)
                pooled = vision_outputs.pooler_output
                features = self._model.visual_projection(pooled)
            # CLIP image features are NOT L2-normalized by default; the
            # contrastive head normalizes inside `forward()`. Do it ourselves.
            features = features / features.norm(p=2, dim=-1, keepdim=True)
            outputs.append(features.cpu().to(torch.float32).numpy())
        return np.concatenate(outputs, axis=0)


# Module-level singleton so the model loads once across the whole process.
default_embedder = CLIPEmbedder()
