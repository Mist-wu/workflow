#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""parse_chapter.py — 章节解析包生成器

产出：审查/第NNN章/parse.json
目的：把章节的结构化摘要一次算好，审查/润色/读者模拟 subagent 直接读它，
      减少每个 subagent 重复分段、数字、找钩子的冗余消耗。

schema 见文件末尾 render_example。

用法：
    python3 scripts/parse_chapter.py 010
    python3 scripts/parse_chapter.py --batch 010-014
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


SYSTEM_RE = re.compile(r"^【[^】]*】$")
MONO_RE = re.compile(r"^（[^）]*）$")


def _count_chinese(s: str) -> int:
    return sum(1 for c in s if '\u4e00' <= c <= '\u9fff')


def classify_paragraph(para: str) -> str:
    lines = [l for l in para.splitlines() if l.strip()]
    if not lines:
        return "empty"
    if all(SYSTEM_RE.match(l.strip()) for l in lines):
        return "system"
    if all(MONO_RE.match(l.strip()) for l in lines):
        return "monologue"
    # 含有对话引号
    if re.search(r"[\u201c\u201d\"][^\u201c\u201d\"\n]+[\u201c\u201d\"]", para):
        return "dialogue"
    # 超短（<= 20 字）
    total = _count_chinese(para)
    if total <= 20:
        return "beat"
    return "description"


def parse_chapter(chapter_no: str) -> dict:
    cn = int(chapter_no)
    paths = list(ZHENGWEN_DIR.glob(f"第{cn:03d}章_*.txt"))
    if not paths:
        return {"error": f"第{cn:03d}章 正文文件未找到"}
    path = paths[0]
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    title = lines[0] if lines else ""
    body = "\n".join(lines[1:])

    # 按空行分段（保留首尾）
    raw_paragraphs = re.split(r"\n\s*\n", body)
    paragraphs = []
    line_no = 2  # 首行标题占第1行，空行2后正文
    for p in raw_paragraphs:
        if not p.strip():
            line_no += len(p.splitlines()) + 1
            continue
        para_type = classify_paragraph(p)
        para_chars = _count_chinese(p)
        # 高亮标志：含系统/情绪关键字/重要人名
        flags = []
        if SYSTEM_RE.match(p.strip().splitlines()[0] if p.strip() else ""):
            flags.append("system")
        if "好感度" in p:
            flags.append("affinity")
        if re.search(r"[!！？?]", p):
            flags.append("emo")
        paragraphs.append({
            "line_start": line_no,
            "type": para_type,
            "chars": para_chars,
            "preview": p.strip().splitlines()[0][:40],
            "flags": flags,
        })
        line_no += len(p.splitlines()) + 1

    # 统计
    type_counts = {}
    for pp in paragraphs:
        type_counts[pp["type"]] = type_counts.get(pp["type"], 0) + 1
    dialogue_ratio = sum(1 for pp in paragraphs if pp["type"] == "dialogue") / max(len(paragraphs), 1)
    system_appearances = sum(1 for pp in paragraphs if pp["type"] == "system")

    # 钩子：开头前 3 段 + 结尾后 3 段（快速衔接判定用）
    head_preview = [pp["preview"] for pp in paragraphs[:3]]
    tail_preview = [pp["preview"] for pp in paragraphs[-3:]]

    # 上一章末尾（用于 seam check）
    prev_path = list(ZHENGWEN_DIR.glob(f"第{cn-1:03d}章_*.txt")) if cn > 1 else []
    prev_tail: list[str] = []
    if prev_path:
        prev_text = prev_path[0].read_text(encoding="utf-8")
        prev_paras = [p.strip() for p in re.split(r"\n\s*\n", prev_text) if p.strip()]
        prev_tail = [p.splitlines()[0][:40] for p in prev_paras[-3:]]

    # AI 味命中（若有清单）
    ai_hits = []
    ai_path = SHENCHA_DIR / f"第{cn:03d}章" / "AI味命中清单.txt"
    if ai_path.exists():
        m = re.search(r"\[硬禁用\](.*?)(?=\[软限额\]|\[句式模板\]|\[统计\])",
                      ai_path.read_text(encoding="utf-8"), re.S)
        if m:
            for ln in m.group(1).splitlines():
                mm = re.match(r"L(\d+)\s+\S+\s+\"([^\"]+)\"", ln.strip())
                if mm:
                    ai_hits.append({"line": int(mm.group(1)), "phrase": mm.group(2)})

    return {
        "chapter": f"{cn:03d}",
        "file": str(path.relative_to(ROOT)),
        "title": title,
        "stats": {
            "total_chars": _count_chinese(text),
            "paragraph_count": len(paragraphs),
            "type_counts": type_counts,
            "dialogue_ratio": round(dialogue_ratio, 3),
            "system_appearances": system_appearances,
        },
        "paragraphs": paragraphs,
        "seam": {
            "prev_chapter_tail_previews": prev_tail,
            "current_chapter_head_previews": head_preview,
            "current_chapter_tail_previews": tail_preview,
        },
        "flagged_spans": ai_hits,
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("target", nargs="?")
    parser.add_argument("--batch")
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args(argv)

    if args.batch:
        lo, hi = (int(x) for x in args.batch.split("-"))
        chaps = [f"{i:03d}" for i in range(lo, hi + 1)]
    elif args.target:
        chaps = [f"{int(args.target):03d}"]
    else:
        parser.error("必须指定 target 或 --batch")
        return 2

    for ch in chaps:
        data = parse_chapter(ch)
        if "error" in data:
            print(data["error"], file=sys.stderr)
            continue
        out_dir = SHENCHA_DIR / f"第{ch}章"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / "parse.json"
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        if args.stdout:
            print(payload)
        else:
            out.write_text(payload, encoding="utf-8")
            print(f"✅ 第{ch}章 → {out.relative_to(ROOT)}  "
                  f"({data['stats']['total_chars']} 字 / {data['stats']['paragraph_count']} 段)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
