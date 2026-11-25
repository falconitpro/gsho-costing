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
