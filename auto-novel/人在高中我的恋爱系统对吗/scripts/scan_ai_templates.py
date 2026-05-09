#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scan_ai_templates.py — AI 味确定性预筛

用法：
    python scripts/scan_ai_templates.py 正文/第007章_广播站报到.txt
    python scripts/scan_ai_templates.py 正文/第007章_广播站报到.txt --out 审查/第007章/AI味命中清单.txt
    python scripts/scan_ai_templates.py --batch 010-014           # 批量扫整批
    python scripts/scan_ai_templates.py --all                     # 扫所有正文章节

词库来源：framework/06_AI味词库.md
输出：审查/第NNN章/AI味命中清单.txt

约定：
- 第一行必须是章节标题，从标题提取章号 (第0NN章)。
- 脚本不做 LLM 判断，只做正则级命中；改写由润色-AI味 subagent 负责。
- 同一行多次命中同一规则只计一次（避免重复噪声）。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parent.parent
FRAMEWORK_DIR = ROOT / "framework"
ZHENGWEN_DIR = ROOT / "正文"
SHENCHA_DIR = ROOT / "审查"
LEXICON_FILE = FRAMEWORK_DIR / "06_AI味词库.md"
EXEMPT_YAML = FRAMEWORK_DIR / "config" / "exempt_chapters.yaml"


def _load_exempt_chapters() -> tuple[set[str], dict[str, object]]:
    """读取 config/exempt_chapters.yaml。yaml 缺失或解析失败时退回空集合。"""
    default_rules = {
        "hard_ban_threshold_exempt": 3,
        "hard_ban_threshold_normal": 1,
        "heart_skip_exempt": 3,
        "heart_skip_normal": 1,
    }
    if not EXEMPT_YAML.exists():
        return set(), default_rules
    try:
        import yaml  # type: ignore
    except ImportError:
        return set(), default_rules
    try:
        data = yaml.safe_load(EXEMPT_YAML.read_text(encoding="utf-8")) or {}
    except Exception:
        return set(), default_rules
    exempt = {str(c) for c in (data.get("exempt_chapters") or [])}
    rules = {**default_rules, **(data.get("rules") or {})}
    return exempt, rules


EXEMPT_CHAPTERS, EXEMPT_RULES = _load_exempt_chapters()


# ---------------------------------------------------------------------------
# 词库（与 06_AI味词库.md 对齐；正则和阈值在一个地方维护）
# ---------------------------------------------------------------------------

HARD_BAN_PHRASES: list[tuple[str, str]] = [
    # (id_section, phrase)
    ("§1.1", "她不由得"),
    ("§1.1", "他不由得"),
    ("§1.1", "嘴角微扬"),
    ("§1.1", "嘴角勾起"),
    ("§1.1", "眼中闪过一丝"),
    ("§1.1", "空气仿佛凝固"),
    ("§1.1", "空气凝固"),
    ("§1.1", "莫名其妙地"),
    ("§1.1", "不知为何"),
    ("§1.1", "不知怎的"),
    ("§1.1", "刹那间"),
    ("§1.1", "一时间"),
    ("§1.1", "说不清道不明"),
    ("§1.1", "心底泛起"),
    ("§1.1", "心头一颤"),
    ("§1.1", "心头一紧"),
    ("§1.1", "涌上心头"),
    ("§1.1", "不由自主地"),
    ("§1.1", "情不自禁地"),
    ("§1.1", "鬼使神差地"),
    ("§1.2", "就这样，"),
    ("§1.2", "就这样。"),
    ("§1.2", "就这么"),
    ("§1.2", "时间一分一秒"),
    ("§1.2", "夜色渐渐深"),
    ("§1.2", "不知过了多久"),
    ("§1.2", "不知过了多长"),
    ("§1.2", "就在这一刻"),
    ("§1.2", "那一刻"),
    ("§1.3", "像星星一样"),
    ("§1.3", "眼睛像星星"),
    ("§1.3", "笑容像阳光"),
    ("§1.3", "声音像风铃"),
    ("§1.3", "像花一样绽放"),
    ("§1.3", "如沐春风"),
    ("§1.3", "煮熟的虾"),
]

