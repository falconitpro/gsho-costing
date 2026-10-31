import { useEffect, useState } from "react";
import { api, fmtPct, fmtRub } from "../api";
import type { Me } from "../types";

type Client = { id: string; name: string; inn: string; address: string; sites: { name: string }[]; contacts: { name: string; email?: string }[] };
type Card = Client & { funnel: { requests: number; kp_sent: number; won: number }; deals: { id: string; number: string; status: string; outcome: string | null }[];
  last_kp: { deal: string; variant: string; price_rub: number; status: string }[]; avg_margin_pct?: number | null; sum_won_rub?: number };

export default function ClientsPage({ me }: { me: Me }) {
  const [clients, setClients] = useState<Client[]>([]);
  const [card, setCard] = useState<Card | null>(null);
  const [form, setForm] = useState({ name: "", inn: "", address: "" });
  const [err, setErr] = useState("");
  const canCreate = me.roles.some((r) => ["manager", "gd", "admin"].includes(r.code));
  const load = () => api.get<Client[]>("/api/refs/clients").then(setClients);
  useEffect(() => { load(); }, []);
  async function create() {
    try { await api.post("/api/refs/clients", form); setForm({ name: "", inn: "", address: "" }); setErr(""); load(); }
    catch (e) { setErr((e as Error).message); }
  }
  return (
    <div className="grid cols-2">
      <div>
        <h1>Клиенты</h1>
        {canCreate && (
          <div className="card row">
            <input placeholder="Наименование" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            <input placeholder="ИНН" value={form.inn} onChange={(e) => setForm({ ...form, inn: e.target.value })} />
            <input placeholder="Адрес" value={form.address} onChange={(e) => setForm({ ...form, address: e.target.value })} />
            <button className="btn" onClick={create} disabled={!form.name || form.inn.length < 10}>Добавить</button>
            {err && <span className="badge bad">{err}</span>}
          </div>
        )}
        <div className="card">
          <table><thead><tr><th>Наименование</th><th>ИНН</th><th>Объекты</th></tr></thead>
            <tbody>{clients.map((c) => (
              <tr key={c.id} style={{ cursor: "pointer" }} onClick={() => api.get<Card>(`/api/refs/clients/${c.id}`).then(setCard)}>
                <td>{c.name}</td><td>{c.inn}</td><td>{(c.sites || []).map((s) => s.name).join(", ")}</td></tr>))}</tbody></table>
        </div>
      </div>
      <div>
        {card && (
          <div className="card">
            <h2>{card.name}</h2>
            <div className="row" style={{ gap: 24 }}>
              <div className="kpi"><span className="v">{card.funnel.requests}</span><span className="l">запросов</span></div>
              <div className="kpi"><span className="v">{card.funnel.kp_sent}</span><span className="l">КП</span></div>
              <div className="kpi"><span className="v">{card.funnel.won}</span><span className="l">выиграно</span></div>
              {card.avg_margin_pct !== undefined && <div className="kpi"><span className="v">{fmtPct(card.avg_margin_pct)}</span><span className="l">средняя маржа (закрытые)</span></div>}
              {card.sum_won_rub !== undefined && <div className="kpi"><span className="v">{fmtRub(card.sum_won_rub)}</span><span className="l">сумма выигранных, RUB</span></div>}
            </div>
            <h2>Последние КП</h2>
            <table><thead><tr><th>Сделка</th><th>Вариант</th><th className="num">Цена, RUB</th><th>Статус</th></tr></thead>
              <tbody>{card.last_kp.map((k, i) => <tr key={i}><td>{k.deal}</td><td>{k.variant}</td><td className="num">{fmtRub(k.price_rub)}</td><td>{k.status}</td></tr>)}</tbody></table>
          </div>
        )}
      </div>
    </div>
  );
}
