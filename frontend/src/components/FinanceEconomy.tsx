import { createContext, useContext, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError, fmtDate, fmtMoney, fmtPct, fmtRub, newKey } from "../api";
import FinanceExportButton from "./FinanceExportButton";

// «Финансы → Экономика сделок» (docs/finance/05, раздел 11). План — утверждённая версия расчёта (без второго калькулятора),
// текущий прогноз, факт P&L (признание) и факт денег — разными блоками. Признание вводится здесь и в карточке сделки.

type Kpi = { revenue: number | null; cost: number | null; profit: number | null; margin_pct: number | null; markup_pct: number | null; money_cost: number | null } | null;
type Gap = { amount_rub: string; starts_on: string | null; days: number; low_on?: string | null } | null;
type Item = { no: number; code: string; title: string; plan: Record<string, number | null>; forecast: number; fact: number | null; deviation_rub: number | null; deviation_pct: number | null };
type DataGap = { code: string; text: string; rows?: (Record<string, string> | string)[] };
type Ev = { id: string; kind: "revenue" | "expense"; cost_item_code: string | null; cost_item_title: string; amount: string; currency: string; fx_rate: string | null;
  amount_rub: string; recognized_on: string; basis_type: string; basis_title: string; document: { type: string; number: string; date: string | null };
  source_type: string; status: "posted" | "reversed"; reversal_reason: string; comment: string };
type Economy = {
  deal: { id: string; number: string; status: string; plan_fact_closed: boolean }; version_no: number; variant: { id: string; name: string }; scenarios: string[];
  plan: Record<string, Kpi>; forecast: { revenue: number | null; revenue_source: string; cost: number; profit: number | null; margin_pct: number | null };
  pnl_fact: { revenue: number | null; cost: number | null; profit: number | null; margin_pct: number | null; revenue_events: number; expense_events: number; legacy_not_migrated: number };
  cash_fact: { status: string; in_rub: string | null; out_rub: string | null; net_rub: string | null; by_currency: Record<string, { in: string; out: string }>; gap: Gap };
  items: Item[]; cash_gap: { plan_base: Gap; plan_pess: Gap; plan_opt: Gap; forecast: Gap; fact: Gap; fact_status: string };
  fx: { weighted_plan_base: number | null; weighted_plan_pess: number | null; sensitivity: { margin_delta_pct_per_1pct_fx: number; margin_delta_rub_per_1pct_fx: number; breakeven_fx_factor: number | null; breakeven_rate_base_ccy: number | null } | null };
  penalties: { rows: { contract_id: string; number: string; reason_title: string; payer: string; amount: number; currency: string; status: string; status_title: string; amount_rub: number }[];
    receivable_rub: number; payable_rub: number; net_rub: number; receivable_accruing_rub: number; payable_accruing_rub: number };
  data_gaps: DataGap[]; links: { calculation: string; plan_fact: string; versions: string };
};
type FactForm = { open: boolean; items: { no: number; code: string; title: string }[]; payments: boolean };

const errText = (e: unknown) => (e instanceof ApiError ? (typeof e.detail === "string" ? e.detail : e.message) : (e as Error).message);
const SC: [string, string][] = [["base", "базовый"], ["pessimistic", "пессим."], ["optimistic", "оптим."]];
const gapTxt = (g: Gap) => (g == null ? "—" : Number(g.amount_rub) >= 0 ? "нет разрыва" : `${fmtMoney(g.amount_rub)}${g.days ? ` · ${g.days} дн.` : ""}`);

