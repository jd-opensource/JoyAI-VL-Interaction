# 24GB 规格

请先按照 `../README.zh-CN.md` 完成环境准备和镜像构建。

- 模型：INT4 AWQ G32
- 目标 GPU：1 × 24GB 主模型 + 3 × 24GB / 3 个 API
- `MAX_MODEL_LEN=81920`
- 主模型显存利用率：物理 24GB GPU 的 `0.95`
- 摘要模型上下文：8,192 token，显存利用率 `0.95`
- ASR/TTS 显存利用率：`0.40` / `0.60`
- 记忆：中期 5 块，长期 5 块
- `CHUNK=100`

```bash
cp container/24GB/.env.example container/24GB/.env
./container/manage.sh 24GB up
```

将 `MAIN_MODEL_ROOT` 指向包含 `int4_awq_g32` 的目录。
