import { useEffect, useState } from "react";
import { api, fmtDate } from "../api";

// Склад запчастей: приёмка по заказу поставщику (удобно с телефона), отклонения и претензии, движения (ТЗ запчастей, разделы 7–8).

type RLine = { id: string; no: number; article: string; number: string; name: string; qty: number; unit: string; received_qty: number; remaining: number;
  status: string; eta: string | null; request_number?: string; client_name?: string; urgent?: boolean };
type Recv = { id: string; number: string; supplier_name: string; status: string; lines: RLine[] };
type Dev = { id: string; number: string; po_number: string; supplier_name: string; kind: string; kind_title: string; qty: number; decision: string;
  decision_title: string; status: string; status_title: string; claim_text: string; claim_sent_at: string | null; comment: string; created_at: string;
  line: { no: number; number: string; name: string; qty: number; received_qty: number; unit: string } | null; photos: { i: number; name: string }[] };
type Move = { id: string; at: string; article: string; name: string; warehouse: string; qty: number; kind: string; doc_number: string; user: string };
type Meta = { warehouses: { id: string; name: string }[]; discrepancy: Record<string, string>; decisions: Record<string, string>; can_edit: boolean };
type Row = { qty: string; qty_bad: string; discrepancy: string; cell: string; comment: string };

const KIND: Record<string, string> = { receipt: "приёмка", shipment: "отгрузка", adjust: "корректировка" };

export default function PartsWarehouse() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [tab, setTab] = useState<"receipt" | "dev" | "moves">("receipt");
  const [msg, setMsg] = useState("");
  useEffect(() => { api.get<Meta>("/api/parts/stock/meta").then(setMeta).catch((e) => setMsg((e as Error).message)); }, []);
  if (!meta) return msg ? <div className="alert info">{msg}</div> : null;
  return (
    <>
      {msg && <div className="alert info">{msg}</div>}
      <div className="row" style={{ marginBottom: 8 }}>
        <button className={`btn ${tab === "receipt" ? "" : "ghost"}`} onClick={() => setTab("receipt")}>Приёмка</button>
        <button className={`btn ${tab === "dev" ? "" : "ghost"}`} onClick={() => setTab("dev")}>Отклонения</button>
        <button className={`btn ${tab === "moves" ? "" : "ghost"}`} onClick={() => setTab("moves")}>Движения</button>
      </div>
      {tab === "receipt" && <Receipt meta={meta} setMsg={setMsg} />}
      {tab === "dev" && <Deviations meta={meta} setMsg={setMsg} />}
      {tab === "moves" && <Moves />}
    </>
  );
}

