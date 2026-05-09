#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cleanup_drafts.py — Best-of-N 中间稿清理

Best-of-N 模式每章会产 A/B/C 三份 drafts。本脚本在 Synthesizer 合成后、
批次 Merger 完成后，自动清理这些中间产物，节约仓库体积。

策略：
- 默认 --dry-run（只打印要删的文件）
- 对应 state.json 中 stage=done 的章节，才允许删 drafts
- 不删合成正本、不删 state/report 任何已归档文件

用法：
    python3 scripts/cleanup_drafts.py --batch 001                # dry-run
    python3 scripts/cleanup_drafts.py --batch 001 --go           # 真删
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ZHENGWEN_DIR = ROOT / "正文"
DRAFTS_DIR = ZHENGWEN_DIR / "drafts"
SHENCHA_DIR = ROOT / "审查"


def find_state_file(batch: str) -> Path | None:
    for d in SHENCHA_DIR.glob(f"批次{int(batch):03d}_*"):
        p = d / "state.json"
        if p.exists():
            return p
    return None


def load_done_chapters(batch: str) -> list[str]:
    sf = find_state_file(batch)
    if not sf:
        return []
    data = json.loads(sf.read_text(encoding="utf-8"))
    return [ch for ch, m in data.get("chapters", {}).items() if m.get("stage") == "done"]


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", required=True)
    parser.add_argument("--go", action="store_true", help="真删（默认 dry-run）")
    args = parser.parse_args(argv)

    done = load_done_chapters(args.batch)
    if not done:
        print(f"批次 {args.batch} 没有 stage=done 的章节，不执行。")
        return 0

    if not DRAFTS_DIR.exists():
        print("正文/drafts/ 目录不存在，无 drafts 可清。")
        return 0

    victims: list[Path] = []
    for ch in done:
        for f in DRAFTS_DIR.glob(f"第{int(ch):03d}章_*"):
            victims.append(f)

    if not victims:
        print(f"✅ 批次 {args.batch} 无 drafts 残留。")
        return 0

    print(f"找到 {len(victims)} 个 drafts：")
    for f in victims:
        print(f"  {'删除' if args.go else '[dry-run]'} {f.relative_to(ROOT)}")
    if args.go:
        for f in victims:
            f.unlink()
        print(f"\n✅ 已清理 {len(victims)} 个文件。")
    else:
        print(f"\n（dry-run；加 --go 真删）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
