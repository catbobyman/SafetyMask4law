"""A project: the materials of one workspace, their review state, and its mapping vault.

Every mode (workbench, host skill, guard) works through this class; none keeps its own copy of the state.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import time
import uuid
import zipfile

from . import store
from .categories import all_categories, catalog, default_categories, scope_hash
from .detect import locate
from .documents import read_document, read_docx, export_original, masked_sections
from .official import TAG, save_mapping
from .vault import Vault, imported, restore


# Official SKILL.md requires explaining the tag format to the cloud model, or it may rewrite tags and break restore.
TAG_GUIDE = ('本材料已脱敏，敏感信息被替换为 <类型[编号].类型.名称> 形式的代号。\n'
             '- 类型和编号都相同的代号指同一主体，编号不同即不同主体；名称、名称2、名称3 是同一主体在原文中的不同写法。\n'
             '- 回答中提到这些主体时，原样使用对应代号：不要翻译、改写、拆分、合并或省略代号，也不要改动其中的尖括号、方括号和点号。\n'
             '- 不要猜测代号背后的真实信息。')
HANDOFF = 'mask-handoff'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def app_dir():
    return Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local/share')) / 'Mask'


def data_dir(workspace: Path):
    return app_dir() / 'projects' / digest(str(workspace.resolve()).casefold().encode())[:20]


def known_scope(categories):
    known = all_categories()
    if not categories or any(c not in known for c in categories):
        raise ValueError('未知类别')
    return [c for c in known if c in categories]


def initial_scope():
    """The scope a new project starts with: the one the user saved as their default, else the standard categories."""
    file = app_dir() / 'default-scope.json'
    if not file.exists():
        return default_categories()
    saved = json.loads(file.read_text(encoding='utf-8'))
    # The rules may have dropped categories since it was saved; with none left a project would recognize nothing.
    return [c for c in all_categories() if c in saved] or default_categories()


def save_initial_scope(categories):
    # Category names only, no originals or mapping, so this file is not sealed.
    file = app_dir() / 'default-scope.json'
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(known_scope(categories), ensure_ascii=False), encoding='utf-8')


def find(path: Path):
    """The workspace of the project that `path` lies in, or None. Hosts may run in a subfolder."""
    path = path.resolve()
    return next((p for p in [path, *path.parents] if (data_dir(p) / 'vault.mask').exists()), None)


class Project:
    def __init__(self, workspace: Path, data_dir_: Path | None = None):
        self.workspace = workspace.resolve()
        self.directory = data_dir_ or data_dir(self.workspace)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.file, self.vault_file = self.directory / 'project.mask', self.directory / 'vault.mask'
        legacy = self.directory / 'project.json'
        if self.file.exists():
            self.state, self.vault = store.read(self.file), Vault(store.read(self.vault_file))
        elif legacy.exists():
            # Projects saved before sealing kept everything in one readable file.
            old = json.loads(legacy.read_text(encoding='utf-8'))
            self.vault = Vault({'entities': {k: {f: e[f] for f in ('id', 'type', 'no', 'values')} for k, e in old['entities'].items()},
                                'mapping': old['mapping'], 'reserved': old['reserved'],
                                'issued': old.get('issued', sorted(old['mapping']))})
            self.state = {k: old[k] for k in ('revision', 'documents', 'outputs', 'workspace')}
            for i, doc in enumerate(self.state['documents'], 1):
                del doc['rules_hash']
                # The old scope was the 22 categories of the previous rules; rescanning adds the new ones
                # and keeps the decisions already made.
                doc.update(no=i, scope='', status='queued' if doc['status'] == 'review' else doc['status'])
                # Word documents imported before page tracking: read the saved page starts from the snapshot.
                if doc['format'] == 'DOCX' and 'breaks' not in doc['sections'][0]:
                    doc['sections'][0]['breaks'] = read_docx(Path(doc['source']).read_bytes())['sections'][0]['breaks']
            self.state.update(next_no=len(self.state['documents']) + 1, categories=default_categories())
            self.save()
            if store.read(self.file) != self.state or store.read(self.vault_file) != self.vault.data:
                raise RuntimeError('旧项目迁移校验失败，未删除 project.json')
            legacy.unlink()
        else:
            self.state = {'revision': 0, 'documents': [], 'outputs': [], 'workspace': str(self.workspace),
                          'next_no': 1, 'categories': initial_scope()}
            self.vault = Vault()

    @property
    def categories(self):
        return self.state['categories']

    def save(self):
        self.state['revision'] += 1
        # Vault first: after an interrupted save the mapping may hold an unused tag, never miss a used one.
        store.write(self.vault_file, self.vault.data)
        store.write(self.file, self.state)

    def public(self):
        result = deepcopy(self.state)
        result.update(entities=self.vault.public_entities(), mapping=self.vault.mapping,
                      catalog=catalog(), scope=scope_hash(self.categories))
        for doc in result['documents']:
            doc.pop('source', None)
            doc.pop('origin', None)
            for section in doc['sections']:
                section.pop('sources', None)
        return result

    def status(self):
        """What a host may know: counts and handed-off file paths, never names or originals."""
        docs = self.state['documents']
        pending = sum(c['decision'] == 'pending' for d in docs for c in d['candidates'])
        # Handed off means the current decisions of every material are in the workspace folder the host reads.
        files = [self.workspace / HANDOFF / self.masked_name(d, '.md') for d in docs]
        handed = bool(docs) and all(d['handed'] for d in docs) and all(f.exists() for f in files)
        return {'materials': len(docs), 'unrecognized': sum(d['status'] != 'review' for d in docs), 'pending': pending,
                'handed_off': handed, 'files': [str(f) for f in files] if handed else []}

    @staticmethod
    def masked_name(doc, ext):
        return f'材料{doc["no"]}{".部分处理" if doc["issues"] else ".脱敏"}{ext}'

    def check_revision(self, revision):
        if revision != self.state['revision']:
            raise ValueError('项目已在其他页面更新，请刷新后继续')

    def document(self, identity):
        doc = next((d for d in self.state['documents'] if d['id'] == identity), None)
        if not doc:
            raise ValueError('材料不存在')
        return doc

    def set_scope(self, categories):
        self.state['categories'] = known_scope(categories)
        for doc in self.state['documents']:
            if doc['scope'] != scope_hash(self.categories) and doc['status'] == 'review':
                doc.update(status='queued', handed=False)
        self.save()

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
            if literals & self.vault.mapping.keys():
                raise ValueError('原文包含本项目已分配的代号；请在另一项目处理，避免还原歧义')
        except Exception:
            source.unlink()
            raise
        # A material keeps its number for good; a removed number is not reused, so 材料N always names one file.
        doc = {'id': identity, 'no': self.state['next_no'], 'name': Path(name).name, 'format': ext[1:].upper(),
               'source': str(source), 'origin': str(origin) if origin else None, 'sha256': digest(data), 'scope': '',
               'status': 'queued', 'error': '', 'candidates': [], 'handed': False, **extracted}
        self.state['next_no'] += 1
        self.vault.reserve(literals)
        self.state['documents'].append(doc)
        self.save()
        return doc

    def remove(self, identity):
        doc = self.document(identity)
        self.state['documents'].remove(doc)
        Path(doc['source']).unlink(missing_ok=True)
        # Historical mappings remain valid for AI results produced before removal.
        self.save()

    def recognize(self, doc, recognizer):
        text = '\n\n'.join(p for s in doc['sections'] for p in s['paragraphs'])
        # One request per category group: a single request with every category loses values that a
        # short list finds (measured on sample pleadings), at about the same total time.
        found, seen = {}, set()
        for group in catalog():
            wanted = [c for c in group['categories'] if c in self.categories]
            if not wanted:
                continue
            result = recognizer.scan(text, wanted)
            if any(k not in self.categories for k in result):
                raise ValueError('HaS 返回未知实体类别，未将结果当作成功')
            for kind, values in result.items():
                # A value two groups both claim keeps its first category; one span is one candidate.
                found.setdefault(kind, []).extend(v for v in values if v not in seen)
                seen.update(values)
        spans, unmatched = locate(doc['sections'], found, self.categories)
        if unmatched:
            raise ValueError('候选无法对齐原文（可能跨段落）：' + '、'.join(unmatched))
        # A rescan keeps what the user already decided for the same span, and everything added by hand.
        previous = {(c['s'], c['p'], c['start'], c['end'], c['type']): c for c in doc['candidates']}
        candidates = []
        for span in spans:
            key = (span['s'], span['p'], span['start'], span['end'], span['type'])
            if key in previous:
                candidates.append(previous.pop(key))
                continue
            c = {'id': uuid.uuid4().hex, **{k: span[k] for k in ('s', 'p', 'start', 'end', 'original', 'type', 'decision', 'reason')},
                 'entity': self.vault.entity(span['type'], span['original'], span['same_as']), 'manual': False, 'bad': False}
            if span['alias_of']:
                c['suggest'] = self.vault.entity('公司', span['alias_of'])
            candidates.append(c)
        candidates += [c for c in previous.values() if c.get('manual')]
        doc.update(candidates=sorted(candidates, key=lambda c: (c['s'], c['p'], c['start'], -c['end'])),
                   status='review', error='', handed=False, scope=scope_hash(self.categories))

    def review(self, payload):
        self.check_revision(payload['revision'])
        # Validate the entire batch before changing persisted state.
        documents, vault = deepcopy(self.state['documents']), Vault(deepcopy(self.vault.data))
        if {d['id'] for d in payload['documents']} != {d['id'] for d in documents}:
            raise ValueError('材料列表已变化，请刷新页面')
        for key, entity in payload['entities'].items():
            if key in vault.entities:
                if vault.entities[key]['type'] != entity['type']:
                    raise ValueError('已有主体不能直接改变类别，请选择新主体')
            else:
                if entity['type'] not in all_categories():
                    raise ValueError('未知类别')
                vault.create(key, entity['type'])
        incoming = {d['id']: d for d in payload['documents']}
        for doc in documents:
            candidates = incoming[doc['id']]['candidates']
            # Only a candidate the user added may be removed (eraser, undo); model candidates need a decision.
            kept = {c['id'] for c in candidates}
            if any(c['id'] not in kept and not c.get('manual') for c in doc['candidates']):
                raise ValueError('识别出的候选必须明确保留或脱敏，不能直接丢弃')
            if len(kept) != len(candidates):
                raise ValueError('候选编号重复')
            for c in candidates:
                if c['decision'] not in {'mask', 'keep', 'pending'}:
                    raise ValueError('未知处理决定')
                try:
                    text = doc['sections'][c['s']]['paragraphs'][c['p']]
                    entity = vault.entities[c['entity']]
                except (IndexError, KeyError, TypeError) as exc:
                    raise ValueError('候选位置或主体无效') from exc
                if not (0 <= c['s'] and 0 <= c['p'] and 0 <= c['start'] < c['end'] <= len(text)) or text[c['start']:c['end']] != c['original']:
                    raise ValueError('候选范围与原文不一致')
                c['type'] = entity['type']
                if c['original'] not in entity['values']:
                    entity['values'].append(c['original'])
                if c['decision'] == 'mask':
                    c['tag'] = vault.tag(c['entity'], c['original'])
                else:
                    c.pop('tag', None)
            if candidates != doc['candidates']:
                doc['handed'] = False
            doc['candidates'] = candidates
        vault.release(c['tag'] for d in documents for c in d['candidates'] if c['decision'] == 'mask')
        self.state['documents'], self.vault = documents, vault
        self.save()

    def ready(self):
        if not self.state['documents']:
            raise ValueError('尚未导入材料')
        for doc in self.state['documents']:
            if doc['status'] != 'review' or any(c['decision'] == 'pending' for c in doc['candidates']):
                raise ValueError(f'{doc["name"]}尚未识别完成或仍有待确认项')
            if doc['scope'] != scope_hash(self.categories):
                raise ValueError('脱敏范围已变化，请重新识别材料')
            for path in [doc['source'], doc.get('origin')]:
                if path and (not Path(path).exists() or digest(Path(path).read_bytes()) != doc['sha256']):
                    raise ValueError(f'{doc["name"]}原文件已变化，请重新导入')
            active = sorted((c for c in doc['candidates'] if c['decision'] == 'mask'), key=lambda c: (c['s'], c['p'], c['start']))
            for a, b in zip(active, active[1:]):
                if (a['s'], a['p']) == (b['s'], b['p']) and a['end'] > b['start']:
                    raise ValueError(f'{doc["name"]}存在重叠脱敏范围：{a["original"]} / {b["original"]}，请保留其中一个或修正范围')

    def export(self, kind, acknowledge=False, handoff=False):
        """Write masked files to the private output folder; handoff also places the Markdown in the workspace for the host."""
        self.ready()
        if kind not in {'original', 'markdown'}:
            raise ValueError('未知导出格式')
        documents = self.state['documents']
        partial = any(d['issues'] for d in documents)
        if partial and not acknowledge:
            raise ValueError('存在未处理内容，请阅读汇总并确认按部分处理导出')
        batch_id = uuid.uuid4().hex
        folder = self.directory / 'outputs' / batch_id
        folder.mkdir(parents=True)
        outputs = []
        for doc in documents:
            edits = [c for c in doc['candidates'] if c['decision'] == 'mask']
            ext = '.' + doc['format'].lower() if kind == 'original' else '.md'
            target = folder / self.masked_name(doc, ext)
            if kind == 'original':
                export_original(Path(doc['source']), doc, edits, target)
            else:
                sections = masked_sections(doc, edits)
                body = '\n\n'.join('## '+s['title']+'\n\n'+'\n\n'.join(s['paragraphs']) for s in sections)
                guide = '\n'.join('> ' + line for line in TAG_GUIDE.split('\n'))
                target.write_text(f'# 材料 {doc["no"]}\n\n{guide}\n\n'+body, encoding='utf-8', newline='')
            outputs.append({'id': uuid.uuid4().hex, 'name': target.name, 'path': str(target)})
        notes = '部分处理：' + '；'.join(f'材料{d["no"]}：' + '；'.join(d['issues']) for d in documents if d['issues']) if partial else ''
        if handoff:
            # The host reads this folder. It holds masked Markdown only; stale files of removed materials are cleared.
            shared = self.workspace / HANDOFF
            shared.mkdir(exist_ok=True)
            for old in shared.glob('材料*.md'):
                old.unlink()
            for item in outputs:
                (shared / item['name']).write_bytes(Path(item['path']).read_bytes())
            (shared / '使用说明.txt').write_text(TAG_GUIDE + '\n' + notes, encoding='utf-8')
        archive = folder / ('脱敏材料.部分处理.zip' if partial else '脱敏材料.zip')
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
            for item in outputs:
                z.write(item['path'], item['name'])
            z.writestr('使用说明.txt', TAG_GUIDE + '\n' + notes)
        outputs.append({'id': batch_id, 'name': archive.name, 'path': str(archive)})
        self.state['outputs'].extend(outputs)
        self.vault.issue(c['tag'] for d in documents for c in d['candidates'] if c['decision'] == 'mask')
        if kind == 'markdown':
            for doc in documents:
                doc['handed'] = True
        self.save()
        return {'files': outputs, 'partial': partial, 'revision': self.state['revision'],
                'handoff': str(self.workspace / HANDOFF) if handoff else None}

    def export_mapping(self):
        if not self.vault.mapping:
            raise ValueError('尚未分配代号')
        folder = self.directory / 'outputs' / uuid.uuid4().hex
        folder.mkdir(parents=True)
        target = folder / f'{self.workspace.name}.mapping.json'
        # Official file layout {tag: [original]}, readable by `has.sh text restore --mapping`.
        save_mapping(self.vault.official(), str(target))
        item = {'id': uuid.uuid4().hex, 'name': target.name, 'path': str(target)}
        self.state['outputs'].append(item)
        self.save()
        return {'file': item, 'revision': self.state['revision']}

    def restore_result(self, text, fixes, save=False, mapping=None):
        result = restore(text, self.vault.mapping if mapping is None else imported(mapping), fixes)
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
