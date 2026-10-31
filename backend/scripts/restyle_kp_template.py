"""Приводит шаблон КП к бренд-буку Berghaus, сохраняя структуру и текст исходного документа.

Правила оформления: акцент #FF3131 (заголовки, шапки таблиц, итог), текст #000, гарнитура Intro (резерв Arial),
основной логотип на титуле, монохромный знак в колонтитуле.
"""
from __future__ import annotations

import sys
from copy import deepcopy

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Mm, Pt, RGBColor

ACCENT = RGBColor(0xFF, 0x31, 0x31)
ACCENT_HEX = "FF3131"
SOFT_HEX = "FFF0F0"
GREY_HEX = "F5F5F5"
BLACK = RGBColor(0, 0, 0)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
FONT = "Intro"
LOGO_MAIN, LOGO_MONO = sys.argv[3], sys.argv[4]


def set_font(run, size=None, bold=None, color=None, italic=None):
    run.font.name = FONT
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts"); rpr.append(rfonts)
    for a in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rfonts.set(qn(a), FONT)
    if size is not None: run.font.size = Pt(size)
    if bold is not None: run.font.bold = bold
    if italic is not None: run.font.italic = italic
    run.font.color.rgb = color if color is not None else BLACK


def shade(cell, hex_color):
    tcPr = cell._tc.get_or_add_tcPr()
    for old in tcPr.findall(qn("w:shd")): tcPr.remove(old)
    shd = OxmlElement("w:shd"); shd.set(qn("w:val"), "clear"); shd.set(qn("w:color"), "auto"); shd.set(qn("w:fill"), hex_color)
    tcPr.append(shd)


def cell_margins(cell, top=60, bottom=60, left=100, right=100):
    tcPr = cell._tc.get_or_add_tcPr()
    mar = OxmlElement("w:tcMar")
    for k, v in (("top", top), ("bottom", bottom), ("start", left), ("end", right)):
        el = OxmlElement(f"w:{k}"); el.set(qn("w:w"), str(v)); el.set(qn("w:type"), "dxa"); mar.append(el)
    tcPr.append(mar)


def table_borders(table, color="D9D9D9", inner=True):
    tblPr = table._tbl.tblPr
    for old in tblPr.findall(qn("w:tblBorders")): tblPr.remove(old)
    b = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        if edge in ("left", "right", "insideV"):
            el.set(qn("w:val"), "nil")
        else:
            el.set(qn("w:val"), "single"); el.set(qn("w:sz"), "6"); el.set(qn("w:color"), color)
        b.append(el)
    tblPr.append(b)


def para_spacing(p, before=0, after=4, line=1.15):
    pf = p.paragraph_format
    pf.space_before = Pt(before); pf.space_after = Pt(after); pf.line_spacing = line


def restyle_paragraph(p, size=10.5, bold=None, color=None, align=None, before=0, after=4):
    for r in p.runs:
        set_font(r, size=size, bold=bold if bold is not None else r.font.bold, color=color)
    para_spacing(p, before, after)
    if align is not None: p.alignment = align


def bottom_border(p, color=ACCENT_HEX, sz=12):
    pPr = p._p.get_or_add_pPr()
    pbdr = OxmlElement("w:pBdr"); bt = OxmlElement("w:bottom")
    bt.set(qn("w:val"), "single"); bt.set(qn("w:sz"), str(sz)); bt.set(qn("w:space"), "4"); bt.set(qn("w:color"), color)
    pbdr.append(bt); pPr.append(pbdr)


def is_numeric(s: str) -> bool:
    t = s.replace(" ", "").replace(" ", "").replace(",", ".").replace("—", "").replace("-", "")
    return t.replace(".", "", 1).isdigit() or t == ""


