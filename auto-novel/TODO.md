# Idea · Novel 项目 TODO

## 已落地（v3 自动化底座，2026-04-23）

全部 P0–P5 改造已完成，自动化级别显著提高：

- ✅ `framework/09_subagent返回协议.md` — 所有 subagent 必须产 RETURN_JSON 尾段
- ✅ `framework/08_自动升级规则.md` — 30 条"何时停下来问用户"的硬规则
- ✅ `framework/07_烂俗套路清单.md` — 34 条 YAML 清单供 `check_tropes.py` 使用
- ✅ `framework/config/exempt_chapters.yaml` — 抒情豁免章号统一口径
- ✅ 批次状态机：`scripts/state.py` + `审查/批次NNN/state.json`
- ✅ 章级粒度调度器：`scripts/pipeline.py`（status / next / tick / advance）
- ✅ 硬 Quality Gate：`scripts/check_dod.py`（7 项 DoD）
- ✅ canon 校验：`scripts/check_facts.py` + `审查/facts.json`（机读事实表）
- ✅ 格式/衔接硬检查：`scripts/check_format.py` + `scripts/check_seam.py`
- ✅ 风格漂移探针：`scripts/check_voice.py`
- ✅ 套路对抗测试：`scripts/check_tropes.py`
- ✅ AI 词库自增殖：`scripts/propose_lexicon.py`
- ✅ 章节解析包：`scripts/parse_chapter.py` → `parse.json`
- ✅ 批次完成报告：`scripts/batch_report.py`
- ✅ drafts 清理：`scripts/cleanup_drafts.py`
- ✅ 读者 prompt 升级（内嵌 RETURN_JSON）：`scripts/gen_reader_prompts.py`

## 剩下的最后一公里

- [ ] 真正的**编排层**（Cursor hook / 外部脚本轮询）：
  - 监听 "Merger 完成" 事件 → 自动启动下一批 Primer
  - 目前 `pipeline.py tick` 能跑一切可自动化步骤，但"派 subagent"仍需 Cursor 主 agent 手动触发（因为 Task 工具不是 CLI）
  - 解决方案候选：
    1. Cursor hook（on_turn_end）自动读 `state.json` 并派下一批 Task
    2. 写一个外部 Python 轮询脚本 + Cursor CLI
    3. 保留当前"我喊继续"作为 ≤ 1 次/批次的检查点，其它时刻主 agent 全自动推
- [ ] 写 outline/02–10 卷细纲（按路线图排期）

## 下一步

- 选 A：启动批次 000 债务清理到 v3 DoD 通过
- 选 B：跳过 000，直接启动批次 001（010–014）按 v3 模式

详见 `novel/人在高中我的恋爱系统对吗/续写清单.md §四`。
