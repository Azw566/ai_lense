# Implementation Roadmap — Visual Deco Search

> Companion to `spec_technique_deco_visuel.md` and `architecture_scalability.md`
> Audience: project owner (solo dev) + future development agents
> Scope: MVP launch in 6–8 weeks at ~15 h/week, with explicit per-task model routing

---

## 0. How to read this document

This doc translates the architecture into **executable tasks**, each one sized for a single evening or weekend session, with the recommended Claude model and a copy-pasteable prompt scaffold attached.

It follows three layers:

1. **Phase** — a coherent block of work (e.g. "Inference pipeline"). One per week roughly.
2. **Task card** — atomic unit of work, ID'd as `Px-Ty` (phase x, task y). Sized 2–4 h.
3. **Prompt scaffold** — the actual text you (or you-via-Claude-Code) paste into an agent.

For every task you get: goal, deliverables, prerequisites, effort estimate, **which Claude model to use and why**, the prompt scaffold, key concepts to internalize, and a definition-of-done.

The phase order is not negotiable — earlier phases unblock later ones. Tasks within a phase have explicit prerequisites; anything without a prerequisite arrow can be done in parallel within the phase.

---

## 1. Mental model

### 1.1 Three layers of work, three model tiers

The roadmap implicitly assumes a **routing strategy** across the work: not every task needs Opus-grade reasoning, and not every task can be safely handed to Haiku. The general rule, which holds across the whole project:

| Work type | Model | Why |
|---|---|---|
| Architectural decisions, ML pipeline correctness, cross-cutting refactors, hard debugging | **Opus 4.7** | Step-change over 4.6 on agentic coding, best at multi-file reasoning and "lurking complexity" detection. Worth the $5/$25 per MTok premium *only* when the cost of wrong code is high. |
| Default coding within a clear local context, endpoint implementation, tests, docs | **Sonnet 4.6** | 79.6% SWE-bench Verified at $3/$15. Within 1.2 pts of Opus on coding for 40% less. Should be your daily driver. |
| Boilerplate, syntactic refactors, fixture generation, commit messages, config files | **Haiku 4.5** | $1/$5, 80–120 tok/s. Fast enough that you stop context-switching. Quality penalty is real but irrelevant for mechanical work. |

The single sentence to remember: **Sonnet by default, Opus when correctness is load-bearing, Haiku when speed beats nuance.** A well-routed project spends ~5% of dollars on Opus, ~60% on Sonnet, ~35% on Haiku and still gets Opus-level outcomes on the load-bearing parts.

### 1.2 The four "spines" of the project

Your architecture has four independent technical spines that you build mostly in sequence, with later spines depending on earlier ones being at least skeleton-functional:

```
  ┌──────────────┐       ┌──────────────┐
  │   Catalog    │       │  Inference   │
  │  Ingestion   │       │   Pipeline   │
  └──────┬───────┘       └──────┬───────┘
         │ products             │ embeddings, crops
         ▼                      ▼
  ┌──────────────────────────────────────┐
  │           Search Service             │
  │   (Qdrant queries + ranking logic)   │
  └──────────────────┬───────────────────┘
                     │ matches
                     ▼
  ┌──────────────────────────────────────┐
  │       API + Affiliate + Frontend     │
  │           (User-facing path)         │
  └──────────────────────────────────────┘
```

The roadmap reflects this: Phase 1 = catalog, Phase 2 = inference, Phase 3 = search, Phases 4-6 = user-facing path. Phase 0 sits beneath everything (foundations).

### 1.3 What "done" means at MVP

Strict, non-negotiable MVP exit criteria — the goal is to know when to stop building and start measuring:

- A user uploads a deco photo → gets 3–8 detected objects → each with 3–5 matched products and affiliate links → in <2 s p95 → with structured logs and basic metrics emitted → on a Railway-deployed instance with a public URL.

Everything else (async path, GPU, Prefect orchestration, A/B test framework, multi-region) is `[V1]` or `[SCALE]` and must not creep into MVP scope. The architecture doc §11.1 lists the over-engineering traps; re-read it before agreeing to any "while we're at it..." additions.

---

## 2. Reusable agent prompt scaffold

Every task card below references this template. The structure matters because agents (Claude included) produce noticeably better output when context, constraints, and verification expectations are explicit and separated.

```
ROLE: You are working on the Visual Deco Search MVP. The project owner is a solo
developer on a 6-8 week MVP timeline. Codebase is FastAPI + PostgreSQL + Redis +
Qdrant + Cloudflare R2, deployed on Railway. The architecture and spec docs are
attached/referenced — read them before producing code.

CONTEXT:
- Relevant spec sections: <list section numbers from architecture_scalability.md
  and spec_technique_deco_visuel.md>
- Existing modules touched: <file paths>
- Architectural constraints that apply: <list any from §1 of arch doc, e.g.
  "stateless API, stateful workers" or "Postgres = source of truth">

TASK:
<one-paragraph clear statement of the goal, verbatim from this roadmap>

DELIVERABLES:
1. <concrete file path 1> with <what>
2. <concrete file path 2> with <what>
3. Test in <path> verifying <behavior>

CONSTRAINTS:
- Python 3.12, type hints everywhere, ruff-clean.
- No new top-level dependencies without justifying them (state the alternative
  considered).
- Structured JSON logs only (use the project's existing logger).
- All I/O paths must handle the failure modes listed in arch doc §4.3 if applicable.

NON-GOALS (do NOT do these now):
- <list things tempting to scope-creep, e.g. "do not add GPU support",
  "do not refactor unrelated modules">

VERIFICATION:
- Tests pass: <command>
- Manual check: <how I'll verify it works>

OUTPUT FORMAT: produce a diff or full file contents, then a brief change summary
(<200 words) explaining WHY you made the non-obvious choices.
```

**Why this format works**: the `NON-GOALS` block is the highest-value section. Agents (and humans) over-extend by default; an explicit "don't touch X" cuts the most common failure mode. The `WHY` requirement in the output forces the agent to surface assumptions you can correct.

---

## 3. Phase 0 — Foundations & deployable shell (Week 1, ~10 h)

**Goal**: an empty FastAPI app that returns `200 OK` on `/healthz`, deployed on Railway, with all four backing services (Postgres, Redis, Qdrant, R2) connected and pinged at startup. **No business logic yet.**

This phase is unglamorous and you'll be tempted to skip ahead. Don't. Every later phase assumes these foundations exist; retrofitting structured logging or request-ID propagation later costs 5× more.

### P0-T1 — Repository scaffolding

| | |
|---|---|
| **Goal** | Project skeleton matching arch doc §14 module mapping. |
| **Effort** | 1.5 h |
| **Model** | **Haiku 4.5** |
| **Prerequisites** | none |

**Why Haiku**: this is pure boilerplate generation — directory structure, `pyproject.toml`, `.gitignore`, `.env.example`, `ruff.toml`, `pre-commit-config.yaml`. Haiku at 100 tok/s makes it feel instant, and there's no reasoning needed.

**Deliverables**:
- `app/` package with subdirs: `api/`, `services/`, `models/` (Pydantic), `db/`, `core/` (config + logging).
- `scripts/` for ingestion entrypoints.
- `tests/` mirroring `app/` structure.
- `pyproject.toml` with: fastapi, uvicorn, pydantic v2, pydantic-settings, sqlalchemy 2.x async, asyncpg, redis-py, qdrant-client, boto3 (for R2), structlog, pytest, pytest-asyncio, httpx, ruff, mypy.
- `Dockerfile` (multi-stage, slim runtime).
- `.env.example` listing every env var the app reads.

**Prompt scaffold variation**: skip the full template here, just give Haiku a paste of arch doc §14 and ask for the scaffold. Haiku doesn't need elaborate framing for mechanical work.

**Key concepts to internalize**:
- **Pydantic-settings** vs raw `os.getenv`: typed env access, validates at startup. Fails fast = good.
- **Multi-stage Docker**: builder stage installs deps, runtime stage copies only what's needed → smaller image, faster cold-start on Railway.
- Why mirroring `tests/` to `app/` matters: lets you grep `pytest tests/services/test_search.py` instinctively.

**Done when**: `ruff check .` is clean, `pytest` runs (zero tests passing is fine), `docker build` succeeds.

---

