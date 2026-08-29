# 服务器部署指南（Docker）

## 快速部署

```bash
# 服务器上（需已装 Docker 与 docker compose）
git clone <你的仓库> pdf-trans && cd pdf-trans   # 或仅拷贝项目文件（不含 .venv/data）

# 可选：固定访问 token（不设则首次启动自动生成，见 data/token.txt）
export PDFTRANS_TOKEN=你的私密token

docker compose up -d --build

# 查看自动生成的 token
cat data/token.txt
```

浏览器访问 `http://<服务器IP>:8765/?token=<token>`，首次打开即写入 cookie。

## 安全清单（公网部署必读）

- **务必设置 `PDFTRANS_TOKEN`** 或确认 `data/token.txt` 已生成且随机——服务无账号体系，token 是唯一防线
- token 会出现在 URL 中（首次），建议用私密窗口首次访问（写入 cookie 后用干净地址）
- 不需要公网时，用防火墙/安全组限制 8765 端口来源 IP，或仅走 SSH 隧道：`ssh -L 8765:127.0.0.1:8765 user@server`
- 上传大小默认上限 200MB（环境变量 `PDFTRANS_MAX_UPLOAD_MB` 可调）；不限制页数，超过 `PDFTRANS_LARGE_DOC_PAGES`（默认 500）页时任务页仅提示翻译耗时可能较久

## 离线服务器（无法访问外网）

引擎需要布局模型与中文字体资产（首次约数百 MB）。联网机器上打包后带入：

```bash
# 联网机器上
python -m babeldoc --generate-offline-assets ./offline_assets

# 离线服务器上（容器内）
docker cp offline_assets pdf-trans:/tmp/assets
docker exec pdf-trans python -m babeldoc --restore-offline-assets /tmp/assets
docker restart pdf-trans
```

## nginx 反向代理（可选）

SSE 进度推送需要关闭缓冲：

```nginx
location / {
    proxy_pass http://127.0.0.1:8765;
    proxy_set_header Host $host;
    proxy_buffering off;          # SSE 必需
    proxy_read_timeout 3600s;
    client_max_body_size 220m;    # 大文件上传
}
```

## 升级

```bash
docker compose up -d --build   # data/ 卷持久化任务与术语表，升级不丢数据
```
