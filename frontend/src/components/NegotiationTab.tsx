import { useEffect, useMemo, useRef, useState } from "react";
import { api, fmtDate, fmtRub } from "../api";
import { EVENT_TITLES, type PaymentLine, type Variant } from "../types";
import CompetitorsTab from "./CompetitorsTab";
import { STATUS_TITLES } from "../types";
import KpRevisions from "./KpRevisions";

// Торги (D-074): шаги снижения цены и изменения условий после КП, данные для решения, КП на каждом шаге, конкуренты, аналитика.
// Видят и меняют только КД и ЗГД.

type Money = { net_rub: number; gross_rub: number; currency: string; net: number; gross: number };
type Dec = { name: string; price: Money; before: Money; first: Money; change_rub: number; change_pct: number | null; from_first_pct: number | null;
  cost_rub: number | null; cost_pess_rub: number | null; margin: { base: number | null; pessimistic: number | null; optimistic: number | null };
  profit_rub: number | null; cash_gap_rub: number | null; cash_gap_days: number | null; finance_rub: number | null; threshold_pct: number;
  min_price_threshold: Money | null; min_price_zero: Money | null; room_rub: number | null; room_pct: number | null; warnings: string[] };
type Var = { id: string; name: string; units?: number; currency: string; kp_rate: number; first_rub: number; price_rub: number; client_payments: PaymentLine[]; delivery_term: string };
type Step = { id: string; no: number; at: string; by_name: string; kind: string; reason: string; reason_title: string; comment: string;
  input: Record<string, { mode: string; basis: string; currency: string; value: number; units?: number }>; terms: Record<string, { client_payments?: PaymentLine[]; delivery_term?: string }>;
  result: Record<string, Dec>; price_before_rub: number; price_rub: number; margin_pct: number | null; show_discount: boolean; kp_revision_id: string;
  sent_at: string | null; sent_to: string[]; client_answer: string;
  review: { status?: string; since?: string; remind_on?: string; assignee_name?: string; note?: string; closed_at?: string } };
type Comp = { id: string; competitor: string; on: string; equipment: string; rub_net: number; delivery_term: string; payment_terms: string };
type An = { first_rub: number; current_rub: number; discount_rub: number; discount_pct: number | null; bargain_coef: number | null; price_ratio: number | null;
  steps: number; price_steps: number; terms_steps: number; avg_step_pct: number | null; days: number; first_kp_on: string | null; last_step_on: string | null;
  margin_first_pct: number | null; margin_now_pct: number | null; margin_lost_pp: number | null; best_competitor_rub: number | null; gap_to_competitor_pct: number | null };
type Data = { ready: boolean; message?: string; can_edit: boolean; status: string; vat_pct: number; reasons: Record<string, string>; variants: Var[];
  decision: Record<string, Dec>; steps: Step[]; competitors: Comp[]; analytics: An; fx: Record<string, number>;
  client_rates: Record<string, number>; client_rates_history: { at: string; by_name: string; rates: Record<string, number>; comment: string }[] };
type PIn = { mode: "pct" | "amount" | "target"; basis: "net" | "gross"; currency: string; value: string };

const pct = (v: number | null | undefined, sign = false) => (v == null ? "—" : `${sign && v > 0 ? "+" : ""}${v.toFixed(2).replace(".", ",")} %`);
const mcls = (m: number | null | undefined, thr: number) => (m == null ? "" : m < 0 ? "ng-bad" : m < thr ? "ng-warn" : "ng-ok");
const cur2 = (m: Money, gross = false) => (m.currency === "RUB" ? `${fmtRub(gross ? m.gross_rub : m.net_rub)} ₽` : `${fmtRub(gross ? m.gross : m.net)} ${m.currency}`);
// цены — в валюте контракта (валюте КП) с НДС или без, справочно в рублях (D-078)
type Fx = { cur: string; rate: number; gross: boolean; vat: number; ref?: number; client?: boolean; n?: number };
const k = (fx: Fx) => (fx.gross ? 1 + fx.vat / 100 : 1);
const sym = (c: string) => (c === "RUB" ? "₽" : c);
const inCur = (fx: Fx, rub: number | null | undefined, sign = false) => (rub == null ? "—" : `${sign && rub > 0 ? "+" : sign && rub < 0 ? "−" : ""}${fmtRub(Math.abs(rub) * k(fx) / fx.rate / (fx.n || 1))} ${sym(fx.cur)}`);
// итог на всё количество — справочно (D-085)
const totRef = (fx: Fx, rub: number | null | undefined) => ((fx.n || 1) > 1 && rub != null ? `за ${fx.n} шт.: ${fmtRub(Math.abs(rub) * k(fx) / fx.rate)} ${sym(fx.cur)}` : "");
// справочно в рублях: по внутреннему курсу заказчика, если он задан в торгах (D-079), иначе — по курсу цены КП
const rubRef = (fx: Fx, rub: number | null | undefined) => (rub == null || fx.cur === "RUB" ? "" : `≈ ${fmtRub(Math.abs(rub) / fx.rate * (fx.ref || fx.rate) * k(fx) / (fx.n || 1))} ₽${fx.client ? " (курс заказчика)" : ""}`);
const vatLbl = (fx: Fx) => (fx.gross ? `с НДС ${fx.vat} %` : "без НДС");
const mm = (m: Money, gross: boolean) => cur2(m, gross);

