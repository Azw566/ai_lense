# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

---

## Project Status — Visual Deco Search MVP

### What is done (as of 2026-05-13)

Phase 0 is fully implemented and committed on `main`. Two commits:

- `111d863` — P0-T1/T2/T3: project scaffold, config, structured logging, async DB connections (Postgres, Redis, Qdrant, R2), `/healthz` + `/livez` endpoints
- `09fde9e` — P0-T4: GitHub Actions CI pipeline, `railway.toml`, Dockerfile hardening (non-root user, layer caching, permission fix)

The codebase is clean and passes the QA director review (Opus model). All P0 blockers resolved.

### What is NOT done — requires another machine

**P0-T4 is code-complete but not yet deployed.** The Railway CLI and web dashboard could not be accessed from the development machine (corporate security restrictions).

The following steps must be completed from an unrestricted machine:

#### 1. Push to GitHub
```bash
git push origin main
```

#### 2. Create Railway account
Go to https://railway.app and sign up (GitHub login recommended).

#### 3. Create a Qdrant Cloud cluster
Go to https://cloud.qdrant.io → free tier → 1 GB cluster in EU region.
Note the cluster URL and API key.

#### 4. Create a Cloudflare R2 bucket
Go to https://dash.cloudflare.com → R2 → Create bucket named `visual-deco-search`.
Create an API token with R2 read/write permissions.
Note the account ID, access key, and secret key.

#### 5. Deploy on Railway (CLI)
```bash
cd <project-dir>
railway login
railway init           # name: visual-deco-search, type: Empty project
railway add --plugin postgresql
railway add --plugin redis
railway variables set APP_ENV=production
railway variables set QDRANT_URL=<qdrant-cluster-url>
railway variables set QDRANT_API_KEY=<qdrant-api-key>
railway variables set R2_ENDPOINT_URL=https://<account_id>.r2.cloudflarestorage.com
railway variables set R2_ACCESS_KEY_ID=<r2-access-key>
railway variables set R2_SECRET_ACCESS_KEY=<r2-secret-key>
railway variables set R2_BUCKET_NAME=visual-deco-search
git push origin main
railway up
```

#### 6. Enable CI gating in Railway dashboard
Railway dashboard → your service → Settings → Deploy → enable **"Wait for CI"**
so Railway only deploys after GitHub Actions passes.

#### 7. Verify the deploy
Hit `<railway-public-url>/livez` → should return `{"status": "ok"}` (HTTP 200).
Hit `<railway-public-url>/healthz` → should return all four checks as `true`.

#### 8. Tag the foundations commit
```bash
git tag v0.0.1-foundations
git push origin v0.0.1-foundations
```

### Next phase after deploy: Phase 1 — Catalog ingestion
See `roadmap_visual_deco_search.md` §4 (P1-T1 through P1-T6).
Start with P1-T1 (Postgres schema + Alembic) using Sonnet 4.6.