import { Fragment, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError, fmtDate, fmtMoney } from "../api";
import FinanceExportButton from "./FinanceExportButton";

// «Финансы → Дебиторка и кредиторка» (docs/finance/05, раздел 9; D-143). Остатки по валютам и ageing от юридического срока;
// группы по контрагенту или сделке; без согласованной даты — отдельный блок.

type Row = { id: string; direction: "in" | "out"; title: string; counterparty: { name: string }; deal: { id: string; number: string; url: string } | null;
  contract: { id: string; number: string; url: string } | null; amount: string; currency: string; confirmed_paid: string; provisional_paid: string; remaining: string;
  legal_due_on: string | null; legal_due_text: string; forecast_on: string | null; status: string; status_title: string; days_overdue: number; bucket: string; flags: string[] };
type Group = { direction: "in" | "out"; title: string; count: number; max_days_overdue: number; by_currency: Record<string, { remaining: string; overdue: string }>;
  rub: string; rub_complete: boolean; share_pct: number | null; rows: Row[] };
type Data = { buckets: { code: string; title: string }[]; totals: Record<"in" | "out", Record<string, Record<string, string | number>>>; groups: Group[];
  undated: Row[]; parts_undated: { id: string; number: string; client: string; amount: string; currency: string; url: string }[]; rates_missing: string[]; notes: string[] };

const errText = (e: unknown) => (e instanceof ApiError ? (typeof e.detail === "string" ? e.detail : e.message) : (e as Error).message);
const DIR = [["", "Все"], ["in", "Нам должны"], ["out", "Мы должны"]] as const;

