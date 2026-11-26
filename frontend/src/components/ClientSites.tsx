import { useEffect, useState } from "react";
import { api } from "../api";

/** D-182 (K-2): площадки клиента — карьеры, рудники, базы. Адрес и координаты, ссылка «Открыть в Яндекс Картах»,
 *  режим допуска и требования охраны труда. Права — группа «Клиенты: площадки» в «Уровнях доступа». Только новый вид. */

type Site = { id: string; client_id: string; name: string; site_type: string; site_type_title: string; code: string; address: string; country: string;
  latitude: number | null; longitude: number | null; timezone: string; access_mode: string; access_mode_title: string; hse_notes: string;
  is_active: boolean; comment: string; machines_count: number; map_url: string };
type Meta = { site_types: Record<string, string>; access_modes: Record<string, string>; can_edit: boolean };

const EMPTY = { name: "", site_type: "quarry", code: "", address: "", latitude: "", longitude: "", access_mode: "", hse_notes: "", comment: "", is_active: true };
type Form = typeof EMPTY;

const num = (s: string) => (s.trim() === "" ? null : Number(s.replace(",", ".")));

export default function ClientSites({ clientId }: { clientId: string }) {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [items, setItems] = useState<Site[]>([]);
  const [archived, setArchived] = useState(false);
  const [form, setForm] = useState<Form | null>(null);
  const [editId, setEditId] = useState<string | null>(null);
  const [msg, setMsg] = useState("");
  const [denied, setDenied] = useState(false);
  const load = () => api.get<{ items: Site[] }>(`/api/clients/sites?client_id=${clientId}${archived ? "&include_archived=true" : ""}`)
    .then((r) => setItems(r.items)).catch((e) => setMsg((e as Error).message));
  useEffect(() => { api.get<Meta>("/api/clients/sites/meta").then(setMeta).catch(() => setDenied(true)); }, []);
  useEffect(() => { if (!denied) load(); setForm(null); }, [clientId, archived, denied]);   // eslint-disable-line react-hooks/exhaustive-deps
  if (denied) return null;   // нет доступа к группе «Клиенты: площадки»

  const edit = (s: Site) => {
    setEditId(s.id);
    setForm({ name: s.name, site_type: s.site_type, code: s.code, address: s.address, latitude: s.latitude?.toString() ?? "", longitude: s.longitude?.toString() ?? "",
      access_mode: s.access_mode, hse_notes: s.hse_notes, comment: s.comment, is_active: s.is_active });
  };
  const save = async (f: Form, id: string | null) => {
    const body = { ...f, client_id: clientId, latitude: num(f.latitude), longitude: num(f.longitude) };
    try {
      if (id) await api.put(`/api/clients/sites/${id}`, body); else await api.post("/api/clients/sites", body);
      setForm(null); setEditId(null); setMsg(id ? "Площадка сохранена" : "Площадка добавлена"); load();
    } catch (e) { setMsg((e as Error).message); }
  };
  const archive = (s: Site, on: boolean) => save({ ...EMPTY, ...{ name: s.name, site_type: s.site_type, code: s.code, address: s.address,
    latitude: s.latitude?.toString() ?? "", longitude: s.longitude?.toString() ?? "", access_mode: s.access_mode, hse_notes: s.hse_notes, comment: s.comment }, is_active: on }, s.id);
  const coordsBad = !!form && ((form.latitude.trim() === "") !== (form.longitude.trim() === ""));

  return (
    <section className="cs-block" aria-label="Площадки клиента">
      <div className="cs-head">
        <h3>Площадки <span className="muted">· {items.length}</span></h3>
        <label className="row muted"><input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} /> показать архив</label>
        {meta?.can_edit && !form && <button className="btn add" onClick={() => { setEditId(null); setForm(EMPTY); }}>Площадка</button>}
      </div>
      {msg && <div className="alert info">{msg}</div>}
      {items.length === 0 && !form && <p className="muted">Площадок нет. Карьеры, рудники и базы клиента — сюда: адрес, координаты, режим допуска; к площадке привязываются машины парка.</p>}
      {items.length > 0 && (
        <ul className="cs-list">
          {items.map((s) => (
            <li key={s.id} className={"cs-item" + (s.is_active ? "" : " cs-archived")}>
              <div className="cs-main">
                <b>{s.name}</b> <span className="badge">{s.site_type_title}</span>{!s.is_active && <span className="badge">в архиве</span>}
                <div className="muted">{[s.address, s.access_mode && `допуск: ${s.access_mode_title.toLowerCase()}`, `машин: ${s.machines_count}`].filter(Boolean).join(" · ")}</div>
                {s.hse_notes && <div className="cs-hse">Охрана труда: {s.hse_notes}</div>}
              </div>
              <div className="cs-act">
                {s.map_url && <a className="btn ghost" href={s.map_url} target="_blank" rel="noreferrer">Открыть в Яндекс Картах</a>}
                {meta?.can_edit && <>
                  <button className="btn ghost" onClick={() => edit(s)}>изменить</button>
                  <button className="btn ghost" onClick={() => archive(s, !s.is_active)}>{s.is_active ? "в архив" : "вернуть"}</button></>}
              </div>
            </li>))}
        </ul>)}
      {form && meta && (
        <div className="cs-form">
          <h4>{editId ? "Площадка" : "Новая площадка"}</h4>
          <div className="grid cols-3">
            <label className="f">Название *<input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Разрез «Восточный»" /></label>
            <label className="f">Тип<select value={form.site_type} onChange={(e) => setForm({ ...form, site_type: e.target.value })}>
              {Object.entries(meta.site_types).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select></label>
            <label className="f">Код у клиента<input value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} /></label>
            <label className="f cs-wide">Адрес<input value={form.address} onChange={(e) => setForm({ ...form, address: e.target.value })} /></label>
            <label className="f">Широта<input inputMode="decimal" value={form.latitude} onChange={(e) => setForm({ ...form, latitude: e.target.value })} placeholder="55.3512" /></label>
            <label className="f">Долгота<input inputMode="decimal" value={form.longitude} onChange={(e) => setForm({ ...form, longitude: e.target.value })} placeholder="86.0868" /></label>
            <label className="f">Режим допуска<select value={form.access_mode} onChange={(e) => setForm({ ...form, access_mode: e.target.value })}>
              {Object.entries(meta.access_modes).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select></label>
            <label className="f cs-wide">Охрана труда и допуск<textarea rows={2} value={form.hse_notes} onChange={(e) => setForm({ ...form, hse_notes: e.target.value })} placeholder="СИЗ, инструктаж, сопровождение…" /></label>
            <label className="f cs-wide">Комментарий<input value={form.comment} onChange={(e) => setForm({ ...form, comment: e.target.value })} /></label>
          </div>
          {coordsBad && <div className="muted">Заполните широту и долготу вместе — тогда появится ссылка на карту.</div>}
          <div className="row">
            <button className="btn" disabled={!form.name.trim() || coordsBad} onClick={() => save(form, editId)}>Сохранить</button>
            <button className="btn ghost" onClick={() => { setForm(null); setEditId(null); }}>отмена</button>
          </div>
        </div>)}
    </section>
  );
}
