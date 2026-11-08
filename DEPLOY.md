# BuildQuote 上线部署指南

> 目标：把「成本监控工作台」跑在服务器上，别人用浏览器即可访问。
> 适用于：公司内网服务器 / 云主机（Linux，2C4G 起）/ 本机临时演示。

## 一、准备配置（必须）

1. 复制配置模板：`cp .env.example .env`
2. 填 3 个必填项：
   - `MODEL_API_KEY`：DeepSeek API Key（合同问答 / 合同分析 / 联网摘要使用）
   - `DATA_KEY`：AES-256-GCM 数据加密密钥，`openssl rand -hex 32` 生成
   - `SESSION_SECRET`：会话密钥，`openssl rand -hex 32` 生成
3. 可选（公网强烈建议）：在 `.env` 增加访问密码
   ```
   APP_USERNAME=buildquote
   APP_PASSWORD=换成你自己的强密码
   ```
   开启后浏览器访问会弹 HTTP Basic 登录框。
4. 不要把 `.env` 提交到 Git、不要发到聊天工具里（内含真实 Key）。

## 二、方式 A：服务器 + Docker（推荐长期使用）

```bash
# 服务器安装 docker / docker compose 后
git clone <你的仓库地址> buildquote && cd buildquote
cp .env.example .env && vi .env     # 填配置
docker compose up -d --build
docker compose logs -f              # 看到 Uvicorn running 即成功
curl http://127.0.0.1:8010/health   # {"status":"ok",...}
```

- 访问：`http://服务器IP:8010`
- 数据持久化：`data/` 目录（SQLite 库、向量库、合同原件、评测数据都在这里），已在 compose 中挂载
- 升级：`git pull && docker compose up -d --build`
- 备份：定期复制 `data/`（关键是 `buildquote.db`、`buildquote_vectors.db`、`contracts/`）

### 配域名 + HTTPS（Caddy 最简单）

```caddyfile
buildquote.example.com {
    reverse_proxy 127.0.0.1:8010
}
```
Caddy 会自动申请证书；用 Nginx 的话：

```nginx
server {
    listen 80;
    server_name buildquote.example.com;
    client_max_body_size 30m;
    location / {
        proxy_pass http://127.0.0.1:8010;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## 三、方式 B：本机临时公网演示（无需服务器）

用于给同事/客户临时看效果，**URL 会变、不适合长期**：

```bash
cd BuildQuote
cp .env.example .env && vi .env          # 填配置
.venv/bin/python -m app.serve            # 本机 8010 启动
# 另开终端，起临时公网隧道（Serveo，免费）
ssh -p 443 -o StrictHostKeyChecking=no -R 80:127.0.0.1:8010 serveo.net
# 输出里会给出 https://xxxx.serveousercontent.com 这样的地址
```

- 打开该地址，首次会出现 "Serveo Browser Warning"，点 **Continue to Site** 即可进入
- 也可以换 Cloudflare 隧道（需 `cloudflared` 二进制）：`cloudflared tunnel --url http://127.0.0.1:8010`

## 四、运行注意

- **单 worker**：SQLite + 本地向量库 + 定时催收都是单进程设计，请勿开多 worker
- 首次启动会下载本地向量模型（约 100MB，需要外网）；之后走缓存
- 健康检查：`GET /health`；日志：`docker compose logs -f`
- 上传限制：合同 ≤ 20MB、报价单 ≤ 10MB（代码内已限制）
- 扫描件 PDF 需要先 OCR；合同/报价自动识别后仍建议人工核对

## 五、常见问题

| 现象 | 排查 |
| --- | --- |
| 启动报「缺少必需配置」 | `.env` 没填 `MODEL_API_KEY` / `DATA_KEY` / `SESSION_SECRET` |
| 页面能开但问答/分析报错 | `MODEL_API_KEY` 无效、DeepSeek 余额不足或服务器出网受限 |
| 打开是登录框 | 已设置 `APP_USERNAME/APP_PASSWORD`，输入即可 |
| 数据为空 | `data/` 挂载错误，或换了目录启动导致读到新空库 |
| 合同分析很慢 | 首次调用模型 5–10 秒正常，结果会缓存 |
