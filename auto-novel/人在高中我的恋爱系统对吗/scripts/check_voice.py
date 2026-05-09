#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_voice.py — 风格漂移探针

长期连载最大隐患：50 章后主角说话"变味"、女角口癖消失。
本脚本用纯统计方式建立 baseline（前 N 章）后，对新章做对比报警。

指标：
1. 句长分布（中文汉字数/句，排除系统【】与心声（）)
2. 对话占比 / 心理独白占比 / 描写占比
3. 系统出场密度（【】段/千字）
4. 每个女角的"口癖命中率"（她出现的段落里，口癖出现的比例）
5. "对吗？"密度 / "（）"段占比

用法：
    python3 scripts/check_voice.py --baseline 1-10 --target 11-15
    python3 scripts/check_voice.py --baseline 1-10 --target 25
    python3 scripts/check_voice.py --baseline 1-10 --target 11-15 --json

告警阈值（默认 20% 偏离）：
- 句长均值偏移 > 20% → 🟡
- 对话占比偏移 > 15 个百分点 → 🟡
- 女角 speech 命中率下降 > 30% → 🔴（触发 framework/08 R8.3）

exit 0 = 全绿；1 = 有黄色；2 = 有红色
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ZHENGWEN_DIR = ROOT / "正文"
SHENCHA_DIR = ROOT / "审查"
FACTS_FILE = SHENCHA_DIR / "facts.json"


SYSTEM_RE = re.compile(r"【[^】\n]*】")
MONO_RE = re.compile(r"^（[^）\n]*）$", re.M)
# 对话引号 — 支持直角"" 和弯曲 "" 两种
DIAL_CONTENT_RE = re.compile(r"[\u201c\u201d\"]([^\u201c\u201d\"\n]+)[\u201c\u201d\"]")


def _count_chinese(s: str) -> int:
    return sum(1 for c in s if '\u4e00' <= c <= '\u9fff')


@dataclass
class VoiceStat:
    chapter: str
    total_chars: int = 0
    sentence_lengths: list[int] = field(default_factory=list)
    dialogue_chars: int = 0
    mono_chars: int = 0
    system_count: int = 0
    duiba_count: int = 0
    character_speech_hits: dict[str, dict[str, int]] = field(default_factory=dict)

    def summarize(self) -> dict:
        lens = self.sentence_lengths
        avg = sum(lens) / len(lens) if lens else 0
        short_ratio = sum(1 for l in lens if l <= 15) / len(lens) if lens else 0
        return {
            "chapter": self.chapter,
            "total_chars": self.total_chars,
            "avg_sentence_len": round(avg, 2),
            "short_sentence_ratio": round(short_ratio, 3),
            "dialogue_ratio": round(self.dialogue_chars / max(self.total_chars, 1), 3),
            "mono_ratio": round(self.mono_chars / max(self.total_chars, 1), 3),
            "system_density_per_1k": round(self.system_count * 1000 / max(self.total_chars, 1), 2),
            "duiba_count": self.duiba_count,
            "character_speech": self.character_speech_hits,
        }


def analyze_chapter(path: Path, catchphrases: dict[str, list[str]]) -> VoiceStat:
    text = path.read_text(encoding="utf-8")
    chap_m = re.search(r"第(\d{3})", path.name)
    chap = chap_m.group(1) if chap_m else "???"
    stat = VoiceStat(chapter=chap)
    stat.total_chars = _count_chinese(text)

    # 1. 系统 【】 与心声 （） 密度
    stat.system_count = len(SYSTEM_RE.findall(text))
    stat.duiba_count = text.count("对吗？")
    stat.mono_chars = sum(_count_chinese(m) for m in MONO_RE.findall(text))

    # 2. 对话：捕捉成对引号内容（中文弯曲 / 直角）
    dial_quotes = DIAL_CONTENT_RE.findall(text)
    stat.dialogue_chars = sum(_count_chinese(d) for d in dial_quotes)

    # 3. 句长：去掉【】与（）后分句（。！？）
    cleaned = SYSTEM_RE.sub("", text)
    cleaned = re.sub(r"（[^）\n]*）", "", cleaned)
    sentences = re.split(r"[。！？\.\!\?][」』\"]?", cleaned)
    stat.sentence_lengths = [_count_chinese(s) for s in sentences if _count_chinese(s) > 0]

    # 4. 角色口癖命中率
    # 在段落级（空行分段）上：段落里出现角色名，则计为该角色"出场段"；
    # 再在这些段中计口癖命中段数。
    paragraphs = re.split(r"\n\s*\n", text)
    for name, phrases in catchphrases.items():
        appears = sum(1 for p in paragraphs if name in p)
        hits = sum(1 for p in paragraphs if name in p and
                   any(ph in p for ph in phrases))
        if appears:
            stat.character_speech_hits[name] = {
                "appears_paragraphs": appears,
                "catchphrase_hits": hits,
                "hit_rate": round(hits / appears, 3),
            }
    return stat


def load_catchphrases() -> dict[str, list[str]]:
    if not FACTS_FILE.exists():
        return {}
    data = json.loads(FACTS_FILE.read_text(encoding="utf-8"))
    out: dict[str, list[str]] = {}
    for name, ch in data.get("canon", {}).get("characters", {}).items():
        phrases: list[str] = []
        for key in ("catchphrase", "catchphrases"):
            v = ch.get(key)
            if isinstance(v, str):
                phrases.append(v)
            elif isinstance(v, list):
                phrases.extend(v)
        if phrases:
            out[name] = phrases
    return out


