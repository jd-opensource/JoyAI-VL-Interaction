# 常规规格

> 原文档: [README.md](./README.md)

请先按照 `../README.zh-CN.md` 完成环境准备和镜像构建。

- 模型：BF16 `JoyAI-VL-Interaction-Preview`
- 目标 GPU：1 × 32GB 主模型 + 3 × 32GB / 3 个 API
- `MAX_MODEL_LEN=67174`（`65.6 × 1024`，向下取整数）
- 主模型 GPU 配额：`0.95`
- 记忆：中期 5 块，长期 2 块
- 输入频率：1 FPS，每次请求最多 1 帧
- 硬件基准：32GB GPU；可根据实际显存调整上下文长度和显存利用率

```bash
cp container/regular/.env.example container/regular/.env
./container/manage.sh regular up
```

模型路径、GPU、上下文长度和记忆限制均在 `.env` 中修改。
