import json
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from mask.categories import all_categories, default_categories, pattern_hits
from mask.modes import gateway, guard
from mask.modes.workbench import create_app
from mask.project import Project, app_dir

ID = '11010519491231002X'
TEXT = f'原告张三，身份证{ID}，1985年3月2日出生，就职于星河示例科技有限公司。'
NAME, COMPANY = '<人名[1].人名.名称>', '<公司[1].公司.名称>'


class NameAndCompany:
    def scan(self, text, categories):
        found = {'人名': ['张三'], '公司': ['星河示例科技有限公司']}
        if '出生日期' in categories:
            found['出生日期'] = ['1985年3月2日']
        return found


def masked_project(tmp_path, monkeypatch):
    # Guard and skill commands find a project's private folder from its workspace path.
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'private'))
    (tmp_path / 'ws').mkdir()
    project = Project(tmp_path / 'ws')
    source = project.workspace / '起诉状.txt'
    source.write_text(TEXT, encoding='utf-8')
    doc = project.add(source.name, source.read_bytes(), source)
    project.recognize(doc, NameAndCompany())
    payload = project.public()
    for c in payload['documents'][0]['candidates']:
        c['decision'] = 'mask'
    project.review(payload)
    return project


def test_patterns_scope_rescan_and_sealed_files(tmp_path, monkeypatch):
    assert set(default_categories()) < set(all_categories())
    assert pattern_hits(f'{ID}，110105194912310021', all_categories()) == [(0, 18, '身份证')]
    project = masked_project(tmp_path, monkeypatch)
    doc = project.state['documents'][0]
    # The model missed the ID number; its checksum makes it a candidate anyway.
    assert {c['original']: c['type'] for c in doc['candidates']} == {'张三': '人名', ID: '身份证', '星河示例科技有限公司': '公司'}
    for file in ['project.mask', 'vault.mask']:
        data = (project.directory / file).read_bytes()
        assert '张三'.encode() not in data and ID.encode() not in data
    before = dict(project.vault.mapping)
    project.set_scope(default_categories() + ['出生日期'])
    assert doc['status'] == 'queued'
    project.recognize(doc, NameAndCompany())
    # A rescan under a wider scope adds the new candidate and keeps earlier decisions and tags.
    decisions = {c['original']: c['decision'] for c in doc['candidates']}
    assert decisions == {'张三': 'mask', ID: 'mask', '1985年3月2日': 'pending', '星河示例科技有限公司': 'mask'}
    assert project.vault.mapping == before
    reloaded = Project(project.workspace, project.directory)
    assert reloaded.vault.mapping == before and reloaded.categories == project.categories


def test_saved_default_scope_starts_new_projects(tmp_path, monkeypatch):
    project = masked_project(tmp_path, monkeypatch)
    wider = set(default_categories()) | {'出生日期'}
    with TestClient(create_app(project, 'launch'), base_url='http://127.0.0.1') as client:
        client.post('/session', headers={'X-Launch-Token': 'launch'})
        put = lambda categories: client.put('/api/default-scope', headers={'X-Mask': '1'}, json={'categories': categories})
        assert put([*wider, '不存在的类别']).status_code == 400
        assert set(put([*wider]).json()['default_scope']) == wider
        assert set(client.get('/api/project').json()['default_scope']) == wider
    (tmp_path / 'other').mkdir()
    assert set(Project(tmp_path / 'other').categories) == wider
    assert project.categories == default_categories()
    # A category the rules dropped after the default was saved is ignored instead of failing every new project.
    (tmp_path / 'private/Mask/default-scope.json').write_text(json.dumps(['已删除的类别', '人名']), encoding='utf-8')
    (tmp_path / 'third').mkdir()
    assert Project(tmp_path / 'third').categories == ['人名']


installed_block = lambda patch: 'mask-guard' in patch.read_text(encoding='utf-8')


