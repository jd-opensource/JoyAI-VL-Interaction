# StreamingHarness Codex API

> 原文档: [README.md](README.md)

围绕系统 `codex` CLI 的本地 FastAPI 封装，用于后台任务。

## 安全提示

我们推荐在隔离环境中启动 Codex API 服务。该服务会使用 YOLO 模式
（`--dangerously-bypass-approvals-and-sandbox`），这样后台任务可以在没有交互式审批的情况下运行，
但也意味着该进程应被视为高权限进程。

为了更安全地部署，可以将 Codex API 服务封装成 Docker 启动，或者在隔离的临时用户下启动，
并为其配置专用的 `CODEX_HOME` 和工作区。除非你已经额外添加了网络访问控制，否则请保持服务
只绑定在 localhost。

## 运行

推荐从仓库根目录使用组件脚本启动：

```bash
./services/background-agent/scripts/run.sh
```

`run.sh` 优先使用安装脚本创建的共享环境 `services/.venv`。如果该环境不存在，则回退到 `uv run` 开发模式：

```bash
cd services/background-agent
./scripts/run.sh
```

WebUI 后台客户端默认使用 `http://127.0.0.1:8079`。可通过以下方式覆盖：

```bash
export BACKGROUND_AGENT_API_URL=http://127.0.0.1:8079
```

`run.sh` 默认使用 `<repo>/agent-workspace` 作为 Codex 工作区，并在启动时创建它。可通过环境变量覆盖运行时路径：

```bash
CODEX_HOME=/path/to/codex-home CODEX_API_WORKSPACE=/path/to/repo ./scripts/run.sh
```

## 行为

- 默认使用服务目录中的 `codex-home/config.toml` 和 `codex-home/auth.json`。
- 使用 `--dangerously-bypass-approvals-and-sandbox`、`--search`、`--json` 和 `--ephemeral` 运行 `codex exec`。
- 通过 `agents.max_threads` 限制并行 subagent 数量；默认值为 `6`。
- 默认绑定到 localhost。不要将此服务暴露给不可信网络。

## 健康检查

```bash
curl http://127.0.0.1:8079/health
```
