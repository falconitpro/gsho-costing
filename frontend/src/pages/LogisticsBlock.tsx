import { useState } from "react";
import { api, ApiError, fmtRub } from "../api";
import type { Deal, Me } from "../types";
import Questionnaire from "../components/Questionnaire";

type Money = { amount: number; currency: string };
type PosForm = { domestic_logistics: Money; export_formalities: Money; hs_code: string; duty_pct: string; vat_pct: string; hs_manual_comment: string; util_fee_rub: number; certification_rub: number; volume_m3: string; comments: Record<string, string> };

/** Блок логиста (раздел 5): партии, статьи по позициям, ТН ВЭД, проверка по истории с комментарием. */
export default function LogisticsBlock({ deal, me, reload }: { deal: Deal; me: Me; reload: () => void }) {
  const canEdit = me.roles.some((r) => r.code === "logistician" || r.code === "gd") && ["logistics_calc", "service_calc", "rework"].includes(deal.status);
  const [err, setErr] = useState("");
  const [hs, setHs] = useState<{ code: string; description: string; duty_pct: number; vat_pct: number; certification: string[] }[]>([]);
  const batches = deal.batches || [];
  const positions = deal.positions || [];
  const [batch, setBatch] = useState({ name: `Партия ${batches.length + 1}`, route: "завод → Маньчжурия → Забайкальск → пункт поставки", transport_units: 1, transit_days: 14, customs_days: 5, allocation: "cost",
    intl_freight: 0, intl_currency: "CNY", insurance_pct: 0.3, broker_rub: 0, rf_delivery_rub: 0, comment_freight: "", comment_broker: "", comment_rf: "" });
  const [forms, setForms] = useState<Record<string, PosForm>>(() => Object.fromEntries(positions.map((p) => {
    const l = (p.logistics || {}) as Partial<PosForm>;
    return [p.id, { domestic_logistics: (l.domestic_logistics as Money) || { amount: 0, currency: "CNY" }, export_formalities: (l.export_formalities as Money) || { amount: 0, currency: "CNY" },
      hs_code: l.hs_code || "", duty_pct: l.duty_pct != null ? String(l.duty_pct) : "", vat_pct: l.vat_pct != null ? String(l.vat_pct) : "20", hs_manual_comment: "",
      util_fee_rub: l.util_fee_rub || 0, certification_rub: l.certification_rub || 0, volume_m3: l.volume_m3 != null ? String(l.volume_m3) : "", comments: (l.comments as Record<string, string>) || {} }];
  })));

  const onErr = (e: unknown) => { const d = (e as ApiError).detail as { message?: string; check?: { history_avg: number; deviation_pct: number } }; setErr((d?.message || (e as Error).message) + (d?.check ? ` (среднее по истории ${fmtRub(d.check.history_avg)}, отклонение ${d.check.deviation_pct} %)` : "")); };

  async function createBatch() {
    try {
      await api.post(`/api/deals/${deal.id}/logistics/batches`, { name: batch.name, route: batch.route, transport_units: batch.transport_units, transit_days: batch.transit_days, customs_days: batch.customs_days, allocation: batch.allocation,
        items: { intl_freight: { amount: batch.intl_freight, currency: batch.intl_currency }, insurance_pct: batch.insurance_pct, broker_rub: batch.broker_rub, rf_delivery_rub: batch.rf_delivery_rub,
          comments: { intl_freight: batch.comment_freight, broker_rub: batch.comment_broker, rf_delivery_rub: batch.comment_rf } },
        position_ids: positions.filter((p) => !p.batch_id).map((p) => p.id) });
      setErr(""); reload();
    } catch (e) { onErr(e); }
  }
  async function savePos(pid: string) {
    const f = forms[pid];
    try {
      await api.put(`/api/deals/${deal.id}/logistics/positions/${pid}`, { ...f, duty_pct: f.duty_pct === "" ? null : Number(f.duty_pct), vat_pct: f.vat_pct === "" ? null : Number(f.vat_pct), volume_m3: f.volume_m3 === "" ? null : Number(f.volume_m3) });
      setErr(""); reload();
    } catch (e) { onErr(e); }
  }
  async function complete() {
    try { await api.post(`/api/deals/${deal.id}/logistics/complete`); setErr(""); reload(); } catch (e) { onErr(e); }
  }
  const upd = (pid: string, patch: Partial<PosForm>) => setForms({ ...forms, [pid]: { ...forms[pid], ...patch } });

  return (
    <>
      {err && <div className="alert error">{err}</div>}
      <div className="card">
        <h2 style={{ marginTop: 0 }}>Партии и консолидация</h2>
        <table><thead><tr><th>Партия</th><th>Маршрут</th><th className="num">Трансп. ед.</th><th className="num">Транзит / оформление, дн</th><th>Международная перевозка</th><th>Страховка</th><th className="num">Брокер и СВХ</th><th className="num">Доставка по РФ</th><th>Распределение</th><th>Позиции</th></tr></thead>
          <tbody>{batches.map((b) => { const it = b.items as Record<string, unknown>; const fr = it.intl_freight as Money | undefined; return (
            <tr key={b.id}><td>{b.name}</td><td>{b.route}</td><td className="num">{b.transport_units}</td><td className="num">{b.transit_days} / {b.customs_days}</td>
              <td>{fr ? `${fmtRub(fr.amount)} ${fr.currency}` : "—"}</td><td>{it.insurance_pct != null ? `${it.insurance_pct} %` : "—"}</td><td className="num">{fmtRub(it.broker_rub as number)}</td><td className="num">{fmtRub(it.rf_delivery_rub as number)}</td>
              <td>{b.allocation}</td><td>{positions.filter((p) => p.batch_id === b.id).map((p) => p.model).join(", ")}</td></tr>); })}</tbody></table>
        {canEdit && (
          <div className="grid cols-3" style={{ marginTop: 10 }}>
            <label className="f">Название<input value={batch.name} onChange={(e) => setBatch({ ...batch, name: e.target.value })} /></label>
            <label className="f">Маршрут<input value={batch.route} onChange={(e) => setBatch({ ...batch, route: e.target.value })} /></label>
            <label className="f">Транспортных единиц<input type="number" value={batch.transport_units} onChange={(e) => setBatch({ ...batch, transport_units: Number(e.target.value) })} /></label>
            <label className="f">Транзит, дн<input type="number" value={batch.transit_days} onChange={(e) => setBatch({ ...batch, transit_days: Number(e.target.value) })} /></label>
            <label className="f">Оформление, дн<input type="number" value={batch.customs_days} onChange={(e) => setBatch({ ...batch, customs_days: Number(e.target.value) })} /></label>
            <label className="f">Распределение общих статей<select value={batch.allocation} onChange={(e) => setBatch({ ...batch, allocation: e.target.value })}><option value="cost">по стоимости</option><option value="weight">по весу</option><option value="volume">по объёму</option></select></label>
            <label className="f">Международная перевозка<div className="row"><input type="number" value={batch.intl_freight} onChange={(e) => setBatch({ ...batch, intl_freight: Number(e.target.value) })} />
              <select value={batch.intl_currency} onChange={(e) => setBatch({ ...batch, intl_currency: e.target.value })}><option>CNY</option><option>USD</option><option>RUB</option></select></div>
              <input placeholder="комментарий при отклонении от истории" value={batch.comment_freight} onChange={(e) => setBatch({ ...batch, comment_freight: e.target.value })} /></label>
            <label className="f">Страхование, % от стоимости<input type="number" step="0.01" value={batch.insurance_pct} onChange={(e) => setBatch({ ...batch, insurance_pct: Number(e.target.value) })} /></label>
            <label className="f">Брокер и СВХ, RUB<input type="number" value={batch.broker_rub} onChange={(e) => setBatch({ ...batch, broker_rub: Number(e.target.value) })} /><input placeholder="комментарий" value={batch.comment_broker} onChange={(e) => setBatch({ ...batch, comment_broker: e.target.value })} /></label>
            <label className="f">Доставка по РФ до объекта, RUB<input type="number" value={batch.rf_delivery_rub} onChange={(e) => setBatch({ ...batch, rf_delivery_rub: Number(e.target.value) })} /><input placeholder="комментарий" value={batch.comment_rf} onChange={(e) => setBatch({ ...batch, comment_rf: e.target.value })} /></label>
            <div className="row" style={{ alignItems: "end" }}><button className="btn secondary" onClick={createBatch}>Создать партию (нераспределённые позиции)</button></div>
          </div>
        )}
      </div>

      {positions.map((p) => {
        const f = forms[p.id];
        if (!f) return null;
        const lp = p.logistics_params || {};
        return (
          <div className="card" key={p.id}>
            <h2 style={{ marginTop: 0 }}>{p.machine_type} {p.model} × {p.qty} <span className="muted">· данных для логиста (L): {Object.keys(lp).length}</span></h2>
            <details style={{ marginBottom: 10 }} open={Object.keys(lp).length > 0}><summary className="muted" style={{ cursor: "pointer" }}>Технические данные из опросника (метка L)</summary>
              <div style={{ marginTop: 6 }}><Questionnaire position={p} onlyL /></div></details>
            <div className="grid cols-3">
              <label className="f">Внутрикитайская логистика завод → граница<div className="row"><input type="number" value={f.domestic_logistics.amount} disabled={!canEdit} onChange={(e) => upd(p.id, { domestic_logistics: { ...f.domestic_logistics, amount: Number(e.target.value) } })} /><span>CNY</span></div>
                <input placeholder="комментарий к отклонению" disabled={!canEdit} value={f.comments.domestic_logistics || ""} onChange={(e) => upd(p.id, { comments: { ...f.comments, domestic_logistics: e.target.value } })} /></label>
              <label className="f">Экспортные формальности КНР<div className="row"><input type="number" value={f.export_formalities.amount} disabled={!canEdit} onChange={(e) => upd(p.id, { export_formalities: { ...f.export_formalities, amount: Number(e.target.value) } })} /><span>CNY</span></div></label>
              <label className="f">Объём, м³ (для распределения)<input type="number" value={f.volume_m3} disabled={!canEdit} onChange={(e) => upd(p.id, { volume_m3: e.target.value })} /></label>
              <label className="f">Код ТН ВЭД (поиск по коду/описанию)<input value={f.hs_code} disabled={!canEdit} onChange={(e) => { upd(p.id, { hs_code: e.target.value }); if (e.target.value.length >= 2) api.get<typeof hs>(`/api/refs/hs?q=${encodeURIComponent(e.target.value)}`).then(setHs); }} />
                {canEdit && hs.length > 0 && <div style={{ fontSize: 12 }}>{hs.slice(0, 5).map((h) => <div key={h.code} style={{ cursor: "pointer" }} onClick={() => { upd(p.id, { hs_code: h.code, duty_pct: String(h.duty_pct), vat_pct: String(h.vat_pct) }); setHs([]); }}>{h.code} — {h.description} (пошлина {h.duty_pct} %, НДС {h.vat_pct} %; {h.certification.join(", ")})</div>)}</div>}</label>
              <label className="f">Пошлина, % <input type="number" value={f.duty_pct} disabled={!canEdit} onChange={(e) => upd(p.id, { duty_pct: e.target.value })} /><input placeholder="комментарий при ручной ставке" disabled={!canEdit} value={f.hs_manual_comment} onChange={(e) => upd(p.id, { hs_manual_comment: e.target.value })} /></label>
              <label className="f">НДС при ввозе, %<input type="number" value={f.vat_pct} disabled={!canEdit} onChange={(e) => upd(p.id, { vat_pct: e.target.value })} /></label>
              <label className="f">Утилизационный сбор, RUB<input type="number" value={f.util_fee_rub} disabled={!canEdit} onChange={(e) => upd(p.id, { util_fee_rub: Number(e.target.value) })} /></label>
              <label className="f">Сертификация (ТР ТС 010/2011, ЭПБ, ПСМ), RUB<input type="number" value={f.certification_rub} disabled={!canEdit} onChange={(e) => upd(p.id, { certification_rub: Number(e.target.value) })} />
                <input placeholder="комментарий к отклонению" disabled={!canEdit} value={f.comments.certification_rub || ""} onChange={(e) => upd(p.id, { comments: { ...f.comments, certification_rub: e.target.value } })} /></label>
            </div>
            {canEdit && <div className="row" style={{ marginTop: 8 }}><button className="btn secondary" onClick={() => savePos(p.id)}>Сохранить позицию</button><span className="muted">партия: {batches.find((b) => b.id === p.batch_id)?.name || "не распределена"}</span></div>}
          </div>
        );
      })}
      {canEdit && <button className="btn" onClick={complete}>Завершить блок логистики</button>}
    </>
  );
}
