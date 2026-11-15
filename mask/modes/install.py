"""Install mode: put the Mask skill, guard and gateway address into a host: DeepSeek Harness (dsh) or Codex.

dsh reads skills from `<dsh home>/skills` and has no command hooks; the guard is a plugin row in
`<dsh home>/cordis.patch.yml`, the user's patch layer applied to every profile, and the address of
its DeepSeek route is a second row there (dsh refuses DEEPSEEK_BASE_URL in its .env).

Codex reads skills from `<codex home>/skills`; the address of its built-in OpenAI provider is the
top-level `openai_base_url` in `<codex home>/config.toml`. No guard is installed into Codex.

Both files are the user's: only Mask's own block or line is added or removed.
"""
from __future__ import annotations

import difflib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
import tomllib

from ..official import ROOT
from ..project import app_dir
from . import gateway

# Each host: its name, and the file in its home that Mask edits.
HOSTS = {'dsh': ('DeepSeek Harness', 'cordis.patch.yml'), 'codex': ('Codex', 'config.toml')}
BEGIN, END = '# >>> mask guard', '# <<< mask guard'
# A top-level key, so it goes before the first table of the file.
CODEX_LINE = f'openai_base_url = "{gateway.URL}/codex"  # Mask 网关；mask uninstall codex 移除\n'
CODEX_OWN = re.compile(rf'(?m)^openai_base_url *= *"{re.escape(gateway.URL)}/codex".*\n?')


def home(host: str):
    """DSH_HOME or ~/.dsh; CODEX_HOME or ~/.codex."""
    return Path(os.environ.get(f'{host.upper()}_HOME') or Path.home() / f'.{host}')


def config(host: str):
    return home(host) / HOSTS[host][1]


def _block():
    quote = lambda value: json.dumps(str(value), ensure_ascii=False)
    plugin = (ROOT / 'integrations/guard/dsh/index.mjs').as_posix()
    return (f'{BEGIN}\n- insert:\n    - id: mask-guard\n      name: {quote(plugin)}\n'
            f'      config: {{python: {quote(sys.executable)}, root: {quote(ROOT)}}}\n'
            # dsh's DeepSeek route (API key) sends its requests to the Mask gateway.
            f'- id: llm-deepseek\n  config:\n    baseURL: {quote(gateway.URL + "/dsh")}\n{END}\n')


def _patched(now: str, block: str):
    """dsh's patch file with Mask's block replaced by `block`; '' removes the block."""
    text = re.sub(f'{re.escape(BEGIN)}.*?{re.escape(END)}\n?', '', now, flags=re.S)
    rows = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith('#')]
    if rows == ['[]']:
        # The shipped empty layer; list rows cannot follow it.
        text, rows = ''.join(line for line in text.splitlines(True) if line.strip() != '[]'), []
    if block and rows and not rows[0].startswith('-'):
        raise SystemExit(f'{config("dsh")} 不是逐行的列表写法，无法自动加入。请手动加入：\n{block}')
    if block:
        return (text.rstrip('\n') + '\n' if text.strip() else '') + block
    if rows:
        return text
    # dsh refuses to boot on a patch file without rows.
    return text + '[]\n' if text.strip() else ''


def _edited(host: str, on: bool):
    """(the host's config file now, the same with Mask's part added or removed)."""
    file = config(host)
    if host == 'dsh':
        now = file.read_text(encoding='utf-8') if file.exists() else ''
        return now, _patched(now, _block() if on else '')
    # Codex's whole configuration is in this file: read as bytes, its line endings stay as they are.
    now = file.read_bytes().decode('utf-8') if file.exists() else ''
    rest = CODEX_OWN.sub('', now)
    return now, CODEX_LINE + rest if on else rest


def _write(host: str, on: bool):
    """Write the host's config file; True when that changed it."""
    now, text = _edited(host, on)
    if text == now:
        return False
    if not text:
        config(host).unlink(missing_ok=True)
    elif host == 'dsh':
        config(host).write_text(text, encoding='utf-8')
    else:
        config(host).write_bytes(text.encode('utf-8'))
    return True


def _written(host: str):
    return app_dir() / 'gateway' / f'{host}.written'


def written(host: str):
    """When Mask's part last changed in the host's config file, as the gateway's record time, or '' when unknown."""
    file = _written(host)
    return file.read_text(encoding='utf-8') if file.exists() else ''


