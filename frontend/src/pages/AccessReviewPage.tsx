import { FormEvent, useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api";
import OffboardDialog from "../components/OffboardDialog";
import SecurityModal from "../components/SecurityModal";

/** «Настройки → Пересмотр прав» (docs/operations/10, 04 §6, 13 §10): ежеквартальная проверка прав каждого сотрудника.
 *  Решения: оставить / изменено (права уже изменены — разница фиксируется) / отключить (единая операция) / пропуск по решению владельца. */
type Progress = { total: number; done: number; percent: number; by_decision: Record<string, number>; overdue: boolean; days_left: number; long_overrides: number; second_reviewer_needed: number };
type Campaign = { id: string; title: string; period_start: string; period_end: string; due_date: string; status: string; scope: { roles?: string[] }; completed_at: string | null; completion_note: string; progress: Progress };
type Override = { group: string; level: number; valid_to: string | null; comment: string; active: boolean; long: boolean };
type Snap = { display_name: string; email: string; is_active: boolean; roles: { code: string; title: string }[]; perms: Record<string, number>; elevated_by_override: string[];
  sensitive: Record<string, number>; overrides: Override[]; deputy_for: string[]; deputy: { from: string | null; to: string | null } | null; mfa_enabled: boolean; last_seen_at: string | null; inactive_days: number | null };
type Item = { id: string; user_id: string; snapshot: Snap; current_diff: Record<string, unknown>; decision: string; reviewer: string; reviewed_at: string | null; comment: string; is_self: boolean; second_reviewer_needed: boolean };
type Detail = Campaign & { items: Item[]; can_edit: boolean; group_titles: Record<string, string>; sensitive_groups: string[] };

const DEC: Record<string, [string, string]> = { pending: ["не решено", ""], keep: ["оставить", "ok"], change: ["изменено", "info"], disable: ["отключён", "bad"], skipped: ["пропуск", "warn"] };
const STATUS: Record<string, [string, string]> = { open: ["идёт", "info"], completed: ["завершена", "ok"], cancelled: ["отменена", ""], draft: ["черновик", ""] };
const ROLES: [string, string][] = [["manager", "Менеджер"], ["logistician", "Логист"], ["service_head", "Сервис"], ["cd", "КД"], ["fd", "ФД"], ["gd", "ЗГД"], ["admin", "Администратор"], ["observer", "Наблюдатель"], ["purchasing", "Закупки"], ["lawyer", "Юрист"]];
const d = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString("ru-RU") : "—");
const errText = (e: unknown) => (e instanceof ApiError ? e.message : "Не удалось выполнить действие");
const LVL = ["нет", "видит", "редактирует"];

function quarter() {
  const t = new Date(), q = Math.floor(t.getMonth() / 3);
  const start = new Date(t.getFullYear(), q * 3, 1), end = new Date(t.getFullYear(), q * 3 + 3, 0), due = new Date(t.getTime() + 14 * 864e5);
  const iso = (x: Date) => `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, "0")}-${String(x.getDate()).padStart(2, "0")}`;
  return { title: `Пересмотр прав — ${["I", "II", "III", "IV"][q]} кв. ${t.getFullYear()}`, period_start: iso(start), period_end: iso(end), due_date: iso(due) };
}

function NewCampaign({ onClose, onCreated }: { onClose: () => void; onCreated: (id: string) => void }) {
  const [f, setF] = useState({ ...quarter(), roles: [] as string[] });
  const [err, setErr] = useState(""); const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault(); setBusy(true); setErr("");
    try { onCreated((await api.post<Campaign>("/api/security/access-reviews", f)).id); } catch (x) { setErr(errText(x)); } finally { setBusy(false); }
  }
  return (
    <SecurityModal title="Новый пересмотр прав" onClose={onClose} width={560}>
      <form onSubmit={submit}>
        <label className="sec-field">Название<input value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></label>
        <div className="row">
          <label className="sec-field">Период с<input type="date" value={f.period_start} onChange={(e) => setF({ ...f, period_start: e.target.value })} /></label>
          <label className="sec-field">по<input type="date" value={f.period_end} onChange={(e) => setF({ ...f, period_end: e.target.value })} /></label>
          <label className="sec-field">Срок проверки<input type="date" value={f.due_date} onChange={(e) => setF({ ...f, due_date: e.target.value })} /></label>
        </div>
        <fieldset className="ar-roles"><legend className="muted">Кого проверять (пусто — всех активных)</legend>
          {ROLES.map(([code, title]) => <label key={code} className="row"><input type="checkbox" checked={f.roles.includes(code)}
            onChange={(e) => setF({ ...f, roles: e.target.checked ? [...f.roles, code] : f.roles.filter((r) => r !== code) })} /> {title}</label>)}
        </fieldset>
        <p className="muted" style={{ fontSize: 13 }}>Права каждого сотрудника будут зафиксированы на момент создания — потом видно, что изменилось.</p>
        {err && <div className="alert error" role="alert">{err}</div>}
        <div className="row" style={{ justifyContent: "flex-end" }}><button type="button" className="btn secondary" onClick={onClose}>Отмена</button><button className="btn" disabled={busy}>{busy ? "…" : "Создать"}</button></div>
      </form>
    </SecurityModal>
  );
}

