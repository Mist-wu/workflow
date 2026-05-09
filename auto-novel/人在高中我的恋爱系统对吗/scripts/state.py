#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""state.py — 批次状态机读写封装（主 agent 自动化底座）

职责：
- 维护 审查/批次NNN_XXX-YYY/state.json，作为本批次唯一真相源。
- 提供 CLI：init / show / update / next / list-ready，便于 orchestrator 与主 agent 共用。
- 强 schema 校验：字段打错名立刻抛错，不让脏状态进入流水线。

state.json schema（v1）：
{
  "batch": "001",
  "chapter_range": [10, 14],
  "created_at": "2025-...",
  "updated_at": "2025-...",
  "stage": "writing",                # primer|ideator|gatekeeper|writing|scanning|reviewing|revising|integrating|merging|done
  "chapters": {
    "010": {
      "stage": "done",               # writing|scanning|reviewing|revising|ready|done
      "mode": "single",              # single|best-of-n
      "char_count": 4102,
      "hard_ban": 0,
      "reader_avg": 7.8,
      "severity": {"pacing":"轻","style":"无","ai":"无"},
      "A_count": 0, "C_heavy": 0,
      "dod_passed": true,
      "quality_score": 8,
      "retry_counts": {"writer":0,"reviser":0,"synth":0},
      "pending": []                  # 尚待跑的 subagent 名单（剩余任务列表）
    }
  },
  "batch_artifacts": {
    "context_pack":"...","brainstorm_final":"...","integration":"...","fact_preview":"..."
  },
  "escalations": [                    # 触发了 framework/08 升级规则的事件
    {"at":"...","rule":"R3.2","reason":"...","resolved":false}
  ],
  "blocked_by": []                    # 当前阻塞原因（空 = 可推进）
}
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SHENCHA_DIR = ROOT / "审查"


BATCH_STAGES = [
    "primer", "ideator", "gatekeeper",
    "writing", "scanning", "reviewing", "revising",
    "integrating", "merging", "done",
]
CHAPTER_STAGES = [
    "pending", "writing", "scanned", "reviewing", "revising",
    "ready", "done",
]
SEVERITY = {"无", "轻", "中", "重"}


# ---------------------------------------------------------------------------
# 路径解析
# ---------------------------------------------------------------------------

def batch_dir(batch: str) -> Path:
    """审查/批次NNN_XXX-YYY/ — 支持 NNN 号或目录名查找。"""
    # 精确三位数
    batch = f"{int(batch):03d}"
    matches = sorted(SHENCHA_DIR.glob(f"批次{batch}_*"))
    if matches:
        return matches[0]
    raise FileNotFoundError(
        f"找不到 审查/批次{batch}_XXX-YYY/。先创建目录或用 init 命令。"
    )


def batch_dir_or_create(batch: str, lo: int, hi: int) -> Path:
    batch = f"{int(batch):03d}"
    existing = sorted(SHENCHA_DIR.glob(f"批次{batch}_*"))
    if existing:
        return existing[0]
    p = SHENCHA_DIR / f"批次{batch}_{lo:03d}-{hi:03d}"
    p.mkdir(parents=True, exist_ok=True)
    return p


def state_path(batch: str) -> Path:
    return batch_dir(batch) / "state.json"


# ---------------------------------------------------------------------------
# 读写核心
# ---------------------------------------------------------------------------

def load(batch: str) -> dict[str, Any]:
    p = state_path(batch)
    if not p.exists():
        raise FileNotFoundError(f"state.json 尚未初始化：{p}")
    with p.open(encoding="utf-8") as f:
        return json.load(f)


def save(batch: str, state: dict[str, Any]) -> Path:
    state["updated_at"] = _dt.datetime.now().isoformat(timespec="seconds")
    validate(state)
    p = state_path(batch)
    p.parent.mkdir(parents=True, exist_ok=True)
    # 原子写
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(p)
    return p


