import { Fragment, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError, fmtDate, fmtMoney } from "../api";

// «Финансы → Внутригрупповые расчёты» (D-162): кто из компаний группы кому должен — по парам и валютам, сальдо пар.

type Row = { id: string; title: string; deal: { id: string; number: string; url: string } | null; amount: string; remaining: string; status: string;
  legal_due_on: string | null; forecast_on: string | null; days_overdue: number };
type Pair = { payer_id: string; payer: string; receiver_id: string; receiver: string; currency: string; books: "payer" | "receiver"; amount: string; confirmed: string;
  provisional: string; remaining: string; overdue: string; count: number; rows: Row[]; other_side_rows: Row[] };
type Data = { as_of: string; pairs: Pair[]; balances: { debtor: string; creditor: string; currency: string; net: string }[]; warnings: string[]; note: string };

const errText = (e: unknown) => (e instanceof ApiError ? (typeof e.detail === "string" ? e.detail : e.message) : (e as Error).message);

export default function FinanceIntragroup({ stamp }: { stamp: number }) {
  const [d, setD] = useState<Data | null>(null);
  const [err, setErr] = useState("");
  const [open, setOpen] = useState<Record<string, boolean>>({});
  useEffect(() => { api.get<Data>("/api/finance/intragroup").then((x) => { setD(x); setErr(""); }).catch((e) => setErr(errText(e))); }, [stamp]);
  if (err) return <div className="alert error">{err}</div>;
  if (!d) return <div className="card muted">Загружаю внутригрупповые расчёты…</div>;
  const key = (p: Pair) => `${p.payer_id}-${p.receiver_id}-${p.currency}`;
  return (
    <>
      {d.warnings.map((w) => <div key={w} className="alert warn">{w}</div>)}
      <div className="card">
        <h2 style={{ marginTop: 0 }}>Сальдо между компаниями группы</h2>
        {d.balances.length === 0 ? <div className="muted">Открытых внутригрупповых долгов нет.</div> : (
          <table><thead><tr><th>Должник</th><th>Кредитор</th><th className="num">Сальдо</th><th>Валюта</th></tr></thead>
            <tbody>{d.balances.map((b) => <tr key={`${b.debtor}-${b.creditor}-${b.currency}`}><td>{b.debtor}</td><td>{b.creditor}</td><td className="num"><b>{fmtMoney(b.net)}</b></td><td>{b.currency}</td></tr>)}</tbody></table>)}
      </div>
      <div className="card">
        <h2 style={{ marginTop: 0 }}>По парам «плательщик → получатель»</h2>
        {d.pairs.length === 0 ? <div className="muted">Внутригрупповых обязательств нет.</div> : (
          <table>
            <thead><tr><th>Плательщик → получатель</th><th>Валюта</th><th className="num">Начислено</th><th className="num">Оплачено</th><th className="num">Временно разнесено</th><th className="num">Остаток</th><th className="num">Просрочено</th><th className="num">Строк</th></tr></thead>
            <tbody>{d.pairs.map((p) => (
              <Fragment key={key(p)}>
                <tr style={{ cursor: "pointer" }} onClick={() => setOpen({ ...open, [key(p)]: !open[key(p)] })}>
                  <td>{open[key(p)] ? "▾" : "▸"} {p.payer} → {p.receiver}{p.books === "receiver" && <div className="muted" style={{ fontSize: 11 }}>по книгам получателя</div>}</td>
                  <td>{p.currency}</td><td className="num">{fmtMoney(p.amount)}</td><td className="num">{fmtMoney(p.confirmed)}</td><td className="num">{Number(p.provisional) ? fmtMoney(p.provisional) : "—"}</td>
                  <td className="num"><b>{fmtMoney(p.remaining)}</b></td><td className="num" style={{ color: Number(p.overdue) ? "var(--bad)" : undefined }}>{Number(p.overdue) ? fmtMoney(p.overdue) : "—"}</td><td className="num">{p.count}</td>
                </tr>
                {open[key(p)] && [...p.rows.map((r) => ({ r, other: false })), ...p.other_side_rows.map((r) => ({ r, other: true }))].map(({ r, other }) => (
                  <tr key={r.id} className="muted" style={{ fontSize: 12 }}>
                    <td style={{ paddingLeft: 24 }}>{r.title}{other && " · у другой стороны, для сверки"}</td>
                    <td>{r.deal ? <Link to={r.deal.url}>{r.deal.number}</Link> : "—"}</td>
                    <td className="num">{fmtMoney(r.amount)}</td><td></td><td></td><td className="num">{fmtMoney(r.remaining)}</td>
                    <td className="num">{r.days_overdue ? `${r.days_overdue} дн.` : ""}</td><td>{fmtDate(r.legal_due_on || r.forecast_on)}</td>
                  </tr>))}
              </Fragment>))}</tbody>
          </table>)}
        <p className="muted" style={{ fontSize: 11 }}>{d.note} Внутригрупповые — обязательства компании группы перед другой компанией группы (например, Берг-Хаус → Альянс-Импекс). В сводной ликвидности группы их можно исключить: «Ликвидность и валюта» → «без внутригрупповых».</p>
      </div>
    </>);
}
