import { useEffect, useState } from "react";
import { api, fmtDate, fmtRub } from "../api";
import type { Me } from "../types";

// Заказы поставщикам запчастей: к заказу, заказы и внутригрупповые заказы по маршруту, подтверждение, статусы поставки (ТЗ запчастей, раздел 7).

type Cand = { id: string; request_id: string; request_number: string; client_name: string; urgent: boolean; article: string; name: string; qty: number; unit: string;
  status: string; price: number | null; currency: string; lead_days: number | null; priced: boolean };
type CandGroup = { supplier_id: string; supplier_name: string; lines: Cand[] };
type PoLine = { id: string; no: number; number: string; name: string; name_foreign: string; qty: number; unit: string; price?: number; lead_days: number | null;
  status: string; status_title: string; eta: string | null; request_number?: string; request_id?: string; client_name?: string;
  history: { at: string; status: string; eta?: string; comment?: string }[] };
type Po = { id: string; number: string; kind: string; seller_name: string; buyer_name: string; route_name: string; currency: string; incoterms: string;
  status: string; status_title: string; total?: number; supplier_ref: string; planned_ship_date: string | null; actual_ship_date: string | null;
  sent_at: string | null; created_at: string; payments?: { due: string; amount: number; comment: string; paid_on: string | null }[]; prices_hidden?: boolean;
  send_log: { at: string; via: string; ok: boolean; error?: string }[]; lines: PoLine[]; intragroup?: Po[]; eta?: string | null; overdue?: boolean; lines_count?: number };
type Meta = { status: Record<string, string>; line_status: Record<string, string>; can_edit: boolean; can_track: boolean };