export default function NegotiationTab({ dealId, variants, onChanged }: { dealId: string; variants: Variant[]; onChanged: () => void }) {
  const [d, setD] = useState<Data | null>(null);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const [inp, setInp] = useState<Record<string, PIn>>({});
  const [termsOn, setTermsOn] = useState(false);
  const [terms, setTerms] = useState<Record<string, { client_payments: PaymentLine[]; delivery_term: string }>>({});
  const [reason, setReason] = useState("client");
  const [comment, setComment] = useState("");
  const [showDisc, setShowDisc] = useState(false);
  const [prev, setPrev] = useState<Record<string, Dec> | null>(null);
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [section, setSection] = useState<"steps" | "competitors" | "kp">("steps");
  const [gross, setGross] = useState(() => { try { return localStorage.getItem("gsho.ngGross") === "1"; } catch { return false; } });
  const setG = (g: boolean) => { setGross(g); try { localStorage.setItem("gsho.ngGross", g ? "1" : "0"); } catch { /* нет хранилища */ } setInp((x) => Object.fromEntries(Object.entries(x).map(([kk, p]) => [kk, { ...p, basis: g ? "gross" : "net" }]))); };
  const timer = useRef<number | undefined>(undefined);

  const load = () => api.get<Data>(`/api/deals/${dealId}/negotiation`).then((r) => { setD(r); setErr(""); reset(r); }).catch((e) => setErr(e.message));
  const reset = (r: Data) => {
    if (!r.ready) return;
    setInp(Object.fromEntries(r.variants.map((v) => [v.id, { mode: "pct", basis: (localStorage.getItem("gsho.ngGross") === "1" ? "gross" : "net") as PIn["basis"], currency: v.currency, value: "" }])));
    setTerms(Object.fromEntries(r.variants.map((v) => [v.id, { client_payments: v.client_payments.map((l) => ({ ...l })), delivery_term: v.delivery_term }])));
    setTermsOn(false); setComment(""); setPrev(null); setConfirm(""); setShowDisc(false);
  };
  useEffect(() => { load(); }, [dealId]);

  const body = useMemo(() => ({
    prices: Object.fromEntries(Object.entries(inp).filter(([, p]) => p.value.trim() !== "").map(([k, p]) => [k, { ...p, value: Number(p.value.replace(",", ".")) }])),
    terms: termsOn ? Object.fromEntries(Object.entries(terms).map(([k, t]) => [k, { client_payments: t.client_payments.map((l, i) => ({ ...l, no: i + 1 })), delivery_term: t.delivery_term }])) : {},
    reason, comment, show_discount: showDisc,
  }), [inp, terms, termsOn, reason, comment, showDisc]);
  const dirty = Object.keys(body.prices).length > 0 || termsOn;
  // живой пересчёт: через полсекунды после ввода
  useEffect(() => {
    window.clearTimeout(timer.current);
    if (!d?.ready || !dirty) { setPrev(null); return; }
    timer.current = window.setTimeout(() => {
      api.post<{ variants: Record<string, Dec> }>(`/api/deals/${dealId}/negotiation/preview`, body).then((r) => { setPrev(r.variants); setErr(""); }).catch((e) => { setPrev(null); setErr(e.message); });
    }, 500);
    return () => window.clearTimeout(timer.current);
  }, [JSON.stringify(body.prices), JSON.stringify(body.terms)]);

  if (!d) return <div className="card">{err ? <div className="alert error">{err}</div> : "Загрузка…"}</div>;
  if (!d.ready) return <div className="card"><p className="muted" style={{ margin: 0 }}>{d.message}</p></div>;

  async function fix(force = false) {
    setBusy(true);
    try {
      const r = await api.post<Step & { kp_revision_no: number }>(`/api/deals/${dealId}/negotiation/steps`, { ...body, confirm_below_threshold: force });
      setMsg(`Шаг ${r.no} зафиксирован, сформировано КП ред. ${r.kp_revision_no}. Выгрузите или отправьте его в истории ниже.`);
      await load(); onChanged();
    } catch (e) {
      const m = (e as Error).message;
      if (/порога|убыток/.test(m) && !force) setConfirm(m); else setErr(m);
    } finally { setBusy(false); }
  }
  const a = d.analytics;
  const show = prev || d.decision;
  const one = new Set(d.variants.map((v) => v.currency)).size === 1;
  const fxCur = one ? d.variants[0]?.currency || "RUB" : "RUB";
  const fx: Fx = { cur: fxCur, rate: one ? d.variants[0]?.kp_rate || 1 : 1, gross, vat: d.vat_pct, ref: d.client_rates[fxCur], client: !!d.client_rates[fxCur],
    n: d.variants.length === 1 ? d.variants[0].units || 1 : 1 };
  const per = (fx.n || 1) > 1 ? " за 1 шт." : "";
  const curs = (v: Var) => [...new Set(["RUB", v.currency, ...Object.keys(d.fx).filter((c) => ["CNY", "USD", "EUR"].includes(c))])];

  return (
    <div className="ng">
      <div className="ng-vat">
        <span className="muted">Цены {fx.cur !== "RUB" ? `в ${fx.cur} (валюта КП), справочно в рублях` : "в рублях"} ·</span>
        <div className="ng-seg">{([[false, "без НДС"], [true, `с НДС ${d.vat_pct} %`]] as const).map(([g, t]) => <button key={t} className={gross === g ? "active" : ""} onClick={() => setG(g)}>{t}</button>)}</div>
        <span className="muted">— так же вводится снижение в новом шаге</span>
        {fx.cur !== "RUB" && <ClientRates dealId={dealId} d={d} cur={fx.cur} reload={load} edit={d.can_edit} />}
      </div>
      <div className="ng-kpis">
        <Kpi t={`Первая цена КП${per}, ${vatLbl(fx)}`} v={inCur(fx, a.first_rub)} s={[rubRef(fx, a.first_rub), totRef(fx, a.first_rub), a.first_kp_on ? `от ${fmtDate(a.first_kp_on)}` : ""].filter(Boolean).join(" · ")} />
        <Kpi t={`Текущая цена${per}, ${vatLbl(fx)}`} v={inCur(fx, a.current_rub)} s={[rubRef(fx, a.current_rub), totRef(fx, a.current_rub), a.steps ? `шагов: ${a.steps}${a.days ? ` · ${a.days} дн.` : ""}` : "торгов ещё не было"].filter(Boolean).join(" · ")} />
        <Kpi t={`Снижение${per}`} v={a.discount_rub > 0 ? inCur(fx, -a.discount_rub, true) : "—"} s={a.discount_rub > 0 ? [rubRef(fx, a.discount_rub), totRef(fx, a.discount_rub)].filter(Boolean).join(" · ") : ""} tone={a.discount_rub > 0 ? "warn" : ""} />
        <Kpi t="Процент утарговывания" v={a.discount_pct != null ? pct(a.discount_pct) : "—"} s="снижение от первой цены КП" tone={a.discount_rub > 0 ? "warn" : ""} />
        <Kpi t="Маржа" v={pct(a.margin_now_pct)} s={a.margin_lost_pp ? `была ${pct(a.margin_first_pct)}, ${a.margin_lost_pp > 0 ? "−" : "+"}${Math.abs(a.margin_lost_pp).toFixed(2).replace(".", ",")} п.п.` : `в КП ${pct(a.margin_first_pct)}`}
          tone={mcls(a.margin_now_pct, Object.values(d.decision)[0]?.threshold_pct ?? 3).replace("ng-", "")} />
        <Kpi t="Лучший конкурент" v={a.best_competitor_rub ? inCur(fx, a.best_competitor_rub) : "—"} s={a.gap_to_competitor_pct != null ? `мы ${a.gap_to_competitor_pct > 0 ? "дороже" : "дешевле"} на ${pct(Math.abs(a.gap_to_competitor_pct))}` : "сведений нет"} />
      </div>
      {(() => { const r = d.steps.find((x) => x.review?.status === "client_review"); return r ? (
        <div className="alert warn">КП шага {r.no} <b>на рассмотрении клиента</b> с {fmtDate(r.review.since)} · уточнить результат {fmtDate(r.review.remind_on)}{r.review.assignee_name ? ` — ${r.review.assignee_name}` : ""}{r.review.note ? ` · ${r.review.note}` : ""}</div>) : null; })()}
      <PriceChart steps={d.steps} first={a.first_rub} />

      <div className="ng-sections">
        {([["steps", "Шаги торгов"], ["competitors", `Конкуренты${d.competitors.length ? ` · ${d.competitors.length}` : ""}`], ["kp", "Услуги и комплектация КП"]] as const).map(([k, t]) => (
          <button key={k} className={section === k ? "active" : ""} onClick={() => setSection(k)}>{t}</button>))}
      </div>
      {err && <div className="alert error">{err}</div>}
      {msg && <div className="alert ok">{msg}</div>}

      {section === "steps" && <>
        {d.can_edit ? (
          <div className="ng-work">
            <div className="card ng-form">
              <h3 className="ng-h">Новый шаг</h3>
              {d.variants.map((v) => { const p = inp[v.id]; if (!p) return null; const set = (x: Partial<PIn>) => setInp({ ...inp, [v.id]: { ...p, ...x } });
                return (
                  <div key={v.id} className="ng-var">
                    {d.variants.length > 1 && <b>{v.name}</b>}
                    <div className="ng-seg">{([["pct", "−  %"], ["amount", "− сумма"], ["target", "новая цена"]] as const).map(([k, t]) => (
                      <button key={k} className={p.mode === k ? "active" : ""} onClick={() => set({ mode: k })}>{t}</button>))}</div>
                    <div className="ng-inrow">
                      <input inputMode="decimal" placeholder={p.mode === "pct" ? "напр. 3" : "сумма"} value={p.value} onChange={(e) => set({ value: e.target.value })} />
                      {p.mode === "pct" ? <span className="muted">% от текущей цены</span> : <>
                        <select value={p.currency} onChange={(e) => set({ currency: e.target.value })}>{curs(v).map((c) => <option key={c} value={c}>{c}{c !== v.currency && d.client_rates[v.currency] && (c === "RUB" || d.client_rates[c]) ? " · по курсу заказчика" : ""}</option>)}</select>
                        <select value={p.basis} onChange={(e) => set({ basis: e.target.value as PIn["basis"] })}><option value="net">без НДС</option><option value="gross">с НДС {d.vat_pct} %</option></select>
                        {(v.units || 1) > 1 && <span className="muted">за 1 шт. × {v.units}</span>}</>}
                    </div>
                  </div>); })}
              <label className="ng-check"><input type="checkbox" checked={termsOn} onChange={(e) => setTermsOn(e.target.checked)} /> изменить условия: оплата, предоплата, срок поставки</label>
              {termsOn && d.variants.map((v) => { const t = terms[v.id]; if (!t) return null; const setT = (x: Partial<typeof t>) => setTerms({ ...terms, [v.id]: { ...t, ...x } });
                const sum = t.client_payments.reduce((s2, l) => s2 + (Number(l.share_pct) || 0), 0);
                const line = (i: number, x: Partial<PaymentLine>) => setT({ client_payments: t.client_payments.map((l, j) => (j === i ? { ...l, ...x } : l)) });
                return (
                  <div key={v.id} className="ng-terms">
                    <div className="ng-terms-h">{d.variants.length > 1 ? <b>{v.name}: </b> : null}<b>График оплаты клиента</b>
                      <span className="muted">сейчас: {v.client_payments.map((l) => `${l.share_pct ?? "—"} % — ${EVENT_TITLES[l.event] || l.event}${l.offset_days ? ` +${l.offset_days} дн.` : ""}`).join("; ") || "по типовой схеме"}</span></div>
                    <div className="ng-pay ng-pay-head"><span>Доля, %</span><span>Событие</span><span>Через, дн.</span><span /></div>
                    {t.client_payments.map((l, i) => (
                      <div key={i} className="ng-pay">
                        <input inputMode="decimal" value={l.share_pct ?? ""} onChange={(e) => line(i, { share_pct: Number(e.target.value.replace(",", ".")) || 0 })} />
                        <select value={l.event} onChange={(e) => line(i, { event: e.target.value })}>{Object.entries(EVENT_TITLES).map(([kk, n]) => <option key={kk} value={kk}>{n}</option>)}</select>
                        <input type="number" value={l.offset_days} onChange={(e) => line(i, { offset_days: Number(e.target.value) })} />
                        <button className="ng-x" title="удалить платёж" onClick={() => setT({ client_payments: t.client_payments.filter((_, j) => j !== i) })}>×</button>
                      </div>))}
                    <div className="ng-pay-foot">
                      <button className="btn ghost" onClick={() => setT({ client_payments: [...t.client_payments, { no: t.client_payments.length + 1, share_pct: Math.max(0, 100 - sum), event: "delivery", offset_days: 0 }] })}>+ платёж</button>
                      <span className={Math.abs(sum - 100) > 0.01 ? "badge bad" : "badge ok"}>итого {sum} %{Math.abs(sum - 100) > 0.01 ? " — нужно 100 %" : ""}</span>
                    </div>
                    <label className="pf-field" style={{ marginTop: 8 }}><span className="pf-label">Срок поставки в КП{v.delivery_term ? ` (сейчас: ${v.delivery_term})` : " (пусто — по расчёту)"}</span>
                      <input value={t.delivery_term} placeholder="напр. 150 дней с даты предоплаты" onChange={(e) => setT({ delivery_term: e.target.value })} /></label>
                  </div>); })}
              <div className="ng-grid2">
                <label className="pf-field"><span className="pf-label">Причина</span>
                  <select value={reason} onChange={(e) => setReason(e.target.value)}>{Object.entries(d.reasons).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select></label>
                <label className="pf-field"><span className="pf-label">В КП</span>
                  <select value={showDisc ? "1" : "0"} onChange={(e) => setShowDisc(e.target.value === "1")}><option value="0">только новая цена</option><option value="1">первая цена и строка «Скидка»</option></select></label>
              </div>
              <label className="pf-field"><span className="pf-label">Комментарий / обоснование {confirm && <b style={{ color: "var(--accent)" }}>— обязательно при цене ниже порога</b>}</span>
                <textarea rows={2} value={comment} placeholder="что просил клиент, что предложили взамен, кто согласовал" onChange={(e) => setComment(e.target.value)} /></label>
              {confirm && <div className="alert warn">Ниже порога: {confirm}. ЗГД получит уведомление.
                <div className="row" style={{ gap: 8, marginTop: 6 }}><button className="btn" disabled={busy || !comment.trim()} onClick={() => fix(true)}>Подтверждаю — зафиксировать</button>
                  <button className="btn ghost" onClick={() => setConfirm("")}>Отмена</button></div></div>}
              {!confirm && <div className="row" style={{ gap: 8, marginTop: 8 }}>
                <button className="btn" disabled={busy || !dirty} onClick={() => fix(false)}>Зафиксировать шаг и сформировать КП</button>
                {dirty && <button className="btn ghost" onClick={() => reset(d)}>Очистить</button>}</div>}
            </div>
            <Decision dec={show} preview={!!prev} vars={d.variants} comps={d.competitors} gross={gross} client={d.client_rates} />
          </div>
        ) : <><div className="alert info">Торги закрыты: сделка на этапе «{(STATUS_TITLES as Record<string, string>)[d.status] || d.status}». История и аналитика — ниже.</div><Decision dec={d.decision} preview={false} vars={d.variants} comps={d.competitors} gross={gross} client={d.client_rates} /></>}
        <History d={d} dealId={dealId} reload={load} fx={fx} />
      </>}
      {section === "competitors" && <CompetitorsTab dealId={dealId} variants={variants} />}
      {section === "kp" && <KpRevisions dealId={dealId} status={d.status} onDone={() => { load(); onChanged(); }} />}
    </div>);
}

function Kpi({ t, v, s, tone = "" }: { t: string; v: string; s?: string; tone?: string }) {
  return <div className={"ng-kpi " + (tone ? "ng-" + tone : "")}><span className="ng-kpi-t">{t}</span><b>{v}</b>{s && <span className="ng-kpi-s">{s}</span>}</div>;
}

/** Данные для решения: до → после, маржа по сценариям, минимальная цена по порогу и безубыточная, конкуренты. */
function Decision({ dec, preview, vars, comps, gross, client }: { dec: Record<string, Dec>; preview: boolean; vars: Var[]; comps: Comp[]; gross: boolean; client: Record<string, number> }) {
  const best = comps.length ? Math.min(...comps.map((c) => c.rub_net)) : null;
  return (
    <div className="card ng-dec">
      <h3 className="ng-h">{preview ? "Если зафиксировать шаг" : "Сейчас"}{vars.some((v) => (v.units || 1) > 1) ? <span className="muted" style={{ fontWeight: 400, fontSize: 12 }}> · цены за 1 шт.</span> : null} <span className="muted" style={{ fontWeight: 400, fontSize: 12 }}>· расчёт по курсу ЦБ на сегодня, базовый сценарий</span></h3>
      {vars.map((v) => { const x0 = dec[v.id]; if (!x0) return null; const thr = x0.threshold_pct;
        // D-085: цены — за 1 шт.; себестоимость, прибыль и разрыв — по сделке
        const u = v.units || 1, sc = (m: Money | null) => (m ? { ...m, net_rub: m.net_rub / u, gross_rub: m.gross_rub / u, net: m.net / u, gross: m.gross / u } : m);
        const x: Dec = { ...x0, price: sc(x0.price)!, before: sc(x0.before)!, first: sc(x0.first)!, min_price_threshold: sc(x0.min_price_threshold), min_price_zero: sc(x0.min_price_zero),
          change_rub: x0.change_rub / u, room_rub: x0.room_rub == null ? null : x0.room_rub / u };
        const bu = best != null ? best / u : null;   // цена конкурента — на то же количество
        const scale = Math.max(x.first.net_rub, x.price.net_rub, bu || 0) * 1.02, lo = Math.min(x.min_price_zero?.net_rub ?? x.price.net_rub, bu ?? Infinity, x.price.net_rub) * 0.97;
        const pos = (rub: number) => `${Math.max(0, Math.min(100, (rub - lo) / (scale - lo) * 100))}%`;
        return (
          <div key={v.id} className="ng-dec-var">
            {vars.length > 1 && <b>{v.name}</b>}
            {(() => { const v2: Fx = { cur: x.price.currency, rate: (x.price.net_rub / (x.price.net || 1)) || 1, ref: client[x.price.currency], client: !!client[x.price.currency], gross, vat: x.price.net_rub ? Math.round((x.price.gross_rub / x.price.net_rub - 1) * 1000) / 10 : 22 }; return (
            <div className="ng-price">
              <div><span className="muted">цена {vatLbl(v2)}</span><b>{mm(x.price, gross)}</b>{x.price.currency !== "RUB" && <span className="muted">{rubRef(v2, x.price.net_rub)}</span>}</div>
              <div><span className="muted">{gross ? "без НДС" : "с НДС"}</span><b>{mm(x.price, !gross)}</b></div>
              <div><span className="muted">к прошлому шагу</span><b className={x.change_rub < 0 ? "ng-warn-t" : ""}>{x.change_rub ? inCur(v2, x.change_rub, true) : "—"}</b><span className="muted">{x.change_pct ? pct(x.change_pct, true) : ""}</span></div>
              <div><span className="muted">к первой цене КП</span><b>{x.from_first_pct ? pct(x.from_first_pct, true) : "—"}</b></div>
            </div>); })()}
            {(() => {
              // шкала цены: отметки на полосе, подписи разведены по рядам, значения — в легенде под шкалой (без наложений)
              const marks = [
                x.min_price_zero && { k: "zero", t: "безубыточность", rub: x.min_price_zero.net_rub, m: x.min_price_zero },
                x.min_price_threshold && { k: "thr", t: `порог ${thr} %`, rub: x.min_price_threshold.net_rub, m: x.min_price_threshold },
                bu && { k: "comp", t: "конкурент", rub: bu, m: null },
                { k: "first", t: "1-я цена КП", rub: x.first.net_rub, m: x.first },
                { k: "now", t: preview ? "новая цена" : "сейчас", rub: x.price.net_rub, m: x.price },
              ].filter(Boolean) as { k: string; t: string; rub: number; m: Money | null }[];
              const pct = (rub: number) => Math.max(0, Math.min(100, (rub - lo) / (scale - lo) * 100));
              const last = [-100, -100, -100, -100];
              const placed = [...marks].sort((a, b) => pct(a.rub) - pct(b.rub)).map((mk) => {
                const p = pct(mk.rub);
                let row = [0, 1, 2, 3].find((r) => p - last[r] >= 16);
                if (row === undefined) row = last.indexOf(Math.min(...last));
                last[row] = p;
                return { ...mk, p, row };
              });
              const rate = (x.price.net_rub / (x.price.net || 1)) || 1;
              const val = (mk: { rub: number; m: Money | null }) => mk.m ? mm(mk.m, gross) : `${fmtRub(mk.rub * (gross ? x.price.gross_rub / (x.price.net_rub || 1) : 1) / rate)} ${sym(x.price.currency)}`;
              return <>
                <div className="ng-scale2">
                  {placed.filter((m) => m.row < 2).map((m) => <span key={m.k + "t"} className={`ng-lbl ng-c-${m.k}`} style={{ left: `${m.p}%`, top: m.row === 0 ? 14 : 0 }}>{m.t}</span>)}
                  <div className="ng-scale-bar2" />
                  {placed.map((m) => <i key={m.k} className={`ng-mark2 ng-b-${m.k}`} style={{ left: `${m.p}%` }} title={`${m.t}: ${val(m)}`} />)}
                  {placed.filter((m) => m.row >= 2).map((m) => <span key={m.k + "b"} className={`ng-lbl ng-c-${m.k}`} style={{ left: `${m.p}%`, top: m.row === 2 ? 52 : 66 }}>{m.t}</span>)}
                </div>
                <div className="ng-legend">{[...placed].sort((a, b) => a.p - b.p).map((m) => <span key={m.k}><i className={`ng-b-${m.k}`} />{m.t}: <b>{val(m)}</b></span>)}</div>
              </>; })()}
            {(() => {
              // D-088: только базовый сценарий; суммы — на 1 машину и (при количестве больше 1) за всё количество
              const r = (x.price.net_rub / (x.price.net || 1)) || 1, c = x.price.currency, g = gross ? x.price.gross_rub / (x.price.net_rub || 1) : 1;
              const money1 = (rubPerUnit: number | null | undefined, vat = false) => (rubPerUnit == null ? "—" : `${fmtRub(rubPerUnit * (vat ? g : 1) / r)} ${sym(c)}`);
              const row = (t: string, perUnitRub: number | null | undefined, opts: { vat?: boolean; strong?: boolean; cls?: string; note?: string } = {}) => (
                <tr><td>{t}</td><td className="num">{opts.strong ? <b className={opts.cls}>{money1(perUnitRub, opts.vat)}</b> : money1(perUnitRub, opts.vat)}{opts.note && <span className="muted"> {opts.note}</span>}</td>
                  {u > 1 && <td className="num muted">{perUnitRub == null ? "—" : money1(perUnitRub * u, opts.vat)}</td>}</tr>);
              const roomPct = x.room_pct;
              return (
                <table className="ng-mt"><thead><tr><th /><th className="num">{u > 1 ? "на 1 шт." : ""}</th>{u > 1 && <th className="num">за {u} шт.</th>}</tr></thead><tbody>
                  <tr><td>Маржа (базовый сценарий)</td><td className="num"><b className={mcls(x.margin.base, thr)}>{pct(x.margin.base)}</b> <span className="muted">порог {thr} %</span></td>{u > 1 && <td />}</tr>
                  {row(`Минимальная цена по порогу, ${gross ? "с НДС" : "без НДС"}`, x.min_price_threshold?.net_rub, { vat: true, strong: true })}
                  {row("Запас снижения до порога", x.room_rub, { vat: true, strong: true, cls: x.room_rub != null && x.room_rub < 0 ? "ng-bad" : "", note: roomPct != null ? pct(roomPct) : "" })}
                  {row(`Безубыточная цена, ${gross ? "с НДС" : "без НДС"}`, x.min_price_zero?.net_rub, { vat: true })}
                  {row("Себестоимость без НДС", x0.cost_rub == null ? null : x0.cost_rub / u)}
                  {row("Прибыль", x0.profit_rub == null ? null : x0.profit_rub / u)}
                  {row("Кассовый разрыв", x0.cash_gap_rub == null ? null : x0.cash_gap_rub / u, { note: x0.cash_gap_days != null ? `${x0.cash_gap_days} дн.` : "" })}
                  {row("Стоимость денег", x0.finance_rub == null ? null : x0.finance_rub / u)}
                  {bu != null && <tr><td>К лучшему конкуренту</td><td className="num"><b>{pct((x.price.net_rub - bu) / bu * 100, true)}</b> <span className="muted">{x.price.net_rub - bu >= 0 ? "+" : "−"}{money1(Math.abs(x.price.net_rub - bu), true)}</span></td>
                    {u > 1 && <td className="num muted">{x.price.net_rub - bu >= 0 ? "+" : "−"}{money1(Math.abs(x.price.net_rub - bu) * u, true)}</td>}</tr>}
                </tbody></table>);
            })()}
            {x.warnings.map((w, i) => <div key={i} className="alert warn" style={{ margin: "6px 0 0" }}>{w}</div>)}
          </div>); })}
    </div>);
}

/** Цена по шагам торгов — линия от первой цены КП. */
function PriceChart({ steps, first }: { steps: Step[]; first: number }) {
  if (!steps.length) return null;
  const pts = [first, ...steps.map((s) => s.price_rub)];
  const w = 600, h = 70, max = Math.max(...pts), min = Math.min(...pts) * 0.995, span = max - min || 1;
  const xy = pts.map((p, i) => [8 + (i * (w - 16)) / Math.max(pts.length - 1, 1), 8 + (1 - (p - min) / span) * (h - 22)]);
  return (
    <div className="card ng-chart"><svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ width: "100%", height: 80 }}>
      <polyline fill="none" stroke="var(--accent)" strokeWidth="2" points={xy.map((p) => p.join(",")).join(" ")} />
      {xy.map(([x, y], i) => <g key={i}><circle cx={x} cy={y} r="3.5" fill={i ? "var(--accent)" : "#222"} />
        <text x={x} y={h - 2} fontSize="10" textAnchor={i === 0 ? "start" : i === xy.length - 1 ? "end" : "middle"} fill="#888">{i ? `шаг ${i}` : "КП"}</text></g>)}
    </svg></div>);
}

