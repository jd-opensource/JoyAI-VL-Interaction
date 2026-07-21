# 24GB Profile

> 中文文档: [README.zh-CN.md](./README.zh-CN.md)

Complete the prerequisites and image build in `../README.md` first.

- Model: INT4 AWQ G32
- Target GPUs: 1 × 24GB main + 3 × 24GB / 3 APIs
- `MAX_MODEL_LEN=81920`
- Main GPU utilization: `0.95` on a physical 24GB GPU
- Summary context: 8,192 tokens at `0.95`
- ASR/TTS GPU utilization: `0.40` / `0.60`
- Memory: 5 mid-term blocks and 5 long-term blocks
- `CHUNK=100`

```bash
cp container/24GB/.env.example container/24GB/.env
./container/manage.sh 24GB up
```

Set `MAIN_MODEL_ROOT` to the directory containing `int4_awq_g32`.
