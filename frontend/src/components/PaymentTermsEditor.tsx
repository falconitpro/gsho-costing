import { useEffect, useState } from "react";
import { UndoButton, useUndoHistory } from "../undo";
import { api } from "../api";
import { DELIVERY_START, EVENT_TITLES, TERM_UNITS, type CalcResponse, type Deal, type PaymentLine } from "../types";

type Scheme = { id: string; name: string; lines: PaymentLine[] };
const CLIENT_EVENTS = ["contract", "client_prepayment", "lc_opened", "readiness", "shipment", "border", "declaration", "delivery", "commissioning", "runtime"];
const FACTORY_EVENTS = ["factory_contract", "factory_prepayment", "readiness", "shipment", "border", "delivery"];

export function payText(l: PaymentLine) {
  return `${l.share_pct != null ? `${l.share_pct} %` : l.amount} — ${EVENT_TITLES[l.event] || l.event}${l.offset_days ? ` + ${l.offset_days} ${l.offset_kind === "working" ? "раб. дн." : "дн."}` : ""}${l.via === "lc" ? " (аккредитив)" : ""}`;
}

function LinesEditor({ lines, events, onChange }: { lines: PaymentLine[]; events: string[]; onChange: (l: PaymentLine[]) => void }) {
  const upd = (i: number, p: Partial<PaymentLine>) => onChange(lines.map((l, j) => (j === i ? { ...l, ...p } : l)));
  const sum = lines.reduce((s, l) => s + (l.share_pct || 0), 0);
  return (
    <div>
      <table className="pay" style={{ width: "100%", minWidth: 640, tableLayout: "fixed" }}>
        <colgroup><col style={{ width: 32 }} /><col style={{ width: 72 }} /><col style={{ width: "30%" }} /><col style={{ width: 72 }} /><col style={{ width: 120 }} /><col style={{ width: 80 }} /><col /><col style={{ width: 32 }} /></colgroup>
        <thead><tr><th>№</th><th>Доля, %</th><th>Событие-основание</th><th>Через, дн.</th><th>Дни</th><th>Аккредитив</th><th>Условие словами</th><th /></tr></thead>
        <tbody>{lines.map((l, i) => (
          <tr key={i}>
            <td className="muted">{i + 1}</td>
            <td><input type="number" value={l.share_pct ?? ""} onChange={(e) => upd(i, { share_pct: Number(e.target.value) })} /></td>
            <td><select value={l.event} onChange={(e) => upd(i, { event: e.target.value })}>{events.map((ev) => <option key={ev} value={ev}>{EVENT_TITLES[ev]}</option>)}</select></td>
            <td><input type="number" value={l.offset_days} onChange={(e) => upd(i, { offset_days: Number(e.target.value) })} /></td>
            <td><select value={l.offset_kind || "calendar"} onChange={(e) => upd(i, { offset_kind: e.target.value })}><option value="calendar">календарные</option><option value="working">рабочие</option></select></td>
            <td style={{ textAlign: "center" }}><input type="checkbox" checked={l.via === "lc"} onChange={(e) => upd(i, { via: e.target.checked ? "lc" : "transfer" })} /></td>
            <td className="muted" style={{ fontSize: 12 }}>{payText(l)}</td>
            <td><button className="btn ghost" style={{ padding: "2px 6px" }} title="удалить платёж" onClick={() => onChange(lines.filter((_, j) => j !== i).map((x, k) => ({ ...x, no: k + 1 })))}>×</button></td>
          </tr>))}
          {lines.length === 0 && <tr><td colSpan={8} className="muted">Платежей нет</td></tr>}</tbody></table>
      <div className="row" style={{ marginTop: 4 }}>
        <button className="btn add" onClick={() => onChange([...lines, { no: lines.length + 1, share_pct: Math.max(0, 100 - sum), event: events[0], offset_days: 0, offset_kind: "calendar", via: "transfer" }])}>Платёж</button>
        <span className={"badge " + (sum === 100 ? "ok" : "bad")}>итого {sum} %</span>
      </div>
    </div>
  );
}