def test_dsh_install_touches_only_its_own_block(tmp_path, monkeypatch):
    from mask.modes import install
    monkeypatch.setenv('DSH_HOME', str(tmp_path))
    started = []
    monkeypatch.setattr(install.gateway, 'start', lambda: started.append(1))
    patch = tmp_path / 'cordis.patch.yml'
    # Mask's address row would replace a DeepSeek route the user configured, in the home patch or in a profile's.
    for own in (patch, tmp_path / 'profiles/web/cordis.patch.yml'):
        own.parent.mkdir(parents=True, exist_ok=True)
        own.write_text('- id: llm-deepseek\n  config: {reasoningEffort: max}\n', encoding='utf-8')
        with pytest.raises(SystemExit):
            install.install()
        assert 'mask' not in own.read_text(encoding='utf-8') and not started and not (tmp_path / 'skills').exists()
        own.unlink()
    for mine, emptied in [('# mine\n- id: ui-theme\n  config: {preference: dark}\n', None), ('# shipped\n[]\n', '# shipped\n[]\n'), (None, None)]:
        patch.write_text(mine, encoding='utf-8') if mine else patch.unlink(missing_ok=True)
        install.install(); install.install()
        text = patch.read_text(encoding='utf-8')
        assert text.count('mask-guard') == 1 and text.count('baseURL: "http://127.0.0.1:8788/dsh"') == 1 and started and '[]' not in text and '<MASK>' not in (tmp_path / 'skills/mask/SKILL.md').read_text(encoding='utf-8')
        install.uninstall()
        assert not (tmp_path / 'skills/mask').exists()
        # A patch file without rows stops dsh from booting, so the empty layer comes back.
        assert (patch.read_text(encoding='utf-8') if patch.exists() else None) == (emptied or mine)
    # The workbench switch is the same install and uninstall.
    with TestClient(create_app(Project(tmp_path / 'ws', tmp_path / 'data'), 'launch'), base_url='http://127.0.0.1') as client:
        client.post('/session', headers={'X-Launch-Token': 'launch'})
        switch = lambda on: client.put('/api/hosts/dsh', headers={'X-Mask': '1'}, json={'enabled': on}).json()
        assert switch(True) == {'hosts': [{'id': 'dsh', 'enabled': True}]} and installed_block(patch)
        assert client.get('/api/project').json()['hosts'] == [{'id': 'dsh', 'enabled': True}]
        # The panel reads its state live: the patch file, whether the gateway listens, whether dsh has sent a request.
        heard = {}
        monkeypatch.setattr(gateway, 'health', lambda: heard.get('agents'))
        panel = lambda: client.get('/api/hosts').json()
        assert panel()['agents'][0]['state'] == 'down' and not panel()['gateway']['running']
        # A request from before the address was written came from a dsh that still ran on its old settings.
        for heard['agents'] in ({}, {'dsh': '2000-01-01T00:00:00'}):
            assert panel()['agents'][0]['state'] == 'pending'
        heard['agents'] = {'dsh': '9999-01-01T00:00:00'}
        gateway.log({'t': gateway.time.strftime('%Y-%m-%dT%H:%M:%S'), 'agent': 'dsh', 'project': 'x', 'tags': {NAME: 2}, 'status': 200})
        gateway.log({'t': gateway.time.strftime('%Y-%m-%dT%H:%M:%S'), 'agent': 'dsh', 'project': 'x', 'refused': 'strict', 'status': 400})
        assert panel()['agents'][0]['state'] == 'live' and panel()['gateway'] == {
            'running': True, 'problem': None, 'requests': 2, 'replaced': 2, 'refused': 1}
        assert [r['status'] for r in client.get('/api/gateway/log').json()['records']] == [400, 200]
        # Disconnecting is confirmed against the lines it removes; connecting can be looked at before and after.
        removed = client.get('/api/hosts/dsh/preview', params={'enabled': False}).json()
        assert removed['file'] == str(patch) and {kind for kind, _ in removed['lines']} == {'-'}
        added = client.get('/api/hosts/dsh/preview', params={'enabled': True}).json()['lines']
        assert [text for kind, text in added if kind == '+'] == [text for _, text in removed['lines']]
        assert switch(False) == {'hosts': [{'id': 'dsh', 'enabled': False}]} and not patch.exists()
        assert panel()['agents'][0]['state'] == 'off' and panel()['agents'][0]['blocked'] is None


