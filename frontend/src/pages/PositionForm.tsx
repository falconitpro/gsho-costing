import { Fragment, useEffect, useMemo, useState } from "react";
import { api, ApiError, designation, fmtDate, fmtRub } from "../api";
import { DELIVERY_START, PRODUCTION_START, TERM_UNITS, type Position } from "../types";
import { collectFields, cmpCode, type RfqFieldData } from "../components/Questionnaire";

type Factory = { id: string; name: string; trademarks?: string[] };
type Tree = { factory_id: string; factory: string; trademarks?: string[]; types: { machine_type: string; models: { id: string; model: string; trademark?: string; executions: { id: string; name: string; params: number }[] }[] }[] }[];
type PriceRef = {
  factory_prices: { on: string; unit_price: number; currency: string; basis: string; production_term: { value?: number; unit?: string }; source: string }[] | null;
  client_prices: { on: string; deal_number: string; unit_price_rub: number | null; qty: number; outcome: string | null; kp_brand: string }[] | null;
};
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
  const [trademark, setTrademark] = useState(position?.trademark || "");
  // сопоставление «завод → торговые марки»: при выборе завода подставляется его марка
  const pickFactory = (id: string) => { setFactoryId(id); const f = factories.find((x) => x.id === id); set("0.7", f?.name || ""); setTrademark(f?.trademarks?.[0] || ""); };
  const marks = factories.find((x) => x.id === factoryId)?.trademarks || [];
  const [vals, setVals] = useState<Record<string, string>>({});
  const [optNames, setOptNames] = useState<Record<string, string>>({});
  const [reqs, setReqs] = useState<Record<string, string>>({});       // требования заказчика по кодам
  const [comms, setComms] = useState<Record<string, string>>({});     // комментарии по кодам
  const [open, setOpen] = useState<Record<number, boolean>>({ 0: true, 1: true, 13: true, 14: true });
  const [priceComment, setPriceComment] = useState("");
  const [advance, setAdvance] = useState({ pct: 30, days: 5 });
  const [prod, setProd] = useState<{ value: string; unit: string; fixed_date: string; start_event: string }>({ value: String(position?.production_term?.value ?? position?.production_days ?? 90), unit: position?.production_term?.unit || "calendar_days", fixed_date: position?.production_term?.fixed_date || "", start_event: position?.production_term?.start_event || "factory_contract" });
  const [dterm, setDterm] = useState<{ unit: string; start_event: string }>({ unit: position?.delivery_term?.unit || "calendar_days", start_event: position?.delivery_term?.start_event || "client_contract" });
  const [tree, setTree] = useState<Tree>([]);
  const [countries, setCountries] = useState<string[]>([]);
  const [execId, setExecId] = useState(position?.execution_id || "");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [ref, setRef] = useState<PriceRef | null>(null);

  useEffect(() => {
    api.get<Schema>("/api/deals/rfq/template").then(setSchema);
    api.get<Factory[]>("/api/refs/factories").then(setFactories);
    api.get<Tree>("/api/catalog/tree").then(setTree).catch(() => setTree([]));
    api.get<string[]>("/api/refs/countries").then(setCountries).catch(() => setCountries([]));
  }, []);
  useEffect(() => {
    if (!position) return;
    const v: Record<string, string> = {}, names: Record<string, string> = {};
    const rq: Record<string, string> = {}, cm: Record<string, string> = {};
    for (const f of collectFields(position)) {
      if (f.value != null && f.value !== "") v[f.code] = String(f.value); if (f.section_no === 12 && f.name) names[f.code] = f.name;
      if (f.requirement != null && f.requirement !== "") rq[f.code] = String(f.requirement); if (f.comment != null && f.comment !== "") cm[f.code] = String(f.comment);
    }
    v["1.1"] ??= position.machine_type; v["1.2"] ??= position.model; v["13.4"] ??= String(position.qty); v["13.5"] ??= String(position.unit_price);
    v["13.1"] ??= position.currency; v["13.2"] ??= position.basis; v["14.1"] ??= String(position.production_days); v["14.2"] ??= String(position.delivery_days);
    setVals(v); setOptNames(names); setReqs(rq); setComms(cm);
    // строки таблицы технических характеристик (в т.ч. правки в ней) — приоритетнее снимка импорта
    api.get<{ rows: { code: string; value_ru: string; customer_requirement_ru: string; comment_ru: string }[] }>(`/api/deals/${dealId}/positions/${position.id}/tech-params`)
      .then((r) => {
        const byCode = r.rows.filter((x) => x.code);
        setVals((s) => { const n = { ...s }; for (const x of byCode) if (x.value_ru) n[x.code] = x.value_ru; return n; });
        setReqs((s) => { const n = { ...s }; for (const x of byCode) if (x.customer_requirement_ru) n[x.code] = x.customer_requirement_ru; return n; });
        setComms((s) => { const n = { ...s }; for (const x of byCode) if (x.comment_ru) n[x.code] = x.comment_ru; return n; });
      }).catch(() => undefined);
  }, [position]);

  const bySection = useMemo(() => {
    const m = new Map<number, RfqFieldData[]>();
    for (const f of schema?.fields || []) { const n = f.section_no ?? 0; if (!m.has(n)) m.set(n, []); m.get(n)!.push(f); }
    for (const arr of m.values()) arr.sort((a, b) => cmpCode(a.code, b.code));
    return m;
  }, [schema]);

  // задача 7: справка при вводе позиции — последние цены и сроки завода (менеджер, КД, ГД), цены клиентам (КД, ГД)
  const refModel = vals["1.2"] || "";
  useEffect(() => {
    if (!factoryId || !refModel) { setRef(null); return; }
    const t = setTimeout(() => api.get<PriceRef>(`/api/catalog/price-reference?factory_id=${factoryId}&model=${encodeURIComponent(refModel)}${execId ? `&execution_id=${execId}` : ""}`).then(setRef).catch(() => setRef(null)), 400);
    return () => clearTimeout(t);
  }, [factoryId, refModel, execId]);

  const set = (code: string, v: string) => setVals((s) => ({ ...s, [code]: v }));
  const num = (code: string) => { const n = Number((vals[code] || "").replace(/\s/g, "").replace(",", ".")); return isNaN(n) ? null : n; };
  const valid = (factoryId || vals["0.7"]) && vals["1.2"] && (num("13.5") || 0) > 0 && (num("13.4") || 0) > 0;
  const filledCount = Object.values(vals).filter((v) => v !== "").length;
  const reqCount = Object.values(reqs).filter((v) => v !== "").length;

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
      const form_rows: { code: string; value: string; requirement: string; comment: string }[] = [];
      for (const f of schema.fields) {
        const raw = vals[f.code] ?? "", rq = reqs[f.code] ?? "", cm = comms[f.code] ?? "";
        form_rows.push({ code: f.code, value: raw, requirement: rq, comment: cm });
        if (raw === "" && rq === "" && cm === "") continue;
        const isNum = schema.numeric.includes(f.code);
        const value = raw === "" ? null : isNum && num(f.code) != null ? num(f.code) : raw;
        const entry = { code: f.code, section: f.section, section_no: f.section_no, name: f.section_no === 12 ? (optNames[f.code] || f.name) : f.name, name_zh: f.name_zh, unit: f.unit, value,
          requirement: rq || null, comment: cm || null, is_l: !!f.is_l };
        (f.is_l ? logistics_params : tech_params)[f.code] = entry;
        if (f.section_no === 12 && f.code !== "12.6") options.push({ code: f.code, name: optNames[f.code] || f.name || f.code, value: typeof value === "number" ? value : null });
      }
      const qty = num("13.4") || 1;
      const optPerUnit = options.reduce((s, o) => s + (o.value || 0), 0) || num("12.6") || 0;
      const cur = (vals["13.1"] || "CNY").toUpperCase().slice(0, 3);
      const body = {
        factory_id: fid, trademark: trademark.trim(), model: vals["1.2"], machine_type: vals["1.1"] || "", qty, unit_price: num("13.5"), currency: CURRENCIES.includes(cur) ? cur : "CNY",
        options, options_price: optPerUnit * qty, basis: vals["13.2"] || "FCA", delivery_point: vals["13.3"] || "",
        production_days: num("14.1") || 90, delivery_days: num("14.2") || 10, warranty_months: num("14.4"), warranty_hours: num("14.5"), zip_price: num("13.8"),
        production_term: prod.unit === "date" ? { unit: "date", fixed_date: prod.fixed_date || null, start_event: prod.start_event } : { value: Number(prod.value) || num("14.1") || 90, unit: prod.unit, start_event: prod.start_event },
        delivery_term: dterm,
        factory_payments: [{ no: 1, share_pct: advance.pct, event: "factory_contract", offset_days: advance.days }, { no: 2, share_pct: 100 - advance.pct, event: "readiness", offset_days: 0 }],
        execution_id: execId || null,
        price_comment: priceComment, tech_params, logistics_params, form_rows,
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
        <select value={factoryId} onChange={(e) => pickFactory(e.target.value)}>
          <option value="">— выбрать завод или ввести —</option>{factories.map((x) => <option key={x.id} value={x.id}>{x.name}{x.trademarks?.length ? ` (${x.trademarks.join(", ")})` : ""}</option>)}
        </select>
        {!factoryId && <input value={v} placeholder="новый завод: полное наименование" onChange={(e) => set(code, e.target.value)} />}
      </div>);
    if (code === "1.2") return (
      <div className="row" style={{ flexWrap: "wrap" }}>
        <input list="pf-marks" value={trademark} placeholder="торговая марка" style={{ width: 130 }} title="Торговая марка оборудования — по сопоставлению с заводом; можно изменить" onChange={(e) => setTrademark(e.target.value)} />
        <datalist id="pf-marks">{marks.map((m) => <option key={m} value={m} />)}</datalist>
        <input value={v} placeholder="модель" onChange={(e) => set(code, e.target.value)} />
        {(trademark || v) && <span className="muted" style={{ fontSize: 12 }}>обозначение: <b>{designation({ trademark, model: v })}</b></span>}
      </div>);
    if (code === "1.3") return (
      <select value={v} onChange={(e) => set(code, e.target.value)}>
        <option value="">—</option>
        {v && !countries.includes(v) && <option value={v}>{v} (не из списка)</option>}
        {countries.map((x) => <option key={x}>{x}</option>)}
      </select>);
    if (code === "13.1") return <select value={v || "CNY"} onChange={(e) => set(code, e.target.value)}>{CURRENCIES.map((c) => <option key={c}>{c}</option>)}</select>;
    if (code === "13.2") return <select value={v || "FCA"} onChange={(e) => set(code, e.target.value)}>{["EXW", "FCA", "CPT", "CIP", "DAP", "DDP"].map((b) => <option key={b}>{b}</option>)}</select>;
    const yesno = (f.unit || "").toLowerCase().startsWith("да/нет");
    if (yesno) return <select value={v} onChange={(e) => set(code, e.target.value)}><option value="">—</option><option>Да</option><option>Нет</option></select>;
    return <input style={{ width: "100%" }} value={v} type="text" inputMode={schema.numeric.includes(code) ? "decimal" : undefined} onChange={(e) => set(code, e.target.value)} />;
  };

  return (
    <div className="card" style={{ borderColor: "var(--accent)" }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 style={{ marginTop: 0 }}>{position ? `Позиция ${designation(position)}: правка полей опросника` : "Новая позиция: ввод полей опросника вручную"}</h2>
        <span className="muted">заполнено {filledCount} из {schema.fields.length}{reqCount ? ` · требований заказчика: ${reqCount}` : ""} · обязательные: завод, модель, кол-во, цена</span>
      </div>
      <p className="muted" style={{ marginTop: 0 }}>Те же поля, что в шаблоне RFQ ({schema.title || "RU-CN"}). Метка <span className="badge">L</span> — данные для логиста. Заполняйте то, что известно; остальное можно дополнить позже или импортом файла.</p>
      <div className="row" style={{ marginBottom: 8 }}>
        <button className="btn ghost" onClick={() => setOpen(Object.fromEntries([...bySection.keys()].map((k) => [k, true])))}>развернуть все разделы</button>
        <button className="btn ghost" onClick={() => setOpen({})}>свернуть</button>
        <a className="btn ghost" href="/api/deals/rfq/template/file">скачать пустой шаблон RFQ для завода</a>
      </div>
      {/* тот же вид, что у таблицы после загрузки файла: разделы, № / параметр / ед. / требования заказчика / значение / комментарий */}
      <div style={{ overflowX: "auto" }}>
        <table>
          <thead><tr><th style={{ width: 60 }}>№</th><th>Параметр</th><th style={{ width: 80 }}>Ед.</th><th>Требования заказчика</th><th>Значение</th><th>Комментарий</th></tr></thead>
          <tbody>
            {[...bySection.entries()].sort((a, b) => a[0] - b[0]).map(([no, fs]) => {
              const filledHere = fs.filter((f) => vals[f.code]).length;
              const title = (schema.sections.find((s) => s.no === no)?.title || fs[0].section || "").split(" / ")[0];
              return (
                <Fragment key={no}>
                  <tr style={{ cursor: "pointer", background: "var(--accent-soft)" }} onClick={() => setOpen((o) => ({ ...o, [no]: !o[no] }))}>
                    <td colSpan={6} style={{ fontWeight: 600 }}>{open[no] ? "▾" : "▸"} {no}. {title} <span className="muted" style={{ fontWeight: 400 }}>· заполнено {filledHere} из {fs.length}
                      {fs.some((f) => reqs[f.code]) && ` · требований заказчика: ${fs.filter((f) => reqs[f.code]).length}`}</span></td>
                  </tr>
                  {open[no] && fs.map((f) => {
                    const req = schema.required.includes(f.code), empty = !vals[f.code];
                    return (
                      <tr key={f.code} style={{ background: empty && req ? "#fde7e7" : empty ? "#fff6d6" : undefined }} title={f.name_zh}>
                        <td>{f.code} {f.is_l && <span className="badge" title="данные для логиста">L</span>}</td>
                        <td>{no === 12 && f.code !== "12.6"
                          ? <input style={{ width: "100%" }} placeholder="наименование опции" value={optNames[f.code] || ""} onChange={(e) => setOptNames({ ...optNames, [f.code]: e.target.value })} />
                          : <>{f.name || (no === 10 ? "Параметр рабочего оборудования" : "")}{req && <span style={{ color: "var(--accent)" }}> *</span>}</>}
                          {f.name_zh && <div className="muted" style={{ fontSize: 11 }}>{f.name_zh}</div>}</td>
                        <td className="muted">{f.unit && f.unit !== "—" ? f.unit.split(" / ")[0] : ""}</td>
                        <td><input style={{ width: "100%" }} value={reqs[f.code] || ""} onChange={(e) => setReqs({ ...reqs, [f.code]: e.target.value })} /></td>
                        <td>{input(f)}</td>
                        <td><input style={{ width: "100%" }} value={comms[f.code] || ""} onChange={(e) => setComms({ ...comms, [f.code]: e.target.value })} /></td>
                      </tr>);
                  })}
                </Fragment>);
            })}
          </tbody>
        </table>
      </div>
      <div className="grid cols-3" style={{ marginTop: 10 }}>
        <label className="f">Исполнение из каталога (заполняет технические характеристики; затем их можно изменить)
          <select value={execId} onChange={(e) => { setExecId(e.target.value); const found = tree.flatMap((f) => f.types.flatMap((t) => t.models.flatMap((m) => m.executions.map((x) => ({ f, t, m, x }))))).find((z) => z.x.id === e.target.value);
            if (found) { setFactoryId(found.f.factory_id); set("0.7", found.f.factory); setTrademark(found.m.trademark || found.f.trademarks?.[0] || ""); set("1.1", found.t.machine_type); set("1.2", found.m.model); } }}>
            <option value="">— не использовать —</option>
            {tree.map((f) => f.types.map((t) => t.models.map((m) => m.executions.map((x) => <option key={x.id} value={x.id}>{f.factory} › {t.machine_type} › {designation(m)} › {x.name} ({x.params} парам.)</option>))))}
          </select></label>
        <label className="f">Срок производства (задача 3): значение, единица, точка отсчёта
          <div className="row">
            {prod.unit !== "date" ? <input type="number" value={prod.value} style={{ width: 80 }} onChange={(e) => setProd({ ...prod, value: e.target.value })} /> : <input type="date" value={prod.fixed_date} onChange={(e) => setProd({ ...prod, fixed_date: e.target.value })} />}
            <select value={prod.unit} onChange={(e) => setProd({ ...prod, unit: e.target.value })}>{Object.entries(TERM_UNITS).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select>
            {prod.unit !== "date" && <select value={prod.start_event} onChange={(e) => setProd({ ...prod, start_event: e.target.value })}>{Object.entries(PRODUCTION_START).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select>}
          </div></label>
        <label className="f">Срок поставки клиенту: единицы и точка отсчёта (значение считает система: +10 %, до целой недели; КД может изменить)
          <div className="row">
            <select value={dterm.unit} onChange={(e) => setDterm({ ...dterm, unit: e.target.value })}>{["calendar_days", "working_days", "weeks"].map((k) => <option key={k} value={k}>{TERM_UNITS[k]}</option>)}</select>
            <select value={dterm.start_event} onChange={(e) => setDterm({ ...dterm, start_event: e.target.value })}>{Object.entries(DELIVERY_START).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select>
          </div></label>
        <label className="f">Условия оплаты завода (справка для КД): аванс, % / через дней после контракта с заводом (остаток — по готовности)
          <div className="row"><input type="number" value={advance.pct} onChange={(e) => setAdvance({ ...advance, pct: Number(e.target.value) })} /><input type="number" value={advance.days} onChange={(e) => setAdvance({ ...advance, days: Number(e.target.value) })} /></div></label>
        <label className="f">Комментарий к цене (если отклонение от истории выше порога)<input value={priceComment} onChange={(e) => setPriceComment(e.target.value)} /></label>
      </div>
      {ref && (ref.factory_prices?.length || ref.client_prices?.length) ? (
        <div className="card" style={{ marginTop: 10, background: "#fafafa" }}>
          <b>Справка по модели {refModel}</b>
          <div className="grid cols-2" style={{ marginTop: 6 }}>
            {ref.factory_prices && (
              <div><div className="muted">Предложения завода (последние)</div>
                {ref.factory_prices.length === 0 ? <div className="muted">истории нет</div> : (
                  <table><tbody>{ref.factory_prices.map((h, i) => (
                    <tr key={i}><td>{fmtDate(h.on)}</td><td className="num">{fmtRub(h.unit_price)} {h.currency}</td><td>{h.basis}</td>
                      <td className="muted">{h.production_term?.value != null ? `${h.production_term.value} ${TERM_UNITS[h.production_term.unit || "calendar_days"] || ""}` : "—"}</td></tr>))}</tbody></table>)}
              </div>)}
            {ref.client_prices && (
              <div><div className="muted">Цены клиентам (видят КД и ГД)</div>
                {ref.client_prices.length === 0 ? <div className="muted">КП по модели ещё не было</div> : (
                  <table><tbody>{ref.client_prices.map((h, i) => (
                    <tr key={i}><td>{fmtDate(h.on)}</td><td>{h.deal_number}</td><td className="num">{fmtRub(h.unit_price_rub)} RUB × {h.qty}</td>
                      <td>{h.outcome === "won" ? <span className="badge ok">выиграна</span> : h.outcome === "lost" ? <span className="badge bad">проиграна</span> : <span className="muted">{h.outcome || "—"}</span>}</td></tr>))}</tbody></table>)}
              </div>)}
          </div>
        </div>) : null}
      {err && <div className="alert error">{err}</div>}
      <div className="row" style={{ marginTop: 10 }}>
        <button className="btn" onClick={save} disabled={!valid || busy}>{position ? "Сохранить изменения" : "Добавить позицию"}</button>
        {onCancel && <button className="btn secondary" onClick={onCancel}>Отмена</button>}
      </div>
    </div>
  );
}
