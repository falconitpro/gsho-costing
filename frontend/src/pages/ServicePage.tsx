import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, fmtDate } from "../api";
import { Crumbs, useQueryParam } from "../components/Levels";
import { askConfirm, askPrompt } from "../components/Dialogs";
import { Empty, ErrorState, Loading } from "../components/States";
import { can, type Me } from "../types";
import { setNewUi, useNewUiFlag } from "../uiMode";

/** D-192 (I-1): рабочее место сервиса — наряды, обращения, справочники. Только новый вид.
 *  Наряд меняется только событиями (D-191): каждое действие уходит с номером события и версией наряда,
 *  сервер отвечает «применено», «объединено», «конфликт» или «отклонено» — с причиной. */

type Ev = { id: string; kind: string; kind_title: string; result: string; reason: string; actor_name: string; offline: boolean;
  received_at: string | null; payload: Record<string, unknown>; new_version: number | null };
type Item = { id: string; position: number; text: string; required: boolean; done: boolean; done_by_name: string; done_at: string | null };
type Order = { id: string; number: string; status: string; status_title: string; client_name: string; site_name: string; machine_title: string;
  work_type_name: string; norm_hours: number | null; engineer_id: string | null; engineer_name: string; planned_start: string | null;
  planned_end: string | null; description: string; version: number; conflicts: number; signed_by_name: string; closed_at: string | null;
  actions?: string[]; checklist?: Item[]; events?: Ev[]; open_conflicts?: Ev[]; assigned_by_name?: string;
  site?: { name: string; address: string; access_mode_title: string; hse_notes: string; map_url: string } | null;
  machine?: { manufacturer: string; model: string; serial: string; warranty_until: string | null } | null;
  request?: { number: string; machine_down: boolean; sla_code: string; received_at: string | null } | null };
type Req = { id: string; number: string; status: string; status_title: string; client_id: string; client_name: string; site_name: string;
  machine_title: string; channel: string; kind: string; sla_code: string; description: string; machine_down: boolean; received_at: string;
  reject_reason: string; orders: { id: string; number: string; status: string }[] };
type Ref = { id: string; code: string; name: string; is_draft: boolean; is_active: boolean; comment: string; category?: string; machine_kind?: string;
  norm_hours?: number | null; crew_size?: number; permit_codes?: string[]; checklist?: string[]; valid_months?: number | null;
  reaction_hours?: number | null; arrival_hours?: number | null; restore_hours?: number | null; calendar?: string };
type Refs = { work_types: Ref[]; permits: Ref[]; sla: Ref[]; categories: Record<string, string>; channels: Record<string, string>;
  statuses: Record<string, string>; request_statuses: Record<string, string>; drafts: number };
type Eng = { id: string; name: string; is_engineer: boolean; open_orders: number };

