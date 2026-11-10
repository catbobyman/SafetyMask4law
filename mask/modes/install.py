"""Install mode: put the Mask skill and guard into a host. DeepSeek Harness (dsh) only.

dsh reads skills from `<dsh home>/skills` and has no command hooks; the guard is a plugin row in
`<dsh home>/cordis.patch.yml`, the user's patch layer applied to every profile. That file is the
user's: only the block between the two marker lines is added or removed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import sys

from ..official import ROOT

BEGIN, END = '# >>> mask guard', '# <<< mask guard'


def dsh_home():
    return Path(os.environ.get('DSH_HOME') or Path.home() / '.dsh')


def _block():
    quote = lambda value: json.dumps(str(value), ensure_ascii=False)
    plugin = (ROOT / 'integrations/guard/dsh/index.mjs').as_posix()
    return (f'{BEGIN}\n- insert:\n    - id: mask-guard\n      name: {quote(plugin)}\n'
            f'      config: {{python: {quote(sys.executable)}, root: {quote(ROOT)}}}\n{END}\n')


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


def installed():
    file = dsh_home() / 'cordis.patch.yml'
    return file.exists() and BEGIN in file.read_text(encoding='utf-8')


def install():
    home = dsh_home()
    if not home.is_dir():
        raise SystemExit(f'没有找到 DeepSeek Harness 的配置目录 {home}')
    skill = home / 'skills/mask'
    skill.mkdir(parents=True, exist_ok=True)
    source = (ROOT / 'integrations/skill/mask/SKILL.md').read_text(encoding='utf-8')
    (skill / 'SKILL.md').write_text(source.replace('<MASK>', str(ROOT)), encoding='utf-8')
    _patch(home / 'cordis.patch.yml', _block())
    print(f'技能已写入 {skill}\n拦截插件已加入 {home / "cordis.patch.yml"}\n重启 DeepSeek Harness 后生效。')


def uninstall():
    home = dsh_home()
    shutil.rmtree(home / 'skills/mask', ignore_errors=True)
    _patch(home / 'cordis.patch.yml', '')
    print(f'已从 {home} 移除 Mask 的技能和拦截插件。重启 DeepSeek Harness 后生效。')
