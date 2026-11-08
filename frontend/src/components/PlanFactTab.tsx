import { useEffect, useState } from "react";
import { api, fmtDate, fmtRub } from "../api";
import type { Deal } from "../types";

type Item = { no: number; code: string; title: string };
type Entry = { id: string; kind: string; cost_item_code: string | null; amount: number; currency: string; fx_rate: number | null; on: string; deviation_reason: string; comment: string; entered_by: string };
type Form = { open: boolean; closed: boolean; items: Item[]; payments: boolean; can_close: boolean; can_report: boolean; deviation_pct: number; entries: Entry[] };
type Row = { no: number; code: string; title: string; plan_base: number; plan_pess: number; fact: number | null; deviation_rub: number | null; deviation_pct: number | null; flag: boolean };
type Tot = { plan_base: number | null; plan_pess: number | null; fact: number | null };
type PenRow = { contract_id: string; number: string; cp_name: string; key: string; payer: string; reason_title: string; obligation: string; days: number; amount: number; currency: string; status: string; status_title: string; amount_rub: number };
type Report = { variant: string; rows: Row[]; totals: Record<string, Tot>; closed: boolean; penalties?: { rows: PenRow[]; receivable_rub: number; payable_rub: number; net_rub: number; receivable_accruing_rub: number; payable_accruing_rub: number } };

const KIND: Record<string, string> = { cost_item: "статья себестоимости", payment_in: "поступление от клиента", payment_out: "оплата заводу / поставщику", delivery: "срок поставки, дней", fx: "курс" };
const num = (s: string) => Number(String(s).replace(/\s/g, "").replace(",", "."));
const pct = (v: number | null | undefined) => (v == null ? "—" : `${v.toFixed(2).replace(".", ",")} %`);

