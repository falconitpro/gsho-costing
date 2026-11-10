import { Fragment, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { lbl, STATUS } from "../labels";
import { api, fmtDate, fmtRub } from "../api";
import { can, type Deal, type Me, type Stage } from "../types";
import ContractScans from "./ContractScans";
import { reqSummary, type ReqSet } from "./ClientRequisites";

type Resp = { stages: Stage[]; status: string; contract_number: string; contract_signed_at: string | null; factory_contract_signed_at: string | null; factory_contract_number?: string | null; won_variant_id: string | null;
  related: { id: string; number: string; status: string }[]; contract_steps: CStep[]; status_detail: string;
  client_locked?: boolean; factory_locked?: boolean; can_amend?: boolean; contract_history?: Amend[];
  client_requisites_id?: string | null; client_requisites?: Partial<ReqSet>; finance_url?: string | null; client_requisites_options?: { id: string; title: string; is_default: boolean; legal_name: string; inn: string; kpp: string }[] };
type Amend = { side: "client" | "factory"; action: "amended" | "unsigned"; old_number: string; old_date: string; number: string; date: string; reason: string; by_name: string; at: string };
type CStep = { code: string; title: string; roles: string[]; hint: string; state: "done" | "current" | "pending"; on: string | null; by: string; comment: string };
// «Финансы»: статус сверки старой отметки оплаты этапа (D-117)
const FIN_MARK: Record<string, [string, string]> = { needs_reconciliation: ["требует сверки в «Финансах»", "warn"], posted: ["подтверждено в «Финансах»", "ok"],
  reversed: ["сторнировано в «Финансах»", "bad"], withdrawn: ["отметка снята", ""] };
const ROLE_T: Record<string, string> = { manager: "менеджер", logistician: "логист", service_head: "сервис", fd: "ФД", cd: "КД", gd: "ЗГД", purchasing: "закупки", lawyer: "юрист" };

/** Задача 2: стадия «Заключение и исполнение контракта» — реквизиты контракта, лента этапов с плановыми и фактическими датами,
 *  платежи (ФД), изменение условий (повторная визация), закрытие (КД). Задача 6: этапы «заказать/установить опцию РФ». */
export default function ExecutionTab({ deal, me, reload }: { deal: Deal; me: Me; reload: () => void }) {
  const [data, setData] = useState<Resp | null>(null);
  const [form, setForm] = useState({ contract_number: deal.contract_number || "", contract_signed_at: deal.contract_signed_at || "", factory_contract_signed_at: deal.factory_contract_signed_at || "", factory_contract_number: deal.factory_contract_number || "", won_variant_id: deal.won_variant_id || "", client_requisites_id: "" });
  const [actual, setActual] = useState<Record<string, { actual_on: string; actual_amount: string; note: string }>>({});
  const [reason, setReason] = useState("");
  const [refine, setRefine] = useState<Record<string, { on: string; note: string }>>({});
  const [hist, setHist] = useState<Record<string, boolean>>({});
  const [err, setErr] = useState("");
  const [step, setStep] = useState<Record<string, { on: string; comment: string; number: string; fnumber: string }>>({});
  const [scan, setScan] = useState<Record<string, File | null>>({});
  const [amend, setAmend] = useState<{ side: "client" | "factory"; number: string; signed_at: string; reason: string } | null>(null);
  const isMgr = me.roles.some((r) => r.code === "manager") && deal.manager_id === me.id;
  const isCdGd = me.roles.some((r) => r.code === "cd" || r.code === "gd");
  const load = () => api.get<Resp>(`/api/deals/${deal.id}/execution/stages`).then(setData).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, [deal.id, deal.status]);
  const [ver, setVer] = useState(0);
  const [notice, setNotice] = useState("");
  async function act(fn: () => Promise<unknown>) {
    try {
      const r = await fn() as { finance?: { warning?: string } } | undefined;
      setNotice(r?.finance?.warning || ""); setErr(""); reload(); load(); setVer((v) => v + 1);
    } catch (e) { setErr((e as Error).message); }
  }
  if (!data) return <div className="muted">{err || "Загрузка…"}</div>;
  const groups: Record<string, Stage[]> = {};
  for (const s of data.stages) { const k = s.batch ? `Партия: ${s.batch}` : s.position ? `Позиция: ${s.position}` : "Сделка"; (groups[k] ||= []).push(s); }
  const done = data.stages.filter((s) => s.done).length;
  const canStep = (c: CStep) => me.roles.some((r) => r.code === "gd" || (r.code !== "manager" && c.roles.includes(r.code))) || (isMgr && c.roles.includes("manager"));
  const sv = (c: CStep) => step[c.code] || { on: new Date().toISOString().slice(0, 10), comment: "", number: form.contract_number, fnumber: form.factory_contract_number };
  const SCAN_KIND: Record<string, string> = { client_signed: "client", factory_signed: "factory" };
  const markStep = (c: CStep, done: boolean) => act(async () => {
    await api.post(`/api/deals/${deal.id}/execution/contract-step`, { code: c.code, done, on: sv(c).on, comment: sv(c).comment, contract_number: sv(c).number || null,
      factory_contract_number: sv(c).fnumber || null });
    const f = scan[c.code];
    if (done && f && SCAN_KIND[c.code]) { await api.upload(`/api/deals/${deal.id}/contract-scans`, f, { kind: SCAN_KIND[c.code] }); setScan({ ...scan, [c.code]: null }); }
  });
  const needNo = (c: CStep, v: { number: string; fnumber: string }) => (c.code === "client_signed" && !v.number.trim()) || (c.code === "factory_signed" && !(v.fnumber || "").trim());
  const allSigned = data.contract_steps.find((c) => c.code === "client_signed")?.state === "done";
  const SIDE_T = { client: "с клиентом", factory: "с заводом" } as const;
  const lockLine = (side: "client" | "factory") => {
    const locked = side === "client" ? data.client_locked : data.factory_locked;
    const hist = (data.contract_history || []).filter((h) => h.side === side);
    if (!locked && hist.length === 0) return null;
    const cur = side === "client" ? { number: data.contract_number || "", on: String(data.contract_signed_at || "") } : { number: data.factory_contract_number || "", on: String(data.factory_contract_signed_at || "") };
    return (
      <div style={{ marginTop: 6 }}>
        {locked && <div className="row" style={{ gap: 8, flexWrap: "wrap", fontSize: 12, background: "var(--done-bg)", borderRadius: 6, padding: "5px 10px" }}>
          <span>🔒 <b>Договор {SIDE_T[side]} подписан</b> — номер и дата зафиксированы.</span>
          {data.can_amend ? <button className="btn ghost" style={{ padding: "2px 10px", fontSize: 12, marginLeft: "auto" }} onClick={() => setAmend({ side, number: cur.number, signed_at: cur.on.slice(0, 10), reason: "" })}>Изменить подписанный договор</button>
            : <span className="muted">Исправить может ЗГД или КД.</span>}
        </div>}
        {amend && amend.side === side && (
          <div style={{ border: "1px solid var(--warn)", background: "var(--warn-bg)", borderRadius: 6, padding: 10, marginTop: 6 }}>
            <b style={{ fontSize: 13 }}>Изменение подписанного договора {SIDE_T[side]}</b>
            <div className="grid cols-3" style={{ marginTop: 6 }}>
              <label className="f">Номер договора<input value={amend.number} onChange={(e) => setAmend({ ...amend, number: e.target.value })} /></label>
              <label className="f">Дата подписания<input type="date" value={amend.signed_at} onChange={(e) => setAmend({ ...amend, signed_at: e.target.value })} /></label>
              <label className="f">Причина изменения *<input value={amend.reason} placeholder="напр. доп. соглашение № 1 от …, опечатка в номере" onChange={(e) => setAmend({ ...amend, reason: e.target.value })} /></label>
            </div>
            <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>Изменение даты пересчитает связанные этапы и платежи. Прежние номер и дата сохранятся в истории.</div>
            <div className="row" style={{ marginTop: 6, gap: 6 }}>
              <button className="btn" disabled={!amend.number.trim() || !amend.signed_at || amend.reason.trim().length < 3}
                onClick={() => act(async () => { await api.post(`/api/deals/${deal.id}/execution/contract-amend`, amend);
                  setForm((f) => side === "client" ? { ...f, contract_number: amend.number.trim(), contract_signed_at: amend.signed_at } : { ...f, factory_contract_number: amend.number.trim(), factory_contract_signed_at: amend.signed_at }); setAmend(null); })}>Сохранить изменение</button>
              <button className="btn ghost" onClick={() => setAmend(null)}>Отмена</button></div>
          </div>)}
        {hist.length > 0 && (
          <details style={{ marginTop: 4, fontSize: 12 }}><summary className="muted" style={{ cursor: "pointer" }}>История изменений · {hist.length}</summary>
            <table style={{ fontSize: 12, marginTop: 4 }}><thead><tr><th>Когда</th><th>Кто</th><th>Было</th><th>Стало</th><th>Причина</th></tr></thead>
              <tbody>{hist.map((h, i) => <tr key={i}><td style={{ whiteSpace: "nowrap" }}>{fmtDate(h.at)}</td><td>{h.by_name}</td>
                <td>№ {h.old_number || "—"} от {h.old_date ? fmtDate(h.old_date) : "—"}</td>
                <td>{h.action === "unsigned" ? <span style={{ color: "var(--warn)" }}>отметка о подписании снята</span> : <>№ {h.number} от {fmtDate(h.date)}</>}</td><td>{h.reason}</td></tr>)}</tbody></table>
          </details>)}
      </div>);
  };
  return (
    <>
      {err && <div className="alert error">{err}</div>}
      {deal.status === "contract" && (
        <div className="card" style={{ borderColor: "var(--accent)" }}>
          <h2 style={{ marginTop: 0 }}>Заключение контракта <span className="badge accent">{data.status_detail.split("·")[1]?.trim() || "в работе"}</span></h2>
          <p className="muted">Подэтапы идут по порядку; отметка этапа ставит задачу следующему ответственному. Номер и дата подписания с клиентом обязательны для начала исполнения; дата подписания с заводом — основание для аванса заводу и срока производства.</p>
          <table>
            <thead><tr><th style={{ width: 28 }} /><th>Подэтап</th><th>Кто отмечает</th><th>Дата</th><th>Отметил / комментарий</th><th /></tr></thead>
            <tbody>{data.contract_steps.map((c, i) => {
              const v = sv(c);
              const editable = canStep(c) && c.state !== "done";
              return (
                <tr key={c.code} style={{ background: c.state === "current" ? "var(--accent-soft)" : undefined, opacity: c.state === "pending" ? 0.75 : 1 }}>
                  <td style={{ textAlign: "center", fontSize: 18 }}>{c.state === "done" ? "✅" : c.state === "current" ? "▶" : i + 1}</td>
                  <td><b>{c.title}</b><div className="muted" style={{ fontSize: 12 }}>{c.hint}</div></td>
                  <td style={{ fontSize: 12 }}>{c.roles.map((r) => ROLE_T[r] || r).join(", ")}</td>
                  <td>{c.state === "done" ? fmtDate(c.on) : editable ? <input type="date" value={v.on} onChange={(e) => setStep({ ...step, [c.code]: { ...v, on: e.target.value } })} /> : "—"}</td>
                  <td style={{ fontSize: 12 }}>{c.state === "done" ? <>{c.code === "client_signed" && data.contract_number && <div>№ {data.contract_number}</div>}{c.code === "factory_signed" && data.factory_contract_number && <div>№ {data.factory_contract_number}</div>}{c.by}{c.comment && <div className="muted">{c.comment}</div>}
                    {(c.code === "client_signed" || c.code === "factory_signed") && <ContractScans dealId={deal.id} meId={me.id} canRemoveAll={isCdGd} version={ver} only={c.code === "client_signed" ? "client" : "factory"} />}</> : editable && <>
                    {c.code === "client_signed" && <input placeholder="номер контракта *" value={v.number} onChange={(e) => setStep({ ...step, [c.code]: { ...v, number: e.target.value } })} style={{ width: 150, marginRight: 4 }} />}
                    {c.code === "factory_signed" && <input placeholder="номер контракта с заводом *" value={v.fnumber || ""} onChange={(e) => setStep({ ...step, [c.code]: { ...v, fnumber: e.target.value } })} style={{ width: 190, marginRight: 4 }} />}
                    <input placeholder="комментарий" value={v.comment} onChange={(e) => setStep({ ...step, [c.code]: { ...v, comment: e.target.value } })} style={{ width: 200 }} />
                    {SCAN_KIND[c.code] && <div style={{ marginTop: 4 }}>скан: <input type="file" accept=".pdf,.jpg,.jpeg,.png,.tif,.tiff,.zip" style={{ fontSize: 11, maxWidth: 220 }}
                      onChange={(e) => setScan({ ...scan, [c.code]: e.target.files?.[0] || null })} /></div>}</>}</td>
                  <td>{c.state === "done" ? (SCAN_KIND[c.code] ? (data.can_amend && <button className="btn ghost" title="только ЗГД или КД, с указанием причины" onClick={() => { const r = prompt("Причина снятия отметки о подписании договора"); if (r && r.trim().length >= 3) act(() => api.post(`/api/deals/${deal.id}/execution/contract-step`, { code: c.code, done: false, comment: r.trim() })); }}>снять отметку</button>)
                      : canStep(c) && <button className="btn ghost" onClick={() => markStep(c, false)}>снять отметку</button>)
                    : editable && <button className={"btn " + (c.state === "current" ? "" : "secondary")} disabled={needNo(c, v)} title={needNo(c, v) ? "укажите номер контракта" : ""} onClick={() => markStep(c, true)}>Выполнено</button>}</td>
                </tr>);
            })}</tbody>
          </table>
          {(isMgr || isCdGd) && <div className="row" style={{ marginTop: 10 }}>
            <button className="btn" disabled={!allSigned} title={allSigned ? "" : "сначала отметьте «Контракт подписан клиентом»"} onClick={() => act(() => api.post(`/api/deals/${deal.id}/execution/start`))}>Контракт подписан — начать исполнение</button>
            {!allSigned && <span className="muted" style={{ fontSize: 12 }}>доступно после подписания контракта клиентом</span>}</div>}
        </div>)}
      <div className="grid cols-2">
        <div className="card">
          <h2 style={{ marginTop: 0 }}>Реквизиты контракта</h2>
          {(() => { const showScans = ["won", "contract", "execution", "closed"].includes(deal.status); return <>
          <h3 style={{ margin: "4px 0" }}>Договор с клиентом</h3>
          <div className="grid cols-2">
            <label className="f">Номер договора с клиентом<input value={form.contract_number} disabled={!(isMgr || isCdGd) || !!data.client_locked} onChange={(e) => setForm({ ...form, contract_number: e.target.value })} /></label>
            <label className="f">Дата договора с клиентом<input type="date" value={form.contract_signed_at || ""} disabled={!(isMgr || isCdGd) || !!data.client_locked} onChange={(e) => setForm({ ...form, contract_signed_at: e.target.value })} /></label>
          </div>
          {lockLine("client")}
          {(() => {
            const opts = data.client_requisites_options || [];
            const curId = form.client_requisites_id || data.client_requisites_id || opts.find((o) => o.is_default)?.id || "";
            const snap = data.client_requisites && Object.keys(data.client_requisites).length ? data.client_requisites : undefined;
            return (
              <div style={{ marginTop: 6 }}>
                <label className="f">Реквизиты клиента в договоре <span className="muted">— из карточки клиента (раздел «Контрагенты»); по умолчанию подставляется набор «по умолчанию»</span>
                  <select value={curId} disabled={!(isMgr || isCdGd)} onChange={(e) => setForm({ ...form, client_requisites_id: e.target.value })}>
                    <option value="">— не выбраны —</option>{opts.map((o) => <option key={o.id} value={o.id}>{o.title}{o.is_default ? " (по умолчанию)" : ""}{o.inn ? ` · ИНН ${o.inn}${o.kpp ? "/" + o.kpp : ""}` : ""}</option>)}</select></label>
                {snap ? <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>В договоре: {reqSummary(snap)}{data.client_requisites_id && form.client_requisites_id && form.client_requisites_id !== data.client_requisites_id && <b style={{ color: "var(--warn)" }}> · выбран другой набор — нажмите «Сохранить реквизиты»</b>}</div>
                  : <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>Реквизиты ещё не зафиксированы в договоре{opts.length <= 1 ? " — добавьте наборы реквизитов в карточке клиента" : ""}.</div>}
              </div>);
          })()}
          {showScans && <ContractScans dealId={deal.id} meId={me.id} canRemoveAll={isCdGd} version={ver} only="client" />}
          <h3 style={{ margin: "12px 0 4px" }}>Договор с заводом</h3>
          <div className="grid cols-2">
            <label className="f">Номер договора с заводом<input value={form.factory_contract_number} disabled={!(isMgr || isCdGd) || !!data.factory_locked} onChange={(e) => setForm({ ...form, factory_contract_number: e.target.value })} /></label>
            <label className="f">Дата договора с заводом<input type="date" value={form.factory_contract_signed_at || ""} disabled={!(isMgr || isCdGd) || !!data.factory_locked} onChange={(e) => setForm({ ...form, factory_contract_signed_at: e.target.value })} /></label>
          </div>
          {lockLine("factory")}
          {showScans && <ContractScans dealId={deal.id} meId={me.id} canRemoveAll={isCdGd} version={ver} only="factory" />}
          </>; })()}
          <div className="grid cols-2" style={{ marginTop: 8 }}>
            {deal.has_alternatives && <label className="f">Предложение, которое выбрал клиент
              <select value={form.won_variant_id} disabled={!(isMgr || isCdGd)} onChange={(e) => setForm({ ...form, won_variant_id: e.target.value })}><option value="">—</option>{deal.variants.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}</select></label>}
          </div>
          {(isMgr || isCdGd) && (
            <div className="row" style={{ marginTop: 8 }}>
              <button className="btn secondary" onClick={() => act(() => api.patch(`/api/deals/${deal.id}/execution/contract`, { ...form, contract_signed_at: form.contract_signed_at || null, factory_contract_signed_at: form.factory_contract_signed_at || null, won_variant_id: form.won_variant_id || null,
                client_requisites_id: form.client_requisites_id || data.client_requisites_id || (data.client_requisites_options || []).find((o) => o.is_default)?.id || null }))}>Сохранить реквизиты</button>
            </div>
          )}
          {(deal.status === "contract" || deal.status === "execution") && (isMgr || isCdGd) && (
            <div className="row" style={{ marginTop: 10 }}>
              <input placeholder="что изменилось в условиях контракта относительно КП" value={reason} onChange={(e) => setReason(e.target.value)} style={{ width: 360 }} />
              <button className="btn ghost" disabled={!reason} onClick={() => act(() => api.post(`/api/deals/${deal.id}/execution/terms-changed`, { reason }))}>Условия контракта отличаются от КП → новая версия и повторная визация</button>
            </div>
          )}
          {deal.status === "execution" && can(me, "execution_close", 2) && (
            <div className="row" style={{ marginTop: 10 }}><button className="btn" onClick={() => { const r = prompt("Комментарий к закрытию:") || ""; act(() => api.post(`/api/deals/${deal.id}/execution/close`, { reason: r })); }}>Закрыть сделку (КД)</button></div>
          )}
          {data.related.length > 0 && <div className="muted" style={{ marginTop: 8 }}>Связанные заявки: {data.related.map((r) => `${r.number} (${lbl(STATUS, r.status)})`).join(", ")}</div>}
        </div>
        <div className="card">
          <div className="row" style={{ justifyContent: "space-between" }}><h2 style={{ marginTop: 0 }}>Ход исполнения</h2>
            {data.finance_url && <Link to={data.finance_url}>Платежи сделки в «Финансах» →</Link>}</div>
          {notice && <div className="alert warn" style={{ fontSize: 13 }}>{notice}</div>}
          <div className="row" style={{ gap: 24 }}>
            <div className="kpi"><span className="v">{done} / {data.stages.length}</span><span className="l">этапов выполнено</span></div>
            <div className="kpi"><span className="v">{data.stages.filter((s) => s.overdue).length}</span><span className="l">просрочено</span></div>
            <div className="kpi"><span className="v">{data.stages.filter((s) => (s.deviation_days ?? 0) > 0).length}</span><span className="l">сдвинуто позже плана</span></div>
          </div>
          {(() => {   // ключевые сроки: базовый план → прогноз / факт
            const key = ["delivery", "commissioning", "closing"].map((c) => data.stages.filter((s) => s.code === c)).map((ss) => ss.sort((a, b) => (b.planned_on || "").localeCompare(a.planned_on || ""))[0]).filter(Boolean);
            return key.length > 0 && (
              <table style={{ marginTop: 8 }}><thead><tr><th>Ключевой срок</th><th>Базовый план</th><th>Прогноз / факт</th><th className="num">Отклонение</th></tr></thead>
                <tbody>{key.map((s) => <tr key={s.id}><td>{s.title}</td><td>{fmtDate(s.baseline_on)}</td><td>{s.done ? <b>{fmtDate(s.actual_on)}</b> : fmtDate(s.refined_on || s.planned_on)}</td><td className="num"><Dev d={s.deviation_days} /></td></tr>)}</tbody></table>);
          })()}
          <p className="muted">Базовый план фиксируется при начале исполнения и не меняется. Даты готовности, предварительной (онлайн) и окончательной приёмки вносятся по данным завода — каждое изменение хранится в истории и пересчитывает прогноз следующих этапов; окончательная приёмка позже готовности сдвигает отгрузку, а замечания приёмки со сроком устранения — ожидаемую дату приёмки; отклонение — от базового плана, «+» — позже. Фактическую дату вносят ответственные (логист — отгрузка, CMR, ДТ, доставка; сервис — ПНР; менеджер — подписание и готовность), платежи и аккредитив — ФД. Ввод факта пересчитывает плановые даты следующих этапов и платежей.</p>
        </div>
      </div>
      {Object.entries(groups).map(([g, stages]) => (
        <div className="card" key={g}>
          <h2 style={{ marginTop: 0 }}>{g}</h2>
          <table>
            <thead><tr><th>Этап</th><th>Базовый план</th><th>Уточнено / по данным завода</th><th>Прогноз</th><th>Факт</th><th className="num">Отклонение</th>{can(me, "cost") && <th className="num">Сумма</th>}<th>Ответственный</th><th>Отметка</th></tr></thead>
            <tbody>{stages.map((s) => {
              const a = actual[s.id] || { actual_on: s.actual_on || "", actual_amount: s.actual_amount != null ? String(s.actual_amount) : (s.amount != null ? String(s.amount) : ""), note: s.note || "" };
              return (
                <Fragment key={s.id}>
                <tr style={{ background: s.done ? "var(--ok-bg)" : s.overdue ? "var(--late-bg)" : undefined }}>
                  <td>{s.title}{s.note && <div className="muted" style={{ fontSize: 11 }}>{s.note}</div>}
                    {(s.history || []).length > 0 && <div><a href="#" style={{ fontSize: 11 }} onClick={(e) => { e.preventDefault(); setHist({ ...hist, [s.id]: !hist[s.id] }); }}>
                      история дат ({s.history!.length}) {hist[s.id] ? "▴" : "▾"}</a></div>}</td>
                  <td>{fmtDate(s.baseline_on) || <span className="muted">—</span>}</td>
                  <td>{s.refined_on ? <>{fmtDate(s.refined_on)}{s.factory_date && <span className="badge" style={{ marginLeft: 4, fontSize: 10 }}>завод</span>}{s.refined_note && <div className="muted" style={{ fontSize: 11 }}>{s.refined_note}</div>}</> : <span className="muted">—</span>}
                    {s.can_set && !s.done && deal.status !== "closed" && (() => { const r = refine[s.id] || { on: s.refined_on || "", note: s.refined_note || "" };
                      return <div className="row" style={{ marginTop: 2 }}>
                        <input type="date" value={r.on} style={{ width: 130 }} onChange={(e) => setRefine({ ...refine, [s.id]: { ...r, on: e.target.value } })} />
                        <input placeholder={s.factory_date ? "основание (письмо завода…)" : "основание"} value={r.note} style={{ width: s.factory_date ? 150 : 100 }} onChange={(e) => setRefine({ ...refine, [s.id]: { ...r, note: e.target.value } })} />
                        <button className="btn ghost" style={{ padding: "2px 6px" }} onClick={() => act(() => api.post(`/api/deals/${deal.id}/execution/stages/${s.id}/refine`, { refined_on: r.on || null, note: r.note }))}>{s.factory_date ? "дата завода" : "уточнить"}</button>
                      </div>; })()}</td>
                  <td>{s.done ? <span className="muted">—</span> : fmtDate(s.planned_on)}</td>
                  <td>{s.done ? <b>{fmtDate(s.actual_on)}</b> : s.overdue ? <span className="badge bad">просрочен</span> : <span className="muted">—</span>}
                    {s.finance && <div style={{ marginTop: 2 }}><Link to={s.finance.url} className={`badge ${FIN_MARK[s.finance.status]?.[1] ?? ""}`} style={{ textDecoration: "none", fontSize: 11 }}>{FIN_MARK[s.finance.status]?.[0] ?? s.finance.status}</Link></div>}</td>
                  <td className="num"><Dev d={s.deviation_days} /></td>
                  {can(me, "cost") && <td className="num">{s.amount != null ? `${fmtRub(s.amount)} ${s.currency}` : ""}{s.actual_amount != null && <div className="muted">факт {fmtRub(s.actual_amount)}</div>}</td>}
                  <td className="muted">{s.responsible_roles.map((r) => ROLE_T[r] || r).join(", ")}</td>
                  <td>{s.code === "factory_acceptance" ? <span className="muted" style={{ fontSize: 11 }}>факт — по решению приёмки всех машин (вкладка «Приёмка на заводе»)</span> : s.can_set && deal.status !== "closed" ? (
                    <div className="row">
                      <input type="date" value={a.actual_on} onChange={(e) => setActual({ ...actual, [s.id]: { ...a, actual_on: e.target.value } })} />
                      {(s.kind === "payment_in" || s.kind === "payment_out") && <input type="number" placeholder="сумма" value={a.actual_amount} style={{ width: 120 }} onChange={(e) => setActual({ ...actual, [s.id]: { ...a, actual_amount: e.target.value } })} />}
                      <input placeholder="примечание" value={a.note} style={{ width: 120 }} onChange={(e) => setActual({ ...actual, [s.id]: { ...a, note: e.target.value } })} />
                      <button className="btn ghost" style={{ padding: "3px 8px" }} onClick={() => act(() => api.post(`/api/deals/${deal.id}/execution/stages/${s.id}/actual`, { actual_on: a.actual_on || null, actual_amount: a.actual_amount ? Number(a.actual_amount) : null, note: a.note }))}>{s.done ? "изменить" : "отметить"}</button>
                    </div>) : null}</td>
                </tr>
                {hist[s.id] && <tr><td colSpan={9} style={{ background: "#fafafa", fontSize: 12 }}>
                  <table style={{ fontSize: 12 }}><thead><tr><th>Когда внесено</th><th>Вид</th><th>Дата</th><th>Основание</th><th>Кто</th></tr></thead>
                    <tbody>{s.history!.map((h, i) => <tr key={i}><td>{fmtDate(h.at)} {h.at?.slice(11, 16)}</td><td>{h.kind_title}</td><td><b>{h.on ? fmtDate(h.on) : "снято"}</b></td><td>{h.note}</td><td>{h.by_name}</td></tr>)}</tbody></table>
                </td></tr>}
                </Fragment>);
            })}</tbody>
          </table>
        </div>
      ))}
    </>
  );
}

/** Отклонение от базового плана: «+N дн.» — позже (красным), «−N дн.» — раньше (зелёным). */
function Dev({ d }: { d?: number | null }) {
  if (d == null) return <span className="muted">—</span>;
  if (d === 0) return <span className="muted">в срок</span>;
  return <span className={"badge " + (d > 0 ? "bad" : "ok")}>{d > 0 ? "+" : "−"}{Math.abs(d)} дн.</span>;
}
