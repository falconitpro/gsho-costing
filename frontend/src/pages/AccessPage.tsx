import { useEffect, useState } from "react";
import { api } from "../api";
import type { Me } from "../types";
import { Loading } from "../components/States";

type Matrix = {
  groups: { code: string; title: string; fields: string[] }[]; levels: { level: number; title: string }[];
  roles: { role: string; title: string; levels: Record<string, number>; defaults: Record<string, number>; protected: string[] }[];
  overrides: { id: string; user_id: string; user_name: string; user_roles: string[]; field_group: string; level: number; valid_to: string | null; comment: string }[];
  users: { id: string; display_name: string; roles: string[]; is_active: boolean }[]; note: string;
};

/** Задача 11: уровни доступа как данные — матрица «роль × группа полей», исключения для сотрудников, предпросмотр. Меняют администратор и ЗГД. */
export default function AccessPage({ me, embedded = false }: { me: Me; embedded?: boolean }) {
  const [m, setM] = useState<Matrix | null>(null);
  const [tab, setTab] = useState<"roles" | "overrides" | "preview" | "history" | "actions">("roles");
  const [acts, setActs] = useState<{ sections: { title: string; rules: string[]; silent: number }[]; ladder: { step: string; role: string; when: string }[]; total: number } | null>(null);
  const [actQ, setActQ] = useState("");
  const [hist, setHist] = useState<{ at: string | null; by: string; what: string; change: string; comment: string }[] | null>(null);
  const [ov, setOv] = useState({ user_id: "", group: "", level: 1, valid_to: "", comment: "" });
  const [preview, setPreview] = useState<{ who: string; perms: { group: string; title: string; level: number; level_title: string }[]; denied_fields: string[] } | null>(null);
  const [pv, setPv] = useState({ role: "", user_id: "" });
  const [msg, setMsg] = useState("");
  const load = () => api.get<Matrix>("/api/admin/access").then(setM).catch((e) => setMsg(e.message));
  useEffect(() => { load(); }, []);
  if (!m) return <Loading msg={msg} />;
  const colors = ["#ffe3e3", "#f4f4f4", "#ededed"];
  async function setLevel(role: string, group: string, level: number) {
    try { await api.put(`/api/admin/access/${role}/${group}`, { level }); setMsg(""); load(); } catch (e) { setMsg((e as Error).message); }
  }
  return (
    <>
      {!embedded && <h1>Уровни доступа</h1>}
      <p className="muted">0 — не видит, 1 — видит, 2 — редактирует. {m.note} Изменения записываются в журнал. Пользователь: {me.display_name}.</p>
      {msg && <div className="alert error">{msg}</div>}
      <div className="tabs">
        <button className={tab === "roles" ? "active" : ""} onClick={() => setTab("roles")}>По ролям</button>
        <button className={tab === "overrides" ? "active" : ""} onClick={() => setTab("overrides")}>Исключения для сотрудников ({m.overrides.length})</button>
        <button className={tab === "preview" ? "active" : ""} onClick={() => setTab("preview")}>Предпросмотр «как видит»</button>
        <button className={tab === "history" ? "active" : ""} onClick={() => { setTab("history"); api.get<{ rows: NonNullable<typeof hist> }>("/api/admin/access/history").then((r) => setHist(r.rows)).catch((e) => setMsg(e.message)); }}>Журнал изменений</button>
        <button className={tab === "actions" ? "active" : ""} onClick={() => { setTab("actions"); if (!acts) api.get<NonNullable<typeof acts>>("/api/admin/access/actions").then(setActs).catch((e) => setMsg(e.message)); }}>Кто что может делать</button>
      </div>
      {tab === "actions" && (
        <div className="card">
          {!acts ? <Loading /> : <>
            <div className="muted" style={{ fontSize: 13, marginBottom: 10 }}>Права на действия заданы в коде и здесь не меняются (решение владельца 20.11.2026). Список собран из кода сервера автоматически — {acts.total} правил,
              поэтому всегда совпадает с тем, как система работает. Что каждая роль видит — на вкладке «По ролям».</div>
            <h3 style={{ margin: "0 0 6px" }}>Утверждение расчёта: ступени</h3>
            <table style={{ marginBottom: 14 }}><thead><tr><th>Ступень</th><th>Кто</th><th>Когда нужна</th></tr></thead>
              <tbody>{acts.ladder.map((l) => <tr key={l.step}><td>{l.step}</td><td><b>{l.role}</b></td><td>{l.when}</td></tr>)}</tbody></table>
            <input placeholder="поиск: роль или действие — например «ЗГД», «виза», «архив»" value={actQ} onChange={(e) => setActQ(e.target.value)} style={{ width: "100%", maxWidth: 520, marginBottom: 10 }} />
            {acts.sections.map((sec) => { const q = actQ.trim().toLowerCase(); const rules = q ? sec.rules.filter((r) => r.toLowerCase().includes(q)) : sec.rules;
              if (!rules.length) return null;
              return <details key={sec.title} open={!!q} style={{ borderTop: "1px solid var(--line)", padding: "8px 0" }}>
                <summary style={{ cursor: "pointer" }}><b>{sec.title}</b> <span className="muted">· {rules.length}</span></summary>
                <ul style={{ margin: "8px 0 0", paddingLeft: 20 }}>{rules.map((r) => <li key={r} style={{ margin: "3px 0" }}>{r}</li>)}</ul>
                {!q && sec.silent > 0 && <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>и ещё {sec.silent} проверок прав без текста пояснения</div>}
              </details>; })}
          </>}
        </div>)}
      {tab === "history" && (
        <div className="card" style={{ overflowX: "auto" }}>
          {!hist ? <Loading /> : hist.length === 0 ? <div className="muted">Права ещё не меняли.</div> : (
            <table><thead><tr><th>Когда</th><th>Кто</th><th>Что</th><th>Изменение</th><th>Комментарий</th></tr></thead>
              <tbody>{hist.map((h, i) => <tr key={i}><td style={{ whiteSpace: "nowrap" }}>{h.at ? new Date(h.at).toLocaleString("ru-RU") : "—"}</td><td>{h.by}</td><td>{h.what}</td><td>{h.change}</td><td className="muted">{h.comment}</td></tr>)}</tbody></table>)}
        </div>)}
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
