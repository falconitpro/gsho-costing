import { useEffect, useState } from "react";
import { api, fmtDate } from "../api";

// Карточка сотрудника: заместитель на период и область наблюдателя.

type UserLite = { id: string; display_name: string; roles: string[] };
type Deputy = { deputy_id: string | null; deputy_from: string | null; deputy_to: string | null; deputy_active?: boolean };

export function DeputyBlock({ userId, value, users, canEdit, onSaved }: { userId: string; value: Deputy; users: UserLite[]; canEdit: boolean; onSaved: (msg: string) => void }) {
  const [f, setF] = useState({ deputy_id: value.deputy_id || "", deputy_from: value.deputy_from || "", deputy_to: value.deputy_to || "" });
  useEffect(() => setF({ deputy_id: value.deputy_id || "", deputy_from: value.deputy_from || "", deputy_to: value.deputy_to || "" }), [userId, value.deputy_id, value.deputy_from, value.deputy_to]);
  const [err, setErr] = useState("");
  async function save(clear = false) {
    try {
      await api.put(`/api/team/${userId}/deputy`, clear ? { deputy_id: null } : { deputy_id: f.deputy_id || null, deputy_from: f.deputy_from || null, deputy_to: f.deputy_to || null });
      setErr(""); onSaved(clear ? "Заместитель снят" : "Заместитель назначен");
    } catch (e) { setErr((e as Error).message); }
  }
  const dep = users.find((u) => u.id === value.deputy_id);
  return (
    <>
      <h3>Заместитель на период</h3>
      <p className="muted" style={{ marginTop: 0, fontSize: 12 }}>Заместитель видит сделки и задачи сотрудника как свои, получает его уведомления; по окончании периода доступ снимается сам. В ленте действия заместителя записаны его именем.</p>
      {dep && <div style={{ marginBottom: 6 }}>Сейчас: <b>{dep.display_name}</b>{value.deputy_from && ` с ${fmtDate(value.deputy_from)}`}{value.deputy_to && ` по ${fmtDate(value.deputy_to)}`}
        {value.deputy_active ? <span className="badge bad" style={{ marginLeft: 6 }}>действует</span> : <span className="badge" style={{ marginLeft: 6 }}>вне периода</span>}</div>}
      {canEdit && (
        <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
          <select value={f.deputy_id} onChange={(e) => setF({ ...f, deputy_id: e.target.value })}>
            <option value="">— не назначен —</option>{users.filter((u) => u.id !== userId).map((u) => <option key={u.id} value={u.id}>{u.display_name}</option>)}</select>
          <label className="row" style={{ gap: 4, fontSize: 12 }}>с <input type="date" value={f.deputy_from} onChange={(e) => setF({ ...f, deputy_from: e.target.value })} /></label>
          <label className="row" style={{ gap: 4, fontSize: 12 }}>по <input type="date" value={f.deputy_to} onChange={(e) => setF({ ...f, deputy_to: e.target.value })} /></label>
          <button className="btn secondary" disabled={!f.deputy_id} onClick={() => save(false)}>Назначить</button>
          {value.deputy_id && <button className="btn ghost" onClick={() => save(true)}>Снять</button>}
        </div>)}
      {err && <div className="alert error">{err}</div>}
    </>
  );
}

type Scope = { all?: boolean; client_ids?: string[]; manager_ids?: string[]; deal_ids?: string[]; phase?: string | null; until?: string | null };

