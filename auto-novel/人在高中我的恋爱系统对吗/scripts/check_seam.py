#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_seam.py — 章际衔接硬检查

目标：捕捉"上一章结尾是周四放学，这一章开头直接跳周六"之类的时间线断裂。

规则：
1. 当前章开头前 3 段是否 echo 上一章结尾（人物、时间、地点至少一项延续）
2. 新登场角色（上一章没出现过）是否有"微提醒"（framework/03 §八 β 视角铁律）
3. 跨章回扣：主角视角下，上一章 ≤ 3 章未出现的人/事首次出现，应带 ≤ 8 字提示

用法：
    python3 scripts/check_seam.py 010       # 检查第010章与第009章的衔接
    python3 scripts/check_seam.py --batch 010-014
    python3 scripts/check_seam.py --json 010
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ZHENGWEN_DIR = ROOT / "正文"
SHENCHA_DIR = ROOT / "审查"
FACTS_FILE = SHENCHA_DIR / "facts.json"


def _load_characters() -> list[str]:
    if not FACTS_FILE.exists():
        return []
    data = json.loads(FACTS_FILE.read_text(encoding="utf-8"))
    return list(data.get("canon", {}).get("characters", {}).keys())


def _read_chapter(chapter_no: int) -> str:
    matches = list(ZHENGWEN_DIR.glob(f"第{chapter_no:03d}章_*.txt"))
    if not matches:
        return ""
    return matches[0].read_text(encoding="utf-8")


def _chapter_body(text: str) -> str:
    lines = text.splitlines()
    return "\n".join(lines[1:]) if lines else ""


def check_seam(chapter_no: int) -> dict:
    cur_text = _read_chapter(chapter_no)
    if not cur_text:
        return {"chapter": f"{chapter_no:03d}", "error": "当前章文件不存在"}
    if chapter_no <= 1:
        return {"chapter": "001", "note": "第 001 章无上一章，跳过"}

    prev_text = _read_chapter(chapter_no - 1)
    if not prev_text:
        return {"chapter": f"{chapter_no:03d}",
                "error": "上一章文件不存在"}

    cur_body = _chapter_body(cur_text)
    prev_body = _chapter_body(prev_text)

    # 上一章结尾 3 段
    prev_paras = [p.strip() for p in re.split(r"\n\s*\n", prev_body) if p.strip()]
    prev_tail = "\n".join(prev_paras[-3:])
    # 当前章开头 5 段
    cur_paras = [p.strip() for p in re.split(r"\n\s*\n", cur_body) if p.strip()]
    cur_head = "\n".join(cur_paras[:5])

    issues: list[dict] = []

    # 规则 1：echo — 检查人物名是否延续
    characters = _load_characters()
    prev_chars = [c for c in characters if c in prev_tail]
    echo_hit = any(c in cur_head for c in prev_chars) or len(prev_chars) == 0
    if not echo_hit:
        issues.append({
            "rule": "SEAM-ECHO", "severity": "warn",
            "msg": f"开头 5 段未 echo 上一章结尾出现的角色 {prev_chars}",
            "fix": "开头加一句'昨晚 X 那番话还在耳朵里转'或明确新场景",
        })

    # 规则 2：时间线跳跃 — 如果上一章结尾有"晚上/深夜/睡下"，当前章开头应说明过了多久
    night_marker = re.search(r"(晚上|深夜|夜里|睡|凌晨|半夜)", prev_tail)
    if night_marker:
        if not re.search(r"(早|起|闹钟|醒|早晨|天亮|第二天|周\S)", cur_head):
            issues.append({
                "rule": "SEAM-TIME", "severity": "warn",
                "msg": "上一章结尾发生在夜间，当前章未交代时间切换",
                "fix": "开头补半句'第二天早上'或同义标记",
            })

    # 规则 3：新登场角色微提醒（简化：当前章出现、前 3 章未出现的角色，
    # 若不是当章主线人物，应带 ≤ 8 字提醒）
    prev_3_text = ""
    for c in range(max(1, chapter_no - 3), chapter_no):
        prev_3_text += _read_chapter(c)
    for name in characters:
        if name in cur_body and name not in prev_3_text:
            # 检查微提醒：名字附近 ±15 字是否有角色档案里的关键词（身份/口癖）
            near = _collect_near(cur_body, name, window=20)
            # 这里做松检查：只要附近有 "（"、"（"、"——"、破折号、逗号+身份词 就算有提醒
            if not re.search(r"[（—,，].{1,15}[班委队长老师同桌发小邻居学姐](?:[，。）])?",
                             near):
                issues.append({
                    "rule": "SEAM-RECALL", "severity": "warn",
                    "msg": f"跨章回扣：{name} 在前 3 章未出现，开头未带微提醒",
                    "fix": f"首次提到 {name} 时补 ≤ 8 字身份标（如'{name}（班长）'）",
                })

    return {
        "chapter": f"{chapter_no:03d}",
        "prev_tail_preview": prev_paras[-1][:60] if prev_paras else "",
        "cur_head_preview": cur_paras[0][:60] if cur_paras else "",
        "issues": issues,
        "passed": not issues,
    }


def _collect_near(text: str, token: str, window: int = 20) -> str:
    idx = 0
    segs: list[str] = []
    while True:
        p = text.find(token, idx)
        if p < 0:
            break
        start = max(0, p - window)
        end = min(len(text), p + len(token) + window)
        segs.append(text[start:end])
        idx = p + len(token)
        if len(segs) > 3:
            break
    return "\n".join(segs)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("target", nargs="?")
    parser.add_argument("--batch")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.batch:
        lo, hi = (int(x) for x in args.batch.split("-"))
        chapters = list(range(lo, hi + 1))
    elif args.target:
        chapters = [int(args.target)]
    else:
        parser.error("必须指定 target 或 --batch")
        return 2

    reports = [check_seam(c) for c in chapters]
    if args.json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))
    else:
        for r in reports:
            if r.get("error"):
                print(f"❌ 第{r['chapter']}章: {r['error']}")
                continue
            if r.get("note"):
                print(f"ℹ 第{r['chapter']}章: {r['note']}")
                continue
            mark = "✅" if r["passed"] else f"❌ ({len(r['issues'])} 条)"
            print(f"\n{mark} 第{r['chapter']}章 衔接检查")
            print(f"  上一章结尾：{r['prev_tail_preview']}")
            print(f"  本 章 开头：{r['cur_head_preview']}")
            for issue in r["issues"]:
                print(f"  🟡 [{issue['rule']}] {issue['msg']}")
                print(f"    修复：{issue['fix']}")
    return 1 if any(not r.get("passed", True) and not r.get("error") and not r.get("note")
                    for r in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())
