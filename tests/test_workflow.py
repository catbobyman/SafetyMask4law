from copy import deepcopy
import json
from pathlib import Path
import os
import re
import sys

import pymupdf as fitz
import pytest
from docx import Document
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent))
from fixtures import make_files, LINES
from mask.documents import read_document, masked_sections
from mask.official import restore
from mask.server import create_app
from mask.workflow import Project


class KnownCandidates:
    def scan(self, text, categories):
        return {'公司': ['星河示例科技有限公司'], '人名': ['张三'], '电话': ['13800138000']}


def confirm(project):
    payload = project.public()
    for d in payload['documents']:
        for c in d['candidates']:
            c['decision'] = 'mask'
    project.review(payload)


def test_three_formats_preserve_content_and_restore(tmp_path):
    project = Project(tmp_path, tmp_path / 'private')
    for path in make_files(tmp_path / 'inputs'):
        doc = project.add(path.name, path.read_bytes(), path)
        project.recognize(doc, KnownCandidates())
    with pytest.raises(ValueError, match='待确认'):
        project.export('original')
    confirm(project)
    result = project.export('original')
    assert not result['partial']
    for doc, output in zip(project.state['documents'], result['files']):
        actual = read_document(Path(output['path']))
        text = '\n'.join(p for s in actual['sections'] for p in s['paragraphs'])
        assert all(value not in text for value in ['星河示例科技有限公司', '张三', '13800138000'])
        assert all(value in text for value in ['10000元', '2026年10月30日', '北京市朝阳区人民法院'])
        for si, section in enumerate(masked_sections(doc, [c for c in doc['candidates'] if c['decision'] == 'mask'])):
            for pi, paragraph in enumerate(section['paragraphs']):
                restored = restore(paragraph, project.state['mapping'])
                assert not restored['partial']
                assert restored['text'] == doc['sections'][si]['paragraphs'][pi]
        if doc['format'] == 'TXT':
            assert restore(Path(output['path']).read_bytes().decode(), project.state['mapping'])['text'] == Path(doc['source']).read_bytes().decode()
        if doc['format'] == 'DOCX':
            word = Document(output['path'])
            assert word.paragraphs[0].runs[0].bold
            assert word.paragraphs[0].runs[1].italic
            assert word.tables[0].cell(0, 1).text == '<人名[1].人名.名称>'
            assert word.sections[0].header.paragraphs[0].text == '<公司[1].公司.名称>'
        if doc['format'] == 'PDF':
            with fitz.open(output['path']) as pdf:
                assert pdf[0].get_drawings()
                pdf[0].get_pixmap().save(tmp_path / 'pdf-export.png')
    reloaded = Project(tmp_path, project.directory)
    assert reloaded.state['mapping'] == project.state['mapping']
    assert reloaded.state['documents'][0]['candidates'] == project.state['documents'][0]['candidates']
    assert {v for v in reloaded.state['mapping'].values()} == {'星河示例科技有限公司', '张三', '13800138000'}


def test_review_overlap_stale_source_and_unknown_tags(tmp_path):
    project = Project(tmp_path, tmp_path / 'private')
    source = make_files(tmp_path / 'inputs')[0]
    doc = project.add(source.name, source.read_bytes(), source)
    project.recognize(doc, KnownCandidates())
    confirm(project)
    payload = project.public()
    c = deepcopy(payload['documents'][0]['candidates'][0])
    c['id'] = 'overlap'; c['start'] += 1; c['original'] = c['original'][1:]
    payload['documents'][0]['candidates'].append(c)
    project.review(payload)
    with pytest.raises(ValueError, match='重叠'):
        project.export('original')
    payload = project.public(); payload['documents'][0]['candidates'][-1]['decision'] = 'keep'; project.review(payload)
    source.write_bytes(b'changed')
    with pytest.raises(ValueError, match='变化'):
        project.export('original')
    result = project.restore_result('<人名[1].人名.名称>与<公司[999].公司.名称>', {})
    assert result['text'] == '张三与<公司[999].公司.名称>' and result['partial']
    with pytest.raises(ValueError, match='缺失代号'):
        project.restore_result('<公司[999].公司.名称>', {}, save=True)
    assert project.restore_result('<公司[999].公司.名称>', {'<公司[999].公司.名称>': 'keep'}, save=True)['partial']
    # Tags an AI rewrote are reported with the official shape as a suggestion, never replaced silently.
    rewritten = '＜人名［1］．人名．名称＞与\\<人名[1].人名.名称\\>，见〈附件〉'
    result = project.restore_result(rewritten, {})
    assert result['text'] == rewritten and result['missing'] == sorted(result['suggest'])
    assert result['suggest'] == {'＜人名［1］．人名．名称＞': '<人名[1].人名.名称>', '\\<人名[1].人名.名称\\>': '<人名[1].人名.名称>'}
    assert project.restore_result('＜人名［1］．人名．名称＞', {'＜人名［1］．人名．名称＞': '<人名[1].人名.名称>'})['text'] == '张三'
    # Original text can itself contain a tag: never cascade replacements.
    assert restore('<人名[1].人名.名称>', {'<人名[1].人名.名称>':'<公司[2].公司.名称>', '<公司[2].公司.名称>':'错误'})['text'] == '<公司[2].公司.名称>'


