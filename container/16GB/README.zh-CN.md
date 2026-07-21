# 16GB 规格

请先按照 `../README.zh-CN.md` 完成环境准备和镜像构建。

- 模型：INT4 AWQ G32
- 目标 GPU：1 × 16GB 主模型 + 3 × 16GB / 3 个 API
- `MAX_MODEL_LEN=32768`
- 主模型显存利用率：物理 16GB GPU 的 `0.95`
- 摘要模型上下文：8,192 token，显存利用率 `0.95`
- ASR/TTS 显存利用率：`0.60` / `0.90`
- 记忆：中期 3 块，长期 1 块
- 摘要上限：3,000 token
- `CHUNK=70`

```bash
cp container/16GB/.env.example container/16GB/.env
./container/manage.sh 16GB up
```

将 `MAIN_MODEL_ROOT` 指向包含 `int4_awq_g32` 的目录。
