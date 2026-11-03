import { useEffect, useState } from "react";
import { api, ApiError, fmtDate, fmtRub, designation } from "../api";
import { STATUS_TITLES, type Deal, type Me } from "../types";
import TechParamsView from "../components/TechParamsView";
import SpecTable from "../components/SpecTable";
import { UndoButton, useUndoHistory } from "../undo";

type Money = { amount: number; currency: string };
type PosForm = { domestic_logistics: Money; export_formalities: Money; hs_code: string; duty_pct: string; vat_pct: string; hs_manual_comment: string; hs_description: string; util_fee_rub: number; certification_rub: number; volume_m3: string; comments: Record<string, string>;
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
    intl_freight: 0, intl_currency: "CNY", insurance_pct: 0.3, broker_rub: 0, rf_delivery_rub: 0, comment_freight: "", comment_broker: "", comment_rf: "", d2d: true, d2d_amount: 0, d2d_currency: "CNY" });
  const [borderPct, setBorderPct] = useState(10);   // единая стоимость доставки: доля до границы РФ (настройка КД / ЗГД)
  useEffect(() => { api.get<{ door_to_door_border_pct: number }>(`/api/deals/${deal.id}/logistics/defaults`).then((r) => setBorderPct(r.door_to_door_border_pct)).catch(() => undefined); }, [deal.id]);
  const [bc, setBc] = useState<Record<string, string>>({});   // комментарии к остальным полям партии
  const bcc = (k: string) => <input value={bc[k] || ""} placeholder="комментарий" onChange={(e) => setBc({ ...bc, [k]: e.target.value })} />;
  const [forms, setForms] = useState<Record<string, PosForm>>(() => Object.fromEntries(positions.map((p) => {
    const l = (p.logistics || {}) as Partial<PosForm>;
    return [p.id, { domestic_logistics: (l.domestic_logistics as Money) || { amount: 0, currency: "CNY" }, export_formalities: (l.export_formalities as Money) || { amount: 0, currency: "CNY" },
      hs_code: l.hs_code || "", duty_pct: l.duty_pct != null ? String(l.duty_pct) : "", vat_pct: l.vat_pct != null ? String(l.vat_pct) : "22", hs_manual_comment: "", hs_description: "",
      util_fee_rub: l.util_fee_rub || 0, certification_rub: l.certification_rub || 0, volume_m3: l.volume_m3 != null ? String(l.volume_m3) : "", comments: (l.comments as Record<string, string>) || {},
      util_applies: l.util_applies ?? null, util_base_rate_rub: l.util_base_rate_rub != null ? String(l.util_base_rate_rub) : "", util_coefficient: l.util_coefficient != null ? String(l.util_coefficient) : "" }];
  })));

  const [uref, setUref] = useState<Record<string, UtilRef>>({});
  const loadUref = (pid: string, hsCode: string) => api.get<UtilRef>(`/api/deals/${deal.id}/logistics/positions/${pid}/util-reference?hs_code=${encodeURIComponent(hsCode)}`)
    .then((r) => setUref((u) => ({ ...u, [pid]: r }))).catch(() => undefined);
  useEffect(() => { positions.forEach((p) => loadUref(p.id, ((p.logistics || {}) as { hs_code?: string }).hs_code || "")); }, [deal.id]);
  const n = (v: string) => (v === "" ? null : Number(v.replace(",", ".")));

  // «Отменить последнее действие»: позиции, новая партия и комментарии
  const history = useUndoHistory({ forms, batch, bc }, (st) => { setForms(st.forms); setBatch(st.batch); setBc(st.bc); }, deal.id);
  const onErr = (e: unknown) => { const d = (e as ApiError).detail as { message?: string; check?: { history_avg: number; deviation_pct: number } }; setErr((d?.message || (e as Error).message) + (d?.check ? ` (среднее по истории ${fmtRub(d.check.history_avg)}, отклонение ${d.check.deviation_pct} %)` : "")); };

  async function createBatch() {
    try {
      await api.post(`/api/deals/${deal.id}/logistics/batches`, { name: batch.name, route: batch.route, transport_units: batch.transport_units, transit_days: batch.transit_days, customs_days: batch.customs_days,
        rf_delivery_days: batch.rf_delivery_days, border_planned: batch.border_planned || null, allocation: batch.allocation,
        items: { intl_freight: { amount: batch.d2d ? 0 : batch.intl_freight, currency: batch.intl_currency }, insurance_pct: batch.insurance_pct, broker_rub: batch.broker_rub,
          rf_delivery_rub: batch.d2d ? 0 : batch.rf_delivery_rub, door_to_door: batch.d2d ? { amount: batch.d2d_amount, currency: batch.d2d_currency } : null,
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
  // единая стоимость доставки партии (сохранённой или создаваемой) покрывает путь завод → граница
  const d2dCovers = (p: { batch_id: string | null }) => {
    const b = batches.find((x) => x.id === p.batch_id);
    const d = b ? ((b.items || {}) as { door_to_door?: Money | null }).door_to_door : null;
    return b ? !!d?.amount : batch.d2d && batch.d2d_amount > 0;
  };
  // ручной ввод кода ТН ВЭД: наименование — из справочника, внешнего сервиса или вручную
  const [hsInfo, setHsInfo] = useState<Record<string, { code: string; source: string | null; description: string; note?: string; error?: string; links?: { title: string; url: string }[]; duty_pct?: number; vat_pct?: number }>>({});
  const lookupHs = (pid: string, code: string) => api.get<(typeof hsInfo)[string]>(`/api/refs/hs/lookup?code=${encodeURIComponent(code)}`).then((r) => {
    setHsInfo((h) => ({ ...h, [pid]: r }));
    if (r.description) setForms((fs) => ({ ...fs, [pid]: { ...fs[pid], hs_code: r.code, hs_description: fs[pid].hs_description || r.description,
      ...(r.source === "reference" ? { duty_pct: String(r.duty_pct), vat_pct: String(r.vat_pct) } : {}) } }));
    loadUref(pid, r.code);
  }).catch(() => undefined);
  const upd = (pid: string, patch: Partial<PosForm>) => setForms({ ...forms, [pid]: { ...forms[pid], ...patch } });

  return (
    <>
      {err && <div className="alert error">{err}</div>}
      <div className="card row" style={{ justifyContent: "space-between" }}>
        <span>Статус: <span className="badge accent">{STATUS_TITLES[deal.status] || deal.status}</span> · логистика: {deal.logistics_done ? <span className="badge ok">рассчитана, передана далее</span> : <span className="badge warn">в работе</span>}</span>
        {canEdit && !deal.logistics_done && <button className="btn" onClick={complete}>Отправить далее на расчёт</button>}
      </div>
      <div className="card">
        <h2 style={{ marginTop: 0 }}>Доставка</h2>
        <table><thead><tr><th>Партия</th><th>Маршрут</th><th className="num">Трансп. ед.</th><th className="num">Транзит / оформление / по РФ, дн</th><th>CMR план / факт</th><th>Доставка до границы РФ</th><th>Страховка</th><th className="num">Брокер и СВХ</th><th className="num">Доставка по РФ</th><th>Позиции</th></tr></thead>
          <tbody>{batches.map((b) => { const it = b.items as Record<string, unknown>; const fr = it.intl_freight as Money | undefined; const d2d = it.door_to_door as Money | null | undefined; return (
            <tr key={b.id}><td>{b.name}</td><td>{b.route}</td><td className="num">{b.transport_units}</td><td className="num">{b.transit_days} / {b.customs_days} / {b.rf_delivery_days ?? 5}</td><td>{fmtDate(b.border_planned)} / {b.border_actual ? <b>{fmtDate(b.border_actual)}</b> : "—"}</td>
              {d2d?.amount ? <td colSpan={1} title="единая стоимость завод → клиент">{fmtRub(d2d.amount * borderPct / 100)} {d2d.currency} <span className="muted">({borderPct} % от {fmtRub(d2d.amount)})</span></td>
                : <td>{fr ? `${fmtRub(fr.amount)} ${fr.currency}` : "—"}</td>}
              <td>{it.insurance_pct != null ? `${it.insurance_pct} %` : "—"}</td><td className="num">{fmtRub(it.broker_rub as number)}</td>
              <td className="num">{d2d?.amount ? <>{fmtRub(d2d.amount * (100 - borderPct) / 100)} {d2d.currency} <span className="muted">({100 - borderPct} %)</span></> : fmtRub(it.rf_delivery_rub as number)}</td>
              <td>{positions.filter((p) => p.batch_id === b.id).map((p) => designation(p)).join(", ")}</td></tr>); })}</tbody></table>
        {canEdit && (
          <>
            <h3 style={{ marginBottom: 4 }}>Новая партия</h3>
            <SpecTable refTitle="Примечание" sections={[
              { title: "Маршрут и сроки", rows: [
                { key: "name", name: "Название партии", filled: !!batch.name, required: true, value: <input value={batch.name} onChange={(e) => setBatch({ ...batch, name: e.target.value })} />, comment: bcc("name") },
                { key: "route", name: "Маршрут", filled: !!batch.route, value: <input value={batch.route} onChange={(e) => setBatch({ ...batch, route: e.target.value })} />, comment: bcc("route") },
                { key: "tu", name: "Транспортных единиц", unit: "шт.", filled: batch.transport_units > 0, value: <input type="number" value={batch.transport_units} onChange={(e) => setBatch({ ...batch, transport_units: Number(e.target.value) })} />, comment: bcc("transport_units") },
                { key: "transit", name: "Транзит до границы", unit: "дн", filled: batch.transit_days > 0, value: <input type="number" value={batch.transit_days} onChange={(e) => setBatch({ ...batch, transit_days: Number(e.target.value) })} />, comment: bcc("transit_days") },
                { key: "customs", name: "Таможенное оформление", unit: "дн", filled: batch.customs_days > 0, value: <input type="number" value={batch.customs_days} onChange={(e) => setBatch({ ...batch, customs_days: Number(e.target.value) })} />, comment: bcc("customs_days") },
                { key: "rfd", name: "Доставка по РФ после выпуска ДТ", unit: "дн", filled: batch.rf_delivery_days > 0, value: <input type="number" value={batch.rf_delivery_days} onChange={(e) => setBatch({ ...batch, rf_delivery_days: Number(e.target.value) })} />, comment: bcc("rf_delivery_days") },
                { key: "cmr", name: "Плановая дата пересечения границы (CMR)", ref: <span className="muted">если известна</span>, filled: !!batch.border_planned, value: <input type="date" value={batch.border_planned} onChange={(e) => setBatch({ ...batch, border_planned: e.target.value })} />, comment: bcc("border_planned") },
              ] },
              { title: "Стоимость", rows: [
                { key: "mode", name: "Стоимость доставки вводится", filled: true,
                  value: <select value={batch.d2d ? "single" : "split"} onChange={(e) => setBatch({ ...batch, d2d: e.target.value === "single" })}>
                    <option value="single">единым полем: завод → клиент</option><option value="split">по участкам: до границы и по РФ</option></select>, comment: bcc("delivery_mode") },
                ...(batch.d2d ? [
                  { key: "d2d", name: <b>Доставка от завода до клиента</b>, unit: <select value={batch.d2d_currency} onChange={(e) => setBatch({ ...batch, d2d_currency: e.target.value })}>{["RUB", "USD", "EUR", "CNY"].map((c) => <option key={c} value={c}>{c === "RUB" ? "руб" : c}</option>)}</select>, filled: batch.d2d_amount > 0, required: true,
                    ref: <span className="muted">до границы РФ {borderPct} % = {fmtRub(batch.d2d_amount * borderPct / 100)}; от границы до клиента {100 - borderPct} % = {fmtRub(batch.d2d_amount * (100 - borderPct) / 100)} {batch.d2d_currency === "RUB" ? "руб" : batch.d2d_currency}</span>,
                    value: <input type="number" value={batch.d2d_amount} onChange={(e) => setBatch({ ...batch, d2d_amount: Number(e.target.value) })} />,
                    comment: <input value={batch.comment_freight} placeholder="комментарий" onChange={(e) => setBatch({ ...batch, comment_freight: e.target.value })} /> },
                ] : [
                { key: "fr", name: "Доставка от завода до границы РФ", unit: <select value={batch.intl_currency} onChange={(e) => setBatch({ ...batch, intl_currency: e.target.value })}><option value="RUB">руб</option><option>USD</option><option>EUR</option><option>CNY</option></select>,
                  ref: <span className="muted">при отклонении от истории — комментарий</span>, filled: batch.intl_freight > 0, required: true,
                  value: <input type="number" value={batch.intl_freight} onChange={(e) => setBatch({ ...batch, intl_freight: Number(e.target.value) })} />,
                  comment: <input value={batch.comment_freight} placeholder="комментарий" onChange={(e) => setBatch({ ...batch, comment_freight: e.target.value })} /> },
                ]),
                { key: "ins", name: "Страхование", unit: "% от стоимости", filled: batch.insurance_pct > 0, value: <input type="number" step="0.01" value={batch.insurance_pct} onChange={(e) => setBatch({ ...batch, insurance_pct: Number(e.target.value) })} />, comment: bcc("insurance_pct") },
                { key: "br", name: "Брокер и СВХ", unit: "RUB", filled: batch.broker_rub > 0, value: <input type="number" value={batch.broker_rub} onChange={(e) => setBatch({ ...batch, broker_rub: Number(e.target.value) })} />,
                  comment: <input value={batch.comment_broker} placeholder="комментарий" onChange={(e) => setBatch({ ...batch, comment_broker: e.target.value })} /> },
                ...(batch.d2d ? [] : [
                { key: "rf", name: "Доставка по РФ до объекта", unit: "RUB", filled: batch.rf_delivery_rub > 0, value: <input type="number" value={batch.rf_delivery_rub} onChange={(e) => setBatch({ ...batch, rf_delivery_rub: Number(e.target.value) })} />,
                  comment: <input value={batch.comment_rf} placeholder="комментарий" onChange={(e) => setBatch({ ...batch, comment_rf: e.target.value })} /> },
                ]),
              ] },
            ]} />
            <div className="row" style={{ marginTop: 8 }}><button className="btn secondary" onClick={createBatch}>Создать партию (нераспределённые позиции)</button><UndoButton h={history} /></div>
          </>
        )}
      </div>

      {positions.map((p) => {
        const f = forms[p.id];
        if (!f) return null;
        // проверка по истории (сохраняется при записи позиции): среднее и отклонение — рядом со статьёй
        const checks = ((p.logistics || {}) as { checks?: Record<string, { history_avg?: number; deviation_pct?: number; needs_comment?: boolean; n?: number }> }).checks || {};
        const ci = (k: string, ph = "комментарий") => <input placeholder={ph} disabled={!canEdit} value={f.comments[k] || ""} onChange={(e) => upd(p.id, { comments: { ...f.comments, [k]: e.target.value } })} />;
        const ur = uref[p.id];
        const hc = ((p.logistics || {}) as { hs_check?: { found?: boolean; mismatch?: boolean; ref_duty_pct?: number; ref_vat_pct?: number; certification?: string[] } }).hs_check;
        const hist = (k: string) => { const c = checks[k]; if (!c || c.history_avg == null) return <span className="muted">—</span>;
          return <span className={c.needs_comment ? "badge warn" : "muted"} style={{ fontSize: 12 }}>ср. {fmtRub(c.history_avg)}{c.deviation_pct != null ? `, ${c.deviation_pct > 0 ? "+" : ""}${c.deviation_pct} %` : ""}</span>; };
        return (
          <div className="card" key={p.id}>
            <h2 style={{ marginTop: 0 }}>{p.machine_type} {designation(p)} × {p.qty} <span className="muted">· технических характеристик заполнено: {(p.tech_param_rows || []).filter((r) => r.value_ru || r.value_zh).length}</span></h2>
            <details style={{ marginBottom: 10 }} open><summary className="muted" style={{ cursor: "pointer" }}>Технические характеристики позиции</summary>
              <div style={{ marginTop: 6 }}><TechParamsView rows={p.tech_param_rows || []} /></div></details>
            <SpecTable refTitle="По истории / справочник" nameTitle="Статья" sections={[
              { title: "Китай: до границы", rows: [
                d2dCovers(p) ? { key: "dl", name: "Внутрикитайская логистика завод → граница", filled: true,
                  ref: <span className="muted">входит в единую стоимость доставки завод → клиент</span>, value: <span className="muted">не заполняется</span> }
                : { key: "dl", name: "Внутрикитайская логистика завод → граница", unit: "CNY", ref: hist("domestic_logistics"), filled: f.domestic_logistics.amount > 0, required: true,
                  value: <input type="number" value={f.domestic_logistics.amount} disabled={!canEdit} onChange={(e) => upd(p.id, { domestic_logistics: { ...f.domestic_logistics, amount: Number(e.target.value) } })} />,
                  comment: ci("domestic_logistics", "при отклонении от истории") },
                { key: "ef", name: "Экспортные формальности КНР", unit: "CNY", ref: hist("export_formalities"), filled: f.export_formalities.amount > 0,
                  value: <input type="number" value={f.export_formalities.amount} disabled={!canEdit} onChange={(e) => upd(p.id, { export_formalities: { ...f.export_formalities, amount: Number(e.target.value) } })} />,
                  comment: ci("export_formalities", "при отклонении от истории") },
                { key: "vol", name: "Объём", unit: "м³", filled: f.volume_m3 !== "",
                  value: <input type="number" value={f.volume_m3} disabled={!canEdit} onChange={(e) => upd(p.id, { volume_m3: e.target.value })} />, comment: ci("volume_m3") },
              ] },
              { title: "Таможня РФ", rows: [
                { key: "hs", name: <>Код ТН ВЭД <span className="muted">(поиск по коду или описанию; можно ввести вручную)</span></>, filled: !!f.hs_code, required: true,
                  ref: <span className="muted">{hc?.found === false ? "кода нет в справочнике" : hc?.certification?.length ? hc.certification.join(", ") : ""}</span>,
                  value: <>
                    <input value={f.hs_code} disabled={!canEdit} placeholder="10 цифр" onChange={(e) => { const v = e.target.value; upd(p.id, { hs_code: v }); if (v.length >= 2) api.get<typeof hs>(`/api/refs/hs?q=${encodeURIComponent(v)}`).then(setHs); if (/^\d{10}$/.test(v.replace(/\s/g, ""))) lookupHs(p.id, v); }} />
                    {canEdit && hs.length > 0 && <div style={{ fontSize: 12, marginTop: 2 }}>{hs.slice(0, 5).map((h) => <div key={h.code} style={{ cursor: "pointer", padding: "2px 0" }} onClick={() => { upd(p.id, { hs_code: h.code, duty_pct: String(h.duty_pct), vat_pct: String(h.vat_pct), util_applies: null, util_coefficient: "" }); setHs([]); loadUref(p.id, h.code); }}><b>{h.code}</b> — {h.description} (пошлина {h.duty_pct} %, НДС {h.vat_pct} %; {h.certification.join(", ")})</div>)}</div>}</>,
                  comment: ci("hs_code") },
                ...(/^\d{10}$/.test(f.hs_code.replace(/\s/g, "")) && hsInfo[p.id]?.code === f.hs_code.replace(/\s/g, "") && hsInfo[p.id]?.source !== "reference" ? [
                  { key: "hsn", name: "Наименование кода ТН ВЭД", filled: !!f.hs_description, required: true,
                    ref: <span className="muted">{hsInfo[p.id]?.error || hsInfo[p.id]?.note || (hsInfo[p.id]?.source === "internet" ? "найдено в интернете — проверьте" : "")}
                      {(hsInfo[p.id]?.links || []).map((l) => <> · <a key={l.url} href={l.url} target="_blank" rel="noreferrer">{l.title}</a></>)}</span>,
                    value: <input value={f.hs_description} placeholder="наименование по ТН ВЭД ЕАЭС" disabled={!canEdit} onChange={(e) => upd(p.id, { hs_description: e.target.value })} />,
                    comment: <span className="muted" style={{ fontSize: 11 }}>сохранится в справочник ТН ВЭД</span> },
                ] : []),
                { key: "duty", name: "Пошлина", unit: "%", filled: f.duty_pct !== "",
                  ref: hc?.found ? <span className={hc.mismatch ? "badge warn" : "muted"}>справочник: {hc.ref_duty_pct} %</span> : null,
                  value: <input type="number" value={f.duty_pct} disabled={!canEdit} onChange={(e) => upd(p.id, { duty_pct: e.target.value })} />,
                  comment: <input placeholder="обязателен при ручной ставке" disabled={!canEdit} value={f.hs_manual_comment} onChange={(e) => upd(p.id, { hs_manual_comment: e.target.value })} /> },
                { key: "vat", name: "НДС при ввозе", unit: "%", filled: f.vat_pct !== "",
                  ref: hc?.found ? <span className={hc.mismatch ? "badge warn" : "muted"}>справочник: {hc.ref_vat_pct} %</span> : null,
                  value: <input type="number" value={f.vat_pct} disabled={!canEdit} onChange={(e) => upd(p.id, { vat_pct: e.target.value })} />, comment: ci("vat_pct") },
                { key: "cert", name: "Сертификация (ТР ТС 010/2011, ЭПБ, ПСМ)", unit: "RUB", ref: hist("certification_rub"), filled: f.certification_rub > 0,
                  value: <input type="number" value={f.certification_rub} disabled={!canEdit} onChange={(e) => upd(p.id, { certification_rub: Number(e.target.value) })} />,
                  comment: ci("certification_rub", "при отклонении от истории") },
              ] },
              { title: "Утилизационный сбор", rows: [
                { key: "ua", name: "Начисляется", filled: true,
                  ref: <span className="muted">{ur?.applies_by_hs == null ? (f.hs_code ? "кода нет в справочнике" : "укажите код ТН ВЭД") : `по ТН ВЭД: ${ur.applies_by_hs ? "начисляется" : "не начисляется"}`}</span>,
                  value: <label className="row"><input type="checkbox" checked={utilOn(p.id)} disabled={!canEdit} onChange={(e) => upd(p.id, { util_applies: e.target.checked })} /> {utilOn(p.id) ? "да" : "нет"}</label>,
                  comment: ci("util_applies") },
                ...(utilOn(p.id) ? [
                  { key: "ub", name: "Базовая ставка", unit: "RUB", filled: true, ref: <span className="muted">{ur ? `по настройке: ${fmtRub(ur.base_rate_rub)}` : ""}</span>,
                    value: <input inputMode="decimal" value={f.util_base_rate_rub} placeholder={ur ? String(ur.base_rate_rub) : ""} disabled={!canEdit} onChange={(e) => upd(p.id, { util_base_rate_rub: e.target.value })} />,
                    comment: ci("util_base_rate_rub") },
                  { key: "uc", name: <>Коэффициент <span className="muted">(вид техники и код ТН ВЭД)</span></>, unit: "×", filled: utilCoef(p.id) != null, required: true,
                    ref: <span className="muted">{ur?.rule ? `по настройке: ${ur.coefficient} (${[ur.rule.hs_prefix, ur.rule.machine_type].filter(Boolean).join(", ")}${ur.rule.comment ? "; " + ur.rule.comment : ""})` : "в настройках нет — введите"}</span>,
                    value: <input inputMode="decimal" value={f.util_coefficient} placeholder={ur?.coefficient != null ? String(ur.coefficient) : "введите"} disabled={!canEdit} onChange={(e) => upd(p.id, { util_coefficient: e.target.value })} />,
                    comment: ci("util_coefficient") },
                  { key: "us", name: <><b>Утилизационный сбор</b> <span className="muted">= ставка × коэффициент</span></>, unit: "RUB", filled: utilCoef(p.id) != null,
                    value: <b>{utilCoef(p.id) == null ? "—" : fmtRub(utilSum(p.id))}</b>, comment: ci("util_fee_rub") },
                ] : []),
              ] },
            ]} />
            {canEdit && <div className="row" style={{ marginTop: 8 }}><button className="btn secondary" onClick={() => savePos(p.id)}>Сохранить позицию</button><UndoButton h={history} /><span className="muted">партия: {batches.find((b) => b.id === p.batch_id)?.name || "не распределена"}</span></div>}
          </div>
        );
      })}
      {canEdit && <div className="row"><button className="btn" onClick={complete}>Отправить далее на расчёт</button><span className="muted">все позиции без партии войдут в первую партию</span></div>}
    </>
  );
}
