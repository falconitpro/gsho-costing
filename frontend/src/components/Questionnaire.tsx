import { Fragment, useState } from "react";
import type { Position } from "../types";

export type RfqFieldData = {
  code: string; section?: string; section_no?: number; name: string; name_zh?: string; unit?: string;
  requirement?: unknown; value?: unknown; value_ru?: unknown; value_zh?: unknown; comment?: unknown; is_l?: boolean;
};

/** Собирает все поля опросника позиции: полный снимок импорта (_rfq.fields) + ручные правки (tech_params/logistics_params). */
export function collectFields(p: Position): RfqFieldData[] {
  const tp = (p.tech_params || {}) as Record<string, unknown>;
  const lp = (p.logistics_params || {}) as Record<string, RfqFieldData>;
  const snap = (tp._rfq as { fields?: Record<string, RfqFieldData> } | undefined)?.fields || {};
  const merged: Record<string, RfqFieldData> = { ...snap };
  for (const [code, f] of Object.entries(tp)) if (!code.startsWith("_") && f && typeof f === "object") merged[code] = { ...(merged[code] || {}), ...(f as RfqFieldData), code };
  for (const [code, f] of Object.entries(lp)) if (f && typeof f === "object") merged[code] = { ...(merged[code] || {}), ...f, code, is_l: true };
  // тип машины и модель — всегда текущие значения позиции (могли поменять в таблице или карточке после импорта)
  if (merged["1.1"] && p.machine_type) merged["1.1"] = { ...merged["1.1"], value: p.machine_type };
  if (merged["1.2"] && p.model) merged["1.2"] = { ...merged["1.2"], value: p.model };
  return Object.values(merged).sort((a, b) => cmpCode(a.code, b.code));
}

export function cmpCode(a: string, b: string) {
  const pa = a.split(".").map(Number), pb = b.split(".").map(Number);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) { const d = (pa[i] ?? -1) - (pb[i] ?? -1); if (d) return d; }
  return 0;
}

const SECTION_TITLES: Record<number, string> = {
  0: "Сведения о запросе и поставщике", 1: "Общие данные", 2: "Дизельный двигатель", 3: "Система выпуска и нейтрализации", 4: "Трансмиссия",
  5: "Мосты", 6: "Тормозная система", 7: "Гидросистема и рулевое управление", 8: "Электрооборудование", 9: "Кабина и безопасность",
  10: "Рабочее оборудование", 11: "Комплектация и инструмент", 12: "Дополнительные опции", 13: "Цена и коммерческие условия", 14: "Сроки, гарантия, документация",
};

export const fmtVal = (v: unknown) => v == null || v === "" ? "—" : typeof v === "number" ? new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 }).format(v) : String(v);

/** Таблица полей опросника по разделам. onlyL — только поля логиста; showEmpty — показывать и незаполненные. */
export default function Questionnaire({ position, onlyL = false, showEmpty = false, title }: { position: Position; onlyL?: boolean; showEmpty?: boolean; title?: string }) {
  const [all, setAll] = useState(showEmpty);
  const fields = collectFields(position).filter((f) => (!onlyL || f.is_l) && (all || (f.value != null && f.value !== "")));
  const meta = ((position.tech_params || {}) as { _rfq?: { filled?: number; total?: number; version?: string }; _source_file?: string });
  const bySection = new Map<number, RfqFieldData[]>();
  for (const f of fields) { const n = f.section_no ?? Number(f.code.split(".")[0]); if (!bySection.has(n)) bySection.set(n, []); bySection.get(n)!.push(f); }
  if (!fields.length) return <div className="muted">{onlyL ? "Технические данные для логиста не заполнены" : "Данные опросника отсутствуют"}</div>;
  if (onlyL) return <LogisticsView fields={fields} all={all} setAll={setAll} />;
  return (
    <div>
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 6 }}>
        <span className="muted">{title || (onlyL ? "Поля с меткой L — данные для расчёта логистики" : "Опросник RFQ")}
          {meta._rfq && <> · заполнено {meta._rfq.filled} из {meta._rfq.total}</>}{meta._source_file && <> · источник: {meta._source_file}</>}</span>
        {!showEmpty && <label className="row muted" style={{ fontSize: 12 }}><input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> показать незаполненные</label>}
      </div>
      {[...bySection.entries()].sort((a, b) => a[0] - b[0]).map(([no, fs]) => (
        <details key={no} open style={{ marginBottom: 6 }}>
          <summary style={{ cursor: "pointer", fontWeight: 600 }}>{no}. {fs[0].section?.split(" / ")[0] || SECTION_TITLES[no] || ""} <span className="muted">({fs.length})</span></summary>
          <table style={{ marginTop: 4 }}>
            <thead><tr><th style={{ width: 60 }}>Код</th><th>Параметр</th><th style={{ width: 90 }}>Ед.</th><th>Требование заказчика</th><th>Значение завода</th><th>Комментарий</th></tr></thead>
            <tbody>{fs.map((f) => (
              <tr key={f.code} style={{ opacity: f.value == null || f.value === "" ? 0.55 : 1 }}>
                <td>{f.code} {f.is_l && <span className="badge" title="данные для логиста">L</span>}</td>
                <td>{f.name}{f.name_zh && <div className="muted" style={{ fontSize: 11 }}>{f.name_zh}</div>}</td>
                <td className="muted">{(f.unit || "").split(" / ")[0]}</td>
                <td className="muted">{fmtVal(f.requirement)}</td>
                <td><b>{fmtVal(f.value)}</b>{f.value_zh != null && f.value_zh !== "" && String(f.value_zh) !== String(f.value) && <div className="muted" style={{ fontSize: 11 }}>{String(f.value_zh)}</div>}</td>
                <td className="muted">{fmtVal(f.comment)}</td>
              </tr>))}</tbody>
          </table>
        </details>
      ))}
    </div>
  );
}

