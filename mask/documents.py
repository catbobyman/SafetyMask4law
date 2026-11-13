"""Read source locations and edit confirmed spans without rewriting whole documents."""
from __future__ import annotations

from collections import Counter
from io import BytesIO
from pathlib import Path
import re
import zipfile

from lxml import etree as ET
import pymupdf as fitz

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
NS = {'w': W}
XML_SPACE = '{http://www.w3.org/XML/1998/namespace}space'


def decode_text(data: bytes) -> tuple[str, str]:
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        encoding = 'utf-16'
    elif data.startswith(b'\xef\xbb\xbf'):
        encoding = 'utf-8-sig'
    else:
        try:
            data.decode('utf-8')
            encoding = 'utf-8'
        except UnicodeDecodeError:
            encoding = 'gb18030'
    text = data.decode(encoding)
    if '\x00' in text:
        raise ValueError('文件包含二进制内容，无法作为文本读取')
    return text, encoding


def xml(data):
    return ET.fromstring(data, ET.XMLParser(resolve_entities=False, no_network=True))


def text_nodes(paragraph):
    # Exclude nested text-box paragraphs; they are reported as unsupported.
    for node in paragraph.iter():
        if node.tag not in {f'{{{W}}}t', f'{{{W}}}tab', f'{{{W}}}br', f'{{{W}}}cr'}:
            continue
        if next(node.iterancestors(f'{{{W}}}p'), None) is paragraph:
            yield node, node.text or '' if node.tag == f'{{{W}}}t' else ('\t' if node.tag == f'{{{W}}}tab' else '\n')


def docx_parts(archive):
    names = [n for n in archive.namelist() if re.fullmatch(r'word/(document|header\d+|footer\d+)\.xml', n)]
    return sorted(names, key=lambda n: (n != 'word/document.xml', n))


def field_codes(root):
    codes, open_fields = [], []
    for node in root.iter(f'{{{W}}}fldChar', f'{{{W}}}instrText', f'{{{W}}}fldSimple'):
        if node.tag == f'{{{W}}}fldSimple':
            codes.append(node.get(f'{{{W}}}instr', ''))
        elif node.tag == f'{{{W}}}instrText':
            if open_fields:
                open_fields[-1] += node.text or ''
        elif node.get(f'{{{W}}}fldCharType') == 'begin':
            open_fields.append('')
        elif node.get(f'{{{W}}}fldCharType') == 'end' and open_fields:
            codes.append(open_fields.pop())
    return codes + open_fields


# Page numbering fields carry no document content.
PAGE_FIELDS = {'PAGE', 'NUMPAGES', 'SECTIONPAGES', 'SECTION'}


def paragraph_breaks(p):
    """Offsets in p where Word started a new page, and whether the next paragraph starts one.

    Word records rendered page starts as lastRenderedPageBreak when it saves; offsets count
    the same characters as text_nodes so they line up with the extracted paragraph.
    """
    cuts, pos = [], 0
    ppr = p.find('w:pPr', NS)
    before = ppr.find('w:pageBreakBefore', NS) if ppr is not None else None
    if before is not None and before.get(f'{{{W}}}val', 'true') not in {'0', 'false', 'off'}:
        cuts.append(0)
    sect = ppr.find('w:sectPr', NS) if ppr is not None else None
    kind = sect.find('w:type', NS) if sect is not None else None
    after = sect is not None and (kind is None or kind.get(f'{{{W}}}val') in {'nextPage', 'oddPage', 'evenPage'})
    for node in p.iter():
        if next(node.iterancestors(f'{{{W}}}p'), None) is not p:
            continue
        if node.tag == f'{{{W}}}lastRenderedPageBreak':
            cuts.append(pos)
        elif node.tag == f'{{{W}}}t':
            pos += len(node.text or '')
        elif node.tag in {f'{{{W}}}tab', f'{{{W}}}cr', f'{{{W}}}br'}:
            pos += 1
            if node.tag == f'{{{W}}}br' and node.get(f'{{{W}}}type') == 'page':
                cuts.append(pos)
    return cuts, after


