import { useEffect, useState } from "react";
import { api } from "../../api";
import SecurityModal from "../SecurityModal";

// Руководители направлений (D-154): назначает ЗГД. Не назначен — на пульте «не назначен», без догадок по ролям.
// Замещение берётся из карточки сотрудника («Настройки → Сотрудники»: заместитель и период).
type Item = { key: string; title: string; head_id: string | null; deputy: { name: string; temporary: boolean; to: string | null } | null };
type U = { id: string; name: string; position: string; pinned: string; by_role: string[] };

export default function DirectionsDialog({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [items, setItems] = useState<Item[] | null>(null);
  const [users, setUsers] = useState<U[]>([]);
  const [heads, setHeads] = useState<Record<string, string>>({});
  const [mem, setMem] = useState<Record<string, string>>({});
  const [err, setErr] = useState("");
  useEffect(() => {
    api.get<{ items: Item[]; users: U[] }>("/api/management/directions").then((r) => {
      setItems(r.items); setUsers(r.users); setHeads(Object.fromEntries(r.items.map((i) => [i.key, i.head_id || ""])));
      setMem(Object.fromEntries(r.users.map((u) => [u.id, u.pinned || ""])));
    }).catch((e: Error) => setErr(e.message));
  }, []);
  const save = () => api.put("/api/management/directions", { heads: Object.fromEntries(Object.entries(heads).map(([k, v]) => [k, v || null])),
    members: Object.fromEntries(Object.entries(mem).map(([k, v]) => [k, v || null]))
  }).then(onSaved).catch((e: Error) => setErr(e.message));
  const title = (k: string) => items?.find((i) => i.key === k)?.title || k;
  return (
    <SecurityModal title="Руководители и состав направлений" onClose={onClose} width={560}>
      <p className="muted">Руководитель получает поручения подразделению и видит прямые поручения своим сотрудникам. Замещение — в карточке сотрудника.</p>
      {items?.map((i) => (
        <label key={i.key} className="f"><span>{i.title}</span>
          <select value={heads[i.key] || ""} onChange={(e) => setHeads({ ...heads, [i.key]: e.target.value })}>
            <option value="">не назначен</option>
            {users.map((u) => <option key={u.id} value={u.id}>{u.name}{u.position ? ` — ${u.position}` : ""}</option>)}
          </select></label>))}
      <h3 className="dir-dlg-h">Сотрудники по подразделениям</h3>
      <p className="muted">По умолчанию подразделение определяется ролью. Если сотрудник фактически работает в другом — закрепите его здесь.</p>
      <table className="asg-table dir-dlg-t"><tbody>{users.map((u) => (
        <tr key={u.id}><td>{u.name}<div className="muted">по роли: {u.by_role.length ? u.by_role.map(title).join(", ") : "—"}</div></td>
          <td><select value={mem[u.id] || ""} onChange={(e) => setMem({ ...mem, [u.id]: e.target.value })} aria-label={`Подразделение: ${u.name}`}>
            <option value="">по роли</option>{items?.map((i) => <option key={i.key} value={i.key}>{i.title}</option>)}</select></td></tr>))}</tbody></table>
      {err && <p className="bad">{err}</p>}
      <div className="asg-actions"><button className="btn ghost" onClick={onClose}>Отмена</button><button className="btn add" onClick={save} disabled={!items}>Сохранить</button></div>
    </SecurityModal>);
}
