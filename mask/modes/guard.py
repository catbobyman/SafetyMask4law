"""Guard mode: keep known originals out of what a host sends to its cloud model.

The host runs `python -m mask guard hook` before a prompt is submitted or a tool is used. A hook can
refuse, not rewrite; rewriting what goes to the model is the gateway's job (modes/gateway.py).

The guard knows what the user already confirmed in the workbench (the vault) and values with a
checksum. It does not run the recognition model: nobody reviews its decisions.
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import sys

from .. import store
from ..categories import STRICT, pattern_hits
from ..project import Project, data_dir, find
from ..vault import Vault, replacer


def checker(replace):
    """(text, tags, strict): text(s) replaces known originals in one string through `replace`, counting the tags
    written into `tags` and adding the categories of unmasked checksum-valid numbers left in it to `strict`."""
    tags, strict = Counter(), set()
    def text(value):
        value, used = replace(value)
        tags.update(used)
        strict.update(kind for _, _, kind in pattern_hits(value, STRICT))
        return value
    return text, tags, strict


def scrub(value, vault: Vault):
    """Replace known originals in every string of a JSON value.

    Returns (value, replaced, strict) where strict lists categories of unmasked checksum-valid numbers left in it.
    """
    text, tags, strict = checker(replacer(vault.forms(escaped=True)))
    def walk(v):
        if isinstance(v, str):
            return text(v)
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        return v
    return walk(value), sum(tags.values()), sorted(strict)


def load_vault(workspace: Path):
    return Vault(store.read(data_dir(workspace) / 'vault.mask'))


def hook(event: dict):
    """Decide one host hook event (Claude Code / Codex hook JSON). Returns the JSON to print, or None to allow."""
    workspace = find(Path(event.get('cwd') or '.'))
    if not workspace:
        return None
    name = event.get('hook_event_name')
    if name == 'UserPromptSubmit':
        prompt = event.get('prompt', '')
        vault = load_vault(workspace)
        known = sorted({o for o in vault.mapping.values() if o in prompt}, key=len, reverse=True)
        _, _, strict = scrub(prompt, vault)
        if not known and not strict:
            return None
        # The host shows this reason to the user and drops the prompt; it is not sent to the model.
        lines = [f'  {o} → {t}' for t, o in vault.mapping.items() if o in known]
        reason = 'Mask 已拦截：这句话含有已脱敏的真实信息，未发送。请改用代号：\n' + '\n'.join(lines) if known else ''
        if strict:
            reason += ('\n' if reason else 'Mask 已拦截：') + f'含有未脱敏的{"、".join(strict)}号码，请先在工作台处理该材料。'
        return {'decision': 'block', 'reason': reason}
    if name == 'PreToolUse':
        project = Project(workspace)
        private = {Path(d['origin']).resolve() for d in project.state['documents'] if d.get('origin')}
        tool_input = event.get('tool_input') or {}
        cwd = Path(event.get('cwd') or '.').resolve()
        targets = {(cwd / tool_input[k]).resolve()
                   for k in ('file_path', 'path', 'notebook_path') if isinstance(tool_input.get(k), str)}
        if not targets and str(event.get('tool_name')).lower() in ('grep', 'glob'):
            # A search without a path covers the folder the host is working in.
            targets = {cwd}
        command = tool_input.get('command') if isinstance(tool_input.get('command'), str) else ''
        inside = any(project.directory == t or project.directory in t.parents for t in targets)
        # Searching or listing a folder that holds an original returns its text or its name.
        covers = any(t in p.parents for t in targets for p in private)
        if targets & private or inside or covers or any(p.name in command for p in private) or str(project.directory) in command:
            return {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                    'permissionDecisionReason': 'Mask：这里有已脱敏材料的原件或映射，不能读取、搜索或列出。请只在 mask-handoff/ 里读取脱敏稿。'}}
    return None


def run_hook():
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        result = hook(json.loads(sys.stdin.buffer.read().decode('utf-8')))
    except Exception as exc:
        # Exit code 2 makes the host refuse the action: a guard that cannot check does not let it through.
        print(f'Mask 拦截检查失败，已阻止本次操作：{type(exc).__name__}: {exc}', file=sys.stderr)
        raise SystemExit(2)
    if result:
        print(json.dumps(result, ensure_ascii=False))