export default function PartsPurchase({ me }: { me: Me }) {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [cands, setCands] = useState<CandGroup[]>([]);
  const [list, setList] = useState<Po[]>([]);
  const [openOnly, setOpenOnly] = useState(true);
  const [cur, setCur] = useState<Po | null>(null);
  const [msg, setMsg] = useState("");
  const [sel, setSel] = useState<string[]>([]);
  void me;

  const load = () => {
    api.get<Po[]>(`/api/parts/po?open_only=${openOnly}`).then(setList).catch((e) => setMsg((e as Error).message));
    if (meta?.can_edit) api.get<CandGroup[]>("/api/parts/po/candidates").then(setCands).catch(() => setCands([]));
  };
  useEffect(() => { api.get<Meta>("/api/parts/po/meta").then(setMeta).catch((e) => setMsg((e as Error).message)); }, []);
  useEffect(load, [openOnly, meta]);
  const open = (id: string) => api.get<Po>(`/api/parts/po/${id}`).then(setCur);
  async function act(fn: () => Promise<unknown>, ok = "") {
    try {
      const r = await fn();
      if (Array.isArray(r) && r[0]?.lines) setCur(r[0] as Po);
      else if (r && (r as Po).lines) setCur(r as Po);
      if (ok) setMsg(ok);
      load();
    } catch (e) { setMsg((e as Error).message); }
  }

  return (
    <>
      {msg && <div className="alert info">{msg}</div>}
      {meta?.can_edit && (
        <div className="card">
          <h2 style={{ marginTop: 0 }}>К заказу</h2>
          <p className="muted" style={{ marginTop: 0 }}>Строки заявок «под заказ» с выбранным поставщиком. Заказ оформляет компания группы первой ступени маршрута; по остальным ступеням внутригрупповые заказы создаются сами.</p>
          {cands.length === 0 && <div className="muted">Заказывать нечего</div>}
          {cands.map((g) => {
            const mine = g.lines.filter((l) => sel.includes(l.id));
            return (
              <div key={g.supplier_id} style={{ marginBottom: 10 }}>
                <div className="row" style={{ justifyContent: "space-between" }}><b>{g.supplier_name}</b>
                  <button className="btn" disabled={!mine.length} onClick={() => act(() => api.post<Po[]>("/api/parts/po", { supplier_id: g.supplier_id, request_line_ids: mine.map((l) => l.id) })
                    .then((x) => { setSel(sel.filter((id) => !mine.some((l) => l.id === id))); return x; }), "Заказ создан — отправьте его поставщику")}>Заказать ({mine.length})</button></div>
                <table>
                  <thead><tr><th><input type="checkbox" checked={g.lines.every((l) => !l.priced || sel.includes(l.id))}
                    onChange={(e) => setSel(e.target.checked ? [...new Set([...sel, ...g.lines.filter((l) => l.priced).map((l) => l.id)])] : sel.filter((id) => !g.lines.some((l) => l.id === id)))} /></th>
                    <th>Заявка</th><th>Клиент</th><th>Артикул</th><th>Наименование</th><th className="num">Кол-во</th><th className="num">Цена поставщика</th><th className="num">Срок, дн</th></tr></thead>
                  <tbody>{g.lines.map((l) => (
                    <tr key={l.id}><td><input type="checkbox" disabled={!l.priced} checked={sel.includes(l.id)} onChange={(e) => setSel(e.target.checked ? [...sel, l.id] : sel.filter((x) => x !== l.id))} /></td>
                      <td>{l.request_number}{l.urgent && <span className="badge bad" style={{ marginLeft: 4 }}>машина стоит</span>}</td><td>{l.client_name}</td>
                      <td>{l.article}</td><td>{l.name}</td><td className="num">{l.qty} {l.unit}</td>
                      <td className="num">{l.priced ? (l.price != null ? `${fmtRub(l.price, 2)} ${l.currency}` : "есть") : <span className="badge bad">нет цены</span>}</td>
                      <td className="num">{l.lead_days ?? "—"}</td></tr>))}</tbody>
                </table>
              </div>);
          })}
        </div>)}

      <div className="card">
        <div className="row"><h2 style={{ margin: 0 }}>Заказы</h2>
          <label className="row" style={{ marginLeft: "auto" }}><input type="checkbox" checked={openOnly} onChange={(e) => setOpenOnly(e.target.checked)} /> только открытые</label></div>
        <table style={{ marginTop: 8 }}>
          <thead><tr><th>Номер</th><th>Дата</th><th>Поставщик</th><th>Покупатель</th><th>Маршрут</th><th className="num">Строк</th><th className="num">Сумма</th><th>Ближайшая дата на складе</th><th>Статус</th></tr></thead>
          <tbody>{list.map((p) => (
            <tr key={p.id} style={{ cursor: "pointer", background: cur?.id === p.id ? "var(--accent-soft)" : undefined }} onClick={() => open(p.id)}>
              <td><b>{p.number}</b></td><td>{fmtDate(p.created_at)}</td><td>{p.seller_name}</td><td>{p.buyer_name}</td><td>{p.route_name || "—"}</td>
              <td className="num">{p.lines_count}</td><td className="num">{p.total != null ? `${fmtRub(p.total, 2)} ${p.currency}` : "—"}</td>
              <td style={{ color: p.overdue ? "var(--bad)" : undefined }}>{fmtDate(p.eta)}{p.overdue && " — просрочка"}</td><td>{p.status_title}</td></tr>))}
            {list.length === 0 && <tr><td colSpan={9} className="muted">Заказов нет</td></tr>}</tbody>
        </table>
      </div>
      {cur && meta && <PoCard po={cur} meta={meta} act={act} />}
    </>
  );
}

