# subagent 返回协议（Machine-Readable Return Contract · v1）

> 本文件规定所有 subagent **最终消息**的结构。
> 存在的意义：主 agent 不再"读懂"每份 markdown 报告，而是 `json.loads()` 一行即可决定下一步派谁。
> 所有 subagent 模板（framework/05_批次化写作工作流.md §三–§十）的"最终返回"段必须按本协议升级。

---

## 一、通用结构

subagent 的最终消息**必须**由两部分组成：

```
## 人读摘要
<= 3 行自然语言，给用户/主 agent 快速扫一眼。

## RETURN_JSON
{单行合法 JSON}
```

- `RETURN_JSON` 段的**前一行必须是** `## RETURN_JSON` （三个字符 "##" + 空格 + 标识），便于主 agent 正则抓取。
- JSON **必须单行**，**必须合法**（能过 `json.loads`）。
- JSON 多余字段不报错（前向兼容），但**必填字段缺一不可**。

---

## 二、通用必填字段

所有 subagent 共享的顶层字段：

| 字段 | 类型 | 说明 |
|-----|------|------|
| `role` | string | 角色名，固定值（见 §三） |
| `batch` | string | 批次号三位，例 `"001"`；`"000"` = 债务清理 |
| `chapter` | string \| null | 章号三位，例 `"010"`；批次级 subagent 填 `null` |
| `artifact_paths` | [string] | 本次产出的全部文件相对路径（相对项目根） |
| `status` | enum | `"ok"` / `"warn"` / `"fail"` / `"need_user"` |
| `next_action` | enum | 见 §四，主 agent 据此调度 |
| `summary` | string | ≤ 80 字给 state.json 看板用 |
| `metrics` | object | 角色专属量化指标（见 §三） |
| `blocking_issues` | [string] | status != ok 时必填，列出阻塞点 |

---

## 三、各角色专属字段（metrics）

### ⓪ Primer
```json
{"role":"Primer","batch":"001","chapter":null,
 "artifact_paths":["审查/批次001_010-014/00_context_pack.md"],
 "status":"ok","next_action":"start_ideator",
 "summary":"context_pack 已打包，红线：卷末钩子不得改",
 "metrics":{"char_count":7823,"red_lines":4,"anchor_samples":3},
 "blocking_issues":[]}
```
- `metrics.char_count`：context_pack 汉字数，**必须** 6000–10000；超限 status=warn。
- `metrics.red_lines`：§1 红线条数。
- `metrics.anchor_samples`：§5 锚样段数，必须 2–3。

### ① Ideator
```json
{"role":"Ideator","batch":"001",...,"metrics":{
  "reverse_suggestions":6,"max_risk_level":"中",
  "proposed_memory_points":5}}
```

### ② Gatekeeper
```json
{"metrics":{"accepted":4,"conditional":2,"rejected":0,"final_instruction_chars":2100}}
```

### ③ Writer（单写手）
```json
{"role":"Writer","chapter":"011",...,
 "artifact_paths":["正文/第011章_XXX.txt"],
 "metrics":{"char_count":4102,"opening_line":"...","closing_line":"...",
            "system_appearances":3,"dialogue_ratio":0.51}}
```
- `char_count` 不在 3800–4400 → status=warn，由 check_dod.py 决定是否阻断。

### ③ Writer-{A|B|C} (Best-of-N)
```json
{"role":"WriterA","chapter":"011",...,
 "artifact_paths":["正文/drafts/第011章_A.txt"],
 "metrics":{"char_count":4021,"persona":"细腻派",
            "self_best":"...30字...","self_worst":"...30字..."}}
```

### ③ Synthesizer
```json
{"role":"Synthesizer","chapter":"011",...,
 "metrics":{"char_count":4088,
            "source_counts":{"A":7,"B":3,"C":9,"self":2},
            "best_from":"B","seam_rewrite":true}}
```

### ③.5 Scanner（`scan_ai_templates.py` 由主 agent 直跑，非 subagent）
主 agent 直接读 `审查/第NNN章/AI味命中清单.txt` 末尾「[统计]」段更新 state。

### ④ 硬校验
```json
{"role":"HardCheck","chapter":"011",...,
 "metrics":{"A_count":2,"B_count":5,"D_count":3,"E_count":1,
            "F_delta_keys":["affinity.林知夏","narrative_date"],
            "F_yaml_path":"审查/第011章/F.delta.json"}}
```
- F 段同时产一份 `F.delta.json`（见 §五），供 Merger 机读合并。

