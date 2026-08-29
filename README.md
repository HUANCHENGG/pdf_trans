# PDF 电子手册翻译工具

面向单片机/电子行业数据手册、协议手册的 AI 翻译 Web 工具。接入自定义 OpenAI 兼容大模型 API，输出保持原版式的双语对照 PDF。

规划文档：[PROJECT_PLAN.md](PROJECT_PLAN.md) | 运行架构：[docs/architecture.md](docs/architecture.md) | 对抗审查：[docs/adversarial_review.md](docs/adversarial_review.md) | M0 引擎探测：[docs/m0-findings.md](docs/m0-findings.md)

## 快速开始（本地 Windows）

**日常启动**：双击 `start.bat`——自动打开浏览器（已带 token），关闭窗口即停止服务。

首次安装（只需一次）：

```bash
# 1. 创建 venv（要求 Python <3.14，推荐 3.13；本机有 uv 可自动拉取）
uv venv --python 3.13 .venv

# 2. 安装依赖
uv pip install --python .venv/Scripts/python.exe -r requirements.txt

# 3. 下载引擎模型资产（首次一次性，约数百 MB）
.venv/Scripts/babeldoc --warmup

# 4. 初始化预置术语表（电子行业惯用译法）
.venv/Scripts/python scripts/seed_terms.py
```

手动启动（等价于 start.bat）：

```bash
.venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
```

启动后终端会打印访问 token（也保存在 `data/token.txt`）。浏览器打开：

```
http://127.0.0.1:8765/?token=<你的token>
```

## 首次真翻译配置

1. 进入 **API 端点** 页，添加 OpenAI 兼容端点：名称、模型名、Base URL（含 `/v1`）、API Key、价格（¥/百万 token，可选，用于费用估算）
2. 进入 **翻译** 页上传 PDF，选择端点；建议**先填试翻页范围 `1-5`** 验证质量，再翻全本
3. 勾选「管线测试」可不调用大模型，仅验证解析与版式重建（免费）

## 功能（v1 当前状态）

- ✅ 上传/任务队列（串行）/SSE 实时进度/单双语输出
- ✅ 共享 token 鉴权（公网部署防滥用）；上传上限 200MB，不限页数（大文档仅提示耗时较久）
- ✅ 多端点多模型管理，任务级选择，任务级 token 用量与费用估算
- ✅ 试翻页范围（`--pages` 原生支持）
- ✅ **目录页保护（目录中文化）**：自动识别目录页（`app/toc.py`），**不走引擎段落重组**——按 y 坐标提取结构化条目（节号/标题/页码/层级缩进），编号协议批量翻译（术语表注入+缺失重试），渲染到双语版目录页右栏：节号缩进、引导点线、右对齐页码全部保留；左右分栏不改变页数，**目录页码与译文页码仍然一一对应**
- ✅ 覆盖度报告：无文字层页/乱码页告警（防静默失败）、图片数量对账（红线自检）、页面重排量化（L3）
- ✅ 术语表：预置 31 条电子行业惯用译法 + Web 增删 + CSV 导出（BabelDOC glossary 兼容），自定义 > 预置
- ✅ 目录中文覆盖校验：约定不翻译词/型号/缩写豁免，其余译文必须含中文，不合格严格重试，结果页统计
- ✅ 批量上传（多文件排队）；失败/取消任务一键用原参数重试
- ✅ 端点级 QPS 并发配置（1-30，大文档翻译速度的关键旋钮；需与 API 供应商限流匹配）
- ✅ 覆盖度报告：无文字层页/乱码页告警（防静默失败）、图片数量对账（红线自检）、中文页面覆盖率
- ⏳ 后续：断点续翻（大文档中断续翻）、正文不翻译规则审计、金标准回归脚本

## 目录结构

```
app/
  main.py        FastAPI 装配
  config.py      配置（token/限额/端点档案 settings.json）
  auth.py        共享 token 中间件
  db.py          SQLite（任务表 + 术语表）
  engine.py      BabelDOC 引擎封装（async_translate 事件流）
  tasks.py       串行任务队列 + 进度状态
  coverage.py    覆盖度报告（PyMuPDF 逐页统计 + 输出对账）
  routes/        API 与页面路由
  templates/     Jinja2 页面（翻译/任务/术语表/端点）
scripts/
  make_sample_pdf.py  生成 3 页样例 datasheet（含引脚表/寄存器表/图）
  seed_terms.py       预置术语种子
data/            运行时数据（token.txt、app.db、settings.json、uploads/、outputs/）
```

## 部署到服务器（Docker）

```bash
export PDFTRANS_TOKEN=你的私密token   # 公网部署务必设置
docker compose up -d --build
```

详见 [docs/deploy.md](docs/deploy.md)：安全清单、离线资产包、nginx 反代（SSE 需关缓冲）、升级方式。

## 已知取舍

- API key 明文存于 `data/settings.json`（自用工具取舍，注意数据目录权限）
- 翻译任务串行执行（自用足够；升级路径已预留）
- 引擎不支持 Python 3.14（`requires-python <3.14`），venv 固定 3.13
- 双语模式页面重排（L3）为已知限制，结果页有量化展示
