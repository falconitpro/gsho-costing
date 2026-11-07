import { Fragment, useState, type ReactNode } from "react";

export type SpecRow = { key: string; no?: string | number; name: ReactNode; unit?: ReactNode; ref?: ReactNode; value: ReactNode; comment?: ReactNode; filled: boolean; required?: boolean };
export type SpecSection = { title: string; rows: SpecRow[] };

/** Таблица в том же виде, что таблица технических характеристик: разделы со сворачиванием и счётчиком,
 *  № / Параметр / Ед. / справка / Значение / Комментарий; незаполненные строки — жёлтым, обязательные — красным. */
export default function SpecTable({ sections, refTitle = "Справка", nameTitle = "Параметр", numbered = true, legend = true }:
  { sections: SpecSection[]; refTitle?: string; nameTitle?: string; numbered?: boolean; legend?: boolean }) {
  const [closed, setClosed] = useState<Record<string, boolean>>({});
  return (
    <div style={{ overflowX: "auto" }}>
      <style>{`.spec td input:not([type=checkbox]), .spec td select { width: 100%; padding: 4px; box-sizing: border-box; } .spec td { padding: 3px; vertical-align: middle; }`}</style>
      <table className="spec">
        <thead><tr><th style={{ width: 50 }}>№</th><th>{nameTitle}</th><th style={{ width: 70 }}>Ед.</th><th style={{ width: "18%" }}>{refTitle}</th><th style={{ width: "20%" }}>Значение</th><th style={{ width: "22%" }}>Комментарий</th></tr></thead>
        <tbody>
          {sections.map((s, si) => {
            const filled = s.rows.filter((r) => r.filled).length;
            const isClosed = !!closed[s.title];
            return (
              <Fragment key={s.title}>
                <tr style={{ background: "#f1f1f1", cursor: "pointer" }} onClick={() => setClosed({ ...closed, [s.title]: !isClosed })}>
                  <td colSpan={6} style={{ fontWeight: 700, padding: "6px 8px" }}>{isClosed ? "▸" : "▾"} {numbered ? `${si + 1}. ` : ""}{s.title}
                    <span className="muted" style={{ fontWeight: 400 }}> · заполнено {filled} из {s.rows.length}</span></td>
                </tr>
                {!isClosed && s.rows.map((r) => {
                  const bg = !r.filled && r.required ? "#ffe3e3" : !r.filled ? "#f4f4f4" : undefined;
                  return (
                    <tr key={r.key} style={{ background: bg }}>
                      <td className="muted">{r.no ?? `${si + 1}.${s.rows.indexOf(r) + 1}`}</td>
                      <td>{r.name}</td>
                      <td className="muted">{r.unit}</td>
                      <td style={{ fontSize: 12 }}>{r.ref}</td>
                      <td>{r.value}</td>
                      <td>{r.comment}</td>
                    </tr>);
                })}
              </Fragment>);
          })}
        </tbody>
      </table>
      {legend && <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>Цвета: жёлтый — не заполнено, красный — обязательное поле без значения. Разделы сворачиваются щелчком по заголовку.</div>}
    </div>
  );
}