def read_docx(data):
    sections, issues = [], []
    try:
        archive = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError('不是有效的 Word .docx 文件，可能已损坏或是 Word 打开时生成的临时文件') from exc
    with archive as z:
        for part in docx_parts(z):
            root = xml(z.read(part))
            paragraphs, sources, breaks = [], [], set()
            for i, p in enumerate(root.iter(f'{{{W}}}p')):
                if any(a.tag == f'{{{W}}}txbxContent' for a in p.iterancestors()):
                    continue
                n, text = len(paragraphs), ''.join(t for _, t in text_nodes(p))
                paragraphs.append(text)
                sources.append({'part': part, 'paragraph': i})
                cuts, after = paragraph_breaks(p)
                # A break at the end of a paragraph is the start of the next one; one page start is kept once.
                breaks.update((n, off) if off < len(text) or off == 0 else (n + 1, 0) for off in cuts)
                if after:
                    breaks.add((n + 1, 0))
            title = '正文与表格' if 'document.xml' in part else ('页眉' if 'header' in part else '页脚')
            section = {'title': title, 'paragraphs': paragraphs, 'sources': sources}
            if 'document.xml' in part:
                section['breaks'] = [list(b) for b in sorted(breaks) if b != (0, 0) and b[0] < len(paragraphs)]
            sections.append(section)
            for tag, label in [('ins', '修订'), ('del', '删除修订'), ('txbxContent', '文本框'),
                               ('hyperlink', '超链接目标'), ('drawing', '图片或绘图'),
                               ('object', '嵌入对象'), ('altChunk', '外部内容')]:
                if root.find('.//w:' + tag, NS) is not None:
                    issues.append(f'{title}包含未处理的{label}')
            if any((code.split() or [''])[0].upper() not in PAGE_FIELDS for code in field_codes(root)):
                issues.append(f'{title}包含未处理的域')
        for part, label in [('word/comments.xml', '批注'), ('word/footnotes.xml', '脚注'), ('word/endnotes.xml', '尾注')]:
            if part in z.namelist() and any(n.text for n in xml(z.read(part)).iter(f'{{{W}}}t')):
                issues.append(f'包含未处理的{label}')
    return {'sections': sections, 'issues': list(dict.fromkeys(issues))}


def pdf_blocks(page):
    result = []
    for block in page.get_text('rawdict', sort=True)['blocks']:
        if block['type'] != 0:
            continue
        text, chars = '', []
        for i, line in enumerate(block['lines']):
            if i:
                text += '\n'
                chars.append(None)
            for span in line['spans']:
                for c in span['chars']:
                    # A MuPDF character normally has exactly one code point.
                    for character in c['c']:
                        text += character
                        chars.append({'c': character, 'bbox': list(c['bbox']), 'origin': list(c['origin']),
                                      'size': span['size'], 'dir': list(line['dir'])})
        result.append((text, chars))
    return result


def read_pdf(data):
    sections, issues = [], []
    with fitz.open(stream=data, filetype='pdf') as pdf:
        if pdf.is_encrypted:
            raise ValueError('PDF 已加密，请先提供可直接打开的文件')
        if pdf.embfile_count():
            issues.append('PDF 含未处理的附件')
        for page in pdf:
            blocks = pdf_blocks(page)
            if not any(t.strip() for t, _ in blocks):
                issues.append(f'第 {page.number + 1} 页无可提取文字（空白页或扫描页）')
            if page.get_images():
                issues.append(f'第 {page.number + 1} 页包含未识别的图片，签章及图片文字未脱敏')
            if list(page.annots() or []) or list(page.widgets() or []) or page.get_links():
                issues.append(f'第 {page.number + 1} 页包含未处理的批注、表单或链接')
            sections.append({'title': f'第 {page.number + 1} 页', 'paragraphs': [t for t, _ in blocks],
                             'sources': [{'page': page.number, 'chars': c} for _, c in blocks]})
    return {'sections': sections, 'issues': issues}


def read_document(path: Path) -> dict:
    data, ext = path.read_bytes(), path.suffix.lower()
    if ext in {'.txt', '.md'}:
        text, encoding = decode_text(data)
        result = {'sections': [{'title': '正文', 'paragraphs': [text], 'sources': [{}]}], 'issues': [], 'encoding': encoding}
    elif ext == '.docx':
        result = read_docx(data)
    elif ext == '.pdf':
        result = read_pdf(data)
    else:
        raise ValueError('支持 TXT、Markdown、DOCX 和原生 PDF；旧版 .doc 请先另存为 .docx')
    if not any(p.strip() for s in result['sections'] for p in s['paragraphs']):
        raise ValueError('没有可处理的文字；扫描件需要 OCR，当前不支持')
    return result


def replace_text(text, edits):
    for e in sorted(edits, key=lambda e: e['start'], reverse=True):
        text = text[:e['start']] + e['tag'] + text[e['end']:]
    return text


def paragraph_edits(edits, s, p):
    return [e for e in edits if e['s'] == s and e['p'] == p]


