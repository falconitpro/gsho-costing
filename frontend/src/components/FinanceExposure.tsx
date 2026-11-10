import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError, fmtDate, fmtMoney, fmtRub } from "../api";

// «Финансы → Обеспечение и неустойки» (docs/finance/05, раздел 12). Только чтение юридического блока и исполнения:
// условия и решения меняются в договоре и претензии, здесь — сводка, деньги и ссылки.

type Ref = { id: string; number: string; url: string } | null;
type Sec = { source: string; kind: string; kind_title: string; direction: string; amount: number | null; currency: string; amount_rub: number | null; valid_to: string | null;
  status: string; status_title: string; bank: string; note: string; contract: (Ref & { status: string }) | null; deal: Ref; counterparty: string };
type Pen = { key: string; direction: "receivable" | "payable"; reason_title: string; obligation: string; days: number; rate_text: string; calc_amount: number | null; amount: number | null;
  currency: string; amount_rub: number | null; capped: boolean; status: string; status_title: string; in_forecast: boolean;
  forecast: { obligation_id: string; on: string | null; url: string } | null; claim: { id: string; number: string; status: string; url: string } | null;
  contract: Ref; deal: Ref; counterparty: string };
type Data = { security: Sec[]; penalties: Pen[]; totals: Record<"receivable" | "payable", Record<string, number>>; counts: { security_attention: number; no_rate: number };
  statuses: Record<string, string>; note: string };

const errText = (e: unknown) => (e instanceof ApiError ? (typeof e.detail === "string" ? e.detail : e.message) : (e as Error).message);
const SEC_BADGE: Record<string, string> = { active: "ok", expires_soon: "warn", expired: "bad", planned: "", no_date: "" };
const PEN_BADGE: Record<string, string> = { accruing: "warn", accrued: "", claimed: "info", recognized: "info", paid: "ok", waived: "" };
const SIDE: Record<string, string> = { provided_by_us: "мы предоставили", provided_by_cp: "в нашу пользу", other: "" };
const STAGES: [string, string][] = [["accruing", "начисляется"], ["accrued", "начислена"], ["claimed", "предъявлена"], ["recognized", "признана"], ["paid", "оплачена"]];

