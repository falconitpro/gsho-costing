import { useEffect, type RefObject } from "react";

/** D-164 (пакет H/B): для «Нового вида» на телефоне строки таблицы показываются карточками — каждой ячейке
 *  проставляется data-label из заголовка столбца (с учётом colSpan). Разметка и данные не меняются;
 *  вызывается после каждой отрисовки, поэтому подписи совпадают с текущим набором колонок. */
export function useTableLabels(ref: RefObject<HTMLTableElement | null>, on: boolean) {
  useEffect(() => {
    const t = ref.current;
    if (!on || !t) return;
    const heads: string[] = [];
    t.querySelectorAll("thead tr:last-child th").forEach((th) => {
      const txt = (th.textContent || "").replace(/[▲▼↕]/g, "").trim();
      for (let i = 0; i < ((th as HTMLTableCellElement).colSpan || 1); i++) heads.push(txt);
    });
    t.querySelectorAll("tbody tr").forEach((tr) => {
      let i = 0;
      tr.querySelectorAll(":scope > td").forEach((td) => {
        const c = td as HTMLTableCellElement;
        if (c.colSpan > 1 && c.colSpan >= heads.length) { c.removeAttribute("data-label"); return; }
        if (heads[i]) c.setAttribute("data-label", heads[i]); else c.removeAttribute("data-label");
        i += c.colSpan || 1;
      });
    });
  });
}

/** D-171 (пакет G): то же для всех таблиц с заголовком внутри контейнера (секции настроек загружают данные сами,
 *  поэтому следим за изменениями). Таблицы-матрицы с полями ввода помечаются классом-исключением и остаются прокручиваемыми. */
export function useAutoTableCards(ref: RefObject<HTMLElement | null>, on: boolean, dep: unknown = null, skip = "table.acc-matrix, table.adm-roles") {
  useEffect(() => {
    const box = ref.current;
    if (!on || !box) return;
    const apply = () => {
      box.querySelectorAll("table").forEach((t) => {
        if (t.matches(skip) || !t.tHead) return;
        if (!t.classList.contains("nv-cards")) t.classList.add("nv-cards");
        const heads: string[] = [];
        t.querySelectorAll("thead tr:last-child th").forEach((th) => {
          const txt = (th.textContent || "").trim();
          for (let i = 0; i < ((th as HTMLTableCellElement).colSpan || 1); i++) heads.push(txt);
        });
        t.querySelectorAll("tbody tr").forEach((tr) => {
          let i = 0;
          tr.querySelectorAll(":scope > td").forEach((td) => {
            const c = td as HTMLTableCellElement;
            const want = c.colSpan > 1 && c.colSpan >= heads.length ? "" : heads[i] || "";
            if ((c.getAttribute("data-label") || "") !== want) { if (want) c.setAttribute("data-label", want); else c.removeAttribute("data-label"); }
            i += c.colSpan || 1;
          });
        });
      });
    };
    apply();
    const mo = new MutationObserver(() => apply());
    mo.observe(box, { childList: true, subtree: true });
    return () => mo.disconnect();
  }, [ref, on, dep, skip]);
}
