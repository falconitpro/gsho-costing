import { useEffect, useState } from "react";
import { api, ApiError, fmtDate, fmtRub, designation } from "../api";
import { STATUS_TITLES, type Batch, type Deal, type Me } from "../types";
import LogistRequests, { LogistAddresses, LogistFields, FieldsChooser, type Workspace, type Quote } from "../components/LogistWorkspace";
import SpecTable from "../components/SpecTable";
import { UndoButton, useUndoHistory } from "../undo";
import { useQueryParam, useUiMode } from "../components/Levels";
import { askConfirm } from "../components/Dialogs";

type Money = { amount: number; currency: string };
type PosForm = { domestic_logistics: Money; export_formalities: Money; hs_code: string; duty_pct: string; vat_pct: string; hs_manual_comment: string; hs_description: string; util_fee_rub: number; certification_rub: number; cert_vat: boolean; volume_m3: string; comments: Record<string, string>;
  util_applies: boolean | null; util_base_rate_rub: string; util_coefficient: string };
type UtilRef = { base_rate_rub: number; coefficient: number | null; rule: { hs_prefix?: string; machine_type?: string; comment?: string } | null; hs_found: boolean; applies_by_hs: boolean | null };

/** Блок логиста (раздел 5): партии, статьи по позициям, ТН ВЭД, проверка по истории с комментарием. */
export default function LogisticsBlock({ deal, me, reload }: { deal: Deal; me: Me; reload: () => void }) {
  const hasRole = me.roles.some((r) => r.code === "logistician" || r.code === "gd");
  const atStage = ["logistics_calc", "service_calc", "rework"].includes(deal.status);
  const canEdit = hasRole && atStage;
  // почему блок только для чтения — словами, с тем, что сделать (D-072)
  const lockReason = canEdit ? "" : !atStage
    ? deal.status === "draft"
      ? "Сделка ещё в черновике. Блок логистики открывается, когда менеджер нажмёт «Отправить на расчёт» (вкладка «Позиции»): для этого в позициях должны быть заполнены поля «перед расчётом» — по умолчанию завод, модель, количество, цена и валюта."
      : `Сделка на этапе «${STATUS_TITLES[deal.status] || deal.status}» — логистика уже передана дальше. Чтобы поправить, КД или ЗГД возвращает сделку на доработку («Вернуть на доработку»), после этого блок снова открыт.`
    : "Заполнять логистику могут логист и ЗГД. У вашей учётной записи этих ролей нет — войдите под нужной учётной записью или попросите администратора добавить роль.";
  const [err, setErr] = useState("");
  // рабочее место логиста (D-073): адреса, выбранные характеристики, запрос перевозчикам, ответы
  const [ws, setWs] = useState<Workspace | null>(null);
  const [chooser, setChooser] = useState(false);
  const loadWs = () => api.get<Workspace>(`/api/deals/${deal.id}/logistics/workspace`).then(setWs).catch(() => setWs(null));
  useEffect(() => { loadWs(); }, [deal.id]);
  const [hs, setHs] = useState<{ code: string; description: string; duty_pct: number; vat_pct: number; certification: string[] }[]>([]);
  const batches = deal.batches || [];
  const positions = deal.positions || [];
  // читаемость (D-075): форма новой партии и заполненные позиции свёрнуты, сверху — сводка со ссылками
  const [showBatchForm, setShowBatchForm] = useState(batches.length === 0);
  const savedHs = (p: { logistics?: unknown }) => !!((p.logistics || {}) as { hs_code?: string }).hs_code;
  const [openPos, setOpenPos] = useState<string[]>(() => positions.filter((p) => !savedHs(p)).map((p) => p.id));
  const emptyBatch = () => ({ name: `Партия ${batches.length + 1}`, route: "завод → Маньчжурия → Забайкальск → пункт поставки", transport_units: 1, transit_days: 14, customs_days: 5, rf_delivery_days: 5, border_planned: "", allocation: "cost",
    intl_freight: 0, intl_currency: "CNY", insurance_pct: 0.3, broker_rub: 0, rf_delivery_rub: 0, broker_vat: false, rf_vat: false, comment_freight: "", comment_broker: "", comment_rf: "", d2d: true, d2d_amount: 0, d2d_currency: "CNY" });
  // правка сохранённой партии: значения — в ту же форму, сохранение обновляет партию
  // D-087: суммы партии — на 1 машину; прежние партии (без признака) хранили итог на партию — в форме делим на число машин
  const batchUnits = (ids: string[]) => positions.filter((p) => ids.includes(p.id)).reduce((a, p) => a + Math.max(1, p.qty || 1), 0) || 1;
  const perUnitItems = (b: Batch) => {
    const raw = (b.items || {}) as Record<string, unknown>;
    const dv = raw.per_unit ? 1 : batchUnits(positions.filter((p) => p.batch_id === b.id).map((p) => p.id));
    const dm = (m: unknown) => (m && typeof m === "object" ? { ...(m as Money), amount: Math.round(((m as Money).amount || 0) / dv * 100) / 100 } : m);
    return { ...raw, intl_freight: dm(raw.intl_freight), door_to_door: dm(raw.door_to_door), insurance_amount: dm(raw.insurance_amount),
      broker_rub: Number(raw.broker_rub || 0) / dv, rf_delivery_rub: Number(raw.rf_delivery_rub || 0) / dv } as Record<string, unknown>;
  };
  const batchForm = (b: Batch) => {
    const it = perUnitItems(b);
    const fr = it.intl_freight as Money | undefined; const d = it.door_to_door as Money | null | undefined;
    const cm = (it.comments || {}) as Record<string, string>;
    return { name: b.name, route: b.route, transport_units: b.transport_units, transit_days: b.transit_days, customs_days: b.customs_days, rf_delivery_days: b.rf_delivery_days ?? 5,
      border_planned: b.border_planned || "", allocation: b.allocation || "cost", intl_freight: fr?.amount || 0, intl_currency: fr?.currency || "CNY",
      insurance_pct: (it.insurance_pct as number | null) ?? 0, broker_rub: Number(it.broker_rub || 0), rf_delivery_rub: Number(it.rf_delivery_rub || 0),
      broker_vat: !!(it.vat_included as Record<string, boolean> | undefined)?.broker_rub, rf_vat: !!(it.vat_included as Record<string, boolean> | undefined)?.rf_delivery_rub,
      comment_freight: cm.intl_freight || cm.door_to_door || "", comment_broker: cm.broker_rub || "", comment_rf: cm.rf_delivery_rub || "",
      d2d: !!d?.amount || !(fr?.amount || it.rf_delivery_rub), d2d_amount: d?.amount || 0, d2d_currency: d?.currency || "CNY" };
  };
  const [editId, setEditId] = useState<string | null>(null);
  const [batch, setBatch] = useState(emptyBatch);
  const [batchSaved, setBatchSaved] = useState(() => JSON.stringify(emptyBatch()));
  const freePositions = () => positions.filter((p) => !p.batch_id).map((p) => p.id);
  const [posSel, setPosSel] = useState<string[]>(() => (batches.length ? freePositions() : positions.map((p) => p.id)));
  const [borderPct, setBorderPct] = useState(10);   // единая стоимость доставки: доля до границы РФ (настройка КД / ЗГД)
  useEffect(() => { api.get<{ door_to_door_border_pct: number }>(`/api/deals/${deal.id}/logistics/defaults`).then((r) => setBorderPct(r.door_to_door_border_pct)).catch(() => undefined); }, [deal.id]);
  const [bc, setBc] = useState<Record<string, string>>({});   // комментарии к остальным полям партии
  const startEdit = (b: Batch) => {
    const f = batchForm(b); setEditId(b.id); setBatch(f); setBatchSaved(JSON.stringify(f)); setShowBatchForm(true);
    setBc(Object.fromEntries(Object.entries(((b.items || {}) as { comments?: Record<string, string> }).comments || {}).filter(([k]) => !["intl_freight", "door_to_door", "broker_rub", "rf_delivery_rub"].includes(k))));
    setPosSel(positions.filter((p) => p.batch_id === b.id).map((p) => p.id));
  };
  const resetBatchForm = () => { const f = emptyBatch(); setEditId(null); setBatch(f); setBatchSaved(JSON.stringify(f)); setBc({}); setPosSel(freePositions()); };
  // ответ логистической компании → в форму партии (сохранение — обычной кнопкой партии)
  const applyQuote = (q0: Quote) => {
    const nU = batchUnits(posSel), q = { ...q0, amount: Math.round(q0.amount / nU * 100) / 100 };   // ответ — на весь груз, в партию — на 1 машину
    const c = `${q.carrier}${q.comment ? ": " + q.comment : ""}${nU > 1 ? ` (${fmtRub(q0.amount)} ${q0.currency} на ${nU} шт.)` : ""}`;
    if (q.scope === "door_to_door") setBatch((b) => ({ ...b, d2d: true, d2d_amount: q.amount, d2d_currency: q.currency, comment_freight: c, transit_days: q.transit_days || b.transit_days }));
    else if (q.scope === "intl_freight") setBatch((b) => ({ ...b, d2d: false, intl_freight: q.amount, intl_currency: q.currency, comment_freight: c, transit_days: q.transit_days || b.transit_days }));
    else if (q.scope === "customs") setBatch((b) => ({ ...b, broker_rub: q.amount, comment_broker: c }));
    else if (q.scope === "rf_delivery") setBatch((b) => ({ ...b, d2d: false, rf_delivery_rub: q.amount, comment_rf: c, rf_delivery_days: q.transit_days || b.rf_delivery_days }));
  };
  // выбранное предложение логистической компании → в форму партии: единая ставка или участки (страхование — % в партии, вручную)
  const applyChosen = (lines: Quote[]) => {
    const d2d = lines.find((q) => q.scope === "door_to_door");
    (d2d ? [d2d, ...lines.filter((q) => q.scope === "customs")] : lines).forEach(applyQuote);
    setErr(""); setShowBatchForm(true); setTimeout(() => document.getElementById("batch-form")?.scrollIntoView({ behavior: "smooth" }), 50);
  };
  const batchCost = batch.d2d ? batch.d2d_amount : batch.intl_freight + batch.rf_delivery_rub + batch.broker_rub;
  const batchDirty = JSON.stringify(batch) !== batchSaved;
  const bcc = (k: string) => <input value={bc[k] || ""} placeholder="комментарий" onChange={(e) => setBc({ ...bc, [k]: e.target.value })} />;
  const [forms, setForms] = useState<Record<string, PosForm>>(() => Object.fromEntries(positions.map((p) => {
    const l0 = (p.logistics || {}) as Partial<PosForm> & { per_unit?: boolean };
    // D-087: расходы позиции — на 1 машину; прежние записи (без признака) хранили сумму на всю позицию — в форме делим на количество
    const dq = l0.per_unit ? 1 : Math.max(1, p.qty || 1);
    const dmo = (m?: Money) => (m ? { ...m, amount: Math.round((m.amount || 0) / dq * 100) / 100 } : m);
    const l = { ...l0, domestic_logistics: dmo(l0.domestic_logistics as Money), export_formalities: dmo(l0.export_formalities as Money),
      certification_rub: (l0.certification_rub || 0) / dq } as Partial<PosForm>;
    const cat = (p as { catalog_hs?: { code: string; description?: string; duty_pct?: number | null; vat_pct?: number | null } }).catalog_hs;   // код из карточки оборудования каталога
    return [p.id, { domestic_logistics: (l.domestic_logistics as Money) || { amount: 0, currency: "CNY" }, export_formalities: (l.export_formalities as Money) || { amount: 0, currency: "CNY" },
      hs_code: l.hs_code || cat?.code || "", duty_pct: l.duty_pct != null ? String(l.duty_pct) : cat?.duty_pct != null ? String(cat.duty_pct) : "",
      vat_pct: l.vat_pct != null ? String(l.vat_pct) : cat?.vat_pct != null ? String(cat.vat_pct) : "22", hs_manual_comment: "",
      hs_description: (l as { hs_description?: string }).hs_description || cat?.description || "",
      util_fee_rub: l.util_fee_rub || 0, certification_rub: l.certification_rub || 0, cert_vat: !!((l0 as { vat_included?: Record<string, boolean> }).vat_included?.certification_rub), volume_m3: l.volume_m3 != null ? String(l.volume_m3) : "", comments: (l.comments as Record<string, string>) || {},
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

  async function saveBatch(silent = false): Promise<boolean> {
    if (!posSel.length && !silent && !(await askConfirm("В партии нет позиций — её расходы не попадут в себестоимость. Сохранить всё равно?"))) return false;
    try {
      const body = { name: batch.name, route: batch.route, transport_units: batch.transport_units, transit_days: batch.transit_days, customs_days: batch.customs_days,
        rf_delivery_days: batch.rf_delivery_days, border_planned: batch.border_planned || null, allocation: batch.allocation,
        items: { intl_freight: { amount: batch.d2d ? 0 : batch.intl_freight, currency: batch.intl_currency }, insurance_pct: batch.insurance_pct, broker_rub: batch.broker_rub,
          rf_delivery_rub: batch.d2d ? 0 : batch.rf_delivery_rub, door_to_door: batch.d2d ? { amount: batch.d2d_amount, currency: batch.d2d_currency } : null,
          comments: { ...bc, intl_freight: batch.comment_freight, door_to_door: batch.comment_freight, broker_rub: batch.comment_broker, rf_delivery_rub: batch.comment_rf }, per_unit: true,
          vat_included: { broker_rub: batch.broker_vat, rf_delivery_rub: batch.rf_vat } },
        position_ids: posSel };
      if (editId) await api.patch(`/api/deals/${deal.id}/logistics/batches/${editId}`, body);
      else await api.post(`/api/deals/${deal.id}/logistics/batches`, body);
      setBatchSaved(JSON.stringify(batch)); setErr("");
      if (!silent) { setEditId(null); reload(); }
      return true;
    } catch (e) { onErr(e); return false; }
  }
  async function deleteBatch(b: Batch) {
    if (!(await askConfirm(`Удалить партию «${b.name}»? Её позиции станут нераспределёнными.`))) return;
    try { await api.del(`/api/deals/${deal.id}/logistics/batches/${b.id}`); if (editId === b.id) resetBatchForm(); setErr(""); reload(); } catch (e) { onErr(e); }
  }
  async function savePos(pid: string, silent = false): Promise<boolean> {
    const f = forms[pid];
    try {
      await api.put(`/api/deals/${deal.id}/logistics/positions/${pid}`, { ...f, duty_pct: f.duty_pct === "" ? null : Number(f.duty_pct), vat_pct: f.vat_pct === "" ? null : Number(f.vat_pct), volume_m3: f.volume_m3 === "" ? null : Number(f.volume_m3), vat_included: { certification_rub: f.cert_vat },
        util_applies: utilOn(pid), util_base_rate_rub: utilBase(pid), util_coefficient: utilCoef(pid), util_fee_rub: utilOn(pid) ? utilSum(pid) : 0 });
      setDirtyPos((d) => d.filter((x) => x !== pid)); setErr("");
      if (!silent) reload();
      return true;
    } catch (e) { onErr(e); return false; }
  }
  // «Отправить далее»: сначала сохраняем то, что введено, но не сохранено (партия и позиции), иначе в расчёт уйдут нули
  async function complete(noDeliveryCost = false) {
    if (batchDirty && batchCost > 0 && !(await saveBatch(true))) return;
    for (const pid of dirtyPos) if (!(await savePos(pid, true))) return;
    try { await api.post(`/api/deals/${deal.id}/logistics/complete`, { no_delivery_cost: noDeliveryCost }); setErr(""); reload(); }
    catch (e) {
      const d = (e as ApiError).detail as { message?: string; confirm?: string } | undefined;
      if (d?.confirm === "no_delivery_cost" && (await askConfirm(`${d.message}\n\nОтправить на расчёт без стоимости доставки?`))) return complete(true);
      onErr(e); reload();
    }
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
    if (r.description) setDirtyPos((d) => (d.includes(pid) ? d : [...d, pid]));
    if (r.description) setForms((fs) => ({ ...fs, [pid]: { ...fs[pid], hs_code: r.code, hs_description: fs[pid].hs_description || r.description,
      ...(r.source === "reference" ? { duty_pct: String(r.duty_pct), vat_pct: String(r.vat_pct) } : {}) } }));
    loadUref(pid, r.code);
  }).catch(() => undefined);
  const [dirtyPos, setDirtyPos] = useState<string[]>([]);   // позиции с несохранёнными изменениями
  const upd = (pid: string, patch: Partial<PosForm>) => { setForms({ ...forms, [pid]: { ...forms[pid], ...patch } }); setDirtyPos((d) => (d.includes(pid) ? d : [...d, pid])); };

  // Аудит 19.11.2026: «Новый вид» — мастер в три шага (в адресе ?lstep=1..3): 1 — адреса и маршрут, 2 — перевозка (партии и логистические
  // компании), 3 — таможня и сборы по позициям. Шаги не размонтируются, а скрываются: введённое, но не сохранённое не теряется.
  // «Все шаги на одной странице» и «Прежний вид» — как раньше.
  const [uiMode] = useUiMode();
  const [lstep, setLstep] = useQueryParam("lstep");
  const wizard = uiMode !== "classic" && lstep !== "all";
  const step = wizard ? (["1", "2", "3"].includes(lstep || "") ? Number(lstep) : 1) : 0;
  const show = (n: number) => !wizard || step === n;
  const stepDone = [!!ws?.addresses.delivery_address, batches.length > 0 && ws?.compare.chosen_status === "approved", positions.length > 0 && positions.every(savedHs)];
  const STEPS = ["Адреса и маршрут", "Перевозка: партии и логистические компании", "Таможня и сборы по позициям"];
  const go = (n: number) => { setLstep(String(n)); window.scrollTo({ top: 0 }); };
  const stepNav = wizard && (
    <div className="row" style={{ justifyContent: "space-between", margin: "4px 0 16px", gap: 8, flexWrap: "wrap" }}>
      {step > 1 ? <button className="btn ghost" onClick={() => go(step - 1)}>← {STEPS[step - 2]}</button> : <span />}
      {step < 3 ? <button className="btn secondary" onClick={() => go(step + 1)}>Далее: {STEPS[step]} →</button>
        : canEdit && !deal.logistics_done ? <button className="btn" onClick={() => complete()}>Отправить далее на расчёт</button> : <span />}
    </div>);
  return (
    <>
      {lockReason && <div className="alert warn" role="status" style={{ marginBottom: 10 }}><b>Блок логистики только для просмотра.</b> {lockReason}</div>}
      {err && <div className="alert error">{err}</div>}
      {chooser && <FieldsChooser dealId={deal.id} onClose={() => { setChooser(false); loadWs(); }} />}
      <div className="card lb-sum">
        <div className="row" style={{ justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
          <span>Статус: <span className="badge accent">{STATUS_TITLES[deal.status] || deal.status}</span> · логистика: {deal.logistics_done ? <span className="badge ok">рассчитана, передана далее</span> : <span className="badge warn">в работе</span>}</span>
          {canEdit && !deal.logistics_done && <button className="btn" onClick={() => complete()}>Отправить далее на расчёт</button>}
        </div>
        {wizard && <div className="lb-wiz" role="tablist" aria-label="Шаги логистики">
          {STEPS.map((t, i) => (
            <button key={t} type="button" role="tab" aria-selected={step === i + 1} className={`${step === i + 1 ? "on" : ""} ${stepDone[i] ? "done" : ""}`} onClick={() => go(i + 1)}>
              <span className="n">{stepDone[i] ? "✓" : i + 1}</span>{t}</button>))}
          <span className="lb-links muted" style={{ fontSize: 12, alignSelf: "center" }}>
            <a href="#" onClick={(e) => { e.preventDefault(); setLstep("all"); }}>все шаги на одной странице</a> · <a href="?tab=positions">все характеристики позиций</a> · <a href="?tab=offers">ответы заводов</a></span>
        </div>}
        {!wizard && uiMode !== "classic" && <div style={{ marginTop: 6 }}><a href="#" className="muted" style={{ fontSize: 12 }} onClick={(e) => { e.preventDefault(); setLstep("1"); }}>по шагам</a></div>}
        {!wizard && <div className="lb-steps">
          <a href="#lb-adr" className={ws?.addresses.delivery_address ? "done" : ""}>Адреса</a>
          <a href="#lb-batches" className={batches.length ? "done" : ""}>Партии: {batches.length || "нет"}</a>
          <a href="#lb-pos" className={positions.every(savedHs) ? "done" : ""}>Позиции, ТН ВЭД: {positions.filter(savedHs).length} из {positions.length}</a>
          <a href="#lb-req" className={ws?.compare.chosen_status === "approved" ? "done" : ""}>Логистические компании: {ws?.compare.rows.length ? `ответов ${ws.compare.rows.length}` : "запросов " + (ws?.requests.length || 0)}{ws?.compare.chosen ? ` · выбрано: ${ws.compare.chosen}` : ""}</a>
          <span className="lb-links muted">· <a href="?tab=positions">все характеристики позиций</a> · <a href="?tab=offers">ответы заводов</a></span>
        </div>}
      </div>
      <div hidden={!show(1)}>{ws && <div id="lb-adr"><LogistAddresses dealId={deal.id} ws={ws} reload={loadWs} /></div>}{show(1) && stepNav}</div>
      <div hidden={!show(2)}>
      <div className="card" id="lb-batches">
        <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}><h2 style={{ margin: 0 }}>Доставка: партии <span className="muted" style={{ fontSize: 13, fontWeight: 400 }}>· суммы на 1 машину</span></h2>
          {canEdit && !showBatchForm && <button className="btn secondary" onClick={() => { resetBatchForm(); setShowBatchForm(true); }}>+ Новая партия</button>}</div>
        {batches.length > 0 && <table><thead><tr><th>Партия</th><th>Маршрут</th><th className="num">Трансп. ед.</th><th className="num">Транзит / оформление / по РФ, дн</th><th>CMR план / факт</th><th>Доставка до границы РФ</th><th>Страховка</th><th className="num">Брокер и СВХ</th><th className="num">Доставка по РФ</th><th>Позиции</th>{canEdit && <th />}</tr></thead>
          <tbody>{batches.map((b) => { const it = perUnitItems(b); const fr = it.intl_freight as Money | undefined; const d2d = it.door_to_door as Money | null | undefined; return (
            <tr key={b.id}><td>{b.name}</td><td>{b.route}</td><td className="num">{b.transport_units}</td><td className="num">{b.transit_days} / {b.customs_days} / {b.rf_delivery_days ?? 5}</td><td>{fmtDate(b.border_planned)} / {b.border_actual ? <b>{fmtDate(b.border_actual)}</b> : "—"}</td>
              {d2d?.amount ? <td colSpan={1} title="единая стоимость завод → клиент">{fmtRub(d2d.amount * borderPct / 100)} {d2d.currency} <span className="muted">({borderPct} % от {fmtRub(d2d.amount)})</span></td>
                : <td>{fr ? `${fmtRub(fr.amount)} ${fr.currency}` : "—"}</td>}
              <td>{it.insurance_pct != null ? `${it.insurance_pct} %` : "—"}</td><td className="num">{fmtRub(it.broker_rub as number)}{(it.vat_included as Record<string, boolean> | undefined)?.broker_rub && <div className="note-xs">с НДС</div>}</td>
              <td className="num">{d2d?.amount ? <>{fmtRub(d2d.amount * (100 - borderPct) / 100)} {d2d.currency} <span className="muted">({100 - borderPct} %)</span></> : <>{fmtRub(it.rf_delivery_rub as number)}{(it.vat_included as Record<string, boolean> | undefined)?.rf_delivery_rub && <div className="note-xs">с НДС</div>}</>}</td>
              <td>{positions.filter((p) => p.batch_id === b.id).map((p) => designation(p)).join(", ") || <span className="badge warn">нет позиций — расходы не учитываются</span>}</td>
              {canEdit && <td><button className="btn ghost" onClick={() => startEdit(b)}>изменить</button> <button className="btn ghost" onClick={() => deleteBatch(b)}>удалить</button></td>}</tr>); })}</tbody></table>}
        {batches.length === 0 && <p className="muted">Партий пока нет.</p>}
        {canEdit && showBatchForm && (
          <>
            <h3 id="batch-form" style={{ marginBottom: 4 }}>{editId ? `Изменение партии «${batches.find((b) => b.id === editId)?.name || batch.name}»` : "Новая партия"}</h3>
            {(() => { const nU = batchUnits(posSel); const tot = batch.d2d ? batch.d2d_amount : batch.intl_freight; return (
              <div className="alert info" style={{ margin: "4px 0 8px" }}>Суммы — <b>на 1 машину</b>{nU > 1 ? <>; в партии {nU} шт. — в расчёт × {nU}: {batch.d2d ? "доставка" : "перевозка"} {fmtRub(tot * nU)} {batch.d2d ? batch.d2d_currency : batch.intl_currency}, брокер {fmtRub(batch.broker_rub * nU)} руб{batch.d2d ? "" : `, по РФ ${fmtRub(batch.rf_delivery_rub * nU)} руб`}</> : ""}. Ответ логистической компании на весь груз при «в партию» делится на число машин.</div>); })()}
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
                { key: "br", name: "Брокер и СВХ", unit: <VatUnit v={batch.broker_vat} on={(x) => setBatch({ ...batch, broker_vat: x })} />, filled: batch.broker_rub > 0, value: <input type="number" value={batch.broker_rub} onChange={(e) => setBatch({ ...batch, broker_rub: Number(e.target.value) })} />,
                  comment: <input value={batch.comment_broker} placeholder="комментарий" onChange={(e) => setBatch({ ...batch, comment_broker: e.target.value })} /> },
                ...(batch.d2d ? [] : [
                { key: "rf", name: "Доставка по РФ до объекта", unit: <VatUnit v={batch.rf_vat} on={(x) => setBatch({ ...batch, rf_vat: x })} />, filled: batch.rf_delivery_rub > 0, value: <input type="number" value={batch.rf_delivery_rub} onChange={(e) => setBatch({ ...batch, rf_delivery_rub: Number(e.target.value) })} />,
                  comment: <input value={batch.comment_rf} placeholder="комментарий" onChange={(e) => setBatch({ ...batch, comment_rf: e.target.value })} /> },
                ]),
              ] },
            ]} />
            <div style={{ marginTop: 8 }}><b>Позиции в партии</b> <span className="muted">(позиция из другой партии перейдёт в эту)</span>
              <div className="row" style={{ flexWrap: "wrap", gap: 12 }}>{positions.map((p) => (
                <label key={p.id}><input type="checkbox" checked={posSel.includes(p.id)} onChange={(e) => setPosSel(e.target.checked ? [...posSel, p.id] : posSel.filter((x) => x !== p.id))} />{" "}
                  {designation(p)}{p.batch_id && p.batch_id !== editId ? <span className="muted"> ({batches.find((b) => b.id === p.batch_id)?.name})</span> : null}</label>))}</div></div>
            <div className="row" style={{ marginTop: 8 }}><button className="btn secondary" onClick={() => saveBatch()}>{editId ? "Сохранить партию" : "Создать партию"}</button>
              {editId && <button className="btn ghost" onClick={resetBatchForm}>Отменить правку</button>}
              {batches.length > 0 && <button className="btn ghost" onClick={() => { resetBatchForm(); setShowBatchForm(false); }}>Свернуть</button>}<UndoButton h={history} />
              {batchDirty && batchCost > 0 && <span className="badge warn">не сохранено — сохранится при «Отправить далее»</span>}</div>
          </>
        )}
      </div>
      {wizard && ws && <div id="lb-req"><LogistRequests dealId={deal.id} ws={ws} reload={loadWs} onApplyChosen={canEdit ? applyChosen : undefined} /></div>}
      {show(2) && stepNav}
      </div>
      <div hidden={!show(3)}>
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
          <div className="card" key={p.id} id={p.id === positions[0]?.id ? "lb-pos" : undefined}>
            <h2 style={{ margin: 0, cursor: "pointer" }} onClick={() => setOpenPos(openPos.includes(p.id) ? openPos.filter((x) => x !== p.id) : [...openPos, p.id])}>
              <span className="muted" style={{ fontSize: 14 }}>{openPos.includes(p.id) ? "▾" : "▸"}</span> {p.machine_type} {designation(p)} × {p.qty}{" "}
              <span style={{ fontSize: 13, fontWeight: 400 }}>{savedHs(p) ? <span className="badge ok">ТН ВЭД {((p.logistics || {}) as { hs_code?: string }).hs_code}</span> : <span className="badge warn">не заполнена</span>}
                {" "}<span className="muted">партия: {batches.find((b) => b.id === p.batch_id)?.name || "не распределена"}</span></span></h2>
            {openPos.includes(p.id) && <div style={{ marginTop: 10 }}>
            <LogistFields pos={ws?.fields.find((x) => x.position_id === p.id)} canChoose={!!ws?.can_choose_fields} onChoose={() => setChooser(true)} />
            {(p.qty || 1) > 1 && <div className="alert info" style={{ margin: "0 0 8px" }}>Расходы позиции (логистика, экспорт, сертификация, утилизационный сбор) — <b>на 1 машину</b>; в расчёт × {p.qty} шт. Пошлина и НДС — в процентах, от стоимости всех машин.</div>}
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
                { key: "hs", name: <>Код ТН ВЭД <span className="muted">(вводится вручную; поиск по справочнику)</span></>, filled: !!f.hs_code, required: true,
                  ref: <span className="muted">{hc?.found === false ? "кода нет в справочнике" : hc?.certification?.length ? hc.certification.join(", ") : ""}</span>,
                  value: <>
                    <input value={f.hs_code} disabled={!canEdit} placeholder="10 цифр" onChange={(e) => { const v = e.target.value; upd(p.id, { hs_code: v }); if (v.length >= 2) api.get<typeof hs>(`/api/refs/hs?q=${encodeURIComponent(v)}`).then(setHs); if (/^\d{10}$/.test(v.replace(/\s/g, ""))) lookupHs(p.id, v); }} />
                    {canEdit && hs.length > 0 && <div style={{ fontSize: 12, marginTop: 2 }}>{hs.slice(0, 5).map((h) => <div key={h.code} style={{ cursor: "pointer", padding: "2px 0" }} onClick={() => { upd(p.id, { hs_code: h.code, duty_pct: String(h.duty_pct), vat_pct: String(h.vat_pct), util_applies: null, util_coefficient: "" }); setHs([]); loadUref(p.id, h.code); }}><b>{h.code}</b> — {h.description} (пошлина {h.duty_pct} %, НДС {h.vat_pct} %; {h.certification.join(", ")})</div>)}</div>}</>,
                  comment: ci("hs_code") },
                ...(/^\d{10}$/.test(f.hs_code.replace(/\s/g, "")) ? [
                  { key: "hsn", name: "Наименование кода ТН ВЭД", filled: !!f.hs_description, required: true,
                    ref: <span className="muted">{hsInfo[p.id]?.source === "reference" ? "из справочника" : (p as { catalog_hs?: unknown }).catalog_hs && !(p.logistics as { hs_code?: string } | undefined)?.hs_code ? "из карточки оборудования в каталоге" : "вводится вручную"}</span>,
                    value: <input value={f.hs_description} placeholder="наименование по ТН ВЭД ЕАЭС" disabled={!canEdit} onChange={(e) => upd(p.id, { hs_description: e.target.value })} />,
                    comment: <span className="muted" style={{ fontSize: 11 }}>сохранится в позиции и в карточке оборудования каталога</span> },
                ] : []),
                { key: "duty", name: "Пошлина", unit: "%", filled: f.duty_pct !== "",
                  ref: hc?.found ? <span className={hc.mismatch ? "badge warn" : "muted"}>справочник: {hc.ref_duty_pct} %</span> : null,
                  value: <input type="number" value={f.duty_pct} disabled={!canEdit} onChange={(e) => upd(p.id, { duty_pct: e.target.value })} />,
                  comment: <input placeholder="обязателен при ручной ставке" disabled={!canEdit} value={f.hs_manual_comment} onChange={(e) => upd(p.id, { hs_manual_comment: e.target.value })} /> },
                { key: "vat", name: "НДС при ввозе", unit: "%", filled: f.vat_pct !== "",
                  ref: hc?.found ? <span className={hc.mismatch ? "badge warn" : "muted"}>справочник: {hc.ref_vat_pct} %</span> : null,
                  value: <input type="number" value={f.vat_pct} disabled={!canEdit} onChange={(e) => upd(p.id, { vat_pct: e.target.value })} />, comment: ci("vat_pct") },
                { key: "cert", name: "Сертификация (ТР ТС 010/2011, ЭПБ, ПСМ)", unit: <VatUnit v={f.cert_vat} disabled={!canEdit} on={(x) => upd(p.id, { cert_vat: x })} />, ref: hist("certification_rub"), filled: f.certification_rub > 0,
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
            </div>}
          </div>
        );
      })}
      {show(3) && stepNav}
      </div>
      {!wizard && ws && <div id="lb-req"><LogistRequests dealId={deal.id} ws={ws} reload={loadWs} onApplyChosen={canEdit ? applyChosen : undefined} /></div>}
      {canEdit && !wizard && <div className="row"><button className="btn" onClick={() => complete()}>Отправить далее на расчёт</button><span className="muted">несохранённые данные партии и позиций сохранятся; позиции без партии войдут в первую партию</span></div>}
    </>
  );
}


/** D-122: рублёвая сумма логиста — с НДС или без НДС (выбирает логист); в себестоимость идёт сумма без НДС. */
function VatUnit({ v, on, disabled = false }: { v: boolean; on: (x: boolean) => void; disabled?: boolean }) {
  return (
    <select className="vat-unit" value={v ? "gross" : "net"} disabled={disabled} title="Сумма введена с НДС или без НДС" onChange={(e) => on(e.target.value === "gross")}>
      <option value="net">RUB без НДС</option>
      <option value="gross">RUB с НДС</option>
    </select>
  );
}
