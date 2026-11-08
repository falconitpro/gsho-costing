import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { Me } from "../types";

// Каталог: комплекты ТО, техника других производителей, склады (перенесены из раздела запчастей / новые экраны).

const has = (me: Me, ...r: string[]) => me.roles.some((x) => r.includes(x.code));

type KitLine = { part_id: string | null; article: string; name: string; qty: number };
type Kit = { id: string; name: string; interval_hours: number; source: string; active: boolean; lines: KitLine[]; equipment_model_id: string | null; other_model_id: string | null; model_title: string };
type ModelRef = { id: string; title: string; kind: "equipment" | "other" };

/** Комплекты ТО: запчасти на одно ТО модели с периодичностью в моточасах. */
export function KitsBlock({ me }: { me: Me }) {
  const [rows, setRows] = useState<Kit[]>([]);
  const [models, setModels] = useState<ModelRef[]>([]);
  const [q, setQ] = useState("");
  const [form, setForm] = useState<{ id?: string; model: string; name: string; interval_hours: string; text: string; active: boolean } | null>(null);
  const [msg, setMsg] = useState("");
  const edit = has(me, "manager", "service_head", "cd", "gd", "admin");
  const load = () => api.get<Kit[]>("/api/parts/fleet/kits").then(setRows).catch((e) => setMsg((e as Error).message));
  useEffect(() => {
    load();
    Promise.all([
      api.get<{ factory: string; types: { models: { id: string; model: string; trademark?: string }[] }[] }[]>("/api/catalog/tree").catch(() => []),
      api.get<{ id: string; manufacturer: string; model: string }[]>("/api/parts/fleet/other-models").catch(() => []),
    ]).then(([tree, other]) => setModels([
      ...tree.flatMap((f) => f.types.flatMap((t) => t.models.map((m) => ({ id: m.id, title: `${m.trademark || f.factory} ${m.model}`, kind: "equipment" as const })))),
      ...other.map((o) => ({ id: o.id, title: `${o.manufacturer} ${o.model}`, kind: "other" as const })),
    ]));
  }, []);
  const shown = rows.filter((k) => !q || `${k.model_title} ${k.name} ${k.lines.map((l) => l.article).join(" ")}`.toLowerCase().includes(q.toLowerCase()));
  const save = () => {
    if (!form) return;
    const m = models.find((x) => x.id === form.model);
    const lines = form.text.split("\n").map((s) => s.split(/\t|;/)).filter((x) => x[0]?.trim()).map((x) => ({ article: x[0].trim(), qty: Number((x[1] || "1").replace(",", ".")) || 1 }));
    const body = { name: form.name, interval_hours: Number(form.interval_hours), lines, active: form.active,
      equipment_model_id: m?.kind === "equipment" ? m.id : null, other_model_id: m?.kind === "other" ? m.id : null };
    (form.id ? api.put(`/api/parts/fleet/kits/${form.id}`, body) : api.post("/api/parts/fleet/kits", body))
      .then(() => { setForm(null); setMsg("Комплект сохранён"); load(); }).catch((e) => setMsg((e as Error).message));
  };
  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <div><b>Комплекты ТО</b><div className="muted" style={{ fontSize: 12 }}>По ним считается следующее ТО машин клиентов и формируется заявка на комплект. Строки без карточки запчасти помечены — заведите её в «Запасных частях».</div></div>
        <div className="row" style={{ gap: 6 }}><input placeholder="модель, комплект, артикул…" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 220 }} />
          {edit && <button className="btn add" onClick={() => setForm({ model: "", name: "", interval_hours: "", text: "", active: true })}>Комплект</button>}</div>
      </div>
      {msg && <div className="alert info" style={{ marginTop: 8 }}>{msg}</div>}
      {form && (
        <div className="poa-section" style={{ marginTop: 10 }}>
          <div className="poa-form">
            <label className="f" style={{ gridColumn: "span 2" }}><span>Модель</span><select value={form.model} disabled={!!form.id} onChange={(e) => setForm({ ...form, model: e.target.value })}>
              <option value="">— выберите —</option><optgroup label="Каталог ГШО">{models.filter((m) => m.kind === "equipment").map((m) => <option key={m.id} value={m.id}>{m.title}</option>)}</optgroup>
              <optgroup label="Другие производители">{models.filter((m) => m.kind === "other").map((m) => <option key={m.id} value={m.id}>{m.title}</option>)}</optgroup></select></label>
            <label className="f"><span>Название</span><input value={form.name} placeholder="ТО-500" onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
            <label className="f"><span>Каждые, м·ч</span><input inputMode="decimal" value={form.interval_hours} onChange={(e) => setForm({ ...form, interval_hours: e.target.value })} /></label>
            <label className="f" style={{ gridColumn: "span 2" }}><span>Состав: артикул ⇥ количество — по строке</span><textarea rows={6} value={form.text} onChange={(e) => setForm({ ...form, text: e.target.value })} /></label>
            <label className="row" style={{ gap: 6, fontSize: 13 }}><input type="checkbox" style={{ width: "auto", minHeight: 0 }} checked={form.active} onChange={(e) => setForm({ ...form, active: e.target.checked })} />действует</label>
          </div>
          <div className="row" style={{ gap: 8, marginTop: 8 }}><button className="btn" disabled={!form.model || !form.name || !Number(form.interval_hours)} onClick={save}>Сохранить</button><button className="btn ghost" onClick={() => setForm(null)}>Отмена</button></div>
        </div>)}
      <table className="table" style={{ marginTop: 10, fontSize: 13 }}>
        <thead><tr><th>Модель</th><th>Комплект</th><th className="num">Каждые, м·ч</th><th>Состав</th><th /></tr></thead>
        <tbody>
          {shown.length === 0 && <tr><td colSpan={5} className="muted">Комплектов нет. Их можно завести здесь или взять из ЗИП на ТО сделки (Парк машин → карточка машины).</td></tr>}
          {shown.map((k) => (
            <tr key={k.id} style={{ opacity: k.active ? 1 : 0.5 }}>
              <td>{k.model_title || "—"}</td><td><b>{k.name}</b>{k.source === "deal_zip" && <div className="muted" style={{ fontSize: 11 }}>из ЗИП сделки</div>}</td><td className="num">{k.interval_hours}</td>
              <td style={{ fontSize: 12 }}>{k.lines.map((l, i) => <div key={i} style={{ color: l.part_id ? undefined : "var(--bad)" }}>{l.article} × {l.qty}{l.name ? ` — ${l.name}` : l.part_id ? "" : " (нет карточки)"}</div>)}</td>
              <td>{edit && <button className="btn ghost" style={{ padding: "0 8px" }} onClick={() => setForm({ id: k.id, model: k.equipment_model_id || k.other_model_id || "", name: k.name, interval_hours: String(k.interval_hours), active: k.active, text: k.lines.map((l) => `${l.article}\t${l.qty}`).join("\n") })}>изменить</button>}</td>
            </tr>))}
        </tbody>
      </table>
    </div>);
}

