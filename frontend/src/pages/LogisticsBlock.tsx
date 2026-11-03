import { useEffect, useState } from "react";
import { api, ApiError, fmtDate, fmtRub, designation } from "../api";
import type { Deal, Me } from "../types";
import Questionnaire from "../components/Questionnaire";

type Money = { amount: number; currency: string };
type PosForm = { domestic_logistics: Money; export_formalities: Money; hs_code: string; duty_pct: string; vat_pct: string; hs_manual_comment: string; util_fee_rub: number; certification_rub: number; volume_m3: string; comments: Record<string, string>;
  util_applies: boolean | null; util_base_rate_rub: string; util_coefficient: string };
type UtilRef = { base_rate_rub: number; coefficient: number | null; rule: { hs_prefix?: string; machine_type?: string; comment?: string } | null; hs_found: boolean; applies_by_hs: boolean | null };

/** Блок логиста (раздел 5): партии, статьи по позициям, ТН ВЭД, проверка по истории с комментарием. */
export default function LogisticsBlock({ deal, me, reload }: { deal: Deal; me: Me; reload: () => void }) {
  const canEdit = me.roles.some((r) => r.code === "logistician" || r.code === "gd") && ["logistics_calc", "service_calc", "rework"].includes(deal.status);
  const [err, setErr] = useState("");
  const [hs, setHs] = useState<{ code: string; description: string; duty_pct: number; vat_pct: number; certification: string[] }[]>([]);
  const batches = deal.batches || [];
  const positions = deal.positions || [];
  const [batch, setBatch] = useState({ name: `Партия ${batches.length + 1}`, route: "завод → Маньчжурия → Забайкальск → пункт поставки", transport_units: 1, transit_days: 14, customs_days: 5, rf_delivery_days: 5, border_planned: "", allocation: "cost",
    intl_freight: 0, intl_currency: "CNY", insurance_pct: 0.3, broker_rub: 0, rf_delivery_rub: 0, comment_freight: "", comment_broker: "", comment_rf: "" });
  const [bc, setBc] = useState<Record<string, string>>({});   // комментарии к остальным полям партии
  const bci = (k: string) => <td><input value={bc[k] || ""} placeholder="комментарий" onChange={(e) => setBc({ ...bc, [k]: e.target.value })} /></td>;
  const [forms, setForms] = useState<Record<string, PosForm>>(() => Object.fromEntries(positions.map((p) => {
    const l = (p.logistics || {}) as Partial<PosForm>;
    return [p.id, { domestic_logistics: (l.domestic_logistics as Money) || { amount: 0, currency: "CNY" }, export_formalities: (l.export_formalities as Money) || { amount: 0, currency: "CNY" },
      hs_code: l.hs_code || "", duty_pct: l.duty_pct != null ? String(l.duty_pct) : "", vat_pct: l.vat_pct != null ? String(l.vat_pct) : "20", hs_manual_comment: "",
      util_fee_rub: l.util_fee_rub || 0, certification_rub: l.certification_rub || 0, volume_m3: l.volume_m3 != null ? String(l.volume_m3) : "", comments: (l.comments as Record<string, string>) || {},
      util_applies: l.util_applies ?? null, util_base_rate_rub: l.util_base_rate_rub != null ? String(l.util_base_rate_rub) : "", util_coefficient: l.util_coefficient != null ? String(l.util_coefficient) : "" }];
  })));

  const [uref, setUref] = useState<Record<string, UtilRef>>({});
  const loadUref = (pid: string, hsCode: string) => api.get<UtilRef>(`/api/deals/${deal.id}/logistics/positions/${pid}/util-reference?hs_code=${encodeURIComponent(hsCode)}`)
    .then((r) => setUref((u) => ({ ...u, [pid]: r }))).catch(() => undefined);
  useEffect(() => { positions.forEach((p) => loadUref(p.id, ((p.logistics || {}) as { hs_code?: string }).hs_code || "")); }, [deal.id]);
  const n = (v: string) => (v === "" ? null : Number(v.replace(",", ".")));

  const onErr = (e: unknown) => { const d = (e as ApiError).detail as { message?: string; check?: { history_avg: number; deviation_pct: number } }; setErr((d?.message || (e as Error).message) + (d?.check ? ` (среднее по истории ${fmtRub(d.check.history_avg)}, отклонение ${d.check.deviation_pct} %)` : "")); };

  async function createBatch() {
    try {
      await api.post(`/api/deals/${deal.id}/logistics/batches`, { name: batch.name, route: batch.route, transport_units: batch.transport_units, transit_days: batch.transit_days, customs_days: batch.customs_days,
        rf_delivery_days: batch.rf_delivery_days, border_planned: batch.border_planned || null, allocation: batch.allocation,
        items: { intl_freight: { amount: batch.intl_freight, currency: batch.intl_currency }, insurance_pct: batch.insurance_pct, broker_rub: batch.broker_rub, rf_delivery_rub: batch.rf_delivery_rub,
          comments: { ...bc, intl_freight: batch.comment_freight, broker_rub: batch.comment_broker, rf_delivery_rub: batch.comment_rf } },
        position_ids: positions.filter((p) => !p.batch_id).map((p) => p.id) });
      setErr(""); reload();
    } catch (e) { onErr(e); }
  }
  async function savePos(pid: string) {
    const f = forms[pid];
    try {
      await api.put(`/api/deals/${deal.id}/logistics/positions/${pid}`, { ...f, duty_pct: f.duty_pct === "" ? null : Number(f.duty_pct), vat_pct: f.vat_pct === "" ? null : Number(f.vat_pct), volume_m3: f.volume_m3 === "" ? null : Number(f.volume_m3),
        util_applies: utilOn(pid), util_base_rate_rub: utilBase(pid), util_coefficient: utilCoef(pid), util_fee_rub: utilOn(pid) ? utilSum(pid) : 0 });
      setErr(""); reload();
    } catch (e) { onErr(e); }
  }
  async function complete() {
    try { await api.post(`/api/deals/${deal.id}/logistics/complete`); setErr(""); reload(); } catch (e) { onErr(e); }
  }
  // утилизационный сбор: начисляется (по справочнику ТН ВЭД, можно изменить) → базовая ставка × коэффициент
  const utilOn = (pid: string) => forms[pid].util_applies ?? (uref[pid]?.applies_by_hs ?? (forms[pid].util_fee_rub > 0));
  const utilBase = (pid: string) => n(forms[pid].util_base_rate_rub) ?? uref[pid]?.base_rate_rub ?? 0;
  const utilCoef = (pid: string) => n(forms[pid].util_coefficient) ?? uref[pid]?.coefficient ?? null;
  const utilSum = (pid: string) => Math.round(utilBase(pid) * (utilCoef(pid) ?? 0) * 100) / 100;
  const upd = (pid: string, patch: Partial<PosForm>) => setForms({ ...forms, [pid]: { ...forms[pid], ...patch } });

  return (
    <>
      <style>{`.ltable td input, .ltable td select { width: 100%; box-sizing: border-box; } .ltable td { vertical-align: middle; padding: 4px 6px; }
        .ltable tr.lsec td { background: var(--accent-soft); font-weight: 600; } .ltable tbody tr:not(.lsec):hover { background: #fafafa; }`}</style>
      {err && <div className="alert error">{err}</div>}
      <div className="card">
        <h2 style={{ marginTop: 0 }}>Партии и консолидация</h2>
        <table><thead><tr><th>Партия</th><th>Маршрут</th><th className="num">Трансп. ед.</th><th className="num">Транзит / оформление / по РФ, дн</th><th>CMR план / факт</th><th>Международная перевозка</th><th>Страховка</th><th className="num">Брокер и СВХ</th><th className="num">Доставка по РФ</th><th>Распределение</th><th>Позиции</th></tr></thead>
          <tbody>{batches.map((b) => { const it = b.items as Record<string, unknown>; const fr = it.intl_freight as Money | undefined; return (
            <tr key={b.id}><td>{b.name}</td><td>{b.route}</td><td className="num">{b.transport_units}</td><td className="num">{b.transit_days} / {b.customs_days} / {b.rf_delivery_days ?? 5}</td><td>{fmtDate(b.border_planned)} / {b.border_actual ? <b>{fmtDate(b.border_actual)}</b> : "—"}</td>
              <td>{fr ? `${fmtRub(fr.amount)} ${fr.currency}` : "—"}</td><td>{it.insurance_pct != null ? `${it.insurance_pct} %` : "—"}</td><td className="num">{fmtRub(it.broker_rub as number)}</td><td className="num">{fmtRub(it.rf_delivery_rub as number)}</td>
              <td>{b.allocation}</td><td>{positions.filter((p) => p.batch_id === b.id).map((p) => designation(p)).join(", ")}</td></tr>); })}</tbody></table>
        {canEdit && (
          <>
            <h3 style={{ marginBottom: 4 }}>Новая партия</h3>
            <table className="ltable">
              <thead><tr><th style={{ width: "34%" }}>Параметр</th><th style={{ width: "26%" }}>Значение</th><th style={{ width: 80 }}>Ед.</th><th>Комментарий</th></tr></thead>
              <tbody>
                <tr className="lsec"><td colSpan={4}>Маршрут и сроки</td></tr>
                <tr><td>Название партии</td><td><input value={batch.name} onChange={(e) => setBatch({ ...batch, name: e.target.value })} /></td><td />{bci("name")}</tr>
                <tr><td>Маршрут</td><td colSpan={2}><input value={batch.route} onChange={(e) => setBatch({ ...batch, route: e.target.value })} /></td>{bci("route")}</tr>
                <tr><td>Транспортных единиц</td><td><input type="number" value={batch.transport_units} onChange={(e) => setBatch({ ...batch, transport_units: Number(e.target.value) })} /></td><td className="muted">шт.</td>{bci("transport_units")}</tr>
                <tr><td>Транзит до границы</td><td><input type="number" value={batch.transit_days} onChange={(e) => setBatch({ ...batch, transit_days: Number(e.target.value) })} /></td><td className="muted">дн</td>{bci("transit_days")}</tr>
                <tr><td>Таможенное оформление</td><td><input type="number" value={batch.customs_days} onChange={(e) => setBatch({ ...batch, customs_days: Number(e.target.value) })} /></td><td className="muted">дн</td>{bci("customs_days")}</tr>
                <tr><td>Доставка по РФ после выпуска ДТ</td><td><input type="number" value={batch.rf_delivery_days} onChange={(e) => setBatch({ ...batch, rf_delivery_days: Number(e.target.value) })} /></td><td className="muted">дн</td>{bci("rf_delivery_days")}</tr>
                <tr><td>Плановая дата пересечения границы (CMR)</td><td><input type="date" value={batch.border_planned} onChange={(e) => setBatch({ ...batch, border_planned: e.target.value })} /></td><td />{bci("border_planned")}</tr>
                <tr className="lsec"><td colSpan={4}>Стоимость</td></tr>
                <tr><td>Международная перевозка</td><td><input type="number" value={batch.intl_freight} onChange={(e) => setBatch({ ...batch, intl_freight: Number(e.target.value) })} /></td>
                  <td><select value={batch.intl_currency} onChange={(e) => setBatch({ ...batch, intl_currency: e.target.value })}><option>CNY</option><option>USD</option><option>RUB</option></select></td>
                  <td><input value={batch.comment_freight} onChange={(e) => setBatch({ ...batch, comment_freight: e.target.value })} /></td></tr>
                <tr><td>Страхование</td><td><input type="number" step="0.01" value={batch.insurance_pct} onChange={(e) => setBatch({ ...batch, insurance_pct: Number(e.target.value) })} /></td><td className="muted">% от стоимости</td>{bci("insurance_pct")}</tr>
                <tr><td>Брокер и СВХ</td><td><input type="number" value={batch.broker_rub} onChange={(e) => setBatch({ ...batch, broker_rub: Number(e.target.value) })} /></td><td className="muted">RUB</td>
                  <td><input value={batch.comment_broker} onChange={(e) => setBatch({ ...batch, comment_broker: e.target.value })} /></td></tr>
                <tr><td>Доставка по РФ до объекта</td><td><input type="number" value={batch.rf_delivery_rub} onChange={(e) => setBatch({ ...batch, rf_delivery_rub: Number(e.target.value) })} /></td><td className="muted">RUB</td>
                  <td><input value={batch.comment_rf} onChange={(e) => setBatch({ ...batch, comment_rf: e.target.value })} /></td></tr>
                <tr><td>Распределение общих статей между позициями</td><td><select value={batch.allocation} onChange={(e) => setBatch({ ...batch, allocation: e.target.value })}><option value="cost">по стоимости</option><option value="weight">по весу</option><option value="volume">по объёму</option></select></td><td />{bci("allocation")}</tr>
              </tbody>
            </table>
            <div className="row" style={{ marginTop: 8 }}><button className="btn secondary" onClick={createBatch}>Создать партию (нераспределённые позиции)</button></div>
          </>
        )}
      </div>

      {positions.map((p) => {
        const f = forms[p.id];
        if (!f) return null;
        const lp = p.logistics_params || {};
        // проверка по истории (сохраняется при записи позиции): среднее и отклонение — рядом со статьёй
        const checks = ((p.logistics || {}) as { checks?: Record<string, { history_avg?: number; deviation_pct?: number; needs_comment?: boolean; n?: number }> }).checks || {};
        const ci = (k: string, ph = "комментарий") => <td><input placeholder={ph} disabled={!canEdit} value={f.comments[k] || ""} onChange={(e) => upd(p.id, { comments: { ...f.comments, [k]: e.target.value } })} /></td>;
        const ur = uref[p.id];
        const hc = ((p.logistics || {}) as { hs_check?: { found?: boolean; mismatch?: boolean; ref_duty_pct?: number; ref_vat_pct?: number; certification?: string[] } }).hs_check;
        const hist = (k: string) => { const c = checks[k]; if (!c || c.history_avg == null) return <span className="muted">—</span>;
          return <span className={c.needs_comment ? "badge warn" : "muted"} style={{ fontSize: 12 }}>ср. {fmtRub(c.history_avg)}{c.deviation_pct != null ? `, ${c.deviation_pct > 0 ? "+" : ""}${c.deviation_pct} %` : ""}</span>; };
        return (
          <div className="card" key={p.id}>
            <h2 style={{ marginTop: 0 }}>{p.machine_type} {designation(p)} × {p.qty} <span className="muted">· данных для логиста (L): {Object.keys(lp).length}</span></h2>
            <details style={{ marginBottom: 10 }} open={Object.keys(lp).length > 0}><summary className="muted" style={{ cursor: "pointer" }}>Технические данные из опросника (метка L)</summary>
              <div style={{ marginTop: 6 }}><Questionnaire position={p} onlyL /></div></details>
            <table className="ltable">
              <thead><tr><th style={{ width: "34%" }}>Статья</th><th style={{ width: "22%" }}>Значение</th><th style={{ width: 70 }}>Ед.</th><th style={{ width: "18%" }}>По истории</th><th>Комментарий</th></tr></thead>
              <tbody>
                <tr className="lsec"><td colSpan={5}>Китай: до границы</td></tr>
                <tr><td>Внутрикитайская логистика завод → граница</td>
                  <td><input type="number" value={f.domestic_logistics.amount} disabled={!canEdit} onChange={(e) => upd(p.id, { domestic_logistics: { ...f.domestic_logistics, amount: Number(e.target.value) } })} /></td>
                  <td className="muted">CNY</td><td>{hist("domestic_logistics")}</td>
                  <td><input placeholder="при отклонении от истории" disabled={!canEdit} value={f.comments.domestic_logistics || ""} onChange={(e) => upd(p.id, { comments: { ...f.comments, domestic_logistics: e.target.value } })} /></td></tr>
                <tr><td>Экспортные формальности КНР</td>
                  <td><input type="number" value={f.export_formalities.amount} disabled={!canEdit} onChange={(e) => upd(p.id, { export_formalities: { ...f.export_formalities, amount: Number(e.target.value) } })} /></td>
                  <td className="muted">CNY</td><td>{hist("export_formalities")}</td>
                  <td><input placeholder="при отклонении от истории" disabled={!canEdit} value={f.comments.export_formalities || ""} onChange={(e) => upd(p.id, { comments: { ...f.comments, export_formalities: e.target.value } })} /></td></tr>
                <tr><td>Объём (для распределения общих статей)</td>
                  <td><input type="number" value={f.volume_m3} disabled={!canEdit} onChange={(e) => upd(p.id, { volume_m3: e.target.value })} /></td><td className="muted">м³</td><td />{ci("volume_m3")}</tr>
                <tr className="lsec"><td colSpan={5}>Таможня РФ</td></tr>
                <tr><td>Код ТН ВЭД <span className="muted">(поиск по коду или описанию)</span></td>
                  <td colSpan={2}><input value={f.hs_code} disabled={!canEdit} onChange={(e) => { upd(p.id, { hs_code: e.target.value }); if (e.target.value.length >= 2) api.get<typeof hs>(`/api/refs/hs?q=${encodeURIComponent(e.target.value)}`).then(setHs); }} />
                    {canEdit && hs.length > 0 && <div style={{ fontSize: 12, marginTop: 2 }}>{hs.slice(0, 5).map((h) => <div key={h.code} style={{ cursor: "pointer", padding: "2px 0" }} onClick={() => { upd(p.id, { hs_code: h.code, duty_pct: String(h.duty_pct), vat_pct: String(h.vat_pct), util_applies: null, util_coefficient: "" }); setHs([]); loadUref(p.id, h.code); }}><b>{h.code}</b> — {h.description} (пошлина {h.duty_pct} %, НДС {h.vat_pct} %; {h.certification.join(", ")})</div>)}</div>}</td>
                  <td className="muted" style={{ fontSize: 12 }}>{hc?.found === false ? "кода нет в справочнике" : hc?.certification?.length ? hc.certification.join(", ") : ""}</td>{ci("hs_code")}</tr>
                <tr><td>Пошлина</td><td><input type="number" value={f.duty_pct} disabled={!canEdit} onChange={(e) => upd(p.id, { duty_pct: e.target.value })} /></td><td className="muted">%</td><td>{hc?.found ? <span className={hc.mismatch ? "badge warn" : "muted"} style={{ fontSize: 12 }}>справочник: {hc.ref_duty_pct} %</span> : null}</td>
                  <td><input placeholder="обязателен при ручной ставке" disabled={!canEdit} value={f.hs_manual_comment} onChange={(e) => upd(p.id, { hs_manual_comment: e.target.value })} /></td></tr>
                <tr><td>НДС при ввозе</td><td><input type="number" value={f.vat_pct} disabled={!canEdit} onChange={(e) => upd(p.id, { vat_pct: e.target.value })} /></td><td className="muted">%</td><td>{hc?.found ? <span className={hc.mismatch ? "badge warn" : "muted"} style={{ fontSize: 12 }}>справочник: {hc.ref_vat_pct} %</span> : null}</td>{ci("vat_pct")}</tr>
                <tr><td>Сертификация (ТР ТС 010/2011, ЭПБ, ПСМ)</td><td><input type="number" value={f.certification_rub} disabled={!canEdit} onChange={(e) => upd(p.id, { certification_rub: Number(e.target.value) })} /></td><td className="muted">RUB</td><td>{hist("certification_rub")}</td>
                  <td><input placeholder="при отклонении от истории" disabled={!canEdit} value={f.comments.certification_rub || ""} onChange={(e) => upd(p.id, { comments: { ...f.comments, certification_rub: e.target.value } })} /></td></tr>
                <tr className="lsec"><td colSpan={5}>Утилизационный сбор</td></tr>
                <tr><td>Начисляется</td>
                  <td><label className="row"><input type="checkbox" style={{ width: "auto" }} checked={utilOn(p.id)} disabled={!canEdit} onChange={(e) => upd(p.id, { util_applies: e.target.checked })} /> {utilOn(p.id) ? "да" : "нет"}</label></td><td />
                  <td className="muted" style={{ fontSize: 12 }}>{ur?.applies_by_hs == null ? (f.hs_code ? "кода нет в справочнике" : "укажите код ТН ВЭД") : `по ТН ВЭД: ${ur.applies_by_hs ? "начисляется" : "не начисляется"}`}</td>{ci("util_applies")}</tr>
                {utilOn(p.id) && <>
                  <tr><td>Базовая ставка</td><td><input inputMode="decimal" value={f.util_base_rate_rub} placeholder={ur ? String(ur.base_rate_rub) : ""} disabled={!canEdit} onChange={(e) => upd(p.id, { util_base_rate_rub: e.target.value })} /></td>
                    <td className="muted">RUB</td><td className="muted" style={{ fontSize: 12 }}>{ur ? `по настройке: ${fmtRub(ur.base_rate_rub)}` : ""}</td>{ci("util_base_rate_rub")}</tr>
                  <tr><td>Коэффициент <span className="muted">(вид техники и код ТН ВЭД)</span></td><td><input inputMode="decimal" value={f.util_coefficient} placeholder={ur?.coefficient != null ? String(ur.coefficient) : "введите"} disabled={!canEdit} onChange={(e) => upd(p.id, { util_coefficient: e.target.value })} /></td>
                    <td className="muted">×</td><td className="muted" style={{ fontSize: 12 }}>{ur?.rule ? `по настройке: ${ur.coefficient} (${[ur.rule.hs_prefix, ur.rule.machine_type].filter(Boolean).join(", ")}${ur.rule.comment ? "; " + ur.rule.comment : ""})` : "в настройках нет — введите"}</td>{ci("util_coefficient")}</tr>
                  <tr><td><b>Утилизационный сбор</b> <span className="muted">= ставка × коэффициент</span></td><td className="num"><b>{utilCoef(p.id) == null ? "—" : fmtRub(utilSum(p.id))}</b></td><td className="muted">RUB</td><td />{ci("util_fee_rub")}</tr>
                </>}
              </tbody>
            </table>
            {canEdit && <div className="row" style={{ marginTop: 8 }}><button className="btn secondary" onClick={() => savePos(p.id)}>Сохранить позицию</button><span className="muted">партия: {batches.find((b) => b.id === p.batch_id)?.name || "не распределена"}</span></div>}
          </div>
        );
      })}
      {canEdit && <button className="btn" onClick={complete}>Завершить блок логистики</button>}
    </>
  );
}