def test_partial_content_and_api_boundary(tmp_path):
    project = Project(tmp_path, tmp_path / 'private')
    pdf_path = make_files(tmp_path / 'inputs')[2]
    with fitz.open(pdf_path) as pdf:
        pdf.new_page()
        data = pdf.tobytes()
    doc = project.add('部分.pdf', data)
    project.recognize(doc, KnownCandidates()); confirm(project)
    with pytest.raises(ValueError, match='未处理'):
        project.export('original')
    assert project.export('original', acknowledge=True)['partial']
    with TestClient(create_app(project, 'test-launch'), base_url='http://127.0.0.1') as client:
        assert client.get('/api/project').status_code == 401
        assert client.post('/session', headers={'X-Launch-Token':'test-launch'}).status_code == 200
        assert client.post('/session', headers={'X-Launch-Token':'test-launch'}).status_code == 403
        assert client.get('/api/project').status_code == 200
        assert client.post('/api/restore', json={'text':'x'}).status_code == 403
        assert client.get('/api/project', headers={'Origin':'https://example.com'}).status_code == 403
        assert client.post('/api/import-local', headers={'X-Mask':'1'}, json={'name':'../secret.txt'}).status_code == 400


def test_alias_identity_literal_tags_and_stable_history(tmp_path):
    project = Project(tmp_path, tmp_path / 'private')
    text = '😀星河示例科技有限公司（以下简称“星河”）。星河负责交付。原文注记<公司[1].公司.名称>。'
    doc = project.add('简称.txt', text.encode())
    class CompanyOnly:
        def scan(self, text, categories):
            return {'公司':['星河示例科技有限公司']}
    project.recognize(doc, CompanyOnly())
    payload = project.public()
    full = next(c for c in payload['documents'][0]['candidates'] if c['original'] == '星河示例科技有限公司')
    for c in payload['documents'][0]['candidates']:
        if c['original'] == '星河' and c['start'] == full['start']:
            c['decision'] = 'keep'  # The short string inside a full name is an overlapping alternative.
        else:
            c['decision'] = 'mask'; c['entity'] = full['entity']
    project.review(payload)
    before = deepcopy(project.state['mapping'])
    assert '<公司[1].公司.名称>' not in before
    assert set(before.values()) == {'星河示例科技有限公司', '星河'}
    result = project.export('original')
    masked = Path(result['files'][0]['path']).read_bytes().decode()
    assert restore(masked, before)['text'] == text
    payload = project.public()
    payload['documents'][0]['candidates'][0]['decision'] = 'keep'
    project.review(payload)
    assert project.state['mapping'] == before


def test_rotated_pdf_keeps_page_orientation(tmp_path):
    source = make_files(tmp_path / 'inputs')[2]
    with fitz.open(source) as pdf:
        pdf[0].set_rotation(90)
        data = pdf.tobytes()
    project = Project(tmp_path, tmp_path / 'private')
    doc = project.add('旋转.pdf', data)
    project.recognize(doc, KnownCandidates()); confirm(project)
    exported = project.export('original')['files'][0]['path']
    with fitz.open(exported) as pdf:
        assert pdf[0].rotation == 90
        assert '张三' not in pdf[0].get_text()
        assert '<人名[1].人名.名称>' in re.sub(r'\s', '', pdf[0].get_text())


