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
  - 免费跨网试用：ngrok free Dev Domain
  - 更正式内测：一台云服务器 + 域名/HTTPS 反代
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

# 内测反馈周摘要。反馈本身始终先写入 Postgres；邮件只是汇总通知。
BGH_FEEDBACK_DIGEST_TO=wpb889@outlook.com
FEEDBACK_DIGEST_INTERVAL_SECONDS=604800
FEEDBACK_DIGEST_LOOKBACK_DAYS=7

# 配好 SMTP 后才会真正发邮件；不要提交真实密码。
# SMTP_HOST=smtp.example.com
# SMTP_PORT=587
# SMTP_USERNAME=
# SMTP_PASSWORD=
# SMTP_TLS=1
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

当前 Postgres runtime 已支持家庭鉴权、children/events/heatmap/feedback 的读写。
内测家庭访问码用 `family_admin` 创建；命令只会打印一次明文访问码，
数据库只保存 hash。

```bash
docker compose --env-file .env -f deploy/docker-compose.family-trial.yml exec api \
  uv run --no-sync python -m src.scripts.family_admin create \
  --family-id fam_001 \
  --name "Alpha Family" \
  --owner-name "Alpha Owner"
```

返回 JSON 里的 `access_code` 发给对应家庭。不要把它提交进 Git。
自动生成的码形如 `bgh-abcd-2345-wxyz`，尽量避开容易看错的字符。每个家庭
都应该使用不同访问码；如果需要更好记的短码，可在创建时显式传入：

```bash
docker compose --env-file .env -f deploy/docker-compose.family-trial.yml exec api \
  uv run --no-sync python -m src.scripts.family_admin create \
  --family-id fam_002 \
  --name "Beta Family" \
  --access-code "bgh-beta-2026"
```

显式短码更方便手输，但也更容易被猜到；只适合小规模邀请制内测。

家庭成员登录后可以在 `/children` 自己创建 child。管理员也可以提前创建：

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

输入家庭访问码后进入 `/log`；如果家庭还没有孩子档案，进入 `/children`
创建。之后该手机会把访问码和当前孩子保存在浏览器本地。

安卓/华为浏览器可以从浏览器菜单选择“添加到桌面”。manifest 已在
`web/public/manifest.webmanifest`。

不在同一 Wi-Fi 时，推荐先用 Cloudflare Tunnel：

```bash
cloudflared tunnel --url http://localhost:3000
```

Cloudflare 会给一个 `https://*.trycloudflare.com` 临时地址。把这个地址发给
内测家庭，仍然输入家庭访问码登录。这个方案要求运行 Docker 的电脑保持开机和联网。
更正式的内测再切到命名 Tunnel + 自有域名。

## 6. 固定域名：Cloudflare Named Tunnel

推荐固定域名：

```text
https://app.babygrowhelper.com
```

前提：

1. 你拥有 `babygrowhelper.com`。
2. `babygrowhelper.com` 已添加到 Cloudflare，并且 DNS 由 Cloudflare 托管。
3. Docker 服务仍然在运行这套 Compose。

Cloudflare 控制台操作：

1. 进入 Cloudflare Zero Trust。
2. 打开 `Networks` → `Tunnels` → `Create a tunnel`。
3. 类型选 `Cloudflared`。
4. 命名为 `babygrow-family-trial`。
5. 选择 Docker connector，复制 Cloudflare 给出的 tunnel token。
6. 在 `Public Hostname` 添加：
   - Subdomain: `app`
   - Domain: `babygrowhelper.com`
   - Type: `HTTP`
   - URL: `http://web:3000`

本机 `.env` 增加：

```bash
APP_PUBLIC_HOSTNAME=app.babygrowhelper.com
NEXT_PUBLIC_API_BASE=/api
API_INTERNAL_BASE=http://api:8000
CLOUDFLARE_TUNNEL_TOKEN=粘贴Cloudflare给你的token
```

启动固定域名 tunnel：

```bash
cd /Users/wangpengbo/Documents/Claude/Projects/baby_grow_helper
docker compose --env-file .env -f deploy/docker-compose.family-trial.yml \
  --profile named-tunnel up -d
```

验证：

```bash
curl -I https://app.babygrowhelper.com/login
curl https://app.babygrowhelper.com/api/health
```

注意：

- `trycloudflare.com` Quick Tunnel 会变、会断，只适合临时测试。
- Named Tunnel + 自有域名才适合发给多个家庭持续内测。
- 不要把 `CLOUDFLARE_TUNNEL_TOKEN` 提交进 Git。

## 7. 免费固定入口：ngrok Dev Domain

如果暂时不想买 `babygrowhelper.com`，家庭内测的第一条稳定 HTTPS 地址建议用
ngrok 免费 Dev Domain。它适合当前阶段：不用同一 Wi-Fi，不需要路由器端口转发，
也不需要先买域名。

前提：

1. 注册一个免费 ngrok 账号。
2. 在 ngrok Dashboard 复制 `Authtoken`。
3. 在 ngrok Dashboard 的 Domains/Endpoints 页面找到你的免费 Dev Domain，
   形如 `https://xxxx.ngrok-free.app`。

本机 `.env` 增加：

```bash
NEXT_PUBLIC_API_BASE=/api
API_INTERNAL_BASE=http://api:8000
NGROK_AUTHTOKEN=粘贴ngrok给你的authtoken
NGROK_DOMAIN=https://你的免费dev-domain.ngrok-free.app
```

启动：

```bash
cd /Users/wangpengbo/Documents/Claude/Projects/baby_grow_helper
docker compose --env-file .env -f deploy/docker-compose.family-trial.yml \
  --profile ngrok up -d
```

验证：

```bash
curl -I "$NGROK_DOMAIN/login"
curl "$NGROK_DOMAIN/api/health"
```

