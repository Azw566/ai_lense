# Issues — audit snapshot 2026-05-15 (updated post-P1-T6 + QA review)

State of the branch after Phase 1 close (`v0.1.0-catalog`). CI gates are now
all green locally: 105 tests pass, `ruff check`, `ruff format --check`, and
`mypy app/` all clean.

Status legend: ✅ resolved · 🟡 latent bug / drift · 🟢 hygiene · 🔴 blocks ship.

---

## ✅ Resolved (post-QA-review pass)

### 1. `ruff check` — `app/main.py` import block. ✅ Fixed.
Auto-resolved by `ruff check --fix .`. Imports now grouped properly.

### 2. `ruff format --check` — 8 unformatted files. ✅ Fixed.
Auto-resolved by `ruff format .`. Cosmetic diff only, no behavior change.

### 3. `mypy app/` — 13 errors across 3 files. ✅ Fixed.
- `app/db/qdrant.py` — `AsyncQdrantClient(**kwargs)` replaced with explicit kwargs (mirrors `scripts/init_qdrant.py`).
- `app/db/redis.py` — three stale `# type: ignore[type-arg]` removed; `ping()` return narrowed via `# type: ignore[misc]`.
- `app/api/middleware.py` — `send_with_header(message: dict)` switched to `Message` from `starlette.types`.

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

1. ~~Run `ruff check --fix . && ruff format .`~~ → done.
2. ~~Apply the explicit fixes in `app/db/qdrant.py`, `app/db/redis.py`, `app/api/middleware.py`~~ → done.
3. Recreate `.venv` on Python 3.12 → resolves issue 4 and lets mypy see the right stubs.
4. Issues 5–13 can roll into a "tooling hygiene" PR after Phase 2 starts.

## 🔴 Blocks ship — deployment

**Railway + Qdrant Cloud + R2 deploy is still open** per `CLAUDE.md` §"What is NOT done". P0-T4 + Phase 1 code is committed and tested in-memory but **never exercised against the real services**. The QA review (Phase 1) called this out as the top risk to ship date:

- P1-T6 "Done when" needs an `indexing_runs` row on real Postgres + a Qdrant Cloud UI spot-check — neither performed.
- The Postgres dialect branches in `app/catalog/ingest.py` (`ON CONFLICT DO UPDATE`) and `app/catalog/embed_job.py` (`ON CONFLICT DO NOTHING`) are **completely untested in production dialect** — tests only hit the SQLite fallback.
- The Qdrant `filter+ANN` payload-index claim is untested — local Qdrant ignores indexes (warning at runtime).
- The live CLIP forward pass is silently skipped on CI when weights aren't cached (`tests/services/test_embedding.py::test_embed_pil_images_returns_l2_normalized_512d`).

Until these are run end-to-end against the real stack, Phase 2 should not start.
