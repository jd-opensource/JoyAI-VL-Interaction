# JoyAI-VL-Interaction

> Chinese documentation: [api.zh-CN.md](./api.zh-CN.md)

JoyAI-VL-Interaction is JoyAI's in-house real-time video stream interaction model. It supports video frame input, session state management, and output normalization.

## API Information

| Item | Value |
| --- | --- |
| Base URL | `https://joyrouter.jd.com` |
| Endpoint | `POST /v1/chat/completions` |
| Session reset | `POST /v1/streaming/reset` |
| Model name | `JoyAI-VL-Interaction` |
| Protocol | OpenAI Chat Completions compatible |
| Streaming | Not supported (synchronous JSON only) |
| Authentication | `Authorization: Bearer $JOYAI_API_KEY` |

## Key Features

- **Video frame input**: Supports single-frame and batch-frame input. A time range can be specified for each frame.
- **Session state management**: Uses the `x-streaming-session` header to isolate sessions and accumulate memory.
- **Output normalization**: Normalizes model output to either `</silence>` (no response) or `</response> one sentence` (response).

## Request Parameters

### Headers

| Header | Required | Description |
| --- | --- | --- |
| `Content-Type` | Yes | `application/json` |
| `Authorization` | Yes | `Bearer $JOYAI_API_KEY` |
| `x-streaming-session` | No | Session ID. Providing one is strongly recommended; otherwise, requests share the default session. Only letters, digits, `._-`, and a maximum of 120 characters are allowed. |
| `x-system-prompt-key` | No | Selects the system prompt: `DEFAULT_SYSTEM_PROMPT_EN` (with delegation) or `DEFAULT_SYSTEM_PROMPT_NO_DELEGATION` (without delegation). |
| `x-frame-time-range` | No | Time range for a single frame, for example, `"10.0 seconds ~ 12.0 seconds"`. |

### Body

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `messages` | array | Yes | Standard OpenAI messages array. The text in the last user message is used as the user's question. |
| `model` | string | No | Backend model name. The default backend is used when omitted. |
| `frame_time_ranges` | string[] | No | Array of time ranges for batch frames, in the same order as the images. |
| `max_tokens` | int | No | Maximum number of tokens to generate. |
| `temperature` | float | No | Sampling temperature. |
| `top_p` | float | No | Nucleus sampling probability. |

### Image Input Format

Each image is an item in the `content` array. A single user message may contain multiple images for batch-frame input:

```json
{ "type": "image_url", "image_url": { "url": "data:image/jpeg;base64,/9j/4AAQ..." } }
```

### Time Range Resolution Priority

1. The `frame_time_ranges` array in the request body, mapped to images in order
2. The `x-frame-time-range` header, used as a single value for the first frame
3. A `<X seconds ~ Y seconds>` pattern extracted from the prompt text
4. An automatically generated range based on `FRAME_SECONDS` (default: 1.0 second)

## Examples

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
        {"type": "text", "text": "Who is in the scene, and what are they doing?"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,/9j/4AAQ..."}}
      ]
    }]
  }'
```

### Python

```python
import base64

import requests

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
            {"type": "text", "text": "Who is in the scene, and what are they doing?"},
            {"type": "image_url", "image_url": {"url": image_to_data_url("frame.jpg")}}
        ]
    }]
})

print(resp.json()["choices"][0]["message"]["content"])
```

### Batch Frames

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
        {"type": "text", "text": "What changed between these two frames?"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
      ]
    }]
  }'
```

## Response Format

```json
{
  "id": "chatcmpl-a1b2c3d4...",
  "object": "chat.completion",
  "model": "streaming-infer-adapter",
  "choices": [{
    "index": 0,
    "message": { "role": "assistant", "content": "</response> A man is working on a laptop at a desk." },
    "finish_reason": "stop"
  }],
  "usage": { "prompt_tokens": 1200, "completion_tokens": 30, "total_tokens": 1230 },
  "streamingharness": {
    "main_model": "JoyAI-VL-Interaction",
    "raw_content": "</response> A man is working on a laptop at a desk.",
    "timing": { "adapter_total_ms": 150.3, "vllm_inference_ms": 115.0 }
  }
}
```

### Output Normalization Rules

The `content` field is normalized to one of two possible results:

| Output | Meaning | Trigger |
| --- | --- | --- |
| `</silence>` | The model chooses not to respond. | There is no user question, the main model output contains `</silence>`, or the output is empty. |
| `</response> <one sentence>` | The model chooses to respond. | The main model output contains `</response>`; the first line following it is used as the response. |

The raw main model output is available in `streamingharness.raw_content`.

## Session Management

### Session ID Resolution Priority

1. The `x-streaming-session` header
2. The `x-session-id` header
3. The `user` field in the request body
4. `"default"` as the fallback

### Reset a Session

```bash
curl -X POST https://joyrouter.jd.com/v1/streaming/reset?model=JoyAI-VL-Interaction \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $JOYAI_API_KEY" \
  -H "x-streaming-session: session-001" \
  -d '{}'
```

Response: `{ "ok": true, "session_id": "session-001", "removed": true }`

## Notes

- **No streaming support**: All endpoints return synchronous JSON and do not support SSE.
- **Session isolation**: Providing `x-streaming-session` is strongly recommended. Otherwise, requests share the default session and state may leak between clients.
- **Text-only requests**: Requests without images are forwarded directly to the main model. The response does not include the extended `timing` or `memory` fields.
- **Silence when no question is provided**: If the request contains no user question text, the API returns `</silence>` immediately.