function Receipt({ meta, setMsg }: { meta: Meta; setMsg: (s: string) => void }) {
  const [list, setList] = useState<Recv[]>([]);
  const [cur, setCur] = useState<Recv | null>(null);
  const [wh, setWh] = useState(meta.warehouses[0]?.id || "");
  const [rows, setRows] = useState<Record<string, Row>>({});
  const [comment, setComment] = useState("");
  const load = () => api.get<Recv[]>("/api/parts/stock/receivable").then(setList).catch((e) => setMsg((e as Error).message));
  useEffect(() => { load(); }, []);
  const pick = (p: Recv) => { setCur(p); setRows(Object.fromEntries(p.lines.map((l) => [l.id, { qty: String(l.remaining), qty_bad: "", discrepancy: "", cell: "", comment: "" }]))); };
  const set = (id: string, k: keyof Row, v: string) => setRows({ ...rows, [id]: { ...rows[id], [k]: v } });
  async function save() {
    if (!cur) return;
    try {
      const lines = cur.lines.map((l) => ({ po_line_id: l.id, qty: Number(rows[l.id].qty.replace(",", ".") || 0), qty_bad: Number(rows[l.id].qty_bad.replace(",", ".") || 0),
        discrepancy: rows[l.id].discrepancy, cell: rows[l.id].cell, comment: rows[l.id].comment })).filter((x) => x.qty > 0 || x.qty_bad > 0);
      const r = await api.post<{ number: string; deviations: { number: string; kind: string }[] }>("/api/parts/stock/receipts", { po_id: cur.id, warehouse_id: wh, comment, lines });
      setMsg(`Приёмка ${r.number} проведена: товар на складе, под заявки клиентов — в резерве.` + (r.deviations.length ? ` Отклонения: ${r.deviations.map((d) => d.number).join(", ")} — решите во вкладке «Отклонения».` : ""));
      setCur(null); setComment(""); load();
    } catch (e) { setMsg((e as Error).message); }
  }
  return (
    <div className="card">
      {!cur && <>
        <h2 style={{ marginTop: 0 }}>Ждём поступления</h2>
        {list.length === 0 && <div className="muted">Нет заказов, отправленных поставщикам и ожидающих приёмки</div>}
        {list.map((p) => (
          <div key={p.id} style={{ borderBottom: "1px solid var(--line)", padding: "6px 0", cursor: meta.can_edit ? "pointer" : undefined }} onClick={() => meta.can_edit && pick(p)}>
            <b>{p.number}</b> · {p.supplier_name} <span className="muted">· строк {p.lines.length}</span>
            <div className="muted" style={{ fontSize: 12 }}>{p.lines.map((l) => `${l.article} × ${l.remaining}`).join(", ")}</div>
          </div>))}
      </>}
      {cur && <>
        <div className="row" style={{ justifyContent: "space-between" }}><h2 style={{ margin: 0 }}>Приёмка по {cur.number}</h2><button className="btn ghost" onClick={() => setCur(null)}>назад</button></div>
        <label className="f">Склад<select value={wh} onChange={(e) => setWh(e.target.value)}>{meta.warehouses.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}</select></label>
        {cur.lines.map((l) => (
          <div key={l.id} style={{ border: "1px solid var(--line)", borderRadius: 6, padding: 8, marginTop: 8 }}>
            <b>{l.article}</b> {l.name}{l.urgent && <span className="badge bad" style={{ marginLeft: 4 }}>машина стоит</span>}
            <div className="muted" style={{ fontSize: 12 }}>ожидаем {l.remaining} {l.unit}{l.received_qty ? ` (уже принято ${l.received_qty})` : ""}{l.request_number && ` · ${l.request_number}, ${l.client_name}`}</div>
            <div className="row" style={{ flexWrap: "wrap", marginTop: 4 }}>
              <label className="f">Годных<input inputMode="decimal" style={{ width: 70 }} value={rows[l.id]?.qty ?? ""} onChange={(e) => set(l.id, "qty", e.target.value)} /></label>
              <label className="f">Брак / пересорт<input inputMode="decimal" style={{ width: 70 }} value={rows[l.id]?.qty_bad ?? ""} onChange={(e) => set(l.id, "qty_bad", e.target.value)} /></label>
              {Number(rows[l.id]?.qty_bad || 0) > 0 && <label className="f">Что не так<select value={rows[l.id].discrepancy || "damaged"} onChange={(e) => set(l.id, "discrepancy", e.target.value)}>
                <option value="damaged">брак</option><option value="wrong">пересорт</option></select></label>}
              <label className="f">Ячейка<input style={{ width: 80 }} value={rows[l.id]?.cell ?? ""} onChange={(e) => set(l.id, "cell", e.target.value)} /></label>
              <label className="f" style={{ flex: 1 }}>Комментарий<input value={rows[l.id]?.comment ?? ""} onChange={(e) => set(l.id, "comment", e.target.value)} /></label>
            </div>
          </div>))}
        <label className="f" style={{ marginTop: 8 }}>Комментарий к приёмке<input value={comment} onChange={(e) => setComment(e.target.value)} /></label>
        <p className="muted" style={{ fontSize: 12 }}>Меньше ожидаемого — создаётся отклонение «недопоставка»; фото брака добавляются в отклонении.</p>
        <button className="btn" disabled={!wh} onClick={save}>Принять</button>
      </>}
    </div>
  );
}

