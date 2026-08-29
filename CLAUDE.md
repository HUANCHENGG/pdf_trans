# CLAUDE.md — pdf_trans 项目约定

电子行业 PDF 翻译 Web 工具（自用）：上传 datasheet/协议手册 → OpenAI 兼容大模型翻译 → 输出保留原版式的双语/纯译文 PDF。规划 [PROJECT_PLAN.md](PROJECT_PLAN.md)，运行架构 [docs/architecture.md](docs/architecture.md)，引擎行为 [docs/m0-findings.md](docs/m0-findings.md)，部署 [docs/deploy.md](docs/deploy.md)。

## 常用命令

```bash
# 日常启动（或双击 start.bat，自动开浏览器带 token）
.venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
# 语法检查
.venv/Scripts/python -m compileall -q app
# 生成测试 PDF / 术语种子
.venv/Scripts/python scripts/make_sample_pdf.py docs/samples/sample_datasheet.pdf
.venv/Scripts/python scripts/seed_terms.py
```

访问 token 在 `data/token.txt`；URL 带 `?token=...` 首访写入 cookie。改完代码需重启服务生效（无热加载）；重启会把遗留 pending/translating 任务标记 cancelled。

## 硬约束（违反即出错，都踩过坑）

1. **Python 固定 3.13 venv**：BabelDOC 要求 `<3.14`，本机系统 Python 是 3.14 不可用；用 `uv venv --python 3.13 .venv`
2. **`async_translate` 事件流收到 `finish` 必须 `break`**——引擎不会主动结束流，漏 break 任务永久挂起
3. **`watermark_output_mode` 必须显式 `WatermarkOutputMode.NoWatermark`**（引擎默认带水印）
4. **取消必须走协作式**（`_states` 标志 + 进度检查点抛 `CancelRequested`）；`asyncio.Task.cancel()` 会被引擎资源清理阻塞 30s+，禁用
5. **engine.py 的 `on_progress` 调用处不能吞 `ProgressAbort`**（取消信号穿透依赖它）
6. **`db.py` 用 `RLock`**（execute 持锁内调 get_conn 再取锁，Lock 会死锁）
7. **Starlette 新版 `TemplateResponse(request, name, ctx)`**——request 是第一个参数
8. 目录翻译/渲染逻辑在 `app/toc.py`，**必须独立于引擎段落重组**（引擎会把"节号行+点线行"合并劈裂，破坏目录）
9. 修改 `tasks.py` 后检查 `dry_run` 等变量作用域（曾因 walrus 在条件分支内定义导致 NameError）

## 结构速查

- `app/engine.py` 引擎封装 / `app/tasks.py` 队列+进度 / `app/toc.py` 目录管线 / `app/coverage.py` 覆盖度报告 / `app/db.py` SQLite(tasks/terms) / `app/config.py` 配置（端点档案在 `data/settings.json`）
- 输出 PDF 在 `data/outputs/<task_id>/`；上传件在 `data/uploads/<id>.pdf`（retry 复用）
- 引擎自带翻译缓存与批处理——不要重复自研；QPS 在端点级配置（大文档速度旋钮）
- 测试翻译用 `pages_spec=1-5` + 真手册 `SDS13782-4 *.pdf`（根目录），成本几分钱；不调 API 的管线验证勾选 dry_run

## 用户偏好

中文交流；电子行业工程师（术语/引脚名/型号绝不能被翻译——`source==target` 术语条目即约定不翻）；测试认可后才算完成；每轮改动都要真实跑任务验证，不只编译通过。
