# JoyAI-VL-Interaction

> 原文档: [api.md](./api.md)

JoyAI-VL-Interaction 是 JoyAI 自研的实时视频流交互模型，支持视频帧输入、会话状态管理与输出归一化。

## 接口信息

| 项目 | 值 |
| --- | --- |
| Base URL | `https://joyrouter.jd.com` |
| 端点 | `POST /v1/chat/completions` |
| 会话重置 | `POST /v1/streaming/reset` |
| 模型名 | `JoyAI-VL-Interaction` |
| 协议 | OpenAI Chat Completions 兼容 |
| 流式 | 不支持（同步 JSON） |
| 鉴权 | `Authorization: Bearer $JOYAI_API_KEY` |

## 核心特性

- **视频帧输入**：支持单帧和批量帧输入，每帧可指定时间范围
- **会话状态管理**：通过 `x-streaming-session` header 隔离不同会话，支持记忆累积
- **输出归一化**：模型输出统一归一化为 `</silence>`（沉默）或 `</response> 一句话`（回复）

## 请求参数

### Headers

| Header | 必填 | 说明 |
| --- | --- | --- |
| `Content-Type` | 是 | `application/json` |
| `Authorization` | 是 | `Bearer $JOYAI_API_KEY` |
| `x-streaming-session` | 否 | 会话 ID，强烈建议传，否则共享 default 会话。仅允许字母、数字、`._-`，最长 120 字符 |
| `x-system-prompt-key` | 否 | 系统提示选择：`DEFAULT_SYSTEM_PROMPT_EN`（带 delegation）/ `DEFAULT_SYSTEM_PROMPT_NO_DELEGATION`（不带） |
| `x-frame-time-range` | 否 | 单帧时间范围，如 `"10.0 seconds ~ 12.0 seconds"` |

### Body

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `messages` | array | 是 | OpenAI 标准消息数组，最后一条 user 消息的文本作为用户问题 |
| `model` | string | 否 | 后端模型名，不传时使用默认后端 |
| `frame_time_ranges` | string[] | 否 | 批量帧的时间范围数组，与图片一一对应 |
| `max_tokens` | int | 否 | 最大生成 token 数 |
| `temperature` | float | 否 | 采样温度 |
| `top_p` | float | 否 | 核采样概率 |

### 图片输入格式

每张图片作为 `content` 数组中的一项，一个 user 消息可包含多张图片（批量帧）：

```json
{ "type": "image_url", "image_url": { "url": "data:image/jpeg;base64,/9j/4AAQ..." } }
```

### 时间范围解析优先级

1. Body `frame_time_ranges` 数组（按图片顺序对应）
2. Header `x-frame-time-range`（单值，用于第一帧）
3. 从 prompt 文本中提取的 `<X seconds ~ Y seconds>` 模式
4. 根据 `FRAME_SECONDS`（默认 1.0s）自动生成

## 调用示例

### curl

```bash
curl -X POST https://joyrouter.jd.com/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $JOYAI_API_KEY" \
  -H "x-streaming-session: session-001" \
  -d '{
    "model": "JoyAI-VL-Interaction",
    "messages": [{
      "role": "user",
      "content": [
        {"type": "text", "text": "画面里有什么人？在做什么？"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,/9j/4AAQ..."}}
      ]
    }]
  }'
```

### Python

```python
import base64, requests

URL = "https://joyrouter.jd.com/v1/chat/completions"
API_KEY = "your-api-key"

def image_to_data_url(path):
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return f"data:image/jpeg;base64,{b64}"

resp = requests.post(URL, headers={
    "Content-Type": "application/json",
    "Authorization": f"Bearer {API_KEY}",
    "x-streaming-session": "session-001"
}, json={
    "model": "JoyAI-VL-Interaction",
    "messages": [{
        "role": "user",
        "content": [
            {"type": "text", "text": "画面里有什么人？在做什么？"},
            {"type": "image_url", "image_url": {"url": image_to_data_url("frame.jpg")}}
        ]
    }]
})

print(resp.json()["choices"][0]["message"]["content"])
```

### 批量帧

```bash
curl -X POST https://joyrouter.jd.com/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $JOYAI_API_KEY" \
  -H "x-streaming-session: session-001" \
  -d '{
    "model": "JoyAI-VL-Interaction",
    "frame_time_ranges": ["10.0 seconds ~ 11.0 seconds", "11.0 seconds ~ 12.0 seconds"],
    "messages": [{
      "role": "user",
      "content": [
        {"type": "text", "text": "这两帧之间发生了什么变化？"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
      ]
    }]
  }'
```

## 响应格式

```json
{
  "id": "chatcmpl-a1b2c3d4...",
  "object": "chat.completion",
  "model": "streaming-infer-adapter",
  "choices": [{
    "index": 0,
    "message": { "role": "assistant", "content": "</response> 画面中有一名男子正在桌前操作笔记本电脑。" },
    "finish_reason": "stop"
  }],
  "usage": { "prompt_tokens": 1200, "completion_tokens": 30, "total_tokens": 1230 },
  "streamingharness": {
    "main_model": "JoyAI-VL-Interaction",
    "raw_content": "</response> 画面中有一名男子正在桌前操作笔记本电脑。",
    "timing": { "adapter_total_ms": 150.3, "vllm_inference_ms": 115.0 }
  }
}
```

### 输出归一化规则

`content` 字段经过归一化，只有两种结果：

| 输出 | 含义 | 触发条件 |
| --- | --- | --- |
| `</silence>` | 模型选择沉默 | 无用户问题；或主模型输出包含 `</silence>`；或输出为空 |
| `</response> <一句话>` | 模型选择回复 | 主模型输出包含 `</response>`，取其后第一行作为回复 |

主模型原始输出保存在 `streamingharness.raw_content` 中。

## 会话管理

### 会话 ID 解析优先级

1. Header `x-streaming-session`
2. Header `x-session-id`
3. Body `user` 字段
4. `"default"`（兜底）

### 重置会话

```bash
curl -X POST https://joyrouter.jd.com/v1/streaming/reset?model=JoyAI-VL-Interaction \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $JOYAI_API_KEY" \
  -H "x-streaming-session: session-001" \
  -d '{}'
```

响应：`{ "ok": true, "session_id": "session-001", "removed": true }`

## 注意事项

- **不支持流式**：所有接口均为同步 JSON，不支持 SSE。
- **会话隔离**：强烈建议传 `x-streaming-session`，否则共享 default 会话导致状态串台。
- **纯文本请求**：无图片时直接转发到主模型，响应不含 `timing`/`memory` 扩展字段。
- **无问题时返回 silence**：请求中不含用户问题文本时，直接返回 `</silence>`。