/** История: первая цена КП и шаги — цена, изменение, маржа, условия, КП (Word / PDF), отправка, ответ клиента. */
function History({ d, dealId, reload, fx }: { d: Data; dealId: string; reload: () => void; fx: Fx }) {
  const [send, setSend] = useState<string | null>(null);
  const [rcp, setRcp] = useState<{ recipients: { name: string; email: string }[]; mail_configured: boolean; body: string } | null>(null);
  const [to, setTo] = useState("");
  const [text, setText] = useState("");
  const [ans, setAns] = useState<Record<string, string>>({});
  const [e, setE] = useState("");
  const plus = (n: number) => { const x = new Date(); x.setDate(x.getDate() + n); return x.toISOString().slice(0, 10); };
  const [rv, setRv] = useState<{ id: string; remind_on: string; assignee: string; note: string } | null>(null);
  const saveReview = () => rv && api.put(`/api/deals/${dealId}/negotiation/steps/${rv.id}/review`, { remind_on: rv.remind_on, assignee: rv.assignee, note: rv.note })
    .then(() => { setRv(null); reload(); }).catch((x) => setE(x.message));
  const closeReview = (id: string) => api.post(`/api/deals/${dealId}/negotiation/steps/${id}/review/close`, { client_answer: ans[id] ?? "" }).then(reload).catch((x) => setE(x.message));
  const open = (id: string) => {
    setSend(id); setE("");
    api.get<typeof rcp>(`/api/deals/${dealId}/kp/recipients`).then((r) => { setRcp(r); setTo((r?.recipients || []).map((x) => x.email).join(", ")); setText(r?.body || ""); }).catch(() => setRcp(null));
  };
  const doSend = (id: string, manual: boolean) => api.post(`/api/deals/${dealId}/negotiation/steps/${id}/send`, { to: to.split(/[,;\s]+/).filter(Boolean), body: text, manual })
    .then(() => { setSend(null); reload(); }).catch((x) => setE(x.message));
  const saveAns = (id: string) => api.put(`/api/deals/${dealId}/negotiation/steps/${id}/answer`, { client_answer: ans[id] ?? "" }).then(reload).catch((x) => setE(x.message));
  const first = d.analytics.first_rub;
  const pay = (l: PaymentLine[]) => l.map((x) => `${x.share_pct ?? "—"} % — ${EVENT_TITLES[x.event] || x.event}${x.offset_days ? ` +${x.offset_days} дн.` : ""}`).join("; ");
  return (
    <div className="card">
      <h3 className="ng-h">История торгов <span className="muted" style={{ fontWeight: 400, fontSize: 12 }}>· цены{(fx.n || 1) > 1 ? " за 1 шт." : ""} {vatLbl(fx)}</span></h3>
      {e && <div className="alert error">{e}</div>}
      <div className="ng-tl">
        <div className="ng-tl-item ng-tl-first"><span className="ng-dot" /><div className="ng-tl-body">
          <div className="ng-tl-head"><b>Первое КП</b><span className="muted">{fmtDate(d.analytics.first_kp_on)}</span><b className="ng-tl-price">{inCur(fx, first)}</b><span className="muted">{rubRef(fx, first)}</span>
            <span className="muted">маржа {pct(d.analytics.margin_first_pct)}</span>
            <span className="ng-files"><a href={`/api/deals/${dealId}/kp/file?format=docx&original=1`}>Word</a> · <a href={`/api/deals/${dealId}/kp/file?format=pdf&original=1`}>PDF</a></span></div></div></div>
        {d.steps.map((s) => { const dif = s.price_rub - s.price_before_rub; return (
          <div key={s.id} className="ng-tl-item"><span className="ng-dot" /><div className="ng-tl-body">
            <div className="ng-tl-head"><b>Шаг {s.no}</b><span className="muted">{fmtDate(s.at)} · {s.by_name}</span><span className="badge">{s.reason_title}</span>
              <b className="ng-tl-price">{inCur(fx, s.price_rub)}</b><span className="muted">{rubRef(fx, s.price_rub)}</span>
              {Math.abs(dif) > 0.5 && <span className={dif < 0 ? "badge warn" : "badge ok"}>{inCur(fx, dif, true)} · {pct(dif / s.price_before_rub * 100, true)}</span>}
              <span className="muted">от первой {pct((s.price_rub - first) / first * 100, true)}</span>
              <span className="muted">маржа {pct(s.margin_pct)}</span>
              <span className="ng-files"><a href={`/api/deals/${dealId}/kp/revisions/${s.kp_revision_id}/file?format=docx`}>Word</a> · <a href={`/api/deals/${dealId}/kp/revisions/${s.kp_revision_id}/file?format=pdf`}>PDF</a></span>
              {s.sent_at ? <span className="badge ok" title={s.sent_to.join(", ")}>отправлено {fmtDate(s.sent_at)}</span>
                : d.can_edit && <button className="btn ghost" onClick={() => open(s.id)}>✉ отправить клиенту</button>}
              {s.review?.status === "client_review" && <span className="badge ng-review" title={s.review.note || ""}>на рассмотрении клиента · напомнить {fmtDate(s.review.remind_on)}{s.review.assignee_name ? ` (${s.review.assignee_name})` : ""}</span>}
              {d.can_edit && s.review?.status !== "client_review" && s.no === d.steps[d.steps.length - 1]?.no && <button className="btn ghost" onClick={() => setRv({ id: s.id, remind_on: plus(3), assignee: "manager", note: "" })}>⏳ на рассмотрении клиента</button>}</div>
            {rv?.id === s.id && <div className="ng-send">
              <b>На рассмотрении клиента — когда уточнить результат?</b>
              <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                {[1, 3, 5, 7].map((n) => <button key={n} className={"btn " + (rv.remind_on === plus(n) ? "" : "ghost")} style={{ padding: "2px 10px" }} onClick={() => setRv({ ...rv, remind_on: plus(n) })}>через {n} дн.</button>)}
                <input type="date" value={rv.remind_on} onChange={(x) => setRv({ ...rv, remind_on: x.target.value })} />
                <select value={rv.assignee} onChange={(x) => setRv({ ...rv, assignee: x.target.value })}><option value="manager">напомнить менеджеру сделки</option><option value="me">напомнить мне</option></select></div>
              <input placeholder="что ждём от клиента (необязательно)" value={rv.note} onChange={(x) => setRv({ ...rv, note: x.target.value })} />
              <div className="row" style={{ gap: 8 }}><button className="btn" onClick={saveReview}>Установить статус и напоминание</button><button className="btn ghost" onClick={() => setRv(null)}>Отмена</button></div></div>}
            {Object.entries(s.input).map(([vid, i]) => <div key={vid} className="muted ng-tl-line">{d.variants.length > 1 ? `${s.result[vid]?.name}: ` : ""}
              {i.mode === "pct" ? `снижение ${i.value} %` : i.mode === "amount" ? `снижение на ${fmtRub(i.value)} ${i.currency} ${i.basis === "gross" ? "с НДС" : "без НДС"}` : `новая цена ${fmtRub(i.value)} ${i.currency} ${i.basis === "gross" ? "с НДС" : "без НДС"}`}
              {(i.units || 1) > 1 && i.mode !== "pct" ? ` за 1 шт. (× ${i.units})` : ""}{s.show_discount ? " · в КП строкой «Скидка»" : ""}</div>)}
            {Object.entries(s.terms).map(([vid, t]) => <div key={vid} className="muted ng-tl-line">{t.client_payments && <>оплата: {pay(t.client_payments)}</>}{t.delivery_term && <> · срок: {t.delivery_term}</>}</div>)}
            {s.comment && <div className="ng-tl-line">{s.comment}</div>}
            {send === s.id && <div className="ng-send">
              <input value={to} placeholder="e-mail получателей через запятую" onChange={(x) => setTo(x.target.value)} />
              <textarea rows={3} value={text} onChange={(x) => setText(x.target.value)} />
              <div className="row" style={{ gap: 8 }}>
                <button className="btn" disabled={!rcp?.mail_configured || !to.trim()} title={rcp?.mail_configured ? "" : "почта не подключена"} onClick={() => doSend(s.id, false)}>Отправить из системы (PDF)</button>
                <button className="btn ghost" disabled={!to.trim()} onClick={() => doSend(s.id, true)}>Отправил своей почтой — отметить</button>
                <button className="btn ghost" onClick={() => setSend(null)}>Отмена</button></div>
              {!rcp?.mail_configured && <span className="muted" style={{ fontSize: 12 }}>Почта не подключена: скачайте PDF или Word выше, отправьте и отметьте отправку.</span>}</div>}
            <div className="ng-answer"><input placeholder="ответ клиента: принял / просит ещё / ушёл к конкуренту…" value={ans[s.id] ?? s.client_answer} disabled={!d.can_edit}
              onChange={(x) => setAns({ ...ans, [s.id]: x.target.value })} />
              {s.review?.status === "client_review" && d.can_edit ? <button className="btn secondary" onClick={() => closeReview(s.id)}>Клиент ответил</button>
                : (ans[s.id] ?? s.client_answer) !== s.client_answer && <button className="btn ghost" onClick={() => saveAns(s.id)}>сохранить</button>}</div>
          </div></div>); })}
        {d.steps.length === 0 && <p className="muted" style={{ marginLeft: 22 }}>Шагов пока нет. Каждое снижение или изменение условий фиксируйте здесь — по каждому шагу формируется КП.</p>}
      </div>
    </div>);
}

