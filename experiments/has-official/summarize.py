#!/usr/bin/env python3
"""汇总重复 scan：每个应识别值的命中率、误伤和幻觉。"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent


def values(run: dict) -> dict[str, list[str]]:
    seen: dict[str, list[str]] = {}
    for cat, items in run.items():
        for item in items:
            seen.setdefault(str(item), []).append(cat)
    return seen


def report(case: dict, runs: list[dict]) -> dict:
    n = len(runs)
    print(f"\n== {case['id']} {case['title']}（{n} 次）")
    expected = [(c, v) for c, vs in case["expected"].items() for v in vs]
    exact = wrong = total = 0
    for cat, value in expected:
        total += n
        hits = sum(1 for r in runs if value in values(r))
        right = sum(1 for r in runs if cat in values(r).get(value, []))
        exact += right
        wrong += hits - right
        if right < n:
            near = Counter(f"{c}:{v}" for r in runs for v, cs in values(r).items() for c in cs if v != value and (v in value or value in v or v.replace("\n", "") == value.replace("\n", "")))
            other = Counter(c for r in runs for c in values(r).get(value, []) if c != cat)
            print(f"  {cat} {value!r}: 正确 {right}/{n} 错类{dict(other)} 近似{dict(near)}")
    known = {v for _, v in expected} | set(case.get("optional", []))
    extra = Counter(f"{c}:{v}" for r in runs for v, cs in values(r).items() for c in cs
                    if v not in known and not any(v.replace("\n", "") == k.replace("\n", "") or v in k or k in v for k in known))
    if extra:
        print(f"  多余: {dict(extra)}")
    ghost = Counter(v for r in runs for v in values(r) if v not in case["text"])
    if ghost:
        print(f"  不在原文: {dict(ghost)}")
    distinct = len({json.dumps(r, ensure_ascii=False, sort_keys=True) for r in runs})
    print(f"  正确 {exact}/{total}，错类 {wrong}，{distinct} 种不同结果")
    return {"exact": exact, "total": total, "wrong": wrong, "extra": sum(extra.values())}


def main() -> None:
    cases = {c["id"]: c for f in ("cases.json", "cases-hard.json")
             for c in json.loads((HERE / f).read_text(encoding="utf-8"))["cases"]}
    mode = sys.argv[1]
    total = Counter()
    if mode == "official":
        for row in json.loads((HERE / "results-hard/repeat.json").read_text(encoding="utf-8")):
            total.update(report(cases[row["id"]], row["runs"]))
    else:
        for row in json.loads((HERE / "results-direct.json").read_text(encoding="utf-8")):
            total.update(report(cases[row["id"]], row[mode]))
    print(f"\n合计 {dict(total)}，正确率 {total['exact'] / total['total']:.1%}")


if __name__ == "__main__":
    main()