function PoCard({ po, meta, act }: { po: Po; meta: Meta; act: (fn: () => Promise<unknown>, ok?: string) => Promise<void> }) {
  const [sel, setSel] = useState<string[]>([]);
  const [st, setSt] = useState("");
  const [eta, setEta] = useState("");
  const [note, setNote] = useState("");
  const [conf, setConf] = useState({ supplier_ref: po.supplier_ref, planned_ship_date: po.planned_ship_date || "" });
  const [pays, setPays] = useState(po.payments || []);
  useEffect(() => { setSel([]); setConf({ supplier_ref: po.supplier_ref, planned_ship_date: po.planned_ship_date || "" }); setPays(po.payments || []); }, [po.id]);
  const base = `/api/parts/po/${po.id}`;
  const live = !["closed", "cancelled"].includes(po.status);
  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 style={{ margin: 0 }}>{po.number} · {po.seller_name} → {po.buyer_name}</h2><span className="muted">{po.status_title}</span></div>
      <p className="muted" style={{ marginTop: 4 }}>{[po.route_name && `маршрут: ${po.route_name}`, po.incoterms, po.currency, po.supplier_ref && `подтверждение ${po.supplier_ref}`,
        po.planned_ship_date && `отгрузка ${fmtDate(po.planned_ship_date)}`, po.sent_at && `отправлен ${fmtDate(po.sent_at)}`].filter(Boolean).join(" · ")}</p>
      <div className="row" style={{ flexWrap: "wrap" }}>
        {!po.prices_hidden && <a className="btn ghost" href={`${base}/xlsx`}>Excel</a>}
        {meta.can_edit && live && <>
          <button className="btn ghost" onClick={() => act(async () => { const x = await api.post<Po & { send_results: { via: string; ok: boolean; error: string }[] }>(`${base}/send`, {});
            alert(x.send_results.map((s) => `${s.via === "mail" ? "почта" : "WeCom"}: ${s.ok ? "отправлено" : s.error}`).join("\n")); return x; })}>Отправить поставщику</button>
          {po.status === "draft" && <button className="btn ghost" onClick={() => act(() => api.post(`${base}/mark-sent`), "Отмечено как отправленный")}>отправлен вручную</button>}
          {po.lines.every((l) => ["ordered", "confirmed", "cancelled"].includes(l.status)) &&
            <button className="btn ghost" onClick={() => { if (confirm("Отменить заказ? Строки заявок вернутся «к заказу».")) act(() => api.post(`${base}/cancel`), "Заказ отменён"); }}>отменить заказ</button>}
        </>}
      </div>

      {meta.can_edit && live && (
        <>
          <h3>Подтверждение поставщика</h3>
          <div className="row" style={{ flexWrap: "wrap" }}>
            <label className="f">№ подтверждения / счёта<input value={conf.supplier_ref} onChange={(e) => setConf({ ...conf, supplier_ref: e.target.value })} /></label>
            <label className="f">Плановая дата отгрузки<input type="date" value={conf.planned_ship_date} onChange={(e) => setConf({ ...conf, planned_ship_date: e.target.value })} /></label>
          </div>
          {!po.prices_hidden && <>
            <div className="muted" style={{ marginTop: 6 }}>График оплат</div>
            {pays.map((p, i) => (
              <div className="row" key={i}>
                <input type="date" value={p.due} onChange={(e) => setPays(pays.map((x, j) => (j === i ? { ...x, due: e.target.value } : x)))} />
                <input type="number" style={{ width: 110 }} value={p.amount} onChange={(e) => setPays(pays.map((x, j) => (j === i ? { ...x, amount: Number(e.target.value) } : x)))} /> {po.currency}
                <input placeholder="комментарий" value={p.comment} onChange={(e) => setPays(pays.map((x, j) => (j === i ? { ...x, comment: e.target.value } : x)))} />
                <label className="row">оплачено<input type="date" value={p.paid_on || ""} onChange={(e) => setPays(pays.map((x, j) => (j === i ? { ...x, paid_on: e.target.value || null } : x)))} /></label>
                <button className="btn ghost" onClick={() => setPays(pays.filter((_, j) => j !== i))}>×</button></div>))}
            <button className="btn ghost" onClick={() => setPays([...pays, { due: new Date().toISOString().slice(0, 10), amount: po.total || 0, comment: "", paid_on: null }])}>+ платёж</button>
          </>}
          <div style={{ marginTop: 6 }}><button className="btn" onClick={() => act(() => api.post(`${base}/confirm`, {
            supplier_ref: conf.supplier_ref, planned_ship_date: conf.planned_ship_date || null, ...(po.prices_hidden ? {} : { payments: pays.filter((p) => p.due && p.amount) }) }),
            "Подтверждение сохранено, сроки в заявках обновлены")}>Сохранить подтверждение</button></div>
        </>)}

      <h3>Строки и статус поставки</h3>
      <table>
        <thead><tr><th></th><th>№</th><th>Номер у поставщика</th><th>Наименование</th><th className="num">Кол-во</th>{!po.prices_hidden && <th className="num">Цена</th>}<th>Заявка</th><th>Статус</th><th>На складе ≈</th></tr></thead>
        <tbody>{po.lines.map((l) => (
          <tr key={l.id} style={{ opacity: l.status === "cancelled" ? 0.5 : 1 }}>
            <td><input type="checkbox" disabled={!meta.can_track || !live} checked={sel.includes(l.id)} onChange={(e) => setSel(e.target.checked ? [...sel, l.id] : sel.filter((x) => x !== l.id))} /></td>
            <td>{l.no}</td><td>{l.number}</td><td>{l.name}{l.name_foreign && <div className="muted" style={{ fontSize: 11 }}>{l.name_foreign}</div>}</td>
            <td className="num">{l.qty} {l.unit}</td>{!po.prices_hidden && <td className="num">{fmtRub(l.price ?? 0, 2)}</td>}
            <td>{l.request_number ? <>{l.request_number}<div className="muted" style={{ fontSize: 11 }}>{l.client_name}</div></> : <span className="muted">на склад</span>}</td>
            <td title={(l.history || []).map((h) => `${fmtDate(h.at)}: ${meta.line_status[h.status] || h.status}${h.eta ? `, ${fmtDate(h.eta)}` : ""}${h.comment ? ` — ${h.comment}` : ""}`).join("\n")}>{l.status_title}</td>
            <td style={{ color: l.eta && new Date(l.eta) < new Date() && !["received", "cancelled"].includes(l.status) ? "var(--bad)" : undefined }}>{fmtDate(l.eta)}</td></tr>))}</tbody>
      </table>
      {meta.can_track && live && (
        <div className="row" style={{ marginTop: 6, flexWrap: "wrap" }}>
          <button className="btn ghost" onClick={() => setSel(po.lines.filter((l) => !["received", "cancelled"].includes(l.status)).map((l) => l.id))}>отметить все</button>
          <select value={st} onChange={(e) => setSt(e.target.value)}><option value="">статус не менять</option>{Object.entries(meta.line_status).filter(([k]) => k !== "received").map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select>
          <label className="row">новая дата на складе<input type="date" value={eta} onChange={(e) => setEta(e.target.value)} /></label>
          <input placeholder="комментарий (причина сдвига)" value={note} onChange={(e) => setNote(e.target.value)} />
          <button className="btn" disabled={!sel.length || (!st && !eta)} onClick={() => act(() => api.post(`${base}/lines/status`, { line_ids: sel, status: st || null, eta: eta || null, comment: note,
            actual_date: st === "shipped" ? new Date().toISOString().slice(0, 10) : null }).then((x) => { setSel([]); setSt(""); setEta(""); setNote(""); return x; }), "Статус обновлён, заявки клиентов обновлены")}>Применить</button>
        </div>)}

      {po.intragroup && po.intragroup.length > 0 && (<>
        <h3>Внутригрупповые заказы по маршруту</h3>
        <table>
          <thead><tr><th>Номер</th><th>Продавец → покупатель</th><th>Incoterms</th><th className="num">Сумма</th><th>Статус</th><th></th></tr></thead>
          <tbody>{po.intragroup.map((c) => (
            <tr key={c.id}><td><b>{c.number}</b></td><td>{c.seller_name} → {c.buyer_name}</td><td>{c.incoterms}</td>
              <td className="num">{c.total != null ? `${fmtRub(c.total, 2)} ${c.currency}` : <span className="muted">цены видят КД, ФД и ЗГД</span>}</td><td>{c.status_title}</td>
              <td>{!c.prices_hidden && <a className="btn ghost" href={`/api/parts/po/${c.id}/xlsx`}>Excel</a>}</td></tr>))}</tbody>
        </table></>)}
    </div>
  );
}