def restyle_table(table, header=True, kind="data"):
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = True
    table_borders(table)
    rows = table.rows
    for ri, row in enumerate(rows):
        trPr = row._tr.get_or_add_trPr()
        cs = OxmlElement("w:cantSplit"); trPr.append(cs)
        if header and ri == 0:
            th = OxmlElement("w:tblHeader"); trPr.append(th)
    for ri, row in enumerate(rows):
        for ci, cell in enumerate(row.cells):
            cell_margins(cell)
            if not (header and ri == 0):
                shade(cell, "FFFFFF")
            for p in cell.paragraphs:
                para_spacing(p, 0, 0, 1.1)
                for r in p.runs:
                    set_font(r, size=9.5, color=BLACK, bold=r.font.bold)
            if header and ri == 0:
                shade(cell, ACCENT_HEX)
                for p in cell.paragraphs:
                    for r in p.runs: set_font(r, size=9.5, bold=True, color=WHITE)
            elif kind == "props" and ci == 0:   # таблицы «параметр — значение»: первая колонка серая, полужирная
                shade(cell, GREY_HEX)
                for p in cell.paragraphs:
                    for r in p.runs: set_font(r, size=9.5, bold=True, color=BLACK)
            # числа — вправо
            if kind == "data" and ci > 0 and ri > 0 and cell.text.strip() and is_numeric(cell.text):
                for p in cell.paragraphs: p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    # строка «Итого» (объединённая) — акцент
    last = rows[-1]
    if "итого" in last.cells[0].text.lower():
        for cell in last.cells:
            shade(cell, SOFT_HEX)
            for p in cell.paragraphs:
                for r in p.runs: set_font(r, size=10, bold=True, color=ACCENT)
        for p in last.cells[-1].paragraphs: p.alignment = WD_ALIGN_PARAGRAPH.RIGHT


def add_field(run, instr):
    for tag, extra in (("begin", None), (None, instr), ("end", None)):
        if tag:
            fc = OxmlElement("w:fldChar"); fc.set(qn("w:fldCharType"), tag); run._r.append(fc)
        else:
            it = OxmlElement("w:instrText"); it.set(qn("xml:space"), "preserve"); it.text = extra; run._r.append(it)