export default function FinanceEconomy({ sp, go, stamp }: { sp: URLSearchParams; go: (p: Record<string, string | null>) => void; stamp: number }) {
  const [deals, setDeals] = useState<{ id: string; number: string; site: string; status: string; plan_fact_closed: boolean }[] | null>(null);
  const [eco, setEco] = useState<Economy | null>(null);
  const [err, setErr] = useState("");
  const [reload, setReload] = useState(0);
  const dealId = sp.get("deal") || "";
  useEffect(() => { api.get<{ deals: typeof deals }>("/api/finance/economy/deals").then((r) => setDeals(r.deals)).catch((e) => setErr(errText(e))); }, [stamp]);
  useEffect(() => { setEco(null); setErr(""); }, [dealId]);   // при обновлении после действия блоки не перемонтируются (сообщения и форма сохраняются)
  useEffect(() => {
    if (dealId) api.get<Economy>(`/api/finance/deals/${dealId}/economy`).then((r) => { setEco(r); setErr(""); }).catch((e) => setErr(errText(e)));
  }, [dealId, stamp, reload]);

  return (
    <div className="fin-eco">
      <div className="card row" style={{ flexWrap: "wrap", gap: 8 }}>
        <label className="row">Сделка
          <select value={dealId} onChange={(e) => go({ deal: e.target.value || null })} style={{ minWidth: 280 }}>
            <option value="">— выберите сделку —</option>
            {(deals || []).map((d) => <option key={d.id} value={d.id}>{d.number}{d.site ? ` · ${d.site}` : ""}{d.plan_fact_closed ? " · план/факт закрыт" : ""}</option>)}
          </select></label>
        {deals && deals.length === 0 && <span className="muted">Нет выигранных сделок с утверждённым расчётом.</span>}
        <span className="muted" style={{ fontSize: 12, flex: "1 1 300px" }}>План — утверждённая версия расчёта; признание выручки и расхода — по документам; деньги — по проведённым и подтверждённым операциям. Значок ⓘ — как получена цифра.</span>
        {dealId && <FinanceExportButton kind="economy" params={{ deal_id: dealId }} />}
      </div>
      {err && <div className="alert error">{err}</div>}
      {dealId && !eco && !err && <div className="muted">Загружаю…</div>}
      {eco && eco.deal.id === dealId && <EconomyView e={eco} onChanged={() => setReload((x) => x + 1)} />}
    </div>
  );
}

type Ex = { metric: string; scenario?: string; code?: string } | null;
const ExplainCtx = createContext<(x: Ex) => void>(() => undefined);
/** Значок «как получена цифра» рядом со значением. */
function I({ m, s, c }: { m: string; s?: string; c?: string }) {
  const open = useContext(ExplainCtx);
  return <button className="fin-i" title="Объяснить цифру" aria-label="Объяснить цифру" onClick={() => open({ metric: m, scenario: s, code: c })}>ⓘ</button>;
}

