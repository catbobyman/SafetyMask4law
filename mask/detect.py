"""Locate candidates in source paragraphs: model values, explicit abbreviations, and fixed-shape patterns.

Every result is a candidate for review, never a final decision.
"""
from __future__ import annotations

import re

from .categories import pattern_hits

PLACE = re.compile(r'(?:[一-龥]{2,3}(?:省|自治区))?[一-龥]{2,3}市')
ROLES = {'甲方', '乙方', '双方', '学员', '教练', '联系人', '法定代表人', '开户银行'}
PROTECTED = re.compile(r'人民法院|人民检察院|公安局|公安分局|派出所|市场监督管理局|人民政府|税务局|司法局')


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
    if value.strip(' ：:') in ROLES or re.search(r'_{2,}|X{3,}|【姓名】|请填写', value):
        return 'keep', '规则：角色、字段名或占位符，保留当前候选'
    if kind in {'公司', '机构'} and PROTECTED.search(value):
        return 'keep', '规则：法院或政府机关，保留当前候选'
    if re.search(r'[（(](学员|联系人|盖|法定代表人)|[，,]邮编', value):
        return 'pending', '候选夹带角色或后续字段；请保留此候选，再划选正确范围补录，或修正范围'
    # Ordinary candidates carry no reason; the review UI shows only specific guidance.
    return 'pending', ''


def locate(sections, found, categories):
    """Spans for model output `found` ({category: [values]}) in `sections`, plus pattern hits the model missed.

    Returns (spans, unmatched). A span names the model spelling it came from (`same_as`) and, for a defined
    abbreviation, the full name it may be merged into (`alias_of`).
    """
    text = '\n\n'.join(p for s in sections for p in s['paragraphs'])
    found = {kind: list(values) for kind, values in found.items()}
    # Explicit abbreviation definitions add candidates, never final decisions.
    aliases = {}
    for full in found.get('公司', []):
        for match in re.finditer(re.escape(full) + r'[（(]以下简称[“"「]?([^”"」）)]+)[”"」]?[）)]', text):
            alias = match.group(1).strip()
            # A bare type word such as 酒店 is ordinary wording, not an identifying abbreviation.
            if alias not in {'甲方', '乙方', '双方', '公司'} and not (full.endswith(alias) and len(alias) <= 2):
                aliases[alias] = full
    places = place_forms(found.get('公司', []) + found.get('地址', []))
    if aliases:
        found.setdefault('公司', []).extend(a for a in aliases if a not in found['公司'])
    spans, unmatched, searched = [], [], set()
    for kind, values in found.items():
        for value in values:
            # HaS returns one spelling per company; search its core so 深圳市X, 深圳X and X are all found.
            needle = company_core(value, places) if kind == '公司' else value
            if (kind, needle) in searched:
                continue
            searched.add((kind, needle))
            hits = 0
            for si, section in enumerate(sections):
                for pi, paragraph in enumerate(section['paragraphs']):
                    for start, end in aligned_occurrences(paragraph, needle):
                        hits += 1
                        if kind == '公司':
                            start -= len(next((p for p in places if paragraph[:start].endswith(p)), ''))
                        raw = paragraph[start:end]
                        decision, reason = rule_hint(kind, raw)
                        if raw != value and not reason:
                            reason = f'「{value}」的另一种写法，按同一主体待确认'
                        alias_of = aliases.get(raw)
                        if alias_of:
                            reason = '原文有明确简称定义，可确认并入全称主体'
                        spans.append({'s': si, 'p': pi, 'start': start, 'end': end, 'original': raw, 'type': kind,
                                      'same_as': value, 'alias_of': alias_of, 'decision': decision, 'reason': reason,
                                      'short': value in aliases})
            if not hits:
                unmatched.append(value)
    # An abbreviation inside a longer candidate is part of that name, not a separate occurrence.
    spans = [c for c in spans if not (c['short'] and any(
        o is not c and (o['s'], o['p']) == (c['s'], c['p']) and o['start'] <= c['start'] and c['end'] <= o['end']
        and (o['start'], o['end']) != (c['start'], c['end']) for o in spans))]
    for si, section in enumerate(sections):
        for pi, paragraph in enumerate(section['paragraphs']):
            taken = [(c['start'], c['end']) for c in spans if (c['s'], c['p']) == (si, pi)]
            for start, end, kind in pattern_hits(paragraph, categories):
                if not any(start < e and s < end for s, e in taken):
                    raw = paragraph[start:end]
                    spans.append({'s': si, 'p': pi, 'start': start, 'end': end, 'original': raw, 'type': kind,
                                  'same_as': raw, 'alias_of': None, 'decision': 'pending', 'short': False,
                                  'reason': f'规则补充：模型未识别，形状符合{kind}'})
    unique = {(c['s'], c['p'], c['start'], c['end'], c['type']): c for c in spans}
    return sorted(unique.values(), key=lambda c: (c['s'], c['p'], c['start'], -c['end'])), unmatched