/** Внутренние курсы заказчика (D-079): только для торгов — пересчёт рублей и ввод снижения в рублях; расчёт сделки — по ЦБ. */
function ClientRates({ dealId, d, cur, reload, edit }: { dealId: string; d: Data; cur: string; reload: () => void; edit: boolean }) {
  const [open, setOpen] = useState(false);
  const [val, setVal] = useState(d.client_rates[cur] ? String(d.client_rates[cur]) : "");
  const [comment, setComment] = useState("");
  const [err, setErr] = useState("");
  const cbr = d.fx[cur];
  const set = d.client_rates[cur];
  const save = (rates: Record<string, number | string>) => api.put(`/api/deals/${dealId}/negotiation/client-rates`, { rates, comment })
    .then(() => { setOpen(false); setComment(""); setErr(""); reload(); }).catch((e) => setErr((e as Error).message));
  return (
    <span className="ng-rates">
      <span className={set ? "badge warn" : "muted"}>{set ? `курс заказчика ${cur}: ${String(set).replace(".", ",")} ₽` : `рубли — по курсу цены КП`}</span>
      {cbr && <span className="muted">ЦБ {cbr.toFixed(4).replace(".", ",")}</span>}
      {edit && !open && <button className="btn ghost" style={{ padding: "1px 8px" }} onClick={() => setOpen(true)}>{set ? "изменить" : "задать курс заказчика"}</button>}
      {open && <span className="ng-rates-form">
        <input inputMode="decimal" placeholder={`₽ за 1 ${cur}`} value={val} style={{ width: 110 }} onChange={(e) => setVal(e.target.value)} />
        <input placeholder="откуда курс (необязательно)" value={comment} onChange={(e) => setComment(e.target.value)} />
        <button className="btn" disabled={!val.trim()} onClick={() => save({ ...d.client_rates, [cur]: val })}>Применить</button>
        {set && <button className="btn ghost" onClick={() => { const r = { ...d.client_rates }; delete r[cur]; save(r); }}>Снять</button>}
        <button className="btn ghost" onClick={() => setOpen(false)}>×</button></span>}
      {err && <span className="badge bad">{err}</span>}
      {d.client_rates_history.length > 0 && <details className="ng-rates-h"><summary className="muted">история</summary>
        {[...d.client_rates_history].reverse().map((h, i) => <div key={i} className="muted" style={{ fontSize: 12 }}>{fmtDate(h.at)} · {h.by_name}: {Object.entries(h.rates).map(([c, r]) => `${c} ${r}`).join(", ") || "сняты"}{h.comment ? ` · ${h.comment}` : ""}</div>)}</details>}
      <span className="muted" style={{ fontSize: 12 }}>— только для торгов; расчёт сделки и маржа — по курсам ЦБ</span>
    </span>);
}
