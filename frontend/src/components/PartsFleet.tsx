import { useEffect, useState } from "react";
import { api, fmtDate } from "../api";
import type { Me } from "../types";

// Парк машин клиентов: машины из сделок ГШО и других производителей, наработка, ТО-комплекты, напоминания и заявка на комплект (ТЗ запчастей, раздел 9).

type Next = { to_hours: number; due_on: string | null; kits: { id: string; name: string; interval_hours: number }[] };
type Forecast = { per_day: number | null; hours_now: number | null; last_reading: { on: string; hours: number } | null; next: Next | null };
type Kit = { id: string; name: string; interval_hours: number; source: string; active: boolean; lines: { part_id: string | null; article: string; name: string; qty: number }[] };
type Machine = { id: string; deal_id: string | null; client_id: string; client_name: string; deal_number: string; equipment_model_id: string | null; other_model_id: string | null;
  manufacturer: string; model: string; serial: string; commissioned_on: string | null; site: string; hours_per_day: number | null; comment: string; active: boolean;
  forecast: Forecast; readings?: { id: string; on: string; hours: number; comment: string }[]; kits?: Kit[];
  requests?: { id: string; number: string; created_at: string }[] };
type Reminder = { id: string; machine_id: string; to_hours: number; due_on: string | null; manager_name: string; machine: Machine };
type Models = { gsho: { id: string; title: string }[]; other: { id: string; title: string; manufacturer: string; model: string }[] };
type Ref = { id: string; name: string };

const EMPTY = { client_id: "", equipment_model_id: "", other_manufacturer: "", other_model: "", serial: "", commissioned_on: "", site: "", hours_per_day: "", comment: "" };

