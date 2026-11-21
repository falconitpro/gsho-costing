import { useEffect, useMemo, useState } from "react";
import { lbl, FX_SOURCE, IMAGE_KIND, IMAGE_SOURCE, OUTCOME, STATUS } from "../labels";
import { api, fmtDate, fmtPct, fmtCur, fmtRub, designation } from "../api";
import { COST_ITEMS, EVENT_TITLES, can, type CalcResponse, type Deal, type Me, type Position, type ScenarioResult } from "../types";
import CashflowChart from "../components/CashflowChart";
import PaymentTermsEditor from "../components/PaymentTermsEditor";
import ProductionTermBlock from "../components/ProductionTermBlock";
import { useNavigate } from "react-router-dom";

const SCEN = [["pessimistic", "Пессимистичный"], ["base", "Базовый"], ["optimistic", "Оптимистичный"]] as const;

/** Экран утверждения КД (раздел 10): слева варианты, справа 14 статей × 3 сценария, цена с живым пересчётом, индикатор маржи,
 *  денежный поток, комментарии, история цен, курсы. ФД видит то же и даёт визу. */
export default function ApprovalScreen({ deal, me, reload }: { deal: Deal; me: Me; reload: () => void }) {
  const nav = useNavigate();
  const [calc, setCalc] = useState<CalcResponse | null>(null);
  const [vid, setVid] = useState<string>((deal.variants || [])[0]?.id || "");
  const [mode, setMode] = useState<"amount" | "markup" | "margin">("amount");
  const [value, setValue] = useState("");
  const [cur, setCur] = useState("");   // валюта цены клиенту; пусто — ранее выбранная или валюта предложения завода
  const [withVat, setWithVat] = useState(false);   // сумма вводится с НДС (D-066)
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [tab, setTab] = useState<"cost" | "money" | "kp">("cost");   // D-098: три подвкладки вместо семи
  const [rework, setRework] = useState({ block: "logistics", reason: "", reason_code: "", comment: "" });
  // D-100: причина возврата — из справочника («Настройки → Ставки и нормативы → Возвраты на доработку»), комментарий — по желанию
  const [reasons, setReasons] = useState<{ code: string; title: string; block: string }[] | null>(null);
  const [visaComment, setVisaComment] = useState("");
  const [insistComment, setInsistComment] = useState("");
  const isCD = me.roles.some((r) => r.code === "cd" || r.code === "gd");
  const canTerms = can(me, "payment_terms", 2);
  const isFD = me.roles.some((r) => r.code === "fd") || !!me.acting_roles?.includes("fd");     // D-112: заместитель ФД визирует за него
  const isGD = me.roles.some((r) => r.code === "gd") || !!me.acting_roles?.includes("gd");
  useEffect(() => { if (isCD || isFD) api.get<{ items: { code: string; title: string; block: string }[] }>("/api/rework-reasons").then((r) => setReasons(r.items)).catch(() => setReasons(null)); }, [isCD, isFD]);
  const blockAllowed = (b: string) => b === "logistics" || b === "pricing" || b === "gd" || (b === "manager" && isCD) || (b === "service" && !!deal.service_flag);
  const pickReason = (code: string) => {
    const r = reasons?.find((x) => x.code === code);
    setRework({ ...rework, reason_code: code, block: r && r.block && blockAllowed(r.block) ? r.block : rework.block });
  };
  const reworkReady = reasons ? !!rework.reason_code && (rework.reason_code !== "other" || !!rework.comment.trim()) : !!rework.reason.trim();

  const [repDate, setRepDate] = useState(deal.report_date || "");
  async function saveReportDate(v: string | null) {
    setBusy(true);
    try { await api.put(`/api/deals/${deal.id}/report-date`, { report_date: v }); reload(); await load(); setErr(""); }
    catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  async function load() {
    try { setCalc(await api.get<CalcResponse>(`/api/deals/${deal.id}/calc`)); setErr(""); }
    catch (e) { setErr((e as Error).message); }
  }
  useEffect(() => { load(); }, [deal.id, deal.status]);

  const scen = calc?.result.variants[vid];
  const base = scen?.base;
  const thr = calc?.threshold_by_variant?.[vid] ?? calc?.threshold_pct ?? 3;   // порог по стоимости сделки (D-067)

  const indicator = useMemo(() => {
    if (!base || base.margin_pct == null) return { cls: "", text: "цена не задана" };
    if ((scen?.pessimistic?.margin_pct ?? 0) < 0) return { cls: "bad", text: "отрицательная маржа в пессимистичном сценарии" };
    if (base.margin_pct < thr) return { cls: "warn", text: `ниже порога ${thr} % — нужна виза ФД` };
    return { cls: "ok", text: `выше порога ${thr} %` };
  }, [base, scen, thr]);

  async function setPrice() {
    if (!value) return;
    setBusy(true);
    try {
      await api.post(`/api/deals/${deal.id}/price`, { variant_id: vid, mode, value: Number(value), currency: priceCur, vat_included: mode === "amount" && withVat, per_unit: true });
      await load(); reload(); setCur(""); setErr("");
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  async function toggleService() {
    const v = (deal.variants || []).find((x) => x.id === vid);
    if (!v || base?.price_rub == null) return;
    const keep = v.price_currency && v.price_amount != null ? { value: v.price_amount, currency: v.price_currency } : { value: base.price_rub, currency: "RUB" };
    await api.post(`/api/deals/${deal.id}/price`, { variant_id: vid, mode: "amount", ...keep, per_unit: false, service_in_kp: !v.service_in_kp });
    reload(); load();
  }
  async function act(url: string, body?: unknown) {
    setBusy(true);
    try { await api.post(url, body); setErr(""); reload(); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }

  if (err && !calc) return <div className="alert warn">{err}</div>;
  if (!calc || !scen) return <div className="muted">Загрузка расчёта…</div>;

  const variant = (deal.variants || []).find((v) => v.id === vid);
  if (!variant) return <div className="muted">Нет предложений</div>;
  // валюта предложения завода — самая частая валюта цен позиций предложения
  const posCurs = (deal.positions || []).filter((p) => !variant.position_ids?.length || variant.position_ids.includes(p.id)).map((p) => (p.currency || "").toUpperCase()).filter(Boolean);
  const offerCur = posCurs.sort((a, b) => posCurs.filter((x) => x === b).length - posCurs.filter((x) => x === a).length)[0] || (deal.base_currency || "CNY").toUpperCase();
  // D-080: валюта сделки — по умолчанию валюта предложения поставщика; цена, введённая в другой валюте, пересчитывается в неё
  const dealCur = (calc.deal_currency?.code || offerCur).toUpperCase();
  const priceCur = cur || dealCur;   // валюта ввода суммы
  const units = calc.units?.[variant.id] || 1;     // D-085: цена — за 1 шт.
  const curs = Array.from(new Set(["RUB", offerCur, ...Object.keys(calc.input.fx.rates || {})])).filter((c) => c === "RUB" || calc.input.fx.rates[c]);
  const inCur = (v: { price_currency?: string | null; price_amount?: number | null }) =>
    v.price_currency && v.price_currency !== "RUB" && v.price_amount != null ? `${fmtRub(v.price_amount)} ${v.price_currency}` : "";
  const canPrice = isCD && ["calc_ready", "cd_approval", "rework", "fd_visa", "gd_visa"].includes(deal.status);   // после утверждения цена меняется только в «Торгах» (D-074)
  const cdChecks = calc.cd_checks || [];
  const vatPct = calc.sales_vat_pct ?? 22;
  const cc = calc.contract_currency;
  const terms = calc.terms?.[vid] || [];

  return (
    <div className="grid approval-grid">
      <div className="approval-side">
        {(deal.variants || []).length > 1 && <div className="card">
          <h2 style={{ marginTop: 0 }}>{deal.has_alternatives ? "Предложения (альтернативы в КП)" : "Предложение"}</h2>
          {(deal.variants || []).map((v) => {
            const b = calc.result.variants[v.id]?.base;
            return (
              <div key={v.id} onClick={() => setVid(v.id)} style={{ padding: 8, borderRadius: 6, cursor: "pointer", background: v.id === vid ? "var(--accent-soft)" : "transparent", marginBottom: 4 }}>
                <b>{v.name}</b>
                <div className="muted">цена {inCur(v) ? `${inCur(v)} (≈ ${fmtRub(b?.price_rub)} RUB)` : fmtRub(b?.price_rub)} · маржа {fmtPct(b?.margin_pct)}</div>
              </div>
            );
          })}
        </div>}
        {/* D-126: курсы на сегодня — в шапке; здесь — только если расчёт сделан на другую дату или не по курсу ЦБ */}
        {(calc.input.fx.as_of !== new Date().toISOString().slice(0, 10) || calc.input.fx.source !== "cbr") && <div className="card">
          <h2 style={{ marginTop: 0 }}>Курс расчёта на {fmtDate(calc.input.fx.as_of)}</h2>
          {Object.entries(calc.input.fx.rates).filter(([c]) => c !== "RUB").map(([c, r]) => <div key={c}>{c}: <b>{r.toFixed(4)}</b></div>)}
          {calc.input.fx.source !== "cbr" && <span className="badge warn">{lbl(FX_SOURCE, calc.input.fx.source)}</span>}
        </div>}
        {isCD && deal.manager_id === me.id && !["approved", "kp_sent"].includes(deal.status) && (
          <div className="alert warn">Вы ведёте эту сделку как менеджер и сами её утверждаете. Правило «считал — не утверждает»: об утверждении будет уведомлён ЗГД, пометка останется в ленте и журнале.</div>)}
        {(calc.warnings.length > 0 || calc.result.approval.reasons.length > 0 || cdChecks.length > 0) && (
          <div className="card">
            {cdChecks.map((r, i) => <div key={"c" + i} className="alert error">Перед утверждением: {r}</div>)}
            {calc.result.approval.reasons.map((r, i) => <div key={i} className="alert warn">{r}</div>)}
            {calc.warnings.map((w, i) => <div key={i} className="alert warn">{w}</div>)}
          </div>
        )}
      </div>

      <div className="approval-main">
        {(() => {
          // D-098: панель решения — главное по сделке и одно главное действие; закреплена при прокрутке
          const dR = dealCur === "RUB" ? 1 : (variant.price_currency || "").toUpperCase() === dealCur && variant.price_fx_rate ? variant.price_fx_rate : calc.input.fx.rates[dealCur] || 0;
          const inC = (rub?: number | null) => (rub == null ? "—" : dealCur !== "RUB" && dR ? `${fmtRub(rub / dR)} ${dealCur}` : `${fmtRub(rub)} ₽`);   // на панели — без копеек, точные цифры — в таблице
          const rubRef = (rub?: number | null) => (rub != null && dealCur !== "RUB" ? <small>≈ {fmtRub(rub)} ₽</small> : null);
          const per = (v?: number | null) => (v == null ? null : v / units);
          const canSubmit = isCD && ["calc_ready", "cd_approval", "rework"].includes(deal.status);
          const onVisa = (isFD && deal.status === "fd_visa") || (isGD && deal.status === "gd_visa");
          const toActions = () => document.getElementById("approval-actions")?.scrollIntoView({ behavior: "smooth", block: "start" });
          return (
            <div className="dp">
              <div className={"dp-m " + indicator.cls}><b>{fmtPct(base?.margin_pct)}</b><span>маржа · {indicator.text}</span></div>
              <div className="dp-k"><span>Цена{units > 1 ? " за 1 шт." : ""} без НДС</span><b>{inC(per(base?.price_rub))}</b>{rubRef(per(base?.price_rub))}</div>
              <div className="dp-k"><span>Себестоимость{units > 1 ? " 1 машины" : ""}</span><b>{inC(per(base?.cost_total_rub))}</b>{rubRef(per(base?.cost_total_rub))}</div>
              <div className="dp-k"><span>Прибыль{units > 1 ? " с 1 машины" : ""}</span><b>{inC(per(base?.profit_rub))}</b><small>{units > 1 ? `за ${units} шт.: ${inC(base?.profit_rub)}` : rubRef(base?.profit_rub)}</small></div>
              <div className="dp-k"><span>Кассовый разрыв{units > 1 ? " на 1 машину" : ""}</span><b>{inC(per(base?.cash_gap.amount_rub))}</b><small>{base?.cash_gap.days ?? 0} дн.{units > 1 ? ` · за ${units} шт.: ${inC(base?.cash_gap.amount_rub)}` : ""}</small></div>
              <div className="dp-act">
                {canSubmit && <button className="btn" disabled={busy || cdChecks.length > 0 || base?.price_rub == null} title={cdChecks.length ? cdChecks.join("\n") : base?.price_rub == null ? "сначала назначьте цену" : ""}
                  onClick={() => act(`/api/deals/${deal.id}/submit`)}>Утвердить / отправить на визу</button>}
                {onVisa && <button className="btn" onClick={toActions}>К визе ↓</button>}
                {isCD && ["approved", "kp_sent"].includes(deal.status) && <button className="btn" onClick={() => nav(`/deals/${deal.id}/negotiation`)} title="снижение цены и изменение условий после КП — шагами торгов">Торги →</button>}
                {!canSubmit && !onVisa && !(isCD && ["approved", "kp_sent"].includes(deal.status)) && <span className="muted" style={{ fontSize: 12 }}>{lbl(STATUS, deal.status)}</span>}
              </div>
            </div>);
        })()}
        <div className="card">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <div>
              {base?.price_rub != null && (() => {
                // D-085: при количестве больше одной цена фиксируется за 1 шт., итог на количество — справочно
                const neg = calc.negotiated?.[variant.id];
                const c = variant.price_currency || "RUB", k = 1 + vatPct / 100;
                const fxr = c === "RUB" ? 1 : variant.price_fx_rate || 1;
                const amt = neg?.price_rub != null ? base.price_rub / fxr : variant.price_amount ?? base.price_rub;   // D-093: после торгов — актуальная цена
                return <div style={{ fontSize: 13 }}>
                  {neg?.price_rub != null && <div className="alert info" style={{ margin: "0 0 6px" }}>После торгов: расчёт по <b>актуальной цене</b> (КП ред. {neg.revision_no})
                    {" "}— {fmtRub(neg.price_rub / fxr / units)} {c}{units > 1 ? " за 1 шт." : ""} без НДС; первая цена КП — {fmtRub((neg.first_rub ?? 0) / fxr / units)} {c}
                    {" "}({(((neg.price_rub - (neg.first_rub ?? neg.price_rub)) / (neg.first_rub || 1)) * 100).toFixed(2).replace(".", ",")} %).{neg.client_payments ? " Условия оплаты — из торгов." : ""}</div>}
                  {neg?.price_rub == null && neg?.client_payments && <div className="alert info" style={{ margin: "0 0 6px" }}>Условия оплаты — из торгов (шаг {neg.step_no}).</div>}
                  <details className="dp-more"><summary>Цена подробно: с НДС {vatPct} %{units > 1 ? `, за ${units} шт.` : ""}, курс, введённая сумма</summary>
                  <div>Цена клиенту{units > 1 ? " за 1 шт." : ""} без НДС: <b>{fmtRub(amt / units)} {c}</b>{c !== "RUB" && <span className="muted"> ≈ {fmtRub(base.price_rub / units)} RUB</span>}
                    {" "}· с НДС {vatPct} %: <b>{fmtRub(amt * k / units)} {c}</b>{c !== "RUB" && <span className="muted"> ≈ {fmtRub(base.price_rub * k / units)} RUB</span>}</div>
                  {units > 1 && <div className="muted">за {units} шт. (справочно): без НДС {fmtRub(amt)} {c} · с НДС {fmtRub(amt * k)} {c}{c !== "RUB" ? ` ≈ ${fmtRub(base.price_rub)} RUB без НДС` : ""}</div>}
                  {c !== "RUB" && <div className="muted">курс цены {variant.price_fx_rate?.toFixed(4)} · КП — в {c}</div>}
                  {variant.price_input_amount != null && (variant.price_vat_included || (variant.price_input_currency && variant.price_input_currency !== variant.price_currency) || (variant.price_input_units || 1) > 1) &&
                    <div className="muted">введено: {fmtRub(variant.price_input_amount)} {variant.price_input_currency || variant.price_currency}{variant.price_vat_included ? " с НДС" : " без НДС"}{(variant.price_input_units || 1) > 1 ? " за 1 шт." : ""}</div>}
                  </details>
                </div>; })()}
              {base?.price_rub != null && <div style={{ fontSize: 13 }}>
                {cc && cc.code !== (variant.price_currency || "RUB") && cc.rate && <div className="muted">в валюте контракта ({cc.code}, {cc.source}): {fmtRub(base.price_rub / cc.rate)} без НДС · {fmtRub(base.price_rub * (1 + vatPct / 100) / cc.rate)} с НДС</div>}
                {mode === "amount" && value && Number(value) > 0 && priceCur !== dealCur && (priceCur === "RUB" || calc.input.fx.rates[priceCur]) && (dealCur === "RUB" || calc.input.fx.rates[dealCur]) &&
                  <div className="muted">в валюте сделки: <b>{fmtRub((withVat ? Number(value) / (1 + vatPct / 100) : Number(value)) * (priceCur === "RUB" ? 1 : calc.input.fx.rates[priceCur]) / (dealCur === "RUB" ? 1 : calc.input.fx.rates[dealCur]))} {dealCur}</b> без НДС по курсу ЦБ</div>}
                {mode === "amount" && value && Number(value) > 0 && units > 1 && <div className="muted">за {units} шт.: <b>{fmtRub((withVat ? Number(value) / (1 + vatPct / 100) : Number(value)) * units)} {priceCur}</b> без НДС</div>}
                {mode === "amount" && value && Number(value) > 0 && <div className="muted">вводимая сумма{units > 1 ? " за 1 шт." : ""}: без НДС {fmtRub(withVat ? Number(value) / (1 + vatPct / 100) : Number(value))} · с НДС {fmtRub(withVat ? Number(value) : Number(value) * (1 + vatPct / 100))} {priceCur}
                  {priceCur !== "RUB" && calc.input.fx.rates[priceCur] ? ` ≈ ${fmtRub((withVat ? Number(value) / (1 + vatPct / 100) : Number(value)) * calc.input.fx.rates[priceCur])} RUB без НДС` : ""}
                  {cc && cc.code !== priceCur && cc.rate && (priceCur === "RUB" || calc.input.fx.rates[priceCur]) ? ` ≈ ${fmtRub((withVat ? Number(value) / (1 + vatPct / 100) : Number(value)) * (priceCur === "RUB" ? 1 : calc.input.fx.rates[priceCur]) / cc.rate)} ${cc.code} без НДС` : ""}</div>}</div>}
              {(() => { const hr = dealCur === "RUB" ? 1 : (variant.price_currency || "").toUpperCase() === dealCur && variant.price_fx_rate ? variant.price_fx_rate : calc.input.fx.rates[dealCur] || 0;
                const mc = (rub?: number | null) => (rub == null ? "—" : hr && dealCur !== "RUB" ? `${fmtRub(rub / hr)} ${dealCur}` : `${fmtRub(rub)} RUB`);
                return base?.price_rub != null ? <div className="muted" style={{ fontSize: 12 }}>наценка {fmtPct(base?.markup_pct)}{units > 1 ? <> · себестоимость 1 машины {mc((base?.cost_total_rub ?? 0) / units)} · за {units} шт.: прибыль {mc(base?.profit_rub)}</> : null}</div> : <div className="muted">Цена не назначена — задайте её справа: сумма, наценка или целевая маржа.</div>; })()}
            </div>
            {isCD && ["approved", "kp_sent"].includes(deal.status) && <span className="muted" style={{ fontSize: 12, maxWidth: 260 }}>Цена утверждена. Снижение цены и новые условия — в «Торгах» (кнопка на панели вверху).</span>}
            {canPrice && (
              <div className="row">
                <select value={mode} onChange={(e) => setMode(e.target.value as typeof mode)}>
                  <option value="amount">сумма</option><option value="markup">наценка, %</option><option value="margin">целевая маржа, %</option>
                </select>
                {mode === "amount" && <select value={withVat ? "gross" : "net"} onChange={(e) => setWithVat(e.target.value === "gross")} title={`НДС ${vatPct} %`}>
                  <option value="net">без НДС</option><option value="gross">с НДС {vatPct} %</option></select>}
                <input type="number" value={value} onChange={(e) => setValue(e.target.value)}
                  placeholder={mode === "amount" ? String(((priceCur === variant.price_currency ? variant.price_amount : null) ?? (priceCur === "RUB" ? base?.price_rub ?? "" : "")) && Math.round(Number((priceCur === variant.price_currency ? variant.price_amount : null) ?? (priceCur === "RUB" ? base?.price_rub ?? 0 : 0)) / units * 100) / 100 || "") : ""} style={{ width: 160 }} />
                {mode === "amount" && units > 1 && <span className="muted" style={{ fontSize: 12 }}>за 1 шт. × {units}</span>}
                {mode === "amount" && <label className="muted" style={{ fontSize: 12 }} title={`сумма в другой валюте пересчитывается в валюту сделки (${dealCur}) по курсу ЦБ на дату расчёта`}>
                  ввод в{" "}
                  <select value={priceCur} onChange={(e) => setCur(e.target.value)}>{curs.map((c) => <option key={c} value={c}>{c}{c === dealCur ? " (валюта сделки)" : ""}</option>)}</select>
                </label>}
                <button className={base?.price_rub != null ? "btn secondary" : "btn"} onClick={setPrice} disabled={busy}>{base?.price_rub != null ? "Изменить цену" : "Назначить цену"}</button>
              </div>
            )}
          </div>
          {calc.group_chain && <GroupMargin g={calc.group_chain} vid={vid} />}
          <div className="row muted" style={{ marginTop: 6 }}>
            Дата отчёта (расчёта): <b>{fmtDate(calc.input.calc_date)}</b>{deal.report_date ? " — задана вручную" : " — сегодня"}
            {isCD && <>
              <input type="date" value={repDate} onChange={(e) => setRepDate(e.target.value)} style={{ width: 150 }} />
              <button className="btn ghost" disabled={busy || !repDate} onClick={() => saveReportDate(repDate)}>изменить дату</button>
              {deal.report_date && <button className="btn ghost" disabled={busy} onClick={() => saveReportDate(null)}>вернуть «сегодня»</button>}
            </>}
          </div>
          <DealCurrencyRow dealId={deal.id} dc={calc.deal_currency} fallback={offerCur} curs={curs} canEdit={isCD && !["approved", "kp_sent", "won", "contract", "execution", "closed"].includes(deal.status)}
            onDone={() => { reload(); load(); setCur(""); }} />
          {base?.sensitivity && (
            <div className="muted" style={{ marginTop: 6 }}>
              Чувствительность: курс +1 % → маржа {base.sensitivity.margin_delta_pct_per_1pct_fx.toFixed(2)} п.п. ({fmtRub(base.sensitivity.margin_delta_rub_per_1pct_fx)} RUB);
              курс безубыточности по порогу: <b>{base.sensitivity.breakeven_rate_base_ccy?.toFixed(4) ?? "—"}</b> RUB/{calc.input.base_currency}
            </div>
          )}
          {err && <div className="alert error">{err}</div>}
        </div>

        <div className="tabs tabs-compact">
          <button className={tab === "cost" ? "active" : ""} onClick={() => setTab("cost")}>Себестоимость и курсы</button>
          <button className={tab === "money" ? "active" : ""} onClick={() => setTab("money")}>Условия и деньги</button>
          <button className={tab === "kp" ? "active" : ""} onClick={() => setTab("kp")}>КП и история</button>
        </div>
        {tab === "cost" && (
          <div className="card">
            {base && (() => {
              const dRate = dealCur === "RUB" ? 1 : (variant.price_currency || "").toUpperCase() === dealCur && variant.price_fx_rate ? variant.price_fx_rate : calc.input.fx.rates[dealCur] || 0;
              return <>
                <CostBreakdown cards={false} base={base} units={units} baseCcy={calc.input.base_currency} threshold={calc.input.thresholds?.margin_threshold_pct} dealCur={dealCur} dealRate={dRate} />
                {isCD && <FactoryPricePanel dealId={deal.id} status={deal.status} variantId={variant.id} positions={(deal.positions || []).filter((p) => !variant.position_ids?.length || variant.position_ids.includes(p.id))}
                  units={units} dealCur={dealCur} dealRate={dRate} threshold={calc.input.thresholds?.margin_threshold_pct} currentPriceUnit={base.price_rub != null && dRate ? base.price_rub / units / dRate : null}
                  onApplied={() => { reload(); load(); }} />}
              </>; })()}
            {calc.production && Object.keys(calc.production).length > 0 && (
              <ProductionTermBlock dealId={deal.id} production={calc.production} canEdit={!!calc.can_edit_production}
                events={Object.fromEntries(terms.map((t) => [t.position_id, t.events]))} onSaved={() => { reload(); load(); }} />
            )}
            {terms.length > 0 && (
              <div style={{ marginTop: 10 }}>
                <b>Срок поставки клиенту по позициям</b> <span className="muted">(расчёт × 1,1 → округление до целой недели; в КП)</span>
                <table style={{ marginTop: 4 }}><thead><tr><th>Позиция</th><th>Точка отсчёта</th><th className="num">Расчёт</th><th className="num">С резервом</th><th>В КП</th><th>Готовность</th><th>CMR</th><th>Поставка</th></tr></thead>
                  <tbody>{terms.map((t) => t.delivery_term && (
                    <tr key={t.position_id}><td>{t.model}</td><td>{fmtDate(t.delivery_term.start_on)}</td><td className="num">{t.delivery_term.calc_days}{t.delivery_term.install_days ? ` (+${t.delivery_term.install_days} установка опций РФ)` : ""}</td>
                      <td className="num">{t.delivery_term.with_reserve}</td><td><b>{t.delivery_term.text}</b></td><td>{fmtDate(t.events.readiness)}</td><td>{fmtDate(t.events.border)}</td><td>{fmtDate(t.events.delivery)}</td></tr>))}</tbody></table>
              </div>
            )}
            {base?.payment_dates && base.payment_dates.length > 0 && (
              <div style={{ marginTop: 10 }}>
                <b>График клиента (базовый)</b> {base.defaults_used?.client_payments && <span className="badge warn">типовая схема — КД не задал</span>}
                <table style={{ marginTop: 4 }}><tbody>{base.payment_dates.map((p) => <tr key={p.no}><td>{p.no}</td><td>{EVENT_TITLES[p.event] || p.event}{p.via === "lc" && " (аккредитив)"}</td><td>{fmtDate(p.on)}</td><td className="num">{fmtRub(p.amount_rub)}</td></tr>)}</tbody></table>
              </div>
            )}
          </div>
        )}
        {tab === "money" && canTerms && calc.payment_terms && <PaymentTermsEditor deal={deal} calc={calc} onSaved={() => { reload(); load(); }} />}
        {tab === "kp" && can(me, "kp_images_select", 2) && <KpSettings deal={deal} calc={calc} reload={() => { reload(); load(); }} />}
        {tab === "money" && (
          <div className="card">
            {SCEN.map(([k, t]) => (<div key={k}><h2>{t}: {gapText(scen[k])}</h2><CashflowChart points={scen[k].cashflow} /></div>))}
            <div className="muted">Финансовые расходы (базовый): {Object.entries(base?.finance_breakdown || {}).map(([k, v]) => `${k} ${fmtRub(v)}`).join(" · ")}</div>
            {base?.vat?.budget && (() => {   // D-121: НДС в денежном потоке
              const v = base.vat!;
              const imp = (v.import || []).reduce((a, x) => a + x.amount_rub, 0);
              const qName = (q: string) => `${Math.floor((Number(q.slice(5, 7)) - 1) / 3) + 1} кв. ${q.slice(0, 4)}`;
              return (<div className="vat-cf">
                <h2>НДС в денежном потоке (базовый)</h2>
                <table><tbody>
                  <tr><td>НДС {v.pct} % в оплатах клиента</td><td className="num">+{fmtRub(v.from_client_rub)} ₽</td><td className="muted">приходит вместе с каждой оплатой</td></tr>
                  {(v.import || []).map((x, i) => <tr key={"i" + i}><td>Ввозной НДС (база: таможенная стоимость с доставкой до границы РФ + пошлина)</td><td className="num">−{fmtRub(x.amount_rub)} ₽</td><td className="muted">на дату ДТ {fmtDate(x.on)}{v.import_deducted ? " · к вычету в квартале ввоза" : " · расход, к вычету не принимается"}</td></tr>)}
                  {!!v.input_rub && <tr><td>Входящий НДС: брокер, доставка по РФ, сертификация (введены «с НДС»)</td><td className="num">−{fmtRub(v.input_rub)} ₽</td><td className="muted">с оплатой услуги · к вычету в квартале оплаты</td></tr>}
                  {v.budget.map((q) => <tr key={q.quarter}><td>В бюджет за {qName(q.quarter)}{q.amount_rub < 0 ? " (вычет больше начисленного)" : ""}</td>
                    <td className="num">{q.amount_rub < 0 ? "+" : "−"}{fmtRub(Math.abs(q.amount_rub))} ₽</td><td className="muted">по 1/3: {q.dates.map((d) => fmtDate(d)).join(", ")}</td></tr>)}
                  <tr className="vat-sum"><td>Остаток после расчётов с бюджетом</td><td className="num">{fmtRub(v.from_client_rub - imp - (v.input_rub || 0) - v.budget.reduce((a, q) => a + q.amount_rub, 0))} ₽</td>
                    <td className="muted">0 — НДС для компании нейтрален и влияет только на кассовый разрыв и стоимость денег. НДС с аванса — с даты оплаты, с остатка цены — на дату поставки; итог квартала — 28-го числа трёх следующих месяцев</td></tr>
                </tbody></table>
              </div>);
            })()}
          </div>
        )}
        {tab === "cost" && (() => {
          const ITEM: Record<string, string> = { domestic_logistics: "внутрикитайская логистика", export_formalities: "экспортные формальности КНР", freight: "доставка до границы РФ",
            door_to_door: "доставка от границы до клиента", ins: "страхование", service: "сервис", "таможенная стоимость": "таможенная стоимость (пошлина, НДС)", _base: "пересчёт в базовую валюту" };
          const label = (ref: string) => {
            const i = ref.indexOf("/");
            if (i < 0) return ITEM[ref] || ref;
            const id = ref.slice(0, i), rest = ref.slice(i + 1);
            const pos = deal.positions.find((p) => p.id === id), bat = (deal.batches || []).find((b) => b.id === id);
            const what = rest.startsWith("завод №") ? `оплата заводу, платёж ${rest.slice(7)}` : rest.startsWith("опция РФ") ? rest : ITEM[rest] || rest;
            return `${pos ? designation(pos) : bat ? bat.name : "сделка"}: ${what}`;
          };
          const rows = calc.result.fx_components.filter((c) => c.payment_ref !== "_alloc" && !c.payment_ref.endsWith("/_alloc"))
            .filter((c, i, arr) => arr.findIndex((x) => x.payment_ref === c.payment_ref && x.on === c.on && x.currency === c.currency) === i)
            .sort((x, y) => x.on.localeCompare(y.on));
          const noHist = (c: (typeof rows)[number]) => c.comp_c_season === 1 && c.season_spread[0] === 1 && c.season_spread[1] === 1 && Math.abs(c.comp_b_trend - c.comp_a_forward) < 1e-9;
          const f4 = (v: number | null | undefined) => (v == null ? "—" : v.toFixed(4).replace(".", ","));
          const anyNoHist = rows.some(noHist);
          return (
            <details className="card dp-fx" style={{ overflowX: "auto" }}>
              <summary><b>Курсы и прогноз по платежам</b> <span className="muted">· {rows.length} платежей · сценарии курса для денежного потока</span></summary>
              <div className="muted" style={{ margin: "8px 0 6px" }}>Курс ЦБ на {fmtDate(calc.input.fx.as_of)}: {Object.entries(calc.input.fx.rates).filter(([k]) => k !== "RUB").map(([k, v]) => `${k} ${f4(v)}`).join(" · ")}
                {calc.input.fx.note && <> · {calc.input.fx.note}</>}</div>
              {anyNoHist && <div className="alert warn">Истории курсов ЦБ пока нет — тренд и сезонность не учитываются, прогноз строится по форварду{rows.some((c) => c.comp_g_expert != null) ? " и экспертным точкам" : ""}. История за 5 лет загрузится автоматически в течение часа.</div>}
              <table>
                <thead><tr><th>Дата платежа</th><th>Платёж</th><th>Валюта</th><th className="num">Дней до платежа</th><th className="num">Курс ЦБ сегодня</th><th className="num">Форвард</th>
                  <th className="num">Тренд</th><th className="num">Сезонность</th><th className="num">Экспертная оценка</th><th>Веса (форвард / тренд / эксперт)</th>
                  <th className="num">Прогноз (базовый)</th><th className="num">Пессимистичный</th><th className="num">Оптимистичный</th></tr></thead>
                <tbody>{rows.map((c, i) => (
                  <tr key={i}><td>{fmtDate(c.on)}</td><td>{label(c.payment_ref)}{c.fixed && <span className="badge ok" style={{ marginLeft: 4 }}>курс зафиксирован</span>}</td><td>{c.currency}</td>
                    <td className="num">{c.t_days}</td><td className="num">{f4(c.k_cbr)}</td><td className="num">{f4(c.comp_a_forward)}</td>
                    <td className="num">{noHist(c) ? <span className="muted">нет истории</span> : f4(c.comp_b_trend)}</td>
                    <td className="num">{noHist(c) ? <span className="muted">—</span> : <>{c.comp_c_season.toFixed(3).replace(".", ",")} <span className="muted">({c.season_spread[0].toFixed(3)}–{c.season_spread[1].toFixed(3)})</span></>}</td>
                    <td className="num">{f4(c.comp_g_expert)}</td><td className="muted">{c.weights.map((w) => `${Math.round(w * 100)} %`).join(" / ")}</td>
                    <td className="num"><b>{f4(c.forecast)}</b></td>
                    <td className="num">{f4(c.rate_by_scenario.pessimistic)} <span className="muted">({c.dk_pct.pessimistic != null ? `${c.dk_pct.pessimistic > 0 ? "+" : ""}${c.dk_pct.pessimistic} %` : ""})</span></td>
                    <td className="num">{f4(c.rate_by_scenario.optimistic)} <span className="muted">({c.dk_pct.optimistic != null ? `${c.dk_pct.optimistic > 0 ? "+" : ""}${c.dk_pct.optimistic} %` : ""})</span></td></tr>))}</tbody>
              </table>
            </details>);
        })()}
        
        {tab === "kp" && (
          <div className="grid cols-2">
            <div className="card"><h2 style={{ marginTop: 0 }}>Комментарии логиста и менеджера</h2>
              {calc.comments.length === 0 && <div className="muted">Нет комментариев к отклонениям</div>}
              {calc.comments.map((c, i) => <div key={i} style={{ marginBottom: 6 }}><span className="badge">{c.from}</span> <b>{c.item}</b> ({c.ref}): {c.comment}</div>)}
            </div>
            <div className="card"><h2 style={{ marginTop: 0 }}>История цен заводов</h2>
              <table><tbody>{(calc.factory_price_history || []).map((h, i) => <tr key={i}><td>{h.model}</td><td className="num">{fmtRub(h.unit_price)} {h.currency}</td><td>{fmtDate(h.on)}</td></tr>)}</tbody></table>
              {calc.client_kp_history && <><h2>История КП этому клиенту</h2>
              <table><thead><tr><th>Сделка</th><th>Оборудование</th><th className="num">За 1 шт. без НДС</th><th className="num">За количество</th><th>Результат</th><th>Дата</th></tr></thead>
              <tbody>{calc.client_kp_history.map((h, i) => { const fc = !!h.currency && h.currency !== "RUB" && h.total != null;
                const m = (v: number | null | undefined, rub: number | null | undefined) => <>{fc && v != null ? `${fmtCur(v)} ${h.currency}` : `${fmtRub(rub)} ₽`}{fc && <div className="muted" style={{ fontSize: 11 }}>≈ {fmtRub(rub)} ₽</div>}</>;
                return <tr key={i}><td>{h.deal}{h.version_no ? ` v${h.version_no}` : ""}</td><td>{(h.models || []).join(", ")}{h.variant && <div className="muted" style={{ fontSize: 11 }}>{h.variant}</div>}</td>
                  <td className="num"><b>{m(h.unit_price, h.unit_price_rub)}</b></td><td className="num">{m(h.total, h.price_rub)}{(h.qty || 1) > 1 && <div className="muted" style={{ fontSize: 11 }}>{h.qty} шт.</div>}</td>
                  <td>{lbl(OUTCOME, h.status)}</td><td>{fmtDate(h.on)}</td></tr>; })}</tbody></table></>}
            </div>
          </div>
        )}

        {(isCD || isFD) && (
          <div className="card" id="approval-actions">
            <h2 style={{ marginTop: 0 }}>Действия</h2>
            {isCD && (
              <div className="row" style={{ marginBottom: 8 }}>
                {deal.service_flag && <button className="btn secondary" onClick={toggleService} disabled={busy || base?.price_rub == null}>{variant.service_in_kp ? "Скрыть строку сервиса в КП" : "Показать сервис отдельной строкой в КП"}</button>}
                {["calc_ready", "cd_approval", "rework"].includes(deal.status) && <span className="muted" style={{ fontSize: 12 }}>Утвердить или отправить на визу — кнопкой на панели вверху.</span>}
              </div>
            )}
            {isCD && deal.status === "rework" && calc.fd_declined && (
              <div className="alert warn" style={{ marginBottom: 8 }}>
                <div><b>ФД отказал в визе</b>{calc.fd_declined.user ? ` (${calc.fd_declined.user}` : ""}{calc.fd_declined.at ? `, ${fmtDate(calc.fd_declined.at)})` : calc.fd_declined.user ? ")" : ""}: {calc.fd_declined.comment}</div>
                <div className="muted" style={{ fontSize: 12, margin: "4px 0" }}>Можно изменить цену или условия и снова отправить на визу, либо настоять на текущей цене — тогда решение принимает ЗГД.</div>
                <div className="row">
                  <input placeholder="почему КД настаивает (обязательно)" value={insistComment} onChange={(e) => setInsistComment(e.target.value)} style={{ width: 360 }} />
                  <button className="btn" disabled={busy || !insistComment.trim()} onClick={() => act(`/api/deals/${deal.id}/insist`, { comment: insistComment })}>Настаиваю — передать на визу ЗГД</button>
                </div>
              </div>
            )}
            {(isCD || isFD) && (
              <div className="row" style={{ marginBottom: 8 }}>
                <b>Доработать:</b>
                <select value={rework.block} onChange={(e) => setRework({ ...rework, block: e.target.value })}>
                  {isCD && <option value="manager">менеджер</option>}<option value="logistics">логист</option>{deal.service_flag && <option value="service">сервис</option>}
                  <option value="pricing">КД (цена и условия)</option><option value="gd">ЗГД</option>
                </select>
                {reasons ? <>
                  <select value={rework.reason_code} onChange={(e) => pickReason(e.target.value)} aria-label="Причина возврата" style={{ maxWidth: 380 }}>
                    <option value="">причина возврата (обязательно)…</option>
                    {[...reasons].sort((a, b) => Number(b.block === rework.block) - Number(a.block === rework.block)).map((r) => <option key={r.code} value={r.code}>{r.title}</option>)}
                  </select>
                  <input placeholder={rework.reason_code === "other" ? "что не так (обязательно)" : "уточнение (по желанию)"} value={rework.comment}
                    onChange={(e) => setRework({ ...rework, comment: e.target.value })} style={{ width: 260 }} />
                </> : <input placeholder="причина возврата (обязательно)" value={rework.reason} onChange={(e) => setRework({ ...rework, reason: e.target.value })} style={{ width: 320 }} />}
                <button className="btn ghost" disabled={busy || !reworkReady}
                  onClick={() => act(`/api/deals/${deal.id}/rework`, reasons ? { block: rework.block, reason_code: rework.reason_code, comment: rework.comment } : { block: rework.block, reason: rework.reason })}>Вернуть на доработку</button>
              </div>
            )}
            {((isFD && deal.status === "fd_visa") || (isGD && deal.status === "gd_visa")) && (
              <div className="row">
                <input placeholder="комментарий к визе" value={visaComment} onChange={(e) => setVisaComment(e.target.value)} style={{ width: 320 }} />
                <button className="btn" disabled={busy} onClick={() => act(`/api/deals/${deal.id}/visa`, { approved: true, comment: visaComment })}>Виза: согласовано</button>
                <button className="btn ghost" disabled={busy || !visaComment} onClick={() => act(`/api/deals/${deal.id}/visa`, { approved: false, comment: visaComment })}>Отказать</button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function gapText(r?: ScenarioResult) {
  if (!r) return "";
  return r.cash_gap.amount_rub < 0 ? `разрыв ${fmtRub(r.cash_gap.amount_rub)} RUB с ${fmtDate(r.cash_gap.starts_on)}, ${r.cash_gap.days} дн` : "без кассового разрыва";
}


/** Задачи 9, 10: бренд подачи КП и способ вложения изображений; сводка выбранных файлов. */
function KpSettings({ deal, calc, reload }: { deal: Deal; calc: CalcResponse; reload: () => void }) {
  const [msg, setMsg] = useState("");
  const set = (body: Record<string, string>) => api.patch(`/api/deals/${deal.id}/kp-settings`, body).then(() => { setMsg(""); reload(); }).catch((e) => setMsg(e.message));
  const selected = deal.positions.flatMap((p) => (p.images || []).filter((i) => i.in_kp).map((i) => ({ ...i, model: designation(p) })));
  return (
    <div className="card">
      <h2 style={{ marginTop: 0 }}>Подача КП</h2>
      {msg && <div className="alert error">{msg}</div>}
      <div className="grid cols-2">
        <label className="f">Бренд подачи
          <select value={calc.kp_brand || deal.kp_brand || "factory"} onChange={(e) => set({ kp_brand: e.target.value })}>
            <option value="factory">под маркой завода (клиент видит завод и модель)</option>
            <option value="company">под брендом компании (без названия завода; наименование из каталога)</option></select></label>
        <label className="f">Изображения и чертежи в КП
          <select value={calc.kp_images_mode || deal.kp_images_mode || "document"} onChange={(e) => set({ kp_images_mode: e.target.value })}>
            <option value="document">в документ КП</option><option value="attachment">отдельными файлами к письму</option><option value="both">и в документ, и к письму</option></select></label>
      </div>
      <p className="muted">Файлы для КП отмечаются в карточке позиции (вкладка «Позиции…», блок «Изображения и чертежи»). Если ничего не отмечено — в КП ничего не вкладывается.</p>
      {selected.length > 0 ? <ul>{selected.map((i) => <li key={i.id}>{i.model}: {i.caption || i.file_name} <span className="muted">({lbl(IMAGE_KIND, i.kind)}, {lbl(IMAGE_SOURCE, i.source)}{i.has_factory_logo ? ", логотип завода" : ""})</span></li>)}</ul> : <div className="muted">Файлы в КП не выбраны</div>}
    </div>
  );
}


// маржа группы при закупке через компанию группы (D-161): «Цена завода» сделки — цена компании группы, её маржа — отдельно
type GroupChain = { positions: { position_id: string; designation: string; qty: number; end_supplier: string; group_company: string; supplier_price: number | null;
  supplier_currency: string; group_price: number | null; currency: string; transfer_price: number | null; group_margin_rub_unit: number | null; group_margin_rub: number | null;
  state: string; policy_version: number | null; warnings: string[] }[];
  variants: Record<string, { name: string; group_margin_rub: number; deal_profit_rub: number | null; group_profit_rub: number | null; group_margin_pct: number | null; incomplete: boolean }> };
function GroupMargin({ g, vid }: { g: GroupChain; vid: string }) {
  const v = g.variants[vid];
  if (!v) return null;
  return (
    <div style={{ marginTop: 8, borderTop: "1px solid var(--line)", paddingTop: 6 }}>
      <b>Через компанию группы</b>
      <div style={{ fontSize: 13 }}>прибыль сделки {fmtRub(v.deal_profit_rub)} RUB + маржа группы {fmtRub(v.group_margin_rub)} RUB = <b>прибыль группы {fmtRub(v.group_profit_rub)} RUB</b>
        {v.group_margin_pct != null && <> · {fmtPct(v.group_margin_pct)} от выручки</>}{v.incomplete && <span className="badge warn">не по всем позициям есть расчёт цепочки</span>}</div>
      <table style={{ fontSize: 12, marginTop: 4 }}>
        <thead><tr><th>Позиция</th><th>Поставщик → компания группы</th><th className="num">Цена поставщика</th><th className="num">Цена компании группы</th><th className="num">Маржа группы, RUB</th></tr></thead>
        <tbody>{g.positions.map((r) => (
          <tr key={r.position_id}><td>{r.designation} × {r.qty}</td><td>{r.end_supplier} → {r.group_company}{r.state === "changed_after_select" && <span className="badge warn">ответ поставщика изменился</span>}</td>
            <td className="num">{r.supplier_price != null ? `${fmtRub(r.supplier_price)} ${r.supplier_currency}` : "—"}</td>
            <td className="num">{r.group_price != null ? `${fmtRub(r.group_price)} ${r.currency}` : "—"}{r.transfer_price != null && <div className="muted">трансфертная</div>}</td>
            <td className="num">{fmtRub(r.group_margin_rub)}{r.group_margin_rub_unit != null && <div className="muted">{fmtRub(r.group_margin_rub_unit)} за ед.</div>}</td></tr>))}</tbody>
      </table>
    </div>);
}

/** Валюта сделки (D-080): по умолчанию — валюта предложения поставщика; КД / ЗГД меняют до утверждения. */
function DealCurrencyRow({ dealId, dc, fallback, curs, canEdit, onDone }: { dealId: string; dc?: { code: string; chosen: string | null; offer: string }; fallback: string; curs: string[];
  canEdit: boolean; onDone: () => void }) {
  const [edit, setEdit] = useState(false);
  const [err, setErr] = useState("");
  const code = dc?.code || fallback, offer = dc?.offer || fallback;
  const save = (c: string | null) => api.put(`/api/deals/${dealId}/deal-currency`, { currency: c }).then(() => { setEdit(false); setErr(""); onDone(); }).catch((e) => setErr((e as Error).message));
  return (
    <div className="row muted" style={{ marginTop: 6, gap: 8, alignItems: "center", flexWrap: "wrap" }}>
      Валюта сделки: <b style={{ color: "var(--text, #222)" }}>{code}</b>{dc?.chosen ? " — выбрана вручную" : " — валюта предложения поставщика"}
      {code !== "RUB" && <span>· цены справочно в рублях по курсу ЦБ</span>}
      {canEdit && !edit && <button className="btn ghost" style={{ padding: "1px 8px" }} onClick={() => setEdit(true)}>изменить</button>}
      {edit && <>
        <select defaultValue={code} onChange={(e) => save(e.target.value === offer ? null : e.target.value)}>{Array.from(new Set([offer, ...curs])).map((c) => <option key={c} value={c}>{c}{c === offer ? " (поставщик)" : ""}</option>)}</select>
        <button className="btn ghost" onClick={() => setEdit(false)}>×</button>
        <span style={{ fontSize: 12 }}>назначенные цены пересчитаются в новую валюту по курсу ЦБ</span></>}
      {err && <span className="badge bad">{err}</span>}
    </div>);
}

/** Себестоимость (D-088): только базовый сценарий — на 1 машину и за всё количество; статьи по группам, доля в себестоимости,
 *  итоговые карточки. Пессимистичный и оптимистичный — на вкладке «Денежный поток» и в аналитике. */
const COST_GROUPS: [string, string[]][] = [
  ["Закупка у завода", ["factory_price", "domestic_logistics", "export_formalities"]],
  ["Доставка", ["intl_freight", "insurance", "rf_delivery"]],
  ["Таможня и разрешения", ["customs_duty", "import_vat", "broker_util", "certification"]],
  ["Финансы, сервис и гарантия", ["finance", "service", "warranty_reserve"]],
  ["Накладные, доукомплектование, комиссии", ["overhead", "local_completion", "external_commission", "manager_commission", "cd_commission"]],
];
function CostBreakdown({ base, units, baseCcy, threshold, dealCur = "RUB", dealRate = 1, cards = true }: { base: ScenarioResult; units: number; baseCcy: string; threshold?: number; dealCur?: string; dealRate?: number; cards?: boolean }) {
  // D-090: все расходы — и в валюте контракта (валюте сделки) по курсу цены / ЦБ
  const fc = dealCur !== "RUB" && dealRate > 0, cv = (rub: number) => fmtRub(rub / (dealRate || 1));
  const [showZero, setShowZero] = useState(false);
  const u = Math.max(1, units), many = u > 1;
  const title = Object.fromEntries(COST_ITEMS.map(([no, k, t]) => [k, { no, t }]));
  const total = base.cost_total_rub || 0;
  // D-115: статьи, введённые в валюте сделки (цена завода, логистика, фрахт…), показываются как введены — по курсу сделки;
  // надбавка прогноза курса на даты оплаты — отдельной строкой «Курсовая разница». Итог себестоимости не меняется.
  const native: Record<string, number> = {};
  if (fc) for (const [, k] of COST_ITEMS) {
    const rub = base.items_rub[k] || 0; if (!rub) continue;
    const ms = (base.positions || []).map((pc) => ({ m: pc.items_fx?.[k], r: pc.items_rub?.[k] || 0 })).filter((x) => x.r);
    if (!ms.length || ms.some((x) => !x.m || (x.m.currency || "").toUpperCase() !== dealCur)) continue;
    if (Math.abs(ms.reduce((t, x) => t + x.r, 0) - rub) > 1) continue;                  // в статье есть не только суммы позиций
    const amt = ms.reduce((t, x) => t + (x.m!.amount || 0), 0), atRate = amt * dealRate;
    if (amt > 0 && Math.abs(rub - atRate) / rub < 0.3) native[k] = atRate;
  }
  const it = (k: string) => native[k] ?? (base.items_rub[k] || 0);
  const fxDiff = Object.keys(native).reduce((t, k) => t + (base.items_rub[k] || 0) - native[k], 0);
  const zeroCount = COST_ITEMS.filter(([, k]) => !base.items_rub[k]).length;
  const m = base.margin_pct ?? null, low = m != null && threshold != null && m < threshold;
  // D-092: главная цифра — в валюте сделки, мелко — справочно в рублях
  const main = (rub: number) => (fc ? `${cv(rub)} ${dealCur}` : `${fmtRub(rub)} ₽`);
  const card = (t: string, v: number | null | undefined, sub?: string, cls = "") => (
    <div className={"cb-card " + cls}><span>{t}</span><b>{v == null ? "—" : main(v / u)}</b>{fc && v != null && <em className="cb-rub">≈ {fmtRub(v / u)} ₽</em>}
      {many && v != null && <em>за {u} шт.: {main(v)}{fc ? ` (≈ ${fmtRub(v)} ₽)` : ""}</em>}{sub && <em>{sub}</em>}</div>);
  return (
    <div className="cb">
      {cards && <div className="cb-cards">
        {card(many ? "Себестоимость 1 машины" : "Себестоимость", total, undefined, "cb-dark")}
        {card(many ? "Цена 1 машины без НДС" : "Цена без НДС", base.price_rub)}
        <div className={"cb-card " + (m == null ? "" : m < 0 ? "cb-bad" : low ? "cb-warn" : "cb-ok")}><span>Маржа от выручки</span><b>{fmtPct(m)}</b>
          <em>наценка {fmtPct(base.markup_pct)}{threshold != null ? ` · порог ${threshold} %` : ""}</em></div>
        {card(many ? "Прибыль с 1 машины" : "Прибыль", base.profit_rub, `до стоимости денег ${main((base.profit_before_finance_rub || 0) / u)}`)}
        {card(many ? "Кассовый разрыв на 1 машину" : "Кассовый разрыв", base.cash_gap.amount_rub, `${base.cash_gap.days} дн. · поставка ${base.delivery_days} дн. от договора`)}
      </div>}
      <div className="cb-note muted">{fc ? <>Валюта контракта — {dealCur}, курс {dealRate.toFixed(4).replace(".", ",")} ₽ · </> : null}Базовый сценарий{many ? <> · суммы <b>на 1 машину</b>, справа — за весь объём ({u} шт.)</> : ""} · пессимистичный и оптимистичный — на вкладке «Денежный поток»</div>
      <table className="cb-table">
        <thead><tr><th>Статья</th>{fc && <th className="num">{many ? `На 1 машину, ${dealCur}` : dealCur}</th>}<th className={"num" + (fc ? " cb-rubh" : "")}>{many ? "На 1 машину, ₽" : "Сумма, ₽"}{fc ? " (справочно)" : ""}</th><th className="cb-sharecol">Доля в себестоимости</th>
          {many && fc && <th className="num">За {u} шт., {dealCur}</th>}{many && <th className={"num" + (fc ? " cb-rubh" : "")}>За {u} шт., ₽</th>}</tr></thead>
        <tbody>
          {COST_GROUPS.map(([g, keys]) => {
            const rows = keys.filter((k) => showZero || base.items_rub[k]);
            const sum = keys.reduce((a, k) => a + it(k), 0);
            if (!rows.length) return null;
            return [
              <tr key={g} className="cb-group"><td>{g}</td>{fc && <td className="num">{cv(sum / u)}</td>}<td className={"num" + (fc ? " cb-rubc" : "")}>{fmtRub(sum / u)}</td><td>{total ? `${(sum / total * 100).toFixed(1).replace(".", ",")} %` : ""}</td>{many && fc && <td className="num">{cv(sum)}</td>}{many && <td className={"num" + (fc ? " cb-rubc" : " muted")}>{fmtRub(sum)}</td>}</tr>,
              ...rows.map((k) => { const v = it(k), sh = total ? v / total * 100 : 0; return (
                <tr key={k} className={v ? "" : "cb-zero"}><td><span className="cb-no">{title[k]?.no}</span>{title[k]?.t || k}</td>{fc && <td className="num">{cv(v / u)}</td>}<td className={"num" + (fc ? " cb-rubc" : "")}>{fmtRub(v / u)}</td>
                  <td><div className="cb-bar"><i style={{ width: `${Math.min(100, sh)}%` }} /><span>{sh ? `${sh.toFixed(1).replace(".", ",")} %` : ""}</span></div></td>
                  {many && fc && <td className="num">{cv(v)}</td>}{many && <td className={"num" + (fc ? " cb-rubc" : " muted")}>{fmtRub(v)}</td>}</tr>); }),
            ]; })}
          {Math.abs(fxDiff) >= 1 && (() => { const sh = total ? fxDiff / total * 100 : 0; return (
            <tr className="cb-group cb-fxdiff" title="Платежи в валюте считаются по прогнозному курсу на дату каждого платежа; строки выше — по курсу сделки. Разница — здесь.">
              <td>Курсовая разница по прогнозу <span className="muted" style={{ fontWeight: 400 }}>· рост курса к датам оплаты</span></td>
              {fc && <td className="num">{cv(fxDiff / u)}</td>}<td className={"num" + (fc ? " cb-rubc" : "")}>{fmtRub(fxDiff / u)}</td>
              <td>{`${sh.toFixed(1).replace(".", ",")} %`}</td>{many && fc && <td className="num">{cv(fxDiff)}</td>}{many && <td className={"num" + (fc ? " cb-rubc" : " muted")}>{fmtRub(fxDiff)}</td>}</tr>); })()}
          <tr className="cb-total"><td>Себестоимость{many ? " 1 машины" : ""}</td>{fc && <td className="num">{cv(total / u)}</td>}<td className={"num" + (fc ? " cb-rubc" : "")}>{fmtRub(total / u)}</td><td>100 %</td>{many && fc && <td className="num">{cv(total)}</td>}{many && <td className={"num" + (fc ? " cb-rubc" : " muted")}>{fmtRub(total)}</td>}</tr>
          <tr className="cb-sub"><td>Цена продажи без НДС</td>{fc && <td className="num">{cv((base.price_rub || 0) / u)}</td>}<td className={"num" + (fc ? " cb-rubc" : "")}>{fmtRub((base.price_rub || 0) / u)}</td><td />{many && fc && <td className="num">{cv(base.price_rub || 0)}</td>}{many && <td className={"num" + (fc ? " cb-rubc" : " muted")}>{fmtRub(base.price_rub)}</td>}</tr>
          {Math.abs(base.client_fx_diff_rub || 0) >= 1 && (() => { const d = base.client_fx_diff_rub || 0; return (   // D-123
            <tr className="cb-sub" title="Клиент платит в валюте договора: рубли считаются по прогнозу курса на дату каждой оплаты, цена — по курсу фиксации цены. Разница входит в прибыль.">
              <td>Курсовая разница по оплатам клиента <span className="muted">· прогноз курса на даты оплат</span></td>{fc && <td className="num">{d > 0 ? "+" : ""}{cv(d / u)}</td>}
              <td className={"num" + (fc ? " cb-rubc" : "")}>{d > 0 ? "+" : ""}{fmtRub(d / u)}</td><td />{many && fc && <td className="num">{d > 0 ? "+" : ""}{cv(d)}</td>}{many && <td className={"num" + (fc ? " cb-rubc" : " muted")}>{d > 0 ? "+" : ""}{fmtRub(d)}</td>}</tr>); })()}
          <tr className="cb-sub"><td>Прибыль</td>{fc && <td className="num">{cv((base.profit_rub || 0) / u)}</td>}<td className={"num" + (fc ? " cb-rubc" : "")}>{fmtRub((base.profit_rub || 0) / u)}</td><td>{fmtPct(m)} маржи</td>{many && fc && <td className="num">{cv(base.profit_rub || 0)}</td>}{many && <td className={"num" + (fc ? " cb-rubc" : " muted")}>{fmtRub(base.profit_rub)}</td>}</tr>
          {!!base.guarantee_cost_rub && <tr className="cb-sub"><td>Банковская гарантия на аванс</td>{fc && <td className="num">{cv(base.guarantee_cost_rub / u)}</td>}<td className={"num" + (fc ? " cb-rubc" : "")}>{fmtRub(base.guarantee_cost_rub / u)}</td><td />{many && fc && <td className="num">{cv(base.guarantee_cost_rub)}</td>}{many && <td className={"num" + (fc ? " cb-rubc" : " muted")}>{fmtRub(base.guarantee_cost_rub)}</td>}</tr>}
        </tbody>
      </table>
      {zeroCount > 0 && <button className="btn ghost" style={{ marginTop: 6 }} onClick={() => setShowZero(!showZero)}>{showZero ? "Скрыть нулевые статьи" : `Показать нулевые статьи (${zeroCount})`}</button>}
    </div>);
}

type FpSum = { cost_total_rub: number; price_rub: number | null; margin_pct: number | null; markup_pct: number | null; profit_rub: number | null; cash_gap_rub: number; cash_gap_days: number;
  factory_price_rub: number; customs_rub: number; finance_rub: number };
/** Цена производителя (D-091): «что если» — скидка или новая цена с наглядным пересчётом маржи; запрос производителю (задача менеджеру);
 *  применение подтверждённой цены; обратный расчёт — какая цена производителя нужна при заданной марже и цене продажи. */
function FactoryPricePanel({ dealId, status, variantId, positions, units, dealCur, dealRate, threshold, currentPriceUnit, onApplied }: {
  dealId: string; status: string; variantId: string; positions: Position[]; units: number; dealCur: string; dealRate: number; threshold?: number;
  currentPriceUnit: number | null; onApplied: () => void }) {
  const [pid, setPid] = useState(positions[0]?.id || "");
  const pos = positions.find((p) => p.id === pid) || positions[0];
  const [mode, setMode] = useState<"discount_pct" | "price">("discount_pct");
  const [val, setVal] = useState("");
  const [res, setRes] = useState<{ before: FpSum; after: FpSum; new_prices: Record<string, number> } | null>(null);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const [comment, setComment] = useState("");
  const [tm, setTm] = useState(String(threshold ?? 10));
  const [sp, setSp] = useState(currentPriceUnit ? String(Math.round(currentPriceUnit * 100) / 100) : "");
  const [solved, setSolved] = useState<{ unit_price: number; currency: string; discount_pct: number | null; after: FpSum } | null>(null);
  const canApply = ["calc_ready", "cd_approval", "rework"].includes(status);
  const num = (x: string) => Number(x.replace(",", ".").replace(/\s/g, ""));
  useEffect(() => { setSp(currentPriceUnit ? String(Math.round(currentPriceUnit * 100) / 100) : ""); }, [currentPriceUnit]);
  useEffect(() => {
    if (!pos || !val.trim() || !(num(val) > 0 || mode === "discount_pct")) { setRes(null); return; }
    const t = window.setTimeout(() => api.post<typeof res>(`/api/deals/${dealId}/factory-price/what-if`, { variant_id: variantId, changes: { [pos.id]: { mode, value: num(val) } } })
      .then((r) => { setRes(r); setErr(""); }).catch((e) => { setRes(null); setErr((e as Error).message); }), 400);
    return () => window.clearTimeout(t);
  }, [val, mode, pid]);
  if (!pos) return null;
  const newUnit = res?.new_prices[pos.id];
  const u = Math.max(1, units);
  const fc = dealCur !== "RUB" && dealRate > 0;
  const rub1 = (v: number | null | undefined) => (v == null ? "—" : `${fmtRub(v / u)} ₽`);
  const cur1 = (v: number | null | undefined) => (v == null || !fc ? "" : `${fmtRub(v / u / dealRate)} ${dealCur}`);
  const delta = (a?: number | null, b?: number | null, money = true, isCost = false) => {
    if (a == null || b == null) return null; const d = b - a; if (Math.abs(d) < 0.005) return <span className="muted">—</span>;
    return <span className={(isCost ? d < 0 : d > 0) ? "fp-good" : "fp-bad"}>{d > 0 ? "+" : "−"}{money ? (fc ? `${fmtRub(Math.abs(d) / u / dealRate)} ${dealCur}` : fmtRub(Math.abs(d) / u) + " ₽") : Math.abs(d).toFixed(2).replace(".", ",") + " п.п."}</span>;
  };
  const rows: [string, (x: FpSum) => number | null | undefined, boolean, boolean][] = [
    ["Цена производителя в себестоимости", (x) => x.factory_price_rub, true, true], ["Таможенные платежи", (x) => x.customs_rub, true, true],
    ["Стоимость денег", (x) => x.finance_rub, true, true], ["Себестоимость", (x) => x.cost_total_rub, true, true],
    ["Прибыль", (x) => x.profit_rub, true, false], ["Маржа от выручки", (x) => x.margin_pct, false, false], ["Наценка", (x) => x.markup_pct, false, false]];
  const request = () => api.post(`/api/deals/${dealId}/factory-price/request`, { position_id: pos.id, mode, value: num(val), comment })
    .then(() => { setMsg("Запрос оформлен: менеджеру сделки поставлена задача запросить у производителя, запись — в ленте."); setErr(""); }).catch((e) => setErr((e as Error).message));
  const apply = () => newUnit && api.post(`/api/deals/${dealId}/factory-price/apply`, { position_id: pos.id, unit_price: newUnit, comment })
    .then(() => { setMsg(`Новая цена производителя ${fmtRub(newUnit)} ${pos.currency} за 1 шт. применена в расчёте.`); setVal(""); setComment(""); setErr(""); onApplied(); }).catch((e) => setErr((e as Error).message));
  const solve = () => api.post<typeof solved>(`/api/deals/${dealId}/factory-price/solve`, { variant_id: variantId, position_id: pos.id, target_margin_pct: num(tm), sale_price_unit: sp.trim() ? num(sp) : null, sale_currency: dealCur })
    .then((r) => { setSolved(r); setErr(""); }).catch((e) => { setSolved(null); setErr((e as Error).message); });
  return (
    <div className="fp-panel">
      <h3>Цена производителя: скидка, новая цена, нужная цена</h3>
      {positions.length > 1 && <label className="pf-field" style={{ maxWidth: 420 }}><span className="pf-label">Позиция</span>
        <select value={pos.id} onChange={(e) => { setPid(e.target.value); setVal(""); setSolved(null); }}>{positions.map((p) => <option key={p.id} value={p.id}>{p.machine_type} {p.model} × {p.qty}</option>)}</select></label>}
      <div className="fp-now">Сейчас: <b>{fmtRub(pos.unit_price)} {pos.currency}</b> за 1 шт.{(pos.qty || 1) > 1 ? ` · за ${pos.qty} шт. ${fmtRub(pos.unit_price * pos.qty)} ${pos.currency}` : ""}</div>
      {err && <div className="alert error">{err}</div>}
      {msg && <div className="alert ok">{msg}</div>}
      <div className="fp-cols">
        <div className="fp-col">
          <b>Что если производитель даст</b>
          <div className="row" style={{ gap: 6, alignItems: "center", margin: "6px 0" }}>
            <div className="ng-seg">{([["discount_pct", "скидку, %"], ["price", `новую цену, ${pos.currency}`]] as const).map(([k, t]) => <button key={k} className={mode === k ? "active" : ""} onClick={() => { setMode(k); setVal(""); }}>{t}</button>)}</div>
            <input inputMode="decimal" value={val} placeholder={mode === "discount_pct" ? "напр. 5" : "за 1 шт."} style={{ width: 140, height: 34, fontWeight: 600 }} onChange={(e) => setVal(e.target.value)} />
          </div>
          {res && newUnit != null && <>
            <div className="fp-new">Новая цена производителя: <b>{fmtRub(newUnit)} {pos.currency}</b> за 1 шт. ({(((newUnit - pos.unit_price) / (pos.unit_price || 1)) * 100).toFixed(2).replace(".", ",")} %) · цена продажи — прежняя</div>
            <table className="fp-table"><thead><tr><th>{u > 1 ? "на 1 машину" : ""}</th><th className="num">Сейчас</th><th className="num">С новой ценой</th><th className="num">Изменение</th></tr></thead>
              <tbody>{rows.map(([t, g, money, isCost]) => (
                <tr key={t} className={t === "Маржа от выручки" ? "fp-key" : ""}><td>{t}</td>
                  <td className="num">{money ? (fc ? cur1(g(res.before)) : rub1(g(res.before))) : fmtPct(g(res.before))}{money && fc && <div className="cb-rubc">≈ {rub1(g(res.before))}</div>}</td>
                  <td className="num"><b>{money ? (fc ? cur1(g(res.after)) : rub1(g(res.after))) : fmtPct(g(res.after))}</b>{money && fc && <div className="cb-rubc">≈ {rub1(g(res.after))}</div>}</td>
                  <td className="num">{delta(g(res.before), g(res.after), money, isCost)}</td></tr>))}</tbody></table>
            {threshold != null && res.after.margin_pct != null && <div className={res.after.margin_pct >= threshold ? "alert ok" : "alert warn"} style={{ marginTop: 6 }}>
              Маржа {fmtPct(res.after.margin_pct)} {res.after.margin_pct >= threshold ? "не ниже" : "ниже"} порога {threshold} %</div>}
            <input placeholder="комментарий / основание (для применения — обязательно: письмо, ответ производителя)" value={comment} onChange={(e) => setComment(e.target.value)} style={{ width: "100%", marginTop: 6 }} />
            <div className="row" style={{ gap: 8, marginTop: 6, flexWrap: "wrap" }}>
              <button className="btn secondary" onClick={request}>Запросить у производителя</button>
              <button className="btn" disabled={!canApply || !comment.trim()} title={canApply ? "" : "после утверждения — через «Вернуть на доработку»"} onClick={apply}>Применить в расчёте</button>
            </div>
          </>}
        </div>
        <div className="fp-col">
          <b>Какая цена производителя нужна</b>
          <div className="fp-solve">
            <label className="pf-field"><span className="pf-label">Маржа, %</span><input inputMode="decimal" value={tm} onChange={(e) => setTm(e.target.value)} /></label>
            <label className="pf-field"><span className="pf-label">Цена продажи без НДС за 1 шт., {dealCur}</span><input inputMode="decimal" value={sp} onChange={(e) => setSp(e.target.value)} /></label>
            <button className="btn" onClick={solve}>Рассчитать</button>
          </div>
          {solved && <div className="fp-result">
            <div>Нужна цена производителя: <b>{fmtRub(solved.unit_price)} {solved.currency}</b> за 1 шт.</div>
            <div className="muted">{solved.discount_pct != null ? (solved.discount_pct >= 0 ? `скидка ${solved.discount_pct.toFixed(2).replace(".", ",")} % от текущей` : `можно на ${Math.abs(solved.discount_pct).toFixed(2).replace(".", ",")} % дороже текущей`) : ""} · маржа {fmtPct(solved.after.margin_pct)}, прибыль {fc ? `${cur1(solved.after.profit_rub)} (≈ ${rub1(solved.after.profit_rub)})` : rub1(solved.after.profit_rub)}{u > 1 ? " с 1 машины" : ""}</div>
            <button className="btn ghost" style={{ marginTop: 6 }} onClick={() => { setMode("price"); setVal(String(solved.unit_price)); }}>Подставить в «что если»</button>
          </div>}
        </div>
      </div>
    </div>);
}
