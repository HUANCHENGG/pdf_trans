# M0 探测记录 —— BabelDOC 0.6.4 引擎适配结论

> 日期：2026-08-28 | 结论：**go，采用 Python API 集成路径**
> 后续实测补充（2026-08-29）：下方"待真实 API 验证项"已全部验证，结论见文末。

## 已验证能力

| 项 | 结论 |
|----|------|
| 安装 | `pip install BabelDOC`（requires-python `<3.14,>=3.10`，本机用 uv 拉 3.13） |
| 模型资产 | `babeldoc --warmup` 一次性下载布局分析模型，已在本机完成 |
| Python API 嵌入 | ✅ 可行：`babeldoc.format.pdf.high_level.async_translate(config)` 异步事件流 |
| OpenAI 兼容端点 | ✅ `OpenAITranslator(lang_in, lang_out, model, base_url, api_key)` |
| 试翻页范围 | ✅ `TranslationConfig(pages="1-5")`，字符串格式 |
| 单双语输出 | ✅ `no_mono` / `no_dual`；输出路径在 `TranslateResult(mono_pdf_path, dual_pdf_path)` |
| 术语表 | ✅ `Glossary.from_csv(path, lang_out)`，CSV 列 `source,target[,tgt_lng]` |
| token 统计 | ✅ translator.token_count / prompt_token_count / completion_token_count / cache_hit_... |
| 水印 | ⚠ 默认开启，必须显式 `WatermarkOutputMode.NoWatermark`（已封装） |
| dry-run | ✅ `skip_translation=True` 不调 API，完整走解析→重建→保存（管线测试按钮已接入） |

## 事件流结构（实测）

```
stage_summary { stages: [{name, percent}, ...] }   # 阶段名来源（percent<100 为进行中）
progress_start / progress_update / progress_end
  { stage_progress, stage_current, stage_total,
    overall_progress(0~100), part_index, total_parts }
error  { error }
finish { translate_result }                        # 收到后必须 break！
```

## 关键坑（已规避）

1. **finish 后事件流不主动结束**：官方 CLI 实现收到 finish 即 `break`；封装漏 break 会永久挂起（已在 engine.py 修复，dry-run 实测通过）。
2. **Windows + stdin 脚本 spawn 失败**：引擎内部用 multiprocessing（字体子集化/PDF 保存），从 `<stdin>` 运行会 `OSError: Invalid argument '<stdin>'`。从真实模块文件（uvicorn/脚本文件）运行正常。
3. **dry-run 也要求非空 api_key**：OpenAI client 构造即校验，封装已用占位 key。
4. **auto_extract_glossary 默认开**：自动术语提取会额外消耗 token，v1 封装已显式关闭（对应 v2 的"术语自动学习"）。
5. **PDF 保存 fallback 链**：clean=True 失败会自动降级重存（日志可见 failed 消息但输出正常）。

## 值得后续利用的引擎参数（M2 候选）

- `figure_table_protection_threshold`（图表保护阈值，表格红线相关）
- `translate_table_text`（CLI 有；表格文本翻译开关）
- `custom_system_prompt`（注入行业翻译规范）
- `primary_font_family`（serif/sans-serif/script 字体族映射）
- `only_include_translated_page`、`max_pages_per_part`（大文档分片，断点续翻基础）
- `qps` + `pool_max_workers`（限流与并发）

## 待真实 API 验证项 → 已全部验证（2026-08-29，真实手册 SDS13782-4 + deepseek-v4-flash）

- [x] 密表格 datasheet 表格质量：用户实测 1-5 页认可（"感觉还可以"）；目录页曾碎裂 → 已由目录专项管线解决
- [x] 双语版式：实为**左右分栏**（side_by_side，页宽 1191pt），未翻译页也重排（原文复制到左栏）；**页数不变**，目录页码与译文页码仍一一对应
- [x] glossary 语义：作为 prompt 约束注入（非译后强替换）——与设计一致，术语硬约束靠注入+译后校验
- [x] 术语一致性跨页：目录条目 106/106 术语统一（去耦电容类惯用译法生效）
- [x] 中文字体嵌入：思源宋体正常（babeldoc 资产含 SourceHanSerifCN）
- [x] 阶段名：progress_start 事件自带 stage 字段（stage_summary 仅初始快照，不可依赖）
- [x] 取消：asyncio cancel 被引擎阻塞 >30s 不可用 → 协作式取消（进度检查点抛异常，2s 生效）
- [x] 目录策略升级：原"跳翻保留原文"已迭代为**目录中文化管线**（提取条目→编号协议批翻译→dual 右栏/mono 条目区重绘，含中文覆盖校验），用户需求为目录必须中文且格式不乱
