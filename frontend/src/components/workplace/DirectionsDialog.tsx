import { useEffect, useState } from "react";
import { api } from "../../api";
import SecurityModal from "../SecurityModal";

// Руководители направлений (D-154): назначает ЗГД. Не назначен — на пульте «не назначен», без догадок по ролям.
// Замещение берётся из карточки сотрудника («Настройки → Сотрудники»: заместитель и период).
type Item = { key: string; title: string; head_id: string | null; deputy: { name: string; temporary: boolean; to: string | null } | null };
type U = { id: string; name: string; position: string };

export default function DirectionsDialog({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [items, setItems] = useState<Item[] | null>(null);
  const [users, setUsers] = useState<U[]>([]);
  const [heads, setHeads] = useState<Record<string, string>>({});
  const [err, setErr] = useState("");
  useEffect(() => {
    api.get<{ items: Item[]; users: U[] }>("/api/management/directions").then((r) => {
      setItems(r.items); setUsers(r.users); setHeads(Object.fromEntries(r.items.map((i) => [i.key, i.head_id || ""])));
    }).catch((e: Error) => setErr(e.message));
  }, []);
  const save = () => api.put("/api/management/directions", { heads: Object.fromEntries(Object.entries(heads).map(([k, v]) => [k, v || null]))
  }).then(onSaved).catch((e: Error) => setErr(e.message));
  return (
    <SecurityModal title="Руководители направлений" onClose={onClose} width={560}>
      <p className="muted">Руководитель получает поручения подразделению и видит прямые поручения своим сотрудникам. Замещение — в карточке сотрудника.</p>
      {items?.map((i) => (
        <label key={i.key} className="f"><span>{i.title}</span>
          <select value={heads[i.key] || ""} onChange={(e) => setHeads({ ...heads, [i.key]: e.target.value })}>
            <option value="">не назначен</option>
            {users.map((u) => <option key={u.id} value={u.id}>{u.name}{u.position ? ` — ${u.position}` : ""}</option>)}
          </select></label>))}
      {err && <p className="bad">{err}</p>}
      <div className="asg-actions"><button className="btn ghost" onClick={onClose}>Отмена</button><button className="btn add" onClick={save} disabled={!items}>Сохранить</button></div>
    </SecurityModal>);
}
