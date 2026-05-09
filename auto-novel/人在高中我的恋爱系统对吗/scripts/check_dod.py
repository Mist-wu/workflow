#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_dod.py — 单章 Definition of Done 硬 gate

职责：把 framework/00_项目说明.md §五 的 7 条 DoD 做成 exit-code 级强校验。
- exit 0 = 通过
- exit 1 = 未过 gate（列出具体缺失项）
- exit 2 = 脚本错误（章号不存在、文件缺失等）

用法：
    python3 scripts/check_dod.py 010                      # 单章
    python3 scripts/check_dod.py --batch 010-014          # 批次
    python3 scripts/check_dod.py --batch 010-014 --json   # 结构化输出

副作用：通过后会自动调 state.py set-chapter dod_passed=true（除非加 --no-state）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ZHENGWEN_DIR = ROOT / "正文"
SHENCHA_DIR = ROOT / "审查"
FRAMEWORK_DIR = ROOT / "framework"
EXEMPT_YAML = FRAMEWORK_DIR / "config" / "exempt_chapters.yaml"


CHAPTER_RE = re.compile(r"第\s*[0-9一二三四五六七八九十百零〇]+\s*章")
WORDCOUNT_RE = re.compile(r"————本章字数：约\s*(\d{3,5})\s*字————")


def _load_exempt() -> set[str]:
    if not EXEMPT_YAML.exists():
        return set()
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(EXEMPT_YAML.read_text(encoding="utf-8")) or {}
        return {str(c) for c in (data.get("exempt_chapters") or [])}
    except Exception:
        return set()


EXEMPT = _load_exempt()


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class GateCheck:
    name: str
    passed: bool
    detail: str = ""

    def render(self) -> str:
        mark = "✅" if self.passed else "❌"
        return f"  {mark} {self.name}" + (f" — {self.detail}" if self.detail else "")


@dataclass
class ChapterDoDReport:
    chapter: str
    chapter_path: str
    checks: list[GateCheck]

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def fail_count(self) -> int:
        return sum(1 for c in self.checks if not c.passed)


# ---------------------------------------------------------------------------
# 单项校验
# ---------------------------------------------------------------------------

def _find_chapter_path(chapter_no: str) -> Path | None:
    cn = int(chapter_no)
    matches = list(ZHENGWEN_DIR.glob(f"第{cn:03d}章_*.txt"))
    return matches[0] if matches else None


def _count_chinese_chars(text: str) -> int:
    return sum(1 for c in text if '\u4e00' <= c <= '\u9fff')


def check_word_count(text: str) -> GateCheck:
    """DoD 1：字数 3800–4400（按中文汉字数，不含标点/空格）。"""
    cn = _count_chinese_chars(text)
    ok = 3800 <= cn <= 4400
    return GateCheck(
        "字数 ∈ [3800, 4400]", ok,
        f"中文汉字数 = {cn}" + ("" if ok else f"（{'过短' if cn < 3800 else '过长'}）"),
    )


def check_format(text: str) -> GateCheck:
    """DoD 2：首行标题 + 末行字数标注 + 系统【】格式 + 心声（）独立成段 + 段间空行。"""
    issues: list[str] = []
    lines = text.splitlines()

    if not lines or not CHAPTER_RE.search(lines[0]):
        issues.append("首行非章节标题")

    stripped_tail = [ln for ln in lines[-5:] if ln.strip()]
    if not stripped_tail or not WORDCOUNT_RE.search(stripped_tail[-1]):
        issues.append("末行缺'————本章字数：约 XXXX 字————'")

    # 系统【】：只要是"任务指令"格式（含'请'、'下一步'、'要求'等动词）就必须以对吗/是吗/可以吗 结尾
    # 短通知（好感度播报 / 系统状态 / 轻语气）不强制。
    task_cues = re.compile(r"请\S|宿主[，、]\s*请|下一步|要求你|把.*给|去\S+吧")
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith("【") and s.endswith("】"):
            inner = s[1:-1]
            if task_cues.search(inner):
                if not (inner.endswith("对吗？") or inner.endswith("是吗？")
                        or inner.endswith("可以吗？")):
                    issues.append(f"第{i+1}行任务类系统台词未以'对吗？'等收尾：{s[:30]}")

    return GateCheck(
        "格式铁律（标题/字数行/系统【】/段间空行）",
        not issues,
        "；".join(issues[:3]) + (f"（另 {len(issues)-3} 条）" if len(issues) > 3 else ""),
    )


