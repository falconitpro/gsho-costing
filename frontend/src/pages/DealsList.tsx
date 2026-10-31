import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, fmtDate } from "../api";
import { STATUS_TITLES, type Deal, type Me } from "../types";

export default function DealsList({ me }: { me: Me }) {
  const [deals, setDeals] = useState<Deal[]>([]);
  const [clients, setClients] = useState<{ id: string; name: string; sites: { name: string }[] }[]>([]);
  const [showNew, setShowNew] = useState(false);
  const [form, setForm] = useState({ client_id: "", site: "", contact: "", kp_due: "", priority: "normal", service_flag: false, target_hours: "" });
  const [err, setErr] = useState("");
  const nav = useNavigate();
  const isManager = me.roles.some((r) => r.code === "manager" || r.code === "gd");

  useEffect(() => {
    api.get<Deal[]>("/api/deals").then(setDeals);
    api.get<typeof clients>("/api/refs/clients").then(setClients);
  }, []);

  async function create() {
    try {
      const d = await api.post<Deal>("/api/deals", { ...form, kp_due: form.kp_due || null, target_hours: form.target_hours ? Number(form.target_hours) : null });
      nav(`/deals/${d.id}`);
    } catch (e) { setErr((e as Error).message); }
  }

  return (
    <>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1>Сделки</h1>
        {isManager && <button className="btn" onClick={() => setShowNew(!showNew)}>+ Новая сделка</button>}
      </div>
      {showNew && (
        <div className="card">
          <div className="grid cols-3">
            <label className="f">Клиент
              <select value={form.client_id} onChange={(e) => setForm({ ...form, client_id: e.target.value })}>
                <option value="">—</option>
                {clients.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
            </label>
            <label className="f">Объект / месторождение<input value={form.site} onChange={(e) => setForm({ ...form, site: e.target.value })} /></label>
            <label className="f">Контактное лицо<input value={form.contact} onChange={(e) => setForm({ ...form, contact: e.target.value })} /></label>
            <label className="f">Срок КП<input type="date" value={form.kp_due} onChange={(e) => setForm({ ...form, kp_due: e.target.value })} /></label>
            <label className="f">Приоритет
              <select value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })}>
                <option value="low">низкий</option><option value="normal">обычный</option><option value="high">высокий</option>
              </select>
            </label>
            <label className="f">Сервис до наработки, м·ч
              <div className="row"><input type="checkbox" checked={form.service_flag} onChange={(e) => setForm({ ...form, service_flag: e.target.checked })} />
                <input type="number" value={form.target_hours} placeholder="10000" disabled={!form.service_flag} onChange={(e) => setForm({ ...form, target_hours: e.target.value })} /></div>
            </label>
          </div>
          {err && <div className="alert error">{err}</div>}
          <div className="row" style={{ marginTop: 10 }}><button className="btn" onClick={create} disabled={!form.client_id}>Создать</button></div>
        </div>
      )}
      <div className="card">
        <table>
          <thead><tr><th>Номер</th><th>Клиент</th><th>Объект</th><th>Статус</th><th>Срок КП</th><th>Приоритет</th><th>Версия</th></tr></thead>
          <tbody>
            {deals.map((d) => (
              <tr key={d.id}>
                <td><Link to={`/deals/${d.id}`}>{d.number}</Link></td><td>{d.client_name}</td><td>{d.site}</td>
                <td><span className="badge accent">{STATUS_TITLES[d.status] || d.status}</span></td>
                <td>{fmtDate(d.kp_due)}</td><td>{d.priority}</td><td>{d.current_version_no}</td>
              </tr>
            ))}
            {deals.length === 0 && <tr><td colSpan={7} className="muted">Сделок нет</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}
