# Issues — audit snapshot 2026-05-15

State of `feat/p1-t3-awin-source` after P1-T4 implementation. New code
(`app/services/embedding.py`, `app/catalog/embed_job.py`, plus their tests)
passes ruff, format, mypy, and pytest. Issues below are pre-existing or
environmental — none block P1-T5, but CI on this branch is currently red.

Status legend: 🔴 blocks CI · 🟡 latent bug / drift · 🟢 hygiene.

---

## 🔴 CI-blocking

### 1. `ruff check` fails — `app/main.py` import block unsorted
- **Where**: `app/main.py:1-14` (rule `I001`)
- **Why**: third-party (`structlog`, `fastapi`) and first-party (`app.*`) imports are interleaved instead of grouped.
- **Fix**: `ruff check --fix app/main.py` (one-line auto-fix), then commit.
- **Note**: pre-existing on the P0 commit `09fde9e` — likely passed at the time because the locally installed ruff was older. Current env has ruff 0.15.12; pyproject pin is `ruff>=0.4.8`.

### 2. `ruff format --check` reports 8 unformatted files
- **Files**:
  - `alembic/versions/1ea1f6a1ed9d_p1_t1_catalog_schema_products_indexing_.py`
  - `app/api/middleware.py`
  - `app/catalog/sources/awin.py`
  - `app/db/models.py`
  - `app/db/r2.py`
  - `app/services/taxonomy.py`
  - `tests/services/test_taxonomy.py`
  - `tests/test_smoke.py`
- **Why**: again pre-existing; current ruff format rules tightened since baseline.
- **Fix**: `ruff format <files>`. Pure cosmetic diff — no behavior change.

### 3. `mypy app/` reports 13 errors in 3 files
- **`app/db/qdrant.py:26`** — 7 errors from `AsyncQdrantClient(**kwargs)` where `kwargs: dict[str, str]`. The client accepts heterogeneous kwarg types (`int | bool | dict | Callable`), so a `dict[str, str]` unpack is type-unsafe. **Fix**: build the client with explicit kwargs:
  ```python
  _qdrant_client = AsyncQdrantClient(
      url=settings.qdrant_url,
      api_key=settings.qdrant_api_key or None,
  )
  ```
- **`app/db/redis.py:8,11,18`** — three `# type: ignore[type-arg]` comments are now unused because newer `redis` stubs make `aioredis.Redis` non-generic. **Fix**: drop the three ignores.
- **`app/db/redis.py:46`** — `await client.ping()` flagged because `ping()` is typed as `Awaitable[bool] | bool` in current stubs. **Fix**: `result = client.ping(); result = await result if hasattr(result, "__await__") else result` — or, simpler, cast: `result = await client.ping()  # type: ignore[misc]` (the runtime returns an awaitable on async clients).
- **`app/api/middleware.py:37`** — unused `# type: ignore[type-arg]` on `send_with_header(message: dict)`.
- **`app/api/middleware.py:47`** — ASGI scope/message types don't satisfy starlette's `MutableMapping[str, Any]` parameter. **Fix**: change `send_with_header(message: dict)` to `send_with_header(message: Message)` (import `Message` from `starlette.types`).

---

## 🟡 Drift / latent bugs

### 4. Local venv is Python 3.10; pyproject requires 3.12
- `.venv/pyvenv.cfg` shows `version = 3.10.12`, but `pyproject.toml` says `requires-python = ">=3.12"`.
- Local runs still pass because we don't use 3.12-only syntax, but mypy operates with `python_version = "3.12"` settings against a 3.10 interpreter — stub mismatches above are partly caused by this.
- **Fix**: recreate venv with `python3.12 -m venv .venv` once 3.12 is available on the box. CI uses 3.12 already.

### 5. Tooling-version drift between pyproject pins and what's installed
- pyproject: `ruff>=0.4.8`, `mypy>=1.10.0` → installed: `ruff 0.15.12`, `mypy 2.1.0`.
- Both major versions tightened rules that the existing codebase doesn't satisfy → see items 1–3.
- **Fix**: either (a) tighten pins and update the code once, or (b) loosen and pin to a known-good range.

### 6. transformers v5 installed; CLIP integration in P1-T4 had to be rewritten
- `transformers 5.8.1` ships a different return type for `CLIPModel.get_image_features`. Worked around by calling `model.vision_model(...).pooler_output` + `model.visual_projection(...)` explicitly — stable across v4/v5.
- **Watch**: HF stubs in v5 also misannotate `from_pretrained` as wanting `PreTrainedModel`; we typed the result `Any` instead of suppressing.

### 7. Pillow 12.x installed; pyproject says `>=10.3.0`
- No issue surfaced in tests, but PIL has known per-major behavior differences (palette decoding, `getexif`). Worth pinning a known floor.

### 8. `app/models/` is an empty package
- Only `__init__.py` present, declared in `[tool.setuptools].packages`. Either populate (Pydantic request/response models for `/analyze` per P4-T1) or drop.

### 9. README duplication
- `README.md` (35 lines, real) and `README.txt` (1 line, typo `Personnal`) coexist. **Fix**: delete `README.txt`.

---

## 🟢 Hygiene / future work

### 10. `ImageFile.LOAD_TRUNCATED_IMAGES = True` is a global side effect
- Set at module import time in `app/services/embedding.py`. Affects every PIL operation in the process, not just our embed path. Acceptable for our use (we want lenient decoding of CDN-served images everywhere), but worth documenting if a stricter consumer is added later.

### 11. `embed_products` doesn't deduplicate identical `image_url`s within a batch
- Two products in the same call sharing one image will hash-collide on the cache key but trigger two downloads (one per cache miss row in `_download_and_embed`). Cheap to fix when it matters: group `to_download` by `image_hash` before downloading.

### 12. No end-to-end smoke against a real CLIP forward pass in CI
- `test_embed_pil_images_returns_l2_normalized_512d` is gated by `_can_load_clip()` which loads the model up-front to decide whether to skip. That doubles CI time when weights *are* cached (loads twice).
- **Fix**: cache the embedder at module scope or use a session-scoped fixture so the gate and the test share one load.

### 13. `app/db/redis.py` warning suppressions are stale
- Three `# type: ignore[type-arg]` for `aioredis.Redis` reflect an older redis-stubs version. Rolled into issue 3 above but worth a separate grep next time redis is bumped.

---

## Quick triage order

1. Run `ruff check --fix . && ruff format .` → resolves issues 1 + 2 in one commit. Diff is mechanical.
2. Apply the four explicit fixes in `app/db/qdrant.py`, `app/db/redis.py`, `app/api/middleware.py` → resolves issue 3.
3. Recreate `.venv` on Python 3.12 → resolves issue 4 and lets mypy see the right stubs.
4. Issues 5–13 can roll into a "tooling hygiene" PR after P1-T5/T6 land.

## Out of scope but called out

- **Deployment** (Railway + Qdrant Cloud + R2) still blocked per `CLAUDE.md` §"What is NOT done". P0-T4 code is committed but never exercised against the real services. Worth flagging again before P1-T6 because P1-T6's "done when" checks (`indexing_runs` row, Qdrant Cloud UI spot-check) need the deploy to mean anything.