function ItemRow({ it, c, onChanged }: { it: Item; c: Detail; onChanged: () => void }) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<"change" | "skipped" | null>(null);
  const [comment, setComment] = useState("");
  const [err, setErr] = useState("");
  const [off, setOff] = useState(false);
  const s = it.snapshot, title = (g: string) => c.group_titles[g] || g;
  const longOv = s.overrides.filter((o) => o.active && o.long).length;
  const diffKeys = Object.keys(it.current_diff || {});
  const canAct = c.can_edit && c.status === "open" && !it.second_reviewer_needed;
  async function decide(decision: string, text = "") {
    setErr("");
    try { await api.post(`/api/security/access-reviews/${c.id}/items/${it.id}/decision`, { decision, comment: text }); setMode(null); setComment(""); onChanged(); }
    catch (x) { setErr(errText(x)); }
  }
  return (
    <>
      <tr className={open ? "ar-open" : ""}>
        <td><button className="btn ghost ar-name" onClick={() => setOpen(!open)} aria-expanded={open}>{open ? "▾" : "▸"} {s.display_name}</button><div className="muted" style={{ fontSize: 12 }}>{s.email}</div></td>
        <td style={{ fontSize: 13 }}>{s.roles.map((r) => r.title).join(", ")}</td>
        <td className="ar-flags">
          {s.elevated_by_override.length > 0 && <span className="badge warn">сверх роли: {s.elevated_by_override.length}</span>}
          {Object.keys(s.sensitive).length > 0 && <span className="badge info">особые права</span>}
          {longOv > 0 && <span className="badge warn">бессрочные исключения: {longOv}</span>}
          {!s.mfa_enabled && <span className="badge">без кода</span>}
          {s.inactive_days != null && s.inactive_days >= 30 && <span className="badge bad">не заходил {s.inactive_days} дн.</span>}
          {s.last_seen_at == null && <span className="badge">не входил</span>}
          {diffKeys.length > 0 && <span className="badge info">изменилось с начала</span>}
        </td>
        <td><span className={`badge ${DEC[it.decision][1]}`}>{DEC[it.decision][0]}</span>{it.reviewer && <div className="muted" style={{ fontSize: 12 }}>{it.reviewer}, {d(it.reviewed_at)}</div>}</td>
        <td className="ar-actions">
          {it.second_reviewer_needed && it.decision === "pending" && <span className="muted" style={{ fontSize: 12 }}>нужен другой проверяющий</span>}
          {canAct && it.decision === "pending" && <>
            <button className="btn secondary" onClick={() => decide("keep")}>Оставить</button>
            <button className="btn secondary" onClick={() => { setOpen(true); setMode("change"); }}>Изменено</button>
            <button className="btn secondary" onClick={() => (s.is_active ? setOff(true) : decide("disable"))}>Отключить</button>
          </>}
          {canAct && it.decision !== "pending" && <button className="btn ghost" onClick={async () => { try { await api.post(`/api/security/access-reviews/${c.id}/items/${it.id}/reopen`); onChanged(); } catch (x) { setErr(errText(x)); } }}>Пересмотреть</button>}
        </td>
      </tr>
      {(open || err) && <tr className="ar-detail"><td colSpan={5}>
        {err && <div className="alert error" role="alert">{err}</div>}
        {open && <div className="ar-grid">
          <div><h4>Права на начало проверки</h4>
            <ul className="ar-list">{Object.entries(s.perms).map(([g, l]) => <li key={g} className={c.sensitive_groups.includes(g) ? "sens" : ""}>{title(g)} — {LVL[l]}{s.elevated_by_override.includes(g) && <b> (исключение)</b>}</li>)}</ul></div>
          <div><h4>Исключения и замещения</h4>
            {s.overrides.length === 0 ? <p className="muted">Исключений нет.</p> : <ul className="ar-list">{s.overrides.map((o, i) => <li key={i} className={o.active ? "" : "muted"}>{title(o.group)} — {LVL[o.level]}, {o.valid_to ? `до ${d(o.valid_to)}` : "бессрочно"}{o.comment && ` · ${o.comment}`}</li>)}</ul>}
            {s.deputy_for.length > 0 && <p>Замещает: {s.deputy_for.join(", ")}</p>}
            <p className="muted">Код из приложения: {s.mfa_enabled ? "включён" : "нет"} · последний вход: {d(s.last_seen_at)}</p>
            {diffKeys.length > 0 && <div className="alert info">С начала проверки изменилось: {diffKeys.map((k) => ({ perms: "права", roles: "роли", overrides: "исключения", observer_scope: "область наблюдения", is_active: "доступ" } as Record<string, string>)[k] || k).join(", ")}</div>}
            {it.comment && <p><b>Комментарий:</b> {it.comment}</p>}
          </div>
        </div>}
        {mode === "change" && <div className="ar-form">
          <p style={{ marginTop: 0 }}>Сначала измените права в <Link to="/settings?s=access">«Уровни доступа»</Link> (роль или исключение для сотрудника), затем опишите здесь, что изменено.
            Без реального изменения отметка не сохранится.</p>
          <textarea rows={2} value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Например: снят доступ к финансам — перешёл в логистику" aria-label="Что изменено" />
          <div className="row"><button className="btn" onClick={() => decide("change", comment)} disabled={!comment.trim()}>Сохранить «Изменено»</button>
            <button className="btn ghost" onClick={() => setMode("skipped")}>Пропуск по решению владельца…</button><button className="btn ghost" onClick={() => setMode(null)}>Отмена</button></div>
        </div>}
        {mode === "skipped" && <div className="ar-form">
          <textarea rows={2} value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Кто из руководства решил и почему (например: владелец — сотрудник в отпуске, проверим в мае)" aria-label="Причина пропуска" />
          <div className="row"><button className="btn" onClick={() => decide("skipped", comment)} disabled={comment.trim().length < 10}>Отметить пропуск</button><button className="btn ghost" onClick={() => setMode(null)}>Отмена</button></div>
        </div>}
      </td></tr>}
      {off && <OffboardDialog user={{ id: it.user_id, display_name: s.display_name }} mode="offboard" onClose={() => setOff(false)} onDone={() => decide("disable")} />}
    </>
  );
}