export default function FinanceDebt({ sp, go, stamp }: { sp: URLSearchParams; go: (p: Record<string, string | null>) => void; stamp: number }) {
  const [d, setD] = useState<Data | null>(null);
  const [err, setErr] = useState("");
  const [closed, setClosed] = useState<Record<string, boolean>>({});
  const direction = sp.get("direction") || "", group = sp.get("group") === "deal" ? "deal" : "counterparty";
  useEffect(() => {
    const q = new URLSearchParams({ group_by: group });
    if (direction) q.set("direction", direction);
    api.get<Data>(`/api/finance/debt?${q}`).then((x) => { setD(x); setErr(""); }).catch((e) => setErr(errText(e)));
  }, [direction, group, stamp]);
  if (err) return <div className="alert error">{err}</div>;
  if (!d) return <div className="muted">Загружаю…</div>;
  const sides = (direction ? [direction] : ["in", "out"]) as ("in" | "out")[];
  return (
    <>
      <div className="row" style={{ gap: 8, flexWrap: "wrap", marginBottom: 10 }}>
        <div className="tabs" style={{ margin: 0 }}>{DIR.map(([k, t]) => <button key={k} className={direction === k ? "active" : ""} onClick={() => go({ direction: k || null })}>{t}</button>)}</div>
        <label className="row">Группировать <select value={group} onChange={(e) => go({ group: e.target.value === "deal" ? "deal" : null })}><option value="counterparty">по контрагенту</option><option value="deal">по сделке</option></select></label>
        <div className="push"><FinanceExportButton kind="debt" params={{ direction, group_by: group }} /></div>
      </div>

      {sides.map((side) => {
        const cur = d.totals[side] || {};
        return (
          <div key={side} className="card" style={{ padding: 0, overflowX: "auto" }}>
            <h3 style={{ margin: "10px 12px 6px" }}>{side === "in" ? "Нам должны" : "Мы должны"}</h3>
            {Object.keys(cur).length === 0 ? <div className="muted" style={{ padding: "0 12px 12px" }}>Открытой задолженности с датой нет.</div> : (
              <table style={{ minWidth: 900 }}>
                <thead><tr><th>Валюта</th><th className="num">Остаток</th><th className="num">Временно разнесено</th>{d.buckets.map((b) => <th key={b.code} className="num">{b.title}</th>)}</tr></thead>
                <tbody>{Object.entries(cur).map(([c, t]) => (
                  <tr key={c}><td>{c}</td><td className="num"><b>{fmtMoney(String(t.remaining))}</b></td><td className="num">{Number(t.provisional) ? fmtMoney(String(t.provisional)) : "—"}</td>
                    {d.buckets.map((b) => <td key={b.code} className="num" style={{ color: b.code !== "current" && Number(t[b.code]) ? "var(--bad)" : undefined }}>{Number(t[b.code]) ? fmtMoney(String(t[b.code])) : "—"}</td>)}</tr>))}</tbody>
              </table>)}
          </div>);
      })}
      {d.rates_missing.length > 0 && <div className="alert warn">Нет курса ЦБ для {d.rates_missing.join(", ")} — доля таких строк в концентрации не посчитана.</div>}

      <div className="card" style={{ padding: 0, overflowX: "auto", marginTop: 12 }}>
        <table style={{ minWidth: 1050 }}>
          <thead><tr><th>{group === "deal" ? "Сделка" : "Контрагент"} / строка</th><th>Срок по договору</th><th>Ожидается</th><th className="num">Сумма</th><th className="num">Оплачено</th><th className="num">Остаток</th><th>Статус</th></tr></thead>
          <tbody>{d.groups.map((g) => {
            const k = g.direction + g.title;
            const open = !closed[k];
            return (
              <Fragment key={k}>
                <tr style={{ background: "#f6f6f6", cursor: "pointer" }} onClick={() => setClosed({ ...closed, [k]: open })}>
                  <td colSpan={5}><b>{open ? "▾" : "▸"} {g.title}</b> <span className="muted" style={{ fontSize: 12 }}>· {g.direction === "in" ? "нам должны" : "мы должны"} · строк {g.count}
                    {g.max_days_overdue > 0 ? ` · просрочка до ${g.max_days_overdue} дн.` : ""}{g.share_pct != null ? ` · ${g.share_pct.toString().replace(".", ",")} % ${g.direction === "in" ? "дебиторки" : "кредиторки"}` : ""}</span></td>
                  <td className="num">{Object.entries(g.by_currency).map(([c, v]) => <div key={c}><b>{fmtMoney(v.remaining, c)}</b>{Number(v.overdue) > 0 && <div style={{ color: "var(--bad)", fontSize: 11 }}>просрочено {fmtMoney(v.overdue, c)}</div>}</div>)}</td><td />
                </tr>
                {open && g.rows.map((r) => (
                  <tr key={r.id}>
                    <td style={{ paddingLeft: 22 }}><Link to={`/finance?tab=calendar&open=${r.id}`}>{r.title}</Link>
                      <div className="muted" style={{ fontSize: 11 }}>{group === "deal" ? r.counterparty.name : r.deal?.number || ""}{r.contract ? ` · ${r.contract.number}` : ""}</div></td>
                    <td>{r.legal_due_on ? fmtDate(r.legal_due_on) : <span className="muted" style={{ fontSize: 12 }}>{r.legal_due_text || "—"}</span>}</td><td>{fmtDate(r.forecast_on)}</td>
                    <td className="num">{fmtMoney(r.amount, r.currency)}</td>
                    <td className="num">{fmtMoney(r.confirmed_paid)}{Number(r.provisional_paid) > 0 && <div className="muted" style={{ fontSize: 11 }}>временно {fmtMoney(r.provisional_paid)}</div>}</td>
                    <td className="num"><b>{fmtMoney(r.remaining, r.currency)}</b></td>
                    <td><span className={`badge ${r.days_overdue > 0 ? "bad" : ""}`}>{r.status_title}{r.days_overdue > 0 ? ` · ${r.days_overdue} дн.` : ""}</span>
                      {r.flags.includes("needs_reconciliation") && <span className="badge warn" style={{ marginLeft: 4 }}>требует сверки</span>}</td>
                  </tr>))}
              </Fragment>);
          })}</tbody>
        </table>
        {d.groups.length === 0 && <div className="muted" style={{ padding: 12 }}>Открытой задолженности нет.</div>}
      </div>

      <div className="card" style={{ marginTop: 12 }}>
        <h3 style={{ marginTop: 0 }}>Ожидаемые суммы без согласованной даты</h3>
        <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>В задолженность и просрочку не входят: срок ещё не определён договором или событием.</div>
        {d.undated.length === 0 && d.parts_undated.length === 0 ? <div className="muted">Нет.</div> : (
          <table style={{ fontSize: 13 }}><tbody>
            {d.undated.map((r) => <tr key={r.id}><td><Link to={`/finance?tab=calendar&open=${r.id}`}>{r.title}</Link><div className="muted" style={{ fontSize: 11 }}>{r.legal_due_text}</div></td>
              <td>{r.counterparty.name}</td><td className="num">{fmtMoney(r.remaining, r.currency)}</td></tr>)}
            {d.parts_undated.map((p) => <tr key={p.id}><td><Link to={p.url}>Запчасти {p.number}</Link><div className="muted" style={{ fontSize: 11 }}>заказано, не отгружено</div></td><td>{p.client}</td><td className="num">{fmtMoney(p.amount, p.currency)}</td></tr>)}
          </tbody></table>)}
      </div>
      <ul className="muted" style={{ fontSize: 12 }}>{d.notes.map((n) => <li key={n}>{n}</li>)}</ul>
    </>
  );
}