/** План/факт по сделке (после выигрыша): ввод факта по ролям, отчёт по статьям с отклонениями, закрытие ФД. */
export default function PlanFactTab({ deal }: { deal: Deal }) {
  const [form, setForm] = useState<Form | null>(null);
  const [rep, setRep] = useState<Report | null>(null);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const blank = { kind: "cost_item", cost_item_code: "", amount: "", currency: "RUB", fx_rate: "", on: new Date().toISOString().slice(0, 10), deviation_reason: "", comment: "" };
  const [f, setF] = useState(blank);
  const items = (form?.items || []);
  const load = async () => {
    try {
      const fm = await api.get<Form>(`/api/deals/${deal.id}/fact/form`); setForm(fm);
      if (fm.can_report) setRep(await api.get<Report>(`/api/deals/${deal.id}/plan-fact`));
    } catch (e) { setMsg((e as Error).message); }
  };
  useEffect(() => { load(); }, [deal.id]);
  if (!form) return msg ? <div className="alert error">{msg}</div> : null;

  const planOf = (code: string) => rep?.rows.find((r) => r.code === code)?.plan_base;
  const rub = num(f.amount) * (f.currency === "RUB" ? 1 : num(f.fx_rate) || 0);
  const plan = f.kind === "cost_item" && f.cost_item_code ? planOf(f.cost_item_code) : undefined;
  const already = f.kind === "cost_item" ? (rep?.rows.find((r) => r.code === f.cost_item_code)?.fact || 0) : 0;
  const devPct = plan ? ((already + rub - plan) / plan) * 100 : null;
  const needReason = devPct != null && Math.abs(devPct) > form.deviation_pct;

  async function add() {
    setBusy(true);
    try {
      await api.post(`/api/deals/${deal.id}/fact`, { ...f, cost_item_code: f.kind === "cost_item" ? f.cost_item_code : null, amount: num(f.amount),
        fx_rate: f.currency === "RUB" ? null : num(f.fx_rate) });
      setF({ ...blank, kind: f.kind }); setMsg("Факт записан"); await load();
    } catch (e) { setMsg((e as Error).message); } finally { setBusy(false); }
  }
  async function del(id: string) {
    try { await api.del(`/api/deals/${deal.id}/fact/${id}`); setMsg("Запись удалена (остаётся в журнале действий)"); await load(); } catch (e) { setMsg((e as Error).message); }
  }
  async function close() {
    setBusy(true);
    try { await api.post(`/api/deals/${deal.id}/plan-fact/close`); setMsg("План/факт закрыт: данные попадут в отчёт точности оценок"); await load(); }
    catch (e) { setMsg((e as Error).message); } finally { setBusy(false); }
  }
  const kinds = [...(items.length ? ["cost_item"] : []), ...(form.payments ? ["payment_in", "payment_out"] : []), ...(form.can_report ? ["delivery"] : [])];
  const itemTitle = (c: string | null) => (rep?.rows.find((r) => r.code === c) || items.find((i) => i.code === c))?.title || c || "";
  const T = rep?.totals;

  return (
    <>
      {msg && <div className="alert info">{msg}</div>}
      {form.closed && <div className="alert ok">План/факт закрыт ФД{form.can_close ? "" : " — изменения вносит только ФД"}.</div>}
      {rep && (
        <div className="card">
          <h2 style={{ marginTop: 0 }}>План и факт{rep.variant ? ` · ${rep.variant}` : ""}</h2>
          <p className="muted">План — утверждённая версия расчёта (базовый и пессимистичный сценарии). Отклонение больше {form.deviation_pct} % от базового плана подсвечено. Фактические платежи, внесённые ФД на вкладке «Контракт и исполнение», тоже учитываются.</p>
          {T && (
            <table style={{ marginBottom: 12 }}>
              <thead><tr><th>Итог</th><th className="num">План (базовый)</th><th className="num">План (пессим.)</th><th className="num">Факт</th></tr></thead>
              <tbody>
                <tr><td>Выручка, ₽</td><td className="num">{fmtRub(T.revenue?.plan_base)}</td><td className="num">{fmtRub(T.revenue?.plan_pess)}</td><td className="num">{fmtRub(T.revenue?.fact)}</td></tr>
                <tr><td>Себестоимость, ₽</td><td className="num">{fmtRub(T.cost_total?.plan_base)}</td><td className="num">{fmtRub(T.cost_total?.plan_pess)}</td><td className="num">{fmtRub(T.cost_total?.fact)}</td></tr>
                <tr><td>Прибыль, ₽</td><td className="num">{fmtRub(T.profit?.plan_base)}</td><td className="num">{fmtRub(T.profit?.plan_pess)}</td><td className="num">{fmtRub(T.profit?.fact)}</td></tr>
                {T.penalties && <tr><td>Неустойки по договорам (нетто), ₽<div className="muted" style={{ fontSize: 11 }}>пессим. — с учётом ещё начисляющихся</div></td><td className="num">{fmtRub(T.penalties.plan_base)}</td><td className="num">{fmtRub(T.penalties.plan_pess)}</td><td className="num">{fmtRub(T.penalties.fact)}</td></tr>}
                {T.profit_after_penalties && <tr><td>Прибыль с учётом неустоек, ₽</td><td className="num">{fmtRub(T.profit_after_penalties.plan_base)}</td><td className="num">{fmtRub(T.profit_after_penalties.plan_pess)}</td><td className="num"><b>{fmtRub(T.profit_after_penalties.fact)}</b></td></tr>}
                <tr><td>Маржа</td><td className="num">{pct(T.margin_pct?.plan_base)}</td><td className="num">{pct(T.margin_pct?.plan_pess)}</td><td className="num"><b>{pct(T.margin_pct?.fact)}</b></td></tr>
                <tr><td>Срок поставки, дней</td><td className="num">{T.delivery_days?.plan_base ?? "—"}</td><td className="num">{T.delivery_days?.plan_pess ?? "—"}</td><td className="num">{T.delivery_days?.fact ?? "—"}</td></tr>
                <tr><td>Средний курс оплат заводу</td><td className="num">{T.weighted_fx?.plan_base ?? "—"}</td><td className="num">{T.weighted_fx?.plan_pess ?? "—"}</td><td className="num">{T.weighted_fx?.fact ?? "—"}</td></tr>
              </tbody>
            </table>)}
          {rep.penalties && rep.penalties.rows.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <h3 style={{ margin: "4px 0" }}>Неустойки по договорам сделки</h3>
              <div className="muted" style={{ fontSize: 12, marginBottom: 4 }}>к получению {fmtRub(rep.penalties.receivable_rub)} ₽ · к уплате {fmtRub(rep.penalties.payable_rub)} ₽ · ещё начисляются: нам {fmtRub(rep.penalties.receivable_accruing_rub)} ₽, с нас {fmtRub(rep.penalties.payable_accruing_rub)} ₽</div>
              <table style={{ fontSize: 13 }}><thead><tr><th>Договор</th><th>За что</th><th className="num">Дней</th><th className="num">Сумма</th><th className="num">₽</th><th>Статус</th></tr></thead>
                <tbody>{rep.penalties.rows.map((p) => (
                  <tr key={p.contract_id + p.key}><td><a href={`/legal?tab=contracts&open=${p.contract_id}`}>{p.number}</a><div className="muted" style={{ fontSize: 11 }}>{p.cp_name}</div></td>
                    <td>{p.reason_title}<div className="muted" style={{ fontSize: 11 }}>{p.payer === "cp" ? "платит контрагент" : "платим мы"} · {p.obligation}</div></td><td className="num">{p.days}</td>
                    <td className="num">{fmtRub(p.amount, 2)} {p.currency}</td><td className="num" style={{ color: p.payer === "us" ? "var(--bad)" : undefined }}>{p.payer === "us" ? "−" : "+"}{fmtRub(p.amount_rub)}</td><td>{p.status_title}</td></tr>))}</tbody></table>
            </div>)}
          <table>
            <thead><tr><th>№</th><th>Статья</th><th className="num">План (базовый), ₽</th><th className="num">План (пессим.), ₽</th><th className="num">Факт, ₽</th><th className="num">Отклонение, ₽</th><th className="num">Отклонение</th></tr></thead>
            <tbody>{rep.rows.filter((r) => r.plan_base || r.fact != null).map((r) => (
              <tr key={r.code} style={{ background: r.flag ? "#f4f4f4" : undefined }}>
                <td>{r.no}</td><td>{r.title}</td><td className="num">{fmtRub(r.plan_base)}</td><td className="num">{fmtRub(r.plan_pess)}</td>
                <td className="num">{r.fact == null ? <span className="muted">нет факта</span> : <b>{fmtRub(r.fact)}</b>}</td>
                <td className="num">{r.deviation_rub == null ? "—" : `${r.deviation_rub > 0 ? "+" : ""}${fmtRub(r.deviation_rub)}`}</td>
                <td className="num">{r.deviation_pct == null ? "—" : <span className={"badge " + (r.flag ? "warn" : "ok")}>{r.deviation_pct > 0 ? "+" : ""}{pct(r.deviation_pct)}</span>}</td></tr>))}</tbody>
          </table>
        </div>)}

      {form.open && kinds.length > 0 && (
        <div className="card">
          <h2 style={{ marginTop: 0 }}>Внести факт</h2>
          <div className="muted" style={{ marginBottom: 6 }}>Вы вносите: {items.length ? items.map((i) => `${i.no}. ${i.title}`).join(", ") : "—"}{form.payments ? "; фактические платежи" : ""}. Сумму в валюте вносите с фактическим курсом — в отчёт она попадёт в рублях.</div>
          <div className="row" style={{ flexWrap: "wrap" }}>
            <select value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>{kinds.map((k) => <option key={k} value={k}>{KIND[k]}</option>)}</select>
            {f.kind === "cost_item" && <select value={f.cost_item_code} onChange={(e) => setF({ ...f, cost_item_code: e.target.value })}><option value="">— статья —</option>{items.map((i) => <option key={i.code} value={i.code}>{i.no}. {i.title}</option>)}</select>}
            <input inputMode="decimal" placeholder={f.kind === "delivery" ? "дней" : "сумма"} value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} style={{ width: 140 }} />
            {f.kind !== "delivery" && <select value={f.currency} onChange={(e) => setF({ ...f, currency: e.target.value })}>{["RUB", "CNY", "USD", "EUR"].map((c) => <option key={c}>{c}</option>)}</select>}
            {f.currency !== "RUB" && f.kind !== "delivery" && <input inputMode="decimal" placeholder="курс, ₽" value={f.fx_rate} onChange={(e) => setF({ ...f, fx_rate: e.target.value })} style={{ width: 90 }} />}
            <label className="row">дата <input type="date" value={f.on} onChange={(e) => setF({ ...f, on: e.target.value })} /></label>
          </div>
          {plan != null && f.amount && (
            <div className={"alert " + (needReason ? "warn" : "info")} style={{ marginTop: 6 }}>
              План по статье {fmtRub(plan)} ₽; с этой записью факт {fmtRub(already + rub)} ₽ — отклонение {pct(devPct)}{needReason ? `, больше ${form.deviation_pct} %: укажите причину` : ""}.</div>)}
          <div className="row" style={{ marginTop: 6, flexWrap: "wrap" }}>
            <input placeholder={needReason ? "причина отклонения (обязательно)" : "причина отклонения"} value={f.deviation_reason} onChange={(e) => setF({ ...f, deviation_reason: e.target.value })} style={{ width: 320 }} />
            <input placeholder="комментарий, документ" value={f.comment} onChange={(e) => setF({ ...f, comment: e.target.value })} style={{ width: 260 }} />
            <button className="btn" disabled={busy || !f.amount || (f.kind === "cost_item" && !f.cost_item_code) || (f.currency !== "RUB" && f.kind !== "delivery" && !f.fx_rate) || (needReason && !f.deviation_reason.trim())} onClick={add}>Записать факт</button>
          </div>
        </div>)}

      <div className="card">
        <h2 style={{ marginTop: 0 }}>Записи факта</h2>
        {form.entries.length === 0 ? <div className="muted">Записей пока нет.</div> : (
          <table><thead><tr><th>Дата</th><th>Что</th><th className="num">Сумма</th><th>Курс</th><th>Причина / комментарий</th><th /></tr></thead>
            <tbody>{form.entries.map((e) => (
              <tr key={e.id}><td>{fmtDate(e.on)}</td><td>{e.kind === "cost_item" ? itemTitle(e.cost_item_code) : KIND[e.kind] || e.kind}</td>
                <td className="num">{fmtRub(e.amount)} {e.kind === "delivery" ? "дн." : e.currency}</td><td>{e.fx_rate ?? ""}</td>
                <td style={{ fontSize: 12 }}>{e.deviation_reason}{e.comment && <div className="muted">{e.comment}</div>}</td>
                <td>{!form.closed && <button className="btn ghost" onClick={() => del(e.id)}>удалить</button>}</td></tr>))}</tbody></table>)}
        {form.can_close && <div className="row" style={{ marginTop: 10 }}><button className="btn secondary" disabled={busy} onClick={close}>Закрыть план/факт (ФД)</button>
          <span className="muted" style={{ fontSize: 12 }}>после закрытия факт меняет только ФД; сделка попадает в отчёт точности оценок</span></div>}
      </div>
    </>
  );
}
