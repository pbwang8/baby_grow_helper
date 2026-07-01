# ngrok Free Domain Setup

> 目的：在还没有购买 `babygrowhelper.com` 前，先给家庭内测一个固定 HTTPS 入口。

## Why ngrok

当前最适合 BabyGrowHelper 家庭内测的免费方案是 ngrok Dev Domain：

- 不需要家人和你的电脑在同一个 Wi-Fi。
- 不需要改路由器端口转发。
- 不需要现在购买域名。
- 可以直接接到现有 Docker Compose 的 `web:3000` 服务。

这仍然是临时内测入口。正式对外时，优先切到自有域名
`app.babygrowhelper.com` + Cloudflare Named Tunnel 或云服务器 HTTPS。

## Owner Setup

1. 注册并登录 ngrok。
2. 复制 `Authtoken`。
3. 找到免费 Dev Domain，形如：

```text
https://example.ngrok-free.app
```

4. 在项目根目录的 `.env` 里加入：

```bash
NEXT_PUBLIC_API_BASE=/api
API_INTERNAL_BASE=http://api:8000
NGROK_AUTHTOKEN=粘贴ngrok给你的authtoken
NGROK_DOMAIN=https://example.ngrok-free.app
```

不要提交 `.env`。

## Start

```bash
cd /Users/wangpengbo/Documents/Claude/Projects/baby_grow_helper
docker compose --env-file .env -f deploy/docker-compose.family-trial.yml \
  --profile ngrok up -d
```

## Verify

```bash
curl -I "$NGROK_DOMAIN/login"
curl "$NGROK_DOMAIN/api/health"
```

Expected health shape:

```json
{"ok":true,"sqlite":true,"ollama":true}
```

## Family Trial URL

Send this to invited family members:

```text
https://example.ngrok-free.app/login
```

Each family should still use its own access code, for example:

```text
bgh-abcd-2345-wxyz
```

## Limits

- The Mac running Docker must stay powered on and online.
- The ngrok account/token is operational secret material; never commit it.
- This is suitable for early invite-only testing, not public launch.
