import { useEffect, useState } from "react";
import { lbl, PRIORITY } from "../labels";
import { Link, useNavigate } from "react-router-dom";
import { api, fmtDate } from "../api";
import { STATUS_TITLES, type Deal, type Me } from "../types";

type Row = Deal & { group?: string; parent_number?: string; related_count?: number };

/** Задача 9: связанные заявки (исходная и её дубли) идут подряд — дубли сразу под исходной, с отступом. */
function grouped(deals: Row[]): Row[] {
  const byGroup = new Map<string, Row[]>();
  for (const d of deals) { const g = d.group || d.id; if (!byGroup.has(g)) byGroup.set(g, []); byGroup.get(g)!.push(d); }
  const out: Row[] = [], seen = new Set<string>();
  for (const d of deals) {
    const g = d.group || d.id;
    if (seen.has(g)) continue;
    seen.add(g);
    const members = byGroup.get(g)!;
    out.push(...members.filter((m) => !m.parent_number), ...members.filter((m) => m.parent_number).sort((a, b) => a.number.localeCompare(b.number, "ru", { numeric: true })));
  }
  return out;
}

export default function DealsList({ me }: { me: Me }) {
  const [deals, setDeals] = useState<Row[]>([]);
  const [clients, setClients] = useState<{ id: string; name: string; sites: { name: string }[] }[]>([]);
  const [showNew, setShowNew] = useState(false);
  const [form, setForm] = useState({ client_id: "", site: "", contact: "", kp_due: "", priority: "normal", service_flag: false, target_hours: "" });
  const [err, setErr] = useState("");
  const nav = useNavigate();
  const isManager = me.roles.some((r) => r.code === "manager" || r.code === "gd");

  useEffect(() => {
    api.get<Row[]>("/api/deals").then(setDeals);
    api.get<typeof clients>("/api/refs/clients").then(setClients);
  }, []);

  const [docs, setDocs] = useState<{ file: File; category: string }[]>([]);   // файлы от клиента — хранятся в сделке
  async function create() {
    try {
      const d = await api.post<Deal>("/api/deals", { ...form, kp_due: form.kp_due || null, target_hours: form.target_hours ? Number(form.target_hours) : null });
      for (const x of docs) await api.upload(`/api/deals/${d.id}/documents`, x.file, { category: x.category, comment: "загружено при заведении сделки" });
      nav(`/deals/${d.id}`);
    } catch (e) { setErr((e as Error).message); }
  }

  return (
    <>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1>Сделки</h1>
        {isManager && <button className="btn add" onClick={() => setShowNew(!showNew)}>Новая сделка</button>}
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
          <div style={{ marginTop: 10 }}>
            <b>Файлы от клиента</b> <span className="muted">(если есть: тендерная документация, типовая форма договора, ТЗ — сохраняются в сделке; тендер и форму договора проверит юрист)</span>
            <div className="row" style={{ marginTop: 4 }}><input type="file" multiple onChange={(e) => { setDocs([...docs, ...(Array.from(e.target.files || []) as File[]).map((file) => ({ file, category: /догов|contract/i.test(file.name) ? "contract_form" : /тендер|tender|документац/i.test(file.name) ? "tender" : /тз|техническ|spec/i.test(file.name) ? "client_spec" : "client_other" }))]); e.target.value = ""; }} /></div>
            {docs.map((x, i) => (
              <div key={i} className="row" style={{ marginTop: 4 }}>
                <span>{x.file.name}</span>
                <select value={x.category} onChange={(e) => setDocs(docs.map((y, j) => (j === i ? { ...y, category: e.target.value } : y)))}>
                  <option value="tender">Тендерная документация</option><option value="contract_form">Типовая форма договора клиента</option>
                  <option value="client_spec">Техническое задание клиента</option><option value="client_other">Прочие файлы клиента</option>
                </select>
                <button className="btn ghost" style={{ padding: "0 6px" }} onClick={() => setDocs(docs.filter((_, j) => j !== i))}>×</button>
              </div>))}
          </div>
          {err && <div className="alert error">{err}</div>}
          <div className="row" style={{ marginTop: 10 }}><button className="btn" onClick={create} disabled={!form.client_id}>Создать</button></div>
        </div>
      )}
      <div className="card">
        <table>
          <thead><tr><th>Номер</th><th>Клиент</th><th>Объект</th><th>Статус</th><th>Срок КП</th><th>Приоритет</th><th>Версия</th></tr></thead>
          <tbody>
            {grouped(deals).map((d) => (
              <tr key={d.id} style={d.parent_number ? { background: "#fafafa" } : undefined}>
                <td style={d.parent_number ? { paddingLeft: 22 } : undefined}>{d.parent_number && <span className="muted">↳ </span>}<Link to={`/deals/${d.id}`}>{d.number}</Link>
                  {!d.parent_number && (d.related_count || 0) > 0 && <span className="badge" title="связанные заявки по одному запросу клиента" style={{ marginLeft: 6 }}>+{d.related_count} связ.</span>}
                  {d.parent_number && <div className="muted" style={{ fontSize: 11 }}>дубль {d.parent_number}</div>}</td><td>{d.client_name}</td><td>{d.site}</td>
                <td><span className="badge accent">{d.status_detail || STATUS_TITLES[d.status] || d.status}</span></td>
                <td>{fmtDate(d.kp_due)}</td><td>{lbl(PRIORITY, d.priority)}</td><td>{d.current_version_no}</td>
              </tr>
            ))}
            {deals.length === 0 && <tr><td colSpan={7} className="muted">Сделок нет</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}