def main(src, dst):
    d = Document(src)
    sec = d.sections[0]
    sec.left_margin = sec.right_margin = Cm(2.0); sec.top_margin = Cm(1.5); sec.bottom_margin = Cm(1.8)
    sec.header_distance = Cm(0.8); sec.footer_distance = Cm(0.8)

    # базовый стиль
    st = d.styles["Normal"]; st.font.name = FONT; st.font.size = Pt(10.5); st.font.color.rgb = BLACK
    st.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)

    paras = d.paragraphs

    # --- шапка документа: логотип + реквизитная строка справа
    hdr = sec.header; hdr.is_linked_to_previous = False
    hp = hdr.paragraphs[0]; hp.text = ""
    ht = hdr.add_table(rows=1, cols=2, width=sec.page_width - sec.left_margin - sec.right_margin)
    ht.alignment = WD_TABLE_ALIGNMENT.CENTER
    lc, rc = ht.rows[0].cells
    lc.paragraphs[0].add_run().add_picture(LOGO_MAIN, height=Mm(13))
    rp = rc.paragraphs[0]; rp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    set_font(rp.add_run("ООО «Берг-Хаус»"), size=9, bold=True, color=BLACK)
    rp2 = rc.add_paragraph(); rp2.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    set_font(rp2.add_run("поставки горношахтного оборудования · berghaus.ru · info@berghaus.ru"), size=8, color=RGBColor(0x6B, 0x6B, 0x6B))
    # красная линия под шапкой
    line = hdr.add_paragraph(); bottom_border(line, ACCENT_HEX, 18); para_spacing(line, 0, 0, 1)
    for p in hdr.paragraphs:
        if not p.text and not p.runs: para_spacing(p, 0, 0, 1)

    # --- титул: заголовок, подзаголовок, обращение
    restyle_paragraph(paras[0], size=20, bold=True, color=ACCENT, align=WD_ALIGN_PARAGRAPH.LEFT, before=6, after=2)
    restyle_paragraph(paras[1], size=13, bold=False, color=BLACK, align=WD_ALIGN_PARAGRAPH.LEFT, before=0, after=4)
    # строка «№ / дата / действительно до» — добавляем после подзаголовка (оставляем плейсхолдеры)
    meta = deepcopy(paras[1]._p); paras[1]._p.addnext(meta)
    from docx.text.paragraph import Paragraph
    mp = Paragraph(meta, paras[1]._parent)
    for r in mp.runs: r._r.getparent().remove(r._r)
    set_font(mp.add_run("№ ________ от «____» ____________ 20__ г.   ·   действительно до «____» ____________ 20__ г."), size=9.5, color=RGBColor(0x6B, 0x6B, 0x6B))
    bottom_border(mp, "D9D9D9", 6); para_spacing(mp, 0, 10)

    paras = d.paragraphs   # пересчитать после вставки
    idx = {p.text.strip()[:30]: i for i, p in enumerate(paras) if p.text.strip()}

    for p in paras[3:]:
        t = p.text.strip()
        if not t: para_spacing(p, 0, 2); continue
        if t.startswith("Уважаем"):
            restyle_paragraph(p, size=11, bold=True, before=4, after=6)
        elif t[:2] in ("1.", "2.", "3.") and len(t) < 60 or t.startswith("Дополнительное оборудование"):
            restyle_paragraph(p, size=12.5, bold=True, color=ACCENT, before=12, after=4)
            p.paragraph_format.keep_with_next = True
            if t[:2] in ("1.", "2.", "3."): bottom_border(p, ACCENT_HEX, 8)
        elif t.startswith("Технические характеристики предварительные") or t.startswith("Цены указаны"):
            restyle_paragraph(p, size=9, color=RGBColor(0x6B, 0x6B, 0x6B), before=2, after=4)
            for r in p.runs: r.font.italic = True
        else:
            restyle_paragraph(p, size=10.5, before=0, after=6)
            p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY if len(t) > 120 else p.alignment

    # --- таблицы (структура исходная)
    tables = d.tables
    restyle_table(tables[0], kind="data")      # состав и стоимость
    restyle_table(tables[1], kind="data")      # доп. оборудование
    restyle_table(tables[2], header=False, kind="props")   # коммерческие условия
    restyle_table(tables[3], kind="props")     # техника
    restyle_table(tables[4], kind="props")     # зарядная станция
    # подпись
    sig = tables[5]; table_borders(sig, color="FFFFFF")
    for p in d.paragraphs[-3:]:
        p.paragraph_format.keep_with_next = True
    sig.autofit = False
    tblPr = sig._tbl.tblPr
    lay = OxmlElement("w:tblLayout"); lay.set(qn("w:type"), "fixed"); tblPr.append(lay)
    for col, w in zip(sig.columns, (Cm(9.0), Cm(4.0), Cm(4.0))): col.width = w
    for c, w in zip(sig.rows[0].cells, (Cm(9.0), Cm(4.0), Cm(4.0))): c.width = w
    for cell in sig.rows[0].cells:
        for p in cell.paragraphs:
            para_spacing(p, 0, 2, 1.15)
            for r in p.runs: set_font(r, size=10.5, color=BLACK, bold=r.font.bold)
    for p in sig.rows[0].cells[2].paragraphs:
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        for r in p.runs: set_font(r, size=10.5, bold=True)
    # ширины колонок таблиц состава: № узкая
    widths = {0: [Cm(0.9), Cm(9.1), Cm(1.8), Cm(2.6), Cm(2.6)], 1: [Cm(0.9), Cm(10.4), Cm(1.8), Cm(3.9)]}
    for ti, ws in widths.items():
        tables[ti].autofit = False
        for row in tables[ti].rows:
            for c, w in zip(row.cells, ws): c.width = w
    for row in tables[2].rows:
        row.cells[0].width = Cm(4.2); row.cells[1].width = Cm(12.8)

    # --- колонтитул: монохромный знак + текст + номер страницы
    ftr = sec.footer; ftr.is_linked_to_previous = False
    fp = ftr.paragraphs[0]
    txt = "".join(r.text for r in fp.runs)
    txt = txt.split("· стр.")[0].strip()
    for r in list(fp.runs): r._r.getparent().remove(r._r)
    bottom = fp  # перестраиваем: линия сверху, знак, текст, стр. X из Y
    pPr = fp._p.get_or_add_pPr(); pbdr = OxmlElement("w:pBdr"); top = OxmlElement("w:top")
    top.set(qn("w:val"), "single"); top.set(qn("w:sz"), "6"); top.set(qn("w:space"), "4"); top.set(qn("w:color"), ACCENT_HEX); pbdr.append(top); pPr.append(pbdr)
    fp.add_run().add_picture(LOGO_MONO, height=Mm(5))
    set_font(fp.add_run(f"   {txt}   ·   стр. "), size=8, color=RGBColor(0x6B, 0x6B, 0x6B))
    r = fp.add_run(); set_font(r, size=8, color=RGBColor(0x6B, 0x6B, 0x6B)); add_field(r, " PAGE ")
    set_font(fp.add_run(" из "), size=8, color=RGBColor(0x6B, 0x6B, 0x6B))
    r = fp.add_run(); set_font(r, size=8, color=RGBColor(0x6B, 0x6B, 0x6B)); add_field(r, " NUMPAGES ")
    para_spacing(fp, 4, 0, 1)

    d.core_properties.title = "Коммерческое предложение — ООО «Берг-Хаус»"
    d.core_properties.author = "ООО «Берг-Хаус»"
    d.save(dst)
    print("saved", dst)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
