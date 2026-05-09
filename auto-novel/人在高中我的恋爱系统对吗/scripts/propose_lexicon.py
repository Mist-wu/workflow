#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""propose_lexicon.py — AI 味词库自增殖建议器

扫描 审查/第NNN章/润色报告_AI味.md（或合并版 润色报告.md）
提取 LLM 发现但正则词库未收录的"新型模板"，达阈值后提建议。

思路：
- 每份润色报告 C 段里有类似"- [原句] ...：建议改为 ..."的形式。
- 我们把所有 C 段的"原句"抓出来（引号里的短语）、做 n-gram 计数。
- 如果某 n-gram 出现 ≥ MIN_FREQ 次而又不是词库里已有的，列为候选。
- 输出 建议 patch：追加到 framework/06_AI味词库.md。

用法：
    python3 scripts/propose_lexicon.py               # 全书扫
    python3 scripts/propose_lexicon.py --batch 010-014
    python3 scripts/propose_lexicon.py --min-freq 3 --min-len 4 --max-len 10
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SHENCHA_DIR = ROOT / "审查"
FRAMEWORK_DIR = ROOT / "framework"
LEXICON_FILE = FRAMEWORK_DIR / "06_AI味词库.md"


QUOTE_RE = re.compile(r"[\"\"'']([^\"\"''\n]{2,40})[\"\"'']")
CH_RE = re.compile(r"第(\d{3})章")


def collect_polish_reports(batch_range: tuple[int, int] | None) -> list[Path]:
    paths: list[Path] = []
    for dir_ in sorted(SHENCHA_DIR.glob("第*章")):
        m = re.search(r"第(\d+)章", dir_.name)
        if not m:
            continue
        n = int(m.group(1))
        if batch_range and not (batch_range[0] <= n <= batch_range[1]):
            continue
        for fn in ("润色报告_AI味.md", "润色报告.md"):
            p = dir_ / fn
            if p.exists():
                paths.append(p)
                break
    return paths


def extract_c_section(text: str) -> str:
    m = re.search(r"##\s*C[\s\S]*?(?=##\s*[BDEF]|$)", text)
    return m.group(0) if m else ""


def extract_quoted_phrases(c_seg: str) -> list[str]:
    return QUOTE_RE.findall(c_seg)


def load_existing_lexicon() -> set[str]:
    if not LEXICON_FILE.exists():
        return set()
    text = LEXICON_FILE.read_text(encoding="utf-8")
    # 收集所有反引号包裹和中文引号包裹的短语作为已有词
    existing: set[str] = set()
    for m in re.finditer(r"[`\"\"''「]([^\s`\"\"''「」]{2,20})[`\"\"''」]", text):
        existing.add(m.group(1))
    return existing


def ngrams(s: str, n: int) -> list[str]:
    return [s[i:i+n] for i in range(len(s) - n + 1)]


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", help="区间 010-014")
    parser.add_argument("--min-freq", type=int, default=3,
                        help="最小频次（≥ 该值才提建议）")
    parser.add_argument("--min-len", type=int, default=4)
    parser.add_argument("--max-len", type=int, default=8)
    parser.add_argument("--write-patch", action="store_true",
                        help="生成补丁文件 framework/06_AI味词库.patch.md")
    args = parser.parse_args(argv)

    batch_range = None
    if args.batch:
        lo, hi = (int(x) for x in args.batch.split("-"))
        batch_range = (lo, hi)

    reports = collect_polish_reports(batch_range)
    if not reports:
        print("未找到任何润色-AI味报告。", file=sys.stderr)
        return 1

    existing = load_existing_lexicon()
    phrase_counter: Counter[str] = Counter()
    source_map: dict[str, set[str]] = {}

    for p in reports:
        text = p.read_text(encoding="utf-8")
        c_seg = extract_c_section(text)
        quotes = extract_quoted_phrases(c_seg)
        chap = CH_RE.search(p.as_posix())
        ch = chap.group(1) if chap else "???"
        for q in quotes:
            # 对每个引用句做 n-gram 统计
            q = q.strip()
            # 去除过短或含英文/数字的
            if len(q) < args.min_len:
                continue
            for n in range(args.min_len, min(args.max_len, len(q)) + 1):
                for g in ngrams(q, n):
                    if not re.fullmatch(r"[\u4e00-\u9fff，。！？、；：]+", g):
                        continue
                    if g in existing:
                        continue
                    phrase_counter[g] += 1
                    source_map.setdefault(g, set()).add(ch)

    # 挑出超阈值的候选；同时去掉被更短的候选覆盖（保留"最大满足"）
    candidates = [(g, c) for g, c in phrase_counter.items() if c >= args.min_freq]
    candidates.sort(key=lambda x: (-x[1], -len(x[0])))

    # 做"子串过滤"：如果 X 是 Y 的子串且 Y 也在候选里，只保留 Y
    kept = []
    for g, c in candidates:
        if any(g in other and len(g) < len(other) for other, _ in kept):
            continue
        kept.append((g, c))

    if not kept:
        print("✅ 未发现达阈值的新模板。")
        return 0

    print(f"== 发现 {len(kept)} 个候选新模板 ==")
    print(f"{'短语':<20} 频次 出现章节")
    for g, c in kept[:30]:
        chapters = ",".join(sorted(source_map[g]))
        print(f"{g:<20} {c:>3}   {chapters}")

    if args.write_patch:
        patch_path = FRAMEWORK_DIR / "06_AI味词库.patch.md"
        lines = [
            "# 06_AI味词库 新候选（由 propose_lexicon.py 自动生成）",
            f"# 来源：{len(reports)} 份润色-AI味报告；阈值 min_freq={args.min_freq}",
            "# 用户审阅后：✅ 条目追加到 06_AI味词库.md §1.X 并同步 scripts/scan_ai_templates.py HARD_BAN_PHRASES",
            "",
        ]
        for g, c in kept:
            chapters = ",".join(sorted(source_map[g]))
            lines.append(f"- `{g}` （{c} 次；章节 {chapters}）")
        patch_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\n✅ 补丁已写入 {patch_path.relative_to(ROOT)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
