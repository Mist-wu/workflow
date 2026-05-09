#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_reader_prompts.py — 读者模拟 subagent 批量派单

用法：
    # 单章（3 份）
    python3 scripts/gen_reader_prompts.py 007

    # 批次（15 份：5 章 × 3 人格）
    python3 scripts/gen_reader_prompts.py --batch 010-014

    # 指定人格（默认三人格全开）
    python3 scripts/gen_reader_prompts.py 007 --personas α

输出：
    审查/_reader_prompts/第NNN章_{α|β|γ}.prompt.txt
    每份是一段可直接粘进 Task 工具的完整 prompt 字符串。

设计原则：
- 读者模拟 subagent 不读 framework、不读细纲、不读事实表，只读本章正文 + 上一章末尾。
- 脚本不直接调任何 LLM；它只组装 prompt，由主 agent 决定怎么派（Task 工具、手工复制、MCP 等）。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ZHENGWEN_DIR = ROOT / "正文"
SHENCHA_DIR = ROOT / "审查"
PROMPT_OUT_DIR = SHENCHA_DIR / "_reader_prompts"

CHAPTER_RE = re.compile(r"第\s*0*(\d+)\s*章")


PERSONA_SPEC: dict[str, dict[str, str]] = {
    "α": {
        "name": "追更挑剔党",
        "setup": (
            "你追这本书已经一个月了，前面每一章都看过，对主角陈屿 / 系统 / 五位女角的调性很熟。"
            "你对套路敏感，不耐烦废话。你关心：这一章爽不爽、有没有让你想弃书的瞬间、"
            "作者有没有在灌水。"
        ),
        "ask": (
            "- 最爽 / 最戳的一处（必须引用原句或具体画面，≤ 30 字）\n"
            "- 最想跳过的一段（指明位置 + 一句为什么）\n"
            "- 本章如果发在追更群，你会【弃/吐槽/平淡/追/安利】打勾哪一个\n"
            "- 综合打分（1–10 分）"
        ),
    },
    "β": {
        "name": "入坑三章党",
        "setup": (
            "你只看过前 3 章，刚追到本章。你对人物关系不熟，对系统机制只有模糊印象。"
            "你关心：我看得懂吗？信息量有没有压死我？哪里让我想翻回前面查？"
        ),
        "ask": (
            "- 最爽 / 最戳的一处（引用原句或画面，≤ 30 字）\n"
            "- 最晕的一段（位置 + 是谁没交代清楚 / 哪个设定没理解）\n"
            "- 是否想继续追（是 / 否 / 看看第 4 章再说）\n"
            "- 综合打分（1–10 分）"
        ),
    },
    "γ": {
        "name": "预判党",
        "setup": (
            "你刚看完本章，要**立刻预测下一章会发生什么**。你对套路烂熟于心：表白 → 误会 → 雨中追逐 → 第三者出现 → 冰释前嫌。"
            "如果你的预测命中率太高，说明作者写得太套路。"
        ),
        "ask": (
            "- 本章最套路的一处（≤ 30 字，指出是哪种常见套路）\n"
            "- 本章最反套路/新鲜的一处（≤ 30 字）\n"
            "- 下一章 3 条预测（每条一句，尽量具体，例：『林知夏会借口还作业来陈屿家』）\n"
            "- 综合打分（1–10 分）"
        ),
    },
}


def find_chapter(no: int) -> Path | None:
    cands = list(ZHENGWEN_DIR.glob(f"第{no:03d}章_*.txt"))
    return cands[0] if cands else None