### P0-T2 — Config + structured logging

| | |
|---|---|
| **Goal** | Single config object, structlog with JSON renderer, request-ID middleware. |
| **Effort** | 2 h |
| **Model** | **Sonnet 4.6** |
| **Prerequisites** | P0-T1 |

**Why Sonnet**: structlog setup has subtle wiring (processors order, context binding, async-safe context vars). Haiku gets this wrong about half the time; Opus is overkill.

**Deliverables**:
- `app/core/config.py`: `Settings(BaseSettings)` with sections for `db`, `redis`, `qdrant`, `r2`, `app` (env, log_level, request_id_header).
- `app/core/logging.py`: structlog config with JSON renderer in prod, console renderer in dev. Bound keys per arch §8.1: `timestamp`, `level`, `service`, `request_id`, `message`, plus a `context` dict via `event_dict`.
- `app/api/middleware.py`: ASGI middleware that reads/generates request_id, binds to structlog contextvars, echoes back in response header.
- `app/main.py`: FastAPI app, middleware mounted, `/healthz` endpoint that returns `{"status": "ok", "request_id": <id>}`.

**Key concepts to internalize**:
- **`contextvars.ContextVar`** is how structured logging stays correct under asyncio. Threading.local won't work. structlog's `merge_contextvars` processor does this for you.
- **Middleware ordering matters**: request-ID binding must run before anything that logs. Mount it last so it wraps everything.
- **JSON-by-default in prod** unlocks Loki/Axiom/Better Stack querying like `{level="error"} | json | request_id="abc-123"`. Plain text logs are debugger output, not observability.

**Prompt scaffold**: full template. Reference arch §8.1. Non-goals: "do not add tracing yet, do not add Sentry yet."

**Done when**: hitting `/healthz` produces a JSON log line with a `request_id` matching the `X-Request-ID` response header.

---

### P0-T3 — Database & cache connections (lazy + health-checked)

| | |
|---|---|
| **Goal** | Async connections to Postgres, Redis, Qdrant, R2; all pinged at startup; `/healthz` returns degraded if any is down. |
| **Effort** | 2.5 h |
| **Model** | **Sonnet 4.6** |
| **Prerequisites** | P0-T2 |

**Deliverables**:
- `app/db/postgres.py`: SQLAlchemy 2.0 async engine, session factory, dependency for FastAPI.
- `app/db/redis.py`: `redis.asyncio.Redis` client, connection pool sized 10.
- `app/db/qdrant.py`: async Qdrant client wrapper.
- `app/db/r2.py`: aioboto3 client configured for Cloudflare R2 endpoint.
- `app/main.py` lifespan event: ping each, fail fast if any unreachable in prod, warn in dev.
- `/healthz` upgraded: returns `{"status": "ok"|"degraded", "checks": {pg, redis, qdrant, r2}}`.

