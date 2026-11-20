"""«Кто что может делать» (D-115): правила на действия, собранные из кода сервера.

Права на действия (кто утверждает, визирует, возвращает на доработку, ведёт логистику) заданы в коде, а не в настройках
(решение владельца 20.11.2026). Чтобы экран не расходился с тем, как система работает на самом деле, список строится
разбором исходного кода при первом обращении: тексты отказов «нет прав» (HTTP 403) и роли, перечисленные у методов API.
"""
from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path

from ..core.roles import Role
from ..core.workflow import DEFAULT_APPROVAL_LADDER, role_title

APP_DIR = Path(__file__).resolve().parent.parent

SECTIONS: list[tuple[str, tuple[str, ...]]] = [
    ("Сделка: заявка, позиции, исход", ("api/deals", "api/deal_trash", "api/images_options", "api/tech_params", "api/competitors", "services/deal_service")),
    ("Расчёт, цена, утверждение и КП", ("api/pricing", "services/mirror_offers", "services/deal_calc")),
    ("Торги", ("api/negotiation", "services/negotiation")),
    ("Логистика и таможня", ("api/logistics", "api/logistics_workspace", "services/logistics_workspace")),
    ("Сервис и приёмка", ("api/service", "api/acceptance")),
    ("Контракт и исполнение", ("api/execution",)),
    ("Задачи, файлы, план / факт", ("api/tasks_files_planfact", "api/documents", "api/model_docs")),
    ("Юридический блок", ("api/legal_", "services/legal_")),
    ("Финансы", ("api/finance", "services/finance")),
    ("Запчасти, снабжение и склад", ("api/parts_", "services/parts")),
    ("Каталог и справочники", ("api/catalog", "api/references")),
    ("Сотрудники, доступ, настройки, безопасность", ("api/admin", "api/team", "api/security", "api/auth", "core/auth", "api/operations")),
    ("Рабочее место, сводка, уведомления, помощник, поддержка", ("api/workplace", "api/summary", "api/notifications", "api/briefing", "api/assistant", "api/support")),
]
OTHER = "Прочее"
CONDITIONS = {"always": "всегда", "margin_below_threshold": "маржа ниже порога", "cash_gap_above_limit": "кассовый разрыв выше лимита",
              "deal_above_gd_limit": "сумма сделки выше лимита ЗГД", "fd_declined": "ФД отказал в визе", "overhead_override": "изменены накладные"}


def _text(node: ast.AST) -> str:
    """Текст сообщения: строка как есть; в f-строке вычисляемые части — «…»."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else "…" for v in node.values)
    return ""


def _roles(call: ast.Call) -> list[str]:
    out = []
    for a in call.args:
        if isinstance(a, ast.Attribute) and isinstance(a.value, ast.Name) and a.value.id == "Role":
            try:
                out.append(role_title(Role[a.attr]))
            except KeyError:
                out.append(a.attr)
    return out


def _section(rel: str) -> str:
    for title, prefixes in SECTIONS:
        if any(rel.startswith(p) for p in prefixes):
            return title
    return OTHER


def _scan_file(path: Path, rel: str) -> tuple[list[str], int]:
    rules: list[str] = []
    silent = 0
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        # 1) отказ «нет прав» с текстом
        if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) == "HTTPException" and node.args:
            code = node.args[0]
            if isinstance(code, ast.Constant) and code.value == 403:
                msg = _text(node.args[1]).strip() if len(node.args) > 1 else ""
                if not msg:
                    msg = next((_text(k.value).strip() for k in node.keywords if k.arg == "detail"), "")
                if msg:
                    rules.append(msg)
                else:
                    silent += 1
        # 2) метод API, доступный только перечисленным ролям
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in node.args.defaults + node.args.kw_defaults:
                for c in ast.walk(d) if d is not None else ():
                    if isinstance(c, ast.Call) and getattr(c.func, "id", "") == "require_roles":
                        roles = _roles(c)
                        doc = (ast.get_docstring(node) or "").strip().split("\n")[0].rstrip(".")
                        if roles and doc:
                            rules.append(f"{doc} — только: {', '.join(roles)}")
    return rules, silent


@lru_cache(maxsize=1)
def collect() -> dict:
    by_section: dict[str, dict] = {}
    for sub in ("api", "services", "core"):
        for path in sorted((APP_DIR / sub).glob("*.py")):
            rel = f"{sub}/{path.stem}"
            try:
                rules, silent = _scan_file(path, rel)
            except (SyntaxError, OSError):
                continue
            if not rules and not silent:
                continue
            s = by_section.setdefault(_section(rel), {"rules": set(), "silent": 0})
            s["rules"].update(rules)
            s["silent"] += silent
    order = [t for t, _ in SECTIONS] + [OTHER]
    sections = [{"title": t, "rules": sorted(by_section[t]["rules"], key=str.lower), "silent": by_section[t]["silent"]} for t in order if t in by_section]
    ladder = [{"step": st.name, "role": role_title(st.role), "when": " или ".join(CONDITIONS.get(c, c) for c in st.condition.split("|"))} for st in DEFAULT_APPROVAL_LADDER]
    return {"sections": sections, "ladder": ladder, "total": sum(len(s["rules"]) for s in sections)}
