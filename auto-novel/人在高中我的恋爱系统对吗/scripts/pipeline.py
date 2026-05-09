#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pipeline.py — 批次流水线调度器（章级粒度 DAG）

本脚本不直接派 subagent（那是主 agent 的活），而是：
1. 读 state.json，告诉主 agent「现在应该派哪些 subagent，对哪些章」。
2. 自动跑确定性脚本（scan_ai_templates / check_dod / check_facts），更新 state。
3. 把触发 framework/08_自动升级规则.md 的情况汇报出来。

使用流程（主 agent 每一轮的 mantra）：

    python3 scripts/pipeline.py status 001       # 看全局
    python3 scripts/pipeline.py next 001         # JSON 返回下一批动作
    python3 scripts/pipeline.py tick 001         # 跑所有可自动化的步骤（扫描/DoD），更新 state

    # tick 完 → pipeline 告诉你"该派 X 个 subagent，对 Y 章" → 主 agent 派 Task

命令：
    status <batch>              — 人读状态总览
    next <batch>                — JSON 输出下一步可派的 subagent 清单
    tick <batch>                — 跑所有可自动化步骤；输出后续动作
    advance <batch>             — 当所有章 dod_passed=true 时，把 stage 推到 integrating
    init <batch> --range XX-YY  — 等价于 state.py init
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"

sys.path.insert(0, str(SCRIPTS))
from state import load, save, ready_chapters, infer_next  # type: ignore
from state import init as state_init  # type: ignore


# ---------------------------------------------------------------------------
# 核心：章级 DAG 决策
# ---------------------------------------------------------------------------

def _chapter_next_action(ch: str, meta: dict) -> dict | None:
    """返回某章下一步该干什么。None 表示该章已完成。"""
    stage = meta["stage"]
    if stage == "pending":
        return {"action": "start_writing", "chapter": ch, "mode": meta["mode"]}
    if stage == "writing":
        # 写作 subagent 交稿时会 set stage=scanned? 不，要先扫描
        return {"action": "wait_writer_return", "chapter": ch}
    if stage == "scanned":
        return {"action": "start_review", "chapter": ch,
                "subagents": ["hard_check", "polish_pacing",
                              "polish_style", "polish_ai",
                              "reader_α", "reader_β", "reader_γ"]}
    if stage == "reviewing":
        return {"action": "wait_reviewers", "chapter": ch}
    if stage == "revising":
        return {"action": "wait_reviser", "chapter": ch}
    if stage == "ready":
        if meta["dod_passed"]:
            return None  # done
        return {"action": "run_dod_gate", "chapter": ch}
    if stage == "done":
        return None
    return {"action": "unknown", "chapter": ch, "stage": stage}


def compute_next_actions(batch: str) -> dict:
    st = load(batch)
    actions: list[dict] = []

    # 批次级串行段优先
    if st["stage"] in ("primer", "ideator", "gatekeeper",
                       "integrating", "merging"):
        return {
            "batch": batch, "stage": st["stage"],
            "blocked_by": st.get("blocked_by", []),
            "actions": [infer_next(batch)],
        }

    if st["stage"] == "done":
        return {"batch": batch, "stage": "done", "actions": []}

    # writing / scanning / reviewing / revising 都走章级并发
    for ch, meta in sorted(st["chapters"].items()):
        act = _chapter_next_action(ch, meta)
        if act is not None:
            actions.append(act)

    # 检查是否可以推进到 integrating
    all_done = all(m["dod_passed"] for m in st["chapters"].values())
    if all_done and st["stage"] != "integrating":
        actions.append({"action": "advance_to_integrating", "batch": batch})

    return {
        "batch": batch, "stage": st["stage"],
        "blocked_by": st.get("blocked_by", []),
        "escalations_unresolved": [
            e for e in st.get("escalations", []) if not e.get("resolved")
        ],
        "actions": actions,
    }


# ---------------------------------------------------------------------------
# tick：跑所有可自动化步骤
# ---------------------------------------------------------------------------

def run_scan(ch: str) -> tuple[int, str]:
    cp = subprocess.run(
        ["python3", str(SCRIPTS / "scan_ai_templates.py"), ch],
        capture_output=True, text=True,
    )
    return cp.returncode, (cp.stdout + cp.stderr).strip()


def run_dod(ch: str) -> tuple[int, dict]:
    cp = subprocess.run(
        ["python3", str(SCRIPTS / "check_dod.py"), ch, "--json", "--no-state"],
        capture_output=True, text=True,
    )
    data = []
    try:
        data = json.loads(cp.stdout)
    except Exception:
        pass
    return cp.returncode, data[0] if data else {}


def run_facts_check() -> tuple[int, list[dict]]:
    cp = subprocess.run(
        ["python3", str(SCRIPTS / "check_facts.py"), "--json"],
        capture_output=True, text=True,
    )
    try:
        return cp.returncode, json.loads(cp.stdout).get("violations", [])
    except Exception:
        return cp.returncode, []


