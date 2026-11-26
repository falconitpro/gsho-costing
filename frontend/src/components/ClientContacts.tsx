import { useEffect, useState } from "react";
import { api, fmtDate } from "../api";

/** D-184 (K-1): контактные лица клиента. Персональные данные (152-ФЗ): рабочие телефон, почта, мессенджер; каждый просмотр
 *  записывается в журнал; удаление по запросу — полностью, с основанием. Права — группа «Клиенты: контактные лица». Только новый вид. */

type Contact = { id: string; full_name: string; position: string; department: string; roles: string[]; roles_titles: string[]; work_phone: string;
  work_email: string; messenger: string; site_id: string | null; site_name: string; is_primary: boolean; is_active: boolean;
  consent_on: string | null; consent_source: string; comment: string };
type Meta = { roles: Record<string, string>; consent_required: boolean; can_edit: boolean; can_see_views: boolean };
type View = { at: string; user: string; kind: string };

const EMPTY = { full_name: "", position: "", department: "", roles: [] as string[], work_phone: "", work_email: "", messenger: "", site_id: "",
  is_primary: false, is_active: true, consent_on: "", consent_source: "", comment: "" };
type Form = typeof EMPTY;
const KIND: Record<string, string> = { list: "список", card: "карточка", export: "выгрузка" };

