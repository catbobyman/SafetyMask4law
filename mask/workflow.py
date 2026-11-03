"""Project persistence, candidate review, mapping, and validated exports."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid
import zipfile

from .documents import read_document, read_docx, export_original, masked_sections
from .official import ROOT, TAG, restore, save_mapping, save_mapping, save_mapping


def digest(data):
    return hashlib.sha256(data).hexdigest()


def rules():
    raw = (ROOT / '规则.md').read_bytes()
    block = re.search(r'```json\s*(.*?)```', raw.decode('utf-8'), re.S)
    return json.loads(block.group(1))['categories'], digest(raw)


def aligned_occurrences(text, value):
    # HaS can remove PDF extraction whitespace. Preserve exact source spans for editing.
    compact, offsets = [], []
    for i, c in enumerate(text):
        if not c.isspace():
            compact.append(c); offsets.append(i)
    needle = ''.join(value.split())
    if not needle:
        return []
    haystack, start, result = ''.join(compact), 0, []
    while (start := haystack.find(needle, start)) >= 0:
        result.append((offsets[start], offsets[start+len(needle)-1]+1))
        start += 1
    return result


PLACE = re.compile(r'(?:[一-龥]{2,3}(?:省|自治区))?[一-龥]{2,3}市')


def place_forms(values):
    # City prefixes used in this document, with the shortened forms contracts commonly write.
    forms = set()
    for value in values:
        if match := PLACE.match(value):
            city = re.sub(r'^.*?(?:省|自治区)', '', match.group())
            forms |= {match.group(), city, city[:-1]}
    return sorted(forms, key=len, reverse=True)


def company_core(value, places):
    prefix = next((p for p in places if value.startswith(p) and len(value) - len(p) >= 4), '')
    return value[len(prefix):]


def rule_hint(kind, value):
    roles = {'甲方', '乙方', '双方', '学员', '教练', '联系人', '法定代表人', '开户银行'}
    if value.strip(' ：:') in roles or re.search(r'_{2,}|X{3,}|【姓名】|请填写', value):
        return 'keep', '规则：角色、字段名或占位符，保留当前候选'
    if kind == '公司' and re.search(r'人民法院|人民检察院|公安局|市场监督管理局|人民政府', value):
        return 'keep', '规则：法院或政府机关，保留当前候选'
    if re.search(r'[（(](学员|联系人|盖|法定代表人)|[，,]邮编', value):
        return 'pending', '候选夹带角色或后续字段；请保留此候选，再划选正确范围补录，或修正范围'
    # Ordinary candidates carry no reason; the review UI shows only specific guidance.
    return 'pending', ''


class Project:
    def __init__(self, workspace: Path, data_dir: Path | None = None):
        self.workspace = workspace.resolve()
        self.categories, self.rules_hash = rules()
        base = Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local/share')) / 'Mask/projects'
        self.directory = data_dir or base / digest(str(self.workspace).casefold().encode())[:20]
        self.directory.mkdir(parents=True, exist_ok=True)
        self.file = self.directory / 'project.json'
        self.state = json.loads(self.file.read_text(encoding='utf-8')) if self.file.exists() else {
            'revision': 0, 'documents': [], 'entities': {}, 'mapping': {}, 'reserved': [], 'outputs': [],
            'workspace': str(self.workspace)}
        # Projects saved before export tracking: every existing tag may already be in an AI result.
        self.state.setdefault('issued', sorted(self.state['mapping']))
        # Word documents imported before page tracking: read the saved page starts from the snapshot.
        for doc in self.state['documents']:
            if doc['format'] == 'DOCX' and 'breaks' not in doc['sections'][0]:
                doc['sections'][0]['breaks'] = read_docx(Path(doc['source']).read_bytes())['sections'][0]['breaks']

    def save(self):
        self.state['revision'] += 1
        temp = self.file.with_suffix('.tmp')
        temp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(self.file)

    def public(self):
        result = deepcopy(self.state)
        result['categories'] = self.categories
        for doc in result['documents']:
            doc.pop('source', None)
            doc.pop('origin', None)
            for section in doc['sections']:
                section.pop('sources', None)
        return result

    def check_revision(self, revision):
        if revision != self.state['revision']:
            raise ValueError('项目已在其他页面更新，请刷新后继续')

    def entity(self, kind, value, same_as=None):
        for key, e in self.state['entities'].items():
            if e['type'] == kind and value in e['values']:
                return key
        if same_as and same_as != value:
            key = self.entity(kind, same_as)
            self.state['entities'][key]['values'].append(value)
            return key
        key = uuid.uuid4().hex
        no = max((e['no'] for e in self.state['entities'].values() if e['type'] == kind), default=0)+1
        self.state['entities'][key] = {'id': key, 'type': kind, 'no': no, 'values': [value], 'variants': {}}
        return key

    def add(self, name, data, origin=None):
        ext = Path(name).suffix.lower()
        if ext not in {'.txt', '.docx', '.pdf'}:
            raise ValueError('支持 TXT、DOCX、PDF；.doc 请先另存为 .docx')
        identity = uuid.uuid4().hex
        source = self.directory / (identity + ext)
        source.write_bytes(data)
        try:
            extracted = read_document(source)
            literals = {m.group() for m in TAG.finditer('\n'.join(p for s in extracted['sections'] for p in s['paragraphs']))}
            if literals & self.state['mapping'].keys():
                raise ValueError('原文包含本项目已分配的代号；请在另一项目处理，避免还原歧义')
        except Exception:
            source.unlink()
            raise
        doc = {'id': identity, 'name': Path(name).name, 'format': ext[1:].upper(), 'source': str(source),
               'origin': str(origin) if origin else None, 'sha256': digest(data), 'rules_hash': self.rules_hash,
               'status': 'queued', 'error': '', 'candidates': [], 'handed': False, **extracted}
        self.state['reserved'] = sorted(set(self.state['reserved']) | literals)
        self.state['documents'].append(doc)
        self.save()
        return doc

    def recognize(self, doc, recognizer):
        text = '\n\n'.join(p for s in doc['sections'] for p in s['paragraphs'])
        found = recognizer.scan(text, self.categories)
        if any(k not in self.categories for k in found):
            raise ValueError('HaS 返回未知实体类别，未将结果当作成功')
        # Explicit abbreviation definitions add candidates, never final decisions.
        aliases = []
        for full in found.get('公司', []):
            for match in re.finditer(re.escape(full) + r'[（(]以下简称[“"「]?([^”"」）)]+)[”"」]?[）)]', text):
                alias = match.group(1).strip()
                # A bare type word such as 酒店 is ordinary wording, not an identifying abbreviation.
                if alias not in {'甲方', '乙方', '双方', '公司'} and not (full.endswith(alias) and len(alias) <= 2):
                    aliases.append((full, alias))
        places = place_forms(found.get('公司', []) + found.get('地址', []))
        if aliases:
            found.setdefault('公司', []).extend(a for _, a in aliases if a not in found['公司'])
        candidates, unmatched, searched = [], [], set()
        for kind, values in found.items():
            for value in values:
                # HaS returns one spelling per company; search its core so 深圳市X, 深圳X and X are all found.
                needle = company_core(value, places) if kind == '公司' else value
                if (kind, needle) in searched:
                    continue
                searched.add((kind, needle))
                hits = 0
                for si, section in enumerate(doc['sections']):
                    for pi, paragraph in enumerate(section['paragraphs']):
                        for start, end in aligned_occurrences(paragraph, needle):
                            hits += 1
                            if kind == '公司':
                                start -= len(next((p for p in places if paragraph[:start].endswith(p)), ''))
                            raw = paragraph[start:end]
                            decision, reason = rule_hint(kind, raw)
                            if raw != value and not reason:
                                reason = f'「{value}」的另一种写法，按同一主体待确认'
                            candidates.append({'id': uuid.uuid4().hex, 's': si, 'p': pi, 'start': start, 'end': end,
                                'original': raw, 'type': kind, 'entity': self.entity(kind, raw, value), 'decision': decision,
                                'reason': reason, 'manual': False, 'bad': False})
                if not hits:
                    unmatched.append(value)
        if unmatched:
            raise ValueError('候选无法对齐原文（可能跨段落）：' + '、'.join(unmatched))
        # An abbreviation inside its own full name is not a separate occurrence.
        short = {a for _, a in aliases}
        outer = [c for c in candidates if c['original'] not in short]
        candidates = [c for c in candidates if c['original'] not in short or not any(
            o['s'] == c['s'] and o['p'] == c['p'] and o['start'] <= c['start'] and c['end'] <= o['end'] for o in outer)]
        for full, alias in aliases:
            full_id = self.entity('公司', full)
            for c in candidates:
                if c['original'] == alias:
                    c['suggest'] = full_id
                    c['reason'] = '原文有明确简称定义，可确认并入全称主体'
        unique = {(c['s'], c['p'], c['start'], c['end'], c['type']): c for c in candidates}
        doc.update(candidates=sorted(unique.values(), key=lambda c: (c['s'], c['p'], c['start'], -c['end'])),
                   status='review', error='', handed=False, rules_hash=self.rules_hash)

    def review(self, payload):
        self.check_revision(payload['revision'])
        # Validate the entire batch before changing persisted state.
        proposed = deepcopy(self.state)
        if {d['id'] for d in payload['documents']} != {d['id'] for d in proposed['documents']}:
            raise ValueError('材料列表已变化，请刷新页面')
        for key, entity in payload['entities'].items():
            if key in proposed['entities']:
                if proposed['entities'][key]['type'] != entity['type']:
                    raise ValueError('已有主体不能直接改变类别，请选择新主体')
            else:
                if entity['type'] not in self.categories:
                    raise ValueError('未知类别')
                no = max((e['no'] for e in proposed['entities'].values() if e['type'] == entity['type']), default=0)+1
                proposed['entities'][key] = {'id': key, 'type': entity['type'], 'no': no, 'values': [], 'variants': {}}
        incoming = {d['id']: d for d in payload['documents']}
        for doc in proposed['documents']:
            candidates = incoming[doc['id']]['candidates']
            # Only a candidate the user added may be removed (eraser, undo); model candidates need a decision.
            kept = {c['id'] for c in candidates}
            if any(c['id'] not in kept and not c.get('manual') for c in doc['candidates']):
                raise ValueError('识别出的候选必须明确保留或脱敏，不能直接丢弃')
            if len({c['id'] for c in candidates}) != len(candidates):
                raise ValueError('候选编号重复')
            for c in candidates:
                if c['decision'] not in {'mask', 'keep', 'pending'}:
                    raise ValueError('未知处理决定')
                try:
                    text = doc['sections'][c['s']]['paragraphs'][c['p']]
                    entity = proposed['entities'][c['entity']]
                except (IndexError, KeyError, TypeError) as exc:
                    raise ValueError('候选位置或主体无效') from exc
                if not (0 <= c['s'] and 0 <= c['p'] and 0 <= c['start'] < c['end'] <= len(text)) or text[c['start']:c['end']] != c['original']:
                    raise ValueError('候选范围与原文不一致')
                c['type'] = entity['type']
                if c['original'] not in entity['values']:
                    entity['values'].append(c['original'])
                if c['decision'] == 'mask':
                    if c['original'] not in entity['variants']:
                        index = 1
                        while True:
                            # Official HaS tag shape <类型[编号].分类.属性>; other spellings of one subject count up 名称2, 名称3.
                            tag = f'<{entity["type"]}[{entity["no"]}].{entity["type"]}.名称{"" if index == 1 else index}>'
                            if tag not in proposed['reserved'] and tag not in proposed['mapping']:
                                break
                            index += 1
                        entity['variants'][c['original']] = tag
                        proposed['mapping'][tag] = c['original']
                    c['tag'] = entity['variants'][c['original']]
                else:
                    c.pop('tag', None)
            if candidates != doc['candidates']:
                doc['handed'] = False
            doc['candidates'] = candidates
        # A tag that never left the workbench is released, so trial decisions do not linger in restore.
        used = {c['tag'] for d in proposed['documents'] for c in d['candidates'] if c['decision'] == 'mask'}
        proposed['mapping'] = {t: v for t, v in proposed['mapping'].items() if t in used or t in proposed['issued']}
        for entity in proposed['entities'].values():
            entity['variants'] = {v: t for v, t in entity['variants'].items() if t in proposed['mapping']}
        self.state = proposed
        self.save()

    def ready(self):
        if not self.state['documents']:
            raise ValueError('尚未导入材料')
        _, current_rules = rules()
        for doc in self.state['documents']:
            if doc['status'] != 'review' or any(c['decision'] == 'pending' for c in doc['candidates']):
                raise ValueError(f'{doc["name"]}尚未识别完成或仍有待确认项')
            if current_rules != doc['rules_hash']:
                raise ValueError('业务规则已变化，请重新识别材料')
            for path in [doc['source'], doc.get('origin')]:
                if path and (not Path(path).exists() or digest(Path(path).read_bytes()) != doc['sha256']):
                    raise ValueError(f'{doc["name"]}原文件已变化，请重新导入')
            active = sorted((c for c in doc['candidates'] if c['decision'] == 'mask'), key=lambda c: (c['s'], c['p'], c['start']))
            for a, b in zip(active, active[1:]):
                if (a['s'], a['p']) == (b['s'], b['p']) and a['end'] > b['start']:
                    raise ValueError(f'{doc["name"]}存在重叠脱敏范围：{a["original"]} / {b["original"]}，请保留其中一个或修正范围')

    def export(self, kind, acknowledge=False):
        self.ready()
        if kind not in {'original', 'markdown'}:
            raise ValueError('未知导出格式')
        partial = any(d['issues'] for d in self.state['documents'])
        if partial and not acknowledge:
            raise ValueError('存在未处理内容，请阅读汇总并确认按部分处理导出')
        batch_id = uuid.uuid4().hex
        folder = self.directory / 'outputs' / batch_id
        folder.mkdir(parents=True)
        outputs = []
        for i, doc in enumerate(self.state['documents'], 1):
            edits = [c for c in doc['candidates'] if c['decision'] == 'mask']
            ext = '.' + doc['format'].lower() if kind == 'original' else '.md'
            target = folder / f'材料{i}{".部分处理" if doc["issues"] else ".脱敏"}{ext}'
            if kind == 'original':
                export_original(Path(doc['source']), doc, edits, target)
            else:
                sections = masked_sections(doc, edits)
                body = '\n\n'.join('## '+s['title']+'\n\n'+'\n\n'.join(s['paragraphs']) for s in sections)
                target.write_text(f'# 材料 {i}\n\n'+body, encoding='utf-8', newline='')
            outputs.append({'id': uuid.uuid4().hex, 'name': target.name, 'path': str(target)})
        archive = folder / ('脱敏材料.部分处理.zip' if partial else '脱敏材料.zip')
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
            for item in outputs:
                z.write(item['path'], item['name'])
            z.writestr('使用说明.txt', '代号格式为 <类别[编号].类别.名称>，同主体不同写法使用 名称2 等后缀。分析时请原样保留代号，不猜测真名。\n'
                       + ('部分处理：' + '；'.join(f'材料{i}：'+ '；'.join(d['issues']) for i, d in enumerate(self.state['documents'], 1) if d['issues']) if partial else ''))
        outputs.append({'id': batch_id, 'name': archive.name, 'path': str(archive)})
        self.state['outputs'].extend(outputs)
        self.state['issued'] = sorted(set(self.state['issued']) | {
            c['tag'] for d in self.state['documents'] for c in d['candidates'] if c['decision'] == 'mask'})
        if kind == 'markdown':
            for doc in self.state['documents']:
                doc['handed'] = True
        self.save()
        return {'files': outputs, 'partial': partial, 'revision': self.state['revision']}

    def export_mapping(self):
        if not self.state['mapping']:
            raise ValueError('尚未分配代号')
        folder = self.directory / 'outputs' / uuid.uuid4().hex
        folder.mkdir(parents=True)
        target = folder / f'{self.workspace.name}.mapping.json'
        # Official file layout {tag: [original]}, readable by `has.sh text restore --mapping`.
        save_mapping({tag: [value] for tag, value in self.state['mapping'].items()}, str(target))
        item = {'id': uuid.uuid4().hex, 'name': target.name, 'path': str(target)}
        self.state['outputs'].append(item)
        self.save()
        return {'file': item, 'revision': self.state['revision']}

    def export_mapping(self):
        if not self.state['mapping']:
            raise ValueError('尚未分配代号')
        folder = self.directory / 'outputs' / uuid.uuid4().hex
        folder.mkdir(parents=True)
        target = folder / f'{self.workspace.name}.mapping.json'
        # Official file layout {tag: [original]}, readable by `has.sh text restore --mapping`.
        save_mapping({tag: [value] for tag, value in self.state['mapping'].items()}, str(target))
        item = {'id': uuid.uuid4().hex, 'name': target.name, 'path': str(target)}
        self.state['outputs'].append(item)
        self.save()
        return {'file': item, 'revision': self.state['revision']}

    def export_mapping(self):
        if not self.state['mapping']:
            raise ValueError('尚未分配代号')
        folder = self.directory / 'outputs' / uuid.uuid4().hex
        folder.mkdir(parents=True)
        target = folder / f'{self.workspace.name}.mapping.json'
        # Official file layout {tag: [original]}, readable by `has.sh text restore --mapping`.
        save_mapping({tag: [value] for tag, value in self.state['mapping'].items()}, str(target))
        item = {'id': uuid.uuid4().hex, 'name': target.name, 'path': str(target)}
        self.state['outputs'].append(item)
        self.save()
        return {'file': item, 'revision': self.state['revision']}

    def restore_result(self, text, fixes, save=False):
        result = restore(text, self.state['mapping'], fixes)
        if save:
            if result['missing']:
                raise ValueError('存在未处理的缺失代号，请先选择对应映射或保留代号')
            identity = uuid.uuid4().hex
            folder = self.directory / 'restored'
            folder.mkdir(exist_ok=True)
            stamp = time.strftime('%Y%m%d-%H%M%S')
            target = folder / f'还原结果-{stamp}-{identity[:4]}{".部分还原" if result["partial"] else ".已还原"}.md'
            target.write_bytes(result['text'].encode('utf-8'))
            item = {'id': identity, 'name': target.name, 'path': str(target)}
            self.state['outputs'].append(item)
            self.save()
            result['file'] = item
        return result
