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
type Meta = { warehouses: { id: string; name: string }[]; discrepancy: Record<string, string>; decisions: Record<string, string>; can_edit: boolean; can_approve?: boolean };
type Row = { qty: string; qty_bad: string; discrepancy: string; cell: string; comment: string };

const KIND: Record<string, string> = { receipt: "приёмка", shipment: "отгрузка", adjust: "инвентаризация" };

export default function PartsWarehouse() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [tab, setTab] = useState<"receipt" | "dev" | "inv" | "repl" | "moves">("receipt");
  const [msg, setMsg] = useState("");
  useEffect(() => { api.get<Meta>("/api/parts/stock/meta").then(setMeta).catch((e) => setMsg((e as Error).message)); }, []);
  if (!meta) return msg ? <div className="alert info">{msg}</div> : null;
  return (
    <>
      {msg && <div className="alert info">{msg}</div>}
      <div className="row" style={{ marginBottom: 8 }}>
        <button className={`btn ${tab === "receipt" ? "" : "ghost"}`} onClick={() => setTab("receipt")}>Приёмка</button>
        <button className={`btn ${tab === "dev" ? "" : "ghost"}`} onClick={() => setTab("dev")}>Отклонения</button>
        <button className={`btn ${tab === "inv" ? "" : "ghost"}`} onClick={() => setTab("inv")}>Инвентаризация</button>
        <button className={`btn ${tab === "repl" ? "" : "ghost"}`} onClick={() => setTab("repl")}>Пополнение</button>
        <button className={`btn ${tab === "moves" ? "" : "ghost"}`} onClick={() => setTab("moves")}>Движения</button>
      </div>
      {tab === "receipt" && <Receipt meta={meta} setMsg={setMsg} />}
      {tab === "dev" && <Deviations meta={meta} setMsg={setMsg} />}
      {tab === "inv" && <Inventory meta={meta} setMsg={setMsg} />}
      {tab === "repl" && <Replenish meta={meta} setMsg={setMsg} />}
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

// ------------------------------------------------------------------ инвентаризация: задание → подсчёт с телефона → утверждение КД

type InvLine = { id: string; article: string; name: string; unit: string; cell: string; expected: number; counted: number | null; added: boolean; diff: number | null; comment: string };
type Inv = { id: string; number: string; warehouse_name: string; cell_prefix: string; part_group: string; status: string; status_title: string; created_at: string;
  comment: string; approved_by_name: string; summary: { lines: number; counted: number; diff_lines: number; surplus: number; shortage: number }; lines?: InvLine[] };

function Inventory({ meta, setMsg }: { meta: Meta; setMsg: (s: string) => void }) {
  const [list, setList] = useState<Inv[]>([]);
  const [cur, setCur] = useState<Inv | null>(null);
  const [f, setF] = useState({ warehouse_id: meta.warehouses[0]?.id || "", cell_prefix: "", part_group: "" });
  const [q, setQ] = useState("");
  const [onlyDiff, setOnlyDiff] = useState(false);
  const [found, setFound] = useState({ article: "", counted: "", cell: "" });
  const load = () => api.get<Inv[]>("/api/parts/stock/inventories").then(setList).catch((e) => setMsg((e as Error).message));
  useEffect(() => { load(); }, []);
  const open = (id: string) => api.get<Inv>(`/api/parts/stock/inventories/${id}`).then(setCur);
  const run = async (fn: () => Promise<Inv>, ok = "") => { try { setCur(await fn()); if (ok) setMsg(ok); load(); } catch (e) { setMsg((e as Error).message); } };
  const count = (l: InvLine, v: string) => { if (v === "" || Number(v.replace(",", ".")) === l.counted) return;
    run(() => api.put<Inv>(`/api/parts/stock/inventories/${cur!.id}/counts`, { lines: [{ line_id: l.id, counted: Number(v.replace(",", ".")) }] })); };
  if (cur) {
    const rows = (cur.lines || []).filter((l) => (!q || `${l.article} ${l.name} ${l.cell}`.toLowerCase().includes(q.toLowerCase())) && (!onlyDiff || (l.diff != null && l.diff !== 0)));
    const counting = cur.status === "counting" && meta.can_edit;
    return (
      <div className="card">
        <div className="row" style={{ justifyContent: "space-between" }}><h2 style={{ margin: 0 }}>{cur.number} · {cur.warehouse_name}</h2><button className="btn ghost" onClick={() => setCur(null)}>назад</button></div>
        <p className="muted" style={{ marginTop: 4 }}>{cur.status_title} · посчитано {cur.summary.counted} из {cur.summary.lines} · расхождений {cur.summary.diff_lines} (излишек {cur.summary.surplus}, недостача {cur.summary.shortage}){cur.comment && ` · ${cur.comment}`}</p>
        <div className="row" style={{ flexWrap: "wrap" }}>
          <input placeholder="артикул, название, ячейка" value={q} onChange={(e) => setQ(e.target.value)} />
          <label className="row"><input type="checkbox" checked={onlyDiff} onChange={(e) => setOnlyDiff(e.target.checked)} /> только расхождения</label>
        </div>
        <table style={{ marginTop: 6 }}>
          <thead><tr><th>Ячейка</th><th>Артикул</th><th>Наименование</th><th className="num">Учёт</th><th className="num">Посчитано</th><th className="num">Разница</th></tr></thead>
          <tbody>{rows.map((l) => (
            <tr key={l.id}><td>{l.cell || "—"}</td><td><b>{l.article}</b>{l.added && <span className="badge" style={{ marginLeft: 4 }}>найдено</span>}</td><td>{l.name}</td><td className="num">{l.expected}</td>
              <td className="num">{counting ? <input inputMode="decimal" style={{ width: 70 }} defaultValue={l.counted ?? ""} key={`${l.id}-${l.counted}`} onBlur={(e) => count(l, e.target.value)} /> : (l.counted ?? "—")}</td>
              <td className="num" style={{ color: l.diff ? "var(--bad)" : undefined }}>{l.diff == null ? "" : l.diff > 0 ? `+${l.diff}` : l.diff}</td></tr>))}</tbody>
        </table>
        {counting && <>
          <div className="row" style={{ marginTop: 8, flexWrap: "wrap" }}>
            <b>Нашли на полке то, чего нет в списке:</b>
            <input placeholder="артикул" value={found.article} onChange={(e) => setFound({ ...found, article: e.target.value })} />
            <input inputMode="decimal" placeholder="кол-во" style={{ width: 70 }} value={found.counted} onChange={(e) => setFound({ ...found, counted: e.target.value })} />
            <input placeholder="ячейка" style={{ width: 80 }} value={found.cell} onChange={(e) => setFound({ ...found, cell: e.target.value })} />
            <button className="btn ghost" disabled={!found.article || !found.counted} onClick={() => run(() => api.put<Inv>(`/api/parts/stock/inventories/${cur.id}/counts`,
              { lines: [{ article: found.article.trim(), counted: Number(found.counted.replace(",", ".")), cell: found.cell }] })).then(() => setFound({ article: "", counted: "", cell: "" }))}>добавить</button>
          </div>
          <div className="row" style={{ marginTop: 8 }}>
            <button className="btn" onClick={() => { const left = cur.summary.lines - cur.summary.counted;
              if (left && !confirm(`Не посчитано строк: ${left}. Они будут считаться нулём (недостача). Отправить КД?`)) return;
              run(() => api.post<Inv>(`/api/parts/stock/inventories/${cur.id}/submit`), "Отправлено КД на утверждение"); }}>Подсчёт завершён — на утверждение КД</button>
            <button className="btn ghost" onClick={() => { if (confirm("Отменить инвентаризацию? Остатки не изменятся.")) run(() => api.post<Inv>(`/api/parts/stock/inventories/${cur.id}/cancel`), "Отменена"); }}>отменить</button>
          </div></>}
        {cur.status === "submitted" && meta.can_approve && (
          <div className="row" style={{ marginTop: 8 }}>
            <button className="btn" onClick={() => run(() => api.post<Inv>(`/api/parts/stock/inventories/${cur.id}/approve`, {}), "Утверждено: остатки исправлены, движения записаны")}>Утвердить и исправить остатки</button>
            <button className="btn ghost" onClick={() => { const c = prompt("Что пересчитать?") ?? ""; run(() => api.post<Inv>(`/api/parts/stock/inventories/${cur.id}/approve`, { approve: false, comment: c }), "Возвращено на пересчёт"); }}>вернуть на пересчёт</button>
          </div>)}
      </div>);
  }
  return (
    <div className="card">
      <h2 style={{ marginTop: 0 }}>Инвентаризация</h2>
      {meta.can_edit && (
        <div className="row" style={{ flexWrap: "wrap", alignItems: "flex-end" }}>
          <label className="f">Склад<select value={f.warehouse_id} onChange={(e) => setF({ ...f, warehouse_id: e.target.value })}>{meta.warehouses.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}</select></label>
          <label className="f">Ячейки, начинаются с<input style={{ width: 90 }} value={f.cell_prefix} onChange={(e) => setF({ ...f, cell_prefix: e.target.value })} placeholder="все" /></label>
          <label className="f">Группа товара<input value={f.part_group} onChange={(e) => setF({ ...f, part_group: e.target.value })} placeholder="все" /></label>
          <button className="btn add" disabled={!f.warehouse_id} onClick={() => run(() => api.post<Inv>("/api/parts/stock/inventories", f), "Задание создано — считайте по списку")}>Новая инвентаризация</button>
        </div>)}
      <table style={{ marginTop: 8 }}>
        <thead><tr><th>Номер</th><th>Дата</th><th>Склад</th><th>Отбор</th><th className="num">Строк</th><th className="num">Расхождений</th><th>Статус</th></tr></thead>
        <tbody>{list.map((i) => (
          <tr key={i.id} style={{ cursor: "pointer" }} onClick={() => open(i.id)}><td><b>{i.number}</b></td><td>{fmtDate(i.created_at)}</td><td>{i.warehouse_name}</td>
            <td>{[i.cell_prefix && `ячейки ${i.cell_prefix}…`, i.part_group].filter(Boolean).join(", ") || "весь склад"}</td>
            <td className="num">{i.summary.counted} / {i.summary.lines}</td><td className="num">{i.summary.diff_lines}</td><td>{i.status_title}</td></tr>))}
          {list.length === 0 && <tr><td colSpan={7} className="muted">Инвентаризаций не было</td></tr>}</tbody>
      </table>
      <p className="muted" style={{ fontSize: 12 }}>Учётный остаток фиксируется при создании задания; при утверждении разница проводится движением «инвентаризация», поступления и отгрузки во время подсчёта не теряются. Выгрузка в 1С — после подключения обмена.</p>
    </div>
  );
}

// ------------------------------------------------------------------ пополнение по минимальному остатку

type ReplLine = { part_id: string; article: string; name: string; unit: string; warehouse: string; qty: number; reserved: number; free: number; incoming: number;
  min_qty: number; suggest: number; moq: number | null; multiple: number | null; price: number | null; currency: string; priced: boolean };
type ReplGroup = { supplier_id: string | null; supplier_name: string; lines: ReplLine[] };

function Replenish({ meta, setMsg }: { meta: Meta; setMsg: (s: string) => void }) {
  const [list, setList] = useState<ReplGroup[]>([]);
  const [qty, setQty] = useState<Record<string, string>>({});
  const load = () => api.get<ReplGroup[]>("/api/parts/stock/replenishment").then((g) => {
    setList(g); setQty(Object.fromEntries(g.flatMap((x) => x.lines).map((l) => [l.part_id, String(l.suggest)])));
  }).catch((e) => setMsg((e as Error).message));
  useEffect(() => { load(); }, []);
  async function order(g: ReplGroup) {
    try {
      const lines = g.lines.filter((l) => l.priced && Number(qty[l.part_id]) > 0).map((l) => ({ part_id: l.part_id, qty: Number(qty[l.part_id].replace(",", ".")) }));
      const r = await api.post<{ number: string }[]>("/api/parts/po", { supplier_id: g.supplier_id, stock_lines: lines });
      setMsg(`Заказ на склад создан: ${r.map((x) => x.number).join(", ")} — отправьте его во вкладке «Заказы поставщикам»`);
      load();
    } catch (e) { setMsg((e as Error).message); }
  }
  return (
    <div className="card">
      <h2 style={{ marginTop: 0 }}>Пополнение склада</h2>
      <p className="muted" style={{ marginTop: 0 }}>Позиции, у которых свободный остаток с учётом ожидаемого по заказам на склад ниже минимального. Минимальный остаток задаётся в карточке запчасти (блок «Остатки»).</p>
      {list.length === 0 && <div className="muted">Всё выше минимума</div>}
      {list.map((g) => (
        <div key={g.supplier_id || "none"} style={{ marginBottom: 10 }}>
          <div className="row" style={{ justifyContent: "space-between" }}><b>{g.supplier_name}</b>
            {meta.can_edit && g.supplier_id && <button className="btn" onClick={() => order(g)}>Заказать на склад</button>}</div>
          <table>
            <thead><tr><th>Артикул</th><th>Наименование</th><th>Склад</th><th className="num">Свободно</th><th className="num">Ожидается</th><th className="num">Минимум</th><th className="num">Заказать</th><th className="num">Цена</th></tr></thead>
            <tbody>{g.lines.map((l) => (
              <tr key={l.part_id + l.warehouse}><td><b>{l.article}</b></td><td>{l.name}</td><td>{l.warehouse}</td><td className="num">{l.free}</td><td className="num">{l.incoming}</td><td className="num">{l.min_qty}</td>
                <td className="num"><input inputMode="decimal" style={{ width: 70 }} value={qty[l.part_id] ?? ""} onChange={(e) => setQty({ ...qty, [l.part_id]: e.target.value })} />
                  {(l.moq || l.multiple) && <div className="muted" style={{ fontSize: 11 }}>{[l.moq && `мин. ${l.moq}`, l.multiple && `кратно ${l.multiple}`].filter(Boolean).join(", ")}</div>}</td>
                <td className="num">{l.priced ? (l.price != null ? `${l.price} ${l.currency}` : "есть") : <span className="badge bad">нет цены</span>}</td></tr>))}</tbody>
          </table>
          {!g.supplier_id && <div className="muted" style={{ fontSize: 12 }}>Укажите поставщика в карточках этих позиций, чтобы заказать.</div>}
        </div>))}
    </div>
  );
}
