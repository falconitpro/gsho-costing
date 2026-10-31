import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../api";
import type { Position } from "../types";
import { collectFields, cmpCode, type RfqFieldData } from "../components/Questionnaire";

type Factory = { id: string; name: string };
type Schema = { title: string; sections: { no: number; title: string }[]; fields: RfqFieldData[]; required: string[]; numeric: string[] };

/** Коды опросника → поля позиции (как в импорте RFQ). */
const CORE: Record<string, string> = {
  "0.7": "factory", "1.1": "machine_type", "1.2": "model", "13.1": "currency", "13.2": "basis", "13.3": "delivery_point", "13.4": "qty",
  "13.5": "unit_price", "13.8": "zip_price", "14.1": "production_days", "14.2": "delivery_days", "14.4": "warranty_months", "14.5": "warranty_hours",
};
const CURRENCIES = ["CNY", "USD", "EUR", "RUB"];

/** Ручной ввод позиции по всем полям опросника RFQ (не все обязательные) или правка импортированной. */
export default function PositionForm({ dealId, position, onDone, onCancel }: { dealId: string; position?: Position; onDone: () => void; onCancel?: () => void }) {
  const [schema, setSchema] = useState<Schema | null>(null);
  const [factories, setFactories] = useState<Factory[]>([]);
  const [factoryId, setFactoryId] = useState(position?.factory_id || "");
  const [vals, setVals] = useState<Record<string, string>>({});
  const [optNames, setOptNames] = useState<Record<string, string>>({});
  const [open, setOpen] = useState<Record<number, boolean>>({ 0: true, 1: true, 13: true, 14: true });
  const [priceComment, setPriceComment] = useState("");
  const [advance, setAdvance] = useState({ pct: 30, days: 5 });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.get<Schema>("/api/deals/rfq/template").then(setSchema);
    api.get<Factory[]>("/api/refs/factories").then(setFactories);
  }, []);
  useEffect(() => {
    if (!position) return;
    const v: Record<string, string> = {}, names: Record<string, string> = {};
    for (const f of collectFields(position)) { if (f.value != null && f.value !== "") v[f.code] = String(f.value); if (f.section_no === 12 && f.name) names[f.code] = f.name; }
    v["1.1"] ??= position.machine_type; v["1.2"] ??= position.model; v["13.4"] ??= String(position.qty); v["13.5"] ??= String(position.unit_price);
    v["13.1"] ??= position.currency; v["13.2"] ??= position.basis; v["14.1"] ??= String(position.production_days); v["14.2"] ??= String(position.delivery_days);
    setVals(v); setOptNames(names);
  }, [position]);

  const bySection = useMemo(() => {
    const m = new Map<number, RfqFieldData[]>();
    for (const f of schema?.fields || []) { const n = f.section_no ?? 0; if (!m.has(n)) m.set(n, []); m.get(n)!.push(f); }
    for (const arr of m.values()) arr.sort((a, b) => cmpCode(a.code, b.code));
    return m;
  }, [schema]);

  const set = (code: string, v: string) => setVals((s) => ({ ...s, [code]: v }));
  const num = (code: string) => { const n = Number((vals[code] || "").replace(/\s/g, "").replace(",", ".")); return isNaN(n) ? null : n; };
  const valid = (factoryId || vals["0.7"]) && vals["1.2"] && (num("13.5") || 0) > 0 && (num("13.4") || 0) > 0;
  const filledCount = Object.values(vals).filter((v) => v !== "").length;

  async function save() {
    if (!schema) return;
    setBusy(true);
    try {
      let fid = factoryId;
      if (!fid && vals["0.7"]) {
        const existing = factories.find((f) => f.name.toLowerCase() === vals["0.7"].trim().toLowerCase());
        fid = existing ? existing.id : (await api.post<Factory>("/api/refs/factories", { name: vals["0.7"].trim(), address: vals["0.8"] || "" })).id;
      }
      const tech_params: Record<string, unknown> = {}, logistics_params: Record<string, unknown> = {};
      const options: { code: string; name: string; value: number | null }[] = [];
      for (const f of schema.fields) {
        const raw = vals[f.code]; if (raw == null || raw === "") continue;
        const isNum = schema.numeric.includes(f.code);
        const value = isNum && num(f.code) != null ? num(f.code) : raw;
        const entry = { code: f.code, section: f.section, section_no: f.section_no, name: f.section_no === 12 ? (optNames[f.code] || f.name) : f.name, name_zh: f.name_zh, unit: f.unit, value, is_l: !!f.is_l };
        (f.is_l ? logistics_params : tech_params)[f.code] = entry;
        if (f.section_no === 12 && f.code !== "12.6") options.push({ code: f.code, name: optNames[f.code] || f.name || f.code, value: typeof value === "number" ? value : null });
      }
      const qty = num("13.4") || 1;
      const optPerUnit = options.reduce((s, o) => s + (o.value || 0), 0) || num("12.6") || 0;
      const cur = (vals["13.1"] || "CNY").toUpperCase().slice(0, 3);
      const body = {
        factory_id: fid, model: vals["1.2"], machine_type: vals["1.1"] || "", qty, unit_price: num("13.5"), currency: CURRENCIES.includes(cur) ? cur : "CNY",
        options, options_price: optPerUnit * qty, basis: vals["13.2"] || "FCA", delivery_point: vals["13.3"] || "",
        production_days: num("14.1") || 90, delivery_days: num("14.2") || 10, warranty_months: num("14.4"), warranty_hours: num("14.5"), zip_price: num("13.8"),
        factory_payments: [{ no: 1, share_pct: advance.pct, event: "contract", offset_days: advance.days }, { no: 2, share_pct: 100 - advance.pct, event: "readiness", offset_days: 0 }],
        price_comment: priceComment, tech_params, logistics_params,
      };
      if (position) await api.patch(`/api/deals/${dealId}/positions/${position.id}`, body);
      else await api.post(`/api/deals/${dealId}/positions`, body);
      setErr(""); onDone();
    } catch (e) {
      const d = (e as ApiError).detail as { message?: string; price_reference?: { last_price: number; deviation_pct: number } };
      setErr((d?.message || (e as Error).message) + (d?.price_reference ? ` — последняя цена ${d.price_reference.last_price}, отклонение ${d.price_reference.deviation_pct} %` : ""));
    } finally { setBusy(false); }
  }

  if (!schema) return <div className="card muted">Загрузка полей опросника…</div>;

  const input = (f: RfqFieldData) => {
    const code = f.code, v = vals[code] ?? "";
    if (code === "0.7") return (
      <div className="row">
        <select value={factoryId} onChange={(e) => { setFactoryId(e.target.value); set("0.7", factories.find((x) => x.id === e.target.value)?.name || ""); }}>
          <option value="">— выбрать завод или ввести —</option>{factories.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
        </select>
        {!factoryId && <input value={v} placeholder="новый завод: полное наименование" onChange={(e) => set(code, e.target.value)} />}
      </div>);
    if (code === "13.1") return <select value={v || "CNY"} onChange={(e) => set(code, e.target.value)}>{CURRENCIES.map((c) => <option key={c}>{c}</option>)}</select>;
    if (code === "13.2") return <select value={v || "FCA"} onChange={(e) => set(code, e.target.value)}>{["EXW", "FCA", "CPT", "CIP", "DAP", "DDP"].map((b) => <option key={b}>{b}</option>)}</select>;
    const yesno = (f.unit || "").toLowerCase().startsWith("да/нет");
    if (yesno) return <select value={v} onChange={(e) => set(code, e.target.value)}><option value="">—</option><option>Да</option><option>Нет</option></select>;
    return <input value={v} type={schema.numeric.includes(code) ? "text" : "text"} inputMode={schema.numeric.includes(code) ? "decimal" : undefined} onChange={(e) => set(code, e.target.value)} />;
  };

  return (
    <div className="card" style={{ borderColor: "var(--accent)" }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 style={{ marginTop: 0 }}>{position ? `Позиция ${position.model}: правка полей опросника` : "Новая позиция: ввод полей опросника вручную"}</h2>
        <span className="muted">заполнено {filledCount} из {schema.fields.length} · обязательные: завод, модель, кол-во, цена</span>
      </div>
      <p className="muted" style={{ marginTop: 0 }}>Те же поля, что в шаблоне RFQ ({schema.title || "RU-ZH"}). Метка <span className="badge">L</span> — данные для логиста. Заполняйте то, что известно; остальное можно дополнить позже или импортом файла.</p>
      <div className="row" style={{ marginBottom: 8 }}>
        <button className="btn ghost" onClick={() => setOpen(Object.fromEntries([...bySection.keys()].map((k) => [k, true])))}>развернуть все разделы</button>
        <button className="btn ghost" onClick={() => setOpen({})}>свернуть</button>
        <a className="btn ghost" href="/api/deals/rfq/template/file">скачать пустой шаблон RFQ для завода</a>
      </div>
      {[...bySection.entries()].sort((a, b) => a[0] - b[0]).map(([no, fs]) => {
        const filledHere = fs.filter((f) => vals[f.code]).length;
        return (
          <details key={no} open={!!open[no]} onToggle={(e) => setOpen((o) => ({ ...o, [no]: (e.target as HTMLDetailsElement).open }))} style={{ marginBottom: 6 }}>
            <summary style={{ cursor: "pointer", fontWeight: 600 }}>{no}. {(schema.sections.find((s) => s.no === no)?.title || fs[0].section || "").split(" / ")[0]} <span className="muted">· {filledHere}/{fs.length}</span></summary>
            <div className="grid cols-3" style={{ marginTop: 8 }}>
              {fs.map((f) => (
                <label className="f" key={f.code} title={f.name_zh}>
                  <span>{f.code} {f.name || (no === 12 ? "Опция" : no === 10 ? "Параметр рабочего оборудования" : "")}{f.unit && f.unit !== "—" ? `, ${f.unit.split(" / ")[0]}` : ""} {f.is_l && <span className="badge">L</span>}{schema.required.includes(f.code) && <span style={{ color: "var(--accent)" }}> *</span>}</span>
                  {no === 12 && f.code !== "12.6" && <input placeholder="наименование опции" value={optNames[f.code] || ""} onChange={(e) => setOptNames({ ...optNames, [f.code]: e.target.value })} />}
                  {input(f)}
                </label>
              ))}
            </div>
          </details>
        );
      })}
      <div className="grid cols-3" style={{ marginTop: 10 }}>
        <label className="f">Оплата заводу: аванс, % / через дней после договора (остаток — по готовности)
          <div className="row"><input type="number" value={advance.pct} onChange={(e) => setAdvance({ ...advance, pct: Number(e.target.value) })} /><input type="number" value={advance.days} onChange={(e) => setAdvance({ ...advance, days: Number(e.target.value) })} /></div></label>
        <label className="f">Комментарий к цене (если отклонение от истории выше порога)<input value={priceComment} onChange={(e) => setPriceComment(e.target.value)} /></label>
      </div>
      {err && <div className="alert error">{err}</div>}
      <div className="row" style={{ marginTop: 10 }}>
        <button className="btn" onClick={save} disabled={!valid || busy}>{position ? "Сохранить изменения" : "Добавить позицию"}</button>
        {onCancel && <button className="btn secondary" onClick={onCancel}>Отмена</button>}
      </div>
    </div>
  );
}
