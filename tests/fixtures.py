from pathlib import Path
from docx import Document
from docx.shared import Pt
import pymupdf as fitz

LINES = ['甲方：星河示例科技有限公司。', '联系人：张三，电话：13800138000。',
         '合同金额10000元，签约日期2026年10月30日。', '争议提交北京市朝阳区人民法院处理。']


def make_files(folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    txt = folder / '示例合同.txt'
    txt.write_bytes(('\r\n'.join(LINES)+'\r\n').encode('utf-8'))
    word = Document()
    p = word.add_paragraph()
    p.add_run('甲方：星河示例').bold = True
    p.add_run('科技有限公司。').italic = True
    for line in LINES[1:]:
        word.add_paragraph(line)
    table = word.add_table(rows=1, cols=2)
    table.cell(0, 0).text = '联系人'
    table.cell(0, 1).text = '张三'
    word.sections[0].header.paragraphs[0].text = '星河示例科技有限公司'
    word.sections[0].footer.paragraphs[0].text = '合同附件1'
    for p in word.paragraphs:
        for r in p.runs:
            r.font.size = Pt(12)
    docx = folder / '示例合同.docx'
    word.save(docx)
    pdf_path = folder / '示例合同.pdf'
    # In-place PDF labels need room to the right; contracts usually put a value at the end of its field line.
    pdf_lines = ['甲方：星河示例科技有限公司', '联系人：张三', '电话：13800138000', *LINES[2:]]
    with fitz.open() as pdf:
        page = pdf.new_page()
        for i, line in enumerate(pdf_lines):
            page.insert_text((60, 70+i*30), line, fontname='china-s', fontsize=12)
        page.draw_line((60, 210), (500, 210))
        pdf.save(pdf_path)
    return [txt, docx, pdf_path]
