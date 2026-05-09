#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_rhythm.py — 节奏与排版闸门（单章硬 gate）

针对 v3 流水线发现的"主 agent 自写章节排版退化"问题（2026-04-26 复盘）：
单字/双字独立成段堆积、破折号过密、"（——心声）"内括号嵌套泛滥导致
中文实际字数大幅低于 DoD（3800–4400），且阅读节奏崩坏。

本脚本是 framework/05 §0.6 工具链总表的新成员。

指标（按 framework/06 §1 节奏硬禁条款；阈值以 010-014 基线 + 015 起退化点之间的安全区为准）：
  H-RHYTHM-DASH-OVERFLOW    破折号密度 > 2.5%（基线最劣 1.72% / 退化最佳 3.32%）
  H-RHYTHM-SHORT-LINE       单/双字独立成段占总段数 > 25%（基线最劣 23.9% / 退化普遍 22-59%）
  H-RHYTHM-INNER-PAREN      "（——XXX）"段首心声 > 12 处/千字（基线最劣 11.6/千字）
  H-RHYTHM-LOW-DENSITY      平均段长 < 8.5 中文字（基线最劣 9.08 / 退化最佳 7.34）
  H-RHYTHM-FAKE-COUNT       末行"约 XXXX 字"与实测偏离 > 150（与 check_dod 重叠，用作 fast-fail）

用法：
    python3 scripts/check_rhythm.py 020
    python3 scripts/check_rhythm.py --batch 015-025
    python3 scripts/check_rhythm.py --batch 010-014 --baseline   # 仅打基线，不判 FAIL
    python3 scripts/check_rhythm.py 020 --json

退出码：
    0 = 全 PASS
    1 = 任一指标 FAIL
    2 = 脚本错误（章节文件缺失等）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ZHENGWEN_DIR = ROOT / "正文"


WORDCOUNT_RE = re.compile(r"————本章字数：约\s*(\d{3,5})\s*字————")
DASH_RE = re.compile(r"——")
INNER_PAREN_RE = re.compile(r"^（\s*——")  # 段首"（——"开头的内括号心声


def _count_chinese(s: str) -> int:
    return sum(1 for c in s if "\u4e00" <= c <= "\u9fff")


def _find_chapter_path(no: str) -> Path | None:
    n = int(no)
    matches = list(ZHENGWEN_DIR.glob(f"第{n:03d}章_*.txt"))
    return matches[0] if matches else None


def _strip_meta(text: str) -> str:
    """剔除首行章节标题与末行字数标注，剩下才是正文段落。"""
    lines = text.splitlines()
    if lines and re.match(r"^\s*第\s*\d+\s*章", lines[0]):
        lines = lines[1:]
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and WORDCOUNT_RE.search(lines[-1]):
        lines.pop()
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def _segments(body: str) -> list[str]:
    """段 = 以一个或多个空行分隔的非空行块（保留每段的换行结构）。"""
    raw = re.split(r"\n\s*\n+", body.strip("\n"))
    return [p for p in raw if p.strip()]


# ---------------------------------------------------------------------------
# 指标
# ---------------------------------------------------------------------------

@dataclass
class RhythmReport:
    chapter: str
    chapter_path: str
    chinese_chars: int
    seg_count: int
    avg_seg_len: float
    short_seg_count: int          # 中文字数 ≤ 2 的段数
    short_seg_ratio: float        # 占比
    dash_count: int               # "——" 出现次数
    dash_density_pct: float       # dash_count / chinese_chars × 100
    inner_paren_count: int        # 段首"（——" 心声括号数
    inner_paren_per_kchar: float  # 内括号心声 / 千中文字
    declared_count: int | None
    declared_diff: int | None
    failures: list[dict] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures


# 阈值（与 framework/06 §1 同步）
# 校准依据：010-014 基线全过 + 015-025 退化段全 FAIL
THRESHOLDS = {
    "dash_density_pct_max": 2.5,
    "short_seg_ratio_max": 0.25,
    "inner_paren_per_kchar_max": 12.0,
    "avg_seg_len_min": 8.5,
    "declared_diff_max": 150,
}