export default function ClientContacts({ clientId }: { clientId: string }) {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [denied, setDenied] = useState(false);
  const [items, setItems] = useState<Contact[]>([]);
  const [sites, setSites] = useState<{ id: string; name: string }[]>([]);
  const [archived, setArchived] = useState(false);
  const [form, setForm] = useState<Form | null>(null);
  const [editId, setEditId] = useState<string | null>(null);
  const [del, setDel] = useState<{ id: string; reason: string } | null>(null);
  const [views, setViews] = useState<View[] | null>(null);
  const [msg, setMsg] = useState("");
  const load = () => api.get<{ items: Contact[] }>(`/api/clients/contacts?client_id=${clientId}${archived ? "&include_archived=true" : ""}`)
    .then((r) => setItems(r.items)).catch((e) => setMsg((e as Error).message));
  useEffect(() => { api.get<Meta>("/api/clients/contacts/meta").then(setMeta).catch(() => setDenied(true)); }, []);
  useEffect(() => {
    if (denied) return;
    load(); setForm(null); setViews(null);
    api.get<{ items: { id: string; name: string }[] }>(`/api/clients/sites?client_id=${clientId}`).then((r) => setSites(r.items)).catch(() => setSites([]));
  }, [clientId, archived, denied]);   // eslint-disable-line react-hooks/exhaustive-deps
  if (denied) return null;

  const edit = (c: Contact) => {
    setEditId(c.id);
    setForm({ full_name: c.full_name, position: c.position, department: c.department, roles: c.roles, work_phone: c.work_phone, work_email: c.work_email,
      messenger: c.messenger, site_id: c.site_id || "", is_primary: c.is_primary, is_active: c.is_active, consent_on: c.consent_on || "", consent_source: c.consent_source, comment: c.comment });
  };
  const save = async (f: Form, id: string | null) => {
    const body = { ...f, client_id: clientId, site_id: f.site_id || null, consent_on: f.consent_on || null };
    try {
      if (id) await api.put(`/api/clients/contacts/${id}`, body); else await api.post("/api/clients/contacts", body);
      setForm(null); setEditId(null); setMsg(id ? "Контакт сохранён" : "Контакт добавлен"); load();
    } catch (e) { setMsg((e as Error).message); }
  };
  const remove = async () => {
    if (!del) return;
    try { await api.post(`/api/clients/contacts/${del.id}/delete`, { reason: del.reason }); setDel(null); setMsg("Контакт удалён полностью"); load(); }
    catch (e) { setMsg((e as Error).message); }
  };
  const toggleRole = (r: string) => form && setForm({ ...form, roles: form.roles.includes(r) ? form.roles.filter((x) => x !== r) : [...form.roles, r] });
  const consentMissing = !!meta?.consent_required && !!form && !editId && !(form.consent_on && form.consent_source.trim());

  return (
    <section className="cs-block" aria-label="Контактные лица клиента">
      <div className="cs-head">
        <h3>Контактные лица <span className="muted">· {items.length}</span></h3>
        <label className="row muted"><input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} /> показать архив</label>
        {meta?.can_see_views && <button className="btn ghost" onClick={() => api.get<View[]>(`/api/clients/contacts/views?client_id=${clientId}&limit=50`).then(setViews).catch((e) => setMsg((e as Error).message))}>журнал просмотров</button>}
        {meta?.can_edit && !form && <button className="btn add" onClick={() => { setEditId(null); setForm(EMPTY); }}>Контакт</button>}
      </div>
      <p className="muted cs-note">Персональные данные: только рабочие телефон и почта. Каждый просмотр записывается в журнал.</p>
      {msg && <div className="alert info">{msg}</div>}
      {views && (
        <div className="cs-form">
          <h4>Журнал просмотров <button className="btn ghost" onClick={() => setViews(null)}>скрыть</button></h4>
          {views.length === 0 ? <p className="muted">Записей нет</p> : <ul className="cs-views">{views.map((v, i) => <li key={i}>{fmtDate(v.at)} {new Date(v.at).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })} · {v.user || "—"} · {KIND[v.kind] || v.kind}</li>)}</ul>}
        </div>)}
      {items.length === 0 && !form && <p className="muted">Контактов нет.</p>}
      {items.length > 0 && (
        <ul className="cs-list">
          {items.map((c) => (
            <li key={c.id} className={"cs-item" + (c.is_active ? "" : " cs-archived")}>
              <div className="cs-main">
                <b>{c.full_name}</b>{c.is_primary && <span className="badge ok">основной</span>}{!c.is_active && <span className="badge">в архиве</span>}
                <div className="muted">{[c.position, c.department, c.site_name && `площадка: ${c.site_name}`].filter(Boolean).join(" · ")}</div>
                <div className="cs-links">
                  {c.work_phone && <a href={`tel:${c.work_phone.replace(/[^\d+]/g, "")}`}>{c.work_phone}</a>}
                  {c.work_email && <a href={`mailto:${c.work_email}`}>{c.work_email}</a>}
                  {c.messenger && <span>{c.messenger}</span>}
                </div>
                {c.roles_titles.length > 0 && <div className="cs-tags">{c.roles_titles.map((t) => <span key={t} className="badge">{t}</span>)}</div>}
                {meta?.consent_required && <div className="muted cs-note">{c.consent_on ? `согласие ${fmtDate(c.consent_on)} · ${c.consent_source}` : "согласие не отмечено"}</div>}
              </div>
              {meta?.can_edit && (
                <div className="cs-act">
                  <button className="btn ghost" onClick={() => edit(c)}>изменить</button>
                  <button className="btn ghost" onClick={() => save({ ...EMPTY, full_name: c.full_name, position: c.position, department: c.department, roles: c.roles, work_phone: c.work_phone,
                    work_email: c.work_email, messenger: c.messenger, site_id: c.site_id || "", is_primary: c.is_primary, consent_on: c.consent_on || "", consent_source: c.consent_source,
                    comment: c.comment, is_active: !c.is_active }, c.id)}>{c.is_active ? "в архив" : "вернуть"}</button>
                  <button className="btn ghost cs-danger" onClick={() => setDel({ id: c.id, reason: "" })}>удалить по запросу</button>
                </div>)}
              {del?.id === c.id && (
                <div className="cs-del">
                  <label className="f">Основание удаления (запрос субъекта, дата) *<input value={del.reason} onChange={(e) => setDel({ ...del, reason: e.target.value })} /></label>
                  <div className="row"><button className="btn cs-danger-btn" disabled={del.reason.trim().length < 3} onClick={remove}>Удалить без восстановления</button>
                    <button className="btn ghost" onClick={() => setDel(null)}>отмена</button></div>
                </div>)}
            </li>))}
        </ul>)}
      {form && meta && (
        <div className="cs-form">
          <h4>{editId ? "Контакт" : "Новый контакт"}</h4>
          <div className="grid cols-3">
            <label className="f">ФИО *<input value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} /></label>
            <label className="f">Должность<input value={form.position} onChange={(e) => setForm({ ...form, position: e.target.value })} /></label>
            <label className="f">Подразделение<input value={form.department} onChange={(e) => setForm({ ...form, department: e.target.value })} /></label>
            <label className="f">Рабочий телефон<input inputMode="tel" value={form.work_phone} onChange={(e) => setForm({ ...form, work_phone: e.target.value })} placeholder="+7 (3842) 00-00-00 доб. 123" /></label>
            <label className="f">Рабочая почта<input inputMode="email" value={form.work_email} onChange={(e) => setForm({ ...form, work_email: e.target.value })} /></label>
            <label className="f">Мессенджер (рабочий)<input value={form.messenger} onChange={(e) => setForm({ ...form, messenger: e.target.value })} placeholder="Telegram @…" /></label>
            <label className="f">Площадка<select value={form.site_id} onChange={(e) => setForm({ ...form, site_id: e.target.value })}>
              <option value="">— не указана —</option>{sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
            <label className="f cs-check"><span><input type="checkbox" checked={form.is_primary} onChange={(e) => setForm({ ...form, is_primary: e.target.checked })} /> основной контакт клиента</span></label>
            {meta.consent_required && <>
              <label className="f">Согласие на обработку ПДн — дата{!editId && " *"}<input type="date" value={form.consent_on} onChange={(e) => setForm({ ...form, consent_on: e.target.value })} /></label>
              <label className="f">Источник согласия{!editId && " *"}<input value={form.consent_source} onChange={(e) => setForm({ ...form, consent_source: e.target.value })} placeholder="письмо, договор, форма" /></label></>}
          </div>
          <fieldset className="cs-roles"><legend>Роль у клиента</legend>
            {Object.entries(meta.roles).map(([k, t]) => <label key={k} className="row"><input type="checkbox" checked={form.roles.includes(k)} onChange={() => toggleRole(k)} /> {t}</label>)}
          </fieldset>
          <label className="f">Комментарий<input value={form.comment} onChange={(e) => setForm({ ...form, comment: e.target.value })} /></label>
          <div className="row">
            <button className="btn" disabled={!form.full_name.trim() || consentMissing} onClick={() => save(form, editId)}>Сохранить</button>
            <button className="btn ghost" onClick={() => { setForm(null); setEditId(null); }}>отмена</button>
          </div>
        </div>)}
    </section>
  );
}