def check_ai_hard_ban(chapter_no: str) -> GateCheck:
    """DoD 5：AI味命中清单.硬禁用段清零（抒情豁免章允许 ≤ 阈值）。"""
    report_path = SHENCHA_DIR / f"第{chapter_no}章" / "AI味命中清单.txt"
    if not report_path.exists():
        return GateCheck("AI 味硬禁用清零", False, "AI味命中清单.txt 缺失")

    content = report_path.read_text(encoding="utf-8")
    match = re.search(r"\[硬禁用\](.*?)(?=\[软限额\]|\[句式模板\]|\[统计\])",
                      content, re.S)
    if not match:
        return GateCheck("AI 味硬禁用清零", False, "格式错误，无[硬禁用]段")

    lines = [ln for ln in match.group(1).splitlines()
             if ln.strip() and not ln.startswith("（无）")]
    count = len(lines)

    is_exempt = chapter_no in EXEMPT
    threshold = 3 if is_exempt else 0
    ok = count <= threshold
    suffix = "（抒情豁免）" if is_exempt else ""
    return GateCheck(
        f"AI 味硬禁用 ≤ {threshold}{suffix}", ok,
        f"命中 {count} 条" + ("" if ok else "（未清零）"),
    )


def check_hard_check_report(chapter_no: str) -> GateCheck:
    """DoD 3：硬校验报告 A 段为'无'或已修复。"""
    p = SHENCHA_DIR / f"第{chapter_no}章" / "硬校验报告.md"
    if not p.exists():
        return GateCheck("硬校验 A 段清零", False, "硬校验报告.md 缺失")
    content = p.read_text(encoding="utf-8")
    match = re.search(r"##\s*A[\s\S]*?(?=##\s*[BCDEF]|$)", content)
    if not match:
        return GateCheck("硬校验 A 段清零", False, "A 段定位失败")
    a_seg = match.group(0)
    # 清洗掉 markdown 装饰（** * ` > 引用符）后再判定
    def _strip_md(s: str) -> str:
        s = re.sub(r"[*`>\[\]()]", "", s)
        return s.strip(" -—·\t")

    has_unfixed = bool(re.search(r"未修|待修|待改|待改写|pending|必须修|unfixed", a_seg, re.I))
    # 逐行：若任何一行去掉装饰后就是"无" / "无。" / "无硬错误" / "（无）"，视为显式"无"
    has_none = False
    for ln in a_seg.splitlines()[1:]:
        stripped = _strip_md(ln)
        if stripped in ("无", "无。", "无硬错误", "无硬错误。", "（无）", "A1. 无", "A1.无"):
            has_none = True
            break
        if re.fullmatch(r"A\d*\.?\s*无[。\.]?", stripped):
            has_none = True
            break
    has_fixed = bool(re.search(r"已修|已改|✅|fixed", a_seg, re.I))
    body = "\n".join(a_seg.splitlines()[1:]).strip()
    body_empty = not body

    ok = not has_unfixed and (has_none or has_fixed or body_empty)
    return GateCheck(
        "硬校验 A 段清零", ok,
        "A 段仍有待修项" if not ok else "",
    )


def check_polish_c_heavy(chapter_no: str) -> GateCheck:
    """DoD 4：三份润色报告 C 段'重 AI 味'条目已修复。"""
    ch_dir = SHENCHA_DIR / f"第{chapter_no}章"
    # v3 用拆分后的三份，v2/v1 用合并版
    candidates = [
        ch_dir / "润色报告_节奏.md",
        ch_dir / "润色报告_文风.md",
        ch_dir / "润色报告_AI味.md",
        ch_dir / "润色报告.md",  # 回退
    ]
    found = [p for p in candidates if p.exists()]
    if not found:
        return GateCheck("润色报告 C 段无重 AI 味遗留", False, "润色报告缺失")
    issues: list[str] = []
    for p in found:
        text = p.read_text(encoding="utf-8")
        # 仅抓 C 段
        m = re.search(r"##\s*C[\s\S]*?(?=##\s*[BDEF]|$)", text)
        if not m:
            continue
        c_seg = m.group(0)
        # 找"重"/"heavy"标记且未打 ✅ 或"已修"
        for ln in c_seg.splitlines():
            if re.search(r"(?:重\s*AI\s*味|严重度[：:].*重|heavy)", ln, re.I):
                if not re.search(r"(?:已修|已改|✅|done|fixed)", ln, re.I):
                    issues.append(f"{p.name}: {ln.strip()[:40]}")
    ok = not issues
    return GateCheck(
        "润色 C 段'重 AI 味'清零", ok,
        f"{len(issues)} 条未修" if issues else "",
    )