export default function FinanceExposure({ sp, go, stamp }: { sp: URLSearchParams; go: (p: Record<string, string | null>) => void; stamp: number }) {
  const [d, setD] = useState<Data | null>(null);
  const [err, setErr] = useState("");
  const direction = sp.get("direction") || "", status = sp.get("pstatus") || "";
  useEffect(() => {
    const q = new URLSearchParams();
    if (direction) q.set("direction", direction);
    if (sp.get("deal")) q.set("deal_id", sp.get("deal")!);
    api.get<Data>(`/api/finance/legal-exposure?${q}`).then((x) => { setD(x); setErr(""); }).catch((e) => setErr(errText(e)));
  }, [direction, sp.get("deal"), stamp]);
  if (err) return <div className="alert error">{err}</div>;
  if (!d) return <div className="muted">Загружаю…</div>;
  const pens = d.penalties.filter((p) => !status || p.status === status);
  const ref = (r: Ref, label?: string) => (r ? <Link to={r.url}>{label || r.number}</Link> : null);
  return (
    <>
      <div className="alert info">{d.note}. Здесь — сводка и деньги; действие «Открыть договор/претензию» ведёт в юридический блок.</div>

      <div className="row" style={{ gap: 10, flexWrap: "wrap", marginBottom: 12 }}>
        {(["receivable", "payable"] as const).map((side) => (
          <div key={side} className="card" style={{ flex: "1 1 360px", margin: 0 }}>
            <h3 style={{ margin: "0 0 6px" }}>{side === "receivable" ? "Неустойки к получению" : "Неустойки к уплате"}, ₽</h3>
            <table style={{ fontSize: 13 }}><tbody>
              {STAGES.map(([k, t]) => <tr key={k}><td>{t}</td><td className="num">{fmtRub(d.totals[side][k] || 0)}</td></tr>)}
              <tr><td><b>в денежном прогнозе</b><div className="muted" style={{ fontSize: 11 }}>только предъявленные и признанные с суммой</div></td><td className="num"><b>{fmtRub(d.totals[side].in_forecast || 0)}</b></td></tr>
            </tbody></table>
          </div>))}
      </div>
      {d.counts.no_rate > 0 && <div className="alert warn">У {d.counts.no_rate} неустоек в валюте нет курса ЦБ — в рублёвые итоги они не вошли.</div>}

      <div className="card" style={{ padding: 0, overflowX: "auto" }}>
        <div className="section-head" style={{ padding: "10px 12px 0" }}><h2>Обеспечение</h2>
          {d.counts.security_attention > 0 && <span className="badge warn">требуют внимания: {d.counts.security_attention}</span>}</div>
        {d.security.length === 0 ? <div className="muted" style={{ padding: 12 }}>Гарантий, аккредитивов и удержаний нет.</div> : (
          <table style={{ minWidth: 900 }}>
            <thead><tr><th>Вид</th><th>Договор / сделка</th><th>Контрагент</th><th className="num">Сумма</th><th>Срок</th><th>Статус</th><th>Банк</th></tr></thead>
            <tbody>{d.security.map((s, i) => (
              <tr key={i}><td>{s.kind_title}<div className="muted" style={{ fontSize: 11 }}>{SIDE[s.direction]}</div></td>
                <td>{ref(s.contract)}{s.contract && s.deal ? " · " : ""}{ref(s.deal)}</td><td>{s.counterparty}</td>
                <td className="num">{s.amount == null ? "—" : fmtMoney(s.amount.toFixed(2), s.currency)}{s.currency !== "RUB" && s.amount_rub != null && <div className="muted" style={{ fontSize: 11 }}>≈ {fmtRub(s.amount_rub)} ₽</div>}</td>
                <td>{fmtDate(s.valid_to)}</td><td><span className={`badge ${SEC_BADGE[s.status] ?? ""}`}>{s.status_title}</span></td><td>{s.bank}</td></tr>))}</tbody>
          </table>)}
      </div>

      <div className="card" style={{ padding: 0, overflowX: "auto", marginTop: 12 }}>
        <div className="row" style={{ padding: "10px 12px 8px", flexWrap: "wrap", gap: 8 }}><h2 style={{ margin: "0 8px 0 0" }}>Неустойки</h2>
          <select value={direction} onChange={(e) => go({ direction: e.target.value || null })}><option value="">к получению и к уплате</option><option value="receivable">к получению</option><option value="payable">к уплате</option></select>
          <select value={status} onChange={(e) => go({ pstatus: e.target.value || null })}><option value="">все статусы</option>{Object.entries(d.statuses).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select>
        </div>
        {pens.length === 0 ? <div className="muted" style={{ padding: 12 }}>Неустоек по выбранным условиям нет.</div> : (
          <table style={{ minWidth: 1000 }}>
            <thead><tr><th>Договор</th><th>За что</th><th className="num">Дней</th><th className="num">Сумма</th><th className="num">₽</th><th>Статус</th><th>Претензия</th><th>Прогноз</th></tr></thead>
            <tbody>{pens.map((p) => (
              <tr key={(p.contract?.id || "") + p.key}>
                <td>{ref(p.contract)}<div className="muted" style={{ fontSize: 11 }}>{p.counterparty}{p.deal ? <> · {ref(p.deal)}</> : null}</div></td>
                <td>{p.reason_title}<div className="muted" style={{ fontSize: 11 }}>{p.direction === "receivable" ? "платит контрагент" : "платим мы"} · {p.obligation} · {p.rate_text}{p.capped ? " · ограничена пределом" : ""}</div></td>
                <td className="num">{p.days}</td>
                <td className="num">{p.amount == null ? "—" : fmtMoney(p.amount.toFixed(2), p.currency)}{p.calc_amount != null && p.amount !== p.calc_amount && <div className="muted" style={{ fontSize: 11 }}>расчёт {fmtRub(p.calc_amount, 2)}</div>}</td>
                <td className="num" style={{ color: p.direction === "payable" ? "var(--bad)" : undefined }}>{p.amount_rub == null ? "—" : `${p.direction === "payable" ? "−" : "+"}${fmtRub(p.amount_rub)}`}</td>
                <td><span className={`badge ${PEN_BADGE[p.status] ?? ""}`}>{p.status_title}</span></td>
                <td>{p.claim ? <Link to={p.claim.url}>{p.claim.number || "претензия"}</Link> : <span className="muted">—</span>}</td>
                <td>{p.forecast ? <Link to={p.forecast.url}>{p.forecast.on ? `ожидается ${fmtDate(p.forecast.on)}` : "в календаре"}</Link> : <span className="muted" style={{ fontSize: 12 }}>{p.status === "accruing" ? "не входит: ещё начисляется" : p.status === "accrued" ? "не входит: не предъявлена" : "—"}</span>}</td>
              </tr>))}</tbody>
          </table>)}
      </div>
    </>
  );
}
