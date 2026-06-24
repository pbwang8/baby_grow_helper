# New Machine Setup

> 用途：把 BabyGrowHelper 从一台电脑迁移到另一台电脑继续开发。
> 代码通过 GitHub 同步；密钥、数据库、模型权重不进 Git。

## 1. 当前电脑先做

在旧电脑确认所有阶段性进度已经提交并推送：

```bash
cd /Users/wangpengbo/Documents/Claude/Projects/baby_grow_helper
git status
git push origin main
```

如果 `git status` 还有未提交文件，先提交再换电脑。

## 2. 新电脑 clone

```bash
mkdir -p ~/Documents/Claude/Projects
cd ~/Documents/Claude/Projects
git clone https://github.com/pbwang8/baby_grow_helper.git
cd baby_grow_helper
```

Windows PowerShell：

```powershell
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\Documents\Claude\Projects"
cd "$env:USERPROFILE\Documents\Claude\Projects"
git clone https://github.com/pbwang8/baby_grow_helper.git
cd baby_grow_helper
```

在 Codex 里打开这个目录：

```text
~/Documents/Claude/Projects/baby_grow_helper
```

Windows：

```text
%USERPROFILE%\Documents\Claude\Projects\baby_grow_helper
```

## 3. 准备系统依赖

必须有：

- `uv`
- `node` + `npm`
- `ollama`
- `git`

不要用 `sudo npm install -g ...`。如果缺 Node，优先用 nodejs.org 的 macOS
installer；如果缺 Ollama，用 Ollama 官方安装包。

## 4. 一键 bootstrap

macOS / Linux / Git Bash：

```bash
cd ~/Documents/Claude/Projects/baby_grow_helper
bash scripts/bootstrap_new_machine.sh
```

Windows PowerShell：

```powershell
cd "$env:USERPROFILE\Documents\Claude\Projects\baby_grow_helper"
.\scripts\bootstrap_new_machine.ps1
```

脚本会做：

- 检查 `git/uv/node/npm/ollama`
- 安装 Python 依赖
- 安装前端依赖
- 初始化本地 SQLite
- 创建 `data/demo_xiaoming.db`，写入合成小明 fixture
- 跑一轮非集成测试
- 跑前端 TypeScript 检查

两台电脑之间的日常交接规则见：

```text
docs/GIT_HANDOFF_WORKFLOW.md
```

## 5. 本地密钥

`.env` 不会通过 Git 同步。需要跑真云端周报或 smoke test 时，在新电脑项目根目录创建：

```bash
cat > .env <<'EOF'
BGH_ANTHROPIC_API_KEY=你的key
EOF
```

确认不会被提交：

```bash
git check-ignore .env && echo ".env is ignored"
```

## 6. 运行 App

终端 A：

```bash
cd ~/Documents/Claude/Projects/baby_grow_helper
export UV_CACHE_DIR="$TMPDIR/uv-cache"
export BGH_DB="./data/demo_xiaoming.db"
uv run --no-sync uvicorn src.api.main:app --host 127.0.0.1 --port 8000 --reload
```

终端 B：

```bash
cd ~/Documents/Claude/Projects/baby_grow_helper/web
npm run dev
```

浏览器打开：

```text
http://localhost:3000
```

如果端口被占用：

```bash
lsof -i :8000
lsof -i :3000
```

看到 `uvicorn/python` 或 `node` 正在监听，通常说明服务已经跑起来了。

## 7. 哪些东西不会同步

这些是每台电脑自己的状态，正常不会进入 Git：

- `.env`
- `data/*.db`
- `.venv`
- `web/node_modules`
- Ollama 模型权重
- `.claude/settings.local.json`

真实家庭数据不要通过 Git 传输。后续家庭内测要走服务端数据库或加密备份方案。