type Other = { id: string; manufacturer: string; model: string; machine_type: string; machines: number; kits: number };

/** Техника других производителей: модели машин клиентов, которые мы не поставляли. */
export function OtherModelsBlock({ me }: { me: Me }) {
  const [rows, setRows] = useState<Other[]>([]);
  const [q, setQ] = useState("");
  const [form, setForm] = useState<{ id?: string; manufacturer: string; model: string; machine_type: string } | null>(null);
  const [msg, setMsg] = useState("");
  const edit = has(me, "manager", "service_head", "cd", "gd", "admin");
  const load = () => api.get<Other[]>(`/api/parts/fleet/other-models?q=${encodeURIComponent(q)}`).then(setRows).catch((e) => setMsg((e as Error).message));
  useEffect(() => { const t = setTimeout(load, 250); return () => clearTimeout(t); }, [q]);
  const save = () => form && (form.id ? api.put(`/api/parts/fleet/other-models/${form.id}`, form) : api.post("/api/parts/fleet/other-models", form))
    .then(() => { setForm(null); setMsg("Сохранено"); load(); }).catch((e) => setMsg((e as Error).message));
  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <div><b>Техника других производителей</b><div className="muted" style={{ fontSize: 12 }}>Для парка машин клиентов, применимости запчастей и комплектов ТО. Без техпараметров и цен.</div></div>
        <div className="row" style={{ gap: 6 }}><input placeholder="производитель, модель…" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 200 }} />
          {edit && <button className="btn add" onClick={() => setForm({ manufacturer: "", model: "", machine_type: "" })}>Модель</button>}</div>
      </div>
      {msg && <div className="alert info" style={{ marginTop: 8 }}>{msg}</div>}
      {form && (
        <div className="row" style={{ gap: 8, marginTop: 10, flexWrap: "wrap" }}>
          <input placeholder="производитель" value={form.manufacturer} onChange={(e) => setForm({ ...form, manufacturer: e.target.value })} />
          <input placeholder="модель" value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} />
          <input placeholder="тип машины" value={form.machine_type} onChange={(e) => setForm({ ...form, machine_type: e.target.value })} />
          <button className="btn" disabled={!form.manufacturer.trim() || !form.model.trim()} onClick={save}>Сохранить</button><button className="btn ghost" onClick={() => setForm(null)}>Отмена</button>
        </div>)}
      <table className="table" style={{ marginTop: 10, fontSize: 13 }}>
        <thead><tr><th>Производитель</th><th>Модель</th><th>Тип</th><th className="num">Машин у клиентов</th><th className="num">Комплектов ТО</th><th /></tr></thead>
        <tbody>
          {rows.length === 0 && <tr><td colSpan={6} className="muted">Моделей нет — они появляются и при добавлении машины клиента в парк.</td></tr>}
          {rows.map((r) => (
            <tr key={r.id}><td>{r.manufacturer}</td><td><b>{r.model}</b></td><td>{r.machine_type || "—"}</td><td className="num">{r.machines}</td><td className="num">{r.kits}</td>
              <td>{edit && <button className="btn ghost" style={{ padding: "0 8px" }} onClick={() => setForm({ id: r.id, manufacturer: r.manufacturer, model: r.model, machine_type: r.machine_type })}>изменить</button>}</td></tr>))}
        </tbody>
      </table>
    </div>);
}

