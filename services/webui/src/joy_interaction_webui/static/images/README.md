# Images Directory

> 中文文档: [README.zh-CN.md](README.zh-CN.md)

Image assets served by the WebUI.

## Directory Structure

```text
images/
├── simple-logo.svg
├── *.png
└── README.md
```

## Usage

Images are served at: `https://yourserver:8090/images/filename.png`

## Asset Guidelines

### Format & Size

- **Format:** PNG with transparency, WebP, or SVG when appropriate
- **Dimensions:** Prefer 128x128px or 256x256px source assets for icons
- **File Size:** Keep small enough for the initial WebUI load path
- **Background:** Transparent when the asset is used as an icon

### File Naming

Use simple, lowercase names with hyphens.

## Optimization

Before adding images:

1. Resize to appropriate dimensions.
2. Optimize with tools like `pngquant`, `cwebp`, or ImageOptim.
3. Prefer WebP for larger bitmap assets when browser support is acceptable.
