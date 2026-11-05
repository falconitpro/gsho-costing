import { useEffect, useState } from "react";
import { api, fmtDate, fmtRub } from "../api";

// Сборные партии у перевозчика или экспедитора: строки заказов поставщикам, статусы перевозки, расходы и их распределение по строкам (ТЗ запчастей, раздел 7).

type Cand = { id: string; po_number: string; supplier_name: string; no: number; number: string; name: string; qty: number; unit: string; status: string; eta: string | null };
type BLine = { id: string; po_number: string; no: number; number: string; name: string; qty: number; unit: string; status: string; eta: string | null; weight_kg: number | null;
  request_number: string; extra_cost_rub: number | null };
type Batch = { id: string; number: string; forwarder: string; status: string; status_title: string; shipped_on: string | null; eta: string | null; tracking: string;
  allocation: string; comment: string; lines: BLine[]; total_weight_kg: number; costs?: { item: string; amount: number; currency: string }[]; costs_rub?: number };

const STATUS: Record<string, string> = { forming: "формируется у экспедитора", transit: "в пути", customs: "таможня", arrived: "прибыла", closed: "закрыта" };
const LINE: Record<string, string> = { confirmed: "подтверждено", shipped: "отгружено поставщиком", consolidation: "на консолидации", transit: "в пути", customs: "таможня", received: "на складе" };

export default function PartsBatches() {
  const [list, setList] = useState<Batch[]>([]);
  const [cands, setCands] = useState<Cand[]>([]);
  const [sel, setSel] = useState<string[]>([]);
  const [fwd, setFwd] = useState("");
  const [cur, setCur] = useState<Batch | null>(null);
  const [msg, setMsg] = useState("");
  const load = () => {
    api.get<Batch[]>("/api/parts/batches").then(setList).catch((e) => setMsg((e as Error).message));
    api.get<Cand[]>("/api/parts/batches/candidates").then(setCands).catch(() => setCands([]));
  };
  useEffect(() => { load(); }, []);
  async function act(fn: () => Promise<Batch>, ok = "") { try { setCur(await fn()); if (ok) setMsg(ok); load(); } catch (e) { setMsg((e as Error).message); } }
  return (
    <>
      {msg && <div className="alert info">{msg}</div>}
      <div className="card">
        <h2 style={{ marginTop: 0 }}>Можно собрать в партию</h2>
        <p className="muted" style={{ marginTop: 0 }}>Подтверждённые и отгруженные поставщиками строки заказов. Партия у экспедитора ведёт их статус («на консолидации» → «в пути» → «таможня») и дату прибытия — в заявках клиентов они обновятся сами.</p>
        {cands.length === 0 ? <div className="muted">Нет строк</div> : (
          <>
            <table><thead><tr><th></th><th>Заказ</th><th>Поставщик</th><th>Позиция</th><th className="num">Кол-во</th><th>Статус</th><th>На складе ≈</th></tr></thead>
              <tbody>{cands.map((x) => (
                <tr key={x.id}><td><input type="checkbox" checked={sel.includes(x.id)} onChange={(e) => setSel(e.target.checked ? [...sel, x.id] : sel.filter((y) => y !== x.id))} /></td>
                  <td>{x.po_number}</td><td>{x.supplier_name}</td><td>{x.number} {x.name}</td><td className="num">{x.qty} {x.unit}</td><td>{LINE[x.status] || x.status}</td><td>{fmtDate(x.eta)}</td></tr>))}</tbody></table>
            <div className="row" style={{ marginTop: 6 }}>
              <input placeholder="экспедитор / перевозчик" value={fwd} onChange={(e) => setFwd(e.target.value)} />
              <button className="btn" disabled={!sel.length} onClick={() => act(() => api.post<Batch>("/api/parts/batches", { forwarder: fwd, po_line_ids: sel }), "Партия создана").then(() => { setSel([]); setFwd(""); })}>Новая партия ({sel.length})</button>
              {cur && cur.status !== "closed" && <button className="btn ghost" disabled={!sel.length} onClick={() => act(() => api.put<Batch>(`/api/parts/batches/${cur.id}`, { add_line_ids: sel }), "Добавлено в партию").then(() => setSel([]))}>добавить в {cur.number}</button>}
            </div>
          </>)}
      </div>
      <div className="card">
        <h2 style={{ marginTop: 0 }}>Партии</h2>
        <table><thead><tr><th>Номер</th><th>Экспедитор</th><th className="num">Строк</th><th className="num">Вес, кг</th><th>Отправлена</th><th>Прибытие ≈</th><th>Статус</th></tr></thead>
          <tbody>{list.map((b) => (
            <tr key={b.id} style={{ cursor: "pointer", background: cur?.id === b.id ? "var(--accent-soft)" : undefined }} onClick={() => setCur(b)}>
              <td><b>{b.number}</b></td><td>{b.forwarder}</td><td className="num">{b.lines.length}</td><td className="num">{b.total_weight_kg || "—"}</td><td>{fmtDate(b.shipped_on)}</td><td>{fmtDate(b.eta)}</td><td>{b.status_title}</td></tr>))}
            {list.length === 0 && <tr><td colSpan={7} className="muted">Партий нет</td></tr>}</tbody></table>
      </div>
      {cur && <BatchCard b={cur} act={act} />}
    </>
  );
}

