# Git Handoff Workflow

> 用途：两台电脑、多个 Codex/Claude 会话之间交接 BabyGrowHelper。代码用 GitHub 同步；密钥、数据库、模型权重不进 Git。

## 1. 每次开工前

```bash
git fetch --prune origin
git status -sb
git pull --ff-only origin main
git log -1 --oneline --decorate
```

如果 `git status -sb` 不是干净的，先判断这些改动是谁留下的：

- 自己上次未完成：继续做，或 `git stash push -u -m "wip: <说明>"`
- 另一台机器已经 push：不要覆盖，先 `git pull --ff-only`
- 不确定来源：停下来问人，不做 reset / checkout

## 2. 每次交接前

```bash
git status -sb
uv run --no-sync pytest -m "not integration"
cd web && npm run typecheck && cd ..
git add <本次改动文件>
git commit -m "<type>(<scope>): <一句话>"
git push origin main
git status -sb
```

commit 格式沿用 `AGENTS.md`：

```text
feat | fix | chore | docs | test | refactor | adr
```

## 3. 分支规则

默认使用 `main` 做短节奏串行开发；同一时间只让一台机器/一个 Code 会话写同一块代码。

需要并行时才开分支：

```bash
git switch -c work/<short-topic>
git push -u origin work/<short-topic>
```

合回前至少通过：

```bash
uv run --no-sync pytest -m "not integration"
cd web && npm run typecheck && cd ..
git status -sb
```

## 4. 本机 Git 配置

每个 clone 建议设置一次：

```bash
git config pull.ff only
git config fetch.prune true
git config core.autocrlf false
git config core.eol lf
```

仓库包含 `.gitattributes`，用于减少 Windows 和 macOS 之间的 CRLF 噪音。

## 5. 永远不进 Git 的东西

提交前用这两条确认：

```bash
git status -sb --ignored
git check-ignore .env data/demo_xiaoming.db web/node_modules
```

这些只属于本机：

- `.env`
- `data/*.db`
- `.venv`
- `web/node_modules`
- Ollama 模型权重
- `.claude/settings.local.json`

真实家庭数据不要通过 Git 传输。

## 6. Windows PowerShell 注意

在 Windows 上优先跑：

```powershell
.\scripts\bootstrap_new_machine.ps1
```

如果 PowerShell 拦截脚本，可用单次进程绕过：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap_new_machine.ps1
```

PowerShell 下建议带上 UTF-8：

```powershell
$env:PYTHONUTF8 = "1"
```
