import { useState } from "react";
import { api } from "../api";
import type { TechParamRow } from "../types";

// Правка карточки каталога КД и ЗГД — в том числе исполнения на подтверждении, до его утверждения:
// технические характеристики (все столбцы, добавление и удаление строк), опции РФ, фото и подписи,
// ТН ВЭД, данные для логистики, регламенты ТО, наработка.

type Row = Partial<TechParamRow> & { name_ru: string };
type Opt = { name?: string; cost?: number; currency?: string; install_days?: number };
type Img = { attachment_id?: string; caption?: string; kind?: string; file_name?: string; url?: string };
type Reg = { name: string; interval_hours: number; operations?: { name: string; hours: number }[] };

const cell = { padding: 2 } as const;
const inp = (w: number | string) => ({ width: w, minWidth: 0 });

export function TechEditor({ execId, name, brand, rows, onDone }: { execId: string; name: string; brand: string; rows: TechParamRow[]; onDone: (msg: string) => void }) {
  const [r, setR] = useState<Row[]>(rows.map((x) => ({ ...x })));
  const [n, setN] = useState(name);
  const [b, setB] = useState(brand);
  const [err, setErr] = useState("");
  const set = (i: number, p: Partial<Row>) => setR(r.map((x, j) => (j === i ? { ...x, ...p } : x)));
  const move = (i: number, d: number) => { const j = i + d; if (j < 0 || j >= r.length) return; const c = [...r]; [c[i], c[j]] = [c[j], c[i]]; setR(c); };
  async function save() {
    try { await api.patch(`/api/catalog/executions/${execId}`, { name: n, brand_name: b, tech_params: r }); onDone("Характеристики исполнения сохранены"); }
    catch (e) { setErr((e as Error).message); }
  }
  const t = (i: number, k: keyof Row, w: number | string, ph = "") => (
    <input style={inp(w)} placeholder={ph} value={String(r[i][k] ?? "")} onChange={(e) => set(i, { [k]: e.target.value } as Partial<Row>)} />);
  return (
    <div style={{ border: "1px solid var(--line)", borderRadius: 6, padding: 8, background: "#fafbfc" }}>
      <div className="row" style={{ flexWrap: "wrap" }}>
        <label className="f">Наименование исполнения<input value={n} onChange={(e) => setN(e.target.value)} /></label>
        <label className="f">Под брендом компании<input value={b} onChange={(e) => setB(e.target.value)} /></label>
      </div>
      <div style={{ overflowX: "auto", marginTop: 6 }}>
        <table style={{ fontSize: 12 }}>
          <thead><tr><th>№</th><th>Код</th><th>Раздел</th><th>Параметр (RU)</th><th>Параметр (中文)</th><th>Ед.</th><th>Значение (RU)</th><th>Значение (中文)</th><th>Комментарий</th><th title="обязательный">Обяз.</th><th title="реализуется в России">РФ</th><th /></tr></thead>
          <tbody>{r.map((x, i) => (
            <tr key={i}>
              <td style={cell}>{i + 1}</td><td style={cell}>{t(i, "code", 50)}</td><td style={cell}>{t(i, "section", 90)}</td><td style={cell}>{t(i, "name_ru", 200)}</td><td style={cell}>{t(i, "name_zh", 140)}</td>
              <td style={cell}>{t(i, "unit", 55)}</td><td style={cell}>{t(i, "value_ru", 130)}</td><td style={cell}>{t(i, "value_zh", 110)}</td><td style={cell}>{t(i, "comment_ru", 130)}</td>
              <td style={cell}><input type="checkbox" checked={!!x.required} onChange={(e) => set(i, { required: e.target.checked })} /></td>
              <td style={cell}><input type="checkbox" checked={!!x.local_ru} onChange={(e) => set(i, { local_ru: e.target.checked })} /></td>
              <td style={{ ...cell, whiteSpace: "nowrap" }}><button className="btn ghost" style={{ padding: "0 4px" }} onClick={() => move(i, -1)}>↑</button>
                <button className="btn ghost" style={{ padding: "0 4px" }} onClick={() => move(i, 1)}>↓</button>
                <button className="btn ghost" style={{ padding: "0 6px" }} onClick={() => setR(r.filter((_, j) => j !== i))}>×</button></td>
            </tr>))}</tbody>
        </table>
      </div>
      <div className="row" style={{ marginTop: 6 }}>
        <button className="btn ghost" onClick={() => setR([...r, { name_ru: "", unit: "", value_ru: "", required: false, local_ru: false }])}>+ строка</button>
        <button className="btn" disabled={!n.trim()} onClick={save}>Сохранить характеристики</button>
        <button className="btn ghost" onClick={() => onDone("")}>отмена</button>
        {err && <span className="muted">{err}</span>}
      </div>
    </div>
  );
}

