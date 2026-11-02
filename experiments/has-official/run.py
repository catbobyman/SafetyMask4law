#!/usr/bin/env python3
"""用官方 has.sh 跑 scan → hide → restore，并按 cases.json 计分。"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
GIT_BASH = r"D:\Git\bin\bash.exe"
HAS_SH = ROOT / "vendor/skills/has-anonymizer/scripts/has.sh"
LLAMA_DIR = ROOT / "vendor/llama.cpp/b12032"
MODEL = ROOT / "models/has_text_model.gguf"


def rule_categories() -> list[str]:
    block = re.search(r"```json\s*(.*?)```", (ROOT / "规则.md").read_text(encoding="utf-8"), re.S)
    return json.loads(block.group(1))["categories"]


def has(*args: str) -> tuple[dict, float]:
    env = dict(
        os.environ,
        PATH=f"{LLAMA_DIR}{os.pathsep}{os.environ['PATH']}",
        HAS_TEXT_MODEL_PATH=str(MODEL),
        PYTHONUTF8="1",
        PYTHONIOENCODING="utf-8",
    )
    started = time.time()
    proc = subprocess.run(
        [GIT_BASH, HAS_SH.as_posix(), "text", "--timing", *args],
        env=env, capture_output=True, encoding="utf-8", errors="replace",
    )
    elapsed = time.time() - started
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"非 JSON 输出 exit={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}") from exc
    if proc.returncode != 0 or "error" in payload:
        raise RuntimeError(f"官方 CLI 失败 exit={proc.returncode}: {proc.stdout}\n{proc.stderr}")
    return payload, elapsed


def score_scan(case: dict, entities: dict[str, list[str]]) -> dict:
    found = {(cat, value) for cat, values in entities.items() for value in values}
    values_found = {value for _, value in found}
    expected = {(cat, value) for cat, values in case["expected"].items() for value in values}
    hit = sorted(pair for pair in expected if pair in found)
    wrong_cat = sorted(
        [cat, value, sorted(c for c, v in found if v == value)]
        for cat, value in expected
        if (cat, value) not in found and value in values_found
    )
    partial = sorted(
        [cat, value, sorted(v for v in values_found if v != value and (v in value or value in v))]
        for cat, value in expected
        if value not in values_found and any(v in value or value in v for v in values_found)
    )
    missed = sorted(
        [cat, value] for cat, value in expected
        if value not in values_found and not any(v in value or value in v for v in values_found)
    )
    expected_values = {value for _, value in expected} | set(case.get("optional", []))
    extra = sorted([cat, value] for cat, value in found if value not in expected_values)
    protected = sorted(
        [cat, value] for cat, value in found
        if any(value == k or k in value for k in case["keep"]) and value not in expected_values
    )
    return {
        "expected": len(expected),
        "hit": len(hit),
        "wrong_cat": wrong_cat,
        "partial": partial,
        "missed": missed,
        "extra": extra,
        "protected_hit": protected,
        "not_in_text": sorted(value for value in values_found if value not in case["text"]),
    }


def score_hide(case: dict, anonymized: str, restored: str) -> dict:
    # 先去掉标签，避免标签文字本身干扰判断。
    bare = re.sub(r"<[^<>]+>", "\0", anonymized)
    expected_values = {value for values in case["expected"].values() for value in values}
    return {
        "leaked": sorted(value for value in expected_values if value in bare),
        "keep_lost": sorted(value for value in case["keep"] if value in case["text"] and value not in bare),
        "tags": len(re.findall(r"<[^<>]+>", anonymized)),
        "roundtrip_equal": restored == case["text"],
    }


def run_case(case: dict, types: list[str], out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    source = out / "original.txt"
    source.write_text(case["text"], encoding="utf-8", newline="\n")
    type_args = [arg for t in types for arg in ("--type", t)]

    scan, scan_s = has("scan", *type_args, "--file", str(source))
    result = {"id": case["id"], "title": case["title"], "chars": len(case["text"]),
              "entities": scan["entities"], "scan_s": round(scan_s, 1),
              "scan_ms": scan.get("elapsed_ms"), "scan": score_scan(case, scan["entities"])}

    anon_path, map_path, back_path = out / "hidden.txt", out / "mapping.json", out / "restored.txt"
    try:
        hide, hide_s = has("hide", *type_args, "--file", str(source), "--output", str(anon_path), "--mapping-output", str(map_path))
        restore, restore_s = has("restore", "--mapping", str(map_path), "--file", str(anon_path), "--output", str(back_path))
    except RuntimeError as exc:
        result["hide_error"] = str(exc)
        return result
    result.update(hide_s=round(hide_s, 1), hide_ms=hide.get("elapsed_ms"),
                  restore_s=round(restore_s, 1), restore_ms=restore.get("elapsed_ms"),
                  hide=score_hide(case, anon_path.read_text(encoding="utf-8"), back_path.read_text(encoding="utf-8")))
    return result


def repeat_scan(case: dict, types: list[str], out: Path, times: int) -> dict:
    """同一用例重复 scan，统计每个值的命中次数和结果是否稳定。"""
    out.mkdir(parents=True, exist_ok=True)
    source = out / "original.txt"
    source.write_text(case["text"], encoding="utf-8", newline="\n")
    type_args = [arg for t in types for arg in ("--type", t)]
    runs = []
    for _ in range(times):
        scan, _ = has("scan", *type_args, "--file", str(source))
        runs.append({cat: values for cat, values in scan["entities"].items() if values})
    return {"id": case["id"], "title": case["title"], "runs": runs}


def long_case(data: dict, repeats: int) -> dict:
    parts: list[str] = []
    expected: dict[str, list[str]] = {}
    keep: list[str] = []
    n = 1
    for case in data["cases"]:
        parts.append(case["text"])
        for _ in range(repeats):
            parts.append(data["filler"].format(n=n))
            n += 1
        for cat, values in case["expected"].items():
            expected.setdefault(cat, [])
            expected[cat] += [v for v in values if v not in expected[cat]]
        keep += [k for k in case["keep"] if k not in keep]
    return {"id": f"long_{repeats}", "title": f"长文拼接（每段后 {repeats} 条填充）", "text": "\n\n".join(parts), "expected": expected, "keep": keep}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", action="append", help="只跑指定 id，可重复")
    parser.add_argument("--long", type=int, action="append", default=[], help="追加长文用例，值为每段后的填充条数")
    parser.add_argument("--out", default="results")
    parser.add_argument("--cases", default="cases.json")
    parser.add_argument("--repeat", type=int, help="只重复 scan，不跑 hide/restore")
    args = parser.parse_args()

    data = json.loads((HERE / args.cases).read_text(encoding="utf-8"))
    cases = [c for c in data["cases"] if not args.only or c["id"] in args.only]
    cases += [long_case(data, n) for n in args.long]
    types = rule_categories()
    out = HERE / args.out

    if args.repeat:
        repeated = []
        for case in cases:
            print(f"[{case['id']}] x{args.repeat} ...", flush=True)
            repeated.append(repeat_scan(case, types, out / case["id"], args.repeat))
            (out / "repeat.json").write_text(json.dumps(repeated, ensure_ascii=False, indent=2), encoding="utf-8")
        return

    results = []
    for case in cases:
        print(f"[{case['id']}] ...", flush=True)
        result = run_case(case, types, out / case["id"])
        results.append(result)
        s = result["scan"]
        print(f"  命中 {s['hit']}/{s['expected']} 错类 {len(s['wrong_cat'])} 边界 {len(s['partial'])} 漏 {len(s['missed'])} 多余 {len(s['extra'])} 误伤保护 {len(s['protected_hit'])} 用时 {result['scan_s']}s", flush=True)
        (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
