# Family Trial Deploy

> 目标：让不超过 10 个受邀家庭用手机浏览器试用 BabyGrowHelper。
> 当前定位是内测，不是公开商业化服务。

## 1. 本机/服务器准备

需要：

- Docker Desktop 或 Docker Engine
- Git
- 一个能被手机访问到的地址
  - 同一 Wi-Fi：电脑局域网 IP，例如 `192.168.1.23`
  - 小范围远程试用：一台云服务器 + 域名/HTTPS 反代

## 2. 配置环境

在项目根目录创建 `.env`，不要提交：

```bash
POSTGRES_PASSWORD=change-this-password
BGH_FAMILY_TRIAL_MAX_FAMILIES=10

# 同一 Wi-Fi 时，把 192.168.1.23 换成运行 Docker 的电脑 IP。
NEXT_PUBLIC_API_BASE=http://192.168.1.23:8000
BGH_CORS_ORIGINS=http://192.168.1.23:3000

BGH_OLLAMA_MODEL=qwen2.5:3b-instruct
```

公开域名部署时，把上面两行换成实际域名，例如：

```bash
NEXT_PUBLIC_API_BASE=https://api.example.com
BGH_CORS_ORIGINS=https://app.example.com
```

## 3. 启动服务

```bash
cd /Users/wangpengbo/Documents/Claude/Projects/baby_grow_helper
docker compose -f deploy/docker-compose.family-trial.yml up --build
```

第一次启动后，另开终端拉本地模型：

```bash
docker compose -f deploy/docker-compose.family-trial.yml exec ollama \
  ollama pull qwen2.5:3b-instruct
```

## 4. 创建家庭访问码

当前 Postgres runtime 已支持家庭鉴权和 events 读写。内测家庭访问码可以通过
Postgres SQL 临时创建；后续会把 admin CLI 也迁到 Postgres。

```bash
docker compose -f deploy/docker-compose.family-trial.yml exec postgres psql \
  -U babygrow -d babygrow
```

在 `psql` 内执行：

```sql
INSERT INTO families(id, name, access_code_hash)
VALUES (
  'fam_001',
  'Alpha Family',
  'sha256:<由本地 family_admin 或 Python 计算出的 hash>'
);

INSERT INTO children(id, family_id, name, birthday)
VALUES ('child_001', 'fam_001', '孩子昵称', '2023-06-01');
```

更安全的操作方式是在本地计算访问码 hash：

```bash
uv run --no-sync python - <<'PY'
from src.core.family import hash_access_code
print(hash_access_code("给家人的访问码"))
PY
```

然后把输出填进 SQL 的 `access_code_hash`。

## 5. 手机访问

同一 Wi-Fi：

```text
http://192.168.1.23:3000/login
```

输入家庭访问码后进入 `/log`，之后该手机会把访问码保存在浏览器本地。

安卓/华为浏览器可以从浏览器菜单选择“添加到桌面”。manifest 已在
`web/public/manifest.webmanifest`。

## 6. 当前边界

- 适合邀请制小规模内测，不适合公开注册。
- 当前 Postgres runtime 优先覆盖 `/auth/family`、`POST /events`、`GET /events`。
- 信号、周报、反馈的 Postgres runtime 迁移是下一批工程任务。
- 真实儿童数据不要通过 Git、截图或未加密渠道传输。
- 公网内测必须加 HTTPS；裸 HTTP 只用于同一 Wi-Fi 临时试用。