function EconomyView({ e, onChanged }: { e: Economy; onChanged: () => void }) {
  const p = e.plan.base;
  const cf = e.cash_fact;
  const [ex, setEx] = useState<Ex>(null);
  return (
    <ExplainCtx.Provider value={setEx}>
      {ex && <Explain dealId={e.deal.id} x={ex} onClose={() => setEx(null)} />}
      <div className="row" style={{ gap: 10, flexWrap: "wrap", marginBottom: 12 }}>
        <Box label="Выручка: план · прогноз · признано">{fmtRub(p?.revenue)} · {fmtRub(e.forecast.revenue)} · <b>{fmtRub(e.pnl_fact.revenue)}</b></Box>
        <Box label="Себестоимость: план · прогноз · признано">{fmtRub(p?.cost)} · {fmtRub(e.forecast.cost)} · <b>{fmtRub(e.pnl_fact.cost)}</b></Box>
        <Box label="Маржа: план · прогноз · факт">{fmtPct(p?.margin_pct)} · {fmtPct(e.forecast.margin_pct)} · <b>{fmtPct(e.pnl_fact.margin_pct)}</b></Box>
        <Box label="Кассовый разрыв: план · прогноз · факт" bad={e.cash_gap.fact_status === "insufficient"}>
          {gapTxt(e.cash_gap.plan_base)} · {gapTxt(e.cash_gap.forecast)} · <b>{e.cash_gap.fact ? gapTxt(e.cash_gap.fact) : "недостаточно операций"}</b></Box>
      </div>
      <div className="muted" style={{ fontSize: 12, marginBottom: 8 }}>
        {e.deal.number} · вариант «{e.variant.name}» · утверждённая версия {e.version_no}{e.deal.plan_fact_closed ? " · план/факт закрыт ФД" : ""} ·{" "}
        <Link to={e.links.calculation}>Расчёт и утверждение</Link> · <Link to={e.links.plan_fact}>Платежи и план/факт</Link> · <Link to={e.links.versions}>Версии и файлы</Link></div>

      {e.data_gaps.length > 0 && (
        <div className="alert warn"><b>Чего не хватает для точной картины</b>
          <ul style={{ margin: "4px 0 0 18px", padding: 0 }}>{e.data_gaps.map((g) => (
            <li key={g.code}>{g.text}{g.rows && g.rows.length > 0 && <div className="muted" style={{ fontSize: 12 }}>
              {g.rows.slice(0, 6).map((r) => (typeof r === "string" ? r : `${r.title || r.currency || ""}${r.due_on ? ` — срок ${fmtDate(r.due_on)}` : ""}${r.amount ? `, ${fmtMoney(r.amount, r.currency)}` : ""}`)).join("; ")}
              {g.rows.length > 6 ? ` и ещё ${g.rows.length - 6}` : ""}</div>}</li>))}</ul></div>)}

      <div className="row" style={{ gap: 12, alignItems: "stretch", flexWrap: "wrap" }}>
        <div className="card" style={{ flex: "1 1 360px", margin: 0 }}>
          <h2 style={{ marginTop: 0 }}>Результат (P&amp;L)</h2>
          <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>Выручка и расходы признаются по документам (УПД, акт, накладная) или хозяйственному событию. Поступление денег выручкой не считается.</div>
          <table><thead><tr><th /><th className="num">План</th><th className="num">Прогноз</th><th className="num">Признано</th></tr></thead>
            <tbody>
              <tr><td>Выручка, ₽</td><td className="num">{fmtRub(p?.revenue)}</td><td className="num">{fmtRub(e.forecast.revenue)}<I m="forecast_revenue" />{e.forecast.revenue_source === "plan" && <div className="muted" style={{ fontSize: 11 }}>по плану</div>}</td><td className="num"><b>{fmtRub(e.pnl_fact.revenue)}</b><I m="revenue_fact" /></td></tr>
              <tr><td>Себестоимость, ₽</td><td className="num">{fmtRub(p?.cost)}<I m="plan" /></td><td className="num">{fmtRub(e.forecast.cost)}</td><td className="num"><b>{fmtRub(e.pnl_fact.cost)}</b><I m="cost_fact" /></td></tr>
              <tr><td>Прибыль, ₽</td><td className="num">{fmtRub(p?.profit)}</td><td className="num">{fmtRub(e.forecast.profit)}</td><td className="num"><b>{fmtRub(e.pnl_fact.profit)}</b></td></tr>
              <tr><td>Маржа</td><td className="num">{fmtPct(p?.margin_pct)}</td><td className="num">{fmtPct(e.forecast.margin_pct)}</td><td className="num"><b>{fmtPct(e.pnl_fact.margin_pct)}</b></td></tr>
              <tr><td>Стоимость денег, ₽</td><td className="num">{fmtRub(p?.money_cost)}</td><td /><td /></tr>
            </tbody></table>
          <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>событий признания: выручка {e.pnl_fact.revenue_events}, расходы {e.pnl_fact.expense_events}</div>
        </div>
        <div className="card" style={{ flex: "1 1 360px", margin: 0 }}>
          <h2 style={{ marginTop: 0 }}>Деньги (cash)</h2>
          <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>Только проведённые банковские операции с подтверждённым разнесением на обязательства сделки.</div>
          {cf.status === "insufficient" ? <div className="muted">Недостаточно подтверждённых операций — фактический кассовый разрыв не считается.</div> : (
            <table><tbody>
              <tr><td>Поступило, ₽</td><td className="num">{fmtMoney(cf.in_rub)}<I m="cash_in" /></td></tr>
              <tr><td>Оплачено, ₽</td><td className="num">{fmtMoney(cf.out_rub)}<I m="cash_out" /></td></tr>
              <tr><td>Сальдо, ₽</td><td className="num"><b>{fmtMoney(cf.net_rub)}</b></td></tr>
              {Object.entries(cf.by_currency).filter(([c]) => c !== "RUB").map(([c, v]) => <tr key={c}><td>в {c}</td><td className="num">+{fmtMoney(v.in, c)} / −{fmtMoney(v.out, c)}</td></tr>)}
            </tbody></table>)}
          <h3 style={{ margin: "10px 0 4px" }}>Кассовый разрыв</h3>
          <table><tbody>
            {[["План, базовый", e.cash_gap.plan_base], ["План, пессимистичный", e.cash_gap.plan_pess], ["План, оптимистичный", e.cash_gap.plan_opt], ["Текущий прогноз", e.cash_gap.forecast], ["Факт", e.cash_gap.fact]].map(([t, g]) => (
              <tr key={t as string}><td>{t as string}</td><td className="num">{g ? gapTxt(g as Gap) : t === "Факт" ? <span className="muted">недостаточно данных</span> : "—"}
                {t === "Факт" && <I m="cash_gap_fact" />}{t === "Текущий прогноз" && <I m="cash_gap_forecast" />}</td>
                <td className="muted" style={{ fontSize: 12 }}>{g && (g as NonNullable<Gap>).starts_on ? `с ${fmtDate((g as NonNullable<Gap>).starts_on)}` : ""}</td></tr>))}
          </tbody></table>
          <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>Прогноз: фактические операции + непогашенные остатки обязательств по их срокам.</div>
        </div>
      </div>

      <div className="card" style={{ padding: 0, overflowX: "auto", marginTop: 12 }}>
        <table style={{ minWidth: 920 }}>
          <thead><tr><th>№</th><th>Статья себестоимости</th>{SC.map(([k, t]) => <th key={k} className="num">План, {t}</th>)}<th className="num">Прогноз</th><th className="num">Признано</th><th className="num">Отклонение</th></tr></thead>
          <tbody>{e.items.map((i) => (
            <tr key={i.code}><td>{i.no}</td><td>{i.title}</td>{SC.map(([k]) => <td key={k} className="num">{fmtRub(i.plan[k])}{i.plan[k] ? <I m="plan" s={k} c={i.code} /> : null}</td>)}
              <td className="num">{i.forecast === 0 && i.fact == null ? "—" : <>{fmtRub(i.forecast)}<I m="forecast_item" c={i.code} /></>}</td><td className="num">{i.fact == null ? <span className="muted">—</span> : <><b>{fmtRub(i.fact)}</b><I m="cost_fact" c={i.code} /></>}</td>
              <td className="num">{i.deviation_rub == null ? "—" : <>{i.deviation_rub > 0 ? "+" : ""}{fmtRub(i.deviation_rub)}{i.deviation_pct != null && <div className="muted" style={{ fontSize: 11 }}>{i.deviation_pct > 0 ? "+" : ""}{fmtPct(i.deviation_pct)}</div>}</>}</td></tr>))}</tbody>
        </table>
        <div className="muted" style={{ fontSize: 12, padding: "6px 10px" }}>Прогноз статьи = признанное + ещё не признанный остаток базового плана (при перерасходе — признанное).</div>
      </div>

      <div className="row" style={{ gap: 12, alignItems: "stretch", flexWrap: "wrap", marginTop: 12 }}>
        <div className="card" style={{ flex: "1 1 300px", margin: 0 }}>
          <h2 style={{ marginTop: 0 }}>Курсы и чувствительность</h2>
          <table><tbody>
            <tr><td>Средний курс оплат заводу, базовый</td><td className="num">{e.fx.weighted_plan_base ?? "—"}</td></tr>
            <tr><td>то же, пессимистичный</td><td className="num">{e.fx.weighted_plan_pess ?? "—"}</td></tr>
            {e.fx.sensitivity && <>
              <tr><td>Курс +1 % → маржа</td><td className="num">{fmtPct(e.fx.sensitivity.margin_delta_pct_per_1pct_fx)} п.п. · {fmtRub(e.fx.sensitivity.margin_delta_rub_per_1pct_fx)} ₽</td></tr>
              <tr><td>Курс безубыточности (к порогу маржи)</td><td className="num">{e.fx.sensitivity.breakeven_rate_base_ccy ?? "—"}</td></tr></>}
          </tbody></table>
        </div>
        <div className="card" style={{ flex: "1 1 300px", margin: 0 }}>
          <h2 style={{ marginTop: 0 }}>Неустойки</h2>
          {e.penalties.rows.length === 0 ? <div className="muted">По договорам сделки неустоек нет.</div> : <>
            <div className="muted" style={{ fontSize: 12 }}>к получению {fmtRub(e.penalties.receivable_rub)} ₽ · к уплате {fmtRub(e.penalties.payable_rub)} ₽ · ещё начисляются: нам {fmtRub(e.penalties.receivable_accruing_rub)} ₽, с нас {fmtRub(e.penalties.payable_accruing_rub)} ₽</div>
            <table style={{ fontSize: 13 }}><tbody>{e.penalties.rows.map((x, n) => (
              <tr key={n}><td><Link to={`/legal?tab=contracts&open=${x.contract_id}`}>{x.number}</Link></td><td>{x.reason_title}</td><td>{x.status_title}</td>
                <td className="num" style={{ color: x.payer === "us" ? "var(--bad)" : undefined }}>{x.payer === "us" ? "−" : "+"}{fmtRub(x.amount_rub)}</td></tr>))}</tbody></table></>}
          <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>Условия и решения — в юридическом блоке.</div>
        </div>
      </div>

      <Recognition e={e} onChanged={onChanged} />
    </ExplainCtx.Provider>
  );
}

