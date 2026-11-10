import { useEffect, useState } from "react";
import { api } from "../api";

// Правила внутригрупповых цепочек по умолчанию (D-164): через какую компанию группы идёт закупка — по виду сделки, стране или поставщику.
// Менеджер выбирает конечного поставщика, цепочку подставляет правило; ведут ФД и ЗГД.

type Rule = { id?: string; kind: string; country: string; supplier_id: string; route_id: string | null; note: string; supplier_name?: string; route_name?: string };
type Data = { rules: Rule[]; kinds: Record<string, string>; can_edit: boolean; countries: string[] };
type Route = { id: string; name: string; is_active: boolean; steps: unknown[] };
type Sup = { id: string; name: string; short_name: string; country: string; supplier_status: string };

const W = { width: "100%", minWidth: 0, boxSizing: "border-box" as const };   // поле — по ширине своей колонки, таблица не выходит за карточку

export default function ChainRulesBlock() {
  const [d, setD] = useState<Data | null>(null);
  const [rows, setRows] = useState<Rule[]>([]);
  const [routes, setRoutes] = useState<Route[]>([]);
  const [sups, setSups] = useState<Sup[]>([]);
  const [msg, setMsg] = useState("");
  const load = () => api.get<Data>("/api/parts/intragroup-rules").then((x) => { setD(x); setRows(x.rules); }).catch((e) => setMsg((e as Error).message));
  useEffect(() => { load(); api.get<Route[]>("/api/parts/routes").then((r) => setRoutes(r.filter((x) => x.is_active && x.steps.length >= 2))).catch(() => undefined);
    api.get<Sup[]>("/api/refs/factories").then((f) => setSups(f.filter((x) => x.supplier_status !== "group_company"))).catch(() => undefined); }, []);
  if (!d) return <div className="muted">{msg || "Загрузка…"}</div>;
  const ro = !d.can_edit;
  const set = (i: number, p: Partial<Rule>) => setRows(rows.map((r, j) => (j === i ? { ...r, ...p } : r)));
  const save = () => api.put<Data>("/api/parts/intragroup-rules", { rules: rows.map((r) => ({ ...r, route_id: r.route_id || null })) })
    .then((x) => { setD(x); setRows(x.rules); setMsg("Правила сохранены — применяются к новым запросам"); }).catch((e) => setMsg((e as Error).message));
  return (
    <div>
      <p className="muted" style={{ fontSize: 12, marginTop: 0 }}>Менеджер выбирает конечного поставщика; через какую компанию группы пойдёт закупка, решает правило. Подходит самое точное:
        поставщик → страна → «любая страна»; вид сделки — точный, затем «любая». Нет правила — запрос напрямую от Берг-Хауса. В сделке ФД или ЗГД может поменять цепочку и утверждает её до КП.</p>
      {msg && <div className="alert info" onClick={() => setMsg("")}>{msg}</div>}
      <div style={{ overflowX: "auto" }}>
      <table style={{ tableLayout: "fixed", width: "100%", minWidth: 760 }}>
        <colgroup><col style={{ width: 130 }} /><col style={{ width: 150 }} /><col style={{ width: "24%" }} /><col style={{ width: "30%" }} /><col />{!ro && <col style={{ width: 56 }} />}</colgroup>
        <thead><tr><th>Вид сделки</th><th>Страна поставщика</th><th>Поставщик (необязательно)</th><th>Цепочка</th><th>Примечание</th>{!ro && <th></th>}</tr></thead>
        <tbody>{rows.map((r, i) => (
          <tr key={r.id || i}>
            <td><select style={W} value={r.kind} disabled={ro} onChange={(e) => set(i, { kind: e.target.value })}>{Object.entries(d.kinds).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select></td>
            <td><select style={W} value={r.supplier_id ? "*" : r.country} disabled={ro || !!r.supplier_id} title={r.supplier_id ? "правило для конкретного поставщика — страна не нужна" : undefined}
              onChange={(e) => set(i, { country: e.target.value })}><option value="*">любая страна</option>
              {[...(d.countries.includes(r.country) || r.country === "*" ? [] : [r.country]), ...d.countries].map((c) => <option key={c} value={c}>{c}</option>)}</select></td>
            <td><select style={W} value={r.supplier_id} disabled={ro} onChange={(e) => set(i, { supplier_id: e.target.value })}><option value="">— любой —</option>
              {sups.map((s) => <option key={s.id} value={s.id}>{s.short_name || s.name} · {s.country}</option>)}</select></td>
            <td><select style={W} value={r.route_id || ""} disabled={ro} onChange={(e) => set(i, { route_id: e.target.value || null })}><option value="">напрямую от Берг-Хауса</option>
              {routes.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></td>
            <td><input style={W} value={r.note} disabled={ro} onChange={(e) => set(i, { note: e.target.value })} /></td>
            {!ro && <td><button className="btn ghost" style={{ padding: "0 10px" }} title="удалить правило" onClick={() => setRows(rows.filter((_, j) => j !== i))}>×</button></td>}
          </tr>))}
          {rows.length === 0 && <tr><td colSpan={6} className="muted">Правил нет — все запросы уходят напрямую.</td></tr>}</tbody>
      </table>
      </div>
      {!ro && <div className="row" style={{ marginTop: 6 }}>
        <button className="btn ghost" onClick={() => setRows([...rows, { kind: "gsho", country: d.countries.includes("Китай") ? "Китай" : "*", supplier_id: "", route_id: routes[0]?.id || null, note: "" }])}>+ правило</button>
        <button className="btn" onClick={save}>Сохранить правила</button></div>}
      {ro && <p className="muted" style={{ fontSize: 12 }}>Правила меняют ФД или ЗГД.</p>}
    </div>);
}
