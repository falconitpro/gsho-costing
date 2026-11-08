import { useEffect, useState } from "react";
import { api, fmtDate } from "../api";
import type { Me } from "../types";
import { DeputyBlock, ObserverScopeBlock } from "../components/TeamExtras";

type Contacts = { position: string; department: string; mobile: string; work_phone: string; wechat: string; telegram: string; whatsapp: string; max: string; extra_email: string; note: string };
type Card = { id: string; display_name: string; email: string; roles: string[]; role_titles: string[]; contacts: Contacts; wecom_user_id: string; can_manage?: boolean; pending_changes?: { fields?: Record<string, [string, string]>; requested_at?: string };
  notify_channels: string[]; can_edit: boolean; can_channels: boolean; is_active: boolean;
  deputy_id: string | null; deputy_from: string | null; deputy_to: string | null; deputy_active?: boolean; observer_scope?: Record<string, unknown> };
type RoleT = { code: string; title: string };
type Perm = { group: string; title: string; level: number; level_title: string };
type Override = { id: string; user_id: string; group: string; level: number; valid_to: string | null; comment: string };
const LEVELS: Record<number, string> = { 0: "не видит", 1: "видит", 2: "редактирует" };
type Log = { at: string | null; channel: string; channel_title: string; text: string; status: string; error: string };

const FIELDS: [keyof Contacts, string, string][] = [
  ["position", "Должность", ""], ["department", "Подразделение", ""], ["mobile", "Мобильный телефон", "+7 900 000-00-00"], ["work_phone", "Рабочий телефон", ""],
  ["wechat", "WeChat ID", ""], ["telegram", "Telegram", "@имя"], ["whatsapp", "WhatsApp", "+7…"], ["max", "MAX", "+7…"], ["extra_email", "Доп. e-mail", ""], ["note", "Примечание", ""],
];
const FIELD_T: Record<string, string> = { display_name: "ФИО", wecom_user_id: "WeCom ID", position: "Должность", department: "Подразделение", mobile: "Мобильный", work_phone: "Рабочий телефон", wechat: "WeChat", telegram: "Telegram", whatsapp: "WhatsApp", max: "MAX", extra_email: "Доп. e-mail", note: "Примечание" };
const STATUS: Record<string, string> = { sent: "отправлено", queued: "ожидает подключения канала", failed: "ошибка", read: "прочитано" };

