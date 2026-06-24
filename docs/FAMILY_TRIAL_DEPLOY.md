# Family Trial Deploy

> 目标：让不超过 10 个受邀家庭用手机浏览器试用 BabyGrowHelper。
> 当前定位是内测，不是公开商业化服务。

## 1. 本机/服务器准备

需要：

- Docker Desktop 或 Docker Engine
- Git
- 能拉取 Docker 镜像，包括 `pgvector/pgvector:pg16`
- 一个能被手机访问到的地址
  - 同一 Wi-Fi：电脑局域网 IP，例如 `192.168.1.23`
  - 小范围远程试用：一台云服务器 + 域名/HTTPS 反代
- Ollama 已在宿主机运行，并已拉取 `qwen2.5:3b-instruct`

## 2. 配置环境

在项目根目录创建 `.env`，不要提交：

```bash
POSTGRES_PASSWORD=change-this-password
BGH_FAMILY_TRIAL_MAX_FAMILIES=10

# 浏览器只访问 Web；Web 通过同源 /api 代理转发到内部 API。
NEXT_PUBLIC_API_BASE=/api
API_INTERNAL_BASE=http://api:8000

# 如果直接调试 FastAPI，可把允许来源加到这里；正常手机访问不需要直连 :8000。
BGH_CORS_ORIGINS=http://192.168.1.23:3000

BGH_OLLAMA_URL=http://host.docker.internal:11434
BGH_OLLAMA_MODEL=qwen2.5:3b-instruct
```

公开域名部署时，仍然保持 `NEXT_PUBLIC_API_BASE=/api`。只需要让公网入口指向
Web 服务；API 不需要单独暴露到公网。例如 Cloudflare Tunnel 只转发到
`http://localhost:3000`。

如果你确实要直接暴露 API 域名，才需要额外设置：

```bash
BGH_CORS_ORIGINS=https://app.example.com
```

## 3. 启动服务

```bash
cd /Users/wangpengbo/Documents/Claude/Projects/baby_grow_helper
docker compose --env-file .env -f deploy/docker-compose.family-trial.yml up --build
```

第一次启动后，另开终端拉本地模型：

```bash
ollama pull qwen2.5:3b-instruct
```

默认配置会让 Docker 里的 API 连接宿主机 Ollama：
`http://host.docker.internal:11434`。如果确实要把 Ollama 也跑进 Docker，
可以改用：

```bash
docker compose --env-file .env --profile container-ollama -f deploy/docker-compose.family-trial.yml up --build
```

## 4. 创建家庭访问码

当前 Postgres runtime 已支持家庭鉴权和 events 读写。内测家庭访问码用
`family_admin` 创建；命令只会打印一次明文访问码，数据库只保存 hash。

```bash
docker compose --env-file .env -f deploy/docker-compose.family-trial.yml exec api \
  uv run --no-sync python -m src.scripts.family_admin create \
  --family-id fam_001 \
  --name "Alpha Family" \
  --owner-name "Alpha Owner"
```

返回 JSON 里的 `access_code` 发给对应家庭。不要把它提交进 Git。

创建这个家庭的 child：

```bash
docker compose --env-file .env -f deploy/docker-compose.family-trial.yml exec api \
  uv run --no-sync python -m src.scripts.family_admin create-child \
  --child-id child_001 \
  --family-id fam_001 \
  --name "孩子昵称" \
  --birthday 2023-06-01
```

## 5. 手机访问

同一 Wi-Fi：

```text
http://192.168.1.23:3000/login
```

输入家庭访问码后进入 `/log`，之后该手机会把访问码保存在浏览器本地。

安卓/华为浏览器可以从浏览器菜单选择“添加到桌面”。manifest 已在
`web/public/manifest.webmanifest`。

不在同一 Wi-Fi 时，推荐先用 Cloudflare Tunnel：

```bash
cloudflared tunnel --url http://localhost:3000
```

Cloudflare 会给一个 `https://*.trycloudflare.com` 临时地址。把这个地址发给
内测家庭，仍然输入家庭访问码登录。这个方案要求运行 Docker 的电脑保持开机和联网。
更正式的内测再切到命名 Tunnel + 自有域名。

## 6. 当前边界

- 适合邀请制小规模内测，不适合公开注册。
- 当前 Postgres runtime 优先覆盖 `/auth/family`、`POST /events`、`GET /events`。
- 信号、周报、反馈的 Postgres runtime 迁移是下一批工程任务。
- 真实儿童数据不要通过 Git、截图或未加密渠道传输。
- 公网内测必须加 HTTPS；裸 HTTP 只用于同一 Wi-Fi 临时试用。