@pytest.mark.skipif(os.environ.get('MASK_REAL_MODEL') != '1', reason='Set MASK_REAL_MODEL=1 for the local GGUF integration test')
def test_real_model_api_roundtrip(tmp_path):
    project = Project(tmp_path, tmp_path / 'private')
    with TestClient(create_app(project, 'real-test'), base_url='http://127.0.0.1') as client:
        client.post('/session', headers={'X-Launch-Token':'real-test'})
        client.headers['X-Mask'] = '1'
        for source in make_files(tmp_path / 'inputs'):
            response = client.post('/api/upload', files={'file':(source.name, source.read_bytes())})
            assert response.status_code == 200, response.text
            d = response.json()['documents'][-1]
            response = client.post('/api/scan/'+d['id'])
            assert response.status_code == 200, response.text
            d = response.json()['documents'][-1]
            assert d['status'] == 'review', d.get('error')
            assert {'张三','13800138000','星河示例科技有限公司'} <= {c['original'] for c in d['candidates']}
        payload = client.get('/api/project').json()
        for d in payload['documents']:
            for c in d['candidates']:
                c['decision'] = 'mask' if c['original'] in {'张三','13800138000','星河示例科技有限公司'} else 'keep'
        response = client.put('/api/review', json=payload)
        assert response.status_code == 200, response.text
        for kind in ['original','markdown']:
            response = client.post('/api/export', json={'kind':kind,'revision':project.state['revision']})
            assert response.status_code == 200, response.text
            for file in response.json()['files']:
                assert client.get('/api/download/'+file['id']).status_code == 200
                if file['name'].endswith('.md'):
                    # The tag guide's example is not a tag, so a pasted-back export restores without missing tags.
                    assert not restore(Path(file['path']).read_text(encoding='utf-8'), project.state['mapping'])['missing']
        response = client.post('/api/restore',json={'text':'<人名[1].人名.名称>电话<电话[1].电话.名称>','save':True})
        assert response.status_code == 200, response.text
        assert response.json()['text'] == '张三电话13800138000'
        # A mapping file from another project restores this text only; the project mapping is unchanged.
        before = deepcopy(project.state['mapping'])
        upload = json.dumps({'mapping': {'<人名[1].人名.名称>': ['李四', '小李']}}, ensure_ascii=False).encode()
        response = client.post('/api/read-mapping', files={'file': ('other.mapping.json', upload)})
        assert response.status_code == 200, response.text
        imported = response.json()['mapping']
        response = client.post('/api/restore', json={'text': '<人名[1].人名.名称>电话<电话[1].电话.名称>', 'mapping': imported})
        assert response.json()['text'] == '李四电话<电话[1].电话.名称>' and response.json()['missing'] == ['<电话[1].电话.名称>']
        assert project.state['mapping'] == before
        for name, data in [('bad.json', b'{"not-a-tag": ["x"]}'), ('bad.json', b'{'), ('map.txt', upload)]:
            assert client.post('/api/read-mapping', files={'file': (name, data)}).status_code == 400


def test_company_spellings_generic_alias_and_unissued_tags(tmp_path):
    project = Project(tmp_path, tmp_path / 'private')
    text = '深圳市星河示例科技有限公司与南山熊猫酒店（以下简称酒店）签约。星河示例科技有限公司付款，深圳南山熊猫酒店收款，酒店提供场地。'
    doc = project.add('写法.txt', text.encode())
    class OneSpelling:
        def scan(self, text, categories):
            return {'公司': ['深圳市星河示例科技有限公司', '南山熊猫酒店']}
    project.recognize(doc, OneSpelling())
    found = {c['original']: c['entity'] for c in doc['candidates']}
    assert set(found) == {'深圳市星河示例科技有限公司', '星河示例科技有限公司', '南山熊猫酒店', '深圳南山熊猫酒店'}
    assert found['深圳市星河示例科技有限公司'] == found['星河示例科技有限公司']
    assert found['南山熊猫酒店'] == found['深圳南山熊猫酒店']
    payload = project.public()
    for c in payload['documents'][0]['candidates']:
        c['decision'] = 'mask'
    project.review(payload)
    trial = set(project.state['mapping'])
    payload = project.public()
    payload['documents'][0]['candidates'][0]['decision'] = 'keep'
    project.review(payload)
    assert len(project.state['mapping']) == len(trial) - 1
    payload = project.public()
    for c in payload['documents'][0]['candidates']:
        c['decision'] = 'mask'
    project.review(payload)
    exported = project.export('markdown')
    payload = project.public()
    payload['documents'][0]['candidates'][0]['decision'] = 'keep'
    project.review(payload)
    assert set(project.state['mapping']) == trial
    # The mapping file must round-trip through the unmodified official loader and deterministic seek.
    from has_text.mapping import load_mapping
    from has_text.commands.seek import restore_without_model
    official = load_mapping(project.export_mapping()['file']['path'])
    masked = Path(exported['files'][0]['path']).read_text(encoding='utf-8')
    assert restore_without_model(masked, official).endswith(text)


def test_pdf_label_needs_room_on_the_line(tmp_path):
    project = Project(tmp_path, tmp_path / 'private')
    with fitz.open() as pdf:
        pdf.new_page().insert_text((60, 70), '联系人：张三，电话：13800138000。', fontname='china-s', fontsize=12)
        data = pdf.tobytes()
    doc = project.add('紧凑.pdf', data)
    class NameAndPhone:
        def scan(self, text, categories):
            return {'人名': ['张三'], '电话': ['13800138000']}
    project.recognize(doc, NameAndPhone()); confirm(project)
    # 张三 is boxed in by the following comma; the phone number has the full stop's width to spare.
    with pytest.raises(ValueError, match='张三.*放不下'):
        project.export('original')