const CJK = /[\u3000-\u303f\u3400-\u9fff\uff00-\uffef]/;
/** Убирает китайскую часть: «Тип машины / 机器类型» → «Тип машины», «мм / 毫米» → «мм». */
const ruOnly = (t: unknown) => {
  const s = String(t ?? "").trim();
  if (!CJK.test(s)) return s;
  const parts = s.split(/\s*[/|]\s*/).filter((x) => x && !CJK.test(x));
  return parts.join(" / ") || "";
};

/** Данные для логиста (метка L): одна таблица по разделам, только по-русски — параметр, значение, единица. */
function LogisticsView({ fields, all, setAll }: { fields: RfqFieldData[]; all: boolean; setAll: (v: boolean) => void }) {
  const bySection = new Map<number, RfqFieldData[]>();
  for (const f of fields) { const n = f.section_no ?? Number(f.code.split(".")[0]); if (!bySection.has(n)) bySection.set(n, []); bySection.get(n)!.push(f); }
  const filled = fields.filter((f) => f.value != null && f.value !== "").length;
  const val = (f: RfqFieldData) => {
    const v = f.value_ru != null && f.value_ru !== "" && typeof f.value !== "number" ? f.value_ru : f.value != null && f.value !== "" ? f.value : f.value_ru;
    if (v == null || v === "") return "";
    return typeof v === "number" ? fmtVal(v) : ruOnly(v) || "нет перевода — уточните у менеджера";
  };
  return (
    <div>
      <style>{`.lview { border-collapse: collapse; width: 100%; } .lview td { padding: 5px 8px; border-bottom: 1px solid #eee; }
        .lview tr.sec td { background: var(--accent-soft); font-weight: 600; padding-top: 7px; } .lview tbody tr:not(.sec):nth-child(even) td { background: #fbfbfb; }
        .lview td.v { font-weight: 600; text-align: right; white-space: nowrap; } .lview td.u { color: #777; width: 70px; }`}</style>
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 4 }}>
        <span className="muted">Заполнено {filled} из {fields.length}</span>
        <label className="row muted" style={{ fontSize: 12 }}><input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> показать незаполненные</label>
      </div>
      <table className="lview">
        <tbody>
          {[...bySection.entries()].sort((a, b) => a[0] - b[0]).map(([no, fs]) => (
            <Fragment key={no}>
              <tr className="sec"><td colSpan={3}>{no}. {ruOnly(fs[0].section?.split(" / ")[0]) || SECTION_TITLES[no] || ""}</td></tr>
              {fs.map((f) => {
                const v = val(f);
                return (
                  <tr key={f.code}>
                    <td>{ruOnly(f.name) || f.code}</td>
                    <td className="v">{v || <span className="muted" style={{ fontWeight: 400 }}>не указано</span>}</td>
                    <td className="u">{ruOnly((f.unit || "").split(" / ")[0]).replace(/^—$/, "")}</td>
                  </tr>);
              })}
            </Fragment>))}
        </tbody>
      </table>
    </div>
  );
}
