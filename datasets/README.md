# JoyAI-VL-Interaction Usage

Dataset: https://huggingface.co/datasets/jdopensource/JoyAI-VL-Interaction

### 1. Download Annotations

```bash
hf download hf://datasets/jdopensource/JoyAI-VL-Interaction \
  --local-dir ./JoyAI-VL-Interaction
```

### 2. Data Format

Use the `source` field to locate the original video, then fill `video_path` with the absolute local path before running conversion.

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
- `question`: user prompt list. `content` is the prompt text, and `time` is the timestamp in seconds when the prompt is issued.
- `response`: ground-truth response list. `content` is the expected answer, and `time` is the timestamp in seconds when the answer or event should occur.

### 3. Run Conversion

```bash
python ./convert_data.py \
  --input /path/to/JoyAI-VL-Interaction/annotations_with_video_path \
  --output /path/to/JoyAI-VL-Interaction/processed_data \
  --frame_dir /path/to/JoyAI-VL-Interaction/images \
  --log_dir /path/to/JoyAI-VL-Interaction/logs \
  --max_duration MAX_DURATION_SECONDS \
  --workers NUM_WORKERS
```


### 4. Converted Output Format

The script creates one user/assistant pair for each second. If a question starts at second 4 and the response is expected at second 7, the converted messages look like this:

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