def test_legacy_plain_project_is_sealed(tmp_path):
    folder = tmp_path / 'private'
    folder.mkdir()
    (folder / 'project.json').write_text(json.dumps({
        'revision': 3, 'documents': [], 'outputs': [], 'workspace': str(tmp_path), 'reserved': [], 'mapping': {NAME: '张三'},
        'entities': {'e': {'id': 'e', 'type': '人名', 'no': 1, 'values': ['张三'], 'variants': {'张三': NAME}}}}, ensure_ascii=False), encoding='utf-8')
    project = Project(tmp_path, folder)
    assert not (folder / 'project.json').exists()
    assert project.vault.mapping == {NAME: '张三'} and project.vault.data['issued'] == [NAME]
    assert project.public()['entities']['e']['variants'] == {'张三': NAME}


def test_handoff_and_host_status(tmp_path, monkeypatch):
    project = masked_project(tmp_path, monkeypatch)
    assert not project.status()['handed_off'] and not project.status()['files']
    project.export('markdown', handoff=True)
    status = project.status()
    assert status['handed_off'] and status['pending'] == 0 and len(status['files']) == 1
    shared = Path(status['files'][0])
    assert shared.parent == project.workspace / 'mask-handoff' and shared.name == '材料1.脱敏.md'
    text = shared.read_text(encoding='utf-8')
    assert '张三' not in text and ID not in text and NAME in text
    # Status is printed into the host conversation.
    printed = json.dumps(status, ensure_ascii=False)
    assert '张三' not in printed and '起诉状' not in printed


def test_guard_hook(tmp_path, monkeypatch):
    project = masked_project(tmp_path, monkeypatch)
    cwd = str(project.workspace)
    prompt = lambda text, where=cwd: guard.hook({'hook_event_name': 'UserPromptSubmit', 'cwd': where, 'prompt': text})
    assert prompt('总结材料1的争议焦点') is None
    blocked = prompt('张三能胜诉吗')
    assert blocked['decision'] == 'block' and NAME in blocked['reason']
    assert prompt('证件 110101199003074610')['decision'] == 'block'
    assert prompt('张三', str(tmp_path / 'elsewhere')) is None
    read = lambda path: guard.hook({'hook_event_name': 'PreToolUse', 'cwd': cwd, 'tool_name': 'Read', 'tool_input': {'file_path': path}})
    assert read('起诉状.txt')['hookSpecificOutput']['permissionDecision'] == 'deny'
    assert read(str(project.directory / 'vault.mask'))['hookSpecificOutput']['permissionDecision'] == 'deny'
    assert read('mask-handoff/材料1.脱敏.md') is None
    # Searching the folder that holds the original would return its text; the handoff folder stays searchable.
    grep = lambda where, **args: guard.hook({'hook_event_name': 'PreToolUse', 'cwd': where, 'tool_name': 'grep', 'tool_input': {'pattern': '原告', **args}})
    assert grep(cwd)['hookSpecificOutput']['permissionDecision'] == 'deny'
    assert grep(cwd + '/mask-handoff', path='..')['hookSpecificOutput']['permissionDecision'] == 'deny'
    assert grep(cwd + '/mask-handoff') is None and grep(cwd, path='mask-handoff') is None


