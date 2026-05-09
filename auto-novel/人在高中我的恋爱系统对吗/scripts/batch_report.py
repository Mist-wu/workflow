#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""batch_report.py — 批次完成报告自动生成

Merger 完成后自动调用，产 审查/批次NNN_XXX-YYY/99_批次完成报告.md
内容：
- 各章字数、DoD 通过时间、硬禁用/软限额/读者均分
- 脑暴定稿提出的目标 vs 实际偏差
- 风险清单变动（本批次新增/下级/消解）
- 下批次建议（基于 check_voice + check_tropes + propose_lexicon 的聚合）

用法：
    python3 scripts/batch_report.py 001
"""
from __future__ import annotations

import argparse
import json
import datetime as _dt
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SHENCHA_DIR = ROOT / "审查"
SCRIPTS = ROOT / "scripts"


def _find_batch_dir(batch: str) -> Path | None:
    for d in SHENCHA_DIR.glob(f"批次{int(batch):03d}_*"):
        return d
    return None


def _load_state(batch: str) -> dict | None:
    d = _find_batch_dir(batch)
    if not d:
        return None
    sf = d / "state.json"
    if not sf.exists():
        return None
    return json.loads(sf.read_text(encoding="utf-8"))


def _run(cmd: list[str]) -> tuple[int, str]:
    cp = subprocess.run(cmd, capture_output=True, text=True)
    return cp.returncode, cp.stdout + cp.stderr


def _live_metrics(lo: int, hi: int) -> dict[str, dict]:
    """直接跑 check_dod --json 拿实时指标，绕过 state 滞后值。

    返回 {章号: {char_count, hard_ban, reader_avg, passed}}。
    """
    rc, out = _run([
        "python3", str(SCRIPTS / "check_dod.py"),
        "--batch", f"{lo}-{hi}", "--no-state", "--json",
    ])
    try:
        arr = json.loads(out)
    except Exception:
        return {}
    result: dict[str, dict] = {}
    for item in arr:
        ch = item.get("chapter")
        if not ch:
            continue
        m = {"char_count": None, "hard_ban": None, "reader_avg": None,
             "passed": bool(item.get("passed"))}
        for c in item.get("checks", []):
            name = c.get("name", "")
            detail = c.get("detail", "")
            if "字数 ∈" in name:
                mm = re.search(r"中文汉字数\s*=\s*(\d+)", detail)
                if mm:
                    m["char_count"] = int(mm.group(1))
            elif "AI 味硬禁用" in name:
                mm = re.search(r"命中\s*(\d+)", detail)
                if mm:
                    m["hard_ban"] = int(mm.group(1))
            elif "读者反馈" in name:
                mm = re.search(r"平均\s*([\d.]+)", detail)
                if mm:
                    m["reader_avg"] = float(mm.group(1))
        result[ch] = m
    return result


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("batch")
    args = parser.parse_args(argv)
    batch = f"{int(args.batch):03d}"

    state = _load_state(batch)
    if not state:
        print(f"找不到批次 {batch} 的 state.json", file=sys.stderr)
        return 2

    bdir = _find_batch_dir(batch)
    lo, hi = state["chapter_range"]
    span = f"{lo:03d}-{hi:03d}"

    # 实时指标（取代 state 老值，彻底消除"state 滞后"）
    live = _live_metrics(lo, hi)

    # 章级指标：优先用实测值；state 用作 dod_passed/quality_score 兜底
    rows = []
    for ch in sorted(state["chapters"].keys()):
        m = state["chapters"][ch]
        lv = live.get(ch, {})
        char_count = lv.get("char_count") or m.get("char_count") or "-"
        hard_ban = lv.get("hard_ban") if lv.get("hard_ban") is not None else m.get("hard_ban", "-")
        reader_avg = lv.get("reader_avg") if lv.get("reader_avg") is not None else m.get("reader_avg", "-")
        dod_passed = lv.get("passed") if "passed" in lv else m.get("dod_passed")
        rows.append([
            ch,
            char_count,
            hard_ban if hard_ban is not None else "-",
            reader_avg if reader_avg is not None else "-",
            "✅" if dod_passed else "❌",
            m.get("quality_score") or "-",
        ])

    # 风格漂移：本批次 vs 前 10 章
    prev_upper = max(1, lo - 1)
    prev_lower = max(1, prev_upper - 9)
    voice_code, voice_out = _run([
        "python3", str(SCRIPTS / "check_voice.py"),
        "--baseline", f"{prev_lower}-{prev_upper}",
        "--target", f"{lo}-{hi}",
    ])

    # 套路命中
    _, trope_out = _run([
        "python3", str(SCRIPTS / "check_tropes.py"),
        "--batch", f"{lo}-{hi}",
    ])

    # 词库建议
    _, lex_out = _run([
        "python3", str(SCRIPTS / "propose_lexicon.py"),
        "--batch", f"{lo}-{hi}", "--min-freq", "3",
    ])

    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# 批次 {batch} ({span}) 完成报告",
        "",
        f"> 自动生成于 {now}；来源 state.json + check_voice + check_tropes + propose_lexicon",
        "",
        "## 一、章节指标",
        "",
        "| 章号 | 字数 | 硬禁用 | 读者均 | DoD | 质量 |",
        "|-----|-----|-------|-------|-----|-----|",
    ]
    for r in rows:
        lines.append(f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]} | {r[5]} |")

    lines += [
        "", "## 二、风格漂移（vs 前 10 章）",
        "```",
        voice_out.strip(),
        "```",
        "", "## 三、烂俗套路命中",
        "```",
        trope_out.strip(),
        "```",
        "", "## 四、AI 词库新候选",
        "```",
        lex_out.strip(),
        "```",
        "", "## 五、升级事件",
    ]
    for e in state.get("escalations", []):
        lines.append(f"- [{e['at']}] {e['rule']} — {e['reason']} "
                     f"{'(已解决)' if e.get('resolved') else '(未解决)'}")
    if not state.get("escalations"):
        lines.append("- 无")

    lines += [
        "", "## 六、下批次建议（主 agent 自动整理）",
        "- 若风格漂移出现 🟡/🔴，下批次 Primer 必须在 §5 锚样放目标区间的句长基线。",
        "- 若套路命中率 > 40%，Ideator 必读本报告 §三，生成时显式反套路。",
        "- 若 AI 词库候选 ≥ 5 条，用户复核 framework/06_AI味词库.patch.md 后再开下批次。",
        "",
    ]

    out_path = bdir / "99_批次完成报告.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"✅ 已生成 {out_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
