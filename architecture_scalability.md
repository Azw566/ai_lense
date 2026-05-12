# Architecture Scalability Design — Visual Deco Search

> Companion to `spec_technique_deco_visuel.md` — Version 1.0
> Audience: future development agents and the project owner
> Scope: scalable architecture from MVP to ~100k req/day, with clear evolution thresholds

---

## 0. How to read this document

This doc **does not replace** the technical spec; it layers a scalability concern on top.
Every section follows the same pattern:
- **What** the component does
- **Why** this design (rationale + alternatives considered)
- **MVP** version (cheap, simple, deployable in week 1)
- **SCALE** evolution (when traffic justifies it)

Tags inline:
- `[MVP]` — implement now
- `[V1]` — within 1–3 months
- `[SCALE]` — only when traffic justifies it; explicit threshold given

---

## 1. Architecture Principles

These are the non-negotiables. Every later decision derives from one of them.

1. **Read path ≠ write path.** User analysis (latency-critical, idempotent reads) and catalog ingestion (throughput-oriented, slow writes) are different systems sharing a few stores. Coupling them is the most common scaling mistake.
2. **Stateless API, stateful workers.** API instances must be disposable (kill any one, traffic continues). State lives in DB, queue, cache, object storage.
3. **Inference is not the API.** ML model serving has a memory/compute profile that doesn't match HTTP request handling. Boundary must be designed early, even if collocated initially.
4. **Two products databases on purpose.** Postgres = source of truth (transactional, full history). Qdrant = derived index (rebuildable from Postgres). Never invert the relationship.
5. **Design for failure of every external dep.** Amazon API rate-limits. R2 returns 503. Qdrant has a slow query. Each must degrade gracefully, not crash the request.
6. **Cache aggressively, invalidate carefully.** Especially for affiliate prices (Amazon TOS: 24h max staleness on displayed prices).
7. **Observability from day 1.** Structured logs + a few key metrics, even if no dashboard exists yet. Adding observability after the fact is 10× harder.
8. **Cost-aware.** GPU only when needed. Spot/serverless for bursty workloads. Free tiers as long as possible.

---

## 2. Service Decomposition (Logical)

Even if you deploy this as a **single FastAPI application** initially, design the internal module boundaries as if they were separate services. This makes later extraction painless.

### 2.1 API Gateway — the thin layer

- **Role:** HTTP routing, request validation (Pydantic), auth (V2), rate limiting, request ID generation, response shaping.
- **State:** none. Stateless workers, horizontally scalable behind a load balancer.
- **Critical:** holds **no model weights, no heavy state**. Restart in <1s.
- **Calls:** Inference Service (in-process at MVP, RPC later), Search Service, Affiliate Service.

### 2.2 Inference Service

- **Role:** runs YOLO + CLIP on user-submitted images.
- **Why isolated:** scales on a completely different axis than HTTP API.
  - Memory: ~1.5–2GB per worker (CLIP weights + YOLO weights + PyTorch overhead).
  - Compute: CPU-bound at MVP, GPU at scale. Each request is "lumpy" (100–500ms blocking).
  - Throughput: benefits massively from **batching** (multiple crops in one CLIP forward pass).
- **MVP:** Embedded in FastAPI workers. Models loaded once at startup (lifespan event). Simplest.
- **V1:** Sidecar inference container, called via localhost HTTP. Independent restart, easier model swap.
- **SCALE:** Managed GPU inference (Modal, Replicate, Banana) or self-hosted (Triton, BentoML, Ray Serve). Trigger: p95 latency on CLIP > 200ms or memory pressure on API workers.

**Key pattern — request batching window.** Even for one-image-at-a-time requests, batch the *multiple crops* from a single image into one CLIP forward pass. Bigger win: when traffic is high, accumulate crops from multiple concurrent requests (50ms window or 8 crops, whichever first) → 3–5× throughput improvement on CPU.

### 2.3 Search Service

