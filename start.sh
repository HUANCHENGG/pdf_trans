#!/bin/sh
set -e
cd /app

# 首次启动预热布局模型与字体（幂等：已有资产则秒过）
python -c "from babeldoc.assets.assets import warmup; warmup()" || \
  echo "[警告] 引擎资产预热失败，翻译功能可能不可用（检查网络）"

python scripts/seed_terms.py || true

echo "================================================"
echo "  PDF 翻译工具已启动"
TOKEN=$(cat /app/data/token.txt 2>/dev/null || true)
echo "  访问地址: http://<服务器IP>:8765/?token=$TOKEN"
echo "================================================"
exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8765