def test_gateway_rewrites_text_and_keeps_protocol(tmp_path, monkeypatch):
    project = masked_project(tmp_path, monkeypatch)
    seen = []
    async def upstream(request):
        seen.append(await request.json())
        return JSONResponse({'content': NAME})
    # dsh states its folder as a JSON string inside a runtime-context text block.
    context = lambda folder: {'type': 'text', 'text': f'Current working directory: {json.dumps(str(folder))}.'}
    sealed = {'type': 'thinking', 'thinking': '张三', 'signature': 'c2ln'}
    schema = {'type': 'object', 'properties': {'张三': {'type': 'string', 'description': '查张三'}}}
    request = lambda folder: {
        'model': 'deepseek-flash', 'max_tokens': 5, 'system': [{'type': 'text', 'text': '用户是张三的代理人'}],
        'tools': [{'name': '张三', 'description': '查张三的案件', 'input_schema': schema}],
        'messages': [
            {'role': 'user', 'content': [*([context(folder)] if folder else []), {'type': 'text', 'text': '张三起诉星河示例科技有限公司'}]},
            {'role': 'assistant', 'content': [sealed, {'type': 'tool_use', 'id': 'toolu_张三', 'name': '张三',
                                                       'input': {'name': '张三', 'nested': json.dumps({'query': '张三'})}}]},
            {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'toolu_张三', 'content': [{'type': 'text', 'text': '张三'}]},
                                         {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': '张三'}}]}]}
    app = gateway.create_app(httpx.ASGITransport(app=Starlette(routes=[Route('/anthropic/v1/messages', upstream, methods=['POST'])])))
    with TestClient(app) as client:
        send = lambda body, agent='dsh': client.post(f'/{agent}/v1/messages', json=body)
        # The reply keeps its tags: real names are restored in the workbench only.
        assert send(request(project.workspace / 'sub')).json() == {'content': NAME}
        sent = seen[0]
        assert sent['system'][0]['text'] == f'用户是{NAME}的代理人' and sent['messages'][0]['content'][1]['text'] == NAME + '起诉' + COMPANY
        # What the model wrote for a tool is text under the tool's own keys; JSON nested in a string is escaped.
        call = sent['messages'][1]['content'][1]
        assert call['input']['name'] == NAME and json.loads(call['input']['nested']) == {'query': NAME}
        assert sent['messages'][2]['content'][0]['content'][0]['text'] == NAME
        # What the vendor matches, decodes or verifies goes out as written.
        assert call['name'] == '张三' and call['id'] == sent['messages'][2]['content'][0]['tool_use_id'] == 'toolu_张三'
        assert sent['messages'][1]['content'][0] == sealed and sent['messages'][2]['content'][1]['source']['data'] == '张三'
        assert sent['tools'] == [{'name': '张三', 'description': f'查{NAME}的案件', 'input_schema': {
            'type': 'object', 'properties': {'张三': {'type': 'string', 'description': f'查{NAME}'}}}}]
        assert sent['model'] == 'deepseek-flash' and sent['max_tokens'] == 5

        # A folder without a project, or no folder stated: every project's originals are replaced.
        send(request(tmp_path / 'elsewhere')), send(request(None))
        assert seen[1]['messages'][0]['content'][1]['text'].startswith(NAME) and seen[2]['messages'][0]['content'][0]['text'].startswith(NAME)

        refused = lambda response: (response.status_code, response.json()['error']['message'])
        # A number the user never confirmed in the workbench; its checksum is valid.
        assert refused(send({'messages': [{'role': 'user', 'content': '证件 110101199003074610'}]}))[0] == 400
        assert client.post('/dsh/v1/files', content=b'not json').status_code == 400
        # The model would get a path it cannot use; the message names the tag, not the original.
        status, message = refused(send(request(project.workspace / '张三')))
        assert status == 400 and NAME in message and '张三' not in message
        assert send({}, 'other').status_code == 404 and len(seen) == 3
        assert client.get('/mask/health').json()['agents'].keys() == {'dsh'}
    records = [json.loads(line) for file in (app_dir() / 'gateway').glob('*.jsonl') for line in file.read_text(encoding='utf-8').splitlines()]
    assert [r['status'] for r in records] == [200, 200, 200, 400, 400, 400] and records[0]['tags'][NAME] == 7
    assert records[0]['project'] == project.directory.name and records[2]['project'] == '*' and records[3]['strict'] == ['身份证']
    assert '张三' not in json.dumps(records, ensure_ascii=False) and str(tmp_path) not in json.dumps(records)
