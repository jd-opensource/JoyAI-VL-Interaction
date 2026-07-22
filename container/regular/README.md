# Regular Profile

> 中文文档: [README.zh-CN.md](./README.zh-CN.md)

Complete the prerequisites and image build in `../README.md` first.

- Model: BF16 `JoyAI-VL-Interaction`
- Target GPUs: 1 × 32GB main + 3 × 32GB / 3 APIs
- `MAX_MODEL_LEN=67174` (`65.6 × 1024`, rounded down to an integer)
- Main GPU allocation: `0.95`
- Memory: 5 mid-term blocks and 2 long-term blocks
- Input rate: 1 FPS, with at most one frame per request
- Hardware baseline: 32GB GPU; adjust context length and GPU allocation to match available VRAM

```bash
cp container/regular/.env.example container/regular/.env
./container/manage.sh regular up
```

Edit `.env` to change model paths, GPU IDs, context length, or memory limits.