def analyze(no: str) -> RhythmReport:
    no = f"{int(no):03d}"
    path = _find_chapter_path(no)
    if not path:
        raise FileNotFoundError(f"正文/第{no}章_*.txt 未找到")

    raw = path.read_text(encoding="utf-8")
    body = _strip_meta(raw)
    segs = _segments(body)
    seg_zh = [_count_chinese(s) for s in segs]
    total_zh = sum(seg_zh)

    # 短段：中文字数 ≤ 2（"嗯。""她说。""——"等）
    short_count = sum(1 for n in seg_zh if n <= 2)
    short_ratio = short_count / len(segs) if segs else 0.0

    dash_count = sum(len(DASH_RE.findall(s)) for s in segs)
    dash_density = (dash_count / total_zh * 100) if total_zh else 0.0

    inner_paren_count = 0
    for seg in segs:
        for line in seg.splitlines():
            if INNER_PAREN_RE.match(line):
                inner_paren_count += 1

    avg_seg_len = (total_zh / len(segs)) if segs else 0.0

    declared = None
    declared_diff = None
    for ln in raw.splitlines()[::-1]:
        m = WORDCOUNT_RE.search(ln)
        if m:
            declared = int(m.group(1))
            declared_diff = abs(declared - _count_chinese(raw))
            break

    inner_paren_per_kchar = (inner_paren_count / total_zh * 1000) if total_zh else 0.0

    failures: list[dict] = []
    if dash_density > THRESHOLDS["dash_density_pct_max"]:
        failures.append({
            "code": "H-RHYTHM-DASH-OVERFLOW",
            "msg": f"破折号密度 {dash_density:.2f}% > 上限 {THRESHOLDS['dash_density_pct_max']}%（{dash_count} 个/{total_zh} 字）",
        })
    if short_ratio > THRESHOLDS["short_seg_ratio_max"]:
        failures.append({
            "code": "H-RHYTHM-SHORT-LINE",
            "msg": f"单/双字短段占比 {short_ratio*100:.1f}% > 上限 {THRESHOLDS['short_seg_ratio_max']*100:.0f}%（{short_count}/{len(segs)} 段）",
        })
    if inner_paren_per_kchar > THRESHOLDS["inner_paren_per_kchar_max"]:
        failures.append({
            "code": "H-RHYTHM-INNER-PAREN",
            "msg": f"段首'（——'心声括号 {inner_paren_per_kchar:.1f}/千字 > 上限 {THRESHOLDS['inner_paren_per_kchar_max']}/千字（实测 {inner_paren_count} 处 / {total_zh} 字）",
        })
    if avg_seg_len and avg_seg_len < THRESHOLDS["avg_seg_len_min"]:
        failures.append({
            "code": "H-RHYTHM-LOW-DENSITY",
            "msg": f"平均段长 {avg_seg_len:.1f} 字 < 下限 {THRESHOLDS['avg_seg_len_min']:.1f} 字（共 {len(segs)} 段）",
        })
    if declared_diff is not None and declared_diff > THRESHOLDS["declared_diff_max"]:
        failures.append({
            "code": "H-RHYTHM-FAKE-COUNT",
            "msg": f"末行标注 {declared} 与实测 {_count_chinese(raw)} 差 {declared_diff} > 150",
        })

    return RhythmReport(
        chapter=no,
        chapter_path=str(path.relative_to(ROOT)),
        chinese_chars=total_zh,
        seg_count=len(segs),
        avg_seg_len=round(avg_seg_len, 2),
        short_seg_count=short_count,
        short_seg_ratio=round(short_ratio, 4),
        dash_count=dash_count,
        dash_density_pct=round(dash_density, 3),
        inner_paren_count=inner_paren_count,
        inner_paren_per_kchar=round(inner_paren_per_kchar, 2),
        declared_count=declared,
        declared_diff=declared_diff,
        failures=failures,
    )


def _print_report(r: RhythmReport, baseline_only: bool) -> None:
    mark = "📊 BASELINE" if baseline_only else ("✅ PASS" if r.passed else f"❌ FAIL ({len(r.failures)})")
    print(f"\n== 第{r.chapter}章 节奏闸: {mark} ==")
    print(f"文件：{r.chapter_path}")
    print(f"  中文字数  : {r.chinese_chars}")
    print(f"  段数      : {r.seg_count}（平均段长 {r.avg_seg_len} 字）")
    print(f"  短段≤2字  : {r.short_seg_count} 段（{r.short_seg_ratio*100:.1f}%）")
    print(f"  破折号    : {r.dash_count} 个（密度 {r.dash_density_pct}%）")
    print(f"  内括号心声: {r.inner_paren_count} 处（{r.inner_paren_per_kchar}/千字，段首'（——'）")
    if r.declared_count is not None:
        print(f"  字数标注  : 标 {r.declared_count} vs 实 {r.chinese_chars}，差 {r.declared_diff}")
    if not baseline_only and r.failures:
        for f in r.failures:
            print(f"  ❌ {f['code']}: {f['msg']}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="章级节奏闸（破折号 / 短段 / 心声括号 / 段长密度）")
    p.add_argument("target", nargs="?", help="章号 020")
    p.add_argument("--batch", help="批次区间 015-025")
    p.add_argument("--baseline", action="store_true", help="仅出基线，不判 FAIL")
    p.add_argument("--json", action="store_true", help="JSON 输出")
    args = p.parse_args(argv)

    if args.batch:
        lo, hi = (int(x) for x in args.batch.split("-"))
        chapters = [f"{i:03d}" for i in range(lo, hi + 1)]
    elif args.target:
        chapters = [f"{int(args.target):03d}"]
    else:
        p.error("必须指定 target 或 --batch")
        return 2

    reports: list[RhythmReport] = []
    for c in chapters:
        try:
            reports.append(analyze(c))
        except FileNotFoundError as e:
            print(f"⚠ {e}", file=sys.stderr)

    if args.json:
        print(json.dumps([asdict(r) for r in reports], ensure_ascii=False, indent=2))
    else:
        for r in reports:
            _print_report(r, args.baseline)

    if args.baseline:
        return 0
    return 0 if all(r.passed for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
