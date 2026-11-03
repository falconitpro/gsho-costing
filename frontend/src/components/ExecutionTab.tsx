import { useEffect, useState } from "react";
import { api, fmtDate, fmtRub } from "../api";
import { can, type Deal, type Me, type Stage } from "../types";

type Resp = { stages: Stage[]; status: string; contract_number: string; contract_signed_at: string | null; factory_contract_signed_at: string | null; won_variant_id: string | null;
  related: { id: string; number: string; status: string }[] };
const ROLE_T: Record<string, string> = { manager: "менеджер", logistician: "логист", service_head: "сервис", fd: "ФД", cd: "КД", gd: "ЗГД", purchasing: "закупки" };

/** Задача 2: стадия «Заключение и исполнение контракта» — реквизиты контракта, лента этапов с плановыми и фактическими датами,
 *  платежи (ФД), изменение условий (повторная визация), закрытие (КД). Задача 6: этапы «заказать/установить опцию РФ». */
export default function ExecutionTab({ deal, me, reload }: { deal: Deal; me: Me; reload: () => void }) {
  const [data, setData] = useState<Resp | null>(null);
  const [form, setForm] = useState({ contract_number: deal.contract_number || "", contract_signed_at: deal.contract_signed_at || "", factory_contract_signed_at: deal.factory_contract_signed_at || "", won_variant_id: deal.won_variant_id || "" });
  const [actual, setActual] = useState<Record<string, { actual_on: string; actual_amount: string; note: string }>>({});
  const [reason, setReason] = useState("");
  const [refine, setRefine] = useState<Record<string, { on: string; note: string }>>({});
  const [err, setErr] = useState("");
  const isMgr = me.roles.some((r) => r.code === "manager") && deal.manager_id === me.id;
  const isCdGd = me.roles.some((r) => r.code === "cd" || r.code === "gd");
  const load = () => api.get<Resp>(`/api/deals/${deal.id}/execution/stages`).then(setData).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, [deal.id, deal.status]);
  async function act(fn: () => Promise<unknown>) { try { await fn(); setErr(""); reload(); load(); } catch (e) { setErr((e as Error).message); } }
  if (!data) return <div className="muted">{err || "Загрузка…"}</div>;
  const groups: Record<string, Stage[]> = {};
  for (const s of data.stages) { const k = s.batch ? `Партия: ${s.batch}` : s.position ? `Позиция: ${s.position}` : "Сделка"; (groups[k] ||= []).push(s); }
  const done = data.stages.filter((s) => s.done).length;
  return (
    <>
      {err && <div className="alert error">{err}</div>}
      <div className="grid cols-2">
        <div className="card">
          <h2 style={{ marginTop: 0 }}>Контракт</h2>
          <div className="grid cols-2">
            <label className="f">Номер контракта<input value={form.contract_number} disabled={!(isMgr || isCdGd)} onChange={(e) => setForm({ ...form, contract_number: e.target.value })} /></label>
            <label className="f">Дата подписания с клиентом<input type="date" value={form.contract_signed_at || ""} disabled={!(isMgr || isCdGd)} onChange={(e) => setForm({ ...form, contract_signed_at: e.target.value })} /></label>
            <label className="f">Дата подписания с заводом<input type="date" value={form.factory_contract_signed_at || ""} disabled={!(isMgr || isCdGd)} onChange={(e) => setForm({ ...form, factory_contract_signed_at: e.target.value })} /></label>
            {deal.has_alternatives && <label className="f">Предложение, которое выбрал клиент
              <select value={form.won_variant_id} disabled={!(isMgr || isCdGd)} onChange={(e) => setForm({ ...form, won_variant_id: e.target.value })}><option value="">—</option>{deal.variants.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}</select></label>}
          </div>
          {(isMgr || isCdGd) && (
            <div className="row" style={{ marginTop: 8 }}>
              <button className="btn secondary" onClick={() => act(() => api.patch(`/api/deals/${deal.id}/execution/contract`, { ...form, contract_signed_at: form.contract_signed_at || null, factory_contract_signed_at: form.factory_contract_signed_at || null, won_variant_id: form.won_variant_id || null }))}>Сохранить реквизиты</button>
              {deal.status === "contract" && <button className="btn" onClick={() => act(() => api.post(`/api/deals/${deal.id}/execution/start`))} disabled={!form.contract_number || !form.contract_signed_at}>Контракт подписан — начать исполнение</button>}
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
          {data.related.length > 0 && <div className="muted" style={{ marginTop: 8 }}>Связанные заявки: {data.related.map((r) => `${r.number} (${r.status})`).join(", ")}</div>}
        </div>
        <div className="card">
          <h2 style={{ marginTop: 0 }}>Ход исполнения</h2>
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
          <p className="muted">Базовый план фиксируется при начале исполнения и не меняется. Уточнённая дата (например, завод подтвердил готовность) пересчитывает прогноз следующих этапов; отклонение — от базового плана, «+» — позже. Фактическую дату вносят ответственные (логист — отгрузка, CMR, ДТ, доставка; сервис — ПНР; менеджер — подписание и готовность), платежи и аккредитив — ФД. Ввод факта пересчитывает плановые даты следующих этапов и платежей.</p>
        </div>
      </div>
      {Object.entries(groups).map(([g, stages]) => (
        <div className="card" key={g}>
          <h2 style={{ marginTop: 0 }}>{g}</h2>
          <table>
            <thead><tr><th>Этап</th><th>Базовый план</th><th>Уточнено</th><th>Прогноз</th><th>Факт</th><th className="num">Отклонение</th>{can(me, "cost") && <th className="num">Сумма</th>}<th>Ответственный</th><th>Отметка</th></tr></thead>
            <tbody>{stages.map((s) => {
              const a = actual[s.id] || { actual_on: s.actual_on || "", actual_amount: s.actual_amount != null ? String(s.actual_amount) : (s.amount != null ? String(s.amount) : ""), note: s.note || "" };
              return (
                <tr key={s.id} style={{ background: s.done ? "#e6f5ec" : s.overdue ? "#fde7e7" : undefined }}>
                  <td>{s.title}{s.note && <div className="muted" style={{ fontSize: 11 }}>{s.note}</div>}</td>
                  <td>{fmtDate(s.baseline_on) || <span className="muted">—</span>}</td>
                  <td>{s.refined_on ? <>{fmtDate(s.refined_on)}{s.refined_note && <div className="muted" style={{ fontSize: 11 }}>{s.refined_note}</div>}</> : <span className="muted">—</span>}
                    {s.can_set && !s.done && deal.status !== "closed" && (() => { const r = refine[s.id] || { on: s.refined_on || "", note: s.refined_note || "" };
                      return <div className="row" style={{ marginTop: 2 }}>
                        <input type="date" value={r.on} style={{ width: 130 }} onChange={(e) => setRefine({ ...refine, [s.id]: { ...r, on: e.target.value } })} />
                        <input placeholder="основание" value={r.note} style={{ width: 100 }} onChange={(e) => setRefine({ ...refine, [s.id]: { ...r, note: e.target.value } })} />
                        <button className="btn ghost" style={{ padding: "2px 6px" }} onClick={() => act(() => api.post(`/api/deals/${deal.id}/execution/stages/${s.id}/refine`, { refined_on: r.on || null, note: r.note }))}>уточнить</button>
                      </div>; })()}</td>
                  <td>{s.done ? <span className="muted">—</span> : fmtDate(s.planned_on)}</td>
                  <td>{s.done ? <b>{fmtDate(s.actual_on)}</b> : s.overdue ? <span className="badge bad">просрочен</span> : <span className="muted">—</span>}</td>
                  <td className="num"><Dev d={s.deviation_days} /></td>
                  {can(me, "cost") && <td className="num">{s.amount != null ? `${fmtRub(s.amount)} ${s.currency}` : ""}{s.actual_amount != null && <div className="muted">факт {fmtRub(s.actual_amount)}</div>}</td>}
                  <td className="muted">{s.responsible_roles.map((r) => ROLE_T[r] || r).join(", ")}</td>
                  <td>{s.can_set && deal.status !== "closed" ? (
                    <div className="row">
                      <input type="date" value={a.actual_on} onChange={(e) => setActual({ ...actual, [s.id]: { ...a, actual_on: e.target.value } })} />
                      {(s.kind === "payment_in" || s.kind === "payment_out") && <input type="number" placeholder="сумма" value={a.actual_amount} style={{ width: 120 }} onChange={(e) => setActual({ ...actual, [s.id]: { ...a, actual_amount: e.target.value } })} />}
                      <input placeholder="примечание" value={a.note} style={{ width: 120 }} onChange={(e) => setActual({ ...actual, [s.id]: { ...a, note: e.target.value } })} />
                      <button className="btn ghost" style={{ padding: "3px 8px" }} onClick={() => act(() => api.post(`/api/deals/${deal.id}/execution/stages/${s.id}/actual`, { actual_on: a.actual_on || null, actual_amount: a.actual_amount ? Number(a.actual_amount) : null, note: a.note }))}>{s.done ? "изменить" : "отметить"}</button>
                    </div>) : null}</td>
                </tr>);
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