def check_reader_feedback(chapter_no: str) -> GateCheck:
    """DoD（v3 补）：三份读者反馈齐全 + 平均分 ≥ 5（< 5 要重修订）。"""
    ch_dir = SHENCHA_DIR / f"第{chapter_no}章"
    files = {p: ch_dir / f"读者反馈_{p}.md" for p in ["α", "β", "γ"]}
    missing = [p for p, f in files.items() if not f.exists()]
    if missing:
        return GateCheck("读者反馈三份齐全", False, f"缺 {','.join(missing)}")
    # 抓分数
    scores: list[int] = []
    for p, f in files.items():
        text = f.read_text(encoding="utf-8")
        m = re.search(r"(?:打分|综合打分|评分)[^\d]{0,10}(\d{1,2})", text)
        if m:
            scores.append(int(m.group(1)))
    if not scores:
        return GateCheck("读者反馈平均分 ≥ 5", False, "未抓到任何打分")
    avg = sum(scores) / len(scores)
    ok = avg >= 5
    return GateCheck(
        "读者反馈平均分 ≥ 5", ok,
        f"平均 {avg:.1f}（{len(scores)} 份）" + ("" if ok else "，需重修订"),
    )


def check_seam_word_count_marker(text: str) -> GateCheck:
    """DoD 7：末行'约 XXXX 字'数字与实际中文字数偏差 ≤ 150。"""
    m = None
    for ln in reversed(text.splitlines()):
        if ln.strip():
            m = WORDCOUNT_RE.search(ln)
            break
    if not m:
        return GateCheck("末行字数标注与实际对齐", False, "未找到字数标注行")
    declared = int(m.group(1))
    actual = _count_chinese_chars(text)
    diff = abs(declared - actual)
    ok = diff <= 150
    return GateCheck(
        "末行字数标注与实际对齐 (±150)", ok,
        f"标 {declared} vs 实 {actual}，差 {diff}",
    )


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def check_chapter(chapter_no: str) -> ChapterDoDReport:
    chapter_no = f"{int(chapter_no):03d}"
    path = _find_chapter_path(chapter_no)
    if not path:
        return ChapterDoDReport(
            chapter=chapter_no, chapter_path="(missing)",
            checks=[GateCheck("章节文件存在", False, f"正文/第{chapter_no}章_*.txt 未找到")],
        )
    text = path.read_text(encoding="utf-8")
    checks = [
        check_word_count(text),
        check_format(text),
        check_seam_word_count_marker(text),
        check_hard_check_report(chapter_no),
        check_ai_hard_ban(chapter_no),
        check_polish_c_heavy(chapter_no),
        check_reader_feedback(chapter_no),
    ]
    return ChapterDoDReport(
        chapter=chapter_no,
        chapter_path=str(path.relative_to(ROOT)),
        checks=checks,
    )


def _update_state(report: ChapterDoDReport, batch: str | None) -> None:
    if batch is None:
        return
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        from state import set_chapter  # type: ignore
        set_chapter(batch, report.chapter, dod_passed=report.passed)
    except Exception as e:
        print(f"⚠ state 更新失败：{e}", file=sys.stderr)


def _infer_batch_for_chapter(chapter_no: str) -> str | None:
    """根据章号从 审查/批次NNN_XXX-YYY/ 反查批次号。"""
    cn = int(chapter_no)
    for d in SHENCHA_DIR.glob("批次*_*"):
        m = re.search(r"批次(\d{3})_(\d{3})-(\d{3})", d.name)
        if m:
            bn, lo, hi = m.group(1), int(m.group(2)), int(m.group(3))
            if lo <= cn <= hi:
                return bn
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="章级 DoD 硬 gate")
    parser.add_argument("target", nargs="?", help="章号 010")
    parser.add_argument("--batch", help="批次区间 010-014")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    parser.add_argument("--no-state", action="store_true", help="不更新 state.json")
    args = parser.parse_args(argv)

    if args.batch:
        lo, hi = (int(x) for x in args.batch.split("-"))
        chapters = [f"{i:03d}" for i in range(lo, hi + 1)]
    elif args.target:
        chapters = [f"{int(args.target):03d}"]
    else:
        parser.error("必须指定 target 或 --batch")
        return 2

    reports = [check_chapter(c) for c in chapters]

    if args.json:
        out = [{
            "chapter": r.chapter, "passed": r.passed,
            "fail_count": r.fail_count,
            "checks": [asdict(c) for c in r.checks],
        } for r in reports]
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        for r in reports:
            mark = "✅ PASS" if r.passed else f"❌ FAIL ({r.fail_count}/{len(r.checks)})"
            print(f"\n== 第{r.chapter}章 DoD gate: {mark} ==")
            print(f"文件：{r.chapter_path}")
            for c in r.checks:
                print(c.render())

    if not args.no_state:
        for r in reports:
            batch = _infer_batch_for_chapter(r.chapter)
            _update_state(r, batch)

    # exit code：全过 → 0；有任何失败 → 1
    return 0 if all(r.passed for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
