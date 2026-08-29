FROM python:3.13-slim

WORKDIR /app

# 系统依赖（PyMuPDF/onnxruntime 轮子自带，无需额外系统库；字体走 babeldoc 资产）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts

# 数据目录（token/术语表/任务库/上传与输出）
RUN mkdir -p /app/data
VOLUME ["/app/data"]

ENV PDFTRANS_DATA_DIR=/app/data
EXPOSE 8765

# 首次启动自动预热引擎模型资产（需外网，约数百 MB，仅一次；
# 离线服务器请参考 docs/deploy.md 的离线资产包方式）
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh
ENTRYPOINT ["/app/start.sh"]
