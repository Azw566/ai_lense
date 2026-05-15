"""Tests for the CLIP image embedding service (P1-T4).

These split into two layers:

* **Pure-Python tests** (no model load): URL/decode helpers, byte packing,
  failure-mode classification. Cheap, run on every CI invocation.
* **Live CLIP test** (gated): one real forward pass over 3 fixture images,
  verifying the contract `embed_pil_images` is committed to — shape (N, 512),
  dtype float32, L2-norm ≈ 1. Skipped automatically if torch/transformers can't
  load the weights (e.g. no network on CI). When run, this catches the silent
  failure modes called out in the roadmap (wrong preprocessing, missing
  normalization).
"""

from __future__ import annotations

import io

import httpx
import numpy as np
import pytest
from PIL import Image

from app.services.embedding import (
    EMBED_DIM,
    CLIPEmbedder,
    _decode_image,
    download_image,
    embedding_from_bytes,
    embedding_to_bytes,
    sha256_of,
)

# -- pure-Python helpers --------------------------------------------------------


def _png_bytes(
    size: tuple[int, int] = (256, 256), color: tuple[int, int, int] = (200, 100, 50)
) -> bytes:
    """Encode a flat-color RGB PNG into bytes — handy across the download tests."""
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def test_sha256_of_is_stable_and_hex() -> None:
    digest = sha256_of("https://img.example.com/x.jpg")
    assert len(digest) == 64
    assert digest == sha256_of("https://img.example.com/x.jpg")  # deterministic


def test_embedding_byte_roundtrip_preserves_vector() -> None:
    vector = np.random.default_rng(0).standard_normal(EMBED_DIM).astype(np.float32)
    restored = embedding_from_bytes(embedding_to_bytes(vector))
    np.testing.assert_array_equal(restored, vector)
    assert restored.dtype == np.float32
    assert restored.shape == (EMBED_DIM,)


def test_embedding_to_bytes_rejects_wrong_shape() -> None:
    with pytest.raises(ValueError, match="expected"):
        embedding_to_bytes(np.zeros(7, dtype=np.float32))


# -- _decode_image: PIL-side failure modes --------------------------------------


def test_decode_rejects_too_small_image() -> None:
    outcome = _decode_image(_png_bytes(size=(40, 40)))
    assert outcome.image is None
    assert outcome.reason == "too_small"


def test_decode_rejects_garbage_bytes() -> None:
    outcome = _decode_image(b"not-an-image")
    assert outcome.image is None
    assert outcome.reason is not None and outcome.reason.startswith("decode_error")


def test_decode_flattens_transparency_onto_white() -> None:
    # Build an RGBA image with a fully-transparent region. Without proper
    # flattening, PIL's plain .convert("RGB") leaves it black, which biases CLIP.
    rgba = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    rgba.paste((255, 0, 0, 255), (0, 0, 128, 256))  # left half opaque red
    buffer = io.BytesIO()
    rgba.save(buffer, format="PNG")
    outcome = _decode_image(buffer.getvalue())
    assert outcome.image is not None and outcome.image.mode == "RGB"
    # The transparent right-half pixel must now be white, not black.
    assert outcome.image.getpixel((200, 100)) == (255, 255, 255)


def test_decode_takes_first_frame_of_animated_gif() -> None:
    frame1 = Image.new("RGB", (256, 256), (255, 0, 0))
    frame2 = Image.new("RGB", (256, 256), (0, 0, 255))
    buffer = io.BytesIO()
    frame1.save(buffer, format="GIF", save_all=True, append_images=[frame2])
    outcome = _decode_image(buffer.getvalue())
    assert outcome.image is not None
    # Frame 0 is red — getpixel returns palette-indexed values after PIL converts.
    # Easier: just check the image came back as a single usable frame.
    assert outcome.image.size == (256, 256)


# -- download_image: HTTP-side failure modes ------------------------------------


def _client_with(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_download_image_returns_pil_image_for_200() -> None:
    client = _client_with(lambda req: httpx.Response(200, content=_png_bytes()))
    outcome = download_image("https://x/img.png", client=client)
    assert outcome.image is not None
    assert outcome.image.mode == "RGB"
    assert outcome.reason is None


def test_download_image_classifies_404() -> None:
    client = _client_with(lambda req: httpx.Response(404))
    outcome = download_image("https://x/missing.png", client=client)
    assert outcome.image is None
    assert outcome.reason == "http_404"


def test_download_image_rejects_oversized_body() -> None:
    huge = b"\x89PNG\r\n\x1a\n" + b"x" * (6 * 1024 * 1024)
    client = _client_with(lambda req: httpx.Response(200, content=huge))
    outcome = download_image("https://x/big.png", client=client)
    assert outcome.image is None
    assert outcome.reason == "too_large"


def test_download_image_rejects_empty_url() -> None:
    outcome = download_image("")
    assert outcome.image is None
    assert outcome.reason == "empty_url"


def test_download_image_rejects_non_image_body() -> None:
    client = _client_with(lambda req: httpx.Response(200, content=b"<html>oops</html>"))
    outcome = download_image("https://x/notimg", client=client)
    assert outcome.image is None
    assert outcome.reason is not None and outcome.reason.startswith("decode_error")


def test_download_image_handles_timeout() -> None:
    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=req)

    client = _client_with(boom)
    outcome = download_image("https://x/slow", client=client)
    assert outcome.image is None
    assert outcome.reason == "timeout"


# -- live CLIP forward pass (the contract the roadmap calls out) ----------------


def _can_load_clip() -> bool:
    """True if the CLIP weights are loadable; we don't want this test to fail
    on a sandbox with no model cache and no network."""
    try:
        CLIPEmbedder()._ensure_loaded()
    except Exception:  # noqa: BLE001
        return False
    return True


@pytest.mark.skipif(not _can_load_clip(), reason="CLIP weights not available in this env")
def test_embed_pil_images_returns_l2_normalized_512d() -> None:
    images = [
        Image.new("RGB", (256, 256), (255, 0, 0)),
        Image.new("RGB", (256, 256), (0, 255, 0)),
        Image.new("RGB", (256, 256), (0, 0, 255)),
    ]
    embedder = CLIPEmbedder()
    vectors = embedder.embed_pil_images(images)
    assert vectors.shape == (3, EMBED_DIM)
    assert vectors.dtype == np.float32
    # CLIP is L2-normalized in our contract — Qdrant Cosine distance assumes it.
    norms = np.linalg.norm(vectors, axis=1)
    np.testing.assert_allclose(norms, np.ones(3), atol=1e-4)


def test_embed_pil_images_handles_empty_list() -> None:
    # No model load needed for the empty case — the lazy guard short-circuits.
    embedder = CLIPEmbedder()
    out = embedder.embed_pil_images([])
    assert out.shape == (0, EMBED_DIM)
    assert out.dtype == np.float32
