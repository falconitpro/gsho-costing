import { Fragment, useEffect, useState } from "react";
import { api, fmtDate, fmtRub } from "../api";
import type { Variant } from "../types";
import { askConfirm } from "./Dialogs";

type Offer = {
  id: string; variant_id: string | null; competitor_id: string | null; competitor: string; on: string; equipment: string; qty: number;
  amount: number; currency: string; vat_included: boolean; vat_pct: number; fx_rate: number; fx_on: string | null; source: string; comment: string; delivery_term: string; payment_terms: string;
  created_by: string; deleted: boolean; net: number; gross: number; rub_net: number; rub_gross: number; contract_net: number; contract_gross: number;
  our_variant?: string; our_rub_net?: number; diff_rub?: number; diff_pct?: number;
};
type Our = { variant_id: string; name: string; rub_net: number; rub_gross: number; currency: string; contract_net: number; contract_gross: number };
type Data = { contract_currency: string; contract_currency_source: string; vat_pct: number; our: Our[]; offers: Offer[]; competitors: string[] };
type Hist = { at: string; user: string; action: string; before: Record<string, unknown> | null; after: Record<string, unknown> | null };
type CompHist = { competitor: { id: string; name: string }; offers: (Offer & { deal_number: string; client: string; our_rub_net: number | null; our_variant: string; diff_pct: number | null })[] };