export default function PartsFleet({ me }: { me: Me }) {
  const edit = me.roles.some((r) => ["manager", "service_head", "cd", "gd", "admin"].includes(r.code));
  const [list, setList] = useState<Machine[]>([]);
  const [rem, setRem] = useState<Reminder[]>([]);
  const [clients, setClients] = useState<Ref[]>([]);
  const [models, setModels] = useState<Models>({ gsho: [], other: [] });
  const [q, setQ] = useState("");
  const [clientId, setClientId] = useState("");
  const [soon, setSoon] = useState(false);
  const [cur, setCur] = useState<Machine | null>(null);
  const [form, setForm] = useState<typeof EMPTY | null>(null);
  const [msg, setMsg] = useState("");
  const load = () => {
    api.get<Machine[]>(`/api/parts/fleet/machines?client_id=${clientId}&q=${encodeURIComponent(q)}${soon ? "&due_days=30" : ""}`).then(setList).catch((e) => setMsg((e as Error).message));
    api.get<Reminder[]>("/api/parts/fleet/reminders").then(setRem).catch(() => setRem([]));
  };
  useEffect(() => { api.get<Ref[]>("/api/refs/clients").then(setClients).catch(() => setClients([])); api.get<Models>("/api/parts/fleet/models").then(setModels).catch(() => undefined); }, []);
  useEffect(() => { const t = setTimeout(load, 300); return () => clearTimeout(t); }, [q, clientId, soon]);
  const open = (id: string) => api.get<Machine>(`/api/parts/fleet/machines/${id}`).then((m) => { setCur(m); setForm(null); });
  async function act<T>(fn: () => Promise<T>, ok = "") {
    try { const r = await fn(); if (r && (r as unknown as Machine).forecast) setCur(r as unknown as Machine); if (ok) setMsg(ok); load(); return r; }
    catch (e) { setMsg((e as Error).message); return undefined; }
  }
  async function kitRequest(m: Machine, reminderId?: string) {
    const r = await act(() => api.post<{ number: string }>(`/api/parts/fleet/machines/${m.id}/kit-request`, { reminder_id: reminderId || null }));
    if (r) setMsg(`Создана заявка ${r.number} на ТО-комплект — откройте её во вкладке «Заявки», проверьте строки и сформируйте КП`);
  }
  function save() {
    if (!form) return;
    const body = { ...form, equipment_model_id: form.equipment_model_id || null, commissioned_on: form.commissioned_on || null,
      hours_per_day: form.hours_per_day ? Number(form.hours_per_day.replace(",", ".")) : null, active: true };
    act(() => (editingId ? api.put<Machine>(`/api/parts/fleet/machines/${editingId}`, body) : api.post<Machine>("/api/parts/fleet/machines", body)), "Сохранено")
      .then((m) => { if (m) setForm(null); });
  }
  const [editingId, setEditingId] = useState<string | null>(null);
  const startEdit = (m: Machine) => { const f = { ...EMPTY, client_id: m.client_id, equipment_model_id: m.equipment_model_id || "", other_manufacturer: m.other_model_id ? m.manufacturer : "",
    other_model: m.other_model_id ? m.model : "", serial: m.serial, commissioned_on: m.commissioned_on || "", site: m.site, hours_per_day: m.hours_per_day != null ? String(m.hours_per_day) : "", comment: m.comment };
    setEditingId(m.id); setForm(f); };

  return (
    <>
      {msg && <div className="alert info">{msg}</div>}
      {rem.length > 0 && (
        <div className="card">
          <h2 style={{ marginTop: 0 }}>Скоро ТО — предложите комплект</h2>
          <table>
            <thead><tr><th>Клиент</th><th>Машина</th><th>ТО, м·ч</th><th>Примерно</th><th>Менеджер</th><th></th></tr></thead>
            <tbody>{rem.map((r) => (
              <tr key={r.id}><td>{r.machine.client_name}</td><td><a href="#" onClick={(e) => { e.preventDefault(); open(r.machine_id); }}>{r.machine.manufacturer} {r.machine.model}</a>{r.machine.serial && <span className="muted"> · {r.machine.serial}</span>}</td>
                <td>{r.to_hours}</td><td>{fmtDate(r.due_on)}</td><td>{r.manager_name || "—"}</td>
                <td>{edit && <><button className="btn" onClick={() => kitRequest(r.machine, r.id)}>Заявка на комплект</button>
                  <button className="btn ghost" onClick={() => act(() => api.post(`/api/parts/fleet/reminders/${r.id}/dismiss`), "Напоминание снято")}>не нужно</button></>}</td></tr>))}</tbody>
          </table>
        </div>)}
      <div className="card">
        <div className="row" style={{ flexWrap: "wrap" }}>
          <input placeholder="клиент, модель, зав. №, место" value={q} onChange={(e) => setQ(e.target.value)} />
          <select value={clientId} onChange={(e) => setClientId(e.target.value)}><option value="">все клиенты</option>{clients.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>
          <label className="row"><input type="checkbox" checked={soon} onChange={(e) => setSoon(e.target.checked)} /> ТО в ближайшие 30 дней</label>
          {edit && <><button className="btn add" style={{ marginLeft: "auto" }} onClick={() => { setCur(null); setEditingId(null); setForm({ ...EMPTY, client_id: clientId }); }}>Машина</button>
            <button className="btn ghost" onClick={() => act(() => api.post<{ created: number }>("/api/parts/fleet/sync-deals"), "").then((r) => r && setMsg(`Из закрытых сделок ГШО добавлено машин: ${r.created}`))}>из сделок ГШО</button></>}
        </div>
        <table style={{ marginTop: 8 }}>
          <thead><tr><th>Клиент</th><th>Машина</th><th>Зав. №</th><th>Место</th><th className="num">Наработка, м·ч</th><th className="num">м·ч/день</th><th>Следующее ТО</th></tr></thead>
          <tbody>{list.map((m) => (
            <tr key={m.id} style={{ cursor: "pointer", background: cur?.id === m.id ? "var(--accent-soft)" : undefined }} onClick={() => open(m.id)}>
              <td>{m.client_name}</td><td>{m.manufacturer} {m.model}{m.deal_number && <div className="muted" style={{ fontSize: 11 }}>сделка {m.deal_number}</div>}</td>
              <td>{m.serial || <span className="muted">—</span>}</td><td>{m.site}</td><td className="num">{m.forecast.hours_now ?? "—"}</td><td className="num">{m.forecast.per_day ?? "—"}</td>
              <td>{m.forecast.next ? `${m.forecast.next.to_hours} м·ч${m.forecast.next.due_on ? ` ≈ ${fmtDate(m.forecast.next.due_on)}` : ""}` : <span className="muted">—</span>}</td></tr>))}
            {list.length === 0 && <tr><td colSpan={7} className="muted">Машин нет</td></tr>}</tbody>
        </table>
      </div>

      {form && (
        <div className="card">
          <h2 style={{ marginTop: 0 }}>{editingId ? "Машина клиента" : "Новая машина клиента"}</h2>
          <div className="grid cols-3">
            <label className="f">Клиент *<select value={form.client_id} onChange={(e) => setForm({ ...form, client_id: e.target.value })}><option value="">— выберите —</option>{clients.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
            <label className="f">Модель из каталога ГШО<select value={form.equipment_model_id} onChange={(e) => setForm({ ...form, equipment_model_id: e.target.value })}>
              <option value="">— другой производитель —</option>{models.gsho.map((m) => <option key={m.id} value={m.id}>{m.title}</option>)}</select></label>
            {!form.equipment_model_id && <>
              <label className="f">Производитель<input list="fleet-man" value={form.other_manufacturer} onChange={(e) => setForm({ ...form, other_manufacturer: e.target.value })} placeholder="Caterpillar, Sandvik…" />
                <datalist id="fleet-man">{[...new Set(models.other.map((o) => o.manufacturer))].map((x) => <option key={x} value={x} />)}</datalist></label>
              <label className="f">Модель<input list="fleet-model" value={form.other_model} onChange={(e) => setForm({ ...form, other_model: e.target.value })} />
                <datalist id="fleet-model">{models.other.filter((o) => !form.other_manufacturer || o.manufacturer === form.other_manufacturer).map((o) => <option key={o.id} value={o.model} />)}</datalist></label></>}
            <label className="f">Заводской №<input value={form.serial} onChange={(e) => setForm({ ...form, serial: e.target.value })} /></label>
            <label className="f">Дата ввода<input type="date" value={form.commissioned_on} onChange={(e) => setForm({ ...form, commissioned_on: e.target.value })} /></label>
            <label className="f">Место работы<input value={form.site} onChange={(e) => setForm({ ...form, site: e.target.value })} /></label>
            <label className="f">м·ч в день (если замеров мало)<input inputMode="decimal" value={form.hours_per_day} onChange={(e) => setForm({ ...form, hours_per_day: e.target.value })} /></label>
            <label className="f">Комментарий<input value={form.comment} onChange={(e) => setForm({ ...form, comment: e.target.value })} /></label>
          </div>
          <div className="row" style={{ marginTop: 8 }}><button className="btn" disabled={!form.client_id || (!form.equipment_model_id && !(form.other_manufacturer && form.other_model))} onClick={save}>Сохранить</button>
            <button className="btn ghost" onClick={() => setForm(null)}>отмена</button></div>
        </div>)}

      {cur && !form && <MachineCard m={cur} edit={edit} act={act} onEdit={() => startEdit(cur)} onKit={() => kitRequest(cur)} />}
    </>
  );
}

function MachineCard({ m, edit, act, onEdit, onKit }: { m: Machine; edit: boolean; act: <T>(fn: () => Promise<T>, ok?: string) => Promise<T | undefined>; onEdit: () => void; onKit: () => void }) {
  const [hrs, setHrs] = useState({ on: new Date().toISOString().slice(0, 10), hours: "", comment: "" });
  const [kit, setKit] = useState<{ id?: string; name: string; interval_hours: string; text: string } | null>(null);
  const [parts, setParts] = useState<{ id: string; article: string; name_ru: string; assembly: string; stock_total: number }[] | null>(null);
  const fc = m.forecast;
  const saveKit = () => {
    if (!kit) return;
    const lines = kit.text.split("\n").map((s) => s.split(/\t|;/)).filter((x) => x[0]?.trim()).map((x) => ({ article: x[0].trim(), qty: Number((x[1] || "1").replace(",", ".")) || 1 }));
    const body = { name: kit.name, interval_hours: Number(kit.interval_hours), lines, equipment_model_id: m.equipment_model_id, other_model_id: m.equipment_model_id ? null : m.other_model_id, active: true };
    act(() => (kit.id ? api.put(`/api/parts/fleet/kits/${kit.id}`, body) : api.post("/api/parts/fleet/kits", body)), "Комплект сохранён")
      .then(() => { setKit(null); act(() => api.get<Machine>(`/api/parts/fleet/machines/${m.id}`)); });
  };
  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 style={{ margin: 0 }}>{m.manufacturer} {m.model}{m.serial && ` · зав. № ${m.serial}`}</h2>{edit && <button className="btn ghost" onClick={onEdit}>изменить</button>}</div>
      <p className="muted" style={{ marginTop: 4 }}>{[m.client_name, m.site, m.deal_number && `сделка ${m.deal_number}`, m.commissioned_on && `ввод ${fmtDate(m.commissioned_on)}`].filter(Boolean).join(" · ")}{m.comment && ` · ${m.comment}`}</p>
      <div className="grid cols-3">
        <div>Наработка сейчас: <b>{fc.hours_now != null ? `≈ ${fc.hours_now} м·ч` : "нет замеров"}</b>{fc.last_reading && <div className="muted" style={{ fontSize: 12 }}>замер {fc.last_reading.hours} м·ч на {fmtDate(fc.last_reading.on)}</div>}</div>
        <div>В среднем: <b>{fc.per_day != null ? `${fc.per_day} м·ч/день` : "—"}</b></div>
        <div>Следующее ТО: <b>{fc.next ? `${fc.next.to_hours} м·ч` : "—"}</b>{fc.next && <div className="muted" style={{ fontSize: 12 }}>{fc.next.kits.map((k) => k.name).join(" + ")}{fc.next.due_on && ` · ≈ ${fmtDate(fc.next.due_on)}`}</div>}</div>
      </div>
      {edit && fc.next && <button className="btn" style={{ marginTop: 6 }} onClick={onKit}>Заявка на ТО-комплект</button>}

      <h3>Наработка</h3>
      {edit && <div className="row" style={{ flexWrap: "wrap" }}>
        <input type="date" value={hrs.on} onChange={(e) => setHrs({ ...hrs, on: e.target.value })} />
        <input inputMode="decimal" placeholder="моточасы" style={{ width: 100 }} value={hrs.hours} onChange={(e) => setHrs({ ...hrs, hours: e.target.value })} />
        <input placeholder="откуда (со слов клиента, сервис…)" value={hrs.comment} onChange={(e) => setHrs({ ...hrs, comment: e.target.value })} />
        <button className="btn add" disabled={!hrs.hours} onClick={() => act(() => api.post(`/api/parts/fleet/machines/${m.id}/hours`, { ...hrs, hours: Number(hrs.hours.replace(",", ".")) }), "Замер добавлен")
          .then(() => setHrs({ ...hrs, hours: "", comment: "" }))}>Замер</button></div>}
      {(m.readings || []).length > 0 && <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>{(m.readings || []).slice(0, 8).map((r) => `${fmtDate(r.on)} — ${r.hours} м·ч${r.comment ? ` (${r.comment})` : ""}`).join(" · ")}</div>}

      <h3>ТО-комплекты модели</h3>
      {(m.kits || []).length === 0 && <div className="muted">Комплектов нет{m.deal_number ? " — их можно взять из «ЗИП на ТО» расчёта сервиса сделки" : ""}.</div>}
      {(m.kits || []).map((k) => (
        <div key={k.id} style={{ marginBottom: 4 }}><b>{k.name}</b> · каждые {k.interval_hours} м·ч{k.source === "deal_zip" && <span className="muted"> · из ЗИП сделки</span>}
          <span className="muted" style={{ fontSize: 12 }}> — {k.lines.map((l) => `${l.article}${l.part_id ? "" : " (нет в каталоге)"} × ${l.qty}`).join(", ")}</span>
          {edit && <a href="#" style={{ marginLeft: 6, fontSize: 12 }} onClick={(e) => { e.preventDefault(); setKit({ id: k.id, name: k.name, interval_hours: String(k.interval_hours), text: k.lines.map((l) => `${l.article}\t${l.qty}`).join("\n") }); }}>изменить</a>}</div>))}
      {edit && !kit && <div className="row"><button className="btn ghost" onClick={() => setKit({ name: "", interval_hours: "", text: "" })}>+ комплект</button>
        {m.deal_id && <button className="btn ghost" onClick={() => act(() => api.post(`/api/parts/fleet/kits/from-deal/${m.deal_id}`), "Комплекты взяты из «ЗИП на ТО» сделки").then(() => act(() => api.get<Machine>(`/api/parts/fleet/machines/${m.id}`)))}>из ЗИП сделки</button>}</div>}
      {kit && (
        <div style={{ border: "1px solid var(--line)", padding: 8, borderRadius: 6 }}>
          <div className="row"><input placeholder="название (ТО-500)" value={kit.name} onChange={(e) => setKit({ ...kit, name: e.target.value })} />
            <input inputMode="decimal" placeholder="каждые, м·ч" style={{ width: 110 }} value={kit.interval_hours} onChange={(e) => setKit({ ...kit, interval_hours: e.target.value })} /></div>
          <textarea rows={5} style={{ width: "100%", marginTop: 4 }} placeholder={"артикул <Tab> количество — по строке на позицию"} value={kit.text} onChange={(e) => setKit({ ...kit, text: e.target.value })} />
          <div className="row"><button className="btn" disabled={!kit.name || !Number(kit.interval_hours)} onClick={saveKit}>Сохранить комплект</button><button className="btn ghost" onClick={() => setKit(null)}>отмена</button></div>
        </div>)}

      <h3>Запчасти для этой машины</h3>
      {parts === null ? <button className="btn ghost" onClick={() => api.get<typeof parts>(`/api/parts/fleet/machines/${m.id}/parts`).then(setParts)}>показать применимые</button>
        : parts!.length === 0 ? <div className="muted">В каталоге нет запчастей с применимостью к этой модели — её задают в карточке запчасти.</div>
        : <table><thead><tr><th>Узел</th><th>Артикул</th><th>Наименование</th><th className="num">Свободно</th></tr></thead>
          <tbody>{parts!.map((p) => <tr key={p.id}><td>{p.assembly}</td><td>{p.article}</td><td>{p.name_ru}</td><td className="num">{p.stock_total}</td></tr>)}</tbody></table>}
      {(m.requests || []).length > 0 && <p className="muted" style={{ marginTop: 8 }}>Заявки по машине: {(m.requests || []).map((r) => `${r.number} (${fmtDate(r.created_at)})`).join(", ")}</p>}
    </div>
  );
}
