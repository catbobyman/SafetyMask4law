"""Category catalog from 规则.md, plus deterministic patterns for values with a fixed shape."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def catalog():
    """Category groups as written in 规则.md; groups marked optional are off until the user turns them on."""
    block = re.search(r'```json\s*(.*?)```', (ROOT / '规则.md').read_text(encoding='utf-8'), re.S)
    return json.loads(block.group(1))['groups']


def all_categories():
    return [c for g in catalog() for c in g['categories']]


def default_categories():
    return [c for g in catalog() if not g.get('optional') for c in g['categories']]


def scope_hash(categories):
    # Recognition depends on the enabled categories; a document scanned under another scope is rescanned.
    return hashlib.sha256('\n'.join(sorted(categories)).encode()).hexdigest()


def valid_id(value):
    if not (1 <= int(value[10:12]) <= 12 and 1 <= int(value[12:14]) <= 31 and 1900 <= int(value[6:10]) <= 2100):
        return False
    total = sum(int(d) * w for d, w in zip(value[:17], [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]))
    return '10X98765432'[total % 11] == value[17].upper()


USCC = '0123456789ABCDEFGHJKLMNPQRTUWXY'


def valid_uscc(value):
    total = sum(USCC.index(c) * w for c, w in zip(value[:17], [1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28]))
    return USCC[(31 - total % 31) % 31] == value[17]


def valid_luhn(value):
    digits = [int(d) for d in reversed(value)]
    return (sum(digits[0::2]) + sum(sum(divmod(d * 2, 10)) for d in digits[1::2])) % 10 == 0


def valid_ip(value):
    return all(int(part) <= 255 for part in value.split('.'))


PROVINCES = '京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼'
# Order matters: an earlier category claims a span first (a valid ID number is not also offered as a bank account).
PATTERNS = [
    ('身份证', re.compile(r'(?<!\d)\d{17}[\dXx](?!\d)'), valid_id),
    ('信用代码', re.compile(r'(?<![0-9A-Za-z])[0-9A-HJ-NPQRTUWXY]{18}(?![0-9A-Za-z])'), valid_uscc),
    ('银行账号', re.compile(r'(?<!\d)\d{16,19}(?!\d)'), valid_luhn),
    ('电话', re.compile(r'(?<![\d.])1[3-9]\d{9}(?![\d.])'), None),
    ('邮箱', re.compile(r'(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+'), None),
    ('IP', re.compile(r'(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?![\d.])'), valid_ip),
    ('车牌', re.compile(f'(?<![A-Za-z0-9])[{PROVINCES}][A-Z][A-HJ-NP-Z0-9]{{5,6}}(?![A-Za-z0-9])'), None),
    ('案号', re.compile(r'[（(]\d{4}[）)][^\s（）()，,。；;]{2,14}?\d+号'), None),
]
# A checksum makes these safe to refuse outright when they appear unmasked in an outgoing request.
STRICT = {'身份证', '信用代码'}


def pattern_hits(text, categories):
    """Non-overlapping (start, end, category) spans whose shape and checksum match an enabled category."""
    hits = []
    for kind, pattern, valid in PATTERNS:
        if kind not in categories:
            continue
        for m in pattern.finditer(text):
            if (valid is None or valid(m.group())) and not any(m.start() < e and s < m.end() for s, e, _ in hits):
                hits.append((m.start(), m.end(), kind))
    return sorted(hits)
