# Cloudflare Fixed Domain Setup

Target hostname:

```text
app.babygrowhelper.com
```

## What Codex Can Prepare

- Docker Compose has a `named-tunnel` profile.
- `.env.example` includes `CLOUDFLARE_TUNNEL_TOKEN`.
- The app should keep `NEXT_PUBLIC_API_BASE=/api`; Cloudflare points only to
  the web container, and Next.js proxies `/api/*` to FastAPI internally.

## What The Owner Must Do In Cloudflare

1. Own or buy `babygrowhelper.com`.
2. Add `babygrowhelper.com` to Cloudflare.
3. Change the domain registrar nameservers to Cloudflare's assigned
   nameservers.
4. In Cloudflare Zero Trust, create a Cloudflared tunnel named
   `babygrow-family-trial`.
5. Add a Public Hostname:
   - Hostname: `app.babygrowhelper.com`
   - Service type: `HTTP`
   - Service URL: `http://web:3000`
6. Copy the tunnel token into local `.env`:

```bash
CLOUDFLARE_TUNNEL_TOKEN=...
```

## Start

```bash
cd /Users/wangpengbo/Documents/Claude/Projects/baby_grow_helper
docker compose --env-file .env -f deploy/docker-compose.family-trial.yml \
  --profile named-tunnel up -d
```

## Verify

```bash
curl -I https://app.babygrowhelper.com/login
curl https://app.babygrowhelper.com/api/health
```

Expected health shape:

```json
{"ok":true,"sqlite":true,"ollama":true}
```

