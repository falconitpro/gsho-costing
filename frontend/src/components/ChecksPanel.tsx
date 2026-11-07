import { useState } from "react";

// Что не заполнено перед «Отправить на расчёт»: сводка одной строкой и раскрывающийся полный перечень по сделке и позициям.

export type CheckItem = { group: string; tab: string; position_id: string | null; fields: string[]; tech: string[] };

export default function ChecksPanel({ items, onGo }: { items: CheckItem[]; onGo: (it: CheckItem) => void }) {
  const [open, setOpen] = useState(false);
  if (!items.length) return null;
  const total = items.reduce((s, x) => s + x.fields.length + x.tech.length, 0);
  const summary = items.map((x) => `${x.group.split(" · ")[0]} — ${x.fields.length + x.tech.length}`).join(", ");
  return (
    <div className="alert warn" style={{ padding: "8px 12px" }}>
      <div className="row" style={{ justifyContent: "space-between", cursor: "pointer", gap: 8 }} onClick={() => setOpen(!open)}>
        <span><b>Перед отправкой на расчёт не заполнено: {total}</b> <span style={{ fontSize: 12 }}>({summary})</span></span>
        <button className="btn ghost" style={{ padding: "1px 8px", fontSize: 12 }}>{open ? "Свернуть ▴" : "Показать всё ▾"}</button>
      </div>
      {open && (
        <div style={{ marginTop: 8, display: "grid", gap: 10 }}>
          {items.map((x, i) => (
            <div key={i} style={{ background: "rgba(255,255,255,.55)", borderRadius: 6, padding: "6px 10px" }}>
              <div className="row" style={{ justifyContent: "space-between", gap: 8 }}>
                <b>{x.group}</b>
                <a href="#" style={{ fontSize: 12 }} onClick={(e) => { e.preventDefault(); onGo(x); }}>{x.tab === "positions" ? "перейти к позиции →" : "перейти к шапке →"}</a>
              </div>
              {x.fields.length > 0 && <div style={{ fontSize: 13, marginTop: 2 }}>Поля: {x.fields.join(", ")}</div>}
              {x.tech.length > 0 && <>
                <div style={{ fontSize: 13, marginTop: 4 }}>Обязательные технические характеристики ({x.tech.length}):</div>
                <ul style={{ margin: "2px 0 0", paddingLeft: 18, columns: "260px auto", columnGap: 24, fontSize: 13 }}>
                  {x.tech.map((t, j) => <li key={j} style={{ breakInside: "avoid" }}>{t}</li>)}
                </ul></>}
            </div>))}
        </div>)}
    </div>
  );
}
