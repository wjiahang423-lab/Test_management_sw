# -*- coding: utf-8 -*-
"""Convert the project Markdown docs to .docx with python-docx.

This is intentionally small: headings, paragraphs, lists, tables, fenced code
blocks and blockquotes are enough for the docs in this repository. It keeps the
Markdown files as the source of truth and regenerates the .docx copies.

Usage:
    python3 tools/md_to_docx.py                 # convert docs/*.md
    python3 tools/md_to_docx.py a.md [b.md ...]
"""
import os
import re
import sys

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")

INLINE_RE = re.compile(r"(\*\*.+?\*\*|`.+?`|\[[^\]]+\]\([^)]+\))")


def _set_run(run, size=10.5, bold=False, mono=False):
    run.font.name = "Consolas" if mono else "Microsoft YaHei"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    run.font.size = Pt(size)
    run.bold = bold


def _add_inline(paragraph, text, size=10.5, bold=False):
    """Add text, converting **bold**, `code` and [label](url) to runs."""
    text = re.sub(r"<br\s*/?>", "\n", text or "")
    pos = 0
    for m in INLINE_RE.finditer(text):
        if m.start() > pos:
            _set_run(paragraph.add_run(text[pos:m.start()]), size, bold)
        token = m.group(0)
        if token.startswith("**"):
            _set_run(paragraph.add_run(token[2:-2]), size, True)
        elif token.startswith("`"):
            _set_run(paragraph.add_run(token[1:-1]), size, bold, mono=True)
        else:
            label, url = re.match(r"\[([^\]]+)\]\(([^)]+)\)", token).groups()
            _set_run(paragraph.add_run(label), size, bold)
            _set_run(paragraph.add_run(" ({})".format(url)), size, False, mono=True)
        pos = m.end()
    if pos < len(text):
        _set_run(paragraph.add_run(text[pos:]), size, bold)


def _flush_table(doc, rows):
    if not rows:
        return
    header = [c.strip() for c in rows[0].strip().strip("|").split("|")]
    body = []
    for row in rows[2:]:
        body.append([c.strip() for c in row.strip().strip("|").split("|")])
    table = doc.add_table(rows=1, cols=len(header))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, text in enumerate(header):
        cell = table.rows[0].cells[i]
        cell.text = ""
        _add_inline(cell.paragraphs[0], text, 9.5, bold=True)
    for row in body:
        cells = table.add_row().cells
        for i, text in enumerate(row[:len(header)]):
            cells[i].text = ""
            _add_inline(cells[i].paragraphs[0], text, 9.5)
    doc.add_paragraph()


def convert(md_path, docx_path=None):
    with open(md_path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Microsoft YaHei"
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    style.font.size = Pt(10.5)

    idx = 0
    while idx < len(lines):
        line = lines[idx]
        stripped = line.strip()

        if stripped.startswith("```"):
            idx += 1
            block = []
            while idx < len(lines) and not lines[idx].strip().startswith("```"):
                block.append(lines[idx])
                idx += 1
            idx += 1
            p = doc.add_paragraph()
            p.paragraph_format.space_after = Pt(4)
            _set_run(p.add_run("\n".join(block)), 9, mono=True)
            continue

        if stripped.startswith("|") and stripped.endswith("|"):
            block = []
            while idx < len(lines) and lines[idx].strip().startswith("|"):
                block.append(lines[idx])
                idx += 1
            _flush_table(doc, block)
            continue

        if stripped in ("---", "***", "___"):
            idx += 1
            continue

        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            level = min(len(m.group(1)), 4)
            h = doc.add_heading("", level=level)
            _add_inline(h, m.group(2), {1: 18, 2: 15, 3: 13, 4: 11.5}.get(level, 11))
            idx += 1
            continue

        if stripped.startswith(">"):
            text = stripped.lstrip("> ").strip()
            if text:
                p = doc.add_paragraph()
                p.paragraph_format.left_indent = Pt(18)
                _add_inline(p, text, 10)
            idx += 1
            continue

        m = re.match(r"^[-*+]\s+(.*)$", stripped)
        if m:
            p = doc.add_paragraph(style="List Bullet")
            _add_inline(p, m.group(1))
            idx += 1
            continue

        m = re.match(r"^\d+[.)]\s+(.*)$", stripped)
        if m:
            p = doc.add_paragraph(style="List Number")
            _add_inline(p, m.group(1))
            idx += 1
            continue

        if stripped:
            p = doc.add_paragraph()
            if re.match(r"^(版本|日期|适用对象|开发技术|技术栈|项目名称|架构原则)", stripped):
                p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            _add_inline(p, line)
        idx += 1

    if docx_path is None:
        docx_path = os.path.splitext(md_path)[0] + ".docx"
    doc.save(docx_path)
    return docx_path


def main(argv):
    if argv:
        files = argv
    else:
        files = [os.path.join(DOCS, n) for n in sorted(os.listdir(DOCS))
                 if n.endswith(".md")]
    for md in files:
        try:
            out = convert(md)
            print("{} -> {}".format(os.path.basename(md), os.path.basename(out)))
        except Exception as e:
            print("转换失败 {}: {}".format(md, e))


if __name__ == "__main__":
    main(sys.argv[1:])
