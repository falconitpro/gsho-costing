import { Fragment, type ReactNode, useEffect, useMemo, useState } from "react";
import { api, ApiError, designation, fmtDate, fmtRub, ru } from "../api";
import { DELIVERY_START, PRODUCTION_START, TERM_UNITS, type Position } from "../types";
import { collectFields, cmpCode, type RfqFieldData } from "../components/Questionnaire";
import { UndoButton, useUndoHistory } from "../undo";

type Factory = { id: string; name: string; trademarks?: string[] };
type Tree = { factory_id: string; factory: string; trademarks?: string[]; types: { machine_type: string; models: { id: string; model: string; trademark?: string; executions: { id: string; name: string; params: number }[] }[] }[] }[];
type PriceRef = {
  factory_prices: { on: string; unit_price: number; currency: string; basis: string; production_term: { value?: number; unit?: string }; source: string }[] | null;
  client_prices: { on: string; deal_number: string; unit_price_rub: number | null; qty: number; outcome: string | null; kp_brand: string }[] | null;
};
type ReqFields = { create: string[]; calc: string[]; fields: Record<string, { title: string; code: string }> };
type Schema = { title: string; sections: { no: number; title: string }[]; fields: RfqFieldData[]; required: string[]; numeric: string[]; required_fields?: ReqFields };

/** Поле формы позиции: подпись (со звёздочкой / «для расчёта») и поле ввода на всю ширину ячейки сетки. Вне компонента формы —
 *  иначе React пересоздаёт поле при каждом вводе и теряется фокус. */
function PfField({ title, mark, missing, wide, full, children }: { title: string; mark?: ReactNode; missing?: boolean; wide?: boolean; full?: boolean; children: ReactNode }) {
  return <label className={"pf-field" + (wide ? " pf-wide" : "") + (full ? " pf-full" : "") + (missing ? " pf-missing" : "")}><span className="pf-label">{title}{mark}</span>{children}</label>;
}

