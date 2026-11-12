"""The mapping vault: the single record of which tag stands for which original text.

mapping   {tag: original}   one tag per spelling, one spelling per tag
entities  {id: {id, type, no, values}}   spellings of one subject share type and number
issued    tags that have left the workbench in an export; never released or reassigned
reserved  tag-shaped text that occurs literally in source documents; never assigned

A tag has the official HaS shape <类型[编号].类型.名称>; further spellings of a subject use 名称2, 名称3.
"""
from __future__ import annotations

import json
import re
import unicodedata
import uuid

from .official import TAG, _tool_seek, normalize_mapping_dict

# Tag-like text an AI may have rewritten: full-width or escaped brackets, other dots, inserted spaces.
# The second form is a tag whose angle brackets were dropped: 类型[编号].类型.名称, the type written twice.
LOOSE = re.compile(r'(?:&lt;|\\?[<＜〈])[^<>＜＞〈〉]{1,80}?(?:&gt;|\\?[>＞〉])'
                   r'|([^\s<>＜＞〈〉\[\]［］【】.．。,，、:：;；()（）]{1,20}?)[\[［【]\d+[\]］】][.．。]\1[.．。]名称\d*')
FOLD = str.maketrans('【】〈〉。', '[]<>.')


def tag_shape(text):
    shape = re.sub(r'[\s\\]', '', unicodedata.normalize('NFKC', text.replace('&lt;', '<').replace('&gt;', '>'))).translate(FOLD)
    return shape if shape.startswith('<') else f'<{shape}>'


def scan(text, mapping, fixes=None):
    """Where tags in text are replaced: ([(start, end, original)], missing, kept, suggest)."""
    fixes = fixes or {}
    spans, missing, kept, suggest = [], set(), set(), {}
    for match in LOOSE.finditer(text):
        tag = target = match.group()
        if tag not in mapping:
            shape = tag_shape(tag)
            if not TAG.fullmatch(shape):
                continue
            # A rewritten tag is never replaced silently; its official shape is only offered as a suggestion.
            if shape in mapping:
                suggest[tag] = shape
            target = fixes.get(tag)
            if target == 'keep':
                kept.add(tag)
            elif target not in mapping:
                missing.add(tag)
        if target in mapping:
            spans.append((match.start(), match.end(), _tool_seek(tag, {tag: [mapping[target]]})))
    return spans, missing, kept, suggest


def restore(text, mapping, fixes=None):
    """Replace tags by their originals in one pass; restored text is never scanned again."""
    spans, missing, kept, suggest = scan(text, mapping, fixes)
    for start, end, original in reversed(spans):
        text = text[:start] + original + text[end:]
    return {'text': text, 'missing': sorted(missing), 'kept': sorted(kept), 'suggest': suggest,
            'partial': bool(missing or kept)}


def imported(mapping):
    """An official {tag: [values]} file as {tag: original}; like official seek, the first value restores."""
    return {tag: values[0] for tag, values in normalize_mapping_dict(mapping).items()}


def replacer(forms):
    """A function that replaces every key of forms by its tag, longest first, and returns (text, tags written)."""
    if not forms:
        return lambda text: (text, [])
    pattern = re.compile('|'.join(re.escape(f) for f in sorted(forms, key=len, reverse=True)))
    def run(text):
        used = []
        def one(match):
            used.append(forms[match.group()])
            return used[-1]
        return pattern.sub(one, text), used
    return run


class Vault:
    def __init__(self, data=None):
        self.data = data or {'entities': {}, 'mapping': {}, 'issued': [], 'reserved': []}

    @property
    def entities(self):
        return self.data['entities']

    @property
    def mapping(self):
        return self.data['mapping']

    def _next_no(self, kind):
        return max((e['no'] for e in self.entities.values() if e['type'] == kind), default=0) + 1

    def create(self, key, kind):
        self.entities[key] = {'id': key, 'type': kind, 'no': self._next_no(kind), 'values': []}
        return key

    def entity(self, kind, value, same_as=None):
        """The subject a spelling belongs to; same_as names another spelling of that subject."""
        for key, e in self.entities.items():
            if e['type'] == kind and value in e['values']:
                return key
        key = self.entity(kind, same_as) if same_as and same_as != value else self.create(uuid.uuid4().hex, kind)
        self.entities[key]['values'].append(value)
        return key

    def variants(self, key):
        e = self.entities[key]
        prefix = f'<{e["type"]}[{e["no"]}].'
        return {original: tag for tag, original in self.mapping.items() if tag.startswith(prefix)}

    def tag(self, key, original):
        e, known = self.entities[key], self.variants(key)
        if original in known:
            return known[original]
        index = 1
        while True:
            tag = f'<{e["type"]}[{e["no"]}].{e["type"]}.名称{"" if index == 1 else index}>'
            if tag not in self.mapping and tag not in self.data['reserved']:
                break
            index += 1
        self.mapping[tag] = original
        return tag

    def release(self, used):
        # A tag that never left the workbench is dropped, so trial decisions do not linger in restore.
        keep = set(used) | set(self.data['issued'])
        self.data['mapping'] = {t: v for t, v in self.mapping.items() if t in keep}

    def issue(self, tags):
        self.data['issued'] = sorted(set(self.data['issued']) | set(tags))

    def reserve(self, literals):
        self.data['reserved'] = sorted(set(self.data['reserved']) | set(literals))

    def official(self):
        return {tag: [original] for tag, original in self.mapping.items()}

    def public_entities(self):
        return {key: {**e, 'variants': self.variants(key)} for key, e in self.entities.items()}

    def forms(self, escaped=False):
        """{original: tag}. escaped adds originals written with JSON \\uXXXX escapes, as in JSON nested inside a string."""
        forms = {}
        for tag, original in self.mapping.items():
            forms.setdefault(original, tag)
            if escaped:
                forms.setdefault(json.dumps(original)[1:-1], tag)
        return forms

    def redact(self, text, escaped=False):
        """Replace every known original by its tag, longest first. Returns (text, replacements)."""
        text, used = replacer(self.forms(escaped))(text)
        return text, len(used)

    def restore(self, text, fixes=None):
        return restore(text, self.mapping, fixes)