type Wh = { id: string; name: string; legal_entity_id: string | null; legal_entity: string; kind: string; is_active: boolean };

/** Склады компаний группы: физические и виртуальные (имена как в МойСклад). */
export function WarehousesBlock({ me }: { me: Me }) {
  const [rows, setRows] = useState<Wh[]>([]);
  const [ents, setEnts] = useState<{ id: string; name: string }[]>([]);
  const [form, setForm] = useState<{ id?: string; name: string; legal_entity_id: string; kind: string; is_active: boolean } | null>(null);
  const [msg, setMsg] = useState("");
  const edit = has(me, "cd", "gd", "admin");
  const load = () => api.get<Wh[]>("/api/parts/warehouses").then(setRows).catch((e) => setMsg((e as Error).message));
  useEffect(() => { load(); api.get<{ id: string; name: string }[]>("/api/parts/entities").then(setEnts).catch(() => setEnts([])); }, []);
  const save = () => form && (form.id ? api.put(`/api/parts/warehouses/${form.id}`, { ...form, legal_entity_id: form.legal_entity_id || null }) : api.post("/api/parts/warehouses", { ...form, legal_entity_id: form.legal_entity_id || null }))
    .then(() => { setForm(null); setMsg("Склад сохранён"); load(); }).catch((e) => setMsg((e as Error).message));
  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div><b>Склады</b><div className="muted" style={{ fontSize: 12 }}>Название — как в МойСклад: по нему сверяются и переносятся остатки. Остатки и движения — в «Снабжении и складе».</div></div>
        {edit && <button className="btn add" onClick={() => setForm({ name: "", legal_entity_id: "", kind: "physical", is_active: true })}>Склад</button>}
      </div>
      {msg && <div className="alert info" style={{ marginTop: 8 }}>{msg}</div>}
      {form && (
        <div className="row" style={{ gap: 8, marginTop: 10, flexWrap: "wrap" }}>
          <input placeholder="название" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <select value={form.legal_entity_id} onChange={(e) => setForm({ ...form, legal_entity_id: e.target.value })}><option value="">— компания группы —</option>{ents.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select>
          <select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}><option value="physical">физический</option><option value="virtual">виртуальный</option></select>
          <label className="row" style={{ gap: 4, fontSize: 13 }}><input type="checkbox" style={{ width: "auto", minHeight: 0 }} checked={form.is_active} onChange={(e) => setForm({ ...form, is_active: e.target.checked })} />действует</label>
          <button className="btn" disabled={!form.name.trim()} onClick={save}>Сохранить</button><button className="btn ghost" onClick={() => setForm(null)}>Отмена</button>
        </div>)}
      <table className="table" style={{ marginTop: 10, fontSize: 13 }}>
        <thead><tr><th>Склад</th><th>Компания группы</th><th>Вид</th><th /></tr></thead>
        <tbody>
          {rows.length === 0 && <tr><td colSpan={4} className="muted">Складов нет.</td></tr>}
          {rows.map((w) => (
            <tr key={w.id} style={{ opacity: w.is_active ? 1 : 0.5 }}><td><b>{w.name}</b></td><td>{w.legal_entity || "—"}</td><td>{w.kind === "virtual" ? "виртуальный" : "физический"}</td>
              <td>{edit && <button className="btn ghost" style={{ padding: "0 8px" }} onClick={() => setForm({ id: w.id, name: w.name, legal_entity_id: w.legal_entity_id || "", kind: w.kind, is_active: w.is_active })}>изменить</button>}</td></tr>))}
        </tbody>
      </table>
    </div>);
}