def preview(host: str, on: bool):
    """What switching a host on or off changes in its config file, as lines for the panel: [kind, text] with kind
    '+', '-' or ' '. Switching on is shown from the file without Mask's part, also when it is already on."""
    now, without = _edited(host, False)
    before, after = (without, _edited(host, True)[1]) if on else (now, without)
    lines = [[line[0], line[2:]] for line in difflib.ndiff(before.splitlines(), after.splitlines()) if line[0] != '?']
    # A few lines of the user's own file around the change are enough to place it.
    changed = [i for i, (kind, _) in enumerate(lines) if kind != ' ']
    return {'file': str(config(host)), 'lines': lines[max(0, changed[0] - 2):changed[-1] + 3] if changed else []}


def blocked(host: str):
    """Why the host cannot be switched on, or None."""
    name, folder = HOSTS[host][0], home(host)
    if not folder.is_dir():
        return f'没有找到 {name} 的配置目录 {folder}'
    if host == 'dsh':
        own = _own_route(folder)
        if own:
            # A patch entry replaces the whole config of its row: Mask's would drop what the user set there.
            return f'{own} 里已有 llm-deepseek 的配置（DeepSeek 路由的地址或模型设置）。Mask 的地址项会整项盖掉它，所以不接入；删去那一项后可以接入'
        return None
    settings = tomllib.loads(_edited(host, False)[1])
    if 'openai_base_url' in settings:
        # Another tool's gateway, or the user's own: Mask would have to take its place and could not put it back.
        return f'{config(host)} 里已有 openai_base_url（{settings["openai_base_url"]}），Mask 不覆盖它；删去那一行后可以接入'
    if settings.get('model_provider', 'openai') != 'openai':
        return (f'{config(host)} 里 model_provider 是 {settings["model_provider"]}，请求不走内置的 OpenAI 通道，'
                '改 openai_base_url 不起作用；改回 openai 后可以接入')
    auth = folder / 'auth.json'
    if not auth.exists() or json.loads(auth.read_text(encoding='utf-8')).get('auth_mode') != 'chatgpt':
        # With an API key Codex talks to api.openai.com/v1; the gateway's upstream for Codex is chatgpt.com.
        return 'Mask 目前只接入用 ChatGPT 账号登录的 Codex；用 API Key 登录或未登录时不接入'


def _own_route(folder: Path):
    """The patch file in which the user configured dsh's DeepSeek route themselves, or None."""
    for file in [folder / 'cordis.patch.yml', *sorted(folder.glob('profiles/*/cordis.patch.yml'))]:
        text = re.sub(f'{re.escape(BEGIN)}.*?{re.escape(END)}', '', file.read_text(encoding='utf-8'), flags=re.S) if file.exists() else ''
        if re.search(r'(?m)^- id: llm-deepseek\s*(#.*)?$', text):
            return file


def installed(host: str):
    now, without = _edited(host, False)
    return BEGIN in now if host == 'dsh' else now != without


def install(host: str):
    name, folder = HOSTS[host][0], home(host)
    problem = blocked(host)
    if problem:
        raise SystemExit(problem)
    # The gateway listens before the host is pointed at it, so the host never talks to a closed port.
    gateway.start()
    skill = folder / 'skills/mask'
    skill.mkdir(parents=True, exist_ok=True)
    source = (ROOT / 'integrations/skill/mask/SKILL.md').read_text(encoding='utf-8')
    (skill / 'SKILL.md').write_text(source.replace('<MASK>', str(ROOT)), encoding='utf-8')
    if _write(host, True):
        _written(host).parent.mkdir(parents=True, exist_ok=True)
        _written(host).write_text(time.strftime('%Y-%m-%dT%H:%M:%S'), encoding='utf-8')
    what = '拦截插件已加入 {file}，DeepSeek 官方路由的地址在同一处' if host == 'dsh' else '{file} 里内置 OpenAI 通道的地址（openai_base_url）'
    print(f'技能已写入 {skill}\n{what.format(file=config(host))}指向 Mask 网关 {gateway.URL}/{host}\n重启 {name} 后生效。')


def uninstall(host: str):
    name, folder = HOSTS[host][0], home(host)
    shutil.rmtree(folder / 'skills/mask', ignore_errors=True)
    _write(host, False)
    _written(host).unlink(missing_ok=True)
    print(f'已从 {folder} 移除 Mask 的技能{"、拦截插件" if host == "dsh" else ""}和网关地址。重启 {name} 后生效，此后它的请求不再经过 Mask。')