SOFT_LIMIT_HEART_SKIP = "心跳漏了一拍"   # §2.1 单章 > 1 即红（日常章）

# §2.2 叠词三连 — 同段内
SOFT_LIMIT_DIEDZ_RE = re.compile(
    r"(?:[静默悄缓轻]\S{0,2}地[^\n]{0,30}){3,}"
)

# §2.3 "她不/他不" 三并
SOFT_LIMIT_SHEBU_RE = re.compile(
    r"(她不\S{0,8}[，。 ]\s*){2,}她不"
)
SOFT_LIMIT_TABU_RE = re.compile(
    r"(他不\S{0,8}[，。 ]\s*){2,}他不"
)

# §2.4 程度副词堆砌（单段内 >=4 次）
DEGREE_RE = re.compile(r"非常|十分|格外|极其|特别")


TEMPLATE_PATTERNS: list[tuple[str, str, re.Pattern[str]]] = [
    ("T01", "眼里闪过 X", re.compile(r"(?:她|他)的?(?:眼|眼睛|眼眸)里?闪过.{1,8}")),
    ("T02", "世界仿佛静止", re.compile(r"仿佛整个世界.{0,10}静止")),
    ("T03", "复杂情绪涌上", re.compile(r"复杂的情绪.{0,10}[涌浮泛]")),
    ("T04", "情绪三段排比", re.compile(r"既有.{1,10}又有.{1,10}还有.{1,10}")),
    ("T05", "像是…又像是…", re.compile(r"像是.{0,4}又像是.{0,6}")),
    ("T06", "同段叠词", re.compile(r"(?:轻轻|悄悄|默默|静静)地[^。\n]{0,20}(?:轻轻|悄悄|默默|静静)地")),
    ("T07", "某个微妙的瞬间", re.compile(r"(?:一|某)个.{0,6}的(?:瞬间|刹那|瞬息)")),
    ("T08", "不经意间", re.compile(r"不经意间")),
    ("T09", "俗套五官喻", re.compile(r"(?:她|他)的.{1,4}(?:清澈|澄澈|明亮)(?:得|地)?.{0,8}(?:像|如|仿佛)")),
    ("T10", "突然之间 赘余", re.compile(r"(?:突然|忽然)之间")),
]


WHITELIST_SNIPPETS = [
    "对吗？",
    "陈屿。",
    "系统",  # 系统吐槽相关表述保留
]


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class Hit:
    line_no: int
    category: str   # "硬禁用" / "软限额" / "句式模板"
    rule_id: str    # §1.1 / §2.1 / T03 ...
    phrase: str
    snippet: str

    def render(self) -> str:
        return f"L{self.line_no:04d} {self.rule_id} \"{self.phrase}\" ← 原句：{self.snippet.strip()}"


@dataclass
class ScanResult:
    chapter_no: str
    chapter_path: Path
    total_chars: int
    hits_hard: list[Hit] = field(default_factory=list)
    hits_soft: list[Hit] = field(default_factory=list)
    hits_tpl: list[Hit] = field(default_factory=list)

    @property
    def total_hits(self) -> int:
        return len(self.hits_hard) + len(self.hits_soft) + len(self.hits_tpl)


# ---------------------------------------------------------------------------
# 核心扫描逻辑
# ---------------------------------------------------------------------------

CHAPTER_RE = re.compile(r"第\s*0*(\d+)\s*章")


def detect_chapter_no(path: Path, first_line: str) -> str:
    """优先从文件首行提取章号（三位数字），退路用文件名。"""
    m = CHAPTER_RE.search(first_line)
    if not m:
        m = CHAPTER_RE.search(path.name)
    if not m:
        return "000"
    return f"{int(m.group(1)):03d}"


def _is_whitelisted(snippet: str) -> bool:
    return any(w in snippet for w in WHITELIST_SNIPPETS)


