import { Fragment, useEffect, useState } from "react";
import { api, fmtDate, fmtPct, fmtRub } from "../api";
import type { Me } from "../types";
import { payText } from "../components/PaymentTermsEditor";
import type { PaymentLine } from "../types";

type Client = { id: string; name: string; inn: string; address: string; sites: { name: string }[]; contacts: { name: string; email?: string }[] };
type KpVer = { version_no: number; on: string; unit_price_rub: number; total_rub: number; payment_terms: PaymentLine[]; delivery_term: string; kp_brand: string; kp_path: string; kp_no: string };
type KpHist = { deal_id: string; deal_number: string; variant: string; factory: string; model: string; qty: number; outcome: string | null; chosen: boolean | null; manager: string; latest: KpVer; versions: KpVer[] };
type Card = Client & { funnel: { requests: number; kp_sent: number; won: number }; deals: { id: string; number: string; status: string; outcome: string | null }[];
  last_kp?: { deal: string; variant: string; price_rub: number; status: string }[]; kp_history?: KpHist[]; kp_totals?: { kp_count: number; won: number; sum_rub: number };
  avg_margin_pct?: number | null; sum_won_rub?: number };

export default function ClientsPage({ me }: { me: Me }) {
  const [clients, setClients] = useState<Client[]>([]);
  const [card, setCard] = useState<Card | null>(null);
  const [form, setForm] = useState({ name: "", inn: "", address: "" });
  const [err, setErr] = useState("");
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [filter, setFilter] = useState({ model: "", outcome: "" });
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
            {card.kp_history ? (
              <>
                <h2>История КП (все версии) <span className="muted" style={{ fontWeight: 400 }}>· видят КД и ГД</span></h2>
                {card.kp_totals && <div className="row" style={{ gap: 20 }}><div className="kpi"><span className="v">{card.kp_totals.kp_count}</span><span className="l">КП</span></div><div className="kpi"><span className="v">{card.kp_totals.won}</span><span className="l">выиграно</span></div><div className="kpi"><span className="v">{fmtRub(card.kp_totals.sum_rub)}</span><span className="l">сумма последних версий, RUB</span></div></div>}
                <div className="row" style={{ margin: "6px 0" }}><input placeholder="модель" value={filter.model} onChange={(e) => setFilter({ ...filter, model: e.target.value })} />
                  <select value={filter.outcome} onChange={(e) => setFilter({ ...filter, outcome: e.target.value })}><option value="">все результаты</option><option value="won">выиграно</option><option value="lost">проиграно</option><option value="cancelled">отменено</option><option value="open">без результата</option></select></div>
                <table><thead><tr><th>Дата</th><th>Сделка</th><th>Оборудование</th><th className="num">Кол-во</th><th className="num">Цена за ед., RUB</th><th className="num">Итого, RUB</th><th>Оплата</th><th>Срок</th><th>Результат</th><th>Менеджер</th><th>Файл</th></tr></thead>
                  <tbody>{card.kp_history.filter((h) => (!filter.model || h.model.toLowerCase().includes(filter.model.toLowerCase())) && (!filter.outcome || (filter.outcome === "open" ? !h.outcome : h.outcome === filter.outcome))).map((h, i) => {
                    const key = h.deal_id + h.model + h.variant;
                    return (<Fragment key={key + i}>
                      <tr style={{ cursor: h.versions.length > 1 ? "pointer" : undefined }} onClick={() => setOpen({ ...open, [key]: !open[key] })}>
                        <td>{fmtDate(h.latest.on)}</td><td><a href={`/deals/${h.deal_id}`} onClick={(e) => e.stopPropagation()}>{h.deal_number}</a>{h.variant && <div className="muted">{h.variant}{h.chosen && " ✓"}</div>}</td>
                        <td>{h.model}<div className="muted" style={{ fontSize: 11 }}>{h.latest.kp_brand === "company" ? "бренд компании" : h.factory}</div></td><td className="num">{h.qty}</td>
                        <td className="num">{fmtRub(h.latest.unit_price_rub)}</td><td className="num">{fmtRub(h.latest.total_rub)}</td><td className="muted" style={{ fontSize: 11 }}>{(h.latest.payment_terms || []).map(payText).join("; ")}</td>
                        <td className="muted" style={{ fontSize: 11 }}>{h.latest.delivery_term}</td><td><span className={"badge " + (h.outcome === "won" ? "ok" : h.outcome ? "bad" : "")}>{h.outcome || "—"}</span></td><td>{h.manager}</td>
                        <td>{h.latest.kp_path && <a href={`/api/files/${h.latest.kp_path}`} onClick={(e) => e.stopPropagation()}>v{h.latest.version_no}</a>}{h.versions.length > 1 && <span className="muted"> (+{h.versions.length - 1})</span>}</td>
                      </tr>
                      {open[key] && h.versions.slice(1).map((v) => <tr key={key + v.version_no} style={{ background: "#f7f7f7" }}><td className="muted">{fmtDate(v.on)}</td><td className="muted">v{v.version_no}</td><td /><td /><td className="num">{fmtRub(v.unit_price_rub)}</td><td className="num">{fmtRub(v.total_rub)}</td><td className="muted" style={{ fontSize: 11 }}>{(v.payment_terms || []).map(payText).join("; ")}</td><td className="muted" style={{ fontSize: 11 }}>{v.delivery_term}</td><td /><td /><td>{v.kp_path && <a href={`/api/files/${v.kp_path}`}>файл</a>}</td></tr>)}
                    </Fragment>);
                  })}</tbody></table>
              </>
            ) : <p className="muted">История КП и цен клиентам доступна КД и ГД (настраивается в уровнях доступа).</p>}
          </div>
        )}
      </div>
    </div>
  );
}
