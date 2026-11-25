import { useState, type ReactNode } from "react";
import { useNewUiFlag } from "../uiMode";

/** D-168 (ТЗ пакет H/E): в «Новом виде» на телефоне фильтры реестра прячутся под кнопку «Фильтры · N»;
 *  на компьютере и в прежнем виде — те же элементы на месте, разметка прежнего вида не меняется. */
export default function MobileFilters({ count = 0, children }: { count?: number; children: ReactNode }) {
  const nv = useNewUiFlag();
  const [open, setOpen] = useState(false);
  if (!nv) return <>{children}</>;
  return <>
    <button type="button" className="btn ghost mf-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>Фильтры{count ? ` · ${count}` : ""} {open ? "▴" : "▾"}</button>
    <div className={"mf-body" + (open ? " open" : "")}>{children}</div>
  </>;
}