function Deviations({ meta, setMsg }: { meta: Meta; setMsg: (s: string) => void }) {
  const [list, setList] = useState<Dev[]>([]);
  const [openOnly, setOpenOnly] = useState(true);
  const [edit, setEdit] = useState<Record<string, { decision: string; claim_text: string }>>({});
  const load = () => api.get<Dev[]>(`/api/parts/stock/deviations?open_only=${openOnly}`).then((l) => {
    setList(l); setEdit(Object.fromEntries(l.map((d) => [d.id, { decision: d.decision, claim_text: d.claim_text }])));
  }).catch((e) => setMsg((e as Error).message));
  useEffect(() => { load(); }, [openOnly]);
  const run = async (fn: () => Promise<unknown>, ok: string) => { try { await fn(); setMsg(ok); load(); } catch (e) { setMsg((e as Error).message); } };
  return (
    <div className="card">
      <div className="row"><h2 style={{ margin: 0 }}>Отклонения поставки</h2>
        <label className="row" style={{ marginLeft: "auto" }}><input type="checkbox" checked={openOnly} onChange={(e) => setOpenOnly(e.target.checked)} /> только открытые</label></div>
      {list.length === 0 && <div className="muted" style={{ marginTop: 6 }}>Отклонений нет</div>}
      {list.map((d) => (
        <div key={d.id} style={{ borderBottom: "1px solid var(--line)", padding: "8px 0" }}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <span><b>{d.number}</b> · {d.kind_title} {d.qty} {d.line?.unit} · {d.po_number}, {d.supplier_name}</span><span className="muted">{d.status_title}{d.decision_title && ` · ${d.decision_title}`}</span></div>
          {d.line && <div className="muted" style={{ fontSize: 12 }}>строка {d.line.no}: {d.line.number} {d.line.name} — заказано {d.line.qty}, принято {d.line.received_qty}{d.comment && ` · ${d.comment}`}</div>}
          {d.photos.length > 0 && <div style={{ fontSize: 12 }}>Фото: {d.photos.map((p) => <a key={p.i} href={`/api/parts/stock/deviations/${d.id}/photos/${p.i}`} target="_blank" rel="noreferrer" style={{ marginRight: 6 }}>{p.name}</a>)}</div>}
          {meta.can_edit && d.status !== "closed" && (
            <div className="row" style={{ flexWrap: "wrap", marginTop: 4 }}>
              <select value={edit[d.id]?.decision || ""} onChange={(e) => setEdit({ ...edit, [d.id]: { ...edit[d.id], decision: e.target.value } })}>
                <option value="">решение…</option>{Object.entries(meta.decisions).filter(([k]) => k !== "cancel" || d.kind === "short").map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select>
              <input style={{ minWidth: 260 }} placeholder="текст претензии поставщику" value={edit[d.id]?.claim_text || ""} onChange={(e) => setEdit({ ...edit, [d.id]: { ...edit[d.id], claim_text: e.target.value } })} />
              <button className="btn" disabled={!edit[d.id]?.decision} onClick={() => run(() => api.put(`/api/parts/stock/deviations/${d.id}`, edit[d.id]), "Решение сохранено")}>Сохранить</button>
              {edit[d.id]?.claim_text && !d.claim_sent_at && <button className="btn ghost" onClick={() => run(() => api.post(`/api/parts/stock/deviations/${d.id}/claim-sent`), "Претензия отмечена как отправленная")}>претензия отправлена</button>}
              <label className="btn ghost" style={{ cursor: "pointer" }}>фото<input type="file" accept="image/*" capture="environment" style={{ display: "none" }}
                onChange={(e) => { const f = e.target.files?.[0]; if (f) run(() => api.upload(`/api/parts/stock/deviations/${d.id}/photos`, f), "Фото добавлено"); e.target.value = ""; }} /></label>
              <button className="btn ghost" onClick={() => run(() => api.post(`/api/parts/stock/deviations/${d.id}/close`), "Отклонение закрыто")}>закрыть</button>
            </div>)}
          {d.claim_sent_at && <div className="muted" style={{ fontSize: 12 }}>Претензия отправлена {fmtDate(d.claim_sent_at)}: {d.claim_text}</div>}
        </div>))}
    </div>
  );
}

function Moves() {
  const [list, setList] = useState<Move[]>([]);
  useEffect(() => { api.get<Move[]>("/api/parts/stock/moves?limit=200").then(setList).catch(() => setList([])); }, []);
  return (
    <div className="card">
      <h2 style={{ marginTop: 0 }}>Движения остатков</h2>
      <table>
        <thead><tr><th>Дата</th><th>Документ</th><th>Вид</th><th>Артикул</th><th>Наименование</th><th>Склад</th><th className="num">Кол-во</th><th>Кто</th></tr></thead>
        <tbody>{list.map((m) => (
          <tr key={m.id}><td>{fmtDate(m.at)}</td><td>{m.doc_number}</td><td>{KIND[m.kind] || m.kind}</td><td>{m.article}</td><td>{m.name}</td><td>{m.warehouse}</td>
            <td className="num" style={{ color: m.qty < 0 ? "var(--bad)" : undefined }}>{m.qty > 0 ? `+${m.qty}` : m.qty}</td><td>{m.user}</td></tr>))}
          {list.length === 0 && <tr><td colSpan={8} className="muted">Движений нет</td></tr>}</tbody>
      </table>
    </div>
  );
}