const uid = () => (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`);
const dt = (s: string | null | undefined) => (s ? new Date(s).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—");
const hrs = (v: number | null | undefined) => (v == null ? "не задано" : `${String(v).replace(".", ",")} ч`);
const RESULT: Record<string, string> = { applied: "выполнено", merged: "выполнено (объединено)", conflict: "конфликт — ждёт решения руководителя", rejected: "не выполнено" };
const STATUS_TONE: Record<string, string> = { draft: "", assigned: "info", in_progress: "warn", on_hold: "", ready_to_close: "ok", closed: "ok", cancelled: "" };
const OPEN = ["draft", "assigned", "in_progress", "on_hold", "ready_to_close"];
const ACTION_BTN: [string, string][] = [["start", "Начать"], ["hold", "Приостановить"], ["resume", "Возобновить"], ["ready", "Готов к закрытию"],
  ["reopen", "Вернуть в работу"], ["close", "Закрыть с подписью клиента"], ["cancel", "Отменить наряд"]];

export default function ServicePage({ me }: { me: Me }) {
  const nv = useNewUiFlag();
  const [tab, setTab] = useQueryParam("tab");
  const engOnly = me.roles.length > 0 && me.roles.every((r) => r.code === "engineer");
  const showReq = !engOnly && can(me, "service_orders");
  const showRefs = can(me, "service_refs");
  if (!nv) return (
    <div><Crumbs items={[{ label: "Рабочее место", to: "/" }, { label: "Сервис" }]} />
      <Empty title="Раздел «Сервис» — в новом виде интерфейса" hint="Наряды, обращения и справочники сервиса открываются только в новом виде."
        action={<button className="btn" onClick={() => setNewUi(true)}>Включить новый вид</button>} /></div>);
  if (!can(me, "service_orders")) return <div><h1>Сервис</h1><Empty title="Нет доступа к сервису" hint="Доступ выдаёт ЗГД в «Уровнях доступа»: группа «Сервис: обращения и наряды»." /></div>;
  const cur = tab === "requests" && showReq ? "requests" : tab === "refs" && showRefs ? "refs" : "orders";
  return (
    <div className="sv-page">
      <Crumbs items={[{ label: "Рабочее место", to: "/" }, { label: "Сервис" }]} />
      <div className="page-head"><h1>Сервис</h1>{engOnly && <span className="muted">наряды, назначенные вам</span>}</div>
      {(showReq || showRefs) && <div className="tabs" role="tablist">
        <button role="tab" aria-selected={cur === "orders"} className={cur === "orders" ? "active" : ""} onClick={() => setTab(null, { drop: ["o"] })}>Наряды</button>
        {showReq && <button role="tab" aria-selected={cur === "requests"} className={cur === "requests" ? "active" : ""} onClick={() => setTab("requests", { drop: ["o"] })}>Обращения</button>}
        {showRefs && <button role="tab" aria-selected={cur === "refs"} className={cur === "refs" ? "active" : ""} onClick={() => setTab("refs", { drop: ["o"] })}>Справочники</button>}
      </div>}
      {cur === "orders" ? <OrdersTab me={me} engOnly={engOnly} /> : cur === "requests" ? <RequestsTab me={me} /> : <RefsTab me={me} />}
    </div>);
}

/* ─────────────── наряды ─────────────── */

function OrdersTab({ me, engOnly }: { me: Me; engOnly: boolean }) {
  const [oid, setOid] = useQueryParam("o");
  const [filter, setFilter] = useState("open");
  const [eng, setEng] = useState("");
  const [data, setData] = useState<{ items: Order[]; statuses: Record<string, string>; counts: Record<string, number> } | null>(null);
  const [err, setErr] = useState("");
  const [engs, setEngs] = useState<Eng[]>([]);
  const head = !engOnly && can(me, "service_orders", 2);
  const load = () => {
    const p = new URLSearchParams({ limit: "300" });
    if (filter !== "open" && filter !== "all") p.set("status", filter);
    if (eng) p.set("engineer_id", eng);
    api.get<typeof data & object>(`/api/service/orders?${p}`).then((r) => { setData(r); setErr(""); }).catch((e) => setErr((e as Error).message));
  };
  useEffect(load, [filter, eng]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (head) api.get<Eng[]>("/api/service/engineers").then(setEngs).catch(() => setEngs([])); }, [head]);
  const rows = useMemo(() => (data?.items || []).filter((o) => filter !== "open" || OPEN.includes(o.status)), [data, filter]);
  if (err && !data) return <ErrorState hint={err} onRetry={load} />;
  if (!data) return <Loading />;
  const c = data.counts;
  const conflicts = data.items.reduce((s, o) => s + (o.conflicts || 0), 0);
  const kpi = (key: string, label: string, n: number, tone = "") => (
    <button type="button" className={`cp-kpi sv-kpi ${tone}${filter === key ? " on" : ""}`} aria-pressed={filter === key} onClick={() => setFilter(filter === key ? "open" : key)}>
      <b>{n}</b><span>{label}</span></button>);
  return (
    <div className={"pc-shell" + (oid ? " with-card" : "")}>
      <div>
        <div className="cp-kpis sv-kpis">
          {head && kpi("draft", "Ждут назначения", c.draft || 0)}
          {kpi("assigned", "Назначены", c.assigned || 0)}
          {kpi("in_progress", c.on_hold ? `В работе · приостановлено ${c.on_hold}` : "В работе", (c.in_progress || 0) + (c.on_hold || 0), "warn")}
          {kpi("ready_to_close", "Готовы к закрытию", c.ready_to_close || 0, "ok")}
          {conflicts > 0 && <div className="cp-kpi sv-kpi bad"><b>{conflicts}</b><span>Конфликтов{head ? " — решите в карточке" : ""}</span></div>}
        </div>
        <div className="pc-toolbar sv-toolbar">
          <div className="pc-groups" role="group" aria-label="Статус наряда">
            {[["open", "Открытые"], ...Object.entries(data.statuses).filter(([k]) => !(engOnly && k === "draft")), ["all", "Все"]].map(([k, t]) => (
              <button key={k} type="button" className={filter === k ? "on" : ""} aria-pressed={filter === k} onClick={() => setFilter(k)}>{t}</button>))}
          </div>
          {head && <label className="f sv-eng">Исполнитель<select value={eng} onChange={(e) => setEng(e.target.value)}>
            <option value="">все</option>{engs.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}</select></label>}
        </div>
        {rows.length === 0 ? <Empty title={filter === "open" ? "Открытых нарядов нет" : "Нарядов с таким статусом нет"}
          hint={engOnly ? "Новые наряды назначает руководитель сервиса — они появятся здесь." : head ? "Наряд создаётся из обращения: вкладка «Обращения» → «Создать наряд»." : undefined}
          action={filter !== "open" ? <button className="btn ghost" onClick={() => setFilter("open")}>Показать открытые</button> : undefined} /> : (
          <table className="pc-table nv-cards sv-table">
            <thead><tr><th>Наряд</th><th>Клиент · площадка</th><th>Машина · работы</th><th>Исполнитель</th><th>Статус</th><th>План</th></tr></thead>
            <tbody>{rows.map((o) => (
              <tr key={o.id} className={oid === o.id ? "cur" : ""} tabIndex={0} onClick={() => setOid(o.id)} onKeyDown={(e) => { if (e.key === "Enter") setOid(o.id); }}>
                <td className="pc-art">{o.number}</td>
                <td data-label="Клиент · площадка"><div className="pc-name">{o.client_name}</div><div className="pc-sub">{o.site_name || "площадка не указана"}</div></td>
                <td data-label="Машина · работы">{o.machine_title || <span className="muted">машина не указана</span>}<div className="pc-sub">{o.work_type_name}</div></td>
                <td data-label="Исполнитель">{o.engineer_name || <span className="muted">не назначен</span>}</td>
                <td data-label="Статус"><span className={`badge ${STATUS_TONE[o.status] || ""}`}>{o.status_title}</span>
                  {o.conflicts > 0 && <span className="badge bad sv-conf">конфликт · {o.conflicts}</span>}</td>
                <td data-label="План">{o.planned_start ? dt(o.planned_start) : <span className="muted">—</span>}</td>
              </tr>))}</tbody>
          </table>)}
      </div>
      {oid && <aside className="pc-panel" aria-label="Карточка наряда">
        <button type="button" className="btn ghost pc-close" onClick={() => setOid(null)} aria-label="Закрыть карточку">✕</button>
        <OrderCard id={oid} engs={engs} onChanged={load} /></aside>}
    </div>);
}

function OrderCard({ id, engs, onChanged }: { id: string; engs: Eng[]; onChanged: () => void }) {
  const [o, setO] = useState<Order | null>(null);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState<{ tone: string; text: string } | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [plan, setPlan] = useState({ start: "", end: "" });
  useEffect(() => {
    setO(null); setMsg(null); setNote("");
    api.get<Order>(`/api/service/orders/${id}`).then((r) => { setO(r); setPlan({ start: local(r.planned_start), end: local(r.planned_end) }); })
      .catch((e) => setErr((e as Error).message));
  }, [id]);
  if (err) return <div className="card"><ErrorState hint={err} /></div>;
  if (!o) return <div className="card"><Loading /></div>;
  const act = new Set(o.actions || []);

  async function send(kind: string, payload: Record<string, unknown> = {}) {
    if (!o) return;
    setBusy(true);
    try {
      const r = await api.post<{ event: Ev; order: Order }>(`/api/service/orders/${o.id}/events`,
        { id: uid(), kind, base_version: o.version, payload, device_id: "web" });
      setO(r.order); setPlan({ start: local(r.order.planned_start), end: local(r.order.planned_end) });
      const e = r.event;
      setMsg({ tone: e.result === "applied" || e.result === "merged" ? "ok" : e.result === "conflict" ? "warn" : "error",
        text: `${e.kind_title}: ${RESULT[e.result] || e.result}${e.reason ? ` — ${e.reason}` : ""}` });
      onChanged();
    } catch (e) { setMsg({ tone: "error", text: (e as Error).message }); } finally { setBusy(false); }
  }
  async function doAction(kind: string) {
    if (kind === "close") {
      const name = await askPrompt("ФИО представителя клиента, подписавшего закрытие", o?.signed_by_name || "", { title: "Закрытие наряда" });
      if (name?.trim()) send("close", { signed_by_name: name.trim() });
    } else if (kind === "cancel") {
      const reason = await askPrompt("Причина отмены наряда", "", { title: "Отмена наряда" });
      if (reason?.trim()) send("cancel", { reason: reason.trim() });
    } else send(kind);
  }
  const items = o.checklist || [];
  const left = items.filter((i) => i.required && !i.done).length;
  return (
    <div className="card sv-card">
      <h2>{o.number} <span className={`badge ${STATUS_TONE[o.status] || ""}`}>{o.status_title}</span></h2>
      <div className="muted">{[o.client_name, o.work_type_name].filter(Boolean).join(" · ")}</div>
      {msg && <div className={`alert ${msg.tone === "ok" ? "info" : msg.tone}`} role="status">{msg.text}</div>}
      {o.request?.machine_down && <div className="alert warn">Машина стоит{o.request.sla_code ? ` · срок реакции ${o.request.sla_code}` : ""} · обращение {o.request.number} от {dt(o.request.received_at)}</div>}

      {ACTION_BTN.some(([k]) => act.has(k)) && <div className="row sv-actions">
        {ACTION_BTN.filter(([k]) => act.has(k)).map(([k, t]) => (
          <button key={k} className={"btn" + (k === "cancel" ? " ghost" : k === "close" || k === "start" || k === "ready" ? " primary" : "")} disabled={busy}
            onClick={() => doAction(k)}>{t}</button>))}
      </div>}
      {(act.has("ready") || act.has("close")) && left > 0 && <div className="muted">Чтобы закрыть, отметьте обязательные пункты чек-листа: осталось {left}.</div>}

      {(o.open_conflicts?.length || 0) > 0 && <>
        <h3>Конфликты</h3>
        <ul className="sv-events">{o.open_conflicts!.map((e) => (
          <li key={e.id} className="sv-ev conflict"><b>{e.kind_title}</b> · {e.actor_name}{e.offline ? " · без связи" : ""} · {dt(e.received_at)}
            <div>{e.reason}</div>
            {act.has("resolve") && <div className="row">
              <button className="btn" disabled={busy} onClick={() => send("resolve", { event_id: e.id, decision: "accept" })}>Принять действие</button>
              <button className="btn ghost" disabled={busy} onClick={() => send("resolve", { event_id: e.id, decision: "dismiss" })}>Отклонить</button></div>}
            {!act.has("resolve") && <div className="muted">Ждёт решения руководителя сервиса.</div>}
          </li>))}</ul></>}

      <h3>Площадка и машина</h3>
      <dl className="sv-dl">
        <dt>Площадка</dt><dd>{o.site ? <>{o.site.name}{o.site.address && <div className="muted">{o.site.address}</div>}
          {o.site.map_url && <a href={o.site.map_url} target="_blank" rel="noreferrer">Открыть в Яндекс Картах</a>}</> : "не указана"}</dd>
        {o.site?.access_mode_title && <><dt>Допуск</dt><dd>{o.site.access_mode_title}</dd></>}
        {o.site?.hse_notes && <><dt>Охрана труда</dt><dd className="cs-hse">{o.site.hse_notes}</dd></>}
        <dt>Машина</dt><dd>{o.machine ? [o.machine.manufacturer, o.machine.model, o.machine.serial && `зав. № ${o.machine.serial}`].filter(Boolean).join(" · ") : "не указана"}
          {o.machine?.warranty_until && <div className="muted">гарантия до {fmtDate(o.machine.warranty_until)}</div>}</dd>
        <dt>Работы</dt><dd>{o.work_type_name || "—"}{o.work_type_name && <span className="muted"> · норматив {hrs(o.norm_hours)}</span>}</dd>
        {o.description && <><dt>Описание</dt><dd className="sv-pre">{o.description}</dd></>}
      </dl>

      <h3>Исполнитель и сроки</h3>
      <dl className="sv-dl">
        <dt>Инженер</dt><dd>{o.engineer_name || <span className="muted">не назначен</span>}{o.assigned_by_name && <span className="muted"> · назначил {o.assigned_by_name}</span>}</dd>
        <dt>План</dt><dd>{o.planned_start ? `${dt(o.planned_start)} — ${dt(o.planned_end)}` : <span className="muted">не запланирован</span>}</dd>
        {o.signed_by_name && <><dt>Подпись клиента</dt><dd>{o.signed_by_name}{o.closed_at && <span className="muted"> · закрыт {dt(o.closed_at)}</span>}</dd></>}
      </dl>
      {act.has("assign") && <div className="grid cols-3 sv-assign">
        <label className="f">Назначить инженера<select value={o.engineer_id || ""} disabled={busy} onChange={(e) => e.target.value && send("assign", { engineer_id: e.target.value })}>
          <option value="">— выберите —</option>
          {engs.map((e) => <option key={e.id} value={e.id}>{e.name}{e.is_engineer ? "" : " (руководитель)"} · открытых {e.open_orders}</option>)}</select></label>
        <label className="f">Начало<input type="datetime-local" value={plan.start} onChange={(e) => setPlan({ ...plan, start: e.target.value })} /></label>
        <label className="f">Окончание<input type="datetime-local" value={plan.end} onChange={(e) => setPlan({ ...plan, end: e.target.value })} /></label>
        <div className="row"><button className="btn ghost" disabled={busy || (plan.start === local(o.planned_start) && plan.end === local(o.planned_end))}
          onClick={() => send("plan", { planned_start: iso(plan.start), planned_end: iso(plan.end) })}>Сохранить сроки</button></div>
      </div>}

      <h3>Чек-лист{items.length > 0 && <span className="muted"> · {items.filter((i) => i.done).length} из {items.length}</span>}</h3>
      {items.length === 0 ? <p className="muted">Пунктов нет — у вида работ не задан шаблон.</p> : (
        <ul className="sv-check">{items.map((i) => (
          <li key={i.id} className={i.done ? "done" : ""}>
            <label><input type="checkbox" checked={i.done} disabled={busy || !act.has("checklist")} onChange={(e) => send("checklist", { item_id: i.id, done: e.target.checked })} />
              <span>{i.text}{i.required && !i.done && <span className="sv-req" aria-label="обязательный"> *</span>}</span></label>
            {i.done && i.done_by_name && <div className="pc-sub">{i.done_by_name} · {dt(i.done_at)}</div>}
          </li>))}</ul>)}

      {act.has("note") && <>
        <h3>Заметка</h3>
        <textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Что сделано, что обнаружено, что нужно" aria-label="Текст заметки" />
        <div className="row"><button className="btn ghost" disabled={busy || !note.trim()} onClick={async () => { await send("note", { text: note.trim() }); setNote(""); }}>Добавить заметку</button></div></>}

      <h3>История</h3>
      <ul className="sv-events">{[...(o.events || [])].reverse().map((e) => (
        <li key={e.id} className={`sv-ev ${e.result}`}>
          <b>{e.kind_title}</b>{e.kind === "note" && <span className="sv-pre"> — {String(e.payload.text || "")}</span>}
          <div className="pc-sub">{e.actor_name || "—"} · {dt(e.received_at)}{e.offline ? " · без связи" : ""}
            {e.result !== "applied" && <span className={`badge ${e.result === "conflict" ? "warn" : e.result === "rejected" ? "" : "info"}`}>{RESULT[e.result]}</span>}</div>
          {e.reason && <div className="muted">{e.reason}</div>}
        </li>))}</ul>
    </div>);
}

const local = (s: string | null | undefined) => {
  if (!s) return "";
  const d = new Date(s); const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
};
const iso = (s: string) => (s ? new Date(s).toISOString() : null);

/* ─────────────── обращения ─────────────── */

type Opt = { id: string; name: string };
const REQ_EMPTY = { client_id: "", site_id: "", machine_id: "", channel: "phone", kind: "repair", sla_code: "", description: "", machine_down: false };

function RequestsTab({ me }: { me: Me }) {
  const nav = useNavigate();
  const [status, setStatus] = useState("new");
  const [q, setQ] = useState("");
  const [items, setItems] = useState<Req[] | null>(null);
  const [refs, setRefs] = useState<Refs | null>(null);
  const [form, setForm] = useState<typeof REQ_EMPTY | null>(null);
  const [msg, setMsg] = useState("");
  const [mk, setMk] = useState<{ req: Req; wt: string } | null>(null);
  const edit = can(me, "service_orders", 2);
  const load = () => api.get<{ items: Req[] }>(`/api/service/requests?limit=300${status ? `&status=${status}` : ""}${q.trim() ? `&q=${encodeURIComponent(q.trim())}` : ""}`)
    .then((r) => setItems(r.items)).catch((e) => setMsg((e as Error).message));
  useEffect(() => { load(); }, [status]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (can(me, "service_refs")) api.get<Refs>("/api/service/refs").then(setRefs).catch(() => setRefs(null)); }, [me]);
  const reject = async (r: Req) => {
    const reason = await askPrompt(`Почему обращение ${r.number} отклоняется?`, "", { title: "Отклонить обращение" });
    if (!reason?.trim()) return;
    try { await api.post(`/api/service/requests/${r.id}/reject`, { reason: reason.trim() }); setMsg(`Обращение ${r.number} отклонено`); load(); }
    catch (e) { setMsg((e as Error).message); }
  };
  const makeOrder = async () => {
    if (!mk) return;
    try {
      const o = await api.post<Order>("/api/service/orders", { request_id: mk.req.id, work_type_id: mk.wt || null });
      setMk(null); nav(`/service?o=${o.id}`);
    } catch (e) { setMsg((e as Error).message); }
  };
  return (
    <div>
      <div className="pc-toolbar sv-toolbar">
        <div className="pc-groups" role="group" aria-label="Статус обращения">
          {[["new", "Новые"], ["accepted", "Принятые"], ["done", "Выполненные"], ["rejected", "Отклонённые"], ["", "Все"]].map(([k, t]) => (
            <button key={k || "all"} type="button" className={status === k ? "on" : ""} aria-pressed={status === k} onClick={() => setStatus(k)}>{t}</button>))}
        </div>
        <form className="row sv-search" onSubmit={(e) => { e.preventDefault(); load(); }}>
          <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Номер или текст" aria-label="Поиск обращений" />
          <button className="btn ghost">Найти</button></form>
        {edit && !form && <button className="btn add primary" onClick={() => setForm({ ...REQ_EMPTY })}>Обращение</button>}
      </div>
      {msg && <div className="alert info" role="status">{msg}</div>}
      {form && refs && <RequestForm refs={refs} form={form} setForm={setForm} onDone={(n) => { setForm(null); setMsg(`Обращение ${n} заведено`); setStatus("new"); load(); }} />}
      {mk && <div className="card sv-mk">
        <h3>Наряд по обращению {mk.req.number}</h3>
        <label className="f">Вид работ (шаблон чек-листа и норматив)<select value={mk.wt} onChange={(e) => setMk({ ...mk, wt: e.target.value })}>
          <option value="">без вида работ</option>
          {(refs?.work_types || []).filter((w) => w.is_active).map((w) => <option key={w.id} value={w.id}>{w.name}{w.is_draft ? " (черновик)" : ""} · {hrs(w.norm_hours)}</option>)}</select></label>
        <div className="row"><button className="btn primary" onClick={makeOrder}>Создать наряд</button><button className="btn ghost" onClick={() => setMk(null)}>отмена</button></div>
      </div>}
      {!items ? <Loading /> : items.length === 0 ? <Empty title="Обращений нет" hint={edit ? "Обращение клиента заводится кнопкой «Обращение»: клиент, площадка, машина, что случилось." : undefined} /> : (
        <table className="pc-table nv-cards sv-table">
          <thead><tr><th>Обращение</th><th>Клиент · площадка</th><th>Что случилось</th><th>Статус</th><th /></tr></thead>
          <tbody>{items.map((r) => (
            <tr key={r.id}>
              <td className="pc-art">{r.number}<div className="pc-sub">{dt(r.received_at)} · {refs?.channels[r.channel] || r.channel}</div></td>
              <td data-label="Клиент · площадка"><div className="pc-name">{r.client_name}</div><div className="pc-sub">{[r.site_name, r.machine_title].filter(Boolean).join(" · ") || "площадка не указана"}</div></td>
              <td data-label="Что случилось">{r.machine_down && <span className="badge bad">машина стоит</span>} {r.sla_code && <span className="badge">{r.sla_code}</span>}
                <div className="sv-clamp">{r.description}</div>{r.reject_reason && <div className="muted">Причина: {r.reject_reason}</div>}</td>
              <td data-label="Статус"><span className="badge">{r.status_title}</span>
                {r.orders.map((o) => <div key={o.id}><Link to={`/service?o=${o.id}`}>{o.number}</Link></div>)}</td>
              <td>{edit && (r.status === "new" || r.status === "accepted") && <div className="row sv-rowact">
                <button className="btn ghost" onClick={() => setMk({ req: r, wt: "" })}>Создать наряд</button>
                {r.status === "new" && <button className="btn ghost" onClick={() => reject(r)}>Отклонить</button>}</div>}</td>
            </tr>))}</tbody>
        </table>)}
    </div>);
}

function RequestForm({ refs, form, setForm, onDone }: { refs: Refs; form: typeof REQ_EMPTY; setForm: (f: typeof REQ_EMPTY | null) => void; onDone: (number: string) => void }) {
  const [clients, setClients] = useState<Opt[]>([]);
  const [sites, setSites] = useState<Opt[]>([]);
  const [machines, setMachines] = useState<{ id: string; title: string; site_id: string | null }[]>([]);
  const [err, setErr] = useState("");
  useEffect(() => { api.get<Opt[]>("/api/refs/clients").then(setClients).catch(() => setClients([])); }, []);
  useEffect(() => {
    if (!form.client_id) { setSites([]); setMachines([]); return; }
    api.get<{ items: Opt[] }>(`/api/clients/sites?client_id=${form.client_id}`).then((r) => setSites(r.items)).catch(() => setSites([]));
    api.get<{ id: string; manufacturer: string; model: string; serial: string; site_id: string | null }[]>(`/api/parts/fleet/machines?client_id=${form.client_id}`)
      .then((r) => setMachines(r.map((m) => ({ id: m.id, site_id: m.site_id, title: [m.manufacturer, m.model, m.serial].filter(Boolean).join(" · ") || "машина" }))))
      .catch(() => setMachines([]));
  }, [form.client_id]);
  const ms = machines.filter((m) => !form.site_id || !m.site_id || m.site_id === form.site_id);
  const save = async () => {
    setErr("");
    try {
      const r = await api.post<Req>("/api/service/requests", { ...form, site_id: form.site_id || null, machine_id: form.machine_id || null });
      onDone(r.number);
    } catch (e) { setErr((e as Error).message); }
  };
  const dirty = form.description.trim() || form.client_id;
  return (
    <div className="card sv-form">
      <h3>Новое обращение</h3>
      {err && <div className="alert error">{err}</div>}
      <div className="grid cols-3">
        <label className="f">Клиент *<select value={form.client_id} onChange={(e) => setForm({ ...form, client_id: e.target.value, site_id: "", machine_id: "" })}>
          <option value="">— выберите —</option>{clients.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
        <label className="f">Площадка<select value={form.site_id} disabled={!form.client_id} onChange={(e) => setForm({ ...form, site_id: e.target.value, machine_id: "" })}>
          <option value="">{!form.client_id ? "сначала выберите клиента" : sites.length ? "— не указана —" : "у клиента нет площадок"}</option>{sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
        <label className="f">Машина<select value={form.machine_id} disabled={!form.client_id} onChange={(e) => setForm({ ...form, machine_id: e.target.value })}>
          <option value="">{!form.client_id ? "сначала выберите клиента" : ms.length ? "— не указана —" : "машин в парке нет"}</option>{ms.map((m) => <option key={m.id} value={m.id}>{m.title}</option>)}</select></label>
        <label className="f">Канал<select value={form.channel} onChange={(e) => setForm({ ...form, channel: e.target.value })}>
          {Object.entries(refs.channels).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select></label>
        <label className="f">Вид<select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
          {Object.entries(refs.categories).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select></label>
        <label className="f">Срок реакции<select value={form.sla_code} onChange={(e) => setForm({ ...form, sla_code: e.target.value })}>
          <option value="">— не задан —</option>{refs.sla.filter((s) => s.is_active).map((s) => <option key={s.id} value={s.code}>{s.code} · {s.name}</option>)}</select></label>
        <label className="f cs-wide">Что случилось *<textarea rows={3} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })}
          placeholder="Признаки неисправности, коды ошибок, моточасы, кто сообщил" /></label>
        <label className="row sv-down"><input type="checkbox" checked={form.machine_down} onChange={(e) => setForm({ ...form, machine_down: e.target.checked })} /> машина стоит</label>
      </div>
      <div className="row">
        <button className="btn primary" disabled={!form.client_id || !form.description.trim()} onClick={save}>Завести обращение</button>
        <button className="btn ghost" onClick={async () => { if (!dirty || await askConfirm("Закрыть форму? Введённое не сохранится.")) setForm(null); }}>отмена</button>
      </div>
    </div>);
}

/* ─────────────── справочники ─────────────── */

const KIND_TITLE: Record<string, string> = { work_types: "Виды работ и нормативы", permits: "Допуски исполнителей", sla: "Сроки реакции" };

function RefsTab({ me }: { me: Me }) {
  const [refs, setRefs] = useState<Refs | null>(null);
  const [kind, setKind] = useState<"work_types" | "permits" | "sla">("work_types");
  const [ed, setEd] = useState<Ref | null>(null);
  const [msg, setMsg] = useState("");
  const engOnly = me.roles.length > 0 && me.roles.every((r) => r.code === "engineer");
  const editor = can(me, "service_refs", 2) && !engOnly;
  const load = () => api.get<Refs>("/api/service/refs").then(setRefs).catch((e) => setMsg((e as Error).message));
  useEffect(() => { load(); }, []);
  if (!refs) return msg ? <ErrorState hint={msg} onRetry={load} /> : <Loading />;
  const rows = refs[kind];
  const save = async (r: Ref, patch: Partial<Ref> = {}) => {
    const body = { ...r, ...patch };
    try { await api.put(`/api/service/refs/${kind}/${r.id}`, body); setMsg(patch.is_draft === false ? `«${r.name}» утверждено` : "Сохранено"); setEd(null); load(); }
    catch (e) { setMsg((e as Error).message); }
  };
  return (
    <div>
      {refs.drafts > 0 && <div className="alert warn">Черновик: {refs.drafts} записей ждут проверки руководителем сервиса. Нормативы и сроки — ориентир для обсуждения; пустое значение — «не задано», а не ноль.</div>}
      {msg && <div className="alert info" role="status">{msg}</div>}
      <div className="pc-groups" role="group" aria-label="Справочник">
        {(["work_types", "permits", "sla"] as const).map((k) => (
          <button key={k} type="button" className={kind === k ? "on" : ""} aria-pressed={kind === k} onClick={() => { setKind(k); setEd(null); }}>
            {KIND_TITLE[k]} · {refs[k].length}</button>))}
      </div>
      {ed && <RefEdit kind={kind} r={ed} refs={refs} onSave={(r) => save(r)} onCancel={() => setEd(null)} />}
      <table className="pc-table nv-cards sv-table">
        <thead><tr><th>Код</th><th>Название</th><th>{kind === "work_types" ? "Норматив · бригада · допуски" : kind === "permits" ? "Срок действия" : "Реакция · выезд · восстановление"}</th><th>Состояние</th><th /></tr></thead>
        <tbody>{rows.map((r) => (
          <tr key={r.id} className={r.is_active ? "" : "arch"}>
            <td className="pc-art">{r.code}</td>
            <td data-label="Название"><div className="pc-name">{r.name}</div>
              {kind === "work_types" && <div className="pc-sub">{refs.categories[r.category || ""] || r.category}{r.machine_kind ? ` · ${r.machine_kind}` : " · любая техника"} · пунктов чек-листа: {r.checklist?.length || 0}</div>}</td>
            <td data-label="Значения">{kind === "work_types" ? <>{hrs(r.norm_hours)} · {r.crew_size} чел.<div className="pc-sub pc-mono">{(r.permit_codes || []).join(", ") || "без допусков"}</div></>
              : kind === "permits" ? (r.valid_months ? `${r.valid_months} мес.` : "бессрочно")
              : <>{hrs(r.reaction_hours)} · {hrs(r.arrival_hours)} · {hrs(r.restore_hours)}<div className="pc-sub">{r.calendar === "business" ? "рабочие часы" : "круглосуточно"}</div></>}</td>
            <td data-label="Состояние">{r.is_draft ? <span className="badge warn">черновик</span> : <span className="badge ok">утверждено</span>}{!r.is_active && <span className="badge">не используется</span>}</td>
            <td>{editor && <div className="row sv-rowact">
              {r.is_draft && <button className="btn ghost" onClick={() => save(r, { is_draft: false })}>Утвердить</button>}
              <button className="btn ghost" onClick={() => setEd(r)}>изменить</button></div>}</td>
          </tr>))}</tbody>
      </table>
    </div>);
}

const numOrNull = (s: string) => (s.trim() === "" ? null : Number(s.replace(",", ".")));

function RefEdit({ kind, r, refs, onSave, onCancel }: { kind: string; r: Ref; refs: Refs; onSave: (r: Ref) => void; onCancel: () => void }) {
  const [f, setF] = useState({ ...r, _norm: r.norm_hours?.toString() ?? "", _months: r.valid_months?.toString() ?? "",
    _r: r.reaction_hours?.toString() ?? "", _a: r.arrival_hours?.toString() ?? "", _x: r.restore_hours?.toString() ?? "", _check: (r.checklist || []).join("\n") });
  const out = (): Ref => ({ ...r, name: f.name, comment: f.comment, is_active: f.is_active, category: f.category, machine_kind: f.machine_kind, crew_size: f.crew_size,
    permit_codes: f.permit_codes, calendar: f.calendar, norm_hours: numOrNull(f._norm), valid_months: numOrNull(f._months),
    reaction_hours: numOrNull(f._r), arrival_hours: numOrNull(f._a), restore_hours: numOrNull(f._x),
    checklist: f._check.split("\n").map((s) => s.trim()).filter(Boolean) });
  return (
    <div className="card sv-form">
      <h3>{r.code} · {r.is_draft ? "черновик" : "утверждено"}</h3>
      <div className="grid cols-3">
        <label className="f cs-wide">Название<input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
        {kind === "work_types" && <>
          <label className="f">Категория<select value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>
            {Object.entries(refs.categories).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select></label>
          <label className="f">Тип техники<input value={f.machine_kind} onChange={(e) => setF({ ...f, machine_kind: e.target.value })} placeholder="пусто — любая" /></label>
          <label className="f">Норматив, ч<input inputMode="decimal" value={f._norm} onChange={(e) => setF({ ...f, _norm: e.target.value })} placeholder="не задано" /></label>
          <label className="f">Бригада, чел.<input type="number" min={1} value={f.crew_size} onChange={(e) => setF({ ...f, crew_size: Number(e.target.value) || 1 })} /></label>
          <fieldset className="f cs-wide sv-permits"><legend>Нужные допуски</legend>
            {refs.permits.map((p) => <label key={p.code} className="row"><input type="checkbox" checked={(f.permit_codes || []).includes(p.code)}
              onChange={(e) => setF({ ...f, permit_codes: e.target.checked ? [...(f.permit_codes || []), p.code] : (f.permit_codes || []).filter((c) => c !== p.code) })} /> {p.code} · {p.name}</label>)}
          </fieldset>
          <label className="f cs-wide">Чек-лист — по пункту в строке<textarea rows={5} value={f._check} onChange={(e) => setF({ ...f, _check: e.target.value })} /></label>
        </>}
        {kind === "permits" && <label className="f">Срок действия, мес.<input inputMode="numeric" value={f._months} onChange={(e) => setF({ ...f, _months: e.target.value })} placeholder="бессрочно" /></label>}
        {kind === "sla" && <>
          <label className="f">Реакция, ч<input inputMode="decimal" value={f._r} onChange={(e) => setF({ ...f, _r: e.target.value })} placeholder="не задано" /></label>
          <label className="f">Выезд, ч<input inputMode="decimal" value={f._a} onChange={(e) => setF({ ...f, _a: e.target.value })} placeholder="не задано" /></label>
          <label className="f">Восстановление, ч<input inputMode="decimal" value={f._x} onChange={(e) => setF({ ...f, _x: e.target.value })} placeholder="не задано" /></label>
          <label className="f">Календарь<select value={f.calendar} onChange={(e) => setF({ ...f, calendar: e.target.value })}>
            <option value="24x7">круглосуточно</option><option value="business">рабочие часы</option></select></label>
        </>}
        <label className="f cs-wide">Комментарий<input value={f.comment} onChange={(e) => setF({ ...f, comment: e.target.value })} /></label>
        <label className="row"><input type="checkbox" checked={f.is_active} onChange={(e) => setF({ ...f, is_active: e.target.checked })} /> используется</label>
      </div>
      <div className="row"><button className="btn primary" disabled={!f.name.trim()} onClick={() => onSave(out())}>Сохранить</button>
        <button className="btn ghost" onClick={onCancel}>отмена</button></div>
    </div>);
}