type Trace = { metric_title: string; value: string | number | null; formula: string; as_of: string; version_no: number; variant: { name: string };
  included: Record<string, string | number | null>[]; excluded: (Record<string, string | number | null> & { reason: string })[]; rates: { currency: string; rate: string; source: string; on: string }[];
  series?: { on: string; net: string; balance: string }[] };

function Explain({ dealId, x, onClose }: { dealId: string; x: NonNullable<Ex>; onClose: () => void }) {
  const [t, setT] = useState<Trace | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    setT(null); setErr("");
    const q = new URLSearchParams({ metric: x.metric, scenario: x.scenario || "base" });
    if (x.code) q.set("cost_item_code", x.code);
    api.get<Trace>(`/api/finance/deals/${dealId}/economy/explanation?${q}`).then(setT).catch((e) => setErr(errText(e)));
  }, [dealId, x.metric, x.scenario, x.code]);
  const v = (r: Record<string, string | number | null>) => (r.amount_rub != null ? fmtMoney(String(r.amount_rub)) : r.amount != null ? fmtMoney(String(r.amount), String(r.currency || "")) : "—");
  return (
    <div className="card" style={{ borderLeft: "4px solid var(--info, #3a6ea5)" }}>
      <div className="section-head"><h2 style={{ margin: 0 }}>Как получена цифра{t ? `: ${t.metric_title}` : ""}</h2><button className="btn ghost" onClick={onClose}>Закрыть</button></div>
      {err && <div className="alert error">{err}</div>}
      {!t && !err && <div className="muted">Считаю…</div>}
      {t && <>
        <div style={{ fontSize: 18, margin: "4px 0" }}><b>{t.value == null ? "нет данных" : typeof t.value === "number" ? fmtRub(t.value) : fmtMoney(t.value)}</b> ₽</div>
        <div className="muted" style={{ fontSize: 13 }}>{t.formula}</div>
        <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>на {fmtDate(t.as_of)} · утверждённая версия {t.version_no} · вариант «{t.variant.name}»</div>
        <h3 style={{ margin: "10px 0 4px" }}>Учтено ({t.included.length})</h3>
        {t.included.length === 0 ? <div className="muted">Ничего.</div> : (
          <table style={{ fontSize: 13 }}><tbody>{t.included.map((r, n) => (
            <tr key={n}><td>{fmtDate(String(r.on || "")) !== "—" ? fmtDate(String(r.on)) : ""}</td>
              <td>{r.url ? <Link to={String(r.url)}>{String(r.title || r.type)}</Link> : String(r.title || r.type)}{r.direction ? <span className="muted"> · {r.direction === "in" ? "поступление" : "оплата"}</span> : null}</td>
              <td className="num">{r.amount_rub != null && r.currency && r.currency !== "RUB" ? <span className="muted" style={{ fontSize: 11 }}>{fmtMoney(String(r.amount), String(r.currency))} → </span> : null}{v(r)}</td></tr>))}</tbody></table>)}
        {t.excluded.length > 0 && <>
          <h3 style={{ margin: "10px 0 4px" }}>Не учтено ({t.excluded.length})</h3>
          <table style={{ fontSize: 13 }}><tbody>{t.excluded.map((r, n) => <tr key={n}><td>{String(r.title || r.type || "")}</td><td className="num">{v(r)}</td><td className="muted">{r.reason}</td></tr>)}</tbody></table></>}
        {t.rates.length > 0 && <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>Курсы: {t.rates.map((r) => `${r.currency} ${r.rate} (${r.source || "—"}, ${fmtDate(r.on)})`).join("; ")}</div>}
        {t.series && t.series.length > 0 && <details style={{ marginTop: 6 }}><summary className="muted">Накопленное сальдо по дням ({t.series.length})</summary>
          <table style={{ fontSize: 12 }}><tbody>{t.series.map((p) => <tr key={p.on}><td>{fmtDate(p.on)}</td><td className="num">{fmtMoney(p.net)}</td><td className="num"><b>{fmtMoney(p.balance)}</b></td></tr>)}</tbody></table></details>}
      </>}
    </div>
  );
}

