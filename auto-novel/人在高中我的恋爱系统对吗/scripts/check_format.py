#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_format.py — 章节格式铁律单点硬检查

从 check_dod.py 中抽出格式检查部分，单独提供更详细报告 + 自动修复建议。
用于 subagent 写完章节后第一道检查（快，无需等 AI 反馈）。

铁律（framework/03 §三 §四）：
1. 首行 = 章节标题（含"第N章"或"第N章 XX"）。
2. 末行 = "————本章字数：约 XXXX 字————"。
3. 段间空一行。
4. 系统台词格式 = 【...】（任务类要以"对吗？"等结尾）。
5. 主角心声 = （...）独立成段。
6. 全章无空白段落连续 >= 3 个空行。

exit 0 = 过 ，1 = 有违规，2 = 文件不存在
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ZHENGWEN_DIR = ROOT / "正文"


CHAPTER_TITLE_RE = re.compile(r"第\s*[0-9一二三四五六七八九十百零〇]+\s*章")
WORDCOUNT_RE = re.compile(r"————本章字数：约\s*(\d{3,5})\s*字————")
TASK_CUES = re.compile(r"请\S|宿主[，、]\s*请|下一步|要求你|把.*给|去\S+吧")


def check(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    issues: list[dict] = []

    # 1. 首行标题
    if not lines or not CHAPTER_TITLE_RE.search(lines[0]):
        issues.append({"rule": "F1", "line": 1, "severity": "canon",
                       "msg": "首行不是章节标题",
                       "fix": "确保首行为 '第X章 XX' 格式"})

    # 2. 末行字数标注
    last_line = next((l for l in reversed(lines) if l.strip()), "")
    if not WORDCOUNT_RE.search(last_line):
        issues.append({"rule": "F2", "line": len(lines), "severity": "canon",
                       "msg": "末行缺字数标注",
                       "fix": "补 '————本章字数：约 XXXX 字————'"})

    # 3. 段间空行：连续 3 个空行
    consec_blank = 0
    for i, l in enumerate(lines):
        if l.strip() == "":
            consec_blank += 1
            if consec_blank >= 3:
                issues.append({"rule": "F3", "line": i + 1, "severity": "warn",
                               "msg": f"连续 {consec_blank} 个空行",
                               "fix": "段间统一 1 个空行"})
        else:
            consec_blank = 0

    # 4. 系统【】格式 + 任务类必须对吗
    for i, l in enumerate(lines):
        s = l.strip()
        # 方括号闭合
        if s.count("【") != s.count("】"):
            issues.append({"rule": "F4a", "line": i + 1, "severity": "canon",
                           "msg": f"第{i+1}行【】不配对",
                           "fix": "检查方括号闭合"})
        if s.startswith("【") and s.endswith("】"):
            inner = s[1:-1]
            if TASK_CUES.search(inner):
                if not (inner.endswith("对吗？") or inner.endswith("是吗？")
                        or inner.endswith("可以吗？")):
                    issues.append({"rule": "F4b", "line": i + 1, "severity": "warn",
                                   "msg": f"任务类系统台词未以'对吗？'收尾：{s[:30]}",
                                   "fix": "末尾加'，对吗？'"})

    # 5. 心声（）
    for i, l in enumerate(lines):
        s = l.strip()
        if s.startswith("（") and s.count("（") != s.count("）"):
            issues.append({"rule": "F5", "line": i + 1, "severity": "canon",
                           "msg": f"第{i+1}行（）不配对",
                           "fix": "检查圆括号闭合"})

    return issues


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("target", nargs="?")
    parser.add_argument("--batch")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.batch:
        lo, hi = (int(x) for x in args.batch.split("-"))
        chapters = [f"{i:03d}" for i in range(lo, hi + 1)]
    elif args.target:
        chapters = [f"{int(args.target):03d}"]
    else:
        parser.error("必须指定 target 或 --batch")
        return 2

    all_reports = []
    exit_code = 0
    for ch in chapters:
        paths = list(ZHENGWEN_DIR.glob(f"第{int(ch):03d}章_*.txt"))
        if not paths:
            all_reports.append({"chapter": ch, "error": "文件未找到"})
            exit_code = 2
            continue
        issues = check(paths[0])
        all_reports.append({
            "chapter": ch, "file": str(paths[0].relative_to(ROOT)),
            "issues": issues, "passed": not issues,
        })
        if issues:
            exit_code = max(exit_code, 1)

    if args.json:
        print(json.dumps(all_reports, ensure_ascii=False, indent=2))
    else:
        for r in all_reports:
            if r.get("error"):
                print(f"❌ 第{r['chapter']}章: {r['error']}")
                continue
            mark = "✅" if r["passed"] else f"❌ ({len(r['issues'])} 条)"
            print(f"\n{mark} 第{r['chapter']}章 — {r['file']}")
            for issue in r["issues"]:
                icon = "🔴" if issue["severity"] == "canon" else "🟡"
                print(f"  {icon} [{issue['rule']}] L{issue['line']:04d} {issue['msg']}")
                print(f"    修复：{issue['fix']}")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
