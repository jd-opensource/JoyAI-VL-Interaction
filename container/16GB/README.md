# 16GB Profile

> 中文文档: [README.zh-CN.md](./README.zh-CN.md)

Complete the prerequisites and image build in `../README.md` first.

- Model: INT4 AWQ G32
- Target GPUs: 1 × 16GB main + 3 × 16GB / 3 APIs
- `MAX_MODEL_LEN=32768`
- Main GPU utilization: `0.95` on a physical 16GB GPU
- Summary context: 8,192 tokens at `0.95`
- ASR/TTS GPU utilization: `0.60` / `0.90`
- Memory: 3 mid-term blocks and 1 long-term block
- Summary limit: 3,000 tokens
- `CHUNK=70`

```bash
cp container/16GB/.env.example container/16GB/.env
./container/manage.sh 16GB up
```

Set `MAIN_MODEL_ROOT` to the directory containing `int4_awq_g32`.
