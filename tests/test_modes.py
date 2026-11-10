import json
from pathlib import Path

import httpx
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from mask.categories import all_categories, default_categories, pattern_hits
from mask.modes import guard
from mask.modes.workbench import create_app
from mask.project import Project

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


def test_guard_hook_and_proxy(tmp_path, monkeypatch):
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

    seen = []
    async def upstream(request):
        seen.append(await request.json())
        return JSONResponse({'content': NAME})
    proxy = guard.create_proxy('http://upstream', lambda: project.vault,
                               httpx.ASGITransport(app=Starlette(routes=[Route('/v1/messages', upstream, methods=['POST'])])))
    with TestClient(proxy) as client:
        nested = json.dumps({'query': '张三'})  # tool arguments: JSON inside a string, non-ASCII escaped
        response = client.post('/v1/messages', json={'max_tokens': 5, 'messages': [
            {'role': 'user', 'content': '张三起诉星河示例科技有限公司'}, {'role': 'assistant', 'content': [{'input': nested}]}]})
        # The reply keeps its tags: real names are restored in the workbench only.
        assert response.json() == {'content': NAME}
        assert seen[0]['messages'][0]['content'] == NAME + '起诉' + COMPANY and seen[0]['max_tokens'] == 5
        assert json.loads(seen[0]['messages'][1]['content'][0]['input']) == {'query': NAME}
        assert client.post('/v1/messages', json={'messages': '证件 110101199003074610'}).status_code == 400
        assert client.post('/v1/messages', content=b'not json').status_code == 400
        assert len(seen) == 1
