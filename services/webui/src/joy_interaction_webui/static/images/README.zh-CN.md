# 图片目录

> 原文档: [README.md](README.md)

由 WebUI 提供服务的图片资源。

## 目录结构

```text
images/
├── simple-logo.svg
├── *.png
└── README.md
```

## 使用方式

图片通过以下地址提供：`https://yourserver:8090/images/filename.png`

## 资源规范

### 格式与大小

- **格式：** PNG（带透明度）、WebP，或在合适时使用 SVG
- **尺寸：** 图标源资源推荐 128x128px 或 256x256px
- **文件大小：** 保持足够小，避免拖慢 WebUI 首屏加载路径
- **背景：** 当资源作为图标使用时，应使用透明背景

### 文件命名

使用简单、小写、带连字符的文件名。

## 优化

添加图片前：

1. 调整到合适尺寸。
2. 使用 `pngquant`、`cwebp` 或 ImageOptim 等工具优化。
3. 对较大的位图资源，在浏览器支持可接受时优先使用 WebP。
