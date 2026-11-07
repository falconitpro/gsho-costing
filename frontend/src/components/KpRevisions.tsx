import { useEffect, useState } from "react";
import { api, fmtDate, fmtRub } from "../api";

type Adj = { kind: "discount" | "markup"; mode: "pct" | "amount"; value: string; label: string };
type Extra = { name: string; price_rub: string; included: boolean };
type VarCh = { adjustments: Adj[]; extras: Extra[]; configuration: string; service_in_kp: boolean | null };
type Variant = { id: string; name: string; price_rub: number; service_flag: boolean; service_in_kp: boolean; configuration: string };
type Row = { id: string | null; no: number; at: string | null; author: string; description: string; price_rub: number | null; price_before_rub: number | null;
  margin_pct: number | null; has_file: boolean; sent_at: string | null };
type Totals = Record<string, { name: string; base: number; extras: [string, number | null][]; adjustments: [string, number][]; total: number; cost: number | null; margin_pct: number | null }>;

const pct = (v: number | null | undefined) => (v == null ? "—" : `${v.toFixed(2).replace(".", ",")} %`);

/** История КП и его изменение (КД / ЗГД): скидки и надбавки, услуги, комплектация — новая редакция с файлом и описанием. */
export default function KpRevisions({ dealId, status, onDone }: { dealId: string; status: string; onDone: () => void }) {
  const [rows, setRows] = useState<Row[]>([]);
  const [variants, setVariants] = useState<Variant[]>([]);
  const [canEdit, setCanEdit] = useState(false);
  const [thr, setThr] = useState(3);
  const [open, setOpen] = useState(false);
  const [ch, setCh] = useState<Record<string, VarCh>>({});
  const [desc, setDesc] = useState("");
  const [totals, setTotals] = useState<Totals | null>(null);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const ready = ["approved", "kp_sent", "contract", "execution", "closed"].includes(status);
  const load = () => api.get<{ rows: Row[]; variants: Variant[]; can_edit: boolean; threshold_pct: number }>(`/api/deals/${dealId}/kp/revisions`).then((r) => {
    setRows(r.rows); setVariants(r.variants); setCanEdit(r.can_edit); setThr(r.threshold_pct ?? 3);
  }).catch(() => undefined);
  useEffect(() => { if (ready) load(); }, [dealId, status]);
  if (!ready) return null;

  const start = () => {
    setCh(Object.fromEntries(variants.map((v) => [v.id, { adjustments: [], extras: [], configuration: v.configuration, service_in_kp: v.service_flag ? v.service_in_kp : null }])));
    setDesc(""); setTotals(null); setOpen(true); setMsg("");
  };
  const payload = () => Object.fromEntries(Object.entries(ch).map(([vid, c]) => [vid, {
    adjustments: c.adjustments.filter((a) => Number(a.value)).map((a) => ({ ...a, value: Number(String(a.value).replace(",", ".")) })),
    extras: c.extras.filter((e) => e.name.trim()).map((e) => ({ name: e.name, price_rub: e.included ? null : Number(String(e.price_rub).replace(",", ".")) || 0 })),
    configuration: c.configuration, service_in_kp: c.service_in_kp,
  }]));
  async function preview() {
    try { setTotals((await api.post<{ variants: Totals }>(`/api/deals/${dealId}/kp/revisions/preview`, { changes: payload() })).variants); setMsg(""); }
    catch (e) { setMsg((e as Error).message); }
  }
  async function create() {
    setBusy(true);
    try {
      const r = await api.post<{ no: number; price_rub: number }>(`/api/deals/${dealId}/kp/revisions`, { changes: payload(), description: desc });
      setMsg(`Сформирована редакция КП №${r.no}: ${fmtRub(r.price_rub)} RUB без НДС`); setOpen(false); load(); onDone();
    } catch (e) { setMsg((e as Error).message); } finally { setBusy(false); }
  }
  const upd = (vid: string, p: Partial<VarCh>) => { setCh({ ...ch, [vid]: { ...ch[vid], ...p } }); setTotals(null); };

  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 style={{ marginTop: 0 }}>История КП</h2>
        {canEdit && !open && <button className="btn secondary" onClick={start}>Изменить КП: скидки, услуги, комплектация</button>}
      </div>
      {msg && <div className="alert info">{msg}</div>}
      <table>
        <thead><tr><th>№</th><th>Дата</th><th>Автор</th><th className="num">Цена без НДС, RUB</th><th className="num">Изменение</th><th className="num">Маржа</th><th>Описание изменений</th><th>Файлы</th></tr></thead>
        <tbody>{rows.map((r) => (
          <tr key={r.no} style={{ background: r.no === rows[rows.length - 1]?.no ? "#f4f4f4" : undefined }}>
            <td>{r.no === 0 ? "исходное" : `ред. ${r.no}`}{r.no === rows[rows.length - 1]?.no && <div className="badge accent">актуальное</div>}</td>
            <td>{fmtDate(r.at)}{r.sent_at && <div className="badge ok">отправлено {fmtDate(r.sent_at)}</div>}</td><td>{r.author}</td>
            <td className="num"><b>{fmtRub(r.price_rub)}</b></td>
            <td className="num">{r.price_before_rub != null && r.price_rub != null ? <span className={r.price_rub < r.price_before_rub ? "badge warn" : "badge ok"}>{r.price_rub >= r.price_before_rub ? "+" : "−"}{fmtRub(Math.abs(r.price_rub - r.price_before_rub))}</span> : "—"}</td>
            <td className="num"><span className={r.margin_pct != null && r.margin_pct < thr ? "badge bad" : ""}>{pct(r.margin_pct)}</span></td>
            <td style={{ fontSize: 12 }}>{r.description}</td>
            <td>{r.has_file && <>
              <a href={r.id ? `/api/deals/${dealId}/kp/revisions/${r.id}/file?format=docx` : `/api/deals/${dealId}/kp/file?format=docx&original=1`}>Word</a> · <a href={r.id ? `/api/deals/${dealId}/kp/revisions/${r.id}/file?format=pdf` : `/api/deals/${dealId}/kp/file?format=pdf&original=1`}>PDF</a></>}</td>
          </tr>))}</tbody>
      </table>

      {open && (
        <div style={{ marginTop: 12, borderTop: "1px solid #eee", paddingTop: 10 }}>
          {variants.map((v) => { const c = ch[v.id]; if (!c) return null; const t = totals?.[v.id];
            return (
              <div key={v.id} className="card" style={{ background: "#fafbfc" }}>
                <b>{variants.length > 1 ? v.name : "Предложение"}</b> <span className="muted">· цена по расчёту {fmtRub(v.price_rub)} RUB без НДС</span>
                <h3>Скидки и надбавки</h3>
                <table><tbody>{c.adjustments.map((a, i) => (
                  <tr key={i}>
                    <td><select value={a.kind} onChange={(e) => upd(v.id, { adjustments: c.adjustments.map((x, j) => (j === i ? { ...x, kind: e.target.value as Adj["kind"] } : x)) })}><option value="discount">скидка</option><option value="markup">надбавка</option></select></td>
                    <td><input inputMode="decimal" value={a.value} style={{ width: 110 }} onChange={(e) => upd(v.id, { adjustments: c.adjustments.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)) })} /></td>
                    <td><select value={a.mode} onChange={(e) => upd(v.id, { adjustments: c.adjustments.map((x, j) => (j === i ? { ...x, mode: e.target.value as Adj["mode"] } : x)) })}><option value="pct">%</option><option value="amount">RUB</option></select></td>
                    <td><input placeholder="подпись в КП (необязательно)" value={a.label} onChange={(e) => upd(v.id, { adjustments: c.adjustments.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)) })} /></td>
                    <td><button className="btn ghost" onClick={() => upd(v.id, { adjustments: c.adjustments.filter((_, j) => j !== i) })}>×</button></td></tr>))}</tbody></table>
                <button className="btn add" onClick={() => upd(v.id, { adjustments: [...c.adjustments, { kind: "discount", mode: "pct", value: "", label: "" }] })}>Скидка / надбавка</button>
                <h3>Дополнительные услуги и позиции</h3>
                <table><tbody>{c.extras.map((x, i) => (
                  <tr key={i}>
                    <td style={{ width: "50%" }}><input style={{ width: "100%" }} placeholder="напр. обучение операторов, шеф-монтаж, доставка ЗИП" value={x.name} onChange={(e) => upd(v.id, { extras: c.extras.map((y, j) => (j === i ? { ...y, name: e.target.value } : y)) })} /></td>
                    <td><input inputMode="decimal" placeholder="цена, RUB" value={x.price_rub} disabled={x.included} style={{ width: 130 }} onChange={(e) => upd(v.id, { extras: c.extras.map((y, j) => (j === i ? { ...y, price_rub: e.target.value } : y)) })} /></td>
                    <td><label className="row"><input type="checkbox" checked={x.included} onChange={(e) => upd(v.id, { extras: c.extras.map((y, j) => (j === i ? { ...y, included: e.target.checked } : y)) })} /> включено в цену</label></td>
                    <td><button className="btn ghost" onClick={() => upd(v.id, { extras: c.extras.filter((_, j) => j !== i) })}>×</button></td></tr>))}</tbody></table>
                <button className="btn add" onClick={() => upd(v.id, { extras: [...c.extras, { name: "", price_rub: "", included: false }] })}>Услуга / позиция</button>
                <h3>Комплектация</h3>
                <textarea rows={2} style={{ width: "100%" }} value={c.configuration} onChange={(e) => upd(v.id, { configuration: e.target.value })} />
                {v.service_flag && <label className="row"><input type="checkbox" checked={!!c.service_in_kp} onChange={(e) => upd(v.id, { service_in_kp: e.target.checked })} /> сервис до наработки отдельной строкой в КП</label>}
                {t && <div className="row" style={{ marginTop: 8, gap: 16 }}>
                  <span>Итог: <b>{fmtRub(t.total)}</b> RUB без НДС</span><span className="muted">было {fmtRub(t.base)}</span>
                  <span className={"badge " + (t.margin_pct != null && t.margin_pct < thr ? "bad" : "ok")}>маржа {pct(t.margin_pct)}{t.margin_pct != null && t.margin_pct < thr ? ` — ниже порога ${thr} %` : ""}</span></div>}
              </div>); })}
          <label className="f">Описание изменений (обязательно)<textarea rows={2} style={{ width: "100%" }} value={desc} placeholder="напр. скидка 5 % по итогам переговоров, добавлено обучение операторов" onChange={(e) => setDesc(e.target.value)} /></label>
          <div className="row" style={{ marginTop: 8 }}>
            <button className="btn ghost" onClick={preview}>Пересчитать итог и маржу</button>
            <button className="btn" disabled={busy || !desc.trim()} onClick={create}>Сформировать новую редакцию КП</button>
            <button className="btn ghost" onClick={() => setOpen(false)}>Отмена</button>
          </div>
        </div>)}
    </div>
  );
}