def masked_sections(document, edits):
    return [{'title': section['title'], 'paragraphs': [replace_text(text, paragraph_edits(edits, si, pi))
            for pi, text in enumerate(section['paragraphs'])]} for si, section in enumerate(document['sections'])]


def replace_span(p, start, end, text):
    """Put text in place of the paragraph's characters [start, end), in the first run the span touches."""
    pos, inserted = 0, False
    for node, value in list(text_nodes(p)):
        stop = pos + len(value)
        a, b = max(pos, start), min(stop, end)
        if a < b:
            new = value[:a-pos] + (text if not inserted else '') + value[b-pos:]
            inserted = True
            if node.tag != f'{{{W}}}t':
                if new:
                    raise ValueError('候选从制表符或换行符开始，请修正范围')
                node.getparent().remove(node)
            else:
                node.text = new
                node.set(XML_SPACE, 'preserve')
        pos = stop


# An AI result is restored wherever Word keeps paragraphs, notes and comments included.
RESULT_PARTS = r'word/(document|header\d+|footer\d+|footnotes|endnotes|comments)\.xml'


def result_roots(archive):
    names = [n for n in archive.namelist() if re.fullmatch(RESULT_PARTS, n)]
    return {n: xml(archive.read(n)) for n in sorted(names, key=lambda n: (n != 'word/document.xml', n))}


def open_result(data):
    try:
        return zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError('不是有效的 Word .docx 文件，可能已损坏或是 Word 打开时生成的临时文件') from exc


def result_text(data):
    """Every paragraph of a Word AI result, one per line."""
    with open_result(data) as z:
        return '\n'.join(''.join(t for _, t in text_nodes(p)) for root in result_roots(z).values() for p in root.iter(f'{{{W}}}p'))


def restore_docx(data, spans):
    """The Word file with spans(paragraph text) -> [(start, end, original)] replaced in place; formatting stays."""
    with open_result(data) as original:
        roots = result_roots(original)
        for root in roots.values():
            for p in root.iter(f'{{{W}}}p'):
                for start, end, text in reversed(spans(''.join(t for _, t in text_nodes(p)))):
                    replace_span(p, start, end, text)
        target = BytesIO()
        with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as out:
            for item in original.infolist():
                out.writestr(item, ET.tostring(roots[item.filename], xml_declaration=True, encoding='UTF-8', standalone=True)
                             if item.filename in roots else original.read(item.filename))
    return target.getvalue()


def export_docx(source, document, edits, target):
    with zipfile.ZipFile(source) as original:
        roots = {p: xml(original.read(p)) for p in docx_parts(original)}
        for si, section in enumerate(document['sections']):
            for pi, location in enumerate(section['sources']):
                p = list(roots[location['part']].iter(f'{{{W}}}p'))[location['paragraph']]
                for edit in sorted(paragraph_edits(edits, si, pi), key=lambda e: e['start'], reverse=True):
                    replace_span(p, edit['start'], edit['end'], edit['tag'])
        with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as out:
            for item in original.infolist():
                data = original.read(item.filename)
                if item.filename in roots:
                    data = ET.tostring(roots[item.filename], xml_declaration=True, encoding='UTF-8', standalone=True)
                elif item.filename in {'docProps/core.xml', 'docProps/app.xml'}:
                    root = xml(data)
                    for node in root:
                        if ET.QName(node).localname in {'creator', 'lastModifiedBy', 'title', 'subject', 'description',
                                                        'keywords', 'Company', 'Manager'}:
                            node.text = ''
                    data = ET.tostring(root, xml_declaration=True, encoding='UTF-8')
                out.writestr(item, data)
    actual = read_docx(target.read_bytes())
    if [s['paragraphs'] for s in actual['sections']] != [s['paragraphs'] for s in masked_sections(document, edits)]:
        raise ValueError('Word 导出校验失败：输出文字与确认结果不一致')


def char_key(c):
    return c['c'], round(c['origin'][0], 1), round(c['origin'][1], 1)


def label_fonts():
    symbol_path = Path('C:/Windows/Fonts/seguisym.ttf')
    if not symbol_path.exists():
        raise ValueError('PDF 代号需要 Segoe UI Symbol 字体，未找到 C:/Windows/Fonts/seguisym.ttf')
    return fitz.Font('cjk'), fitz.Font(fontfile=str(symbol_path))


