import { useEffect, useState } from "react";
import { lbl, PRIORITY } from "../labels";
import { Link, useNavigate } from "react-router-dom";
import { api, fmtDate } from "../api";
import { STATUS_TITLES, type Deal, type Me } from "../types";

type Row = Deal & { group?: string; parent_number?: string; related_count?: number };

type SortKey = "number" | "date" | "client" | "site" | "status" | "kp_due" | "priority" | "version";
const PRIO_ORDER: Record<string, number> = { low: 0, normal: 1, medium: 1, high: 2, urgent: 3, critical: 3 };
const dealDate = (d: Row) => (d.requested_at || d.created_at || "") as string;
const sortVal = (d: Row, k: SortKey): string | number => {
  switch (k) {
    case "number": return d.number; case "date": return dealDate(d); case "client": return d.client_name || "";
    case "site": return d.site || ""; case "status": return d.status_detail || STATUS_TITLES[d.status] || d.status;
    case "kp_due": return d.kp_due || ""; case "priority": return PRIO_ORDER[d.priority] ?? 1; case "version": return d.current_version_no || 0;
  }
};
const COLS: [SortKey, string][] = [["number", "Номер"], ["date", "Дата"], ["client", "Клиент"], ["site", "Объект"], ["status", "Статус"],
  ["kp_due", "Срок КП"], ["priority", "Приоритет"], ["version", "Версия"]];

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

  const [archived, setArchived] = useState(false);
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>(() => {
    try { const v = JSON.parse(localStorage.getItem("gsho.dealsSort") || ""); if (v?.key) return v; } catch { /* нет сохранённой сортировки */ }
    return { key: "date", dir: -1 };
  });
  const toggleSort = (key: SortKey) => {
    const next = { key, dir: (sort.key === key ? -sort.dir : key === "date" || key === "kp_due" || key === "priority" ? -1 : 1) as 1 | -1 };
    setSort(next); try { localStorage.setItem("gsho.dealsSort", JSON.stringify(next)); } catch { /* хранилище недоступно */ }
  };
  const sorted = [...deals].sort((a, b) => {
    const x = sortVal(a, sort.key), y = sortVal(b, sort.key);
    const c = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y), "ru", { numeric: true });
    return c * sort.dir || a.number.localeCompare(b.number, "ru", { numeric: true }) * -1;
  });
  useEffect(() => { api.get<Row[]>(`/api/deals${archived ? "?archived=true" : ""}`).then(setDeals); }, [archived]);
  useEffect(() => { api.get<typeof clients>("/api/refs/clients").then(setClients); }, []);

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
      <div className="page-head">
        <h1>Сделки</h1>
        <div className="tabs" style={{ margin: 0 }}>
          <button className={!archived ? "active" : ""} onClick={() => setArchived(false)}>В работе</button>
          <button className={archived ? "active" : ""} onClick={() => setArchived(true)}>Архив</button>
        </div>
        {isManager && <div className="push"><button className="btn add" onClick={() => setShowNew(!showNew)}>Новая сделка</button></div>}
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
          <thead><tr>{COLS.map(([k, t]) => (
            <th key={k} onClick={() => toggleSort(k)} style={{ cursor: "pointer", userSelect: "none", whiteSpace: "nowrap" }} title="сортировать по столбцу">
              {t} <span style={{ opacity: sort.key === k ? 1 : 0.35 }}>{sort.key === k ? (sort.dir === 1 ? "▲" : "▼") : "↕"}</span></th>))}</tr></thead>
          <tbody>
            {grouped(sorted).map((d) => (
              <tr key={d.id} style={d.parent_number ? { background: "#fafafa" } : undefined}>
                <td style={d.parent_number ? { paddingLeft: 22 } : undefined}>{d.parent_number && <span className="muted">↳ </span>}<Link to={`/deals/${d.id}`}>{d.number}</Link>
                  {!d.parent_number && (d.related_count || 0) > 0 && <span className="badge" title="связанные заявки по одному запросу клиента" style={{ marginLeft: 6 }}>+{d.related_count} связ.</span>}
                  {d.parent_number && <div className="muted" style={{ fontSize: 11 }}>дубль {d.parent_number}</div>}</td><td style={{ whiteSpace: "nowrap" }}>{fmtDate(dealDate(d))}</td><td>{d.client_name}</td><td>{d.site}</td>
                <td><span className="badge accent">{d.status_detail || STATUS_TITLES[d.status] || d.status}</span></td>
                <td>{fmtDate(d.kp_due)}</td><td>{lbl(PRIORITY, d.priority)}</td><td>{d.current_version_no}</td>
              </tr>
            ))}
            {deals.length === 0 && <tr><td colSpan={8} className="muted">{archived ? "В архиве сделок нет" : "Сделок нет"}</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}
