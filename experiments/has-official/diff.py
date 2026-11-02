#!/usr/bin/env python3
"""对比原文、hide 结果和 restore 结果，定位差异。"""

import difflib
import json
import sys
from pathlib import Path

base = Path(__file__).parent / "results"
for case in sys.argv[1:]:
    d = base / case
    original = (d / "original.txt").read_text(encoding="utf-8")
    hidden = (d / "hidden.txt").read_text(encoding="utf-8")
    restored = (d / "restored.txt").read_text(encoding="utf-8")
    print(f"\n===== {case} 原文 {len(original)} 字，hide {len(hidden)} 字，restore {len(restored)} 字")
    print("--- mapping")
    print(json.dumps(json.loads((d / "mapping.json").read_text(encoding="utf-8")), ensure_ascii=False))
    print("--- 原文 vs restore")
    matcher = difflib.SequenceMatcher(None, original, restored, autojunk=False)
    shown = 0
    for tag, a0, a1, b0, b1 in matcher.get_opcodes():
        if tag == "equal":
            continue
        shown += 1
        if shown > 25:
            print("  ...")
            break
        print(f"  {tag} @{a0}: 原={original[max(0, a0 - 12):a1 + 12]!r} 还原={restored[max(0, b0 - 12):b1 + 12]!r}")