**Key concepts**:
- **Lifespan events** (FastAPI's modern replacement for `on_startup`/`on_shutdown`) wrap the app in an async context manager. Use for resources that span the app lifetime: connection pools, model loading.
- **Why ping at startup**: arch §1.7 — "observability from day 1" includes detecting misconfiguration before the first user request. A typo in `DATABASE_URL` should crash the container, not 500 the user.
- **R2-specific gotcha**: Cloudflare R2 uses an S3-compatible API but requires the endpoint URL set explicitly (`https://<account_id>.r2.cloudflarestorage.com`). Easy to lose hours on this.

**Done when**: `docker compose up` (with backing services as docker containers locally) → app starts → `/healthz` returns all four `ok`. Kill the Postgres container → `/healthz` returns degraded for `pg`, app still serves.

---

### P0-T4 — Railway deploy + CI

| | |
|---|---|
| **Goal** | Push to `main` → GitHub Actions runs tests + lint → Railway deploys → public URL responds. |
| **Effort** | 2 h |
| **Model** | **Haiku 4.5** (CI YAML) + **Sonnet 4.6** (Railway config troubleshooting if needed) |
| **Prerequisites** | P0-T3 |

**Deliverables**:
- `.github/workflows/ci.yml`: lint (ruff), typecheck (mypy), pytest, build docker image.
- `railway.toml` or Railway dashboard config: app service + Postgres + Redis plugins.
- Qdrant as Qdrant Cloud free tier (1 GB), separate (Railway doesn't host Qdrant well).
- R2 bucket created on Cloudflare, secrets injected as Railway env vars.
- README section: "Deploying" with the 5-step checklist.

**Key concepts**:
- **Railway plugins** auto-inject env vars like `DATABASE_URL`, `REDIS_URL`. Your `Settings` should read these names (not invent new ones) so it Just Works.
- **GitHub Actions caching**: cache `~/.cache/pip` and Docker layers — cuts CI from 4 min to 1 min.
- **Secrets hygiene**: never `echo $SECRET` in CI logs. GitHub masks declared secrets but not env vars derived from them via shell.

**Done when**: open the Railway-provided public URL, see `/healthz` returning JSON in production. Tag this commit `v0.0.1-foundations`.

---

## 4. Phase 1 — Catalog v0 with Awin (Week 2, ~15 h)

**Goal**: a `scripts/ingest_awin.py` command that pulls products from one Awin CSV feed (you've chosen Awin per the kickoff), normalizes them, embeds the images with CLIP, and upserts to Postgres + Qdrant. Idempotent re-runs produce zero changes.

This is the most important phase to get right. **Bad catalog = unusable product** (arch §5). The matching algorithm is moot if half your embeddings are corrupted or your categories are mismapped.

### P1-T1 — Postgres schema for products + ingestion audit

| | |
|---|---|
| **Goal** | DB schema for `products`, `indexing_runs`, `unmapped_products`, `embedding_cache`. Alembic-managed. |
| **Effort** | 2 h |
| **Model** | **Sonnet 4.6** |
| **Prerequisites** | P0-T4 |

**Deliverables**:
- `app/db/models.py`: SQLAlchemy ORM models for the four tables.
- `alembic/` initialized, first migration committed.
- `products` table columns: `id` (UUID), `retailer` (str), `retailer_product_id` (str, indexed), `title`, `brand` (nullable), `category` (your taxonomy), `price_eur` (numeric), `currency`, `image_url`, `product_url`, `raw_payload` (JSONB), `created_at`, `updated_at`, `last_price_check_at`. Unique constraint on `(retailer, retailer_product_id)`.
- `indexing_runs` table: `id`, `source`, `started_at`, `finished_at`, `status`, `products_in`, `products_out`, `errors_count`, `notes`.
- `unmapped_products` table: products whose source category didn't map → manual review queue.
- `embedding_cache` table: `image_url_sha256` PK, `embedding` (BYTEA — 512 floats packed), `model_version`, `computed_at`.

**Key concepts**:
- **JSONB for `raw_payload`**: keeps the original Awin CSV row. Lets you re-normalize later without re-fetching. Cheap insurance.
- **Unique constraint on `(retailer, retailer_product_id)`** is the idempotency anchor (arch §5.1). Every upsert uses this as the conflict target.
- **Alembic migrations from day 1**: never modify the DB outside migrations. The cost of "I'll add Alembic later" is rewriting your schema history by hand at the worst moment.

**Done when**: `alembic upgrade head` runs cleanly against a fresh Postgres; querying each table returns empty results.

---

### P1-T2 — Category taxonomy YAML + normalizer

| | |
|---|---|
| **Goal** | YAML config defining your 15–25 canonical deco categories with mappings from Awin's taxonomy + YOLO classes. |
| **Effort** | 3 h |
| **Model** | **Opus 4.7** for the taxonomy design pass, **Sonnet 4.6** for the loader code |
| **Prerequisites** | P1-T1 |

**Why Opus for the design pass**: the taxonomy *is* a domain model. Get it wrong and every later product gets mis-bucketed. Opus is meaningfully better at producing taxonomies that are mutually exclusive + collectively exhaustive without you noticing the holes. After Opus drafts the YAML, Sonnet can write the loader code in 20 min.

**Deliverables**:
- `config/taxonomy.yaml`: matches arch §5.3 schema. Categories: couch, armchair, chair, dining_table, coffee_table, side_table, bed, mattress, wardrobe, dresser, bookshelf, lamp_floor, lamp_table, lamp_ceiling, rug, curtain, mirror, wall_art, plant_pot, vase, cushion, throw, candle, clock, storage_basket. (Iterate; this is a starting list.)
- Each entry has `yolo_class` (COCO class ID or `null`), `aliases.awin` (list of source category strings), plus a free-text `description`.
- `app/services/taxonomy.py`: loads YAML, exposes `map_source_category(retailer, source_cat) → your_category | None`.
- `tests/services/test_taxonomy.py`: parametrized tests on 20 real Awin category strings.

**Key concepts**:
- **YOLO COCO classes** only cover ~80 categories (couch=57, chair=56, bed=59, dining_table=60, potted_plant=58, etc.). Many deco objects (cushions, art, mirrors) have no COCO match → you'll handle them via the no-detection fallback (arch §4.3) or fine-tune YOLO later (`[V2]`).
- **Versioned config in git, not code**: the architecture doc insists on this (§5.3). Editing a YAML and PR-reviewing it is safer than redeploying Python on every taxonomy tweak.
- **Quarantine on ambiguous mappings**: if a source category isn't in any aliases list, write to `unmapped_products` and move on. Don't block ingestion on one bad row.

**Prompt scaffold for the Opus design pass**:
```
[paste arch §5.3]
Draft config/taxonomy.yaml for the Visual Deco Search MVP. Constraints:
- 15-25 canonical categories covering >90% of typical Awin home/deco feeds
- Each maps to (a) zero or one YOLO COCO class, (b) Awin source categories as aliases
- Categories must be mutually exclusive — a product belongs to exactly one
- Avoid hyper-specificity (no "3-seat-corner-sofa", just "couch")
- Output the YAML and a 100-word rationale for borderline choices
  (e.g. "armchair vs chair" — when does an item go where?)
```

**Done when**: tests pass; manually feed 10 random Awin category strings and the mapping looks right by inspection.

---

### P1-T3 — Awin CSV ProductSource implementation

| | |
|---|---|
| **Goal** | `AwinSource` class implementing the `ProductSource` ABC (arch §5.2). Fetches one feed, yields normalized product dicts. |
| **Effort** | 3 h |
| **Model** | **Sonnet 4.6** |
| **Prerequisites** | P1-T2 |

**Deliverables**:
- `app/catalog/base.py`: `ProductSource` abstract base class with the four methods from arch §5.2.
- `app/catalog/sources/awin.py`: `AwinSource(ProductSource)`. Streams the CSV (don't load full file in memory — Awin feeds can be 500 MB), parses rows, maps to a `RawProduct` dataclass.
- Affiliate URL builder: takes Awin's `aw_deep_link` field, appends your publisher ID + click tracking params.
- `scripts/inspect_awin_feed.py`: dev utility that prints first 5 rows + category distribution. Useful for sanity-checking new feeds.

**Key concepts**:
- **Stream CSV with `csv.DictReader` over a `requests` response with `stream=True`** — never `.read()` the full body. A 500 MB feed will OOM your Railway container otherwise.
- **The ABC is your insurance**: when you add IKEA in V1, you implement one class. The pipeline doesn't change. If you find yourself adding `if source == "awin"` anywhere in the pipeline code, that's a smell — push it into the source.
- **Affiliate link integrity**: Awin links have a `tag` parameter that must survive any redirect. Test by clicking through and checking your publisher dashboard shows the click.

**Done when**: `python scripts/inspect_awin_feed.py <feed_url>` prints sane output; mapping rate (rows whose category resolves to your taxonomy) is >70% — if lower, expand `taxonomy.yaml` aliases.

---

### P1-T4 — Embedding job (single-process, with cache)

| | |
|---|---|
| **Goal** | A function that takes a list of products with `image_url`, downloads each, embeds with CLIP ViT-B/32, caches by `sha256(image_url)`, returns embeddings. |
| **Effort** | 4 h |
| **Model** | **Opus 4.7** |
| **Prerequisites** | P1-T3 |

**Why Opus**: this is where most ML pipelines silently break. CLIP preprocessing has gotchas (the model expects a specific normalization and image size that's NOT just "resize to 224×224"). Batch tensor shape errors fail silently then ruin retrieval. Failure modes (broken URL, 404 image, malformed JPEG, transparent PNG, animated GIF, image too small) need correct handling. Opus reliably gets this right; Sonnet gets it ~80% right and you'll debug the 20% later.

**Deliverables**:
- `app/services/embedding.py`:
  - `class CLIPEmbedder` — loads `clip-vit-base-patch32` from HuggingFace `transformers` once, exposes `embed_pil_images(images: list[PIL.Image]) → np.ndarray of shape (N, 512)`. Use the model's bundled `CLIPProcessor` for preprocessing — never roll your own.
  - `download_image(url: str) → PIL.Image | None` — handles timeouts, 404s, format validation (via `python-magic` or PIL's `verify()`), too-small images, animated frames.
- `app/catalog/embed_job.py`:
  - `async def embed_products(products: list[RawProduct]) → list[ProductWithEmbedding]`.
  - Checks `embedding_cache` table first (keyed on `sha256(image_url)`).
  - Cache miss → downloads → embeds in batches of 32 → writes to cache table.
  - Quarantines failures (write to `unmapped_products` with reason).
- `tests/services/test_embedding.py`: tests with 3 fixture images, verifies output shape, L2 norm ≈ 1 (CLIP is L2-normalized).

**Key concepts to internalize**:
- **CLIP embeddings are L2-normalized**. Therefore cosine similarity == dot product. Qdrant's `Cosine` distance is what you want.
- **Batching on CPU: 5× throughput at batch=32**. Don't loop one image at a time. PyTorch's overhead per `.forward()` is larger than the actual compute on small inputs.
- **`torch.inference_mode()` (not just `no_grad()`)** in the encoder — disables autograd more aggressively, ~5% faster on CPU.
- **Cache the embedding, not the image**: a 224×224 RGB image is ~150 KB; a 512-d float32 vector is 2 KB. The cache table stays small even at 1 M products.
- **Failure isolation**: one bad image must not crash the batch. Drop it from the batch, log with `image_url` + reason, continue.

**Done when**: feed it 100 real Awin products → ≥95% succeed, embedding cache populated, re-running the same 100 takes <1 s (full cache hit).

---

### P1-T5 — Qdrant collection setup + upsert

| | |
|---|---|
| **Goal** | Qdrant collection created with the right schema; products from P1-T4 land there with the minimum payload from arch §3.3. |
| **Effort** | 1.5 h |
| **Model** | **Sonnet 4.6** |
| **Prerequisites** | P1-T4 |

**Deliverables**:
- `scripts/init_qdrant.py`: idempotent creation of the `products` collection. Vector size 512, distance Cosine, HNSW index with `m=16`, `ef_construct=128`. Payload indexes on `category` (keyword) and `retailer` (keyword) — these are mandatory for filter-then-search to be fast.
- `app/services/qdrant_writer.py`: `upsert_products(products: list[ProductWithEmbedding])` — batches of 100, uses `(retailer, retailer_product_id)` to derive a deterministic UUID (UUID5 from a namespace + the key) for the Qdrant point ID so re-upserts overwrite, not duplicate.
- Payload kept minimal per arch §3.3: `product_id` (Postgres UUID), `category`, `retailer`, `image_url`. Nothing else.

**Key concepts**:
- **HNSW parameters**: `m` is the number of bidirectional links per node (16 is the standard). `ef_construct` controls index build quality (higher = slower build, better recall). Defaults are fine until you have >1M vectors.
- **Payload indexes are MANDATORY for filter+ANN**. Without an index on `category`, Qdrant does a brute filter scan = order-of-magnitude slower at scale. The architecture doc §1 specifically calls this out (Qdrant filter+ANN is one reason it was chosen over pgvector).
- **Deterministic point IDs via UUID5**: lets you upsert idempotently without first querying. `uuid.uuid5(NAMESPACE_PRODUCTS, f"{retailer}:{retailer_product_id}")`.

**Done when**: 100 products in Postgres + 100 points in Qdrant; re-running ingestion adds 0 new rows + 0 new points (idempotent).

---

### P1-T6 — End-to-end Awin ingestion command + audit log

| | |
|---|---|
| **Goal** | `python scripts/ingest_awin.py --feed-id N` runs the full pipeline, writes one row to `indexing_runs`, exits cleanly. |
| **Effort** | 1.5 h |
| **Model** | **Sonnet 4.6** |
| **Prerequisites** | P1-T5 |

**Deliverables**:
- `scripts/ingest_awin.py`: orchestrates fetch → normalize → embed → upsert. Wraps in a transaction-ish flow: failures don't leave the system half-updated.
- Writes an `indexing_runs` row with counts and any error sample.
- Click-tracking: emit a structured log per stage with timings.

**Key concepts**:
- **Resumability**: track per-source `last_indexed_at`. Next run starts from there. The Awin CSV is a full snapshot, so "resume" here means "skip products whose `updated_at` in Postgres matches what's in the CSV" (cheap hash compare).
- **Run audit as first-class state**: when ingestion silently degrades from 95% success rate to 60%, the `indexing_runs` table is the first thing you query. Builds the muscle for thinking of pipelines as observable systems, not scripts.

**Done when**: run completes against a real Awin feed in <30 min for ~10k products; `indexing_runs` row shows status=success; spot-check 5 products manually in Qdrant via the Qdrant Cloud UI.

**End of Phase 1**: you have a queryable product catalog. Worth a celebratory commit. Tag `v0.1.0-catalog`.

---

## 5. Phase 2 — Inference pipeline (Week 3, ~15 h)

**Goal**: a `services/inference.py` module that takes a PIL image, returns `[{box, category, crop_embedding}]`, batched correctly.

This phase pairs YOLO + CLIP for the user-facing path. Most of P1-T4's CLIP work is reused, so this phase moves faster than Phase 1.

### P2-T1 — YOLO integration + crop extraction

| | |
|---|---|
| **Goal** | Detect objects in a user image, return clean crops + category mapping. |
| **Effort** | 3 h |
| **Model** | **Opus 4.7** |
| **Prerequisites** | P1-T4 (CLIPEmbedder reusable), P1-T2 (taxonomy) |

**Why Opus**: same logic as P1-T4. Cropping coordinates from YOLO outputs is one of the top three "silent bug" zones in CV code (off-by-one on tensor vs numpy convention, normalized vs absolute coords, BGR vs RGB). Worth the cost to get right first time.

**Deliverables**:
- `app/services/detection.py`:
  - `class YOLODetector` loads YOLOv8n (the nano variant — 6 MB, fast enough on CPU) from Ultralytics at startup.
  - `detect(image: PIL.Image, conf_threshold: float = 0.5) → list[Detection]` where `Detection` is `(box, coco_class, confidence)`.
  - `extract_crops(image: PIL.Image, detections: list[Detection], margin: float = 0.05) → list[PIL.Image]` — pads boxes by 5% before crop (CLIP does better with a bit of context around the object).
- `app/services/inference.py`:
  - `async def analyze_image(image: PIL.Image) → list[ObjectMatch]` where `ObjectMatch = {box, category, crop_embedding}`. Maps YOLO COCO class → your taxonomy via `taxonomy.py`. Skips unmapped classes.
  - Fallback: zero detections → return one entry with `category=None, crop_embedding=embedding-of-full-image`.

**Key concepts**:
- **YOLOv8 vs YOLOv5 vs YOLO-World**: v8n is sweet spot for CPU MVP. YOLO-World is open-vocab (you can prompt it with category names) — tempting but 5× slower and accuracy varies. Stick with v8n until you have data showing where it fails.
- **Confidence threshold of 0.5–0.6** (arch §13 open question): 0.4 catches noise (especially indoor clutter), 0.7 misses faded background items. Start 0.5, tune from real user data.
- **Crop margin**: YOLO boxes are tight to the object. CLIP benefits from a bit of surrounding context. 5% pad is a sweet spot — too much pad and the embedding starts capturing background.
- **No-detection fallback**: arch §4.3 says embed the full image and search without category filter. This is the largest single quality-quality tradeoff in the system. Implement it but track the rate (`no_detection_count` metric) — if it's >25% your YOLO threshold is too high.

**Done when**: feed 10 real Pinterest screenshots → median 3–5 detections per image, mostly correct categories by manual inspection.

---

### P2-T2 — Batched CLIP encode (multi-crop, multi-request)

| | |
|---|---|
| **Goal** | The "request batching window" from arch §2.2 — accumulate crops from concurrent requests into one CLIP forward pass when load is high. |
| **Effort** | 3 h |
| **Model** | **Opus 4.7** |
| **Prerequisites** | P2-T1 |

**Why Opus**: concurrent batching with a time window is hard to get right. Race conditions, fairness (later request waits 50 ms even when batch isn't full), thread safety, backpressure. This is exactly the "lurking complexity" zone where Opus's reasoning earns its keep.

**Deliverables**:
- `app/services/batcher.py`:
  - `class BatchingCLIPEncoder` — wraps `CLIPEmbedder`.
  - `async def encode(crops: list[PIL.Image]) → np.ndarray` — accumulates into a shared buffer, flushes when (a) 8 crops queued OR (b) 50 ms elapsed since first crop. Returns the slice of results corresponding to this caller's crops.
  - Implemented with `asyncio.Event` + an asyncio task that runs the batched forward pass.
- `app/services/inference.py` updated to use the batcher.
- `tests/services/test_batcher.py`: spin up 10 concurrent `encode()` calls each with 2 crops → verify a single forward pass handled all 20 crops (assert via spy on the underlying `CLIPEmbedder`).

**Key concepts**:
- **The asyncio batcher pattern** (also called "micro-batching"): one consumer task loops on a queue; producers put items + a future, consumer batches them, runs once, resolves futures. Pattern is reusable for any expensive async-bridged sync operation.
- **Fairness vs throughput tradeoff**: longer window = more batching = lower per-request latency-floor but higher tail. 50 ms is a calibration. Make it configurable.
- **When NOT to use this**: at MVP load (<100 req/day) you'll never have concurrent requests. The batcher gracefully degrades to "flush immediately when window starts and there's one crop." Worth implementing now because retrofitting it is painful.

**Done when**: test passes; manual smoke with 1 sequential request still works in <300 ms total for inference stage.

---

### P2-T3 — Image preprocessing + upload validation

| | |
|---|---|
| **Goal** | The 50 ms preprocessing stage from arch §4.1: resize to ≤1024 px, strip EXIF, validate magic bytes, reject too-small/animated/malformed. |
| **Effort** | 2 h |
| **Model** | **Sonnet 4.6** |
| **Prerequisites** | none (parallelizable with P2-T1/T2) |

**Deliverables**:
- `app/services/preprocessing.py`:
  - `validate_upload(data: bytes) → PIL.Image` — checks magic bytes via `python-magic`, max size 5 MB (arch §13 suggests 5 over 10), min dims 128×128, format ∈ {JPEG, PNG, WebP}.
  - `prepare_for_inference(image: PIL.Image) → PIL.Image` — `ImageOps.exif_transpose` to respect orientation, strip EXIF, resize to max edge 1024 with LANCZOS.
- `tests/services/test_preprocessing.py`: cases for too-small, too-big, animated GIF, EXIF-rotated phone photo, valid JPEG.

**Key concepts**:
- **EXIF orientation**: phone photos often have an orientation tag instead of being physically rotated. `ImageOps.exif_transpose` rotates the pixel data and clears the tag → downstream code never needs to handle rotation.
- **EXIF stripping is GDPR-relevant** (arch §10.1): EXIF can carry GPS coordinates. Strip before storage, always.
- **Magic bytes over file extension**: a file called `cute.jpg` can contain anything. `python-magic` reads the actual leading bytes. Cheap, correct.

**Done when**: tests pass; pass a phone photo with GPS EXIF → output has no EXIF (verify with `exiftool` on the saved file).

---

### P2-T4 — Inference timing instrumentation

| | |
|---|---|
| **Goal** | Every stage (upload, preprocess, yolo, clip, total) timed and logged with the request_id so you can validate the §4.1 latency budget. |
| **Effort** | 1 h |
| **Model** | **Haiku 4.5** |
| **Prerequisites** | P2-T1, P2-T2, P2-T3 |

**Deliverables**:
- A `@timed_stage("stage_name")` async context manager that logs `{stage, duration_ms, request_id}` and (later, P6) increments a Prometheus histogram.
- Wrap each of: validation, preprocess, detect, embed, total.

**Done when**: one analyze call produces 5 timing log lines in correct order, summing to ≈ wall clock.

---

## 6. Phase 3 — Search Service (Week 4 first half, ~8 h)

**Goal**: given a list of `(category, embedding)` pairs, return ranked product matches with diversity + fallbacks.

### P3-T1 — Filtered ANN search wrapper

| | |
|---|---|
| **Goal** | One Qdrant call returns N×K matches (N objects, K candidates each), correctly filtered by category. |
| **Effort** | 2 h |
| **Model** | **Sonnet 4.6** |
| **Prerequisites** | P1-T5, P2-T1 |

**Deliverables**:
- `app/services/search.py`:
  - `async def search_for_objects(objects: list[ObjectMatch], k_per_object: int = 20) → list[list[SearchHit]]` — uses `qdrant_client.search_batch()` to do one HTTP call for all N searches.
  - Each search has a `Filter(must=[FieldCondition(key="category", match=MatchValue(value=obj.category))])`. Skip filter for `category=None` (no-detection fallback).
  - Drop hits below score threshold 0.5 (configurable, arch §2.3).

**Key concepts**:
- **`search_batch` is the killer feature**: arch §4.2 says one HTTP call for N queries. `qdrant-client` exposes this directly. Don't loop `search()`.
- **Score threshold of 0.5** is empirical: CLIP cosine similarities for visually-similar deco items tend to land 0.7-0.95 for good matches, 0.4-0.6 for "kind of related," <0.4 for unrelated. Below 0.5 = noise.
- **Why `k_per_object: 20` not 5**: you'll rerank+dedupe down to 5 at the next stage. Starting with 20 gives the reranker room to do diversity work.

**Done when**: feed a known sofa-image embedding with category=couch → top 5 results are all visually couch-shaped products.

---

### P3-T2 — Diversity reranker

| | |
|---|---|
| **Goal** | Take the 20 candidates per object, return 5 with retailer diversity. |
| **Effort** | 2 h |
| **Model** | **Opus 4.7** |
| **Prerequisites** | P3-T1 |

**Why Opus**: ranking is your product. The 5 items the user sees are the entire UX. Cheap to get this wrong, expensive to detect (you'll just have lower CTR forever). Opus is meaningfully better at producing a reranking algorithm that balances multiple signals coherently. Sonnet tends to write "do A then B" sequential filters which produce bad outputs when signals trade off.

**Deliverables**:
- `app/services/ranking.py`:
  - `def rerank(hits: list[SearchHit], top_n: int = 5) → list[SearchHit]`. Default strategy: MMR (Maximal Marginal Relevance) with λ=0.7 over (cosine score, retailer-novelty). Result: high-scoring matches that aren't all from the same retailer.
  - Alternative simple strategy: round-robin by retailer, then sort by score within each retailer slot.
- Make the strategy pluggable via a string config — sets up A/B testing later (arch §9.3).

**Key concepts**:
- **MMR formula**: `score(item) = λ * relevance(item) - (1-λ) * max_similarity(item, already_selected)`. For "retailer diversity" the second term is `1` if same retailer as anything already picked, `0` otherwise. λ=0.7 means relevance dominates but diversity breaks ties.
- **Why this matters for revenue**: 5 results from Amazon = the user clicks the cheapest one. 5 from mixed retailers = price variance creates clicks. (Also: better optics — doesn't look like an Amazon-only shop.)
- **Pluggable strategy**: arch §2.3 — "isolating it makes A/B testing different ranking strategies straightforward." A `RankerProtocol` Protocol is enough; the strategy lives in a string config you swap.

**Done when**: same 20 candidates with 18 Amazon + 2 IKEA → reranker returns 5 with at least one IKEA in the mix.

---

### P3-T3 — Hydration (Qdrant → Redis → Postgres)

| | |
|---|---|
| **Goal** | Turn ranked Qdrant hits into full product objects with title, price, image, affiliate URL. |
| **Effort** | 2.5 h |
| **Model** | **Sonnet 4.6** |
| **Prerequisites** | P3-T2, P1-T1 |

**Deliverables**:
- `app/services/hydration.py`:
  - `async def hydrate(product_ids: list[UUID]) → dict[UUID, Product]`. Implements arch §3.3 pattern:
    1. Redis MGET on `product:{id}` keys (one round trip).
    2. Misses → batched Postgres `WHERE id = ANY(...)`.
    3. Backfill Redis with the misses (TTL 5 min per arch §6.2).
- `app/models/schemas.py`: `Product` Pydantic model — the public-facing shape.

**Key concepts**:
- **MGET, never N round-trips**: Redis is fast but the round trip is the cost. One `MGET` of 50 keys is ~1 ms; 50 individual `GET`s is ~50 ms.
- **`WHERE id = ANY(:ids)` with a list parameter**: better than building an `IN (...)` clause with string interpolation. asyncpg handles arrays natively.
- **Cache stampede**: when many requests miss the same key simultaneously, they all hit Postgres. For MVP scale, ignore. At 10k req/day, add a "request coalescing" pattern (in-memory `dict[key, Future]`).

**Done when**: hydrate 20 IDs end-to-end; cold call <50 ms, second call <5 ms (cache hit).

---

### P3-T4 — Search service fallbacks (arch §4.3)

| | |
|---|---|
| **Goal** | Every failure mode from the resilience table degrades gracefully. |
| **Effort** | 1.5 h |
| **Model** | **Sonnet 4.6** |
| **Prerequisites** | P3-T3 |

**Deliverables**:
- Wrap Qdrant calls with `asyncio.wait_for(timeout=1.5)`.
- On per-object Qdrant timeout: omit that object's matches, append a warning to the response.
- On hydration failure (cache + DB both down): return product IDs + image_urls from Qdrant payload with `partial: true` flag in the response.
- Tests using fault injection (mock the qdrant client to raise on second call).

**Key concepts**:
- **Partial success is a first-class response**. The user gets *something* instead of a 500. Arch §1.5 — "design for failure of every external dep."
- **Distinguishing transient vs permanent failures**: timeout = retry once; 503 = retry with backoff; ValidationError = don't retry, log+fail. Don't put generic retries everywhere — they amplify outages.

**Done when**: chaos test (kill Postgres mid-call) → user gets `partial: true` response with images, no 500.

---

## 7. Phase 4 — /analyze endpoint glue (Week 4 second half, ~10 h)

**Goal**: `POST /api/analyze` accepts an upload and returns the full result.

### P4-T1 — Endpoint + Pydantic schemas + R2 upload

| | |
|---|---|
| **Goal** | The endpoint exists, accepts multipart upload, stores the image in R2 with TTL 24 h, calls the pipeline, returns the response. |
| **Effort** | 3 h |
| **Model** | **Sonnet 4.6** |
| **Prerequisites** | P2 complete, P3 complete, P0-T3 (R2 connection) |

**Deliverables**:
- `app/models/schemas.py`: `AnalyzeRequest` (Pydantic, includes optional `idempotency_key`), `AnalyzeResponse` with nested `DetectedObject` → `Match` → `Product` types.
- `app/api/routes/analyze.py`: `POST /api/analyze`. Multipart `UploadFile`, returns `AnalyzeResponse`.
- R2 upload with key `uploads/{date}/{request_id}.jpg`. R2 bucket lifecycle rule deletes after 24 h (arch §10.1 — set via Cloudflare console, not app code).
- Errors: 400 (bad image), 413 (too big), 415 (unsupported type), 429 (rate-limited, later), 503 (CLIP down).

**Key concepts**:
- **R2 lifecycle rule belongs to the bucket, not the app**: arch §10.1 explicitly. App might crash before deleting; bucket rule guarantees deletion. Cheap correctness.
- **`UploadFile` reads lazily**: don't `await file.read()` then validate — validate magic bytes first by reading 512 bytes, then conditionally read the rest.

**Done when**: `curl -F image=@sofa.jpg http://localhost:8000/api/analyze` returns valid JSON with detections + matches.

---

### P4-T2 — Idempotency + analyze cache

| | |
|---|---|
| **Goal** | Same image uploaded twice within 1 h returns the same response without re-running CLIP. |
| **Effort** | 2 h |
| **Model** | **Sonnet 4.6** |
| **Prerequisites** | P4-T1 |

**Deliverables**:
- Compute `sha256(image_bytes)` before validation.
- Redis key `analyze:{hash}` → cached `AnalyzeResponse` JSON, TTL 1 h (arch §6.2).
- Cache hit: return immediately with the same `request_id` as the original (or new one + indicate `cached: true` — your UX call).

**Key concepts**:
- **Idempotency key over content hash**: arch §7.3 — content hash is the right key for an image upload. Client doesn't need to send anything explicit.
- **The cost saving is huge**: Pinterest screenshots get re-uploaded a lot. Even 20% hit rate cuts inference cost 20% (arch §6.2).

**Done when**: upload same JPEG twice, second response is <50 ms total (vs ~700 ms first).

---

### P4-T3 — Rate limiting

| | |
|---|---|
| **Goal** | Per-IP: 10/min on `/api/analyze`, 1000/h global. 429 with `Retry-After`. |
| **Effort** | 1.5 h |
| **Model** | **Sonnet 4.6** |
| **Prerequisites** | P4-T1 |

**Deliverables**:
- Use `slowapi` (mature, FastAPI-native) OR roll Redis `INCR`+`EXPIRE` (arch §7.1). For MVP, slowapi is fine.
- Real-IP detection behind Cloudflare: read `CF-Connecting-IP` header, fall back to `X-Forwarded-For`.

**Key concepts**:
- **Why `CF-Connecting-IP`**: when behind Cloudflare, `request.client.host` is Cloudflare's IP, not the user's. `X-Forwarded-For` works but is spoofable from outside CF. `CF-Connecting-IP` is set by CF after IP verification.

**Done when**: 11th request in a minute returns 429 with `Retry-After: 60`.

---

### P4-T4 — Affiliate URL construction in response

| | |
|---|---|
| **Goal** | Every match in the response has a clickable affiliate URL, not the raw retailer URL. |
| **Effort** | 1 h |
| **Model** | **Haiku 4.5** |
| **Prerequisites** | P4-T1, P1-T3 (Awin source has the builder) |

**Deliverables**:
- `app/services/affiliate.py`: thin facade calling the right `ProductSource.build_affiliate_url(...)` based on retailer.
- Built at response-time, not stored (arch §3.3 — "don't store stale URLs").

**Done when**: copy a URL from the response → click → land on retailer product page → your publisher dashboard registers the impression.

---

### P4-T5 — Smoke test against deployed Railway instance

| | |
|---|---|
| **Goal** | End-to-end test: real upload → real response → manual review of 5 image cases. |
| **Effort** | 2.5 h |
| **Model** | **Sonnet 4.6** |
| **Prerequisites** | P4-T1 through P4-T4 |

**Deliverables**:
- `tests/e2e/test_analyze.py` parametrized over `tests/fixtures/images/`: living-room.jpg, bedroom.jpg, just-a-sofa.jpg, ambiguous-pinterest.jpg, no-furniture.jpg. Assert structural correctness (response shape, status codes), not exact matches (matches will drift as catalog grows).
- Manual quality review: for each fixture, eyeball the matches and write findings in `docs/quality_notes_v0.md`.

**Done when**: tag `v0.4.0-mvp-functional`. The product *works*; everything from here is hardening.

---

## 8. Phase 5 — Affiliate, click tracking, price freshness (Week 5 first half, ~8 h)

### P5-T1 — Click tracking redirect endpoint

| | |
|---|---|
| **Goal** | `GET /r/{request_id}/{product_id}` logs a click then 302-redirects to the affiliate URL. |
| **Effort** | 2 h |
| **Model** | **Sonnet 4.6** |
| **Prerequisites** | P4-T4 |

**Deliverables**:
- `app/api/routes/click.py`.
- `clicks` table: `id`, `request_id`, `product_id`, `retailer`, `user_session_hash` (cookie-derived, no PII), `ip_hash`, `user_agent`, `timestamp`.
- Frontend uses these `/r/...` URLs, not direct affiliate URLs.

**Key concepts**:
- **Why route clicks through your server**: lets you (a) measure CTR per request, (b) detect affiliate URL drift before users complain, (c) inject more tracking later. The 302 redirect adds ~50 ms; users don't notice.
- **`user_session_hash`**: arch §2.5. Use an HMAC of a session cookie + a server secret. Lets you compute "this user viewed N results and clicked M" without storing PII.

**Done when**: click on a result → redirected to retailer → row appears in `clicks`.

---

### P5-T2 — Price freshness daemon

| | |
|---|---|
| **Goal** | Background task refreshes prices for the hot tier (top 10% by click count) every 6 h. |
| **Effort** | 3 h |
| **Model** | **Sonnet 4.6** |
| **Prerequisites** | P5-T1 |

**Deliverables**:
- `scripts/refresh_prices.py`: queries Postgres for products to refresh (hot tier first, then warm/cold per arch §5.7), re-fetches price from source, updates Postgres, invalidates Redis `product:{id}` keys.
- Runs as a Railway cron job (not in the API container).
- Updates Postgres + Redis, **never** Qdrant.

**Key concepts**:
- **Why Qdrant doesn't change**: vectors are derived from images, not prices. Updating Qdrant on every price change = pointless writes + index churn.
- **Hot tier definition**: top 10% by clicks-in-last-7-days. A simple SQL query against `clicks`. Tier membership re-computed on each daemon run.

**Done when**: change a price in source data → daemon run → response from `/api/analyze` reflects new price; `clicks` aggregation correctly identifies the hot tier.

---

### P5-T3 — Catalog full re-index cron

| | |
|---|---|
| **Goal** | Weekly Railway cron: re-run Awin ingestion to catch new products + remove stale ones. |
| **Effort** | 1.5 h |
| **Model** | **Haiku 4.5** |
| **Prerequisites** | P1-T6 |

**Deliverables**:
- Railway cron (`0 3 * * 0`, Sunday 3 AM UTC): runs `scripts/ingest_awin.py`.
- Stale-product cleanup: products in Postgres but not in latest feed → mark `deleted_at`, remove from Qdrant + Redis.

**Done when**: cron registered; manual trigger works; stale-product simulation correctly removes points from Qdrant.

---

### P5-T4 — GDPR endpoints

| | |
|---|---|
| **Goal** | Right-to-erasure by `request_id`; privacy policy stub. |
| **Effort** | 1.5 h |
| **Model** | **Sonnet 4.6** |
| **Prerequisites** | P4-T1 |

**Deliverables**:
- `DELETE /api/requests/{request_id}`: removes the R2 object (if still present), removes any `clicks` rows for that request_id. Returns 204.
- `docs/privacy_policy.md`: per arch §10.1 must mention image upload, automated processing, 24 h retention, third parties (Qdrant, R2, Railway).

**Done when**: endpoint works; privacy doc reviewed once.

---

## 9. Phase 6 — Observability (Week 5 second half, ~6 h)

Arch §1.7 — "observability from day 1." You have structured logs already (P0-T2). Now add metrics + error tracking + business events.

### P6-T1 — Prometheus /metrics endpoint

| | |
|---|---|
| **Goal** | `prometheus_client` exposing the key metrics from arch §8.2. |
| **Effort** | 2 h |
| **Model** | **Sonnet 4.6** |
| **Prerequisites** | P0-T2 |

**Deliverables**:
- HTTP histograms: `http_request_duration_seconds{method, endpoint, status}`.
- ML histograms: `inference_duration_seconds{stage}` — buckets tuned to your latency budget.
- Counters: `qdrant_queries_total`, `cache_hits_total{layer}`, `affiliate_clicks_total{retailer}`.
- Gauges: `embedding_cache_hit_ratio`, `qdrant_top1_score_p50` (the sneaky quality canary from arch §8.2).
- `/metrics` endpoint, scrape-friendly.

**Key concepts**:
- **The top-1 score gauge is the catalog-drift early warning**: arch §8.2. If it trends down over weeks, your catalog isn't matching real user uploads anymore — quality is degrading silently.
- **Buckets in histograms matter**: defaults are `[5ms, 10ms, 25ms, 50ms, 100ms, 250ms, 500ms, 1s, 2.5s, 5s, 10s]`. For inference stages, custom buckets centered on your budget give actionable p95s.

**Done when**: hit `/metrics`, see real numbers; pipe into Grafana Cloud free tier or Railway's metrics view.

---

### P6-T2 — Sentry + error tagging

| | |
|---|---|
| **Goal** | Unhandled exceptions land in Sentry, tagged with `request_id`, `image_hash` (when applicable), `detected_objects`. |
| **Effort** | 1 h |
| **Model** | **Haiku 4.5** |
| **Prerequisites** | P0-T2 |

**Done when**: deliberately raise from `/analyze` → see issue in Sentry with all expected tags.

---

### P6-T3 — Business events stream (Postgres `events` table)

| | |
|---|---|
| **Goal** | Append-only `events` table capturing `analyze_completed`, `match_shown`, `affiliate_clicked`, `category_detected` (arch §8.5). |
| **Effort** | 2 h |
| **Model** | **Sonnet 4.6** |
| **Prerequisites** | P5-T1, P4-T1 |

**Deliverables**:
- `events` table with `(id, type, payload jsonb, request_id, timestamp)`.
- Helper `emit_event(type, payload, request_id)` called from the right spots in analyze and click flows.
- `sql/analytics/` directory with a few pre-written queries: daily analyze count, CTR per category, no-match rate.

**Key concepts**:
- **Why a separate event stream from logs**: logs answer "what happened during this request"; events answer "is the product getting better." Different consumers (you reading Loki vs you running BI queries). Different cadence (every 100ms vs every week). Don't mix them in the same store.
- **Append-only is a feature**: no `UPDATE events SET ...` ever. This is what makes ClickHouse migration (arch §3.1, `[SCALE]`) trivial later.

**Done when**: one analyze + one click produces 4 events; ad-hoc `psql` query for "CTR per detected category" returns sensible numbers.

---

### P6-T4 — Minimal dashboard

| | |
|---|---|
| **Goal** | One Grafana dashboard (or Railway's built-in) showing the five metrics that actually matter daily. |
| **Effort** | 1 h |
| **Model** | **Haiku 4.5** (dashboard JSON generation) |
| **Prerequisites** | P6-T1 |

**Panels**: req/min, p95 latency, error rate, top-1 score p50 trend, daily affiliate clicks. Five panels. That's it. Resist adding a sixth.

**Done when**: bookmark the dashboard, check it once a day.

---

## 10. Phase 7 — Minimal frontend (Week 6, ~12 h)

You can't ship without a UI. Keep it minimal — the backend is the product.

### P7-T1 — Stack choice + scaffold

| | |
|---|---|
| **Goal** | A frontend that's a single SPA, deployable to Cloudflare Pages, talking to your Railway API. |
| **Effort** | 1.5 h |
| **Model** | **Haiku 4.5** for scaffold |
| **Prerequisites** | none |

**Recommendation**: SvelteKit (or Next.js if you're React-comfortable). Skip the framework debate — pick what you can build fastest. Cloudflare Pages auto-deploys from GitHub.

**Done when**: `https://your-app.pages.dev/` serves an empty homepage.

---

### P7-T2 — Upload + results UI

| | |
|---|---|
| **Goal** | Drag-and-drop upload, loading state, results grid (image with bounding-box overlay + match thumbnails per object). |
| **Effort** | 6 h |
| **Model** | **Sonnet 4.6**, with **Opus 4.7** for the bounding-box overlay component if you find it fiddly |
| **Prerequisites** | P7-T1, P4 complete |

**Key concepts**:
- **Bounding-box overlay** is the visually-clarifying detail that makes the product feel magical: an SVG layer over the user's image with each detected object boxed and labeled, hover → highlights matching products below.
- **Affiliate disclosure** (arch §10.2): "Liens affiliés / Article contenant des liens commerciaux" must appear on the results page. ARPP + DGCCRF. Non-optional in France.

**Done when**: upload a Pinterest screenshot → see boxes + matches on one screen.

---

### P7-T3 — Privacy policy + GDPR deletion form

| | |
|---|---|
| **Goal** | Static `/privacy` page with policy + a simple form for users to request deletion by `request_id`. |
| **Effort** | 2 h |
| **Model** | **Sonnet 4.6** |
| **Prerequisites** | P5-T4 |

**Done when**: form posts to `DELETE /api/requests/{id}`; visible link in footer of every page.

---

### P7-T4 — Frontend telemetry (impression beacons)

| | |
|---|---|
| **Goal** | When results render, emit a beacon to `/api/events/impression` so you can compute CTR. |
| **Effort** | 1.5 h |
| **Model** | **Sonnet 4.6** |
| **Prerequisites** | P6-T3, P7-T2 |

**Deliverables**:
- Browser `navigator.sendBeacon` on results render → `POST /api/events/impression {request_id, shown_product_ids}`.
- Backend writes to `events`.

**Done when**: `impressions / clicks` ratio is computable from `events`.

---

### P7-T5 — Pre-launch UI polish + mobile responsiveness

| | |
|---|---|
| **Goal** | Looks decent on a phone (most Pinterest users are mobile). |
| **Effort** | 1 h |
| **Model** | **Haiku 4.5** (CSS tweaks) |
| **Prerequisites** | P7-T2 |

**Done when**: opened on your phone, it doesn't look broken.

---

## 11. Phase 8 — Hardening & soft launch (Week 7-8, ~10 h)

### P8-T1 — Load test

| | |
|---|---|
| **Goal** | Confirm p95 < 2 s under 5 concurrent users sustained 10 min. |
| **Effort** | 2 h |
| **Model** | **Sonnet 4.6** |
| **Prerequisites** | P6 complete |

**Tool**: `locust` is simplest. Pre-stage 20 real Pinterest screenshots, randomize across them.

**Done when**: p95 reported by locust < 2 s; CPU under 80% on the Railway instance.

---

### P8-T2 — Security review pass

| | |
|---|---|
| **Goal** | Walk arch §10 checklist, fix any gaps. |
| **Effort** | 2 h |
| **Model** | **Opus 4.7** |
| **Prerequisites** | everything |

**Why Opus**: security is a domain where missing one thing matters a lot. Opus is better at producing exhaustive checklists and noticing the things that aren't there.

**Checklist** (mirrors arch §10.3): HTTPS everywhere ✓ (CF default), CORS strict allowlist, CSP header on frontend, secrets in Railway env not git, DB credentials documented for rotation, no `print()` of secrets in logs.

**Done when**: walked the checklist; opened one Sentry-issue-class-of-thing per gap and fixed.

---

### P8-T3 — Documentation pass

| | |
|---|---|
| **Goal** | A README that another developer (or future-you) can use to bootstrap from zero. |
| **Effort** | 2 h |
| **Model** | **Sonnet 4.6** |
| **Prerequisites** | everything |

**Sections**: overview, prerequisites, local dev setup, env vars, common commands (ingestion, refresh, tests), deployment, troubleshooting (the 5 things you got bitten by during development).

---

### P8-T4 — Soft launch — 10 users

| | |
|---|---|
| **Goal** | 10 friends use it for a week, you watch the dashboards and event stream. |
| **Effort** | 2 h setup + ongoing watching |
| **Model** | none — this is human work |

**What to watch**: error rate, no-match rate, top-1 score distribution, CTR, qualitative feedback in a Notion doc.

**Done when**: at least 100 analyze calls + 10 affiliate clicks logged; you have a list of the top 5 quality issues to fix in V1.

---

### P8-T5 — Decision point — V1 prioritization

| | |
|---|---|
| **Goal** | Based on real usage data, pick the 3 things to do in V1. |
| **Effort** | 2 h |
| **Model** | **Opus 4.7** for synthesis + recommendation pass over your event logs |

The V1 candidates from arch (sidecar inference, Prefect orchestration, async response, A/B test framework, fine-tuning dataset, quality feedback loop) — most teams pick the wrong one without data. Wait for data, then choose.

**Done when**: a single-page "V1 plan" written. Tag `v1.0.0-mvp-launched`.

---

## 12. Cross-cutting practices

### 12.1 What NOT to delegate to agents

Pattern that loses badly when handed to an agent:

- **Choosing dependencies.** Agents will helpfully add libraries that solve the immediate problem and complicate everything else. You decide deps. Constraint in your prompts: "no new top-level dependencies without justifying."
- **Database migrations.** Generated migrations on a real Postgres can drop columns or rewrite types. Always read the generated Alembic migration line-by-line before applying.
- **Affiliate URL templates.** One wrong character in a tracking param = months of unattributed revenue. Test each retailer manually.
- **The taxonomy.** Once products are categorized one way and ranked accordingly, changing the taxonomy is expensive. You own the taxonomy.
- **CSP and CORS rules.** Wrong → either you have an open security hole or your frontend can't talk to your backend. Hand-write these.

### 12.2 Commit hygiene with agent assistance

A useful pattern: have the agent produce a `WIP` commit when it claims to be done, then YOU review and squash with a real message before pushing. The Haiku-grade "summarize this diff" prompt is dirt cheap and surprisingly good as a starting point for the message; the squashing forces you to read the diff.

### 12.3 Testing tier

Don't aim for 90% coverage. Aim for:
- **Critical paths**: 100% coverage on `analyze` end-to-end, embedding correctness, idempotency, rate limiting.
- **Pure functions**: 100% coverage (cheap, high signal).
- **Adapters** (Qdrant, Redis, R2 wrappers): integration tests only, hit real services in CI via docker-compose.
- **UI**: smoke tests of upload-and-see-results, no more.

### 12.4 Cost monitoring from day 1

You'll burn money on three things: Railway compute, Qdrant Cloud, Cloudflare R2 (mostly free thanks to no egress fees). Plus your agent-assisted dev costs.

- **Set Railway and Qdrant Cloud spending alerts at €20/mo** during MVP. If you blow past it, something's wrong.
- **Agent cost rule of thumb**: a full Phase 0 cycle through Claude Code in Sonnet 4.6 should cost <€5 in API spend. If you find yourself spending €10+ per phase, you're probably re-prompting too much — slow down and write better prompts upfront.

---

## 13. Tooling recommendations

### 13.1 Claude Code as primary dev surface

Claude Code (the terminal-based agentic coding tool) is the right primary surface for this project. Configure it with:
- Default model: `claude-sonnet-4-6`.
- `/model claude-opus-4-7` when you hit a task tagged Opus in this roadmap.
- `/model claude-haiku-4-5` for the Haiku-tagged tasks.

Switching models mid-session is one keystroke and the cost difference per session is significant.

### 13.2 Other Claude products that may help

- **Claude in Chrome** (beta): useful for navigating affiliate dashboards, downloading Awin feeds, exploring competitor sites. Low priority but mention-worthy.
- **Cowork**: not directly relevant to coding but useful for the operational sides — managing tracking spreadsheets, organizing the V1 backlog.
- **Claude in Excel** (beta): if you end up doing CTR-per-category analysis in spreadsheets, useful. Probably not needed at MVP.

### 13.3 Non-Anthropic tooling worth installing

- **`uv`** for Python deps — 10–100× faster than pip. Pairs with Sonnet-generated `pyproject.toml`.
- **`ruff`** for lint + format. One tool. Fast.
- **`pre-commit`** hooks running ruff + mypy before each commit.
- **`httpie`** or `curl` aliases for hitting `/analyze` from the terminal during development.
- **Qdrant Cloud dashboard** — UI for inspecting your collection. Bookmark it.

---

## 14. Per-task model summary table

For copy-paste into agent system prompts or to keep beside you while working:

| Task ID | Title | Effort | Model |
|---|---|---|---|
| P0-T1 | Repo scaffolding | 1.5 h | Haiku 4.5 |
| P0-T2 | Config + logging | 2 h | Sonnet 4.6 |
| P0-T3 | DB/cache connections | 2.5 h | Sonnet 4.6 |
| P0-T4 | Railway deploy + CI | 2 h | Haiku 4.5 (+ Sonnet) |
| P1-T1 | Postgres schema | 2 h | Sonnet 4.6 |
| P1-T2 | Taxonomy YAML + loader | 3 h | **Opus 4.7** (design) + Sonnet (code) |
| P1-T3 | Awin ProductSource | 3 h | Sonnet 4.6 |
| P1-T4 | Embedding job | 4 h | **Opus 4.7** |
| P1-T5 | Qdrant setup + upsert | 1.5 h | Sonnet 4.6 |
| P1-T6 | E2E ingestion command | 1.5 h | Sonnet 4.6 |
| P2-T1 | YOLO + crops | 3 h | **Opus 4.7** |
| P2-T2 | Batched CLIP encode | 3 h | **Opus 4.7** |
| P2-T3 | Preprocessing + validation | 2 h | Sonnet 4.6 |
| P2-T4 | Timing instrumentation | 1 h | Haiku 4.5 |
| P3-T1 | Filtered ANN search | 2 h | Sonnet 4.6 |
| P3-T2 | Diversity reranker | 2 h | **Opus 4.7** |
| P3-T3 | Hydration | 2.5 h | Sonnet 4.6 |
| P3-T4 | Search fallbacks | 1.5 h | Sonnet 4.6 |
| P4-T1 | /analyze + R2 upload | 3 h | Sonnet 4.6 |
| P4-T2 | Idempotency cache | 2 h | Sonnet 4.6 |
| P4-T3 | Rate limiting | 1.5 h | Sonnet 4.6 |
| P4-T4 | Affiliate URLs in response | 1 h | Haiku 4.5 |
| P4-T5 | Smoke test | 2.5 h | Sonnet 4.6 |
| P5-T1 | Click tracking redirect | 2 h | Sonnet 4.6 |
| P5-T2 | Price freshness daemon | 3 h | Sonnet 4.6 |
| P5-T3 | Catalog re-index cron | 1.5 h | Haiku 4.5 |
| P5-T4 | GDPR endpoints | 1.5 h | Sonnet 4.6 |
| P6-T1 | /metrics endpoint | 2 h | Sonnet 4.6 |
| P6-T2 | Sentry | 1 h | Haiku 4.5 |
| P6-T3 | Events table | 2 h | Sonnet 4.6 |
| P6-T4 | Minimal dashboard | 1 h | Haiku 4.5 |
| P7-T1 | Frontend scaffold | 1.5 h | Haiku 4.5 |
| P7-T2 | Upload + results UI | 6 h | Sonnet 4.6 (+ Opus for overlay) |
| P7-T3 | Privacy + GDPR form | 2 h | Sonnet 4.6 |
| P7-T4 | Impression beacons | 1.5 h | Sonnet 4.6 |
| P7-T5 | Mobile polish | 1 h | Haiku 4.5 |
| P8-T1 | Load test | 2 h | Sonnet 4.6 |
| P8-T2 | Security review | 2 h | **Opus 4.7** |
| P8-T3 | Documentation | 2 h | Sonnet 4.6 |
| P8-T4 | Soft launch | 2 h | (human) |
| P8-T5 | V1 prioritization | 2 h | **Opus 4.7** |

**Totals by model**:
- Opus 4.7: 7 tasks, ~18 h (~17% of total)
- Sonnet 4.6: 25 tasks, ~62 h (~58%)
- Haiku 4.5: 9 tasks, ~14 h (~13%)
- Human-only: 2 h

This routing is close to the "sweet spot" cost-routing curve described in the Claude pricing analyses — Opus where it earns its keep, Haiku where speed matters, Sonnet by default. Expect total API spend across the whole MVP development to land in the €40-80 range if you don't loop-prompt excessively.

---

## 15. Open questions tracked from arch §13

Before writing code at each phase, resolve the relevant open question:

| Phase | Question | Resolve before |
|---|---|---|
| P2 | Upload size cap | P4-T1 — settle at 5 MB |
| P2 | YOLO confidence threshold | P2-T1 — start 0.5, plan to tune from data |
| P3 | Matches per object | P3-T1 — settle at 5 |
| P3 | No-detection fallback | P3-T1 — yes, label "matches généraux" |
| P3 | Multiple objects same category | P3-T2 — return top-N per object, dedupe products across objects (no two of the same product across objects) |
| P5 | Catalog refresh cadence | P5-T3 — weekly full + daily price refresh on hot tier |
| P1 | Multi-retailer dedup | P1-T6 — defer to V1 per arch §5.4 |

---

## End

Build in order. Don't skip P0. Re-read arch §11.1 ("what NOT to do at MVP") any time you feel like adding scope.

When done with MVP, this roadmap dies — replaced by a V1 roadmap driven by your event stream, not your assumptions.
