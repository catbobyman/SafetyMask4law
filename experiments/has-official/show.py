#!/usr/bin/env python3
"""打印 results.json 的逐例细节。"""

import json
import sys
from pathlib import Path

path = Path(__file__).parent / (sys.argv[1] if len(sys.argv) > 1 else "results") / "results.json"
for r in json.loads(path.read_text(encoding="utf-8")):
    s = r["scan"]
    print(f"\n== {r['id']} {r['title']} {r['chars']}字 scan {r['scan_s']}s/{r['scan_ms']}ms hide {r.get('hide_s')}s/{r.get('hide_ms')}ms restore {r.get('restore_s')}s")
    for key in ("wrong_cat", "partial", "missed", "extra", "protected_hit", "not_in_text"):
        if s[key]:
            print(f"  {key}: {json.dumps(s[key], ensure_ascii=False)}")
    if "hide" in r:
        h = r["hide"]
        print(f"  hide: tags={h['tags']} roundtrip={h['roundtrip_equal']} leaked={h['leaked']} keep_lost={h['keep_lost']}")
    if "hide_error" in r:
        print("  hide_error:", r["hide_error"][:600])