function Box({ label, children, bad }: { label: string; children: React.ReactNode; bad?: boolean }) {
  return <div className="card kpi" style={{ flex: "1 1 220px", margin: 0, borderLeft: bad ? "4px solid var(--warn, #c90)" : undefined }}><div className="v" style={{ fontSize: 15 }}>{children}</div><div className="l">{label}</div></div>;
}

function Recognition({ e, onChanged }: { e: Economy; onChanged: () => void }) {
  const [evs, setEvs] = useState<Ev[]>([]);
  const [form, setForm] = useState<FactForm | null>(null);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const today = new Date().toISOString().slice(0, 10);
  const blank = { kind: "revenue", cost_item_code: "", amount: "", currency: "RUB", fx_rate: "", fx_on: today, fx_source: "", recognized_on: today,
    basis_type: "primary_document", document_type: "УПД", document_number: "", document_date: today, comment: "" };
  const [f, setF] = useState(blank);
  const [key, setKey] = useState(newKey());
  const load = () => {
    api.get<{ events: Ev[] }>(`/api/deals/${e.deal.id}/finance/recognition-events`).then((r) => setEvs(r.events)).catch((x) => setMsg(errText(x)));
    api.get<FactForm>(`/api/deals/${e.deal.id}/fact/form`).then(setForm).catch(() => setForm(null));
  };
  useEffect(load, [e.deal.id]);
  const canRevenue = !!form?.payments;
  const kinds = [...(canRevenue ? ["revenue"] : []), ...((form?.items.length || 0) > 0 ? ["expense"] : [])];
  useEffect(() => { if (kinds.length && !kinds.includes(f.kind)) setF((x) => ({ ...x, kind: kinds[0] })); }, [kinds.join()]);

  async function save() {
    setBusy(true); setMsg("");
    try {
      const body = { ...f, amount: f.amount.replace(/\s/g, "").replace(",", "."), cost_item_code: f.kind === "expense" ? f.cost_item_code : null,
        fx_rate: f.currency === "RUB" ? null : f.fx_rate.replace(",", "."), fx_on: f.currency === "RUB" ? null : f.fx_on, fx_source: f.currency === "RUB" ? "" : f.fx_source,
        document_date: f.basis_type === "primary_document" ? f.document_date : null };
      await api.postIdem(`/api/deals/${e.deal.id}/finance/recognition-events`, body, key);
      setF({ ...blank, kind: f.kind }); setKey(newKey()); setMsg("Признание записано"); load(); onChanged();
    } catch (x) { setMsg(errText(x)); } finally { setBusy(false); }
  }
  async function reverse(id: string) {
    const reason = window.prompt("Причина сторно признания");
    if (!reason) return;
    try { await api.post(`/api/finance/recognition-events/${id}/reverse`, { reason }); load(); onChanged(); } catch (x) { setMsg(errText(x)); }
  }
  const doc = f.basis_type === "primary_document";
  return (
    <div className="card" style={{ marginTop: 12 }}>
      <h2 style={{ marginTop: 0 }}>Признание выручки и расходов</h2>
      {msg && <div className="alert info">{msg}</div>}
      {form?.open && kinds.length > 0 && (
        <div style={{ marginBottom: 10 }}>
          <div className="row" style={{ flexWrap: "wrap" }}>
            <select value={f.kind} onChange={(x) => setF({ ...f, kind: x.target.value })}>{kinds.map((k) => <option key={k} value={k}>{k === "revenue" ? "выручка" : "расход по статье"}</option>)}</select>
            {f.kind === "expense" && <select value={f.cost_item_code} onChange={(x) => setF({ ...f, cost_item_code: x.target.value })}><option value="">— статья —</option>
              {form.items.map((i) => <option key={i.code} value={i.code}>{i.no}. {i.title}</option>)}</select>}
            <input inputMode="decimal" placeholder="сумма" value={f.amount} onChange={(x) => setF({ ...f, amount: x.target.value })} style={{ width: 140 }} />
            <select value={f.currency} onChange={(x) => setF({ ...f, currency: x.target.value })}>{["RUB", "CNY", "USD", "EUR"].map((c) => <option key={c}>{c}</option>)}</select>
            {f.currency !== "RUB" && <>
              <input inputMode="decimal" placeholder="курс, ₽" value={f.fx_rate} onChange={(x) => setF({ ...f, fx_rate: x.target.value })} style={{ width: 90 }} />
              <input type="date" value={f.fx_on} onChange={(x) => setF({ ...f, fx_on: x.target.value })} title="дата курса" />
              <input placeholder="источник курса" value={f.fx_source} onChange={(x) => setF({ ...f, fx_source: x.target.value })} style={{ width: 140 }} /></>}
            <label className="row">дата признания <input type="date" value={f.recognized_on} onChange={(x) => setF({ ...f, recognized_on: x.target.value })} /></label>
          </div>
          <div className="row" style={{ flexWrap: "wrap", marginTop: 6 }}>
            <select value={f.basis_type} onChange={(x) => setF({ ...f, basis_type: x.target.value })}>
              <option value="primary_document">первичный документ</option><option value="business_event">хозяйственное событие</option></select>
            {doc && <>
              <select value={f.document_type} onChange={(x) => setF({ ...f, document_type: x.target.value })}>{["УПД", "акт", "накладная", "ГТД", "счёт-фактура", "инвойс"].map((d) => <option key={d}>{d}</option>)}</select>
              <input placeholder="номер документа" value={f.document_number} onChange={(x) => setF({ ...f, document_number: x.target.value })} style={{ width: 150 }} />
              <input type="date" value={f.document_date} onChange={(x) => setF({ ...f, document_date: x.target.value })} title="дата документа" /></>}
            <input placeholder={doc ? "комментарий" : "что произошло (обязательно)"} value={f.comment} onChange={(x) => setF({ ...f, comment: x.target.value })} style={{ width: 260 }} />
            <button className="btn" disabled={busy || !f.amount || (f.kind === "expense" && !f.cost_item_code) || (doc ? !f.document_number.trim() : f.comment.trim().length < 3)
              || (f.currency !== "RUB" && (!f.fx_rate || !f.fx_source.trim()))} onClick={save}>Признать</button>
          </div>
          <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>Выручку признают ФД и ЗГД; расход — по статьям, которые вы вносите в план/факт. Деньги и остатки счетов признание не меняет.</div>
        </div>)}
      {evs.length === 0 ? <div className="muted">Событий признания нет.</div> : (
        <div style={{ overflowX: "auto" }}>
          <table style={{ fontSize: 13 }}><thead><tr><th>Дата</th><th>Что</th><th className="num">Сумма</th><th className="num">₽</th><th>Основание</th><th>Статус</th><th /></tr></thead>
            <tbody>{evs.map((v) => (
              <tr key={v.id} style={{ opacity: v.status === "reversed" ? 0.55 : 1 }}>
                <td>{fmtDate(v.recognized_on)}</td><td>{v.kind === "revenue" ? "выручка" : v.cost_item_title}</td>
                <td className="num">{fmtMoney(v.amount, v.currency)}{v.fx_rate && <div className="muted" style={{ fontSize: 11 }}>курс {v.fx_rate}</div>}</td><td className="num">{fmtMoney(v.amount_rub)}</td>
                <td>{v.document.number ? `${v.document.type} № ${v.document.number} от ${fmtDate(v.document.date)}` : v.basis_title}{v.comment && <div className="muted" style={{ fontSize: 11 }}>{v.comment}</div>}</td>
                <td>{v.status === "reversed" ? <span className="badge">сторно: {v.reversal_reason}</span> : <span className="badge ok">признано</span>}</td>
                <td>{v.status === "posted" && v.source_type === "manual" && <button className="btn ghost" onClick={() => reverse(v.id)}>сторно</button>}
                  {v.source_type === "planfact" && <span className="muted" style={{ fontSize: 11 }}>из план/факта</span>}</td></tr>))}</tbody></table></div>)}
    </div>
  );
}