/** Задача 1: условия оплаты клиента и завода задаёт КД (ЗГД); БГ на аванс; задача 3: точка отсчёта и единицы срока поставки (правка КД). */
export default function PaymentTermsEditor({ deal, calc, onSaved }: { deal: Deal; calc: CalcResponse; onSaved: () => void }) {
  const pt = calc.payment_terms!;
  const [schemes, setSchemes] = useState<Scheme[]>([]);
  const [client, setClient] = useState<PaymentLine[]>(pt.client_payments || []);
  const [variantPay, setVariantPay] = useState<Record<string, PaymentLine[]>>(Object.fromEntries(deal.variants.filter((v) => !v.is_default).map((v) => [v.id, v.client_payments || []])));
  const [factory, setFactory] = useState<Record<string, PaymentLine[]>>(Object.fromEntries(pt.positions.map((p) => [p.id, p.factory_payments || []])));
  const [bg, setBg] = useState<{ enabled: boolean; mode: string; value: number }>({ ...pt.advance_guarantee_default, ...(pt.bank_guarantee as { enabled?: boolean; mode?: string; value?: number }) });
  const [terms, setTerms] = useState<Record<string, { unit: string; start_event: string }>>(Object.fromEntries(pt.positions.map((p) => [p.id, { unit: p.delivery_term?.unit || "calendar_days", start_event: p.delivery_term?.start_event || "client_contract" }])));
  const [msg, setMsg] = useState("");
  const history = useUndoHistory({ client, variantPay, factory, bg, terms },
    (st) => { setClient(st.client); setVariantPay(st.variantPay); setFactory(st.factory); setBg(st.bg); setTerms(st.terms); });
  useEffect(() => { api.get<Scheme[]>("/api/refs/payment-schemes").then(setSchemes); }, []);
  const toLines = (ls: PaymentLine[]) => ls.map((l, i) => ({ ...l, no: i + 1 }));
  async function save() {
    try {
      const r = await api.post<{ cd_checks: string[] }>(`/api/deals/${deal.id}/payment-terms`, {
        client_payments: deal.has_alternatives ? undefined : toLines(client), variant_payments: deal.has_alternatives ? Object.fromEntries(Object.entries(variantPay).map(([k, v]) => [k, toLines(v)])) : undefined,
        factory_payments: Object.fromEntries(Object.entries(factory).map(([k, v]) => [k, toLines(v)])), bank_guarantee: bg, delivery_terms: terms, accept_wishes: false,
      });
      setMsg(r.cd_checks.length ? "Сохранено. Осталось: " + r.cd_checks.join(" ") : "Условия сохранены"); onSaved();
    } catch (e) { setMsg((e as Error).message); }
  }
  const SchemeBar = ({ onPick }: { onPick: (l: PaymentLine[]) => void }) => (
    <div className="row" style={{ marginBottom: 6 }}><span className="muted">Типовая:</span>
      <button className="btn ghost" onClick={() => onPick(pt.default_client)}>30 % / 70 % аккредитив после CMR</button>
      {schemes.map((s) => <button key={s.id} className="btn ghost" onClick={() => onPick(s.lines)}>{s.name}</button>)}</div>
  );
  return (
    <div className="card">
      <h2 style={{ marginTop: 0 }}>Условия оплаты (задаёт КД)</h2>
      {pt.client_payment_wishes && <div className="alert info">Пожелания клиента по оплате (от менеджера): <b>{pt.client_payment_wishes}</b> <button className="btn ghost" style={{ marginLeft: 8 }} onClick={() => api.post(`/api/deals/${deal.id}/payment-terms`, { accept_wishes: true }).then(() => setMsg("Отмечено: пожелания клиента приняты"))}>принять как есть</button></div>}
      {msg && <div className="alert info">{msg}</div>}
      <style>{`.pay { border-collapse: collapse; } .pay td { padding: 4px; vertical-align: middle; border-bottom: 1px solid #e6e9ee; }
        .pay td input:not([type=checkbox]), .pay td select { width: 100%; padding: 5px; box-sizing: border-box; font-size: 13px; }
        .pay thead th { background: #e9edf3; color: #1f2937; font-weight: 600; font-size: 12px; padding: 6px 4px; }
        .pay tbody tr:nth-child(odd) td { background: #fafbfc; } .pay tbody tr:hover td { background: #f1f6ff; }
        .paysec { border: 1px solid #e5e5e5; border-radius: 6px; padding: 10px 12px; margin-bottom: 12px; overflow-x: auto; }`}</style>
      <div className="paysec">
          <h3 style={{ marginTop: 0 }}>1. Расчёты с клиентом <span className="muted" style={{ fontWeight: 400, fontSize: 13 }}>{deal.has_alternatives ? "по каждому предложению" : "по сделке"}</span></h3>
          {!deal.has_alternatives && <><SchemeBar onPick={setClient} /><LinesEditor lines={client} events={CLIENT_EVENTS} onChange={setClient} />
            {client.length === 0 && <div className="muted">Не заданы — расчёт по типовой схеме</div>}</>}
          {deal.has_alternatives && deal.variants.filter((v) => !v.is_default).map((v) => (
            <div key={v.id} style={{ marginBottom: 8 }}><div className="badge accent">{v.name}</div><SchemeBar onPick={(l) => setVariantPay({ ...variantPay, [v.id]: l })} />
              <LinesEditor lines={variantPay[v.id] || []} events={CLIENT_EVENTS} onChange={(l) => setVariantPay({ ...variantPay, [v.id]: l })} /></div>))}
          <div style={{ marginTop: 10 }}>
            <b>Банковская гарантия на аванс</b> <span className="muted">учитывается в статье 11, если есть предоплата</span>
            <div className="row" style={{ marginTop: 4 }}>
              <label className="row"><input type="checkbox" checked={bg.enabled} onChange={(e) => setBg({ ...bg, enabled: e.target.checked })} /> учитывать</label>
              <select value={bg.mode} onChange={(e) => setBg({ ...bg, mode: e.target.value })}><option value="one_time_pct">разовый % от аванса</option><option value="annual_pct">% годовых до поставки клиенту</option><option value="fixed">фиксированная сумма, RUB</option></select>
              <input type="number" step="0.01" value={bg.value} style={{ width: 110 }} onChange={(e) => setBg({ ...bg, value: Number(e.target.value) })} />
            </div>
          </div>
      </div>
      <div className="paysec">
          <h3 style={{ marginTop: 0 }}>2. Расчёты с заводом <span className="muted" style={{ fontWeight: 400, fontSize: 13 }}>по позициям; условия завода из RFQ / ответа — справка</span></h3>
          {pt.positions.map((p) => (
            <div key={p.id} style={{ marginBottom: 10 }}>
              <div className="row" style={{ marginBottom: 4 }}><b>{p.model}</b>
                <button className="btn ghost" style={{ padding: "2px 8px" }} onClick={() => setFactory({ ...factory, [p.id]: pt.default_factory })}>типовая 30/70 (14 раб. дн.)</button>
                {p.factory_payments_offer?.length > 0 && <button className="btn ghost" style={{ padding: "2px 8px" }} onClick={() => setFactory({ ...factory, [p.id]: p.factory_payments_offer })} title={p.factory_payments_offer.map(payText).join("; ")}>условия завода</button>}</div>
              <LinesEditor lines={factory[p.id] || []} events={FACTORY_EVENTS} onChange={(l) => setFactory({ ...factory, [p.id]: l })} />
              <div className="row" style={{ marginTop: 4, fontSize: 12 }}>
                <span className="muted">Срок поставки клиенту:</span>
                <select value={terms[p.id].unit} onChange={(e) => setTerms({ ...terms, [p.id]: { ...terms[p.id], unit: e.target.value } })}>{["calendar_days", "working_days", "weeks"].map((u) => <option key={u} value={u}>{TERM_UNITS[u]}</option>)}</select>
                <select value={terms[p.id].start_event} onChange={(e) => setTerms({ ...terms, [p.id]: { ...terms[p.id], start_event: e.target.value } })}>{Object.entries(DELIVERY_START).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select>
                <span className="muted">производство: {p.production_term?.value ?? "—"} {TERM_UNITS[p.production_term?.unit || "calendar_days"]}</span>
              </div>
            </div>))}
      </div>
      <div className="row" style={{ marginTop: 8 }}><button className="btn" onClick={save}>Сохранить условия оплаты и сроки</button><UndoButton h={history} /><span className="muted">изменение после визы сбрасывает визы</span></div>
    </div>
  );
}