def aggregate(stats: list[VoiceStat]) -> dict:
    if not stats:
        return {}
    summaries = [s.summarize() for s in stats]
    keys_num = ["avg_sentence_len", "short_sentence_ratio",
                "dialogue_ratio", "mono_ratio",
                "system_density_per_1k", "duiba_count"]
    agg = {k: round(sum(s[k] for s in summaries) / len(summaries), 3)
           for k in keys_num}
    # 角色口癖命中率：取均值
    char_stats: dict[str, list[float]] = {}
    for s in summaries:
        for name, d in s["character_speech"].items():
            if d.get("appears_paragraphs", 0) >= 2:  # 至少出场 2 段才算
                char_stats.setdefault(name, []).append(d["hit_rate"])
    agg["character_hit_rate_avg"] = {
        n: round(sum(v) / len(v), 3) for n, v in char_stats.items()
    }
    agg["chapters"] = [s["chapter"] for s in summaries]
    return agg


def compare(baseline: dict, target: dict) -> list[dict]:
    issues: list[dict] = []
    if not baseline or not target:
        return issues

    # 句长均值
    base_len = baseline.get("avg_sentence_len", 0) or 1
    tgt_len = target.get("avg_sentence_len", 0)
    if abs(tgt_len - base_len) / base_len > 0.20:
        issues.append({"severity": "warn", "metric": "avg_sentence_len",
                       "baseline": base_len, "target": tgt_len,
                       "msg": "平均句长偏离基线 > 20%"})

    # 对话占比
    d = target.get("dialogue_ratio", 0) - baseline.get("dialogue_ratio", 0)
    if abs(d) > 0.15:
        issues.append({"severity": "warn", "metric": "dialogue_ratio",
                       "baseline": baseline["dialogue_ratio"],
                       "target": target["dialogue_ratio"],
                       "msg": f"对话占比偏移 {d:+.1%}"})

    # 系统对吗密度
    base_db = baseline.get("duiba_count", 0) / max(len(baseline.get("chapters", [])), 1)
    tgt_db = target.get("duiba_count", 0) / max(len(target.get("chapters", [])), 1)
    if base_db > 0 and tgt_db == 0:
        issues.append({"severity": "warn", "metric": "duiba_count",
                       "baseline": base_db, "target": tgt_db,
                       "msg": "'对吗？'在目标段完全消失，系统出场模式漂移"})

    # 角色口癖：基线命中率 < 5% 的角色信号太弱，跳过
    base_char = baseline.get("character_hit_rate_avg", {})
    tgt_char = target.get("character_hit_rate_avg", {})
    for name, base_rate in base_char.items():
        if base_rate < 0.05:
            continue
        tgt_rate = tgt_char.get(name)
        if tgt_rate is None:
            continue
        drop = (base_rate - tgt_rate) / base_rate
        if drop > 0.30:
            sev = "canon" if drop > 0.60 else "warn"
            issues.append({"severity": sev, "metric": f"character_hit/{name}",
                           "baseline": base_rate, "target": tgt_rate,
                           "msg": f"{name} 口癖命中率下降 {drop:.1%}"})
    return issues


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True, help="基线章节区间 1-10 或单章 003")
    parser.add_argument("--target", required=True, help="目标章节区间或单章")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    def _range(spec: str) -> list[str]:
        if "-" in spec:
            lo, hi = (int(x) for x in spec.split("-"))
            return [f"{i:03d}" for i in range(lo, hi + 1)]
        return [f"{int(spec):03d}"]

    catchphrases = load_catchphrases()

    def _collect(chaps: list[str]) -> list[VoiceStat]:
        stats: list[VoiceStat] = []
        for ch in chaps:
            paths = list(ZHENGWEN_DIR.glob(f"第{int(ch):03d}章_*.txt"))
            if not paths:
                continue
            stats.append(analyze_chapter(paths[0], catchphrases))
        return stats

    base_stats = _collect(_range(args.baseline))
    tgt_stats = _collect(_range(args.target))

    base_agg = aggregate(base_stats)
    tgt_agg = aggregate(tgt_stats)
    issues = compare(base_agg, tgt_agg)

    if args.json:
        print(json.dumps({
            "baseline": base_agg, "target": tgt_agg, "issues": issues,
        }, ensure_ascii=False, indent=2))
    else:
        print(f"== 基线（{args.baseline}）vs 目标（{args.target}）==")
        print(f"\n基线聚合：")
        for k, v in base_agg.items():
            print(f"  {k}: {v}")
        print(f"\n目标聚合：")
        for k, v in tgt_agg.items():
            print(f"  {k}: {v}")
        print(f"\n差异：")
        if not issues:
            print("  ✅ 未检测到显著漂移")
        else:
            for i in issues:
                icon = "🔴" if i["severity"] == "canon" else "🟡"
                print(f"  {icon} {i['metric']}: {i['msg']} "
                      f"(基线 {i['baseline']} → 目标 {i['target']})")

    worst = max((i["severity"] for i in issues), default="ok",
                key=lambda s: {"ok": 0, "warn": 1, "canon": 2}.get(s, 0))
    return {"ok": 0, "warn": 1, "canon": 2}.get(worst, 0)


if __name__ == "__main__":
    raise SystemExit(main())