def init(batch: str, lo: int, hi: int, *, best_of_n: list[int] | None = None,
         debt: bool = False) -> dict[str, Any]:
    bdir = batch_dir_or_create(batch, lo, hi)
    now = _dt.datetime.now().isoformat(timespec="seconds")
    chapters: dict[str, dict[str, Any]] = {}
    for ch in range(lo, hi + 1):
        chapters[f"{ch:03d}"] = {
            "stage": "pending",
            "mode": "best-of-n" if best_of_n and ch in best_of_n else "single",
            "char_count": None,
            "hard_ban": None,
            "reader_avg": None,
            "severity": {"pacing": None, "style": None, "ai": None},
            "A_count": None,
            "C_heavy": None,
            "dod_passed": False,
            "quality_score": None,
            "retry_counts": {"writer": 0, "reviser": 0, "synth": 0},
            "pending": [],
        }
    state = {
        "batch": f"{int(batch):03d}",
        "chapter_range": [lo, hi],
        "debt": debt,
        "created_at": now,
        "updated_at": now,
        "stage": "primer" if not debt else "scanning",
        "chapters": chapters,
        "batch_artifacts": {},
        "escalations": [],
        "blocked_by": [],
    }
    save(batch, state)
    print(f"✅ 已初始化 {bdir.relative_to(ROOT)}/state.json")
    return state


# ---------------------------------------------------------------------------
# schema 校验
# ---------------------------------------------------------------------------

def validate(state: dict[str, Any]) -> None:
    if state["stage"] not in BATCH_STAGES:
        raise ValueError(f"非法 batch stage: {state['stage']}")
    for ch, meta in state["chapters"].items():
        if not re.fullmatch(r"\d{3}", ch):
            raise ValueError(f"章号非法：{ch}")
        if meta["stage"] not in CHAPTER_STAGES:
            raise ValueError(f"第{ch}章 stage 非法：{meta['stage']}")
        for key in ("pacing", "style", "ai"):
            v = meta["severity"].get(key)
            if v is not None and v not in SEVERITY:
                raise ValueError(f"第{ch}章 severity.{key} 非法：{v}")


# ---------------------------------------------------------------------------
# 状态更新的高阶工具
# ---------------------------------------------------------------------------

def set_chapter(batch: str, chapter: str, **kwargs: Any) -> None:
    st = load(batch)
    ch = f"{int(chapter):03d}"
    if ch not in st["chapters"]:
        raise KeyError(f"批次 {batch} 无第 {ch} 章")
    meta = st["chapters"][ch]
    for k, v in kwargs.items():
        if k == "severity" and isinstance(v, dict):
            meta["severity"].update(v)
        elif k == "retry_incr":
            meta["retry_counts"][v] = meta["retry_counts"].get(v, 0) + 1
        elif k in meta:
            meta[k] = v
        else:
            raise KeyError(f"未知字段 {k}")
    save(batch, st)


def set_batch(batch: str, **kwargs: Any) -> None:
    st = load(batch)
    for k, v in kwargs.items():
        if k in ("batch_artifacts", "escalations", "blocked_by"):
            if isinstance(v, dict) and isinstance(st.get(k), dict):
                st[k].update(v)
            elif isinstance(v, list) and isinstance(st.get(k), list):
                st[k].extend(v) if kwargs.get("append") else None
                if not kwargs.get("append"):
                    st[k] = v
            else:
                st[k] = v
        elif k == "append":
            continue
        elif k in st:
            st[k] = v
        else:
            raise KeyError(f"未知字段 {k}")
    save(batch, st)


def add_escalation(batch: str, rule: str, reason: str) -> None:
    st = load(batch)
    st["escalations"].append({
        "at": _dt.datetime.now().isoformat(timespec="seconds"),
        "rule": rule, "reason": reason, "resolved": False,
    })
    save(batch, st)


# ---------------------------------------------------------------------------
# 下一步推断（简化版，pipeline.py 会更完整）
# ---------------------------------------------------------------------------

def ready_chapters(batch: str, target_stage: str) -> list[str]:
    """返回处于 target_stage 的章号列表（用于'现在可以派哪些 subagent'的查询）。"""
    st = load(batch)
    return [ch for ch, m in sorted(st["chapters"].items()) if m["stage"] == target_stage]


