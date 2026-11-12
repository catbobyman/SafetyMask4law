"""Install mode: put the Mask skill, guard and gateway address into a host. DeepSeek Harness (dsh) only.

dsh reads skills from `<dsh home>/skills` and has no command hooks; the guard is a plugin row in
`<dsh home>/cordis.patch.yml`, the user's patch layer applied to every profile, and the address of
its DeepSeek route is a second row there (dsh refuses DEEPSEEK_BASE_URL in its .env). That file is
the user's: only the block between the two marker lines is added or removed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import sys

from ..official import ROOT
from . import gateway

BEGIN, END = '# >>> mask guard', '# <<< mask guard'


def dsh_home():
    return Path(os.environ.get('DSH_HOME') or Path.home() / '.dsh')


def _block():
    quote = lambda value: json.dumps(str(value), ensure_ascii=False)
    plugin = (ROOT / 'integrations/guard/dsh/index.mjs').as_posix()
    return (f'{BEGIN}\n- insert:\n    - id: mask-guard\n      name: {quote(plugin)}\n'
            f'      config: {{python: {quote(sys.executable)}, root: {quote(ROOT)}}}\n'
            # dsh's DeepSeek route (API key) sends its requests to the Mask gateway.
            f'- id: llm-deepseek\n  config:\n    baseURL: {quote(gateway.URL + "/dsh")}\n{END}\n')


def _patch(file: Path, block: str):
    """Rewrite the patch file with Mask's block replaced by `block` ('' removes it)."""
    text = file.read_text(encoding='utf-8') if file.exists() else ''
    text = re.sub(f'{re.escape(BEGIN)}.*?{re.escape(END)}\n?', '', text, flags=re.S)
    rows = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith('#')]
    if rows == ['[]']:
        # The shipped empty layer; list rows cannot follow it.
        text, rows = ''.join(line for line in text.splitlines(True) if line.strip() != '[]'), []
    if block and rows and not rows[0].startswith('-'):
        raise SystemExit(f'{file} 不是逐行的列表写法，无法自动加入。请手动加入：\n{block}')
    if block:
        text = (text.rstrip('\n') + '\n' if text.strip() else '') + block
    elif not rows:
        # dsh refuses to boot on a patch file without rows.
        text = text + '[]\n' if text.strip() else ''
    if text:
        file.write_text(text, encoding='utf-8')
    else:
        file.unlink(missing_ok=True)


def _own_route(home: Path):
    """The patch file in which the user configured dsh's DeepSeek route themselves, or None."""
    for file in [home / 'cordis.patch.yml', *sorted(home.glob('profiles/*/cordis.patch.yml'))]:
        text = re.sub(f'{re.escape(BEGIN)}.*?{re.escape(END)}', '', file.read_text(encoding='utf-8'), flags=re.S) if file.exists() else ''
        if re.search(r'(?m)^- id: llm-deepseek\s*(#.*)?$', text):
            return file


def installed():
    file = dsh_home() / 'cordis.patch.yml'
    return file.exists() and BEGIN in file.read_text(encoding='utf-8')


def install():
    home = dsh_home()
    if not home.is_dir():
        raise SystemExit(f'没有找到 DeepSeek Harness 的配置目录 {home}')
    own = _own_route(home)
    if own:
        # A patch entry replaces the whole config of its row: Mask's would drop what the user set there.
        raise SystemExit(f'{own} 里已有 llm-deepseek 的配置（DeepSeek 路由的地址或模型设置）。Mask 的地址项会整项盖掉它，所以没有接入。'
                         '请先删去那一项，或把其中要保留的设置告诉 Mask 的维护者')
    # The gateway listens before dsh is pointed at it, so dsh never talks to a closed port.
    gateway.start()
    skill = home / 'skills/mask'
    skill.mkdir(parents=True, exist_ok=True)
    source = (ROOT / 'integrations/skill/mask/SKILL.md').read_text(encoding='utf-8')
    (skill / 'SKILL.md').write_text(source.replace('<MASK>', str(ROOT)), encoding='utf-8')
    _patch(home / 'cordis.patch.yml', _block())
    print(f'技能已写入 {skill}\n拦截插件已加入 {home / "cordis.patch.yml"}，'
          f'DeepSeek 官方路由的地址在同一处指向 Mask 网关 {gateway.URL}/dsh\n重启 DeepSeek Harness 后生效。')


def uninstall():
    home = dsh_home()
    shutil.rmtree(home / 'skills/mask', ignore_errors=True)
    _patch(home / 'cordis.patch.yml', '')
    print(f'已从 {home} 移除 Mask 的技能、拦截插件和网关地址。重启 DeepSeek Harness 后生效，此后它的请求不再经过 Mask。')