def tick(batch: str) -> dict:
    """把所有可自动化的步骤跑一遍，更新 state.json。"""
    st = load(batch)
    summary: dict = {"batch": batch, "actions_taken": [], "followups": []}

    for ch, meta in list(st["chapters"].items()):
        chapter_path = ROOT / "正文"
        written = list(chapter_path.glob(f"第{int(ch):03d}章_*.txt"))
        if not written:
            continue

        # 1. 如果章节 stage == writing 且正文已落盘，跑 scan
        if meta["stage"] == "writing":
            code, out = run_scan(ch)
            ai_path = ROOT / "审查" / f"第{ch}章" / "AI味命中清单.txt"
            hard_ban = 0
            if ai_path.exists():
                text = ai_path.read_text(encoding="utf-8")
                import re as _re
                m = _re.search(r"硬禁用：(\d+)\s*条", text)
                if m:
                    hard_ban = int(m.group(1))
            meta["stage"] = "scanned"
            meta["hard_ban"] = hard_ban
            summary["actions_taken"].append(
                f"扫描第{ch}章 → 硬禁用 {hard_ban} 条，stage→scanned"
            )

        # 2. 如果章节 stage == revising（修订 subagent 刚交稿），跑 DoD gate
        elif meta["stage"] == "revising":
            # 重扫一次
            run_scan(ch)
            code, result = run_dod(ch)
            passed = bool(result.get("passed"))
            meta["dod_passed"] = passed
            meta["A_count"] = next(
                (c["detail"].count("A段") for c in result.get("checks", [])
                 if "A 段" in c.get("name", "") and not c["passed"]),
                0,
            )
            if passed:
                meta["stage"] = "ready"
                summary["actions_taken"].append(f"第{ch}章 DoD ✅ → ready")
            else:
                rc = meta["retry_counts"]
                rc["reviser"] = rc.get("reviser", 0) + 1
                if rc["reviser"] >= 2:
                    summary["followups"].append({
                        "type": "escalate",
                        "rule": "R5.1",
                        "chapter": ch,
                        "reason": f"修订后 DoD 连续 {rc['reviser']} 次未过，升级用户",
                    })
                else:
                    summary["followups"].append({
                        "type": "auto_retry_reviser",
                        "chapter": ch,
                        "reason": "DoD 未过，自动再派一次修订",
                    })
                meta["stage"] = "reviewing"  # 回到审查后再次修订
                summary["actions_taken"].append(
                    f"第{ch}章 DoD ❌ ({result.get('fail_count','?')}/7) → 自动回 reviewing"
                )

    save(batch, st)

    # 3. 全书 canon 校验
    code, violations = run_facts_check()
    canon_conflicts = [v for v in violations if v.get("severity") == "canon"]
    if canon_conflicts:
        summary["followups"].append({
            "type": "escalate", "rule": "R7.1",
            "reason": f"facts.json 有 {len(canon_conflicts)} 条 canon 冲突",
            "violations": canon_conflicts,
        })

    return summary


# ---------------------------------------------------------------------------
# advance / init
# ---------------------------------------------------------------------------

def advance(batch: str) -> dict:
    st = load(batch)
    if not all(m["dod_passed"] for m in st["chapters"].values()):
        return {"status": "fail",
                "reason": "尚有章节未过 DoD，无法推进到 integrating"}
    st["stage"] = "integrating"
    save(batch, st)
    return {"status": "ok", "stage": "integrating",
            "msg": "所有章 dod_passed=true，已推进到 integrating，请派 Integrator。"}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_status(args):
    subprocess.run(["python3", str(SCRIPTS / "state.py"), "show", args.batch])
    return 0


def _cmd_next(args):
    print(json.dumps(compute_next_actions(args.batch), ensure_ascii=False, indent=2))
    return 0


def _cmd_tick(args):
    out = tick(args.batch)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"== tick 批次 {args.batch} ==")
        for a in out["actions_taken"]:
            print(f"  ✓ {a}")
        if out["followups"]:
            print("\n需主 agent 跟进：")
            for f in out["followups"]:
                print(f"  → {f}")
    # 返回 0 正常；有未解决升级返回 1
    esc = [f for f in out["followups"] if f["type"] == "escalate"]
    return 1 if esc else 0


def _cmd_advance(args):
    r = advance(args.batch)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if r.get("status") == "ok" else 1


def _cmd_init(args):
    lo, hi = (int(x) for x in args.range.split("-"))
    bon = [int(x) for x in args.best_of_n.split(",")] if args.best_of_n else None
    state_init(args.batch, lo, hi, best_of_n=bon, debt=args.debt)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="批次流水线调度器（章级 DAG）")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, fn in [
        ("status", _cmd_status), ("next", _cmd_next),
        ("tick", _cmd_tick), ("advance", _cmd_advance),
    ]:
        p = sub.add_parser(name)
        p.add_argument("batch")
        if name == "tick":
            p.add_argument("--json", action="store_true")
        p.set_defaults(func=fn)
    p_init = sub.add_parser("init")
    p_init.add_argument("batch")
    p_init.add_argument("--range", required=True)
    p_init.add_argument("--best-of-n")
    p_init.add_argument("--debt", action="store_true")
    p_init.set_defaults(func=_cmd_init)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
