# JoyAI-VL-Interaction 使用说明

> 原文档: [README.md](./README.md)

数据集：https://huggingface.co/datasets/jdopensource/JoyAI-VL-Interaction

### 1. 下载标注

```bash
hf download hf://datasets/jdopensource/JoyAI-VL-Interaction \
  --local-dir ./JoyAI-VL-Interaction
```

### 2. 数据格式

使用 `source` 字段定位原始视频，然后在运行转换前，将 `video_path` 填写为视频在本地的绝对路径。

```json
[
  {
    "video_name": "name.mp4",
    "video_path": "/path/to/videos/name.mp4",
    "task_type": "task_category",
    "source": "source_dataset",
    "question": [
      {"content": "user question or instruction", "time": "4"}
    ],
    "response": [
      {"content": "expected answer or event response", "time": "7"}
    ]
  }
]
```

- `question`：用户提示列表。`content` 是提示文本，`time` 是发出提示时的时间戳，单位为秒。
- `response`：真实响应列表。`content` 是预期答案，`time` 是答案或事件应当出现的时间戳，单位为秒。

### 3. 运行转换

```bash
python ./convert_data.py \
  --input /path/to/JoyAI-VL-Interaction/annotations_with_video_path \
  --output /path/to/JoyAI-VL-Interaction/processed_data \
  --frame_dir /path/to/JoyAI-VL-Interaction/images \
  --log_dir /path/to/JoyAI-VL-Interaction/logs \
  --max_duration MAX_DURATION_SECONDS \
  --workers NUM_WORKERS
```

### 4. 转换后的输出格式

脚本会为每一秒创建一组 user/assistant 消息。如果问题从第 4 秒开始，预期响应出现在第 7 秒，转换后的消息如下：

```json
[
  {
    "messages": [
      {"role": "user", "content": "<0.0 seconds>\n<image>"},
      {"role": "assistant", "content": "</silence>"},
      {"role": "user", "content": "<1.0 seconds>\n<image>"},
      {"role": "assistant", "content": "</silence>"},
      {"role": "user", "content": "<2.0 seconds>\n<image>"},
      {"role": "assistant", "content": "</silence>"},
      {"role": "user", "content": "<3.0 seconds>\n<image>"},
      {"role": "assistant", "content": "</silence>"},
      {"role": "user", "content": "user question or instruction\n<4.0 seconds>\n<image>"},
      {"role": "assistant", "content": "</silence>"},
      {"role": "user", "content": "<5.0 seconds>\n<image>"},
      {"role": "assistant", "content": "</silence>"},
      {"role": "user", "content": "<6.0 seconds>\n<image>"},
      {"role": "assistant", "content": "</silence>"},
      {"role": "user", "content": "<7.0 seconds>\n<image>"},
      {"role": "assistant", "content": "</response> expected answer or event response"}
    ],
    "images": [
      "/path/to/images/task_category/source_dataset/name/frame_000000.jpg",
      "/path/to/images/task_category/source_dataset/name/frame_000001.jpg",
      "/path/to/images/task_category/source_dataset/name/frame_000002.jpg",
      "/path/to/images/task_category/source_dataset/name/frame_000003.jpg"
    ],
    "video_name": "name.mp4",
    "video_path": "/path/to/videos/name.mp4",
    "task_type": "task_category",
    "source": "source_dataset"
  }
]
```