def _scan_hard_ban(lines: list[str]) -> list[Hit]:
    hits: list[Hit] = []
    for idx, line in enumerate(lines, start=1):
        # 系统方括号和心声圆括号内的内容跳过（属于设定语气）
        if line.startswith("【") or line.startswith("（"):
            continue
        for section, phrase in HARD_BAN_PHRASES:
            if phrase in line:
                # 避免同一条在同一行被重复算
                if any(h.line_no == idx and h.phrase == phrase for h in hits):
                    continue
                hits.append(Hit(idx, "硬禁用", section, phrase, line))
    return hits


def _scan_soft_limits(lines: list[str], full_text: str) -> list[Hit]:
    hits: list[Hit] = []

    # §2.1 心跳漏了一拍：单章日常章限额 1 次
    skip_count = full_text.count(SOFT_LIMIT_HEART_SKIP)
    if skip_count > 1:
        # 标出所有出现位置
        for idx, line in enumerate(lines, start=1):
            if SOFT_LIMIT_HEART_SKIP in line:
                hits.append(Hit(
                    idx, "软限额", "§2.1",
                    f"{SOFT_LIMIT_HEART_SKIP}（本章出现 {skip_count} 次，限额 1）",
                    line,
                ))

    # 以段为单位扫描
    paragraphs = _iter_paragraphs(lines)
    for para_lines, start_line in paragraphs:
        para_text = "\n".join(para_lines)

        # §2.2 叠词三连
        if SOFT_LIMIT_DIEDZ_RE.search(para_text):
            hits.append(Hit(
                start_line, "软限额", "§2.2",
                "叠词三连（静静/默默/悄悄/缓缓/轻轻）",
                para_text,
            ))

        # §2.3 她不/他不 三并
        if SOFT_LIMIT_SHEBU_RE.search(para_text):
            hits.append(Hit(
                start_line, "软限额", "§2.3",
                "\"她不\"三并排比",
                para_text,
            ))
        if SOFT_LIMIT_TABU_RE.search(para_text):
            hits.append(Hit(
                start_line, "软限额", "§2.3",
                "\"他不\"三并排比",
                para_text,
            ))

        # §2.4 程度副词 ≥ 4
        if len(DEGREE_RE.findall(para_text)) >= 4:
            hits.append(Hit(
                start_line, "软限额", "§2.4",
                f"程度副词堆砌（{len(DEGREE_RE.findall(para_text))} 次/段）",
                para_text,
            ))

    return hits


def _scan_templates(lines: list[str]) -> list[Hit]:
    hits: list[Hit] = []
    for idx, line in enumerate(lines, start=1):
        if line.startswith("【") or line.startswith("（"):
            continue
        if _is_whitelisted(line):
            # 只是部分白名单短语存在不豁免整行；只豁免整行 == 白名单短语
            pass
        for tid, name, pat in TEMPLATE_PATTERNS:
            if pat.search(line):
                hits.append(Hit(idx, "句式模板", tid, name, line))
    return hits


def _iter_paragraphs(lines: list[str]) -> Iterable[tuple[list[str], int]]:
    buf: list[str] = []
    start = 0
    for idx, line in enumerate(lines, start=1):
        if line.strip():
            if not buf:
                start = idx
            buf.append(line)
        else:
            if buf:
                yield buf, start
                buf = []
    if buf:
        yield buf, start


def scan_chapter(path: Path) -> ScanResult:
    text = path.read_text(encoding="utf-8")
    raw_lines = text.splitlines()
    lines = raw_lines  # 保留空行索引以便段落识别
    first_line = raw_lines[0] if raw_lines else ""
    chapter_no = detect_chapter_no(path, first_line)
    total_chars = sum(1 for c in text if not c.isspace())
    result = ScanResult(chapter_no=chapter_no, chapter_path=path, total_chars=total_chars)
    result.hits_hard = _scan_hard_ban(lines)
    result.hits_soft = _scan_soft_limits(lines, text)
    result.hits_tpl = _scan_templates(lines)
    return result


# ---------------------------------------------------------------------------
# 报告格式化 & 落盘
# ---------------------------------------------------------------------------