/** Карточка модели ГШО → вкладка «Запчасти и ТО»: применимые запчасти по узлам и комплекты ТО. */
export function ModelPartsBlock({ modelId }: { modelId: string }) {
  const [d, setD] = useState<{ parts: { id: string; article: string; name_ru: string; part_group: string; assembly: string; unit: string }[]; kits: { id: string; name: string; interval_hours: number; lines: KitLine[]; active: boolean }[] } | null>(null);
  useEffect(() => { setD(null); api.get<typeof d>(`/api/parts/catalog/by-model/${modelId}`).then(setD).catch(() => setD({ parts: [], kits: [] })); }, [modelId]);
  if (!d) return <div className="muted">Загрузка…</div>;
  return (
    <>
      <h3 style={{ margin: "4px 0" }}>Комплекты ТО ({d.kits.length})</h3>
      {d.kits.length === 0 ? <div className="muted" style={{ fontSize: 13 }}>Комплектов нет — Каталог → «Комплекты ТО».</div> : (
        <table className="table" style={{ fontSize: 13 }}><tbody>{d.kits.map((k) => <tr key={k.id} style={{ opacity: k.active ? 1 : 0.5 }}><td><b>{k.name}</b></td><td>каждые {k.interval_hours} м·ч</td><td style={{ fontSize: 12 }}>{k.lines.map((l) => `${l.article} × ${l.qty}`).join(", ")}</td></tr>)}</tbody></table>)}
      <h3 style={{ margin: "12px 0 4px" }}>Применимые запчасти ({d.parts.length})</h3>
      {d.parts.length === 0 ? <div className="muted" style={{ fontSize: 13 }}>Применимость не указана — её задают в карточке запчасти (раздел «Применимость»).</div> : (
        <table className="table" style={{ fontSize: 13 }}><thead><tr><th>Узел</th><th>Артикул</th><th>Наименование</th><th>Группа</th></tr></thead>
          <tbody>{d.parts.map((p) => <tr key={p.id + p.assembly}><td>{p.assembly || "—"}</td><td><Link to={`/catalog?s=parts&part=${p.id}`}><b>{p.article}</b></Link></td><td>{p.name_ru}</td><td className="muted">{p.part_group}</td></tr>)}</tbody></table>)}
    </>);
}
