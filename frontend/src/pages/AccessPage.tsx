import { useEffect, useState } from "react";
import { api } from "../api";
import type { Me } from "../types";

type Matrix = {
  groups: { code: string; title: string; fields: string[] }[]; levels: { level: number; title: string }[];
  roles: { role: string; title: string; levels: Record<string, number>; defaults: Record<string, number>; protected: string[] }[];
  overrides: { id: string; user_id: string; user_name: string; user_roles: string[]; field_group: string; level: number; valid_to: string | null; comment: string }[];
  users: { id: string; display_name: string; roles: string[]; is_active: boolean }[]; note: string;
};

/** Задача 11: уровни доступа как данные — матрица «роль × группа полей», исключения для сотрудников, предпросмотр. Меняют администратор и ЗГД. */
export default function AccessPage({ me }: { me: Me }) {
  const [m, setM] = useState<Matrix | null>(null);
  const [tab, setTab] = useState<"roles" | "overrides" | "preview">("roles");
  const [ov, setOv] = useState({ user_id: "", group: "", level: 1, valid_to: "", comment: "" });
  const [preview, setPreview] = useState<{ who: string; perms: { group: string; title: string; level: number; level_title: string }[]; denied_fields: string[] } | null>(null);
  const [pv, setPv] = useState({ role: "", user_id: "" });
  const [msg, setMsg] = useState("");
  const load = () => api.get<Matrix>("/api/admin/access").then(setM).catch((e) => setMsg(e.message));
  useEffect(() => { load(); }, []);
  if (!m) return <div className="muted">{msg || "Загрузка…"}</div>;
  const colors = ["#fde7e7", "#fff6d6", "#e6f5ec"];
  async function setLevel(role: string, group: string, level: number) {
    try { await api.put(`/api/admin/access/${role}/${group}`, { level }); setMsg(""); load(); } catch (e) { setMsg((e as Error).message); }
  }
  return (
    <>
      <h1>Уровни доступа</h1>
      <p className="muted">0 — не видит, 1 — видит, 2 — редактирует. {m.note} Изменения записываются в журнал. Пользователь: {me.display_name}.</p>
      {msg && <div className="alert error">{msg}</div>}
      <div className="tabs">
        <button className={tab === "roles" ? "active" : ""} onClick={() => setTab("roles")}>По ролям</button>
        <button className={tab === "overrides" ? "active" : ""} onClick={() => setTab("overrides")}>Исключения для сотрудников ({m.overrides.length})</button>
        <button className={tab === "preview" ? "active" : ""} onClick={() => setTab("preview")}>Предпросмотр «как видит»</button>
      </div>
      {tab === "roles" && (
        <div className="card" style={{ overflowX: "auto" }}>
          <table>
            <thead><tr><th>Группа данных</th>{m.roles.map((r) => <th key={r.role} title={r.role}>{r.title}</th>)}</tr></thead>
            <tbody>{m.groups.map((g) => (
              <tr key={g.code}><td title={g.fields.join(", ")}>{g.title}<div className="muted" style={{ fontSize: 10 }}>{g.code}</div></td>
                {m.roles.map((r) => {
                  const lvl = r.levels[g.code] ?? 0, prot = r.protected.includes(g.code), changed = lvl !== (r.defaults[g.code] ?? 0);
                  return (<td key={r.role} style={{ background: colors[lvl], textAlign: "center" }}>
                    <select value={lvl} disabled={prot} onChange={(e) => setLevel(r.role, g.code, Number(e.target.value))} title={prot ? "нельзя снять" : changed ? `по умолчанию: ${r.defaults[g.code] ?? 0}` : ""} style={{ padding: 2, fontWeight: changed ? 700 : 400 }}>
                      {m.levels.map((l) => <option key={l.level} value={l.level}>{l.level} — {l.title}</option>)}</select></td>);
                })}</tr>))}</tbody>
          </table>
        </div>
      )}
      {tab === "overrides" && (
        <div className="card">
          <table><thead><tr><th>Сотрудник</th><th>Роли</th><th>Группа</th><th>Уровень</th><th>До</th><th>Основание</th><th /></tr></thead>
            <tbody>{m.overrides.map((o) => (
              <tr key={o.id}><td>{o.user_name}</td><td className="muted">{o.user_roles.join(", ")}</td><td>{m.groups.find((g) => g.code === o.field_group)?.title || o.field_group}</td>
                <td><span className="badge" style={{ background: colors[o.level] }}>{o.level} — {m.levels[o.level]?.title}</span></td><td>{o.valid_to ? o.valid_to.slice(0, 10) : "бессрочно"}</td><td>{o.comment}</td>
                <td><button className="btn ghost" onClick={() => api.del(`/api/admin/access/overrides/${o.id}`).then(load)}>снять</button></td></tr>))}
              {m.overrides.length === 0 && <tr><td colSpan={7} className="muted">Исключений нет</td></tr>}</tbody></table>
          <h2>Добавить исключение</h2>
          <div className="row">
            <select value={ov.user_id} onChange={(e) => setOv({ ...ov, user_id: e.target.value })}><option value="">— сотрудник —</option>{m.users.filter((u) => u.is_active).map((u) => <option key={u.id} value={u.id}>{u.display_name} ({u.roles.join(", ")})</option>)}</select>
            <select value={ov.group} onChange={(e) => setOv({ ...ov, group: e.target.value })}><option value="">— группа —</option>{m.groups.map((g) => <option key={g.code} value={g.code}>{g.title}</option>)}</select>
            <select value={ov.level} onChange={(e) => setOv({ ...ov, level: Number(e.target.value) })}>{m.levels.map((l) => <option key={l.level} value={l.level}>{l.level} — {l.title}</option>)}</select>
            <input type="date" value={ov.valid_to} onChange={(e) => setOv({ ...ov, valid_to: e.target.value })} title="срок действия (пусто — бессрочно)" />
            <input placeholder="основание" value={ov.comment} onChange={(e) => setOv({ ...ov, comment: e.target.value })} />
            <button className="btn" disabled={!ov.user_id || !ov.group} onClick={() => api.post("/api/admin/access/overrides", { ...ov, valid_to: ov.valid_to || null }).then(() => { setOv({ user_id: "", group: "", level: 1, valid_to: "", comment: "" }); load(); }).catch((e) => setMsg(e.message))}>Добавить</button>
          </div>
        </div>
      )}
      {tab === "preview" && (
        <div className="card">
          <div className="row">
            <select value={pv.role} onChange={(e) => setPv({ role: e.target.value, user_id: "" })}><option value="">— роль —</option>{m.roles.map((r) => <option key={r.role} value={r.role}>{r.title}</option>)}</select>
            <select value={pv.user_id} onChange={(e) => setPv({ role: "", user_id: e.target.value })}><option value="">— сотрудник —</option>{m.users.map((u) => <option key={u.id} value={u.id}>{u.display_name}</option>)}</select>
            <button className="btn secondary" disabled={!pv.role && !pv.user_id} onClick={() => api.get<typeof preview>(`/api/admin/access/preview?${pv.role ? `role=${pv.role}` : `user_id=${pv.user_id}`}`).then(setPreview)}>Показать</button>
          </div>
          {preview && (<div style={{ marginTop: 10 }}><b>{preview.who}</b>
            <table style={{ marginTop: 6 }}><tbody>{preview.perms.map((p) => <tr key={p.group}><td>{p.title}</td><td><span className="badge" style={{ background: colors[p.level] }}>{p.level_title}</span></td></tr>)}</tbody></table>
            <div className="muted" style={{ marginTop: 6 }}>Закрытые ключи ответа API: {preview.denied_fields.join(", ") || "нет"}</div></div>)}
        </div>
      )}
    </>
  );
}