把 `NGROK_DOMAIN/login` 发给家人，仍然使用每个家庭自己的访问码登录。

边界：

- 这条路径适合 ≤10 家庭早期内测，不是最终商业部署。
- 运行 Docker 的电脑仍然需要开机联网。
- 不要把 `NGROK_AUTHTOKEN` 或真实家庭访问码提交进 Git。
- 未来买下 `babygrowhelper.com` 后，可切回 §6 的 Cloudflare Named Tunnel。

## 8. 内测反馈与每周提醒

产品级内测反馈通过 `/feedback` 提交，服务端会写入 Postgres 的
`trial_feedback` 表。邮件只是每周摘要，不是数据源；即使邮件发送失败，
反馈也不会丢。

手动查看最近 7 天反馈：

```bash
cd /Users/wangpengbo/Documents/Claude/Projects/baby_grow_helper
docker compose --env-file .env -f deploy/docker-compose.family-trial.yml exec api \
  uv run --no-sync python -m src.scripts.feedback_digest --days 7
```

启用每周自动发送前，先在 `.env` 配置 SMTP：

```bash
BGH_FEEDBACK_DIGEST_TO=wpb889@outlook.com
BGH_FEEDBACK_DIGEST_FROM=你的发件邮箱
SMTP_HOST=你的SMTP服务器
SMTP_PORT=587
SMTP_USERNAME=你的SMTP用户名
SMTP_PASSWORD=你的SMTP密码或应用专用密码
SMTP_TLS=1
```

启动每周反馈摘要 worker：

```bash
docker compose --env-file .env -f deploy/docker-compose.family-trial.yml \
  --profile feedback-email up -d feedback-digest
```

注意：

- 不要把 SMTP 密码提交到 Git。
- 如果 SMTP 没配，worker 会把摘要打印到日志，但不会发邮件。
- 正式账号体系上线后，`trial_feedback.family_id/child_id` 可继续迁移和追溯。

如果暂时不配置 SMTP，可以使用本机 macOS 定时提醒：

```bash
cd /Users/wangpengbo/Documents/Claude/Projects/baby_grow_helper
scripts/check_feedback_digest.sh
```

已提供 `deploy/launchd/com.babygrow.feedback-reminder.plist`，用于每周一
09:00 检查最近 7 天是否有反馈；有反馈时弹出系统通知，并把摘要写入
`reports/feedback-digest.latest.local.txt`。

## 9. Postgres 自动备份

家庭内测真实数据以 Postgres 为事实源。为避免本机、Docker volume 或人为
操作造成数据损失，内测机应启用本地自动备份。

脚本：

- `scripts/backup_postgres.sh`：生成 `pg_dump -Fc` 格式备份，默认保留 30 天。
- `scripts/restore_postgres_backup.sh`：从备份恢复到当前 family-trial Postgres。

默认位置：

```text
backups/postgres/babygrow-YYYYMMDDTHHMMSSZ.dump
reports/postgres-backup.local.log
```

`backups/` 和 `reports/*.local.*` 已被 `.gitignore` 忽略，真实数据不会进入 Git。

可配置项：

```bash
BGH_BACKUP_RETENTION_DAYS=30
BGH_BACKUP_MIN_INTERVAL_HOURS=20
# 未来接云盘 / 对象存储时再启用；hook 会收到本地 dump 文件路径作为第一个参数。
# BGH_BACKUP_AFTER_HOOK=/absolute/path/to/private-backup-upload.sh
```

手动备份：

```bash
cd /Users/wangpengbo/Documents/Claude/Projects/baby_grow_helper
scripts/backup_postgres.sh
```

安装 macOS 每日自动备份：

```bash
cd /Users/wangpengbo/Documents/Claude/Projects/baby_grow_helper
chmod +x scripts/backup_postgres.sh scripts/restore_postgres_backup.sh
mkdir -p "$HOME/Library/LaunchAgents"
cp deploy/launchd/com.babygrow.postgres-backup.plist \
  "$HOME/Library/LaunchAgents/com.babygrow.postgres-backup.plist"
launchctl load "$HOME/Library/LaunchAgents/com.babygrow.postgres-backup.plist"
```

默认每天 03:15 执行一次，并且在电脑开机/登录加载任务时也会自检一次。
脚本默认发现最近 20 小时已有备份就跳过；如果 03:15 时电脑没开，下一次
开机/登录会自动补备份。成功、跳过或失败都会写入
`reports/postgres-backup.local.log`，并尽量弹出 macOS 通知。

远端备份接口：

- 已提供 `scripts/backup_remote_hook.example.sh` 作为接口样例。
- 现在不启用任何云盘或对象存储，避免过早绑定供应商。
- 后续确定 iCloud/rclone/S3/R2/OSS 后，只需要写一个私有 hook，并在 `.env`
  设置 `BGH_BACKUP_AFTER_HOOK`。
- 私有 hook 和云端 token 不进 Git。

恢复备份：

```bash
cd /Users/wangpengbo/Documents/Claude/Projects/baby_grow_helper
scripts/restore_postgres_backup.sh backups/postgres/某个备份.dump
```

恢复会删除当前 Postgres `public` schema 后重建，需要手动输入 `RESTORE`
才会继续。只在明确需要回滚数据时执行。

## 10. 当前边界

- 适合邀请制小规模内测，不适合公开注册。
- 当前 Postgres runtime 优先覆盖 `/auth/family`、children、events、
  `/heatmap` 和 `/feedback`。
- 信号提取与周报生成的 Postgres runtime 迁移是下一批工程任务。
- 真实儿童数据不要通过 Git、截图或未加密渠道传输。
- 公网内测必须加 HTTPS；裸 HTTP 只用于同一 Wi-Fi 临时试用。
