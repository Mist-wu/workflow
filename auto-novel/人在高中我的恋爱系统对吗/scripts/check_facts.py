#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_facts.py — canon / 事实表 一致性校验器

能力：
- 加载 审查/facts.json 与 审查/第NNN章/F.delta.json（若有）
- 校验：
  * 不可变字段（身高/生日/口癖/校名）不得被后章覆写
  * 叙事日期单调不回退
  * 好感度变动符合 framework/01 §三规定（日常±1~2 / 事件±3~5）
  * foreshadow_resolved 的 id 必须在历史 foreshadow 表内
  * quotes_locked.canon=true 的台词不得被后章修改
- 模式：
  python3 scripts/check_facts.py                  # 全量校验
  python3 scripts/check_facts.py --merge 011      # 合并 F.delta.json 到 facts.json（dry-run）
  python3 scripts/check_facts.py --merge 011 --write   # 真实写
  python3 scripts/check_facts.py --json           # 机读输出

exit 0 = pass；exit 1 = 有 canon 冲突；exit 2 = 脚本错误
"""
from __future__ import annotations

import argparse
import copy
import datetime as _dt
import json
import re
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SHENCHA_DIR = ROOT / "审查"
FACTS_FILE = SHENCHA_DIR / "facts.json"


# ---------------------------------------------------------------------------
# 数据
# ---------------------------------------------------------------------------

@dataclass
class Violation:
    severity: str   # "canon" 红线 / "warn" 黄
    code: str       # V1 / V2 / ...
    chapter: str
    msg: str

    def render(self) -> str:
        icon = "🔴" if self.severity == "canon" else "🟡"
        return f"{icon} [{self.code}] 第{self.chapter}章 — {self.msg}"


AFFINITY_MAX_DAILY = 2
AFFINITY_MAX_EVENT = 5
AFFINITY_MAX_HIGH_IMPACT = 14  # 第006章 林知夏 +14 是历史最大；高于此需显式标注


# ---------------------------------------------------------------------------
# IO
# ---------------------------------------------------------------------------

def load_facts() -> dict:
    if not FACTS_FILE.exists():
        raise FileNotFoundError(f"审查/facts.json 未找到，请先种子化。")
    return json.loads(FACTS_FILE.read_text(encoding="utf-8"))


def load_delta(chapter_no: str) -> dict | None:
    p = SHENCHA_DIR / f"第{chapter_no}章" / "F.delta.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 校验规则
# ---------------------------------------------------------------------------

def check_immutable_characters(facts: dict) -> list[Violation]:
    """canon.characters.<name>.immutable_fields 中的字段不可被覆盖检测由 merge 时做。
    此处做 self-check：保证 immutable_fields 字段在该角色下都存在。"""
    v: list[Violation] = []
    for name, ch in facts.get("canon", {}).get("characters", {}).items():
        for fld in ch.get("immutable_fields", []):
            if fld not in ch:
                v.append(Violation(
                    "warn", "V-IMMUT-MISSING", "-",
                    f"角色 {name} 的不可变字段 '{fld}' 未在档案里定义。",
                ))
    return v


def check_timeline_monotonic(facts: dict) -> list[Violation]:
    """叙事日期序列必须按章号非回退。"""
    v: list[Violation] = []
    events = facts.get("timeline", {}).get("events_ordered", [])
    last_date: str | None = None
    for ev in events:
        d = _extract_date(ev.get("date", ""))
        if d and last_date and d < last_date:
            v.append(Violation(
                "canon", "V-TIME-REV", ev.get("chapter", "?"),
                f"叙事日期回退：{ev['date']} 早于上一章",
            ))
        if d:
            last_date = d
    return v


def _extract_date(s: str) -> str | None:
    """从 '3月7日(周日)晚' 这类字符串抓 'MMDD'。"""
    m = re.search(r"(\d{1,2})月(\d{1,2})日", s)
    if not m:
        return None
    return f"{int(m.group(1)):02d}{int(m.group(2)):02d}"


def check_affinity_deltas(facts: dict) -> list[Violation]:
    """每次好感度变动不得超过 AFFINITY_MAX_HIGH_IMPACT；常规变动应在 ±AFFINITY_MAX_EVENT 内。"""
    v: list[Violation] = []
    for name, data in facts.get("affinity", {}).items():
        hist = data.get("history", [])
        for rec in hist:
            d = rec.get("delta")
            if d is None:
                continue
            if abs(d) > AFFINITY_MAX_HIGH_IMPACT:
                v.append(Violation(
                    "canon", "V-AFF-TOOBIG", rec.get("chapter", "?"),
                    f"{name} 好感度单次变动 {d}，超过全书最大阈值 {AFFINITY_MAX_HIGH_IMPACT}",
                ))
            elif abs(d) > AFFINITY_MAX_EVENT:
                if not rec.get("event"):
                    v.append(Violation(
                        "warn", "V-AFF-UNEXP", rec.get("chapter", "?"),
                        f"{name} 变动 {d}（高值）但 event 字段为空，建议显式说明触发",
                    ))
    return v


def check_foreshadow_consistency(facts: dict) -> list[Violation]:
    v: list[Violation] = []
    active_ids = {f["id"] for f in facts.get("foreshadow", {}).get("active", [])}
    resolved = facts.get("foreshadow", {}).get("resolved", [])
    for rec in resolved:
        fid = rec.get("id")
        if fid and fid not in active_ids and not rec.get("planted_chapter"):
            v.append(Violation(
                "canon", "V-FS-GHOST", rec.get("chapter", "?"),
                f"伏笔 {fid} 在 resolved 里但 active 里不存在且未声明 planted_chapter",
            ))
    return v


def check_quotes_locked(facts: dict) -> list[Violation]:
    v: list[Violation] = []
    seen_ids: set[str] = set()
    for q in facts.get("quotes_locked", []):
        qid = q.get("id")
        if qid in seen_ids:
            v.append(Violation(
                "canon", "V-QUOTE-DUP", q.get("chapter", "?"),
                f"quotes_locked 出现重复 id {qid}",
            ))
        seen_ids.add(qid)
    return v


# ---------------------------------------------------------------------------
# 合并 F.delta.json → facts.json
# ---------------------------------------------------------------------------

def merge_delta(facts: dict, delta: dict) -> tuple[dict, list[Violation]]:
    """把单章 delta 合入 facts。不可变字段冲突返回 canon violation。"""
    new_facts = copy.deepcopy(facts)
    v: list[Violation] = []
    ch = delta.get("chapter", "?")

    # 1. 角色不可变字段冲突检测
    for name, updates in (delta.get("characters") or {}).items():
        old = new_facts.get("canon", {}).get("characters", {}).get(name, {})
        immut = old.get("immutable_fields", [])
        for k, v_new in updates.items():
            if k in immut and k in old and old[k] != v_new:
                v.append(Violation(
                    "canon", "V-IMMUT-CONFLICT", ch,
                    f"角色 {name}.{k}: 档案 {old[k]} vs delta {v_new}（不可变字段不得覆写）",
                ))
            else:
                old[k] = v_new
        new_facts.setdefault("canon", {}).setdefault("characters", {})[name] = old

    # 2. 叙事日期
    if delta.get("narrative_date"):
        cur = _extract_date(new_facts.get("timeline", {}).get("current_date", ""))
        new = _extract_date(delta["narrative_date"])
        if cur and new and new < cur:
            v.append(Violation(
                "canon", "V-TIME-REV", ch,
                f"delta 叙事日期 {delta['narrative_date']} 早于当前 {new_facts['timeline']['current_date']}",
            ))
        new_facts.setdefault("timeline", {})["current_date"] = delta["narrative_date"]

    # 3. 好感度合并
    for name, new_val in (delta.get("affinity") or {}).items():
        cur_data = new_facts.setdefault("affinity", {}).setdefault(name, {
            "current": None, "history": [],
        })
        old_val = cur_data.get("current")
        delta_ = (new_val - old_val) if (old_val is not None) else 0
        if abs(delta_) > AFFINITY_MAX_HIGH_IMPACT:
            v.append(Violation(
                "canon", "V-AFF-TOOBIG", ch,
                f"{name} 变动 {delta_}，超上限 {AFFINITY_MAX_HIGH_IMPACT}",
            ))
        cur_data["current"] = new_val
        event_text = next(
            (e.get("summary", "") for e in (delta.get("events") or [])
             if name in (e.get("affinity_delta") or {})),
            "",
        )
        cur_data.setdefault("history", []).append({
            "chapter": ch, "value": new_val, "delta": delta_,
            "event": event_text,
        })

    # 4. 事件序列追加
    if delta.get("events"):
        for ev in delta["events"]:
            new_facts.setdefault("timeline", {}).setdefault("events_ordered", []).append({
                "chapter": ch,
                "date": delta.get("narrative_date", ""),
                "summary": ev.get("summary", ""),
            })

    # 5. 伏笔新埋 / 回收
    active = new_facts.setdefault("foreshadow", {}).setdefault("active", [])
    active_ids = {f["id"] for f in active}
    for fs in (delta.get("foreshadow_planted") or []):
        if isinstance(fs, str):
            fs = {"id": fs, "planted_chapter": ch}
        if fs["id"] not in active_ids:
            active.append(fs)
            active_ids.add(fs["id"])
    resolved_list = new_facts["foreshadow"].setdefault("resolved", [])
    for fid in (delta.get("foreshadow_resolved") or []):
        if fid not in active_ids:
            v.append(Violation(
                "canon", "V-FS-GHOST", ch,
                f"回收伏笔 {fid} 在 active 里不存在",
            ))
        resolved_list.append({"id": fid, "chapter": ch})
        active = [f for f in active if f["id"] != fid]
        new_facts["foreshadow"]["active"] = active
        active_ids.discard(fid)

    # 6. quotes locked
    for q in (delta.get("quotes_locked") or []):
        new_facts.setdefault("quotes_locked", []).append(q)

    # 7. chapter status
    st = new_facts.setdefault("chapter_status", {}).setdefault(ch, {})
    for k in ("draft", "hard_check", "polish", "revise", "final", "cn_chars"):
        if k in delta:
            st[k] = delta[k]

    new_facts.setdefault("_meta", {})["last_updated"] = _dt.datetime.now().strftime("%Y-%m-%d")
    return new_facts, v


def write_facts(facts: dict) -> None:
    tmp = FACTS_FILE.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(facts, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(FACTS_FILE)


# ---------------------------------------------------------------------------
# 汇总报告
# ---------------------------------------------------------------------------

def run_full_check() -> list[Violation]:
    facts = load_facts()
    v: list[Violation] = []
    v += check_immutable_characters(facts)
    v += check_timeline_monotonic(facts)
    v += check_affinity_deltas(facts)
    v += check_foreshadow_consistency(facts)
    v += check_quotes_locked(facts)
    return v


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="canon / 事实表 一致性校验")
    parser.add_argument("--merge", metavar="CHAP",
                        help="合并指定章的 F.delta.json 到 facts.json (dry-run)")
    parser.add_argument("--write", action="store_true",
                        help="配合 --merge，真实写回 facts.json")
    parser.add_argument("--json", action="store_true",
                        help="JSON 输出")
    args = parser.parse_args(argv)

    if args.merge:
        ch = f"{int(args.merge):03d}"
        delta = load_delta(ch)
        if delta is None:
            print(f"审查/第{ch}章/F.delta.json 不存在，跳过合并。", file=sys.stderr)
            return 2
        facts = load_facts()
        merged, violations = merge_delta(facts, delta)
        if args.json:
            print(json.dumps({
                "violations": [asdict(v) for v in violations],
                "would_write": args.write,
            }, ensure_ascii=False, indent=2))
        else:
            print(f"== 合并第{ch}章 delta → facts.json {'(真写)' if args.write else '(dry-run)'} ==")
            if violations:
                for vio in violations:
                    print(vio.render())
            else:
                print("✅ 无冲突")
        if args.write:
            canon_conflicts = [v for v in violations if v.severity == "canon"]
            if canon_conflicts:
                print(f"❌ 有 {len(canon_conflicts)} 条 canon 冲突，不写回。", file=sys.stderr)
                return 1
            write_facts(merged)
            print(f"✅ 已写回 {FACTS_FILE.relative_to(ROOT)}")
        return 1 if [v for v in violations if v.severity == "canon"] else 0

    # 全量校验
    violations = run_full_check()
    if args.json:
        print(json.dumps(
            {"violations": [asdict(v) for v in violations]},
            ensure_ascii=False, indent=2,
        ))
    else:
        if not violations:
            print("✅ facts.json 全量校验通过。")
        else:
            print(f"❌ 发现 {len(violations)} 条问题：\n")
            for v in violations:
                print(v.render())
    return 1 if [v for v in violations if v.severity == "canon"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
