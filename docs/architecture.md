# 架构说明

> 面向接手者：本文档解释系统怎么工作、为什么这样设计。安装与日常使用见 [README.md](../README.md)，部署见 [deploy.md](deploy.md)，引擎行为细节见 [m0-findings.md](m0-findings.md)。

## 总体分层

```
Web 前端（Jinja2 + 原生 JS，无构建步骤）
   │ HTTP（共享 token 鉴权，SSE/轮询取进度）
FastAPI 后端（单进程）
   ├─ routes/api.py     JSON/SSE 接口（上传、任务、端点 CRUD、术语表 CRUD、服务状态）
   ├─ routes/pages.py   页面路由（翻译/任务/监控/术语表/端点）
   ├─ auth.py           共享 token 中间件（URL ?token= 首访固化 cookie）
   ├─ tasks.py          串行任务队列 + 进度状态（内存快照 + SQLite 落库）
   ├─ engine.py         BabelDOC 引擎封装（Python API 路径）
   ├─ toc.py            目录专项管线（检测/条目提取/批量翻译/渲染）
   ├─ coverage.py       覆盖度报告（输入体检 + 输出对账 + 中文覆盖率）
   ├─ db.py             SQLite（tasks/terms 两表，RLock）
   └─ config.py         路径/限额/token；端点档案存 data/settings.json
翻译引擎 BabelDOC 0.6.4（async_translate 事件流）→ OpenAI 兼容大模型 API
```

## 任务生命周期

```
pending ──worker 取到──▶ translating ──▶ done
   │                        │   ├─▶ failed（引擎异常/翻译失败）
   └─（排队中取消）──────────┘   └─▶ cancelled（协作式取消）
```

串行执行：`asyncio.Queue` + 单 worker，自用场景足够，天然限流。状态双写：`_states`（内存，供 SSE/轮询快照，含 elapsed/块速/最后活动）+ `tasks` 表（持久）。**服务重启时**内存清空，startup 会把遗留 pending/translating 任务统一标记 cancelled（v1 无断点续翻）。

### 翻译主流程（tasks._run_task）

1. 目录检测（`toc.detect_toc_pages`）：行尾"≥6 连续点+页码"特征 ≥8 行判定为目录页；全书过半命中则判据失效返回空
2. 目录页从引擎范围剔除：pages 集合差运算后压缩为 babeldoc 范围串（如 `1-3,9-177`）
3. **目录条目翻译**（引擎前，见下节）
4. 引擎翻译（`engine.translate`，事件流逐事件更新进度/块速/取消检查点）
5. **目录渲染**（引擎后，写 dual/mono 输出）
6. 覆盖度报告 + token 用量/费用估算

## 目录专项管线（为什么不走引擎）

引擎的段落重组会把"短节号行 + 标题点线行"交替的目录结构合并/劈裂（红线 1 事故模式），且引擎无法保证目录术语一致。因此目录完全脱离引擎：

```
detect_toc_pages → extract_entries（按 y 聚类行 → 节号/标题/页码/缩进/字号）
  → translate_entries（编号 ID 协议批翻译，25 条/批）
      ├─ 术语子集注入（按条目文本命中，避免全量注入）
      ├─ 中文覆盖校验：needs_chinese 豁免（约定不译词/纯数字/短大写缩写），
      │   其余译文必须含中文，不合格以严格指令重试一次，仍失败则丢弃
      └─ 返回 (translations, stats{total,translated,exempt,failed})
  → render_toc_translation（引擎输出后处理）
      ├─ dual 模式：双语版为 2x 加宽横排（1191pt），右栏白底覆盖后按 原坐标+595 重绘
      └─ mono 模式：纯译文版目录页与原 PDF 坐标一致，仅覆盖条目区（页眉页脚保留）
```

关键事实：**双语左右分栏不改变页数**（177 进 177 出），因此目录页码与译文页面仍一一对应，导航功能完整。

## 协作式取消（为什么不能用 asyncio cancel）

`asyncio.Task.cancel()` 对正在跑 `async_translate` 的协程传播极慢——引擎内部线程/进程池的资源清理会阻塞取消传播（实测 >30s）。因此取消是**协作式**：API 置 `_states[id].cancel_requested` → `on_progress` 回调在下一个引擎事件点抛 `CancelRequested`（继承 `engine.ProgressAbort`，后者被 engine.py 特意穿透不被吞）→ worker 捕获后落库 cancelled。响应延迟 = 下一个进度事件的间隔（通常 2~3 秒）。

## 进度监控数据流

引擎事件（`overall_progress` 0~100、`stage_current/stage_total`）→ `_extract_progress` 容错解析 → `_states`。**已用时间是 snapshot() 实时计算的**（不依赖引擎事件频率）；"最后活动"= now - last_event_ago，>60s 前端标红（判断卡住的依据）；块速 = 5 分钟滑动窗口内块号推进速率，用于当前阶段剩余时间估算。

## 数据模型

- **tasks 表**：id/文件/状态/端点快照/页范围/开关（no_mono/use_glossary/dry_run/protect_toc）/进度/输出路径/coverage(JSON)/usage(JSON)/skipped_pages/时间戳
- **terms 表**：source/target/category/is_custom；UNIQUE(source,is_custom)；优先级 自定义 > 预置；`source==target` 的词条语义为"约定不翻译"
- **settings.json**：端点档案（name/base_url/model/api_key 明文/价格/qps）+ default_endpoint

## 引擎集成要点（坑）

- `async_translate` 事件流收到 `finish` **必须 break**，引擎不会主动结束流
- `watermark_output_mode` 默认 Watermarked，必须显式 NoWatermark
- `skip_translation=True` 即 dry-run（不调 API 走完整管线）；dry-run 下 translator 仍需非空 api_key
- 引擎自带翻译缓存（`ignore_cache=False` 默认开）；`auto_extract_glossary` 已关闭（省 token）
- QPS 端点级可配（1-30），大文档速度的主要旋钮
- 详见 [m0-findings.md](m0-findings.md)

## 设计取舍记录

| 决策 | 原因 |
|------|------|
| 引擎 Python API 而非 CLI 子进程 | M0 验证可嵌入；CLI 仅作兜底路径 |
| 串行队列而非 Celery | 自用单进程足够；升级路径预留 |
| SQLite + settings.json | 零运维；端点含明文 key 是自用取舍（注意 data 目录权限） |
| 目录独立管线而非引擎 glossary | 引擎 glossary 语义不足以保护目录结构；实测段落重组破坏目录（红线 1） |
| 双语默认 side-by-side | 页数不变 → 页码有效；L3 页面重排已列为已知限制 |
| 中文目录渲染用覆盖重绘 | 引擎输出不可逆编辑；目录页无图片，覆盖安全 |