- **Role:** query orchestration around Qdrant. This is where **matching business logic** lives.
- **Responsibilities:**
  - Pre-filter by detected category (mandatory per spec §10 — prevents canapé matching to lampe).
  - Diversity reranking (avoid 5 results from same retailer; prefer mixed brands).
  - Score thresholding (don't return matches below ~0.5).
  - Fallback strategies (no match in primary category → try parent category → full-catalog search with warning).
- **Why a separate concern:** matching quality is your product differentiator. Isolating it makes A/B testing different ranking strategies straightforward.

### 2.4 Catalog Service

- **Role:** ingest, normalize, embed, and index products from affiliate sources.
- **Pattern:** completely async, batch-oriented, queue-driven.
- **No HTTP-facing endpoint at MVP.** Triggered by cron or workflow orchestrator.
- See §5 for the full pipeline architecture.

### 2.5 Affiliate Service

- **Role:** build affiliate URLs (per-retailer logic), track clicks, monitor price freshness.
- **Why isolated:** affiliate logic is retailer-specific (different URL parameters, tag formats, redirect chains). Clean isolation lets you add retailers without touching matching code.
- **Tracking:** each affiliate click logged with `(request_id, product_id, retailer, user_session_hash, timestamp)` → revenue attribution + future analytics.
- **Compliance:** owns the 24h price freshness daemon (see §5.7).

### 2.6 Quality Service `[V1+]`

- **Role:** collect feedback signals, store for analysis, eventually feed retraining.
- **Sources:** implicit (clicks, dwell time, scroll depth on results), explicit (thumbs up/down `[V2]`).
- **Storage:** append-only event log → eventually a fine-tuning dataset for CLIP.

---

## 3. Data Architecture

### 3.1 Storage layers and their roles

| Layer | Technology | Role | Why |
|---|---|---|---|
| Hot cache | Redis | Product metadata, popular query results, rate-limit counters, idempotency keys | Sub-ms latency, native TTL, atomic INCR |
| Vector index | Qdrant | Product embeddings + categorical filters | HNSW + payload filters, self-hostable |
| Transactional DB | PostgreSQL | Products master, click logs, indexing audit, users (V2) | ACID, JSON columns, mature ecosystem |
| Object storage | Cloudflare R2 | User uploads (TTL 24h), cached product thumbnails | S3 API, **no egress fees** (critical) |
| Message queue | Redis Streams (MVP) → RabbitMQ/SQS (scale) | Async indexing tasks, embedding jobs | Decouple producers/consumers |
| Analytics store `[SCALE]` | ClickHouse or DuckDB | Aggregated business events | Columnar, cheap, fast aggregations |

### 3.2 Why TWO databases for products (Postgres + Qdrant)?

The most common confusion. Both store "products" but with different roles:

| Aspect | PostgreSQL | Qdrant |
|---|---|---|
| Role | Source of truth | Derived search index |
| Optimized for | Transactional reads/writes, joins, history | Approximate nearest neighbor (ANN) search on 512d vectors |
| Can be rebuilt? | No (master data) | Yes, from Postgres |
| Updated by | Catalog ingestion + price freshness daemon | Catalog ingestion only (embeddings don't change with price) |

**Operational consequence:** if Qdrant goes down or gets corrupted, you re-embed and re-index from Postgres. If Postgres goes down, you've lost the master. Backup priorities and SLAs follow accordingly.

### 3.3 Recommended query pattern

```
User request → Qdrant search returns N (product_id, score) pairs
            → Batch fetch from Redis (cache hit ~95%)
            → Cache miss → Postgres SELECT WHERE id IN (...)
            → Build affiliate URLs on the fly (don't store stale URLs)
            → Response
```

**Why hydrate from Postgres/Redis instead of stuffing everything in Qdrant payload?**
- Prices change. Affiliate URLs may include time-sensitive params. Titles get corrected.
- Qdrant payloads are denormalized snapshots, not live data.
- Lookup adds <30ms with proper batching + cache; freshness gain is worth it.

**Minimum Qdrant payload:** `product_id`, `category` (for ANN filter), `retailer` (for diversity), `image_url` (for instant display before full hydration). Everything else lives in Postgres.

---

## 4. Inference Pipeline Architecture

### 4.1 Latency budget for the user-facing path

Target: **<2s p95** from upload to JSON response.

| Stage | Time (CPU MVP) | Notes |
|---|---|---|
| Upload validation | 10ms | Magic bytes, size, mime |
| Image preprocessing | 50ms | Resize to 1024px LANCZOS, EXIF strip |
| Object detection (YOLO) | 100ms | Single forward pass, returns N boxes |
| Crop embedding (CLIP) | 80ms × N | **Parallelizable + batchable** |
| Vector search (Qdrant) | 20ms × N | Batchable in one HTTP call |
| Hydration (Redis/Postgres) | 30ms | Single batched lookup for all matches |
| Response build | 5ms | Pydantic serialization |
| **Total (N=3 objects)** | **~600ms** | Within budget |
| **Total (N=8 objects, naive)** | **~1100ms** | Tight |
| **Total (N=8 objects, batched)** | **~700ms** | Comfortable |

**Critical insight:** stages 4 and 5 are **per-object**. Batch them. Parallelize them. Don't loop sequentially.

### 4.2 Batching opportunities (concrete)

- **CLIP batch encode:** feed all N crops as one tensor → 1 forward pass instead of N. ~5× throughput on CPU at N=8.
- **Qdrant batch search:** one HTTP call with N query vectors, returns N result lists. Avoids per-object network round-trip.
- **Hydration batch:** one Redis MGET / one Postgres `WHERE id = ANY(...)` for all matched products across all objects.

### 4.3 Fallback strategies (resilience patterns)

| Failure | Fallback |
|---|---|
| No objects detected by YOLO | Embed full image with CLIP, search without category filter, label "general matches" |
| YOLO timeout (>500ms) | Same as above |
| CLIP fails | Return 503 with retry-after; this is core, no graceful degrade |
| Qdrant timeout on one object | Skip that object, return others, log warning |
| Hydration cache+DB both fail | Return product_ids + image_urls (from Qdrant payload) without titles/prices, mark as `partial: true` |

This is what "design for failure" means in practice: every external dep has a failure mode and a defined response.

---

## 5. Catalog Ingestion Pipeline

The slowest, most error-prone, and most critical subsystem. **Bad catalog = unusable product.** Treat it as a first-class system, not a script.

### 5.1 The pipeline as a DAG

```
[sources] → [fetch] → [normalize] → [dedupe] → [image_download] → [embed] → [upsert]
                ↓          ↓            ↓            ↓                ↓
              errors    rejects       merges     quarantine        retries
```

Each stage must be:
- **Idempotent.** Re-running yields the same result. Achieved via `(retailer, product_id)` as upsert key.
- **Resumable.** Failure mid-batch resumes from last successful checkpoint. Track per-source `last_indexed_at`.
- **Observable.** Count in/out, error rate, latency per stage. Log to Postgres `indexing_runs` table.

### 5.2 The `ProductSource` abstraction

Each affiliate source (Amazon PA API, Awin CSV feed, IKEA, future others) implements the same interface:

```
ProductSource:
  list_products(since: datetime) → iterator of raw product dicts
  get_product(id: str) → raw product dict
  build_affiliate_url(product_id: str) → str
  source_category_map → dict mapping source taxonomy to YOUR taxonomy
```

Adding a new retailer = implementing one class. The pipeline stays unchanged.

### 5.3 Normalization layer

Different retailers, different category taxonomies:
- Amazon: "Home & Kitchen > Furniture > Living Room Furniture > Sofas & Couches"
- IKEA: "Sofas & armchairs > 3-seat sofas"
- La Redoute: "Salon > Canapés > Canapés 3 places"

You need **your own taxonomy**, mapped from each source. Build this as versioned config (YAML in git), not code. Example:

```
your_categories:
  couch:
    yolo_class: 57  # for prefilter on detection side
    aliases:
      amazon: ["Sofas & Couches", "Sectional Sofas"]
      ikea: ["3-seat sofas", "2-seat sofas", "Corner sofas"]
      la_redoute: ["Canapés 3 places", "Canapés d'angle"]
```

Ambiguous mappings → quarantine + manual review queue (Postgres `unmapped_products` table).

### 5.4 Deduplication

Same physical product can come from Amazon + Awin (e.g., IKEA "Kivik" sofa available on both). Strategies:

- **Brand + model number** when available (rare for deco).
- **Image perceptual hash** (pHash, dHash) — products with same hash within tolerance = same product.
- **Title fuzzy match** + price proximity — secondary signal.

**MVP recommendation:** **skip deduplication entirely.** Show all variants, prefer higher commission at ranking time. Add dedup only when catalog exceeds ~100k products and duplicates become visible in results.

### 5.5 Image embedding job — the bottleneck

- Per-image embed: ~80ms CPU.
- 100k products = 2.2 hours single-threaded. 1M products = 22 hours.
- **Solution:** parallelize via queue. Spawn N workers consuming an `embed_queue`.
- **Cache:** identical `image_url` → identical embedding. Don't recompute on partial re-runs. Cache embeddings keyed by `sha256(image_url)`.
- **Failure handling:** 404, malformed images, too-small images → quarantine table, retry weekly with exponential backoff.

### 5.6 Scheduling

| Phase | Approach |
|---|---|
| MVP | Cron job, daily full + hourly incremental |
| V1 | Prefect or Dagster for proper DAG orchestration, automatic retries, UI for monitoring |
| SCALE | Event-driven: webhooks from sources where available (rare), fall back to scheduled diffs |

### 5.7 Price freshness daemon

**Separate, lightweight job** from full re-indexing. Critical for Amazon TOS compliance.

- Hot tier (top 10% products by click/impression): refresh every 6h.
- Warm tier: refresh every 24h.
- Cold tier: refresh weekly (or skip; let Redis TTL expire and force a full lookup on next display).
- Updates Postgres + invalidates Redis. **Doesn't touch Qdrant** (vectors don't change with price).

---

## 6. Caching Strategy

Cache wisely or invalidate forever. Three layers:

### 6.1 Layer 1 — CDN (Cloudflare in front of API)

- Proxy R2 through Cloudflare → free egress on product images.
- Edge-cache `GET /api/products/{id}` for 5 min (read endpoint, consumer of catalog).
- **Do NOT cache `POST /api/analyze`** — different image each time, cache key explosion.

### 6.2 Layer 2 — Redis application cache

| Key pattern | TTL | Purpose |
|---|---|---|
| `product:{id}` | 5 min | Hydration of search results |
| `embed:{sha256(image_url)}` | 24h | Skip re-embedding same Pinterest image |
| `analyze:{sha256(image_bytes)}` | 1h | Skip whole pipeline if exact same upload |
| `ratelimit:{ip}:{endpoint}` | 60s | Rate-limit counters (INCR + EXPIRE) |

The `analyze:{hash}` cache is **huge for cost.** Many users upload the same Pinterest screenshots. Even a 20% hit rate cuts inference cost by 20%.

### 6.3 Layer 3 — In-process LRU

- Model weights (CLIP, YOLO) — loaded once at worker startup.
- Category mapping config — loaded once, watched for file changes if hot-reload needed.

### 6.4 Cache invalidation triggers

| Event | Invalidation |
|---|---|
| Price update from freshness daemon | `DEL product:{id}` |
| Product taken down (out of stock, retailer remove) | Delete from Qdrant + Postgres + Redis simultaneously |
| Category mapping changed | Full refresh of affected products (rare) |
| Daily sweep | Touch any key approaching TTL of source data |

---

## 7. API Layer Concerns

### 7.1 Rate limiting

- Per-IP for `/api/analyze`: 10/min unauth, 100/min auth `[V2]`.
- Per-IP global: 1000 req/hour (anti-scraping).
- Implementation: Redis with `INCR` + `EXPIRE`, or `slowapi` library for FastAPI.
- Response: 429 with `Retry-After` header.

### 7.2 Upload validation (security-critical)

- Validate by **magic bytes** (`python-magic`), not file extension. JPEG/PNG/WebP only.
- Max size enforced at **two layers**: Cloudflare (~10MB hard limit) + app-level (configurable).
- **Strip EXIF metadata** before storage. EXIF often contains GPS coordinates from phone photos — that's personal data under GDPR.
- Reject images <128×128 (probably abuse/error).

### 7.3 Idempotency

- `POST /api/analyze` should be idempotent on image content.
- Client may send `Idempotency-Key` header (or compute from image hash server-side).
- Stored in Redis 1h → same image upload returns same `request_id` and cached result.
- Saves cost, improves perceived performance, makes retries safe.

### 7.4 Async response option `[V1]`

For future heavier models or batch processing:
- `POST /api/analyze` returns `202 Accepted` + `request_id`.
- Client polls `GET /api/analyze/{id}` or subscribes via SSE.
- Decouples user latency from worst-case processing time.
- At MVP, sync response is fine (sub-2s budget achievable).

---

## 8. Observability

### 8.1 Logs

- **Structured JSON from day 1.** Not text. This is non-negotiable.
- Each entry: `timestamp`, `level`, `service`, `request_id`, `message`, `context` dict.
- Aggregation: Better Stack, Axiom, Grafana Loki, or Railway's built-in (good enough for MVP).
- `request_id` propagated through every layer (logging, metrics, traces).

### 8.2 Metrics (Prometheus-compatible `/metrics` endpoint)

| Category | Metrics |
|---|---|
| HTTP | req/s, p50/p95/p99 latency, error rate per endpoint |
| ML | inference time per stage (yolo, clip), batch size distribution, model load time |
| Qdrant | query latency, top-1 score distribution (quality signal) |
| Catalog | products indexed/day, indexing errors, queue depth, embed cache hit ratio |
| Business | affiliate clicks/day, CTR per category, no-match rate |

The **top-1 score distribution** is sneakily important: if it shifts downward over time, your catalog is drifting from real user uploads → quality is degrading silently.

### 8.3 Tracing `[V1]`

- OpenTelemetry SDK, traces to Tempo / Jaeger / Honeycomb (free tier).
- Each user request = one trace with spans for each pipeline stage.
- Essential for debugging "why was this analyze call slow" — sampling 10% is enough.

### 8.4 Error tracking

- Sentry (free tier generous) or self-hosted GlitchTip.
- Tag errors with: `request_id`, `image_hash` (for repro), `detected_objects`, `service`.

### 8.5 Business analytics — separate stream

- Separate from logs: structured events to `events` table in Postgres (MVP) or ClickHouse (scale).
- Events: `analyze_completed`, `match_shown`, `affiliate_clicked`, `category_detected`.
- This is what tells you if matching quality is improving — logs don't.

---

## 9. Quality Feedback Loop

The meta-system that makes the product better over time. Often deferred; mistake. Capture signals from day 1, act on them in V1+.

### 9.1 Signals

| Signal | Type | Implementation |
|---|---|---|
| Click on match | Implicit + | Affiliate redirect endpoint logs |
| Long dwell on results, no click | Implicit − | Frontend beacon on unload |
| Re-upload similar image within session | Implicit − | Session tracking |
| Thumbs up/down per match `[V2]` | Explicit | Optional UI |

### 9.2 Storage

Append-only event log: `(request_id, user_image_hash, product_id, score, action, timestamp)`. Postgres at MVP, ClickHouse at scale.

### 9.3 Use cases

- **Compute precision@k per category** — identify which categories underperform.
- **Build a fine-tuning dataset for CLIP** `[V2]` — positive pairs from clicks, hard negatives from shown-but-not-clicked. Even 10k pairs improves CLIP noticeably.
- **A/B test ranking changes** — split traffic by `user_session_hash`, compare CTR.

---

## 10. Security & Compliance

### 10.1 GDPR (you're in France — RGPD applies)

- **User-uploaded images = personal data.** Photos of rooms can identify a person, their address (via window views), their possessions.
- **TTL enforcement on R2** via bucket lifecycle rules (not just app code). If app crashes mid-cleanup, lifecycle rule still deletes.
- **Deletion endpoint** for right-to-erasure requests. Even without user accounts, by `request_id`.
- **Privacy policy mentions** image upload, automated processing, retention, third-party processors (Qdrant, R2).
- **Data Processing Agreement** with Qdrant Cloud, Railway, Cloudflare (they all have standard DPAs).

### 10.2 Affiliate compliance

- **Disclosure** on every results page: "Liens affiliés" / "Article contenant des liens commerciaux" — ARPP rule + DGCCRF.
- **Amazon TOS:** prices in display < 24h staleness, image hotlinking forbidden (cache image URLs but don't deep-link, use product image URLs from API directly).
- **Affiliate tags** preserved through any redirect chain (test this — it's easy to lose tags via URL rewriting).

### 10.3 General security

- HTTPS everywhere (Cloudflare provides).
- Secrets via env vars, never in git. Use Doppler / 1Password CLI / Railway secrets.
- DB credentials rotated quarterly (set a calendar reminder).
- CORS strict allowlist — your frontend origins only.
- CSP header on frontend.

---

## 11. Scaling Path with Thresholds

Concrete triggers for each evolution step. Numbers are orders of magnitude.

| Stage | Trigger | Actions |
|---|---|---|
| **MVP** | 0–100 req/day | Monolith on Railway, embedded models, single worker, free tiers everywhere |
| **Early traction** | 100–1k req/day | Add Redis cache, structured logs, basic dashboards, idempotency on analyze |
| **First scaling pain** | 1k–10k req/day, p95 > 2s | Extract inference service (sidecar), implement CLIP batching, add CDN in front of API |
| **GPU territory** | 10k–100k req/day | Managed GPU inference (Modal), async response for analyze, Postgres connection pooling (PgBouncer) |
| **Multi-region** | International expansion | CDN + Postgres read replicas, multi-region Qdrant, regional R2 buckets |
| **Mature product** | 100k+ req/day | Custom-trained models, full A/B test framework, dedicated ML infra, dedicated SRE concerns |

### 11.1 What NOT to do at MVP

These are common over-engineering traps:
- **Kubernetes.** Use Railway or Fly.io. K8s is a job, not a tool, at your stage.
- **Microservices network.** Start as a modular monolith. Boundaries in code ≠ boundaries in network.
- **Custom ML training.** Pretrained CLIP is excellent. Fine-tuning is a V2+ optimization.
- **Multi-region.** Single EU region (Frankfurt, Paris) is fine for France.
- **Dedicated GPU.** CPU is enough for 80ms CLIP latency at MVP scale.
- **Service mesh, distributed tracing across services, Kafka.** No.

---

## 12. Trade-offs and Decision Records (ADR-style)

Short rationales for the major choices. Format: decision → why → trade-off → migration path.

### 12.1 FastAPI over Django

- **Why:** async-native (I/O-bound workload: Qdrant + Redis + R2 + Postgres), Pydantic validation, smaller surface for a solo dev.
- **Trade-off:** smaller ecosystem for admin/auth than Django (will matter only in V2).
- **Migration:** none likely needed.

### 12.2 Qdrant over Pinecone / Weaviate / pgvector

- **Why:** self-hostable (no vendor lock), excellent filter+ANN combo, mature Python client, generous free tier on Qdrant Cloud.
- **Alternatives considered:**
  - **pgvector** — tempting (one DB instead of two), but slower at 1M+ vectors, less efficient combined filter+ANN.
  - **Pinecone** — fully managed but expensive at scale (~$70/mo entry).
  - **Weaviate** — built-in CLIP module (could simplify), but heavier ops, less Python-idiomatic.
- **Trade-off:** extra operational concern vs single-DB pgvector setup.
- **Migration:** could swap to Pinecone if ops becomes painful, abstraction in `services/search.py` is cheap.

### 12.3 CLIP ViT-B/32 over larger variants

- **Why:** 80ms CPU latency budget. ViT-L/14 is more accurate but 4–6× slower.
- **Trade-off:** ~5–10% accuracy loss on fine-grained matching.
- **Migration:** same API contract for any CLIP variant — swap model name, re-embed catalog (a few hours for 100k products). Plan for it from day 1.

### 12.4 Railway over AWS/GCP

- **Why:** solo developer, abstracts ops, $5/mo entry vs $50/mo minimum on AWS.
- **Trade-off:** less control, vendor lock on Railway-specific features (avoid using them).
- **Migration:** everything containerized → can move to Fly.io, Render, or proper AWS ECS in a weekend.

### 12.5 Not using Elasticsearch / OpenSearch / Vespa

- **Why:** all do vector search now but designed for hybrid text+vector. Overkill for pure visual MVP.
- **Add later if:** you want hybrid search (text descriptions + visual similarity).

### 12.6 Sync analyze response at MVP

- **Why:** simpler frontend, sub-2s achievable, no polling complexity.
- **Trade-off:** worst-case slow requests block client.
- **Migration:** add async path in V1 (202 + polling/SSE) without breaking sync path.

---

## 13. Open Questions to Resolve Before Coding

These need product/UX decisions, not technical ones. Flagging for resolution:

1. **Upload size cap.** Spec says 10MB. Most Pinterest screenshots <2MB. Lower = less abuse surface. Suggest 5MB.
2. **YOLO confidence threshold.** Spec says 0.4. Catches too much noise in informal testing of similar systems. Try 0.5–0.6.
3. **Matches per object.** 5 is reasonable. More = decision fatigue. Don't go above 8.
4. **No-detection fallback.** Should the full image be embedded and searched? Suggested yes, with a "general matches" label so user understands why categories aren't shown.
5. **Multiple objects of same category.** Image has 3 chairs — do you return 5 matches per chair (overwhelming) or merge into 5 total (loses spatial context)? UX call.
6. **Catalog refresh cadence.** Depends on retailer churn rate. Suggested: weekly full re-index, daily price refresh on hot tier.
7. **Multi-retailer dedup.** Same physical product on Amazon + Awin/IKEA. MVP: don't dedup. V1: revisit.

---

## 14. Quick reference — module mapping

How the §2 logical services map to the project structure from spec §6:

| Logical service | Code location | Notes |
|---|---|---|
| API Gateway | `app/api/routes.py`, `app/models/schemas.py` | Thin layer |
| Inference Service | `app/services/detection.py`, `app/services/embedding.py` | Boundary candidate for later extraction |
| Search Service | `app/services/search.py` | Add ranking/diversity logic here |
| Catalog Service | `scripts/index_*.py` → eventually `app/catalog/` module | Becomes its own deployable later |
| Affiliate Service | New: `app/services/affiliate.py` | URL builders + click tracking |
| Quality Service | New: `app/services/feedback.py` | Even at MVP, log events |

**One module = one responsibility = one future service.** If a module has reasons to change from two unrelated business concerns, split it.

---

## End

For future agents: this doc and `spec_technique_deco_visuel.md` together describe the full intended system. Implement the MVP path first. Question any `[SCALE]` recommendation against the actual current load before applying it.
