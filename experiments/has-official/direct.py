#!/usr/bin/env python3
"""用官方模板直连 llama-server，对比默认采样与 temperature=0，并记录 GPU 与耗时。"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from run import LLAMA_DIR, MODEL, ROOT, rule_categories  # noqa: E402

sys.path.insert(0, str(ROOT / "vendor/skills/has-anonymizer/scripts"))
from has_text.mapping import parse_json_tolerant  # noqa: E402
from has_text.prompts import build_ner_messages  # noqa: E402

PORT = 18080
BASE = f"http://127.0.0.1:{PORT}"
opener = build_opener(ProxyHandler({}))


def call(path: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(BASE + path, data=data, headers={"Content-Type": "application/json"})
    with opener.open(request, timeout=120) as response:
        return json.load(response)


def wait_ready(process: subprocess.Popen) -> None:
    for _ in range(120):
        if process.poll() is not None:
            raise RuntimeError(f"llama-server 已退出，exit={process.returncode}")
        try:
            call("/health")
            return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError("llama-server 启动超时")


def ner(text: str, types: list[str], extra: dict) -> tuple[dict, dict]:
    reply = call("/v1/chat/completions", {"messages": build_ner_messages(text, types), **extra})
    choice = reply["choices"][0]
    if choice["finish_reason"] != "stop":
        raise RuntimeError(f"响应未完成: {choice['finish_reason']}")
    parsed = parse_json_tolerant(choice["message"]["content"])
    if not isinstance(parsed, dict):
        raise RuntimeError(f"响应不是 JSON 对象: {choice['message']['content'][:200]}")
    return {k: v for k, v in parsed.items() if v}, reply["usage"]


def main() -> None:
    log = open(HERE / "results-direct.log", "wb")
    process = subprocess.Popen(
        [str(LLAMA_DIR / "llama-server.exe"), "--host", "127.0.0.1", "--port", str(PORT), "-m", str(MODEL),
         "-ngl", "999", "-c", "8192", "-np", "1", "-fa", "on", "-ctk", "q8_0", "-ctv", "q8_0"],
        stdout=log, stderr=subprocess.STDOUT,
    )
    try:
        wait_ready(process)
        props = call("/props")
        settings = props["default_generation_settings"]["params"]
        print("默认采样:", {k: settings.get(k) for k in ("temperature", "top_k", "top_p", "seed", "n_predict")})
        print("thinking/模板片段:", props.get("chat_template", "")[:120].replace("\n", " "))
        print("GPU:", subprocess.run(
            ["nvidia-smi", "--query-compute-apps=name,used_memory", "--format=csv,noheader"],
            capture_output=True, encoding="utf-8", errors="replace").stdout.strip())
        print("GPU 总占用:", subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,utilization.gpu", "--format=csv,noheader"],
            capture_output=True, encoding="utf-8", errors="replace").stdout.strip())

        types = rule_categories()
        data = json.loads((HERE / "cases-hard.json").read_text(encoding="utf-8"))
        easy = json.loads((HERE / "cases.json").read_text(encoding="utf-8"))
        results = []
        for case in data["cases"] + easy["cases"]:
            default_runs = [ner(case["text"], types, {})[0] for _ in range(5)]
            zero_runs = [ner(case["text"], types, {"temperature": 0})[0] for _ in range(5)]
            distinct = lambda runs: len({json.dumps(r, ensure_ascii=False, sort_keys=True) for r in runs})
            print(f"[{case['id']}] 默认 5 次得到 {distinct(default_runs)} 种结果；temperature=0 得到 {distinct(zero_runs)} 种", flush=True)
            results.append({"id": case["id"], "default": default_runs, "zero": zero_runs})
        (HERE / "results-direct.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        process.terminate()
        process.wait(timeout=10)
        log.close()


if __name__ == "__main__":
    main()