function CampaignView({ id, onBack }: { id: string; onBack: () => void }) {
  const [c, setC] = useState<Detail | null>(null);
  const [err, setErr] = useState("");
  const [onlyPending, setOnlyPending] = useState(true);
  const [closing, setClosing] = useState<"complete" | "cancel" | null>(null);
  const [note, setNote] = useState("");
  const load = useCallback(async () => { try { setC(await api.get<Detail>(`/api/security/access-reviews/${id}`)); setErr(""); } catch (x) { setErr(errText(x)); } }, [id]);
  useEffect(() => { load(); }, [load]);
  if (!c) return <div className="card">{err ? <div className="alert error">{err}</div> : <span className="muted">Загрузка…</span>}</div>;
  const p = c.progress;
  const items = c.items.filter((i) => !onlyPending || i.decision === "pending");
  async function close() {
    try { await api.post(`/api/security/access-reviews/${c!.id}/${closing}`, { note }); setClosing(null); setNote(""); load(); } catch (x) { setErr(errText(x)); }
  }
  return (
    <div className="card">
      <button className="btn ghost" onClick={onBack}>← Все проверки</button>
      <div className="row" style={{ justifyContent: "space-between", marginTop: 6 }}>
        <h2 style={{ margin: 0 }}>{c.title} <span className={`badge ${STATUS[c.status][1]}`}>{STATUS[c.status][0]}</span> {p.overdue && <span className="badge bad">просрочена</span>}</h2>
        {c.can_edit && c.status === "open" && <div className="row"><button className="btn" onClick={() => setClosing("complete")} disabled={p.by_decision.pending > 0}>Завершить</button><button className="btn ghost" onClick={() => setClosing("cancel")}>Отменить</button></div>}
      </div>
      <p className="muted">Период {d(c.period_start)}–{d(c.period_end)} · срок {d(c.due_date)}{c.status === "open" && (p.days_left >= 0 ? ` (через ${p.days_left} дн.)` : ` (просрочено на ${-p.days_left} дн.)`)}</p>
      <div className="ar-progress" role="progressbar" aria-valuenow={p.percent} aria-valuemin={0} aria-valuemax={100} aria-label="Проверено"><div style={{ width: `${p.percent}%` }} /></div>
      <p>Проверено <b>{p.done}</b> из {p.total} ({p.percent}%) · оставить {p.by_decision.keep} · изменено {p.by_decision.change} · отключено {p.by_decision.disable} · пропуск {p.by_decision.skipped}
        {p.long_overrides > 0 && <> · <span className="badge warn">бессрочные/долгие исключения: {p.long_overrides}</span></>}</p>
      {p.second_reviewer_needed > 0 && c.status === "open" && <div className="alert info">У {p.second_reviewer_needed} сотрудн. повышенные права: свои такие права проверяющий не подтверждает — нужен другой администратор безопасности.</div>}
      {c.completion_note && <p><b>{c.status === "completed" ? "Итог" : "Причина отмены"}:</b> {c.completion_note}</p>}
      {err && <div className="alert error" role="alert">{err}</div>}
      <label className="row" style={{ margin: "8px 0" }}><input type="checkbox" checked={onlyPending} onChange={(e) => setOnlyPending(e.target.checked)} /> только нерешённые</label>
      {items.length > 0 && <div className="backup-table"><table className="ar-table">
        <thead><tr><th>Сотрудник</th><th>Роль</th><th>На что обратить внимание</th><th>Решение</th><th></th></tr></thead>
        <tbody>{items.map((it) => <ItemRow key={it.id} it={it} c={c} onChanged={load} />)}</tbody>
      </table></div>}
      {items.length === 0 && <p className="muted">{onlyPending ? "Все сотрудники проверены." : "Нет сотрудников."}</p>}
      {closing && <SecurityModal title={closing === "complete" ? "Завершить пересмотр" : "Отменить пересмотр"} onClose={() => setClosing(null)} width={460}>
        <label className="sec-field">{closing === "complete" ? "Итог (необязательно)" : "Причина отмены"}<textarea rows={3} value={note} onChange={(e) => setNote(e.target.value)} /></label>
        <div className="row" style={{ justifyContent: "flex-end" }}><button className="btn secondary" onClick={() => setClosing(null)}>Назад</button><button className="btn" onClick={close}>{closing === "complete" ? "Завершить" : "Отменить пересмотр"}</button></div>
      </SecurityModal>}
    </div>
  );
}

