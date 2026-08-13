# JoyVL 公网 Nginx 部署

该目录提供 HTTPS 反向代理、Basic Auth、大文件上传、WebSocket，以及 LiveKit 公网媒体端口配置。Nginx 不设置带宽或请求速率限制，上传请求也不在 Nginx 中落盘缓冲。

Nginx 的 `client_max_body_size` 为无限制；应用层当前通过 `VIDEO_UPLOAD_MAX_BYTES=10737418240` 将单文件限制为 10 GiB，可在 `nginx/livekit.env` 中调整。该配置没有 `limit_rate` 或 `limit_req`，不会主动限制 10 Gbps 链路。

## 1. 初始账号

初始账号保存在本机 `nginx/.initial-credentials`，密码哈希保存在 `nginx/.htpasswd`。这两个文件均被 Git 忽略。

修改密码：

```bash
bash nginx/set_password.sh joyvl
sudo install -m 0640 -o root -g www-data nginx/.htpasswd /etc/nginx/joyvl/.htpasswd
sudo nginx -t
sudo systemctl reload nginx
```

## 2. 准备 HTTPS 证书

公网应使用与域名匹配的可信证书。假设证书路径为：

```text
/etc/letsencrypt/live/vl.example.com/fullchain.pem
/etc/letsencrypt/live/vl.example.com/privkey.pem
```

当前 WebUI 自签名证书只用于 Nginx 到 `127.0.0.1:7099` 的内部连接，不应作为公网证书。

## 3. 后端和 LiveKit 配置

本机已经生成了 Git 忽略的 `nginx/livekit.env`，其中包含随机 LiveKit API secret，并将 WebUI 后端绑定到 `127.0.0.1:7099`。Nginx 监听内网 `7100`，公网入口 `7099` 需要通过 NAT/端口映射转发到内网 Nginx `7100`。其他部署机器可以从模板创建：

```bash
cp nginx/livekit-public.env.example nginx/livekit.env
chmod 600 nginx/livekit.env
```

只使用上传视频时，不需要设置 LiveKit 公网 IP，也不需要开放 `7299`。浏览器视频帧通过 HTTPS/WSS `7099` 发送到 WebUI，LiveKit 只在服务器内部使用。

需要公网摄像头/WebRTC 时，在 `nginx/livekit.env` 中设置：

```bash
LIVEKIT_NODE_IP=你的公网IPv4
LIVEKIT_USE_EXTERNAL_IP=false
```

如果服务器拥有可自动探测的公网网卡，也可以使用 `LIVEKIT_USE_EXTERNAL_IP=true`。固定公网 IP 或一对一 NAT 更建议显式设置 `LIVEKIT_NODE_IP`。

重启 WebUI 和 LiveKit：

```bash
bash nginx/restart_webui_livekit.sh
```

LiveKit 与 WebUI 是同一启动脚本管理的进程组，因此修改 LiveKit 监听地址或端口后需要一起重启。配置生成在 `services/webui/.livekit/livekit.yaml`，日志位于 `services/webui/.livekit/livekit.log`。

WebUI 和 Nginx 使用不同端口：WebUI 为 `127.0.0.1:7099`，Nginx 为内网 `7100`，两者不会争用监听端口。

## 4. 安装 Nginx 配置

```bash
sudo bash nginx/install.sh \
  vl.example.com \
  /etc/letsencrypt/live/vl.example.com/fullchain.pem \
  /etc/letsencrypt/live/vl.example.com/privkey.pem
```

安装后配置公网 `7099/TCP -> <内网服务器IP>:7100/TCP`，然后访问 `https://vl.example.com:7099/`。浏览器会先要求输入 Basic Auth 用户名和密码。`/ws`、上传 API、TTS WebSocket 和 `/livekit` 信令都受同一认证保护。

## 5. 公网端口

仅上传视频：

| 协议 | 公网端口 | 转发目标 | 用途 |
| --- | ---: | --- | --- |
| TCP | 7099 | 内网 Nginx `7100` | HTTPS 页面、上传、API、WSS |

启用公网摄像头/WebRTC 时额外开放：

| 协议 | 公网端口 | 转发目标 | 用途 |
| --- | ---: | --- | --- |
| UDP | 7299 | 本机 UDP `7299` | LiveKit WebRTC 媒体，主要链路 |
| TCP | 7299 | 本机 TCP `7299` | UDP 不可用时的媒体回退 |

不要向公网开放：

- `7099/TCP`（内网 WebUI）：WebUI 后端只监听 `127.0.0.1`，不能从网络直接访问。公网同号端口 `7099` 映射到内网 Nginx `7100`，不是映射到 WebUI。
- `7100/TCP`：Nginx 的内网入口，只应允许公网 NAT 网关或可信内网访问。
- `8298/TCP`：LiveKit 信令只监听 `127.0.0.1`，由 WebUI 的 `/livekit` 代理。
- VLM、ASR、TTS 和其他模型服务端口：只允许本机或可信内网访问。

若服务器位于 NAT 后，最稳妥的映射是公网 `7299/UDP -> 内网 7299/UDP`，以及公网 `7299/TCP -> 内网 7299/TCP`。LiveKit 通告的公网 IP 和端口必须与 NAT 映射一致。

## 6. 重载和排查

```bash
sudo nginx -t
sudo systemctl reload nginx
ss -lntup | grep -E ':(7099|7100|7299|8298)\b'
tail -f /var/log/nginx/joyvl-error.log
tail -f services/webui/.livekit/livekit.log
```