/** Сотрудники: карточки контактов исполнителей; каналы уведомлений о задачах выбирают КД, ЗГД или администратор. */
export default function TeamPage({ me, embedded = false }: { me: Me; embedded?: boolean }) {
  const [users, setUsers] = useState<Card[]>([]);
  const [channels, setChannels] = useState<Record<string, string>>({});
  const [cur, setCur] = useState<Card | null>(null);
  const [log, setLog] = useState<Log[]>([]);
  const [q, setQ] = useState("");
  const [msg, setMsg] = useState("");
  const [canManage, setCanManage] = useState(false);
  const [roles, setRoles] = useState<RoleT[]>([]);
  const [showNew, setShowNew] = useState(false);
  const [nu, setNu] = useState({ display_name: "", email: "", position: "", roles: [] as string[] });
  const [perms, setPerms] = useState<Perm[]>([]);
  const [overrides, setOverrides] = useState<Override[]>([]);
  const [ovr, setOvr] = useState({ group: "", level: 1, valid_to: "", comment: "" });
  const [err, setErr] = useState("");
  const load = () => api.get<{ users: Card[]; channels: Record<string, string>; can_manage?: boolean; roles?: RoleT[] }>("/api/team?include_inactive=true").then((r) => {
    setUsers(r.users); setChannels(r.channels); setCanManage(!!r.can_manage); setRoles(r.roles || []);
  });
  useEffect(() => { load(); }, []);
  const loadAccess = (uid: string) => {
    if (!canManage) return;
    api.get<{ perms: Perm[] }>(`/api/admin/access/preview?user_id=${uid}`).then((r) => setPerms(r.perms)).catch(() => setPerms([]));
    api.get<{ overrides: Override[] }>("/api/admin/access").then((r) => setOverrides(r.overrides.filter((o) => o.user_id === uid))).catch(() => setOverrides([]));
  };
  const open = (u: Card) => { setCur(JSON.parse(JSON.stringify(u))); setMsg(""); setErr(""); loadAccess(u.id); api.get<{ log: Log[] }>(`/api/team/${u.id}/notifications`).then((r) => setLog(r.log)).catch(() => setLog([])); };
  async function createUser() {
    try {
      const u = await api.post<{ id: string }>("/api/admin/users", { display_name: nu.display_name.trim(), email: nu.email.trim(), roles: nu.roles,
        contacts: nu.position ? { position: nu.position } : {}, notify_channels: ["app", "outlook"] });
      setMsg(`Сотрудник «${nu.display_name}» добавлен. Вход — по корпоративной учётной записи с e-mail ${nu.email}.`); setErr("");
      setShowNew(false); setNu({ display_name: "", email: "", position: "", roles: [] });
      await load();
      const r = await api.get<{ users: Card[] }>("/api/team?include_inactive=true"); const c = r.users.find((x) => x.id === u.id); if (c) open(c);
    } catch (e) { setErr((e as Error).message); }
  }
  async function saveRoles() {
    if (!cur) return;
    try {
      await api.patch(`/api/admin/users/${cur.id}`, { roles: cur.roles, is_active: cur.is_active, display_name: cur.display_name });
      setMsg("Роли и статус сохранены"); setErr(""); await load(); loadAccess(cur.id);
    } catch (e) { setErr((e as Error).message); }
  }
  async function addOverride() {
    if (!cur || !ovr.group) return;
    try {
      await api.post("/api/admin/access/overrides", { user_id: cur.id, group: ovr.group, level: Number(ovr.level), valid_to: ovr.valid_to || null, comment: ovr.comment });
      setMsg("Индивидуальный доступ установлен"); setErr(""); setOvr({ group: "", level: 1, valid_to: "", comment: "" }); loadAccess(cur.id);
    } catch (e) { setErr((e as Error).message); }
  }
  async function delOverride(id: string) {
    if (!cur) return;
    try { await api.del(`/api/admin/access/overrides/${id}`); loadAccess(cur.id); } catch (e) { setErr((e as Error).message); }
  }
  async function save() {
    if (!cur) return;
    try {
      let r = await api.put<Card & { applied: string[]; pending: string[] }>(`/api/team/${cur.id}/contacts`, { contacts: cur.contacts, wecom_user_id: cur.wecom_user_id, display_name: cur.display_name });
      const pend = r.pending || [];
      if (cur.can_channels) r = { ...r, ...(await api.put<Card>(`/api/team/${cur.id}/channels`, { channels: cur.notify_channels.filter((c) => c !== "app") })) };
      if (canManage && cur.email && cur.email !== users.find((u) => u.id === cur.id)?.email) { await api.patch(`/api/admin/users/${cur.id}`, { email: cur.email.trim() }); }
      setCur(r); setMsg(pend.length ? `Карточка сохранена; изменение заполненных полей (${pend.map((k) => FIELD_T[k] || k).join(", ")}) отправлено на подтверждение администратору / ЗГД` : "Карточка сохранена"); setErr(""); load();
    } catch (e) { setErr((e as Error).message); }
  }
  async function decide(approve: boolean, fields?: string[]) {
    if (!cur) return;
    try { const r = await api.post<Card>(`/api/team/${cur.id}/pending/decide`, { approve, fields }); setCur({ ...cur, ...r }); setMsg(approve ? "Изменения подтверждены" : "Изменения отклонены"); load(); }
    catch (e) { setErr((e as Error).message); }
  }
  async function removeUser() {
    if (!cur) return;
    if (!confirm(`Удалить сотрудника «${cur.display_name}»? Если он участвует в сделках или задачах, учётная запись будет отключена с сохранением истории.`)) return;
    try { const r = await api.del<{ message: string }>(`/api/admin/users/${cur.id}`); setMsg(r.message); setCur(null); load(); } catch (e) { setErr((e as Error).message); }
  }
  async function test() {
    if (!cur) return;
    const r = await api.post<{ log: Log[] }>(`/api/team/${cur.id}/test`); setLog(r.log); setMsg("Проверочное уведомление отправлено по всем каналам сотрудника");
  }
  const shown = users.filter((u) => !q || `${u.display_name} ${u.email} ${u.contacts.position} ${u.contacts.mobile}`.toLowerCase().includes(q.toLowerCase()));
  const setC = (k: keyof Contacts, v: string) => cur && setCur({ ...cur, contacts: { ...cur.contacts, [k]: v } });

  return (
    <>
      <div className="page-head">
        {!embedded && <h1>Сотрудники</h1>}
        <div className="push">
          <input placeholder="поиск: имя, должность, телефон" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 260, maxWidth: "100%" }} />
          {canManage && <button className="btn add" onClick={() => { setShowNew(!showNew); setErr(""); }}>Добавить сотрудника</button>}
        </div>
      </div>
      {showNew && canManage && (
        <div className="card">
          <h2 style={{ marginTop: 0 }}>Новый сотрудник</h2>
          <div className="grid" style={{ gridTemplateColumns: "repeat(3, minmax(0, 1fr))" }}>
            <label className="f">ФИО<input value={nu.display_name} placeholder="Фамилия Имя" onChange={(e) => setNu({ ...nu, display_name: e.target.value })} /></label>
            <label className="f">E-mail (логин)<input value={nu.email} placeholder="name@company.ru" onChange={(e) => setNu({ ...nu, email: e.target.value })} /></label>
            <label className="f">Должность<input value={nu.position} onChange={(e) => setNu({ ...nu, position: e.target.value })} /></label>
          </div>
          <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>Роли (можно несколько) — определяют доступ по умолчанию; индивидуальные доступы настраиваются в карточке сотрудника:</div>
          <div className="row" style={{ flexWrap: "wrap", gap: 6, marginTop: 4 }}>{roles.map((r) => (
            <label key={r.code} className="row" style={{ border: "1px solid " + (nu.roles.includes(r.code) ? "var(--accent)" : "var(--line)"), borderRadius: 6, padding: "4px 8px", cursor: "pointer" }}>
              <input type="checkbox" checked={nu.roles.includes(r.code)} onChange={() => setNu({ ...nu, roles: nu.roles.includes(r.code) ? nu.roles.filter((x) => x !== r.code) : [...nu.roles, r.code] })} /> {r.title}</label>))}</div>
          {err && <div className="alert error">{err}</div>}
          <div className="row" style={{ marginTop: 10 }}>
            <button className="btn" disabled={!nu.display_name.trim() || !nu.email.includes("@") || nu.roles.length === 0} onClick={createUser}>Добавить</button>
            <button className="btn ghost" onClick={() => setShowNew(false)}>Отмена</button>
          </div>
        </div>)}
      <p className="muted">При постановке задачи исполнитель сразу получает уведомление во все выбранные каналы. Каналы выбирают КД, ЗГД или администратор; контакты заполняет сам сотрудник. Каналы без подключённой интеграции отмечаются в журнале «ожидает подключения канала».</p>
      {msg && <div className="alert info">{msg}</div>}
      <div className="grid" style={{ gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1.2fr)" }}>
        <div className="card">
          <table><thead><tr><th>Сотрудник</th><th>Контакты</th><th>Уведомления</th></tr></thead>
            <tbody>{shown.map((u) => (
              <tr key={u.id} style={{ cursor: "pointer", background: cur?.id === u.id ? "var(--accent-soft)" : undefined }} onClick={() => open(u)}>
                <td style={{ opacity: u.is_active ? 1 : 0.5 }}><b>{u.display_name}</b>{!u.is_active && <span className="badge"> отключён</span>}<div className="muted" style={{ fontSize: 12 }}>{u.contacts.position || u.role_titles.join(", ")}</div></td>
                <td style={{ fontSize: 12 }}>{u.contacts.mobile && <div>📱 {u.contacts.mobile}</div>}<div>✉ {u.email}</div>{u.contacts.wechat && <div>WeChat: {u.contacts.wechat}</div>}{u.contacts.telegram && <div>Telegram: {u.contacts.telegram}</div>}</td>
                <td style={{ fontSize: 12 }}>{u.notify_channels.map((c) => channels[c] || c).join(", ")}</td>
              </tr>))}</tbody></table>
        </div>
        <div>
          {!cur ? <div className="card muted">Выберите сотрудника слева</div> : (
            <div className="card">
              <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline", flexWrap: "wrap", gap: 8 }}>
                <h2 style={{ margin: 0 }}>{cur.display_name}{!cur.is_active && <span className="badge" style={{ marginLeft: 8, fontSize: 12 }}>отключён</span>}</h2>
                {canManage && cur.id !== me.id && <button className="btn ghost" style={{ color: "var(--bad)" }} title="администратор / ЗГД; участник сделок и задач отключается с сохранением истории" onClick={removeUser}>Удалить сотрудника</button>}
              </div>
              <div className="muted" style={{ marginBottom: 8 }}>{cur.role_titles.join(", ")}</div>
              {cur.pending_changes?.fields && Object.keys(cur.pending_changes.fields).length > 0 && (
                <div className="alert warn" style={{ fontSize: 13 }}>
                  <b>Ожидают подтверждения{cur.pending_changes.requested_at ? ` с ${fmtDate(cur.pending_changes.requested_at)}` : ""}:</b>
                  <table style={{ fontSize: 12, marginTop: 4 }}><tbody>{Object.entries(cur.pending_changes.fields).map(([k, [a, b]]) => (
                    <tr key={k}><td>{FIELD_T[k] || k}</td><td className="muted">{a || "—"}</td><td>→</td><td><b>{b || "—"}</b></td>
                      {canManage && <td><button className="btn ghost" style={{ padding: "0 8px", fontSize: 11 }} onClick={() => decide(true, [k])}>подтвердить</button> <button className="btn ghost" style={{ padding: "0 8px", fontSize: 11, color: "var(--bad)" }} onClick={() => decide(false, [k])}>отклонить</button></td>}</tr>))}</tbody></table>
                  {canManage && <div className="row" style={{ gap: 6, marginTop: 6 }}><button className="btn" style={{ padding: "4px 12px" }} onClick={() => decide(true)}>Подтвердить все</button><button className="btn ghost" onClick={() => decide(false)}>Отклонить все</button></div>}
                  {!canManage && <div className="muted" style={{ fontSize: 12 }}>До подтверждения администратором / ЗГД действуют прежние значения.</div>}
                </div>)}
              <table><tbody>
                <tr><td style={{ width: "36%" }}>ФИО</td><td><input style={{ width: "100%" }} value={cur.display_name} disabled={!cur.can_edit} onChange={(e) => setCur({ ...cur, display_name: e.target.value })} /></td></tr>
                <tr><td>E-mail (логин){!canManage && <span className="muted" style={{ fontSize: 11 }}> · меняет администратор / ЗГД</span>}</td><td><input style={{ width: "100%" }} value={cur.email} disabled={!canManage} onChange={(e) => setCur({ ...cur, email: e.target.value })} /></td></tr>
                {FIELDS.map(([k, t, ph]) => (
                  <tr key={k}><td style={{ width: "36%" }}>{t}</td><td><input style={{ width: "100%" }} value={cur.contacts[k] || ""} placeholder={ph} disabled={!cur.can_edit} onChange={(e) => setC(k, e.target.value)} /></td></tr>))}
                <tr><td>WeCom ID (корпоративный WeChat)</td><td><input style={{ width: "100%" }} value={cur.wecom_user_id || ""} disabled={!cur.can_edit} onChange={(e) => setCur({ ...cur, wecom_user_id: e.target.value })} /></td></tr>
              </tbody></table>
              <h3>Уведомления о задачах</h3>
              <div className="row" style={{ flexWrap: "wrap", gap: 12 }}>
                {Object.entries(channels).map(([c, t]) => (
                  <label key={c} className="row"><input type="checkbox" checked={cur.notify_channels.includes(c)} disabled={!cur.can_channels || c === "app"}
                    onChange={(e) => setCur({ ...cur, notify_channels: e.target.checked ? [...cur.notify_channels, c] : cur.notify_channels.filter((x) => x !== c) })} /> {t}</label>))}
              </div>
              {!cur.can_channels && <div className="muted" style={{ fontSize: 12 }}>Каналы выбирают КД, ЗГД или администратор.</div>}
              <div className="row" style={{ marginTop: 10 }}>
                {cur.can_edit && <button className="btn" onClick={save}>Сохранить карточку</button>}
                {cur.can_edit && <button className="btn ghost" onClick={test}>Проверить уведомления</button>}
              </div>
              {cur.can_edit && !canManage && <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>Пустые поля сохраняются сразу; изменение уже заполненного значения уходит на подтверждение администратору / ЗГД.</div>}
              <DeputyBlock userId={cur.id} value={cur} users={users} canEdit={cur.can_edit} onSaved={(m) => { setMsg(m); load().then(() => api.get<{ users: Card[] }>("/api/team").then((r) => { const u = r.users.find((x) => x.id === cur.id); if (u) setCur({ ...cur, deputy_id: u.deputy_id, deputy_from: u.deputy_from, deputy_to: u.deputy_to, deputy_active: u.deputy_active }); })); }} />
              {cur.roles.includes("observer") && cur.can_channels && <ObserverScopeBlock userId={cur.id} value={(cur.observer_scope || {}) as Record<string, unknown>} users={users} onSaved={(m) => { setMsg(m); load(); }} />}
              {canManage && (
                <>
                  <h3>Роли и доступы</h3>
                  <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>{roles.map((r) => (
                    <label key={r.code} className="row" style={{ border: "1px solid " + (cur.roles.includes(r.code) ? "var(--accent)" : "var(--line)"), borderRadius: 6, padding: "4px 8px", cursor: "pointer" }}>
                      <input type="checkbox" checked={cur.roles.includes(r.code)} onChange={() => setCur({ ...cur, roles: cur.roles.includes(r.code) ? cur.roles.filter((x) => x !== r.code) : [...cur.roles, r.code] })} /> {r.title}</label>))}</div>
                  <div className="row" style={{ marginTop: 8 }}>
                    <label className="row"><input type="checkbox" checked={cur.is_active} onChange={(e) => setCur({ ...cur, is_active: e.target.checked })} /> учётная запись активна</label>
                    <button className="btn secondary" disabled={cur.roles.length === 0} onClick={saveRoles}>Сохранить роли и статус</button>
                  </div>
                  {err && <div className="alert error">{err}</div>}
                  <PasswordBlock userId={cur.id} isSelf={cur.id === me.id} onMsg={setMsg} />
                  {perms.length > 0 && (
                    <table style={{ marginTop: 8 }}><thead><tr><th>Раздел</th><th>Итоговый доступ</th><th>Индивидуально</th></tr></thead>
                      <tbody>{perms.map((p) => {
                        const o = overrides.find((x) => x.group === p.group);
                        return (
                          <tr key={p.group}><td>{p.title}</td>
                            <td><span className={"badge " + (p.level === 2 ? "ok" : p.level === 1 ? "accent" : "")}>{p.level_title}</span></td>
                            <td style={{ fontSize: 12 }}>{o ? <>{LEVELS[o.level]}{o.valid_to ? ` до ${fmtDate(o.valid_to)}` : ""}{o.comment ? ` · ${o.comment}` : ""}
                              <button className="btn ghost" style={{ padding: "0 6px", marginLeft: 6 }} title="убрать индивидуальный доступ — вернуть доступ по роли" onClick={() => delOverride(o.id)}>×</button></>
                              : <span className="muted">по роли</span>}</td></tr>);
                      })}</tbody></table>)}
                  <div className="row" style={{ marginTop: 8, flexWrap: "wrap" }}>
                    <select value={ovr.group} onChange={(e) => setOvr({ ...ovr, group: e.target.value })}>
                      <option value="">— раздел —</option>{perms.map((p) => <option key={p.group} value={p.group}>{p.title}</option>)}</select>
                    <select value={ovr.level} onChange={(e) => setOvr({ ...ovr, level: Number(e.target.value) })}>
                      {Object.entries(LEVELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
                    <label className="row" style={{ fontSize: 12 }}>до <input type="date" value={ovr.valid_to} onChange={(e) => setOvr({ ...ovr, valid_to: e.target.value })} /></label>
                    <input placeholder="основание" value={ovr.comment} onChange={(e) => setOvr({ ...ovr, comment: e.target.value })} style={{ width: 180 }} />
                    <button className="btn secondary" disabled={!ovr.group} onClick={addOverride}>Установить доступ</button>
                  </div>
                  <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>Индивидуальный доступ действует поверх роли: можно открыть раздел или, наоборот, закрыть его. Без даты — бессрочно.</div>
                </>)}
              {log.length > 0 && (
                <>
                  <h3>Последние уведомления</h3>
                  <table><thead><tr><th>Когда</th><th>Канал</th><th>Текст</th><th>Статус</th></tr></thead>
                    <tbody>{log.map((l, i) => <tr key={i}><td className="muted" style={{ fontSize: 12 }}>{l.at ? fmtDate(l.at) : ""}</td><td>{l.channel_title}</td><td style={{ fontSize: 12 }}>{l.text}</td>
                      <td style={{ fontSize: 12 }}><span className={"badge " + (l.status === "sent" ? "ok" : l.status === "failed" ? "bad" : "warn")}>{STATUS[l.status] || l.status}</span>{l.error && <div className="muted">{l.error}</div>}</td></tr>)}</tbody></table>
                </>)}
            </div>)}
        </div>
      </div>
    </>
  );
}


/** Пароль для страницы входа: ЗГД / админ задаёт сотруднику новый пароль (например, при первом входе или если забыт). */
function PasswordBlock({ userId, isSelf, onMsg }: { userId: string; isSelf: boolean; onMsg: (m: string) => void }) {
  const [pw, setPw] = useState("");
  const [open, setOpen] = useState(false);
  const [err, setErr] = useState("");
  const save = () => api.put(`/api/auth/users/${userId}/password`, { new_password: pw }).then(() => { setPw(""); setOpen(false); setErr(""); onMsg(isSelf ? "Ваш пароль изменён" : "Пароль задан — сообщите его сотруднику лично, в системе он не хранится в открытом виде"); })
    .catch((e) => setErr((e as Error).message));
  return (
    <div style={{ marginTop: 10 }}>
      <h3>Пароль для входа</h3>
      {!open ? <button className="btn ghost" onClick={() => setOpen(true)}>Задать новый пароль</button> : (
        <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
          <input type="password" autoComplete="new-password" placeholder="не короче 8 символов, буквы и цифры" value={pw} onChange={(e) => setPw(e.target.value)} style={{ width: 280 }} />
          <button className="btn secondary" disabled={pw.length < 8} onClick={save}>Сохранить пароль</button>
          <button className="btn ghost" onClick={() => { setOpen(false); setPw(""); setErr(""); }}>Отмена</button>
          {err && <span className="badge bad">{err}</span>}
        </div>)}
      <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>Вход по e-mail и паролю на странице входа; после 5 неверных попыток учётная запись блокируется на 15 минут, новый пароль снимает блокировку.</div>
    </div>);
}
