# manus-mini

这是一个 `manus` 项目的 TUI 第一版实现，包含三层 Agent Loop、工具调度、长期记忆、上下文压缩基础能力。

## 安装

在项目根目录执行：

```bash
pip install -e ".[dev]"
```

## 配置

项目会自动读取根目录下的 `.env` 文件。当前支持：

```env
LLM_PROVIDER=mock
LLM_BASE_URL=
LLM_API_KEY=
LLM_MODEL=gpt-4o-mini
LLM_TIMEOUT_SECONDS=120
```

说明：

- `LLM_PROVIDER=mock`：使用内置 Mock LLM，适合本地开发和测试。
- `LLM_PROVIDER=openai-compatible`：使用 OpenAI-compatible `/chat/completions` 接口。
- `LLM_BASE_URL`：例如 `http://localhost:1234/v1`
- `LLM_API_KEY`：接口鉴权用 key
- `LLM_MODEL`：请求模型名
- `LLM_TIMEOUT_SECONDS`：HTTP 超时秒数

## 运行

安装完成后可以直接运行：

```bash
manus-mini
```

默认 TUI 使用 `prompt_toolkit` 输入层，中文输入法上屏后可以直接进入输入区。输入完成后按 `Enter` 发送，按 `Shift+Enter` 换行，按 `Ctrl-C` 退出。

当前版本已经包含最小可运行的 TUI 会话骨架和可测试的 Agent Runtime。
