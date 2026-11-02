"""Генератор коммерческого предложения по корпоративному шаблону (раздел 10 ТЗ).

Бренд-бук BergHaus: акцент #FF3131 (заголовки таблиц, колонтитул, итоговая цена), текст #000000,
гарнитура Intro (резерв — Arial). Логотипы — из настроек (шаблон kind=logo).

Себестоимость, маржа и сценарии в КП никогда не попадают: `assert_no_cost_leak` проверяет готовый документ
по списку стоп-слов и ключей, и генератор падает, если что-то просочилось.

Первая очередь: сборка DOCX через python-docx по структуре ТЗ; во второй очереди — DOCX-шаблон с плейсхолдерами
(docxtpl), который администратор может заменить в настройках.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt, RGBColor
import os

ASSETS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")
DEFAULT_LOGO = os.path.join(ASSETS, "logo_main.png")
DEFAULT_LOGO_MONO = os.path.join(ASSETS, "logo_mono.png")
DEFAULT_LOGO_DARK_BG = os.path.join(ASSETS, "logo_dark_bg.png")   # версия «на тёмный фон» — для тёмных блоков титула

ACCENT = RGBColor(0xFF, 0x31, 0x31)
ACCENT_HEX = "FF3131"
BLACK = RGBColor(0, 0, 0)
FONT = "Intro"
FONT_FALLBACK = "Arial"

STOP_WORDS = ("себестоимост", "маржа", "маржин", "наценк", "сценари", "пессимист", "оптимист", "кассовый разрыв",
              "cost_total", "margin", "markup", "profit", "прибыль")


@dataclass
class KpVariant:
    name: str
    model_factory: str
    qty: int
    configuration: str
    basis: str
    delivery_days: int
    unit_price_rub: float
    total_rub: float
    vat_pct: float
    payment_terms: str
    warranty: str
    service_line: Optional[str] = None     # «включено в цену» или сумма
    delivery_term_text: str = ""           # задача 3: «105 календарных дней с даты открытия аккредитива» по позициям


@dataclass
class KpData:
    kp_no: str
    kp_date: str
    valid_until: str
    client: str
    site: str
    manager: str
    manager_contact: str
    intro_text: str
    variants: list[KpVariant]
    tech_specs: dict[str, list[tuple[str, str, str]]]     # позиция → [(параметр RU, значение, ед.)]
    service_block: Optional[dict] = None                  # {regulations: [...], spare_parts: str, visits: str, price: str}
    delivery_terms: list[tuple[str, str]] = field(default_factory=list)   # 14.7–14.21
    payment_and_warranty: str = ""
    fx_clause_pct: float = 5.0
    signatures: str = ""
    requisites: str = ""
    logo_path: Optional[str] = None
    single_offer: bool = True                                  # задача 5: одно предложение — без слова «Вариант»
    images: dict[str, list[tuple[str, str]]] = field(default_factory=dict)   # задача 10: позиция → [(путь, подпись)]
    brand_company: bool = False                                # задача 9: подача под брендом компании (без названия завода)
    forbidden_words: list[str] = field(default_factory=list)   # названия заводов, которых не должно быть в КП под брендом компании


def _set_cell_bg(cell, hex_color: str) -> None:
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tcPr.append(shd)


def _run(p, text: str, bold=False, color=BLACK, size=10):
    r = p.add_run(text)
    r.bold = bold
    r.font.color.rgb = color
    r.font.size = Pt(size)
    r.font.name = FONT
    r._element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    return r


def _heading(doc, text: str, size=14):
    p = doc.add_paragraph()
    _run(p, text, bold=True, color=ACCENT, size=size)
    return p


def _table(doc, header: list[str], rows: list[list[str]], accent_last_row=False):
    t = doc.add_table(rows=1, cols=len(header))
    t.style = "Table Grid"
    for i, h in enumerate(header):
        c = t.rows[0].cells[i]
        c.text = ""
        _run(c.paragraphs[0], h, bold=True, color=RGBColor(0xFF, 0xFF, 0xFF))
        _set_cell_bg(c, ACCENT_HEX)
    for ri, row in enumerate(rows):
        cells = t.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = ""
            bold = accent_last_row and ri == len(rows) - 1
            _run(cells[i].paragraphs[0], str(v), bold=bold, color=ACCENT if bold else BLACK)
    return t


def build_kp(data: KpData, path: str) -> str:
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = FONT
    style.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    style.font.color.rgb = BLACK

    # Титул: основной логотип (из настроек администратора или встроенный)
    logo = data.logo_path or (DEFAULT_LOGO if os.path.exists(DEFAULT_LOGO) else None)
    if logo:
        try:
            doc.add_picture(logo, width=Mm(55))
        except Exception:   # pragma: no cover
            pass
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    _run(p, "КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ", bold=True, color=ACCENT, size=22)
    p = doc.add_paragraph()
    _run(p, f"№ {data.kp_no} от {data.kp_date} · действительно до {data.valid_until}", size=11)
    p = doc.add_paragraph()
    _run(p, f"Заказчик: {data.client}\nОбъект: {data.site}\nКонтактное лицо: {data.manager}, {data.manager_contact}", size=11)
    doc.add_page_break()

    # Обращение
    doc.add_paragraph(data.intro_text)

    # Таблица предложения (одно предложение) или вариантов (альтернативы, задача 5)
    _heading(doc, "Предложение" if data.single_offer else "Варианты предложения")
    header = ["Показатель"] + (["Значение"] if data.single_offer else [v.name for v in data.variants])
    rows = [
        ["Модель" if data.brand_company else "Модель и завод"] + [v.model_factory for v in data.variants],
        ["Количество"] + [str(v.qty) for v in data.variants],
        ["Комплектация и опции"] + [v.configuration for v in data.variants],
        ["Базис поставки"] + [v.basis for v in data.variants],
        ["Срок поставки"] + [v.delivery_term_text or f"{v.delivery_days} дн." for v in data.variants],
        ["Цена за единицу, RUB без НДС"] + [f"{v.unit_price_rub:,.0f}".replace(",", " ") for v in data.variants],
        ["Условия оплаты"] + [v.payment_terms for v in data.variants],
        ["Гарантия"] + [v.warranty for v in data.variants],
    ]
    if any(v.service_line for v in data.variants):
        rows.append(["Сервис до наработки"] + [v.service_line or "—" for v in data.variants])
    rows.append(["Итого, RUB без НДС"] + [f"{v.total_rub:,.0f}".replace(",", " ") for v in data.variants])
    rows.append([f"Итого, RUB с НДС {data.variants[0].vat_pct:.0f} %"] +
                [f"{v.total_rub * (1 + v.vat_pct / 100):,.0f}".replace(",", " ") for v in data.variants])
    _table(doc, header, rows, accent_last_row=True)

    # Техспецификация (+ изображения и чертежи по выбору КД, задача 10)
    _heading(doc, "Техническая спецификация")
    for pos, params in data.tech_specs.items():
        p = doc.add_paragraph()
        _run(p, pos, bold=True, size=11)
        if params:
            _table(doc, ["Параметр", "Значение", "Ед."], [[a, str(b), c] for a, b, c in params])
        for path, caption in data.images.get(pos, []):
            _add_image(doc, path, caption)

    # Сервис
    if data.service_block:
        sb = data.service_block
        _heading(doc, "Сервис до наработки")
        doc.add_paragraph("Регламенты: " + ", ".join(sb.get("regulations", [])))
        doc.add_paragraph("Объём ЗИП: " + sb.get("spare_parts", ""))
        doc.add_paragraph("Выезды: " + sb.get("visits", ""))
        p = doc.add_paragraph()
        _run(p, "Стоимость: " + sb.get("price", "включено в цену"), bold=True, color=ACCENT)

    # Условия поставки, документация, сертификация
    _heading(doc, "Условия поставки, документация и сертификация")
    if data.delivery_terms:
        _table(doc, ["Условие", "Значение"], [[a, b] for a, b in data.delivery_terms])

    _heading(doc, "Условия оплаты и гарантии")
    doc.add_paragraph(data.payment_and_warranty)
    doc.add_paragraph(f"Цена действительна до {data.valid_until}. При изменении курса ЦБ РФ более чем на ±{data.fx_clause_pct:g} % "
                      f"относительно курса на дату предложения цена подлежит пересмотру.")

    _heading(doc, "Подписи и реквизиты")
    doc.add_paragraph(data.signatures)
    doc.add_paragraph(data.requisites)

    # Колонтитул: монохромный знак + номер КП
    footer = doc.sections[0].footer.paragraphs[0]
    if os.path.exists(DEFAULT_LOGO_MONO):
        try:
            footer.add_run().add_picture(DEFAULT_LOGO_MONO, height=Mm(6))
        except Exception:   # pragma: no cover
            pass
    _run(footer, f"   Berghaus · КП № {data.kp_no}", color=ACCENT, size=8)

    doc.save(path)
    assert_no_cost_leak(path, data.forbidden_words if data.brand_company else None, data.brand_company)
    return path


def pdf_thumbnail(path: str, max_px: int = 1600) -> Optional[str]:
    """Миниатюра первой страницы PDF-чертежа (PyMuPDF); без библиотеки или при ошибке — None."""
    try:
        import fitz   # PyMuPDF
        with fitz.open(path) as pdf:
            if pdf.page_count == 0:
                return None
            page = pdf.load_page(0)
            zoom = max(0.2, min(4.0, max_px / max(page.rect.width, page.rect.height)))
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            out = os.path.splitext(path)[0] + "_p1.png"
            pix.save(out)
            return out
    except Exception:
        return None


def _add_image(doc, path: str, caption: str) -> None:
    """Вставка изображения с уменьшением до разумного размера. PDF-чертёж — миниатюрой первой страницы;
    DWG/DXF в документ не вставляются (только вложением к письму)."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        thumb = pdf_thumbnail(path)
        if not thumb:
            return
        path, caption = thumb, (caption + " (стр. 1)") if caption else "Чертёж, стр. 1"
        ext = ".png"
    if ext not in (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff"):
        return
    src = path
    try:
        from PIL import Image
        im = Image.open(path)
        im.thumbnail((1600, 1600))
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        src = os.path.splitext(path)[0] + "_kp.jpg"
        im.save(src, "JPEG", quality=85)
    except Exception:   # без PIL или неподдерживаемый формат — как есть
        src = path
    try:
        doc.add_picture(src, width=Mm(140))
        if caption:
            p = doc.add_paragraph()
            _run(p, caption, size=9, color=BLACK)
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    except Exception:   # pragma: no cover
        pass


CJK_RE = __import__("re").compile(r"[\u4e00-\u9fff]")


def assert_no_cost_leak(path: str, forbidden: Optional[list[str]] = None, brand_company: bool = False) -> None:
    """Отдельная проверка генератора: в КП нет себестоимости, маржи и сценариев; под брендом компании — нет названий завода и китайского текста."""
    doc = Document(path)
    texts = [p.text for p in doc.paragraphs]
    for t in doc.tables:
        for row in t.rows:
            texts.extend(c.text for c in row.cells)
    blob = "\n".join(texts).lower()
    leaked = [w for w in STOP_WORDS if w in blob]
    if leaked:
        raise RuntimeError(f"КП содержит закрытые данные: {leaked}")
    if forbidden:
        found = [w for w in forbidden if w and w.lower() in blob]
        if found:
            raise RuntimeError(f"КП под брендом компании содержит название завода: {found}")
    if brand_company and CJK_RE.search(blob):
        raise RuntimeError("КП под брендом компании содержит китайский текст — проверьте значения и подписи")
