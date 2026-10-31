import { useEffect, useMemo, useState } from "react";
import { api, fmtDate, fmtPct, fmtRub } from "../api";
import { COST_ITEMS, type CalcResponse, type Deal, type Me, type ScenarioResult } from "../types";
import CashflowChart from "../components/CashflowChart";

const SCEN = [["pessimistic", "Пессимистичный"], ["base", "Базовый"], ["optimistic", "Оптимистичный"]] as const;

/** Экран утверждения КД (раздел 10): слева варианты, справа 14 статей × 3 сценария, цена с живым пересчётом, индикатор маржи,
 *  денежный поток, комментарии, история цен, курсы. ФД видит то же и даёт визу. */
export default function ApprovalScreen({ deal, me, reload }: { deal: Deal; me: Me; reload: () => void }) {
  const [calc, setCalc] = useState<CalcResponse | null>(null);
  const [vid, setVid] = useState<string>(deal.variants[0]?.id || "");
  const [mode, setMode] = useState<"amount" | "markup" | "margin">("amount");
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [tab, setTab] = useState<"cost" | "cashflow" | "fx" | "history">("cost");
  const [rework, setRework] = useState({ block: "logistics", reason: "" });
  const [visaComment, setVisaComment] = useState("");
  const isCD = me.roles.some((r) => r.code === "cd" || r.code === "gd");
  const isFD = me.roles.some((r) => r.code === "fd");
  const isGD = me.roles.some((r) => r.code === "gd");

  async function load() {
    try { setCalc(await api.get<CalcResponse>(`/api/deals/${deal.id}/calc`)); setErr(""); }
    catch (e) { setErr((e as Error).message); }
  }
  useEffect(() => { load(); }, [deal.id, deal.status]);

  const scen = calc?.result.variants[vid];
  const base = scen?.base;
  const thr = calc?.threshold_pct ?? 3;

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
      await api.post(`/api/deals/${deal.id}/price`, { variant_id: vid, mode, value: Number(value) });
      await load(); setErr("");
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  async function toggleService() {
    const v = deal.variants.find((x) => x.id === vid);
    if (!v || base?.price_rub == null) return;
    await api.post(`/api/deals/${deal.id}/price`, { variant_id: vid, mode: "amount", value: base.price_rub, service_in_kp: !v.service_in_kp });
    reload(); load();
  }
  async function act(url: string, body?: unknown) {
    setBusy(true);
    try { await api.post(url, body); setErr(""); reload(); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }

  if (err && !calc) return <div className="alert warn">{err}</div>;
  if (!calc || !scen) return <div className="muted">Загрузка расчёта…</div>;

  const variant = deal.variants.find((v) => v.id === vid)!;
  const canPrice = isCD && ["calc_ready", "cd_approval", "rework", "fd_visa", "gd_visa", "approved"].includes(deal.status);

  return (
    <div className="grid" style={{ gridTemplateColumns: "260px 1fr" }}>
      <div>
        <div className="card">
          <h2 style={{ marginTop: 0 }}>Варианты</h2>
          {deal.variants.map((v) => {
            const b = calc.result.variants[v.id]?.base;
            return (
              <div key={v.id} onClick={() => setVid(v.id)} style={{ padding: 8, borderRadius: 6, cursor: "pointer", background: v.id === vid ? "var(--accent-soft)" : "transparent", marginBottom: 4 }}>
                <b>{v.name}</b>
                <div className="muted">цена {fmtRub(b?.price_rub)} · маржа {fmtPct(b?.margin_pct)}</div>
              </div>
            );
          })}
        </div>
        <div className="card">
          <h2 style={{ marginTop: 0 }}>Курс ЦБ на {fmtDate(calc.input.fx.as_of)}</h2>
          {Object.entries(calc.input.fx.rates).filter(([c]) => c !== "RUB").map(([c, r]) => <div key={c}>{c}: <b>{r.toFixed(4)}</b></div>)}
          {calc.input.fx.source !== "cbr" && <span className="badge warn">{calc.input.fx.source}</span>}
        </div>
        {(calc.warnings.length > 0 || calc.result.approval.reasons.length > 0) && (
          <div className="card">
            {calc.result.approval.reasons.map((r, i) => <div key={i} className="alert warn">{r}</div>)}
            {calc.warnings.map((w, i) => <div key={i} className="alert error">{w}</div>)}
          </div>
        )}
      </div>

      <div>
        <div className="card">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <div>
              <div className={"margin-ind " + indicator.cls}>{fmtPct(base?.margin_pct)}</div>
              <div className="muted">маржа от выручки (базовый) · {indicator.text}</div>
              <div className="muted">наценка {fmtPct(base?.markup_pct)} · прибыль {fmtRub(base?.profit_rub)} RUB · разрыв {fmtRub(base?.cash_gap.amount_rub)} RUB ({base?.cash_gap.days} дн)</div>
            </div>
            {canPrice && (
              <div className="row">
                <select value={mode} onChange={(e) => setMode(e.target.value as typeof mode)}>
                  <option value="amount">сумма, RUB без НДС</option><option value="markup">наценка, %</option><option value="margin">целевая маржа, %</option>
                </select>
                <input type="number" value={value} onChange={(e) => setValue(e.target.value)} placeholder={mode === "amount" ? String(base?.price_rub ?? "") : ""} style={{ width: 160 }} />
                <button className="btn" onClick={setPrice} disabled={busy}>Назначить цену</button>
              </div>
            )}
          </div>
          {base?.sensitivity && (
            <div className="muted" style={{ marginTop: 6 }}>
              Чувствительность: курс +1 % → маржа {base.sensitivity.margin_delta_pct_per_1pct_fx.toFixed(2)} п.п. ({fmtRub(base.sensitivity.margin_delta_rub_per_1pct_fx)} RUB);
              курс безубыточности по порогу: <b>{base.sensitivity.breakeven_rate_base_ccy?.toFixed(4) ?? "—"}</b> RUB/{calc.input.base_currency}
            </div>
          )}
          {err && <div className="alert error">{err}</div>}
        </div>

        <div className="tabs">
          <button className={tab === "cost" ? "active" : ""} onClick={() => setTab("cost")}>Себестоимость 14 × 3</button>
          <button className={tab === "cashflow" ? "active" : ""} onClick={() => setTab("cashflow")}>Денежный поток</button>
          <button className={tab === "fx" ? "active" : ""} onClick={() => setTab("fx")}>Курсы и прогноз</button>
          <button className={tab === "history" ? "active" : ""} onClick={() => setTab("history")}>Комментарии и история</button>
        </div>

        {tab === "cost" && (
          <div className="card">
            <table>
              <thead><tr><th>№</th><th>Статья</th>{SCEN.map(([k, t]) => <th key={k} className="num">{t}</th>)}</tr></thead>
              <tbody>
                {COST_ITEMS.map(([no, key, title]) => (
                  <tr key={key}><td>{no}</td><td>{title}</td>{SCEN.map(([k]) => <td key={k} className="num">{fmtRub(scen[k]?.items_rub[key])}</td>)}</tr>
                ))}
                <tr className="total"><td /><td>Себестоимость, RUB</td>{SCEN.map(([k]) => <td key={k} className="num">{fmtRub(scen[k]?.cost_total_rub)}</td>)}</tr>
                <tr><td /><td>в {calc.input.base_currency}</td>{SCEN.map(([k]) => <td key={k} className="num">{fmtRub(scen[k]?.cost_total_base_ccy)}</td>)}</tr>
                <tr><td /><td>Цена продажи без НДС</td>{SCEN.map(([k]) => <td key={k} className="num">{fmtRub(scen[k]?.price_rub)}</td>)}</tr>
                <tr><td /><td><b>Маржа от выручки</b></td>{SCEN.map(([k]) => <td key={k} className="num"><b>{fmtPct(scen[k]?.margin_pct)}</b></td>)}</tr>
                <tr><td /><td>Наценка от себестоимости (справочно)</td>{SCEN.map(([k]) => <td key={k} className="num">{fmtPct(scen[k]?.markup_pct)}</td>)}</tr>
                <tr><td /><td>Прибыль, RUB</td>{SCEN.map(([k]) => <td key={k} className="num">{fmtRub(scen[k]?.profit_rub)}</td>)}</tr>
                <tr><td /><td>Прибыль до стоимости денег</td>{SCEN.map(([k]) => <td key={k} className="num">{fmtRub(scen[k]?.profit_before_finance_rub)}</td>)}</tr>
                <tr><td /><td>Кассовый разрыв, RUB / дней</td>{SCEN.map(([k]) => <td key={k} className="num">{fmtRub(scen[k]?.cash_gap.amount_rub)} / {scen[k]?.cash_gap.days}</td>)}</tr>
                <tr><td /><td>Срок поставки от договора, дней</td>{SCEN.map(([k]) => <td key={k} className="num">{scen[k]?.delivery_days}</td>)}</tr>
              </tbody>
            </table>
          </div>
        )}
        {tab === "cashflow" && (
          <div className="card">
            {SCEN.map(([k, t]) => (<div key={k}><h2>{t}: {gapText(scen[k])}</h2><CashflowChart points={scen[k].cashflow} /></div>))}
            <div className="muted">Финансовые расходы (базовый): {Object.entries(base?.finance_breakdown || {}).map(([k, v]) => `${k} ${fmtRub(v)}`).join(" · ")}</div>
          </div>
        )}
        {tab === "fx" && (
          <div className="card" style={{ overflowX: "auto" }}>
            <table>
              <thead><tr><th>Платёж</th><th>Дата</th><th className="num">T</th><th className="num">K ЦБ</th><th className="num">A форвард</th><th className="num">Б тренд</th><th className="num">В сезон (min–max)</th><th className="num">Г эксперт</th><th>w A/Б/Г</th><th className="num">Прогноз</th><th className="num">ΔK ±</th><th className="num">пессим.</th><th className="num">оптим.</th></tr></thead>
              <tbody>{calc.result.fx_components.map((c, i) => (
                <tr key={i}><td>{c.payment_ref}{c.fixed && <span className="badge ok">зафиксирован</span>}</td><td>{fmtDate(c.on)}</td><td className="num">{c.t_days}</td>
                  <td className="num">{c.k_cbr.toFixed(4)}</td><td className="num">{c.comp_a_forward.toFixed(4)}</td><td className="num">{c.comp_b_trend.toFixed(4)}</td>
                  <td className="num">{c.comp_c_season.toFixed(4)} ({c.season_spread[0].toFixed(3)}–{c.season_spread[1].toFixed(3)})</td>
                  <td className="num">{c.comp_g_expert?.toFixed(4) ?? "—"}</td><td>{c.weights.map((w) => w.toFixed(2)).join("/")}</td>
                  <td className="num"><b>{c.forecast.toFixed(4)}</b></td><td className="num">{c.dk_pct.pessimistic?.toFixed(2)} / {c.dk_pct.optimistic?.toFixed(2)}</td>
                  <td className="num">{c.rate_by_scenario.pessimistic?.toFixed(4)}</td><td className="num">{c.rate_by_scenario.optimistic?.toFixed(4)}</td></tr>))}</tbody>
            </table>
          </div>
        )}
        {tab === "history" && (
          <div className="grid cols-2">
            <div className="card"><h2 style={{ marginTop: 0 }}>Комментарии логиста и менеджера</h2>
              {calc.comments.length === 0 && <div className="muted">Нет комментариев к отклонениям</div>}
              {calc.comments.map((c, i) => <div key={i} style={{ marginBottom: 6 }}><span className="badge">{c.from}</span> <b>{c.item}</b> ({c.ref}): {c.comment}</div>)}
            </div>
            <div className="card"><h2 style={{ marginTop: 0 }}>История цен заводов</h2>
              <table><tbody>{calc.factory_price_history.map((h, i) => <tr key={i}><td>{h.model}</td><td className="num">{fmtRub(h.unit_price)} {h.currency}</td><td>{fmtDate(h.on)}</td></tr>)}</tbody></table>
              <h2>История КП этому клиенту</h2>
              <table><tbody>{calc.client_kp_history.map((h, i) => <tr key={i}><td>{h.deal}</td><td>{h.variant}</td><td className="num">{fmtRub(h.price_rub)}</td><td>{h.status}</td></tr>)}</tbody></table>
            </div>
          </div>
        )}

        {(isCD || isFD) && (
          <div className="card">
            <h2 style={{ marginTop: 0 }}>Действия</h2>
            {isCD && (
              <div className="row" style={{ marginBottom: 8 }}>
                {deal.service_flag && <button className="btn secondary" onClick={toggleService} disabled={busy || base?.price_rub == null}>{variant.service_in_kp ? "Скрыть строку сервиса в КП" : "Показать сервис отдельной строкой в КП"}</button>}
                <button className="btn" disabled={busy || !["calc_ready", "cd_approval", "rework"].includes(deal.status)} onClick={() => act(`/api/deals/${deal.id}/submit`)}>Утвердить / отправить на визу</button>
              </div>
            )}
            {isCD && (
              <div className="row" style={{ marginBottom: 8 }}>
                <select value={rework.block} onChange={(e) => setRework({ ...rework, block: e.target.value })}>
                  <option value="manager">менеджер</option><option value="logistics">логист</option><option value="service">сервис</option><option value="pricing">цена (КД)</option>
                </select>
                <input placeholder="причина возврата (обязательно)" value={rework.reason} onChange={(e) => setRework({ ...rework, reason: e.target.value })} style={{ width: 320 }} />
                <button className="btn ghost" disabled={busy || !rework.reason} onClick={() => act(`/api/deals/${deal.id}/rework`, rework)}>Вернуть на доработку</button>
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
