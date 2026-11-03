import { useEffect, useState } from "react";
import { api, fmtDate } from "../api";
import type { Me } from "../types";

type Contacts = { position: string; department: string; mobile: string; work_phone: string; wechat: string; telegram: string; whatsapp: string; max: string; extra_email: string; note: string };
type Card = { id: string; display_name: string; email: string; roles: string[]; role_titles: string[]; contacts: Contacts; wecom_user_id: string;
  notify_channels: string[]; can_edit: boolean; can_channels: boolean };
type Log = { at: string | null; channel: string; channel_title: string; text: string; status: string; error: string };

const FIELDS: [keyof Contacts, string, string][] = [
  ["position", "Должность", ""], ["department", "Подразделение", ""], ["mobile", "Мобильный телефон", "+7 900 000-00-00"], ["work_phone", "Рабочий телефон", ""],
  ["wechat", "WeChat ID", ""], ["telegram", "Telegram", "@имя"], ["whatsapp", "WhatsApp", "+7…"], ["max", "MAX", "+7…"], ["extra_email", "Доп. e-mail", ""], ["note", "Примечание", ""],
];
const STATUS: Record<string, string> = { sent: "отправлено", queued: "ожидает подключения канала", failed: "ошибка", read: "прочитано" };

/** Сотрудники: карточки контактов исполнителей; каналы уведомлений о задачах выбирают КД, ЗГД или администратор. */
export default function TeamPage({ me }: { me: Me }) {
  const [users, setUsers] = useState<Card[]>([]);
  const [channels, setChannels] = useState<Record<string, string>>({});
  const [cur, setCur] = useState<Card | null>(null);
  const [log, setLog] = useState<Log[]>([]);
  const [q, setQ] = useState("");
  const [msg, setMsg] = useState("");
  const load = () => api.get<{ users: Card[]; channels: Record<string, string> }>("/api/team").then((r) => { setUsers(r.users); setChannels(r.channels); });
  useEffect(() => { load(); }, []);
  const open = (u: Card) => { setCur(JSON.parse(JSON.stringify(u))); setMsg(""); api.get<{ log: Log[] }>(`/api/team/${u.id}/notifications`).then((r) => setLog(r.log)).catch(() => setLog([])); };
  async function save() {
    if (!cur) return;
    try {
      let r = await api.put<Card>(`/api/team/${cur.id}/contacts`, { contacts: cur.contacts, wecom_user_id: cur.wecom_user_id });
      if (cur.can_channels) r = await api.put<Card>(`/api/team/${cur.id}/channels`, { channels: cur.notify_channels.filter((c) => c !== "app") });
      setCur(r); setMsg("Карточка сохранена"); load();
    } catch (e) { setMsg((e as Error).message); }
  }
  async function test() {
    if (!cur) return;
    const r = await api.post<{ log: Log[] }>(`/api/team/${cur.id}/test`); setLog(r.log); setMsg("Проверочное уведомление отправлено по всем каналам сотрудника");
  }
  const shown = users.filter((u) => !q || `${u.display_name} ${u.email} ${u.contacts.position} ${u.contacts.mobile}`.toLowerCase().includes(q.toLowerCase()));
  const setC = (k: keyof Contacts, v: string) => cur && setCur({ ...cur, contacts: { ...cur.contacts, [k]: v } });

  return (
    <>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1>Сотрудники</h1>
        <input placeholder="поиск: имя, должность, телефон" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 260 }} />
      </div>
      <p className="muted">При постановке задачи исполнитель сразу получает уведомление во все выбранные каналы. Каналы выбирают КД, ЗГД или администратор; контакты заполняет сам сотрудник. Каналы без подключённой интеграции отмечаются в журнале «ожидает подключения канала».</p>
      {msg && <div className="alert info">{msg}</div>}
      <div className="grid" style={{ gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1.2fr)" }}>
        <div className="card">
          <table><thead><tr><th>Сотрудник</th><th>Контакты</th><th>Уведомления</th></tr></thead>
            <tbody>{shown.map((u) => (
              <tr key={u.id} style={{ cursor: "pointer", background: cur?.id === u.id ? "var(--accent-soft)" : undefined }} onClick={() => open(u)}>
                <td><b>{u.display_name}</b><div className="muted" style={{ fontSize: 12 }}>{u.contacts.position || u.role_titles.join(", ")}</div></td>
                <td style={{ fontSize: 12 }}>{u.contacts.mobile && <div>📱 {u.contacts.mobile}</div>}<div>✉ {u.email}</div>{u.contacts.wechat && <div>WeChat: {u.contacts.wechat}</div>}{u.contacts.telegram && <div>Telegram: {u.contacts.telegram}</div>}</td>
                <td style={{ fontSize: 12 }}>{u.notify_channels.map((c) => channels[c] || c).join(", ")}</td>
              </tr>))}</tbody></table>
        </div>
        <div>
          {!cur ? <div className="card muted">Выберите сотрудника слева</div> : (
            <div className="card">
              <h2 style={{ marginTop: 0 }}>{cur.display_name}</h2>
              <div className="muted" style={{ marginBottom: 8 }}>{cur.role_titles.join(", ")} · {cur.email}</div>
              <table><tbody>
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
