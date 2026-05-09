#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_tropes.py — 烂俗套路对抗测试

读取 framework/07_烂俗套路清单.md 的 YAML 段，对章节做模式匹配。
每条 trope 命中的判定：
  patterns 任一命中 + (context_cues 空 或 context_cues 任一命中)

用法：
    python3 scripts/check_tropes.py 010
    python3 scripts/check_tropes.py --batch 010-014
    python3 scripts/check_tropes.py --json 010

exit 0 = 通过（命中率 ≤ 40%）
exit 1 = 警告（40% < 命中率 ≤ 60%）
exit 2 = canon 触发或命中率 > 60%（触发 framework/08 R4.7）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
FRAMEWORK_DIR = ROOT / "framework"
ZHENGWEN_DIR = ROOT / "正文"
TROPES_FILE = FRAMEWORK_DIR / "07_烂俗套路清单.md"


def load_tropes() -> list[dict]:
    """从 markdown 文件的 yaml 代码块里抽出 tropes 列表。"""
    try:
        import yaml  # type: ignore
    except ImportError:
        print("未安装 pyyaml，无法解析清单。", file=sys.stderr)
        return []
    if not TROPES_FILE.exists():
        return []
    text = TROPES_FILE.read_text(encoding="utf-8")
    m = re.search(r"```yaml\s*\n([\s\S]+?)\n```", text)
    if not m:
        return []
    try:
        data = yaml.safe_load(m.group(1)) or {}
    except Exception as e:
        print(f"yaml 解析失败：{e}", file=sys.stderr)
        return []
    return data.get("tropes", [])


def find_chapter_path(chapter_no: str) -> Path | None:
    cn = int(chapter_no)
    matches = list(ZHENGWEN_DIR.glob(f"第{cn:03d}章_*.txt"))
    return matches[0] if matches else None


def check_trope(trope: dict, text: str) -> bool:
    """判定口径：pattern 和 cue 必须在**同一段**（相邻空行间）出现。
    无 cues 时退回整章匹配。"""
    patterns = trope.get("patterns") or []
    cues = trope.get("context_cues") or []
    if not patterns:
        return False
    if not cues:
        return any(p in text for p in patterns)
    # 按空行分段
    paragraphs = re.split(r"\n\s*\n", text)
    for para in paragraphs:
        hit_pattern = any(p in para for p in patterns)
        if not hit_pattern:
            continue
        if any(c in para for c in cues):
            return True
    return False


def scan_chapter(chapter_no: str, tropes: list[dict]) -> dict:
    p = find_chapter_path(chapter_no)
    if not p:
        return {"chapter": chapter_no, "error": "章节文件未找到"}
    text = p.read_text(encoding="utf-8")
    hits = []
    canon_hits = []
    for t in tropes:
        if check_trope(t, text):
            hits.append({"id": t["id"], "name": t["name"], "severity": t.get("severity", "mid")})
            if t.get("severity") == "canon":
                canon_hits.append(t["id"])

    total = len(tropes)
    hit_rate = len(hits) / total if total else 0
    status = "ok"
    if canon_hits:
        status = "canon"
    elif hit_rate > 0.6:
        status = "escalate_R4.7"
    elif hit_rate > 0.4:
        status = "warn"

    return {
        "chapter": chapter_no,
        "path": str(p.relative_to(ROOT)),
        "total": total,
        "hits_count": len(hits),
        "hit_rate": round(hit_rate, 3),
        "canon_hits": canon_hits,
        "status": status,
        "hits": hits,
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("target", nargs="?", help="章号 010")
    parser.add_argument("--batch", help="区间 010-014")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    tropes = load_tropes()
    if not tropes:
        print("⚠ 套路清单为空或解析失败。", file=sys.stderr)
        return 2

    if args.batch:
        lo, hi = (int(x) for x in args.batch.split("-"))
        chapters = [f"{i:03d}" for i in range(lo, hi + 1)]
    elif args.target:
        chapters = [f"{int(args.target):03d}"]
    else:
        parser.error("必须指定 target 或 --batch")
        return 2

    reports = [scan_chapter(c, tropes) for c in chapters]

    if args.json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))
    else:
        for r in reports:
            if "error" in r:
                print(f"❌ 第{r['chapter']}章：{r['error']}")
                continue
            icon = {"ok": "✅", "warn": "🟡", "escalate_R4.7": "🔴",
                    "canon": "🔴"}.get(r["status"], "?")
            print(f"\n{icon} 第{r['chapter']}章  "
                  f"命中 {r['hits_count']}/{r['total']}（{r['hit_rate']*100:.1f}%）  "
                  f"status={r['status']}")
            for h in r["hits"]:
                print(f"   · [{h['severity']}] {h['id']} {h['name']}")

    worst = max((r.get("status", "ok") for r in reports), default="ok",
                key=lambda s: {"ok": 0, "warn": 1, "escalate_R4.7": 2, "canon": 3}.get(s, 0))
    return {"ok": 0, "warn": 1, "escalate_R4.7": 2, "canon": 2}.get(worst, 0)


if __name__ == "__main__":
    raise SystemExit(main())