### ④ 润色-节奏 / 文风 / AI味
```json
{"role":"Polish-Pacing","chapter":"011",...,
 "metrics":{"severity":"轻","C_heavy_count":0,"must_fix":["..."]}}
```
- `severity` 枚举：`"无" | "轻" | "中" | "重"`。
- `C_heavy_count` = C 段里被标"重 AI 味"的条目数，**修订 subagent 必须 100% 修**。

### ④ 读者模拟 α/β/γ
```json
{"role":"Reader-α","chapter":"011",...,
 "metrics":{"score":7,"drop_urge":"low",
            "most_hooked":"...","most_skippable":"...",
            "trope_hits":["天台表白"]   // 仅 γ 用
 }}
```
- `score` 整数 1–10；`drop_urge` 枚举 `"none"|"low"|"mid"|"high"`。

### ⑤ 修订
```json
{"role":"Reviser","chapter":"011",...,
 "metrics":{"char_count":4023,"hard_ban_cleared":true,
            "A_fixed":2,"C_heavy_fixed":3,"reader_retry_triggered":false}}
```

### ⑥ Integrator
```json
{"role":"Integrator","batch":"001",...,
 "metrics":{"need_second_revision":["013"],
            "reader_avg":7.2,"affinity_drift":2,
            "trope_hit_rate":0.35}}
```

### ⑦ Merger
```json
{"role":"Merger","batch":"001",...,
 "artifact_paths":["审查/事实表.md","审查/facts.json","审查/风险清单.md"],
 "metrics":{"chapters_finalized":["010","011","012","013","014"],
            "canon_conflicts":0,"preview_vs_final_diffs":1}}
```

---

## 四、`next_action` 枚举

主 agent 根据 `next_action` 自动派单；无歧义时不问用户。

| 值 | 下一步由谁做 |
|---|---|
| `start_primer` / `start_ideator` / `start_gatekeeper` | 启动对应 subagent |
| `start_writing` | 派 5 个写作 subagent（依 context_pack §7） |
| `start_scanner` | 主 agent 跑 `scan_ai_templates.py --batch NNN-MMM` |
| `start_review` | 派 ④ 审查并发层（单章或整批） |
| `start_revise` | 派 ⑤ 修订 |
| `rerun_scanner` | 修订后重扫验证硬禁用清零 |
| `start_integrator` / `start_merger` | 启动 ⑥ / ⑦ |
| `retry_self` | 本 subagent 失败，主 agent 重派**同一个** subagent（最多 2 次） |
| `retry_writer` | 写作失败，重派单个写手（Best-of-N 档可只重派失败的那一路） |
| `retry_reviser` | 修订未清零硬禁用，再派一次修订 |
| `escalate_user` | 撞到 `framework/08_自动升级规则.md` 的红线，停并告知用户 |
| `done` | 本 subagent 的职责完成，无后续动作 |

---

## 五、F.delta.json schema（硬校验 subagent 专用）

与 `审查/facts.json` schema 对齐的增量包。Merger 拿到后做合并：

```json
{
  "chapter": "011",
  "narrative_date": "2025-03-08",
  "weekday": "Monday",
  "present_characters": ["陈屿","林知夏","郑维"],
  "events": [
    {"id":"EV-011-01","summary":"林知夏主动把小人画涂改","affinity_delta":{"林知夏":3}}
  ],
  "affinity": {"林知夏":58},
  "foreshadow_planted": ["FS-011-01"],
  "foreshadow_resolved": [],
  "quotes_locked": [
    {"id":"Q-011-LZX-01","speaker":"林知夏","text":"...","canon":true}
  ],
  "risks": []
}
```

字段缺省时可省；空数组合法。`check_facts.py` 会校验：
- `narrative_date` 单调不回退
- 好感度变动与 `framework/01` §三规定一致（日常 ±1–2，事件 ±3–5）
- `foreshadow_resolved` 的 id 必须在历史 `foreshadow_planted` 内
- `quotes_locked.canon=true` 的台词不可被后章改写

---

## 六、兼容过渡

2025-Q2 批次 000 / 001 允许旧模板 subagent 不产 `RETURN_JSON`，主 agent 手工从人读摘要补齐后写入 state.json。
批次 002 起所有新派的 subagent 模板**必须**按本协议升级；未升级的模板视为错误。

---

## 七、开发者速查：抓 RETURN_JSON 的正则

```python
import re, json
m = re.search(r"^##\s+RETURN_JSON\s*\n(.+?)\s*$",
              subagent_msg, flags=re.M | re.S)
data = json.loads(m.group(1).strip().splitlines()[0])
```