// поля, вынесенные в блоки «Что нужно клиенту» и «Ответ завода», в таблице параметров не повторяются
const OWN_CODES = new Set(["0.7", "1.1", "1.2", "13.1", "13.2", "13.3", "13.4", "13.5", "14.1", "14.4", "14.5"]);
type Extra = { id?: string; name: string; unit: string; req: string; val: string; comm: string };
let xSeq = 0;
const newKey = () => `x${++xSeq}`;

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
  const [open, setOpen] = useState<Record<number, boolean>>({ 1: true });
  // D-070: свои строки и порядок строк по разделам. Токен строки — «c:<код поля>» или «x:<ключ своей строки>»
  const [order, setOrder] = useState<Record<number, string[]>>({});
  const [extras, setExtras] = useState<Record<string, Extra>>({});
  const [blockNames, setBlockNames] = useState<Record<number, string>>({});   // блоки шаблона без номера («Двигатель») — разделы 100+
  const [techOrder, setTechOrder] = useState<{ code: string; no: number; id?: string; name_ru: string; unit: string; section: string; value_ru: string; customer_requirement_ru: string; comment_ru: string }[] | null>(null);
  const [priceComment, setPriceComment] = useState("");
  const [advance, setAdvance] = useState({ pct: 30, days: 5 });
  const [prod, setProd] = useState<{ value: string; unit: string; fixed_date: string; start_event: string }>({ value: position?.production_term?.value != null ? String(position.production_term.value) : "", unit: position?.production_term?.unit || "calendar_days", fixed_date: position?.production_term?.fixed_date || "", start_event: position?.production_term?.start_event || "factory_contract" });
  const prodFixed = !!position?.production_term?.fixed;   // зафиксирован КД / ЗГД — меняется только на экране «Расчёт и утверждение»
  const [dterm, setDterm] = useState<{ unit: string; start_event: string }>({ unit: position?.delivery_term?.unit || "calendar_days", start_event: position?.delivery_term?.start_event || "client_contract" });
  const [tree, setTree] = useState<Tree>([]);
  const [countries, setCountries] = useState<string[]>([]);
  const [execId, setExecId] = useState(position?.execution_id || "");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [ref, setRef] = useState<PriceRef | null>(null);
  const [types, setTypes] = useState<string[]>([]);
  // D-069: ответ завода (завод, модель, цена, сроки) часто приходит позже — блок свёрнут, пока данных завода нет
  const [factoryOpen, setFactoryOpen] = useState(!!(position && (position.factory_id || position.unit_price || position.model)));

  useEffect(() => {
    api.get<Schema>("/api/deals/rfq/template").then(setSchema);
    api.get<Factory[]>("/api/refs/factories").then(setFactories);
    api.get<Tree>("/api/catalog/tree").then(setTree).catch(() => setTree([]));
    api.get<string[]>("/api/refs/countries").then(setCountries).catch(() => setCountries([]));
    api.get<{ name: string }[]>("/api/refs/machine-types").then((r) => setTypes(r.map((x) => x.name))).catch(() => setTypes([]));
  }, []);
  // «Отменить последнее действие»: все поля формы
  const history = useUndoHistory({ vals, reqs, comms, optNames, factoryId, trademark, execId, prod, dterm, advance, priceComment, order, extras },
    (st) => { setVals(st.vals); setReqs(st.reqs); setComms(st.comms); setOptNames(st.optNames); setFactoryId(st.factoryId); setTrademark(st.trademark);
      setExecId(st.execId); setProd(st.prod); setDterm(st.dterm); setAdvance(st.advance); setPriceComment(st.priceComment); setOrder(st.order); setExtras(st.extras); }, position?.id || "new");
  useEffect(() => {
    if (!position) return;
    history.accept();
    const v: Record<string, string> = {}, names: Record<string, string> = {};
    const rq: Record<string, string> = {}, cm: Record<string, string> = {};
    for (const f of collectFields(position)) {
      if (f.value != null && f.value !== "") v[f.code] = String(f.value); if (f.section_no === 12 && f.name) names[f.code] = f.name;
      if (f.requirement != null && f.requirement !== "") rq[f.code] = String(f.requirement); if (f.comment != null && f.comment !== "") cm[f.code] = String(f.comment);
    }
    v["1.1"] ??= position.machine_type; v["1.2"] ??= position.model; v["13.4"] ??= String(position.qty); if (position.unit_price) v["13.5"] ??= String(position.unit_price);
    v["13.1"] ??= position.currency; v["13.2"] ??= position.basis; if (position.production_term?.value) v["14.1"] ??= String(position.production_days); v["14.2"] ??= String(position.delivery_days);
    setVals(v); setOptNames(names); setReqs(rq); setComms(cm);
    const withData = new Set(collectFields(position).filter((f) => (f.value != null && f.value !== "") || (f.requirement != null && f.requirement !== "")).map((f) => f.section_no ?? 0));
    setOpen((o) => ({ ...o, ...Object.fromEntries([...withData].map((n) => [n, true])) }));
    // строки таблицы технических характеристик (в т.ч. правки в ней) — приоритетнее снимка импорта
    api.get<{ rows: { code: string; no: number; id?: string; name_ru: string; unit: string; section: string; value_ru: string; customer_requirement_ru: string; comment_ru: string }[] }>(`/api/deals/${dealId}/positions/${position.id}/tech-params`)
      .then((r) => {
        history.accept();
        setTechOrder(r.rows);
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
  const fieldByCode = useMemo(() => new Map((schema?.fields || []).map((f) => [f.code, f])), [schema]);
  useEffect(() => {
    if (!schema || (position && techOrder === null)) return;
    const techNo = new Map((techOrder || []).filter((t) => t.code).map((t) => [t.code, t.no]));
    const ord: Record<number, string[]> = {}, ex: Record<string, Extra> = {};
    const keyed: Record<number, { t: string; n: number }[]> = {};
    for (const [no, fs] of bySection.entries()) {
      let last = 0;
      keyed[no] = fs.filter((f) => !OWN_CODES.has(f.code)).map((f) => { const n = techNo.get(f.code) ?? last + 0.001; last = n; return { t: "c:" + f.code, n }; });
    }
    const named: Record<string, number> = {};
    for (const t of techOrder || []) {   // строки без кода: в раздел RFQ по номеру в названии, иначе — в свой блок под его названием
      if (t.code || !(t.name_ru || t.value_ru || t.customer_requirement_ru)) continue;
      const m = /^(\d+)\./.exec(t.section || ""), bn = (t.section || "").trim();
      const sec = m && keyed[Number(m[1])] ? Number(m[1]) : bn && bn !== "Дополнительные параметры" ? (named[bn] ??= 100 + Object.keys(named).length) : 99;
      const k = newKey(); ex[k] = { id: t.id, name: t.name_ru, unit: t.unit, req: t.customer_requirement_ru, val: t.value_ru, comm: t.comment_ru };
      (keyed[sec] ||= []).push({ t: "x:" + k, n: t.no });
    }
    for (const [no, arr] of Object.entries(keyed)) ord[Number(no)] = arr.sort((a, b) => a.n - b.n).map((x) => x.t);
    setOrder(ord); setExtras(ex); setBlockNames(Object.fromEntries(Object.entries(named).map(([n, k]) => [k, n]))); history.accept();
  }, [schema, bySection, techOrder]);

  // задача 7: справка при вводе позиции — последние цены и сроки завода (менеджер, КД, ЗГД), цены клиентам (КД, ЗГД)
  const refModel = vals["1.2"] || "";
  useEffect(() => {
    if (!factoryId || !refModel) { setRef(null); return; }
    const t = setTimeout(() => api.get<PriceRef>(`/api/catalog/price-reference?factory_id=${factoryId}&model=${encodeURIComponent(refModel)}${execId ? `&execution_id=${execId}` : ""}`).then(setRef).catch(() => setRef(null)), 400);
    return () => clearTimeout(t);
  }, [factoryId, refModel, execId]);

  const set = (code: string, v: string) => {
    setVals((s) => ({ ...s, [code]: v }));
    // строка 14.1 таблицы и поле «Срок производства» — одно значение (D-065): ввод в таблице = календарные дни
    if (code === "14.1" && !prodFixed && Number(v) > 0) setProd((p) => (p.unit === "date" ? p : { ...p, value: v, unit: "calendar_days" }));
  };
  const setProdVal = (patch: Partial<typeof prod>) => {
    const next = { ...prod, ...patch }; setProd(next);
    if (next.unit === "calendar_days" && Number(next.value) > 0) setVals((s) => ({ ...s, "14.1": next.value }));
  };
  const num = (code: string) => { const n = Number((vals[code] || "").replace(/\s/g, "").replace(",", ".")); return isNaN(n) ? null : n; };
  // свои строки и порядок строк — на сервер (D-070)
  const customPayload = () => {
    const custom_rows: { id?: string; name: string; unit: string; requirement: string; value: string; comment: string; section_no: number; section: string }[] = [];
    const row_order: string[] = [];
    for (const no of Object.keys(order).map(Number).sort((a, b) => a - b)) for (const t of order[no]) {
      if (t.startsWith("x:")) { const e = extras[t.slice(2)]; if (!e) continue; row_order.push("x:" + custom_rows.length);
        custom_rows.push({ id: e.id, name: e.name, unit: e.unit, requirement: e.req, value: e.val, comment: e.comm, section_no: no, section: blockNames[no] || "" }); }
      else row_order.push(t);
    }
    return { custom_rows, row_order };
  };
  const moveTok = (no: number, idx: number, d: -1 | 1) => setOrder((o) => { const a = [...(o[no] || [])]; const j = idx + d; if (j < 0 || j >= a.length) return o; [a[idx], a[j]] = [a[j], a[idx]]; return { ...o, [no]: a }; });
  const insTok = (no: number, idx: number) => { const k = newKey(); setExtras((e) => ({ ...e, [k]: { name: "", unit: "", req: "", val: "", comm: "" } }));
    setOrder((o) => { const a = [...(o[no] || [])]; a.splice(idx + 1, 0, "x:" + k); return { ...o, [no]: a }; }); setOpen((o) => ({ ...o, [no]: true })); setFocusKey(k); };
  const delTok = (no: number, idx: number) => setOrder((o) => ({ ...o, [no]: (o[no] || []).filter((_, j) => j !== idx) }));
  const updX = (k: string, patch: Partial<Extra>) => setExtras((e) => ({ ...e, [k]: { ...e[k], ...patch } }));
  const [focusKey, setFocusKey] = useState<string | null>(null);
  useEffect(() => { if (focusKey) { (document.querySelector(`[data-x="${focusKey}"]`) as HTMLInputElement | null)?.focus(); setFocusKey(null); } }, [focusKey, order]);

  // обязательные поля — «Настройки → Заявка» (D-069): при создании и перед расчётом
  const rq = schema?.required_fields || { create: ["machine_type", "qty"], calc: ["factory", "model", "qty", "unit_price", "currency"], fields: {} as ReqFields["fields"] };
  const filled: Record<string, boolean> = {
    machine_type: !!(vals["1.1"] || "").trim(), qty: (num("13.4") || 0) > 0, factory: !!(factoryId || (vals["0.7"] || "").trim()), model: !!(vals["1.2"] || "").trim(),
    unit_price: (num("13.5") || 0) > 0 || !!execId, currency: true, basis: true, delivery_point: !!(vals["13.3"] || "").trim(),
    production_term: prod.unit === "date" ? !!prod.fixed_date : Number(prod.value) > 0, warranty: !!(num("14.4") || num("14.5")),
  };
  const missingCreate = rq.create.filter((k) => !filled[k]).map((k) => rq.fields[k]?.title || k);
  const missingCalc = rq.calc.filter((k) => !filled[k]).map((k) => rq.fields[k]?.title || k);
  const valid = missingCreate.length === 0;
  const factoryMissing = rq.calc.filter((k) => !filled[k] && !rq.create.includes(k)).map((k) => rq.fields[k]?.title || k);
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
        factory_id: fid || null, trademark: trademark.trim(), model: vals["1.2"] || "", machine_type: vals["1.1"] || "", qty, unit_price: num("13.5") || null, currency: CURRENCIES.includes(cur) ? cur : "CNY",
        options, options_price: optPerUnit * qty, basis: vals["13.2"] || "FCA", delivery_point: vals["13.3"] || "",
        production_days: num("14.1") || null, delivery_days: num("14.2") || 10, warranty_months: num("14.4"), warranty_hours: num("14.5"), zip_price: num("13.8"),
        // срок неизвестен до ответа завода — не подставляем «90 дней» молча (D-065, D-069)
        production_term: prod.unit === "date" ? (prod.fixed_date ? { unit: "date", fixed_date: prod.fixed_date, start_event: prod.start_event } : {})
          : (Number(prod.value) > 0 ? { value: Number(prod.value), unit: prod.unit, start_event: prod.start_event } : {}),
        delivery_term: dterm,
        factory_payments: [{ no: 1, share_pct: advance.pct, event: "factory_contract", offset_days: advance.days }, { no: 2, share_pct: 100 - advance.pct, event: "readiness", offset_days: 0 }],
        execution_id: execId || null,
        price_comment: priceComment, tech_params, logistics_params, form_rows, ...customPayload(),
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

  const OWN = OWN_CODES;
  const mark = (k: string) => rq.create.includes(k) ? <span className="pf-req" title="обязательно при сохранении"> *</span>
    : rq.calc.includes(k) ? <span className="pf-calc" title="нужно перед отправкой на расчёт">для расчёта</span> : null;
  const fp = (k: string) => ({ mark: mark(k), missing: rq.create.includes(k) && !filled[k] });

  const input = (f: RfqFieldData) => {
    const code = f.code, v = vals[code] ?? "";
    if (code === "1.3") return (
      <select value={v} onChange={(e) => set(code, e.target.value)}>
        <option value="">—</option>
        {v && !countries.includes(v) && <option value={v}>{v} (не из списка)</option>}
        {countries.map((x) => <option key={x}>{x}</option>)}
      </select>);
    const yesno = (f.unit || "").toLowerCase().startsWith("да/нет");
    if (yesno) return <select value={v} onChange={(e) => set(code, e.target.value)}><option value="">—</option><option>Да</option><option>Нет</option></select>;
    return <input value={v} type="text" inputMode={schema.numeric.includes(code) ? "decimal" : undefined} onChange={(e) => set(code, e.target.value)} />;
  };
  const sections = Object.keys(order).map(Number).sort((a, b) => a - b).map((no) => [no, order[no]] as const).filter(([, ts]) => ts.length > 0);
  const secTitle = (no: number) => no >= 100 ? blockNames[no] || "Блок" : no === 99 ? "Дополнительные параметры" : ru((schema.sections.find((x) => x.no === no)?.title || bySection.get(no)?.[0]?.section || "").split(" / ")[0]);

  return (
    <div className="card pf">
      <div className="pf-head">
        <div>
          <h2>{position ? `Позиция ${designation(position) || position.machine_type || ""}` : "Новая позиция"}</h2>
          <div className="muted">Сначала — что нужно клиенту и его требования. Завод, модель, цену и сроки можно внести позже, когда придёт ответ завода.</div>
        </div>
        <div className="pf-stats">
          <span>требований клиента: <b>{reqCount}</b></span><span>заполнено полей: <b>{filledCount}</b></span>
          <UndoButton h={history} />
        </div>
      </div>

      <section className="pf-block">
        <h3><span className="pf-num">1</span> Что нужно клиенту</h3>
        <div className="pf-grid">
          <PfField {...fp("machine_type")} title="Тип техники"><input list="pf-types" value={vals["1.1"] || ""} placeholder="например, самосвал шахтный" onChange={(e) => set("1.1", e.target.value)} />
            <datalist id="pf-types">{types.map((t) => <option key={t} value={t} />)}</datalist></PfField>
          <PfField {...fp("qty")} title="Количество, шт."><input type="number" min="1" value={vals["13.4"] || ""} onChange={(e) => set("13.4", e.target.value)} /></PfField>
          <PfField {...fp("delivery_point")} title="Пункт поставки"><input value={vals["13.3"] || ""} placeholder="город, объект" onChange={(e) => set("13.3", e.target.value)} /></PfField>
          <PfField title="Исполнение из каталога (необязательно) — подтянет характеристики, фото, опции, ТН ВЭД, последние цену и срок завода" full>
            <select value={execId} onChange={(e) => { setExecId(e.target.value); const found = tree.flatMap((f) => f.types.flatMap((t) => t.models.flatMap((m) => m.executions.map((x) => ({ f, t, m, x }))))).find((z) => z.x.id === e.target.value);
              if (found) { setFactoryId(found.f.factory_id); set("0.7", found.f.factory); setTrademark(found.m.trademark || found.f.trademarks?.[0] || ""); set("1.1", found.t.machine_type); set("1.2", found.m.model); setFactoryOpen(true); } }}>
              <option value="">— не использовать —</option>
              {tree.map((f) => f.types.map((t) => t.models.map((m) => m.executions.map((x) => <option key={x.id} value={x.id}>{f.factory} › {t.machine_type} › {designation(m)} › {x.name} ({x.params} парам.)</option>))))}
            </select></PfField>
        </div>
      </section>

      <section className="pf-block">
        <div className="pf-block-head">
          <h3><span className="pf-num">2</span> Требования клиента и технические характеристики</h3>
          <div className="row" style={{ gap: 6 }}>
            <button className="btn ghost" onClick={() => setOpen(Object.fromEntries(sections.map(([k]) => [k, true])))}>развернуть все</button>
            <button className="btn ghost" onClick={() => setOpen({})}>свернуть</button>
            <a className="btn ghost" href="/api/deals/rfq/template/file">пустой шаблон RFQ</a>
          </div>
        </div>
        <p className="muted pf-hint">Вносите то, что прислал клиент, в колонку «Требование клиента». Значения завода — из ответа завода (можно позже или импортом файла).</p>
        <div className="pf-table-wrap">
          <table className="pf-table">
            <colgroup><col style={{ width: "5%" }} /><col style={{ width: "25%" }} /><col style={{ width: "7%" }} /><col style={{ width: "20%" }} /><col style={{ width: "19%" }} /><col style={{ width: "15%" }} /><col style={{ width: 112 }} /></colgroup>
            <thead><tr><th>№</th><th>Параметр</th><th>Ед.</th><th>Требование клиента</th><th>Значение завода</th><th>Комментарий</th><th /></tr></thead>
            <tbody>
              {sections.map(([no, toks]) => {
                const codes = toks.filter((t) => t.startsWith("c:")).map((t) => t.slice(2));
                const xs = toks.filter((t) => t.startsWith("x:")).map((t) => extras[t.slice(2)]).filter(Boolean);
                const reqHere = codes.filter((c) => reqs[c]).length + xs.filter((x) => x.req).length;
                const valHere = codes.filter((c) => vals[c]).length + xs.filter((x) => x.val).length;
                return (
                  <Fragment key={no}>
                    <tr className="pf-sec" onClick={() => setOpen((o) => ({ ...o, [no]: !o[no] }))}>
                      <td colSpan={6}>{open[no] ? "▾" : "▸"} {no >= 99 ? "" : `${no}. `}{secTitle(no)} <span className="muted">· требований {reqHere} · значений завода {valHere} из {toks.length}</span></td>
                      <td onClick={(e) => e.stopPropagation()}><span className="te-acts"><button title="своя строка в конец раздела" onClick={() => insTok(no, toks.length - 1)}>+ строка</button></span></td>
                    </tr>
                    {open[no] && toks.map((t, idx) => {
                      const acts = (own: boolean) => (
                        <td><span className="te-acts">
                          <button title="выше" disabled={idx === 0} onClick={() => moveTok(no, idx, -1)}>↑</button>
                          <button title="ниже" disabled={idx === toks.length - 1} onClick={() => moveTok(no, idx, 1)}>↓</button>
                          <button title="вставить свою строку ниже" onClick={() => insTok(no, idx)}>+</button>
                          {own && <button className="te-del" title="убрать строку" onClick={() => delTok(no, idx)}>×</button>}
                        </span></td>);
                      if (t.startsWith("x:")) {
                        const k = t.slice(2), x = extras[k];
                        if (!x) return null;
                        return (
                          <tr key={t} className={"pf-own" + (x.req ? " pf-has-req" : "")}>
                            <td className="muted" title="своя строка">＋</td>
                            <td><input data-x={k} value={x.name} placeholder="свой параметр" onChange={(e) => updX(k, { name: e.target.value })}
                              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); insTok(no, idx); } }} /></td>
                            <td><input value={x.unit} placeholder="ед." onChange={(e) => updX(k, { unit: e.target.value })} /></td>
                            <td><input value={x.req} onChange={(e) => updX(k, { req: e.target.value })} /></td>
                            <td><input value={x.val} onChange={(e) => updX(k, { val: e.target.value })} /></td>
                            <td><input value={x.comm} onChange={(e) => updX(k, { comm: e.target.value })} /></td>
                            {acts(!x.id)}
                          </tr>);
                      }
                      const f = fieldByCode.get(t.slice(2));
                      if (!f) return null;
                      const req = schema.required.includes(f.code);
                      return (
                        <tr key={t} className={reqs[f.code] ? "pf-has-req" : undefined} title={f.name_zh}>
                          <td className="muted">{f.code}</td>
                          <td>{no === 12 && f.code !== "12.6"
                            ? <input placeholder="наименование опции" value={optNames[f.code] || ""} onChange={(e) => setOptNames({ ...optNames, [f.code]: e.target.value })} />
                            : <>{f.name || (no === 10 ? "Параметр рабочего оборудования" : "")}{req && <span className="pf-req"> *</span>}</>}
                            {f.name_zh && <div className="muted pf-zh">{f.name_zh}</div>}</td>
                          <td className="muted">{f.unit && f.unit !== "—" ? f.unit.split(" / ")[0] : ""}</td>
                          <td><input value={reqs[f.code] || ""} onChange={(e) => setReqs({ ...reqs, [f.code]: e.target.value })} /></td>
                          <td>{input(f)}</td>
                          <td><input value={comms[f.code] || ""} onChange={(e) => setComms({ ...comms, [f.code]: e.target.value })} /></td>
                          {acts(false)}
                        </tr>);
                    })}
                  </Fragment>);
              })}
            </tbody>
          </table>
          <div className="row" style={{ marginTop: 6, gap: 8 }}>
            <button className="btn ghost" onClick={() => { if (!order[99]) setOrder((o) => ({ ...o, 99: [] })); insTok(99, (order[99] || []).length - 1); }}>+ свой параметр в «Дополнительные параметры»</button>
            <span className="muted" style={{ fontSize: 12 }}>Своя строка: Enter в поле «Параметр» — ещё одна строка ниже. Стрелки ↑ ↓ меняют порядок строк внутри раздела.</span>
          </div>
        </div>
      </section>

      <section className="pf-block">
        <button type="button" className="pf-toggle" onClick={() => setFactoryOpen(!factoryOpen)}>
          <h3><span className="pf-num">3</span> Ответ завода {factoryOpen ? "▾" : "▸"}</h3>
          <span className="muted">завод, модель, цена, сроки, оплата — {factoryOpen ? "можно оставить пустым до ответа завода" : "раскрыть, когда придёт ответ"}{factoryMissing.length > 0 ? ` · для расчёта не хватает: ${factoryMissing.join(", ")}` : ""}</span>
        </button>
        {factoryOpen && (
          <div className="pf-grid">
            <PfField {...fp("factory")} title="Завод">
              <select value={factoryId} onChange={(e) => pickFactory(e.target.value)}>
                <option value="">— не выбран / новый —</option>{factories.map((x) => { const na = (x as { approval_status?: string }).approval_status === "pending";
                  return <option key={x.id} value={x.id} disabled={na}>{x.name}{x.trademarks?.length ? ` (${x.trademarks.join(", ")})` : ""}{na ? " — на утверждении" : ""}</option>; })}
              </select>
              {!factoryId && <input value={vals["0.7"] || ""} placeholder="новый завод: полное наименование (если известен)" onChange={(e) => set("0.7", e.target.value)} />}</PfField>
            <PfField title="Торговая марка"><input list="pf-marks" value={trademark} placeholder="по заводу" onChange={(e) => setTrademark(e.target.value)} />
              <datalist id="pf-marks">{marks.map((m) => <option key={m} value={m} />)}</datalist></PfField>
            <PfField {...fp("model")} title="Модель"><input value={vals["1.2"] || ""} placeholder="из ответа завода" onChange={(e) => set("1.2", e.target.value)} />
              {(trademark || vals["1.2"]) && <span className="muted pf-sub">обозначение: <b>{designation({ trademark, model: vals["1.2"] || "" })}</b></span>}</PfField>
            <PfField {...fp("unit_price")} title="Цена за единицу"><input type="text" inputMode="decimal" value={vals["13.5"] || ""} placeholder={execId ? "из каталога" : "из ответа завода"} onChange={(e) => set("13.5", e.target.value)} /></PfField>
            <PfField {...fp("currency")} title="Валюта цены"><select value={vals["13.1"] || "CNY"} onChange={(e) => set("13.1", e.target.value)}>{CURRENCIES.map((c) => <option key={c}>{c}</option>)}</select></PfField>
            <PfField {...fp("basis")} title="Базис поставки"><select value={vals["13.2"] || "FCA"} onChange={(e) => set("13.2", e.target.value)}>{["EXW", "FCA", "CPT", "CIP", "DAP", "DDP"].map((b) => <option key={b}>{b}</option>)}</select></PfField>
            <PfField {...fp("production_term")} title="Срок производства" full>
              <div className="pf-inline">
                {prod.unit !== "date" ? <input type="number" min="0" value={prod.value} placeholder="—" disabled={prodFixed} onChange={(e) => setProdVal({ value: e.target.value })} />
                  : <input type="date" value={prod.fixed_date} disabled={prodFixed} onChange={(e) => setProdVal({ fixed_date: e.target.value })} />}
                <select value={prod.unit} disabled={prodFixed} onChange={(e) => setProdVal({ unit: e.target.value })}>{Object.entries(TERM_UNITS).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select>
                {prod.unit !== "date" && <select value={prod.start_event} disabled={prodFixed} onChange={(e) => setProdVal({ start_event: e.target.value })}>{Object.entries(PRODUCTION_START).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select>}
              </div>
              {prodFixed && <span className="muted pf-sub">зафиксирован КД / ЗГД{position?.production_term?.comment ? `: ${position.production_term.comment}` : ""} — изменить на экране «Расчёт и утверждение»</span>}</PfField>
            <PfField title="Срок поставки клиенту (считает система: +10 %, до целой недели)" wide>
              <div className="pf-inline">
                <select value={dterm.unit} onChange={(e) => setDterm({ ...dterm, unit: e.target.value })}>{["calendar_days", "working_days", "weeks"].map((k) => <option key={k} value={k}>{TERM_UNITS[k]}</option>)}</select>
                <select value={dterm.start_event} onChange={(e) => setDterm({ ...dterm, start_event: e.target.value })}>{Object.entries(DELIVERY_START).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select>
              </div></PfField>
            <PfField {...fp("warranty")} title="Гарантия, мес. / м·ч">
              <div className="pf-inline pf-two"><input type="number" min="0" value={vals["14.4"] || ""} placeholder="мес." onChange={(e) => set("14.4", e.target.value)} />
                <input type="number" min="0" value={vals["14.5"] || ""} placeholder="м·ч" onChange={(e) => set("14.5", e.target.value)} /></div></PfField>
            <PfField title="Аванс заводу, %"><input type="number" min="0" max="100" value={advance.pct} onChange={(e) => setAdvance({ ...advance, pct: Number(e.target.value) })} /></PfField>
            <PfField title="Аванс — через дней после контракта"><input type="number" min="0" value={advance.days} onChange={(e) => setAdvance({ ...advance, days: Number(e.target.value) })} /></PfField>
            <PfField title="Комментарий к цене (если отклонение от истории выше порога)"><input value={priceComment} onChange={(e) => setPriceComment(e.target.value)} /></PfField>
          </div>)}
      </section>

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
              <div><div className="muted">Цены клиентам (видят КД и ЗГД)</div>
                {ref.client_prices.length === 0 ? <div className="muted">КП по модели ещё не было</div> : (
                  <table><tbody>{ref.client_prices.map((h, i) => (
                    <tr key={i}><td>{fmtDate(h.on)}</td><td>{h.deal_number}</td><td className="num">{fmtRub(h.unit_price_rub)} RUB × {h.qty}</td>
                      <td>{h.outcome === "won" ? <span className="badge ok">выиграна</span> : h.outcome === "lost" ? <span className="badge bad">проиграна</span> : <span className="muted">{h.outcome || "—"}</span>}</td></tr>))}</tbody></table>)}
              </div>)}
          </div>
        </div>) : null}
      {err && <div className="alert error">{err}</div>}
      <div className="pf-foot">
        <button className="btn" onClick={save} disabled={!valid || busy}>{position ? "Сохранить изменения" : "Добавить позицию"}</button>
        {onCancel && <button className="btn secondary" onClick={onCancel}>Отмена</button>}
        {missingCreate.length > 0 ? <span className="pf-req">Заполните: {missingCreate.join(", ")}</span>
          : missingCalc.length > 0 ? <span className="muted">Сохранить можно. Для отправки на расчёт понадобятся: {missingCalc.join(", ")}.</span> : null}
      </div>
    </div>
  );
}
