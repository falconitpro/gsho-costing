import { useEffect, useState } from "react";
import { api } from "../api";
import type { Me } from "../types";

export default function SettingsPage({ me }: { me: Me }) {
  const [settings, setSettings] = useState<Record<string, unknown>>({});
  const [edit, setEdit] = useState<{ key: string; value: string; comment: string } | null>(null);
  const [history, setHistory] = useState<{ value: unknown; valid_from: string; valid_to: string | null; comment: string }[]>([]);
  const [msg, setMsg] = useState("");
  const load = () => api.get<Record<string, unknown>>("/api/admin/settings").then(setSettings);
  useEffect(() => { load(); }, []);
  async function save() {
    if (!edit) return;
    try {
      await api.put(`/api/admin/settings/${edit.key}`, { value: JSON.parse(edit.value), comment: edit.comment });
      setMsg(`Сохранено: ${edit.key}`); setEdit(null); load();
    } catch (e) { setMsg((e as Error).message); }
  }
  return (
    <>
      <h1>Ставки и нормативы</h1>
      <p className="muted">Изменения логируются с историей. ФД меняет пороги, ΔK/Δt, стоимость денег; шкалу накладных и лимит ГД — ГД/администратор. Роли: {me.roles.map((r) => r.title).join(", ")}</p>
      {msg && <div className="alert info">{msg}</div>}
      <div className="grid cols-2">
        <div className="card">
          <table><thead><tr><th>Ключ</th><th>Значение</th><th></th></tr></thead>
            <tbody>{Object.entries(settings).map(([k, v]) => (
              <tr key={k}><td>{k}</td><td><code style={{ fontSize: 12 }}>{JSON.stringify(v)}</code></td>
                <td><button className="btn ghost" onClick={() => { setEdit({ key: k, value: JSON.stringify(v, null, 1), comment: "" }); api.get<typeof history>(`/api/admin/settings/${k}/history`).then(setHistory); }}>изменить</button></td></tr>))}</tbody></table>
        </div>
        <div>
          {edit && (
            <div className="card">
              <h2>{edit.key}</h2>
              <textarea rows={8} style={{ width: "100%" }} value={edit.value} onChange={(e) => setEdit({ ...edit, value: e.target.value })} />
              <input style={{ width: "100%", marginTop: 8 }} placeholder="Комментарий к изменению" value={edit.comment} onChange={(e) => setEdit({ ...edit, comment: e.target.value })} />
              <div className="row" style={{ marginTop: 8 }}><button className="btn" onClick={save}>Сохранить</button><button className="btn secondary" onClick={() => setEdit(null)}>Отмена</button></div>
              <h2>История</h2>
              <table><tbody>{history.map((h, i) => <tr key={i}><td>{new Date(h.valid_from).toLocaleString("ru-RU")}</td><td><code>{JSON.stringify(h.value)}</code></td><td>{h.comment}</td></tr>)}</tbody></table>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