export function ObserverScopeBlock({ userId, value, users, onSaved }: { userId: string; value: Scope; users: UserLite[]; onSaved: (msg: string) => void }) {
  const [clients, setClients] = useState<{ id: string; name: string }[]>([]);
  const [f, setF] = useState<Scope>({ all: false, client_ids: [], manager_ids: [], deal_ids: [], phase: null, until: null, ...value });
  const [dealNo, setDealNo] = useState("");
  const [err, setErr] = useState("");
  useEffect(() => { api.get<{ id: string; name: string }[]>("/api/refs/clients").then(setClients).catch(() => setClients([])); }, []);
  useEffect(() => setF({ all: false, client_ids: [], manager_ids: [], deal_ids: [], phase: null, until: null, ...value }), [userId]);
  const toggle = (k: "client_ids" | "manager_ids", id: string) => setF({ ...f, [k]: (f[k] || []).includes(id) ? (f[k] || []).filter((x) => x !== id) : [...(f[k] || []), id] });
  async function save() {
    try { await api.put(`/api/team/${userId}/observer-scope`, { ...f, until: f.until || null, phase: f.phase || null }); setErr(""); onSaved("Область наблюдателя сохранена"); }
    catch (e) { setErr((e as Error).message); }
  }
  const empty = !f.all && !(f.client_ids || []).length && !(f.manager_ids || []).length && !(f.deal_ids || []).length;
  return (
    <>
      <h3>Область наблюдателя</h3>
      <p className="muted" style={{ marginTop: 0, fontSize: 12 }}>Пустая область — наблюдатель не видит ни одной сделки. Отметьте клиентов, менеджеров или конкретные сделки, при необходимости — фазу и срок доступа. Финансовые поля закрыты по общей матрице.</p>
      {empty && <div className="alert warn">Область пуста — сделки не видны.</div>}
      <label className="row" style={{ gap: 6 }}><input type="checkbox" checked={!!f.all} onChange={(e) => setF({ ...f, all: e.target.checked })} /> все сделки</label>
      {!f.all && (
        <div className="grid cols-2" style={{ marginTop: 6 }}>
          <div><div className="muted" style={{ fontSize: 12 }}>Клиенты</div>
            <div style={{ maxHeight: 160, overflow: "auto", border: "1px solid var(--line)", borderRadius: 6, padding: 6 }}>
              {clients.map((c) => <label key={c.id} className="row" style={{ gap: 6, fontSize: 13 }}><input type="checkbox" checked={(f.client_ids || []).includes(c.id)} onChange={() => toggle("client_ids", c.id)} /> {c.name}</label>)}</div></div>
          <div><div className="muted" style={{ fontSize: 12 }}>Менеджеры</div>
            <div style={{ maxHeight: 160, overflow: "auto", border: "1px solid var(--line)", borderRadius: 6, padding: 6 }}>
              {users.filter((u) => u.roles.includes("manager") || u.roles.includes("cd")).map((u) => <label key={u.id} className="row" style={{ gap: 6, fontSize: 13 }}><input type="checkbox" checked={(f.manager_ids || []).includes(u.id)} onChange={() => toggle("manager_ids", u.id)} /> {u.display_name}</label>)}</div></div>
        </div>)}
      <div className="row" style={{ flexWrap: "wrap", gap: 8, marginTop: 6 }}>
        <input placeholder="id сделки — добавить" value={dealNo} onChange={(e) => setDealNo(e.target.value)} style={{ width: 220 }} />
        <button className="btn ghost" disabled={!dealNo.trim()} onClick={() => { setF({ ...f, deal_ids: [...(f.deal_ids || []), dealNo.trim()] }); setDealNo(""); }}>+ сделка</button>
        {(f.deal_ids || []).map((d) => <span key={d} className="badge">{d} <a href="#" onClick={(e) => { e.preventDefault(); setF({ ...f, deal_ids: (f.deal_ids || []).filter((x) => x !== d) }); }}>×</a></span>)}
      </div>
      <div className="row" style={{ flexWrap: "wrap", gap: 8, marginTop: 6 }}>
        <label className="row" style={{ gap: 4, fontSize: 12 }}>фаза <select value={f.phase || ""} onChange={(e) => setF({ ...f, phase: e.target.value || null })}><option value="">обе</option><option value="sale">только продажа</option><option value="contract">только контракт и исполнение</option></select></label>
        <label className="row" style={{ gap: 4, fontSize: 12 }}>доступ до <input type="date" value={f.until || ""} onChange={(e) => setF({ ...f, until: e.target.value || null })} /></label>
        <button className="btn secondary" onClick={save}>Сохранить область</button>
      </div>
      {err && <div className="alert error">{err}</div>}
    </>
  );
}