def export_pdf(source, document, edits, target):
    cjk, symbol = label_fonts()
    with fitz.open(source) as pdf:
        for si, section in enumerate(document['sections']):
            page = pdf[si]
            rotation = page.rotation
            page.set_rotation(0)
            selected, labels = set(), []
            page_edits = [e for e in edits if e['s'] == si]
            for edit in page_edits:
                chars = section['sources'][edit['p']]['chars']
                selected.update((edit['p'], i) for i in range(edit['start'], edit['end']) if chars[i])
            remaining = [c for pi, location in enumerate(section['sources']) for ci, c in enumerate(location['chars'])
                         if c and not c['c'].isspace() and (pi, ci) not in selected]
            text_right = max((c['bbox'][2] for location in section['sources'] for c in location['chars'] if c), default=page.rect.x1)
            for edit in page_edits:
                chars = section['sources'][edit['p']]['chars']
                region = [c for c in chars[edit['start']:edit['end']] if c and not c['c'].isspace()]
                if not region or any(abs(c['dir'][0]-1) > .001 or abs(c['dir'][1]) > .001 for c in region):
                    raise ValueError(f'第 {si+1} 页包含非水平或无文字范围，无法放置代号')
                # Use one selected line for the label; remove every selected character below.
                groups = {}
                for c in region:
                    groups.setdefault(round(c['origin'][1], 1), []).append(c)
                line = max(groups.values(), key=lambda cs: max(c['bbox'][2] for c in cs)-min(c['bbox'][0] for c in cs))
                box = fitz.Rect(min(c['bbox'][0] for c in line), min(c['bbox'][1] for c in line),
                                max(c['bbox'][2] for c in line), max(c['bbox'][3] for c in line))
                glyphs = [(ch, cjk if cjk.has_glyph(ord(ch)) else symbol) for ch in edit['tag']]
                if any(not font.has_glyph(ord(ch)) for ch, font in glyphs):
                    raise ValueError('PDF 代号字体缺少所需字符')
                width = sum(font.text_length(ch, fontsize=1) for ch, font in glyphs)
                # Official tags are long: let the label run into trailing whitespace and the right margin,
                # but never over a glyph that stays on the line.
                baseline = round(line[0]['origin'][1], 1)
                limit = min((c['bbox'][0] for c in remaining if round(c['origin'][1], 1) == baseline and c['bbox'][0] >= box.x1 - .01),
                            default=max(text_right, box.x1))
                size = min(line[0]['size'], (limit-box.x0-.2)/width)
                if size < 4:
                    raise ValueError(f'第 {si+1} 页“{edit["original"]}”位置放不下代号 {edit["tag"]}（字号低于 4pt）')
                labels.append((box.x0, line[0]['origin'][1], size, glyphs, edit['tag']))
            expected = Counter()
            for pi, location in enumerate(section['sources']):
                for ci, c in enumerate(location['chars']):
                    if c is None:
                        continue
                    if (pi, ci) not in selected:
                        expected[char_key(c)] += 1
                    else:
                        r = fitz.Rect(c['bbox'])
                        r.x0 += .05; r.x1 -= .05; r.y0 += .05; r.y1 -= .05
                        page.add_redact_annot(r, fill=False, cross_out=False)
            if selected:
                page.apply_redactions(images=0, graphics=0)
                remaining = Counter(char_key(c) for _, chars in pdf_blocks(page) for c in chars if c)
                if remaining != expected:
                    raise ValueError(f'第 {si+1} 页移除校验失败：目标残留或邻近文字受影响，未输出文件')
            writer = fitz.TextWriter(page.rect)
            for x, y, size, glyphs, _ in labels:
                for ch, font in glyphs:
                    writer.append((x, y), ch, font=font, fontsize=size)
                    x += font.text_length(ch, fontsize=size)
            if labels:
                writer.write_text(page)
                extracted = re.sub(r'\s', '', page.get_text())
                if any(tag not in extracted for *_, tag in labels):
                    raise ValueError(f'第 {si+1} 页代号写入校验失败')
            page.set_rotation(rotation)
        pdf.set_metadata({})
        pdf.del_xml_metadata()
        pdf.subset_fonts()
        pdf.save(target, garbage=4, deflate=True)


def export_original(source: Path, document, edits, target: Path):
    if source.suffix in {'.txt', '.md'}:
        text = replace_text(document['sections'][0]['paragraphs'][0], edits)
        # UTF-8 output supports every label even when the input used a legacy encoding.
        target.write_bytes(text.encode('utf-8'))
    elif source.suffix == '.docx':
        export_docx(source, document, edits, target)
    else:
        export_pdf(source, document, edits, target)
