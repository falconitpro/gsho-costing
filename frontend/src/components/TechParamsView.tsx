import type { TechParamRow } from "../types";
import SpecTable, { type SpecSection } from "./SpecTable";

const CJK = /[　-〿㐀-鿿＀-￯]/;
const ru = (t: unknown) => { const s = String(t ?? "").trim(); return CJK.test(s) ? s.split(/\s*[/|]\s*/).filter((x) => x && !CJK.test(x)).join(" / ") : s; };

/** Таблица технических характеристик позиции только для просмотра (логист): всё, что ввёл менеджер, только по-русски. */
export default function TechParamsView({ rows }: { rows: TechParamRow[] }) {
  if (!rows.length) return <div className="muted">Таблица технических характеристик ещё не заполнена менеджером.</div>;
  const sections: SpecSection[] = [];
  for (const r of [...rows].sort((a, b) => a.no - b.no)) {
    const title = ru(r.section) || "Дополнительные параметры";
    let sec = sections.find((s) => s.title === title);
    if (!sec) { sec = { title, rows: [] }; sections.push(sec); }
    const val = ru(r.value_ru) || (r.value_zh ? "нет перевода — уточните у менеджера" : "");
    sec.rows.push({
      key: r.id || `${r.code}-${r.no}`, no: r.code || r.no, name: ru(r.name_ru) || r.code, unit: ru(r.unit),
      ref: ru(r.customer_requirement_ru), value: val ? <b>{val}</b> : <span className="muted">не указано</span>, comment: ru(r.comment_ru),
      filled: !!(r.value_ru || r.value_zh), required: r.required && !r.local_ru,
    });
  }
  const filled = rows.filter((r) => r.value_ru || r.value_zh).length;
  return (
    <div>
      <div className="muted" style={{ marginBottom: 4 }}>Заполнено {filled} из {rows.length} · таблица, которую ведёт менеджер (только просмотр)</div>
      <SpecTable sections={sections} refTitle="Требования заказчика" numbered={false} />
    </div>
  );
}
