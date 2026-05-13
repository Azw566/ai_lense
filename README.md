# Visual Deco Search MVP

Personal Project for learning purposes on AI image identification and searching engine.

## Deploying

### Prerequisites
- [Railway](https://railway.app) account
- [Qdrant Cloud](https://cloud.qdrant.io) free-tier cluster (1 GB) — Railway does not host Qdrant well
- [Cloudflare R2](https://dash.cloudflare.com) bucket

### Steps

1. **Fork & connect repo** — Connect your GitHub repo to a new Railway project. Railway auto-detects `railway.toml` and uses the Dockerfile.

2. **Add Railway plugins** — In the Railway dashboard, add a **PostgreSQL** plugin and a **Redis** plugin to the project. They auto-inject `DATABASE_URL` and `REDIS_URL` as env vars.

3. **Set environment variables** in Railway dashboard → Variables:
   ```
   APP_ENV=production
   QDRANT_URL=<your-qdrant-cloud-cluster-url>
   QDRANT_API_KEY=<your-qdrant-api-key>
   R2_ENDPOINT_URL=https://<account_id>.r2.cloudflarestorage.com
   R2_ACCESS_KEY_ID=<your-r2-access-key>
   R2_SECRET_ACCESS_KEY=<your-r2-secret-key>
   R2_BUCKET_NAME=visual-deco-search
   ```

4. **Deploy** — Push to `main`. GitHub Actions runs lint + tests first; Railway deploys on success.

   > **Important:** By default Railway deploys on every push to `main` regardless of CI outcome. To enforce the CI-first flow, go to Railway dashboard → your service → Settings → Deploy → enable **"Wait for CI"**. This ensures Railway only deploys after GitHub Actions passes.

5. **Verify** — Open the Railway-provided public URL and hit `/healthz`. All four checks (`pg`, `redis`, `qdrant`, `r2`) should return `true`.

> Tag this commit `v0.0.1-foundations` once all checks pass.