export default function AccessReviewPage() {
  const [items, setItems] = useState<Campaign[]>([]);
  const [canEdit, setCanEdit] = useState(false);
  const [err, setErr] = useState("");
  const [creating, setCreating] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const load = useCallback(async () => { try { const r = await api.get<{ items: Campaign[]; can_edit: boolean }>("/api/security/access-reviews"); setItems(r.items); setCanEdit(r.can_edit); } catch (x) { setErr(errText(x)); } }, []);
  useEffect(() => { load(); }, [load]);
  if (openId) return <CampaignView id={openId} onBack={() => { setOpenId(null); load(); }} />;
  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 style={{ margin: 0 }}>Пересмотр прав</h2>
        {canEdit && <button className="btn" onClick={() => setCreating(true)}>Новая проверка</button>}
      </div>
      <p className="muted">Раз в квартал: у каждого сотрудника проверить роль, исключения прав, замещения и давно не заходивших. Особое внимание — безопасность, финансы, юр. документы и копии.</p>
      {err && <div className="alert error" role="alert">{err}</div>}
      {items.length === 0 ? <p className="muted">Проверок ещё не было.</p> : <div className="backup-table"><table>
        <thead><tr><th>Проверка</th><th>Срок</th><th>Статус</th><th>Готово</th><th></th></tr></thead>
        <tbody>{items.map((c) => <tr key={c.id}>
          <td><b>{c.title}</b><div className="muted" style={{ fontSize: 12 }}>{d(c.period_start)}–{d(c.period_end)}{c.scope.roles?.length ? ` · роли: ${c.scope.roles.length}` : ""}</div></td>
          <td style={{ whiteSpace: "nowrap" }}>{d(c.due_date)}</td>
          <td><span className={`badge ${STATUS[c.status][1]}`}>{STATUS[c.status][0]}</span> {c.progress.overdue && <span className="badge bad">просрочена</span>}</td>
          <td style={{ minWidth: 140 }}><div className="ar-progress small"><div style={{ width: `${c.progress.percent}%` }} /></div><span style={{ fontSize: 13 }}>{c.progress.done} из {c.progress.total}</span></td>
          <td><button className="btn ghost" onClick={() => setOpenId(c.id)}>Открыть</button></td>
        </tr>)}</tbody>
      </table></div>}
      {creating && <NewCampaign onClose={() => setCreating(false)} onCreated={(id) => { setCreating(false); setOpenId(id); }} />}
    </div>
  );
}