const CURS = ["RUB", "CNY", "USD", "EUR"];
const ACTION: Record<string, string> = { create: "добавлено", update: "изменено", delete: "удалено" };
const empty = (vid: string) => ({ competitor: "", on: new Date().toISOString().slice(0, 10), equipment: "", qty: "1", amount: "", currency: "RUB", vat_included: true, vat_pct: "", variant_id: vid, source: "", comment: "", delivery_term: "", payment_terms: "" });
const pct = (v?: number | null) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(1)} %`);

/** Цены конкурентов (D-066): ввод с НДС / без, валюта, справочно в рублях и валюте контракта; хронология, история правок,
 *  сравнение с нашими предложениями и история конкурента по всем сделкам. Видят только КД и ЗГД. */
export default function CompetitorsTab({ dealId, variants }: { dealId: string; variants: Variant[] }) {
  const [data, setData] = useState<Data | null>(null);
  const [err, setErr] = useState("");
  const [form, setForm] = useState(empty(variants[0]?.id || ""));
  const [editId, setEditId] = useState<string | null>(null);
  const [sel, setSel] = useState<string[]>([]);
  const [showDeleted, setShowDeleted] = useState(false);
  const [hist, setHist] = useState<Record<string, Hist[]>>({});
  const [comp, setComp] = useState<CompHist | null>(null);

  const load = () => api.get<Data>(`/api/deals/${dealId}/competitors`).then((d) => { setData(d); setErr(""); }).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, [dealId]);
  if (!data) return <div className="card">{err ? <div className="alert error">{err}</div> : "Загрузка…"}</div>;

  const vat = Number(form.vat_pct || data.vat_pct);
  const amt = Number(String(form.amount).replace(",", "."));
  const net = form.vat_included ? amt / (1 + vat / 100) : amt;
  async function save() {
    const body = { ...form, qty: Number(form.qty) || 1, amount: amt, vat_pct: form.vat_pct === "" ? null : Number(form.vat_pct), variant_id: form.variant_id || null };
    try {
      if (editId) await api.patch(`/api/deals/${dealId}/competitors/${editId}`, body); else await api.post(`/api/deals/${dealId}/competitors`, body);
      setForm(empty(variants[0]?.id || "")); setEditId(null); setHist({}); load();
    } catch (e) { setErr((e as Error).message); }
  }
  const startEdit = (o: Offer) => {
    setEditId(o.id);
    setForm({ competitor: o.competitor, on: o.on, equipment: o.equipment, qty: String(o.qty), amount: String(o.amount), currency: o.currency, vat_included: o.vat_included,
      vat_pct: String(o.vat_pct), variant_id: o.variant_id || "", source: o.source, comment: o.comment,
      delivery_term: o.delivery_term || "", payment_terms: o.payment_terms || "" });
  };
  async function remove(o: Offer) {
    if (!(await askConfirm(`Удалить цену конкурента ${o.competitor || "(название неизвестно)"} от ${fmtDate(o.on)}? Запись останется в истории.`))) return;
    try { await api.del(`/api/deals/${dealId}/competitors/${o.id}`); setSel(sel.filter((x) => x !== o.id)); load(); } catch (e) { setErr((e as Error).message); }
  }
  const toggleHist = (id: string) => {
    if (hist[id]) { const n = { ...hist }; delete n[id]; setHist(n); return; }
    api.get<Hist[]>(`/api/deals/${dealId}/competitors/${id}/history`).then((h) => setHist((x) => ({ ...x, [id]: h }))).catch((e) => setErr(e.message));
  };
  const openComp = (cid: string) => api.get<CompHist>(`/api/competitors/${cid}/offers`).then(setComp).catch((e) => setErr(e.message));
  const ccy = data.contract_currency;
  const rows = data.offers.filter((o) => showDeleted || !o.deleted);
  const chosen = data.offers.filter((o) => sel.includes(o.id) && !o.deleted);
  const histText = (s: Record<string, unknown> | null) => s ? `${s.competitor || "без названия"} · ${s.on} · ${fmtRub(Number(s.amount))} ${s.currency} ${s.vat_included ? "с НДС" : "без НДС"}${s.equipment ? ` · ${s.equipment}` : ""}${s.comment ? ` · ${s.comment}` : ""}` : "—";

  return (
    <div className="card">
      {err && <div className="alert error">{err}</div>}
      <p className="muted" style={{ marginTop: 0 }}>Видят только КД и ЗГД. Сравнение — по цене без НДС в рублях по курсу ЦБ на дату сведений.
        Валюта контракта: <b>{ccy}</b> ({data.contract_currency_source}). НДС по умолчанию {data.vat_pct} %.</p>

      <h3 style={{ marginBottom: 4 }}>{editId ? "Изменение цены конкурента" : "Новая цена конкурента"}</h3>
      <div className="row" style={{ flexWrap: "wrap", gap: 8, alignItems: "flex-end" }}>
        <label className="f">Конкурент (если известен)<input list="competitor-names" value={form.competitor} placeholder="название неизвестно" onChange={(e) => setForm({ ...form, competitor: e.target.value })} />
          <datalist id="competitor-names">{data.competitors.map((n) => <option key={n} value={n} />)}</datalist></label>
        <label className="f">Дата сведений<input type="date" value={form.on} onChange={(e) => setForm({ ...form, on: e.target.value })} /></label>
        <label className="f">Что предлагает<input value={form.equipment} placeholder="марка, модель, комплектация" onChange={(e) => setForm({ ...form, equipment: e.target.value })} /></label>
        <label className="f">Кол-во<input type="number" value={form.qty} style={{ width: 60 }} onChange={(e) => setForm({ ...form, qty: e.target.value })} /></label>
        <label className="f">Цена (за всё кол-во)<input type="number" value={form.amount} style={{ width: 140 }} onChange={(e) => setForm({ ...form, amount: e.target.value })} /></label>
        <label className="f">Валюта<select value={form.currency} onChange={(e) => setForm({ ...form, currency: e.target.value })}>{CURS.map((c) => <option key={c}>{c}</option>)}</select></label>
        <label className="f">НДС<select value={form.vat_included ? "gross" : "net"} onChange={(e) => setForm({ ...form, vat_included: e.target.value === "gross" })}>
          <option value="gross">с НДС</option><option value="net">без НДС</option></select></label>
        <label className="f">ставка, %<input type="number" value={form.vat_pct} placeholder={String(data.vat_pct)} style={{ width: 60 }} onChange={(e) => setForm({ ...form, vat_pct: e.target.value })} /></label>
        <label className="f">Сравнить с нашим<select value={form.variant_id} onChange={(e) => setForm({ ...form, variant_id: e.target.value })}>
          <option value="">—</option>{variants.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}</select></label>
        <label className="f">Срок поставки<input value={form.delivery_term} placeholder="напр. 90 дней" onChange={(e) => setForm({ ...form, delivery_term: e.target.value })} /></label>
        <label className="f">Условия оплаты<input value={form.payment_terms} placeholder="напр. 30 % аванс / 70 % по отгрузке" onChange={(e) => setForm({ ...form, payment_terms: e.target.value })} /></label>
        <label className="f">Источник<input value={form.source} placeholder="тендер, клиент…" onChange={(e) => setForm({ ...form, source: e.target.value })} /></label>
        <label className="f">Комментарий<input value={form.comment} onChange={(e) => setForm({ ...form, comment: e.target.value })} /></label>
        <button className="btn" disabled={!(amt > 0)} onClick={save}>{editId ? "Сохранить" : "Добавить"}</button>
        {editId && <button className="btn ghost" onClick={() => { setEditId(null); setForm(empty(variants[0]?.id || "")); }}>Отмена</button>}
      </div>
      {amt > 0 && <div className="muted" style={{ marginTop: 4 }}>без НДС {fmtRub(net)} {form.currency} · с НДС {fmtRub(net * (1 + vat / 100))} {form.currency}
        {form.currency !== "RUB" ? " · рубли и валюта контракта — по курсу ЦБ на дату сведений после сохранения" : ""}</div>}

      <div className="row" style={{ justifyContent: "space-between", marginTop: 14 }}>
        <h3 style={{ margin: 0 }}>Хронология</h3>
        <label className="muted"><input type="checkbox" checked={showDeleted} onChange={(e) => setShowDeleted(e.target.checked)} /> показать удалённые</label>
      </div>
      {rows.length === 0 ? <p className="muted">Цен конкурентов пока нет.</p> : (
        <div style={{ overflowX: "auto" }}><table>
          <thead><tr><th>Сравнить</th><th>Дата</th><th>Конкурент</th><th>Что предлагает</th><th className="num">Введено</th><th className="num">Без НДС</th><th className="num">С НДС</th>
            <th className="num">RUB без НДС</th><th className="num">{ccy} без НДС</th><th>Наше</th><th className="num">Мы дороже / дешевле</th><th /></tr></thead>
          <tbody>{rows.map((o) => (<Fragment key={o.id}>
            <tr style={o.deleted ? { opacity: 0.5, textDecoration: "line-through" } : undefined}>
              <td>{!o.deleted && <input type="checkbox" checked={sel.includes(o.id)} onChange={(e) => setSel(e.target.checked ? [...sel, o.id] : sel.filter((x) => x !== o.id))} />}</td>
              <td>{fmtDate(o.on)}</td>
              <td>{o.competitor_id ? <a href="#" onClick={(e) => { e.preventDefault(); openComp(o.competitor_id!); }}>{o.competitor}</a> : <span className="muted">название неизвестно</span>}</td>
              <td>{o.equipment || "—"}{o.qty !== 1 ? ` × ${o.qty}` : ""}{(o.delivery_term || o.payment_terms) ? <div className="muted">{[o.delivery_term && `срок: ${o.delivery_term}`, o.payment_terms && `оплата: ${o.payment_terms}`].filter(Boolean).join(" · ")}</div> : null}{o.source ? <div className="muted">{o.source}</div> : null}{o.comment ? <div className="muted">{o.comment}</div> : null}</td>
              <td className="num">{fmtRub(o.amount)} {o.currency} <span className="muted">{o.vat_included ? "с НДС" : "без НДС"}</span></td>
              <td className="num">{fmtRub(o.net)} {o.currency}</td><td className="num">{fmtRub(o.gross)} {o.currency}</td>
              <td className="num">{fmtRub(o.rub_net)}{o.currency !== "RUB" && <div className="muted" style={{ fontSize: 11 }}>курс {o.fx_rate.toFixed(4)} на {fmtDate(o.fx_on)}</div>}</td>
              <td className="num">{fmtRub(o.contract_net)}</td>
              <td>{o.our_variant || "—"}{o.our_rub_net != null && <div className="muted">{fmtRub(o.our_rub_net)} RUB</div>}</td>
              <td className="num">{o.diff_pct != null ? <b style={{ color: o.diff_pct > 0 ? "#b45309" : "#15803d" }}>{pct(o.diff_pct)}</b> : "—"}{o.diff_rub != null && <div className="muted">{fmtRub(o.diff_rub)} RUB</div>}</td>
              <td style={{ whiteSpace: "nowrap" }}>{!o.deleted && <><button className="btn ghost" onClick={() => startEdit(o)}>изменить</button><button className="btn ghost" onClick={() => remove(o)}>удалить</button></>}
                <button className="btn ghost" onClick={() => toggleHist(o.id)}>{hist[o.id] ? "скрыть" : "история"}</button></td>
            </tr>
            {hist[o.id] && <tr key={o.id + "h"}><td /><td colSpan={11} className="muted" style={{ fontSize: 12 }}>{hist[o.id].map((h, i) => (
              <div key={i}>{fmtDate(h.at)} {h.at.slice(11, 16)} · {h.user || "—"} · {ACTION[h.action] || h.action}: {h.action === "update" ? <>{histText(h.before)} → <b>{histText(h.after)}</b></> : histText(h.after || h.before)}</div>))}</td></tr>}
          </Fragment>))}</tbody></table></div>)}

      {chosen.length > 0 && data.our.length > 0 && (
        <>
          <h3 style={{ marginBottom: 4 }}>Сравнение наших предложений с выбранными конкурентами</h3>
          <div style={{ overflowX: "auto" }}><table>
            <thead><tr><th>Наше предложение</th><th className="num">RUB без НДС</th><th className="num">RUB с НДС</th><th className="num">{ccy} без НДС</th>
              {chosen.map((o) => <th key={o.id} className="num">{o.competitor || "без названия"}<div className="muted" style={{ fontWeight: 400 }}>{fmtDate(o.on)}{o.equipment ? ` · ${o.equipment}` : ""}</div></th>)}</tr></thead>
            <tbody>
              <tr className="muted"><td>цена конкурента, RUB без НДС</td><td /><td /><td />{chosen.map((o) => <td key={o.id} className="num">{fmtRub(o.rub_net)}<div>{fmtRub(o.contract_net)} {ccy}</div></td>)}</tr>
              {data.our.map((u) => (
                <tr key={u.variant_id}><td>{u.name}</td><td className="num"><b>{fmtRub(u.rub_net)}</b></td><td className="num">{fmtRub(u.rub_gross)}</td><td className="num">{fmtRub(u.contract_net)}</td>
                  {chosen.map((o) => { const d = (u.rub_net - o.rub_net) / o.rub_net * 100; return (
                    <td key={o.id} className="num"><b style={{ color: d > 0 ? "#b45309" : "#15803d" }}>{pct(d)}</b><div className="muted">{fmtRub(u.rub_net - o.rub_net)} RUB</div></td>); })}</tr>))}
            </tbody></table></div>
          <p className="muted">«+» — наше предложение дороже конкурента, «−» — дешевле.</p>
        </>)}
      {chosen.length > 0 && data.our.length === 0 && <p className="muted">Для сравнения назначьте цену нашего предложения.</p>}

      {comp && (
        <div className="card" style={{ background: "#fafafa", marginTop: 12 }}>
          <div className="row" style={{ justifyContent: "space-between" }}><h3 style={{ margin: 0 }}>{comp.competitor.name}: история предложений по всем сделкам</h3>
            <button className="btn ghost" onClick={() => setComp(null)}>закрыть</button></div>
          <table><thead><tr><th>Дата</th><th>Сделка</th><th>Клиент</th><th>Что предлагал</th><th className="num">Введено</th><th className="num">RUB без НДС</th><th>Наше</th><th className="num">Мы дороже / дешевле</th></tr></thead>
            <tbody>{comp.offers.map((o) => (
              <tr key={o.id}><td>{fmtDate(o.on)}</td><td>{o.deal_number}</td><td>{o.client}</td><td>{o.equipment || "—"}</td>
                <td className="num">{fmtRub(o.amount)} {o.currency} <span className="muted">{o.vat_included ? "с НДС" : "без НДС"}</span></td><td className="num">{fmtRub(o.rub_net)}</td>
                <td>{o.our_variant || "—"}{o.our_rub_net != null && <div className="muted">{fmtRub(o.our_rub_net)} RUB</div>}</td><td className="num">{pct(o.diff_pct)}</td></tr>))}</tbody></table>
        </div>)}
    </div>
  );
}
