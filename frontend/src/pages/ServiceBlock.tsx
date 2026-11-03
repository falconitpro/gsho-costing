import { useEffect, useState } from "react";
import { UndoButton, useUndoHistory } from "../undo";
import { api, fmtRub, designation, ru } from "../api";
import type { Deal, Me } from "../types";

type Op = { name: string; hours: number; qualification: string; spare_part_rows: number[] };
type Reg = { name: string; interval_hours: number; operations: Op[] };
type Defaults = { target_hours: number; hours_per_month: number; regulations: Reg[]; spare_parts: { row: number; name: string; unit_price: number; interval_hours: number | null }[]; rates: Record<string, number>; region_norms: Record<string, number>;
  warranty?: { pct: number; by_type_pct: number; overridden: boolean; factory_price: number; currency: string; amount: number } };
type Result = { rows: { month: number; hours_cum: number; regulations: string[]; labour_hours: number; labour_rub: number; visits: number; visit_rub: number; spare_parts_fx: number; total_rub_part: number }[];
  executions: Record<string, number>; labour_rub: number; visits_rub: number; spare_parts_fx: { amount: number; currency: string }; reserve_fx: { amount: number }; total_rub_part: number; total_fx_part: { amount: number; currency: string } };

/** Блок сервисной службы (раздел 6): регламенты по интервалам, нормативы чел·ч, выезды, итог по периодам. */
export default function ServiceBlock({ deal, me, reload }: { deal: Deal; me: Me; reload: () => void }) {
  const canEdit = me.roles.some((r) => r.code === "service_head" || r.code === "gd") && ["logistics_calc", "service_calc", "rework"].includes(deal.status);
  const positions = deal.positions || [];
  const [pid, setPid] = useState(positions[0]?.id || "");
  const [d, setD] = useState<Defaults | null>(null);
  const [regs, setRegs] = useState<Reg[]>([]);
  const [hpm, setHpm] = useState("");
  const [crew, setCrew] = useState(2);
  const [commissioning, setCommissioning] = useState(0);
  const [training, setTraining] = useState(0);
  const [warrantyPct, setWarrantyPct] = useState("");   // пусто — по настройке типа машины
  const [res, setRes] = useState<Result | null>(null);
  const [err, setErr] = useState("");
  const history = useUndoHistory({ regs, hpm, crew, commissioning, training, warrantyPct },
    (st) => { setRegs(st.regs); setHpm(st.hpm); setCrew(st.crew); setCommissioning(st.commissioning); setTraining(st.training); setWarrantyPct(st.warrantyPct); }, pid);

  useEffect(() => {
    if (!pid) return;
    api.get<Defaults>(`/api/deals/${deal.id}/service/positions/${pid}/defaults`).then((x) => {
      history.accept(); setD(x); setHpm(String(x.hours_per_month)); setWarrantyPct(x.warranty?.overridden ? String(x.warranty.pct) : "");
      setRegs(x.regulations.length ? x.regulations.map((r) => ({ ...r, name: ru(r.name), operations: r.operations.map((o) => ({ ...o, name: ru(o.name) })) })) : [{ name: "ТО-1", interval_hours: 250, operations: [{ name: "Осмотр, смазка", hours: 2, qualification: "mechanic", spare_part_rows: [] }] },
        { name: "ТО-2", interval_hours: 500, operations: [{ name: "Замена масла и фильтров двигателя", hours: 4, qualification: "engineer_2", spare_part_rows: [] }] }]);
      const existing = (positions.find((p) => p.id === pid)?.service_calc as { result?: Result } | undefined)?.result;
      setRes(existing || null);
    }).catch((e) => setErr(e.message));
  }, [pid, deal.id]);

  async function calc() {
    try {
      const r = await api.post<Result>(`/api/deals/${deal.id}/service/positions/${pid}/calc`, { regulations: regs, hours_per_month: Number(hpm), crew_size: crew, commissioning_rub: commissioning, training_rub: training, show_in_kp: true,
        warranty_reserve_pct: warrantyPct === "" ? null : Number(warrantyPct.replace(",", ".")) });
      setRes(r); setErr(""); reload();
    } catch (e) { setErr((e as Error).message); }
  }
  const updReg = (i: number, patch: Partial<Reg>) => setRegs(regs.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const updOp = (i: number, k: number, patch: Partial<Op>) => updReg(i, { operations: regs[i].operations.map((o, j) => (j === k ? { ...o, ...patch } : o)) });

  return (
    <>
      {err && <div className="alert error">{err}</div>}
      <div className="card">
        <div className="row">
          <label className="f">Позиция<select value={pid} onChange={(e) => setPid(e.target.value)}>{positions.map((p) => <option key={p.id} value={p.id}>{designation(p)} × {p.qty}</option>)}</select></label>
          <label className="f">Целевая наработка, м·ч<input value={d?.target_hours ?? ""} disabled /></label>
          <label className="f">Интенсивность, м·ч/мес<input type="number" value={hpm} disabled={!canEdit} onChange={(e) => setHpm(e.target.value)} /></label>
          <label className="f">Бригада, чел<input type="number" value={crew} disabled={!canEdit} onChange={(e) => setCrew(Number(e.target.value))} /></label>
          <label className="f">Шеф-монтаж и ПНР, RUB<input type="number" value={commissioning} disabled={!canEdit} onChange={(e) => setCommissioning(Number(e.target.value))} /></label>
          <label className="f">Обучение (14.8), RUB<input type="number" value={training} disabled={!canEdit} onChange={(e) => setTraining(Number(e.target.value))} /></label>
        </div>
        {d?.warranty && (
          <div className="row" style={{ marginTop: 8, padding: 8, background: "#fafafa", borderRadius: 4, flexWrap: "wrap" }}>
            <b>Гарантийные случаи</b>
            <label className="row">% от цены завода
              <input style={{ width: 70 }} inputMode="decimal" value={warrantyPct} placeholder={String(d.warranty.by_type_pct).replace(".", ",")} disabled={!canEdit} onChange={(e) => setWarrantyPct(e.target.value)} /></label>
            <span className="muted">≈ {fmtRub((d.warranty.factory_price * (warrantyPct === "" ? d.warranty.by_type_pct : Number(warrantyPct.replace(",", ".")) || 0)) / 100)} {d.warranty.currency}
              {" "}(статья 13 себестоимости; пусто — по настройке для типа машины: {String(d.warranty.by_type_pct).replace(".", ",")} %; сохраняется кнопкой «Рассчитать сервис»)</span>
          </div>)}
        <div className="muted" style={{ marginTop: 6 }}>Ставки чел·ч: {Object.entries(d?.rates || {}).map(([k, v]) => `${k} ${fmtRub(v)}`).join(" · ")} · регион: проезд {fmtRub(d?.region_norms.travel_rub)}, суточные {fmtRub(d?.region_norms.per_diem_rub)}</div>
      </div>
      <div className="card">
        <h2 style={{ marginTop: 0 }}>Регламентные работы (выполнений = ЦЕЛОЕ(наработка / периодичность))</h2>
        <table><thead><tr><th>Регламент</th><th className="num">Периодичность, м·ч</th><th>Операция</th><th className="num">Норматив, чел·ч</th><th>Квалификация</th><th>ЗИП (строки листа)</th></tr></thead>
          <tbody>{regs.map((r, i) => r.operations.map((o, k) => (
            <tr key={`${i}-${k}`}>
              <td>{k === 0 && <input value={r.name} disabled={!canEdit} onChange={(e) => updReg(i, { name: e.target.value })} style={{ width: 90 }} />}</td>
              <td className="num">{k === 0 && <input type="number" value={r.interval_hours} disabled={!canEdit} onChange={(e) => updReg(i, { interval_hours: Number(e.target.value) })} style={{ width: 90 }} />}</td>
              <td><input value={o.name} disabled={!canEdit} onChange={(e) => updOp(i, k, { name: e.target.value })} style={{ width: "100%" }} /></td>
              <td className="num"><input type="number" step="0.5" value={o.hours} disabled={!canEdit} onChange={(e) => updOp(i, k, { hours: Number(e.target.value) })} style={{ width: 70 }} /></td>
              <td><select value={o.qualification} disabled={!canEdit} onChange={(e) => updOp(i, k, { qualification: e.target.value })}>{Object.keys(d?.rates || { engineer_2: 0 }).map((q) => <option key={q}>{q}</option>)}</select></td>
              <td><input value={o.spare_part_rows.join(",")} disabled={!canEdit} placeholder="напр. 4,5" onChange={(e) => updOp(i, k, { spare_part_rows: e.target.value.split(",").map((x) => Number(x.trim())).filter((x) => x > 0) })} style={{ width: 90 }} /></td>
            </tr>)))}</tbody></table>
        {canEdit && <div className="row" style={{ marginTop: 8 }}>
          <button className="btn ghost" onClick={() => setRegs([...regs, { name: "ТО-3", interval_hours: 1000, operations: [{ name: "", hours: 1, qualification: "engineer_2", spare_part_rows: [] }] }])}>+ регламент</button>
          <button className="btn ghost" onClick={() => setRegs(regs.map((r, i) => (i === regs.length - 1 ? { ...r, operations: [...r.operations, { name: "", hours: 1, qualification: "mechanic", spare_part_rows: [] }] } : r)))}>+ операция в последний</button>
          <button className="btn" onClick={calc}>Рассчитать сервис</button><UndoButton h={history} />
        </div>}
        {d && d.spare_parts.length > 0 && <details style={{ marginTop: 8 }}><summary className="muted">Лист «ЗИП на ТО» из RFQ ({d.spare_parts.length} строк)</summary>
          <table><tbody>{d.spare_parts.map((s) => <tr key={s.row}><td>{s.row}</td><td>{ru(s.name) || "без перевода"}</td><td className="num">{s.interval_hours ?? "—"}</td><td className="num">{fmtRub(s.unit_price)}</td></tr>)}</tbody></table></details>}
      </div>
      {res && (
        <div className="card">
          <h2 style={{ marginTop: 0 }}>Сервис по периодам</h2>
          <div className="row" style={{ gap: 20, marginBottom: 8 }}>
            <div className="kpi"><span className="v">{fmtRub(res.labour_rub)}</span><span className="l">работы, RUB</span></div>
            <div className="kpi"><span className="v">{fmtRub(res.visits_rub)}</span><span className="l">выезды, RUB</span></div>
            <div className="kpi"><span className="v">{fmtRub(res.spare_parts_fx.amount)} {res.spare_parts_fx.currency}</span><span className="l">ЗИП</span></div>
            <div className="kpi"><span className="v">{fmtRub(res.reserve_fx.amount)}</span><span className="l">резерв на внеплановые</span></div>
            <div className="kpi"><span className="v">{fmtRub(res.total_rub_part)} RUB + {fmtRub(res.total_fx_part.amount)} {res.total_fx_part.currency}</span><span className="l">итого (статья 12)</span></div>
          </div>
          <div className="muted">Выполнений: {Object.entries(res.executions).map(([k, v]) => `${k} × ${v}`).join(", ")}</div>
          <table><thead><tr><th>Месяц</th><th className="num">Наработка</th><th>Регламенты</th><th className="num">Чел·ч</th><th className="num">Работы</th><th className="num">Выезды</th><th className="num">Выезды, RUB</th><th className="num">ЗИП</th></tr></thead>
            <tbody>{res.rows.filter((r) => r.regulations.length).map((r) => <tr key={r.month}><td>{r.month}</td><td className="num">{r.hours_cum}</td><td>{r.regulations.map(ru).join(", ")}</td><td className="num">{r.labour_hours}</td><td className="num">{fmtRub(r.labour_rub)}</td><td className="num">{r.visits}</td><td className="num">{fmtRub(r.visit_rub)}</td><td className="num">{fmtRub(r.spare_parts_fx)}</td></tr>)}</tbody></table>
          {canEdit && <button className="btn" style={{ marginTop: 8 }} onClick={() => api.post(`/api/deals/${deal.id}/service/complete`).then(reload).catch((e) => setErr(e.message))}>Завершить блок сервиса</button>}
        </div>
      )}
    </>
  );
}