def infer_next(batch: str) -> dict[str, Any]:
    st = load(batch)
    # 批次级串行段
    if st["stage"] == "primer":
        return {"action": "start_primer", "batch": batch}
    if st["stage"] == "ideator":
        return {"action": "start_ideator", "batch": batch}
    if st["stage"] == "gatekeeper":
        return {"action": "start_gatekeeper", "batch": batch}
    # 章级并发段
    if st["stage"] == "writing":
        to_write = ready_chapters(batch, "pending")
        return {"action": "start_writing", "batch": batch, "chapters": to_write}
    if st["stage"] == "reviewing":
        to_review = ready_chapters(batch, "scanned")
        return {"action": "start_review", "batch": batch, "chapters": to_review}
    if st["stage"] == "revising":
        to_revise = ready_chapters(batch, "reviewing")
        return {"action": "start_revise", "batch": batch, "chapters": to_revise}
    if st["stage"] == "integrating":
        return {"action": "start_integrator", "batch": batch}
    if st["stage"] == "merging":
        return {"action": "start_merger", "batch": batch}
    if st["stage"] == "done":
        return {"action": "done", "batch": batch}
    return {"action": "unknown", "batch": batch, "stage": st["stage"]}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_init(args: argparse.Namespace) -> int:
    bn = args.batch
    lo, hi = (int(x) for x in args.range.split("-"))
    bon = [int(x) for x in args.best_of_n.split(",")] if args.best_of_n else None
    init(bn, lo, hi, best_of_n=bon, debt=args.debt)
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    st = load(args.batch)
    if args.json:
        print(json.dumps(st, ensure_ascii=False, indent=2))
        return 0
    # 人读摘要
    print(f"== 批次 {st['batch']}（第 {st['chapter_range'][0]}–{st['chapter_range'][1]} 章）==")
    print(f"stage = {st['stage']}    更新于 {st['updated_at']}")
    print(f"blocked_by = {st['blocked_by'] or '(空)'}")
    print(f"escalations = {len(st['escalations'])} 条（未解决 "
          f"{sum(1 for e in st['escalations'] if not e['resolved'])}）")
    print(f"\n章号  stage      mode        字数  硬禁用  读者均  A段  DoD  质量")
    for ch, m in sorted(st["chapters"].items()):
        print(f"{ch}   {m['stage']:<10} {m['mode']:<11} "
              f"{str(m['char_count'] or '-'):>5}  "
              f"{str(m['hard_ban'] if m['hard_ban'] is not None else '-'):>5}  "
              f"{str(m['reader_avg'] or '-'):>5}  "
              f"{str(m['A_count'] if m['A_count'] is not None else '-'):>3}   "
              f"{'✅' if m['dod_passed'] else '⏳'}  "
              f"{m['quality_score'] or '-'}")
    print(f"\n批次产物：")
    for k, v in (st["batch_artifacts"] or {}).items():
        print(f"  {k}: {v}")
    return 0


def _cmd_next(args: argparse.Namespace) -> int:
    nxt = infer_next(args.batch)
    print(json.dumps(nxt, ensure_ascii=False))
    return 0


def _cmd_set_chapter(args: argparse.Namespace) -> int:
    kv: dict[str, Any] = {}
    for item in args.kv:
        k, _, v = item.partition("=")
        # 类型自动推断
        if v.lower() in ("true", "false"):
            kv[k] = v.lower() == "true"
        elif v.replace(".", "", 1).replace("-", "", 1).isdigit():
            kv[k] = float(v) if "." in v else int(v)
        elif v.startswith("{") or v.startswith("["):
            kv[k] = json.loads(v)
        else:
            kv[k] = v
    set_chapter(args.batch, args.chapter, **kv)
    print(f"✅ 第{int(args.chapter):03d}章状态已更新。")
    return 0


def _cmd_set_batch(args: argparse.Namespace) -> int:
    kv: dict[str, Any] = {}
    for item in args.kv:
        k, _, v = item.partition("=")
        if v.startswith("{") or v.startswith("["):
            kv[k] = json.loads(v)
        else:
            kv[k] = v
    set_batch(args.batch, **kv)
    print(f"✅ 批次 {args.batch} 状态已更新。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="批次 state.json 管理器")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="初始化批次 state.json")
    p_init.add_argument("batch")
    p_init.add_argument("--range", required=True, help="XXX-YYY（含两端）")
    p_init.add_argument("--best-of-n", help="逗号分隔的章号，例 012,014")
    p_init.add_argument("--debt", action="store_true", help="债务清理批次（批次 000）")
    p_init.set_defaults(func=_cmd_init)

    p_show = sub.add_parser("show", help="查看批次状态")
    p_show.add_argument("batch")
    p_show.add_argument("--json", action="store_true")
    p_show.set_defaults(func=_cmd_show)

    p_next = sub.add_parser("next", help="查询下一步应派谁")
    p_next.add_argument("batch")
    p_next.set_defaults(func=_cmd_next)

    p_sc = sub.add_parser("set-chapter", help="更新某章字段")
    p_sc.add_argument("batch")
    p_sc.add_argument("chapter")
    p_sc.add_argument("kv", nargs="+", help="key=value ...")
    p_sc.set_defaults(func=_cmd_set_chapter)

    p_sb = sub.add_parser("set-batch", help="更新批次级字段")
    p_sb.add_argument("batch")
    p_sb.add_argument("kv", nargs="+", help="key=value ...")
    p_sb.set_defaults(func=_cmd_set_batch)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