def render_report(result: ScanResult) -> str:
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    density = result.total_hits * 1000 / max(result.total_chars, 1)
    is_exempt = result.chapter_no in EXEMPT_CHAPTERS
    threshold = (EXEMPT_RULES["hard_ban_threshold_exempt"] if is_exempt
                 else EXEMPT_RULES["hard_ban_threshold_normal"])
    exempt_note = "（抒情豁免章）" if is_exempt else ""
    lines = [
        f"# 第{result.chapter_no}章 AI 味命中清单{exempt_note}（生成时间：{now}）",
        f"# 命中来源：framework/06_AI味词库.md",
        f"# 扫描文件：{result.chapter_path.relative_to(ROOT)}",
        f"# 硬禁用阈值：{threshold} 条（超过即 gate fail）",
        "",
        "[硬禁用]",
    ]
    if result.hits_hard:
        lines.extend(h.render() for h in result.hits_hard)
    else:
        lines.append("（无）")
    lines += ["", "[软限额]"]
    if result.hits_soft:
        lines.extend(h.render() for h in result.hits_soft)
    else:
        lines.append("（无）")
    lines += ["", "[句式模板]"]
    if result.hits_tpl:
        lines.extend(h.render() for h in result.hits_tpl)
    else:
        lines.append("（无）")
    lines += [
        "",
        "[统计]",
        f"硬禁用：{len(result.hits_hard)} 条",
        f"软限额：{len(result.hits_soft)} 条",
        f"句式模板：{len(result.hits_tpl)} 条",
        f"总计：{result.total_hits} 条 / 本章字数 {result.total_chars} → 密度 {density:.2f} 条/千字",
        "",
    ]
    return "\n".join(lines)


def default_output_path(chapter_no: str) -> Path:
    out_dir = SHENCHA_DIR / f"第{chapter_no}章"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / "AI味命中清单.txt"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _resolve_chapter_paths(arg: str) -> list[Path]:
    """支持：绝对/相对路径、章号（例 '007'）、'010-014' 批次区间。"""
    if "-" in arg and all(p.isdigit() for p in arg.split("-")):
        start, end = (int(p) for p in arg.split("-"))
        return [p for p in sorted(ZHENGWEN_DIR.glob("第*章_*.txt"))
                if start <= int(CHAPTER_RE.search(p.name).group(1)) <= end]
    if arg.isdigit():
        return list(ZHENGWEN_DIR.glob(f"第{int(arg):03d}章_*.txt"))
    p = Path(arg)
    if not p.is_absolute():
        p = (Path.cwd() / arg).resolve()
    return [p]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AI 味确定性预筛")
    parser.add_argument("target", nargs="?", help="章号(007) / 区间(010-014) / 路径")
    parser.add_argument("--all", action="store_true", help="扫描 正文/ 下全部章节")
    parser.add_argument("--out", help="自定义输出路径（单章时有效）")
    parser.add_argument("--stdout", action="store_true", help="只打印到 stdout，不落盘")
    args = parser.parse_args(argv)

    paths: list[Path]
    if args.all:
        paths = sorted(ZHENGWEN_DIR.glob("第*章_*.txt"))
    elif args.target:
        paths = _resolve_chapter_paths(args.target)
    else:
        parser.error("必须提供 target 或 --all")
        return 2

    if not paths:
        print("未找到匹配章节。", file=sys.stderr)
        return 1

    summary_rows: list[tuple[str, int, int, int, int]] = []
    for path in paths:
        result = scan_chapter(path)
        report = render_report(result)
        if args.stdout:
            print(report)
        else:
            out_path = Path(args.out) if (args.out and len(paths) == 1) else default_output_path(result.chapter_no)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(report, encoding="utf-8")
            print(f"✅ 第{result.chapter_no}章 → {out_path.relative_to(ROOT)}  "
                  f"(硬{len(result.hits_hard)} 软{len(result.hits_soft)} 模{len(result.hits_tpl)})")
        summary_rows.append((
            result.chapter_no,
            len(result.hits_hard),
            len(result.hits_soft),
            len(result.hits_tpl),
            result.total_hits,
        ))

    if len(summary_rows) > 1:
        print("\n== 批量汇总 ==")
        print("章号  硬禁用  软限额  句式模板  合计")
        for row in summary_rows:
            print(f"{row[0]}    {row[1]:>3}    {row[2]:>3}      {row[3]:>3}      {row[4]:>3}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