function BatchCard({ b, act }: { b: Batch; act: (fn: () => Promise<Batch>, ok?: string) => Promise<void> }) {
  const [f, setF] = useState({ status: b.status, eta: b.eta || "", tracking: b.tracking, comment: "" });
  const [costs, setCosts] = useState(b.costs || []);
  useEffect(() => { setF({ status: b.status, eta: b.eta || "", tracking: b.tracking, comment: "" }); setCosts(b.costs || []); }, [b.id, b.status]);
  const live = b.status !== "closed";
  const order = Object.keys(STATUS);
  return (
    <div className="card">
      <h2 style={{ marginTop: 0 }}>{b.number}{b.forwarder && ` · ${b.forwarder}`}</h2>
      {live && (
        <div className="row" style={{ flexWrap: "wrap" }}>
          <label className="f">Статус<select value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}>{order.filter((k) => order.indexOf(k) >= order.indexOf(b.status)).map((k) => <option key={k} value={k}>{STATUS[k]}</option>)}</select></label>
          <label className="f">Прибытие на склад ≈<input type="date" value={f.eta} onChange={(e) => setF({ ...f, eta: e.target.value })} /></label>
          <label className="f">Трек / № документа<input value={f.tracking} onChange={(e) => setF({ ...f, tracking: e.target.value })} /></label>
          <label className="f">Комментарий (причина сдвига)<input value={f.comment} onChange={(e) => setF({ ...f, comment: e.target.value })} /></label>
        </div>)}
      {b.costs !== undefined && (
        <>
          <h3>Расходы партии</h3>
          {costs.map((x, i) => (
            <div key={i} className="row"><input placeholder="статья (фрахт, СВХ, брокер)" value={x.item} disabled={!live} onChange={(e) => setCosts(costs.map((y, j) => (j === i ? { ...y, item: e.target.value } : y)))} />
              <input inputMode="decimal" style={{ width: 110 }} value={x.amount} disabled={!live} onChange={(e) => setCosts(costs.map((y, j) => (j === i ? { ...y, amount: Number(e.target.value.replace(",", ".")) || 0 } : y)))} />
              <select value={x.currency} disabled={!live} onChange={(e) => setCosts(costs.map((y, j) => (j === i ? { ...y, currency: e.target.value } : y)))}>{["RUB", "CNY", "USD", "EUR", "KGS"].map((c) => <option key={c}>{c}</option>)}</select>
              {live && <button className="btn ghost" onClick={() => setCosts(costs.filter((_, j) => j !== i))}>×</button>}</div>))}
          {live && <button className="btn ghost" onClick={() => setCosts([...costs, { item: "", amount: 0, currency: "RUB" }])}>+ расход</button>}
          <div className="muted" style={{ fontSize: 12 }}>Итого {fmtRub(b.costs_rub || 0)} ₽, распределяется по строкам {b.allocation === "weight" ? "по весу (без веса — по стоимости)" : "по стоимости"}.</div>
        </>)}
      {live && <button className="btn" style={{ marginTop: 8 }} onClick={() => act(() => api.put<Batch>(`/api/parts/batches/${b.id}`, { status: f.status, eta: f.eta || null, tracking: f.tracking, comment: f.comment,
        ...(b.costs !== undefined ? { costs } : {}) }), "Партия обновлена, строки заказов и заявки клиентов — тоже")}>Сохранить</button>}
      <h3>Строки</h3>
      <table><thead><tr><th>Заказ</th><th>Позиция</th><th className="num">Кол-во</th><th className="num">Вес, кг</th><th>Заявка</th><th>Статус</th><th>На складе ≈</th>{b.costs !== undefined && <th className="num">Расходы, ₽</th>}<th></th></tr></thead>
        <tbody>{b.lines.map((l) => (
          <tr key={l.id}><td>{l.po_number}</td><td>{l.number} {l.name}</td><td className="num">{l.qty} {l.unit}</td><td className="num">{l.weight_kg ?? "—"}</td><td>{l.request_number || <span className="muted">на склад</span>}</td>
            <td>{LINE[l.status] || l.status}</td><td>{fmtDate(l.eta)}</td>{b.costs !== undefined && <td className="num">{l.extra_cost_rub != null ? fmtRub(l.extra_cost_rub) : "—"}</td>}
            <td>{live && b.status === "forming" && <button className="btn ghost" onClick={() => act(() => api.put<Batch>(`/api/parts/batches/${b.id}`, { remove_line_ids: [l.id] }), "Строка убрана из партии")}>×</button>}</td></tr>))}</tbody></table>
    </div>
  );
}