export function MoreEditor({ modelId, execId, options, hs, logistics, regs, hours, checklist = [], onDone }: {
  modelId: string; execId?: string; options: Opt[]; hs: { code?: string; description?: string; duty_pct?: number | null; vat_pct?: number | null } | null;
  logistics: Record<string, { name?: string; value?: unknown; unit?: string }>; regs: Reg[]; hours?: number | null; checklist?: { group: string; item: string }[]; onDone: (msg: string) => void }) {
  const [chk, setChk] = useState(checklist.map((x) => `${x.group}: ${x.item}`).join("\n"));
  const [opts, setOpts] = useState<Opt[]>(options.map((o) => ({ ...o })));
  const [h, setH] = useState({ code: hs?.code || "", description: hs?.description || "", duty_pct: hs?.duty_pct ?? "", vat_pct: hs?.vat_pct ?? "" });
  const [lg, setLg] = useState(Object.entries(logistics).map(([code, f]) => ({ code, name: f.name || "", value: f.value == null ? "" : String(f.value), unit: f.unit || "" })));
  const [rg, setRg] = useState(regs.map((r) => ({ name: r.name, interval_hours: String(r.interval_hours), ops: (r.operations || []).map((o) => `${o.name}: ${o.hours}`).join("; ") })));
  const [hpm, setHpm] = useState(hours != null ? String(hours) : "");
  const [comment, setComment] = useState("");
  const [err, setErr] = useState("");
  const num = (v: string | number) => (v === "" || v == null ? null : Number(String(v).replace(",", ".")));
  async function save() {
    try {
      if (execId) await api.patch(`/api/catalog/executions/${execId}`, { local_options: opts.map((o) => ({ ...o, cost: o.cost == null ? null : Number(o.cost), install_days: o.install_days == null ? null : Number(o.install_days) })) });
      await api.patch(`/api/catalog/models/${modelId}`, {
        default_hs_code: h.code.trim() || null, hs_info: { description: h.description, duty_pct: num(h.duty_pct), vat_pct: num(h.vat_pct) },
        logistics_params: Object.fromEntries(lg.filter((x) => x.code.trim()).map((x) => [x.code.trim(), { name: x.name, unit: x.unit, value: num(x.value) ?? x.value }])),
        maintenance_regulations: rg.map((r) => ({ name: r.name, interval_hours: Number(r.interval_hours) || 0,
          operations: r.ops.split(";").map((s) => s.trim()).filter(Boolean).map((s) => { const [a, b] = s.split(":"); return { name: (a || "").trim(), hours: Number((b || "0").replace(",", ".")) || 0 }; }) })),
        default_hours_per_month: num(hpm), comment: comment || "правка опций, регламентов и логистики",
        acceptance_checklist: chk.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => { const i = l.indexOf(":"); return i > 0 ? { group: l.slice(0, i).trim(), item: l.slice(i + 1).trim() } : { group: "Проверки", item: l }; }) });
      onDone("Опции, регламенты, ТН ВЭД и данные для логистики сохранены");
    } catch (e) { setErr((e as Error).message); }
  }
  return (
    <div style={{ border: "1px solid var(--line)", borderRadius: 6, padding: 8, background: "#fafbfc" }}>
      <div className="grid cols-2">
        <div>
          {execId && <><h3 style={{ marginTop: 0 }}>Опции РФ (доукомплектование)</h3>
            {opts.map((o, i) => (
              <div key={i} className="row"><input placeholder="опция" value={o.name || ""} onChange={(e) => setOpts(opts.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
                <input inputMode="decimal" placeholder="стоимость" style={inp(90)} value={o.cost ?? ""} onChange={(e) => setOpts(opts.map((x, j) => (j === i ? { ...x, cost: e.target.value === "" ? undefined : Number(e.target.value) } : x)))} />
                <select value={o.currency || "RUB"} onChange={(e) => setOpts(opts.map((x, j) => (j === i ? { ...x, currency: e.target.value } : x)))}>{["RUB", "CNY", "USD", "EUR"].map((c) => <option key={c}>{c}</option>)}</select>
                <input inputMode="numeric" placeholder="монтаж, дн" style={inp(70)} value={o.install_days ?? ""} onChange={(e) => setOpts(opts.map((x, j) => (j === i ? { ...x, install_days: e.target.value === "" ? undefined : Number(e.target.value) } : x)))} />
                <button className="btn ghost" onClick={() => setOpts(opts.filter((_, j) => j !== i))}>×</button></div>))}
            <button className="btn ghost" onClick={() => setOpts([...opts, { name: "", currency: "RUB" }])}>+ опция</button></>}
          <h3>Регламенты ТО</h3>
          {rg.map((r, i) => (
            <div key={i} className="row" style={{ flexWrap: "wrap" }}><input placeholder="ТО-1" style={inp(80)} value={r.name} onChange={(e) => setRg(rg.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
              <input inputMode="decimal" placeholder="каждые, м·ч" style={inp(90)} value={r.interval_hours} onChange={(e) => setRg(rg.map((x, j) => (j === i ? { ...x, interval_hours: e.target.value } : x)))} />
              <input placeholder="операции: замена масла: 2; фильтры: 1" style={inp(260)} value={r.ops} onChange={(e) => setRg(rg.map((x, j) => (j === i ? { ...x, ops: e.target.value } : x)))} />
              <button className="btn ghost" onClick={() => setRg(rg.filter((_, j) => j !== i))}>×</button></div>))}
          <button className="btn ghost" onClick={() => setRg([...rg, { name: "", interval_hours: "", ops: "" }])}>+ регламент</button>
          <label className="f" style={{ marginTop: 6 }}>Наработка, м·ч в месяц<input inputMode="decimal" style={inp(100)} value={hpm} onChange={(e) => setHpm(e.target.value)} /></label>
        </div>
        <div>
          <h3 style={{ marginTop: 0 }}>Код ТН ВЭД</h3>
          <div className="row" style={{ flexWrap: "wrap" }}><input placeholder="код" style={inp(120)} value={h.code} onChange={(e) => setH({ ...h, code: e.target.value })} />
            <input placeholder="описание" style={inp(220)} value={h.description} onChange={(e) => setH({ ...h, description: e.target.value })} />
            <label className="row">пошлина, %<input inputMode="decimal" style={inp(60)} value={String(h.duty_pct)} onChange={(e) => setH({ ...h, duty_pct: e.target.value })} /></label>
            <label className="row">НДС, %<input inputMode="decimal" style={inp(60)} value={String(h.vat_pct)} onChange={(e) => setH({ ...h, vat_pct: e.target.value })} /></label></div>
          <h3>Данные для логистики</h3>
          {lg.map((x, i) => (
            <div key={i} className="row"><input placeholder="код" style={inp(55)} value={x.code} onChange={(e) => setLg(lg.map((y, j) => (j === i ? { ...y, code: e.target.value } : y)))} />
              <input placeholder="параметр" style={inp(160)} value={x.name} onChange={(e) => setLg(lg.map((y, j) => (j === i ? { ...y, name: e.target.value } : y)))} />
              <input placeholder="значение" style={inp(90)} value={x.value} onChange={(e) => setLg(lg.map((y, j) => (j === i ? { ...y, value: e.target.value } : y)))} />
              <input placeholder="ед." style={inp(60)} value={x.unit} onChange={(e) => setLg(lg.map((y, j) => (j === i ? { ...y, unit: e.target.value } : y)))} />
              <button className="btn ghost" onClick={() => setLg(lg.filter((_, j) => j !== i))}>×</button></div>))}
          <button className="btn ghost" onClick={() => setLg([...lg, { code: "", name: "", value: "", unit: "" }])}>+ параметр</button>
        </div>
      </div>
      <label className="f" style={{ marginTop: 6 }}>Чек-лист приёмки на заводе — по строке «раздел: пункт» (пусто — типовой; пункты по характеристикам, ЗИП и документам сделки добавятся сами)
        <textarea rows={5} value={chk} onChange={(e) => setChk(e.target.value)} placeholder={"Внешний осмотр: окраска без повреждений\nКузов: футеровка установлена"} /></label>
      <label className="f" style={{ marginTop: 6 }}>Что исправлено (в историю изменений)<input value={comment} onChange={(e) => setComment(e.target.value)} style={{ width: "100%" }} /></label>
      <div className="row" style={{ marginTop: 6 }}><button className="btn" onClick={save}>Сохранить</button><button className="btn ghost" onClick={() => onDone("")}>отмена</button>{err && <span className="muted">{err}</span>}</div>
    </div>
  );
}

export function ImagesEditor({ execId, images, kinds, onDone }: { execId: string; images: Img[]; kinds: Record<string, string>; onDone: (msg: string) => void }) {
  const [im, setIm] = useState(images.filter((x) => x.attachment_id).map((x) => ({ ...x })));
  const move = (i: number, d: number) => { const j = i + d; if (j < 0 || j >= im.length) return; const c = [...im]; [c[i], c[j]] = [c[j], c[i]]; setIm(c); };
  async function save() {
    try { await api.patch(`/api/catalog/executions/${execId}`, { images: im.map((x) => ({ attachment_id: x.attachment_id, caption: x.caption || "", kind: x.kind || "" })) }); onDone("Фото и чертежи исполнения сохранены"); }
    catch (e) { onDone((e as Error).message); }
  }
  return (
    <div style={{ border: "1px solid var(--line)", borderRadius: 6, padding: 8, background: "#fafbfc" }}>
      {im.map((x, i) => (
        <div key={x.attachment_id} className="row" style={{ marginBottom: 4 }}>
          {x.url && /\.(png|jpe?g|gif|webp|bmp)$/i.test(x.file_name || "") ? <img src={x.url} alt="" style={{ width: 60, height: 45, objectFit: "contain" }} /> : <span className="muted" style={{ width: 60 }}>{(x.file_name || "").split(".").pop()}</span>}
          <input style={inp(260)} placeholder="подпись" value={x.caption || ""} onChange={(e) => setIm(im.map((y, j) => (j === i ? { ...y, caption: e.target.value } : y)))} />
          <select value={x.kind || ""} onChange={(e) => setIm(im.map((y, j) => (j === i ? { ...y, kind: e.target.value } : y)))}><option value="">—</option>{Object.entries(kinds).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select>
          <button className="btn ghost" onClick={() => move(i, -1)}>↑</button><button className="btn ghost" onClick={() => move(i, 1)}>↓</button>
          <button className="btn ghost" title="убрать из исполнения (файл останется в сделке)" onClick={() => setIm(im.filter((_, j) => j !== i))}>×</button>
        </div>))}
      <div className="row"><button className="btn" onClick={save}>Сохранить</button><button className="btn ghost" onClick={() => onDone("")}>отмена</button></div>
    </div>
  );
}