def build_prompt(chapter_no: int, persona: str) -> str:
    p = PERSONA_SPEC[persona]
    chapter_path = find_chapter(chapter_no)
    prev_path = find_chapter(chapter_no - 1)
    if not chapter_path:
        raise FileNotFoundError(f"找不到第 {chapter_no:03d} 章正文文件")

    ch_rel = chapter_path.relative_to(ROOT)
    if prev_path:
        prev_rel = prev_path.relative_to(ROOT)
        prev_line = (
            f"2. /Users/wu/Github/Idea/novel/人在高中我的恋爱系统对吗/{prev_rel}"
            "（上一章正文，只读**最后 5 段**，让你有『刚追完上一章』的记忆）"
        )
    else:
        prev_line = "2. （本章为全书第一章，无上一章）"

    out_path = SHENCHA_DIR / f"第{chapter_no:03d}章" / f"读者反馈_{persona}.md"
    out_rel_abs = f"/Users/wu/Github/Idea/novel/人在高中我的恋爱系统对吗/{out_path.relative_to(ROOT)}"
    # γ 人格额外加套路命中字段
    trope_field = ',"trope_hits":["..."]' if persona == "γ" else ""

    template = f"""你是《人在高中，我的恋爱系统对吗？》第 {chapter_no:03d} 章的【读者模拟 subagent · 人格 {persona}（{p['name']}）】。

你不是编辑，不是作者，不是审查员。**你是读者**。

## 人格设定
{p['setup']}

## 只读这两份（**不要**读 framework / 细纲 / 事实表 / 审查报告）
1. /Users/wu/Github/Idea/novel/人在高中我的恋爱系统对吗/{ch_rel}（本章全文）
{prev_line}

## 硬性约束
- 你是真人读者，不搞工程化审查。**不要**列"节奏 / AI 味 / 设定一致性"这类编辑术语。
- 报告≤ 30 行。一句话能说完就一句话。
- 引用原句用 ≤ 30 字。
- 绝对不要改写正文、不要给"改写建议"；读者不干编辑的活。

## 请回答
{p['ask']}

## 输出
落盘到 {out_rel_abs}，格式：

# 第 {chapter_no:03d} 章 读者反馈（人格 {persona} · {p['name']}）

## 最爽 / 最戳
- ……

## 最想跳过 / 最晕 / 最套路
- ……

（若为 γ 人格，追加）## 下一章预测（3 条）
- 1. ……
- 2. ……
- 3. ……

## 综合打分
- X / 10
- 一句话评语：……

## 人读摘要
≤ 3 行自然语言。

## RETURN_JSON
{{"role":"Reader-{persona}","chapter":"{chapter_no:03d}","batch":null,"artifact_paths":["{out_path.relative_to(ROOT)}"],"status":"ok","next_action":"done","summary":"...","metrics":{{"score":<int 1-10>,"drop_urge":"<none|low|mid|high>","most_hooked":"...","most_skippable":"..."{trope_field}}},"blocking_issues":[]}}

注意：
- RETURN_JSON **必须单行**，合法 JSON。若你的 metrics.score < 5，主 agent 会自动触发 framework/08 R4.6。
- drop_urge：α 必答；β/γ 可答或留"low"。
"""
    return template


def write_prompt_files(chapter_nos: list[int], personas: list[str]) -> tuple[list[Path], list[int]]:
    PROMPT_OUT_DIR.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    missing: list[int] = []
    for no in chapter_nos:
        if not find_chapter(no):
            missing.append(no)
            continue
        for persona in personas:
            prompt = build_prompt(no, persona)
            out = PROMPT_OUT_DIR / f"第{no:03d}章_{persona}.prompt.txt"
            out.write_text(prompt, encoding="utf-8")
            written.append(out)
    return written, missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="读者模拟 subagent prompt 批量生成")
    parser.add_argument("chapter", nargs="?", help="单章章号（例：007）")
    parser.add_argument("--batch", help="批次区间（例：010-014）")
    parser.add_argument("--personas", default="αβγ",
                        help="开启的人格组合，默认 αβγ。可选子集，例：α 或 αγ")
    args = parser.parse_args(argv)

    if not args.chapter and not args.batch:
        parser.error("必须提供 章号 或 --batch")

    if args.batch:
        start, end = (int(p) for p in args.batch.split("-"))
        chapter_nos = list(range(start, end + 1))
    else:
        chapter_nos = [int(args.chapter)]

    personas = [p for p in "αβγ" if p in args.personas]
    if not personas:
        parser.error(f"--personas 必须包含 α / β / γ 中至少一个；收到 {args.personas!r}")

    written, missing = write_prompt_files(chapter_nos, personas)
    print(f"✅ 生成 {len(written)} 份 prompt 到 {PROMPT_OUT_DIR.relative_to(ROOT)}/")
    for p in written:
        print(f"  - {p.relative_to(ROOT)}")
    if missing:
        print(f"\n⚠️  跳过（正文文件未找到）：{', '.join(f'第{n:03d}章' for n in missing)}")
    print("\n主 agent 使用方式：")
    print("  1. 每个 prompt 文件内容直接粘进 Task 工具，subagent_type 用 generalPurpose 即可。")
    print("  2. 理想是 15 份同时派出并发。")
    print("  3. 也可用 `xargs` 配合你的 CLI：")
    print(f"     ls {PROMPT_OUT_DIR.relative_to(ROOT)}/*.prompt.txt | xargs -I{{}} cat {{}}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
