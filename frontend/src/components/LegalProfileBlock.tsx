import { useEffect, useState } from "react";
import { api, fmtDate } from "../api";

// Юридический блок, этап 1: юридические профили контрагента (клиент / поставщик / компания группы) и банковские профили.
// Один владелец — несколько профилей (головная организация, филиал); один по умолчанию. Правки — с историей; банк — статус и подтверждение.

export type Names = { ru: { full: string; short: string }; en: { full: string; short: string }; zh: { full: string; short: string } };
export type Bank = { id: string; title: string; currency: string; country: string; beneficiary: string; bank_name: string; bank_address: string; account: string; bik: string; swift: string;
  corr_account: string; corr_bank: string; purpose: string; status: string; status_title: string; valid_from: string | null; valid_to: string | null;
  verified: boolean; verified_at: string | null; verified_by_name: string; history: Hist[] };
export type Hist = { at: string; by_name: string; action: string; fields: Record<string, [unknown, unknown]> };
export type Profile = { id: string; owner_kind: string; owner_id: string; kind: string; kind_title: string; title: string; is_default: boolean; country: string; names: Names;
  opf: string; reg_number: string; tax_number: string; kpp: string; legal_address: string; actual_address: string; postal_address: string; director: string;
  signatory_name: string; signatory_position: string; signatory_basis: string; contacts: { name: string; position: string; phone: string; email: string }[];
  documents: { title: string; number: string; issued_on: string; valid_until: string }[]; powers_of_attorney: { person: string; number: string; issued_on: string; valid_until: string; scope: string }[];
  extra: Record<string, string>; check_status: string; check_status_title: string; checked_at: string | null; checked_by_name: string; lawyer_comment: string; is_active: boolean;
  country_fields: { key: string; title: string }[]; banks: Bank[]; summary: string; history: Hist[] };
type Resp = { owner_name: string; items: Profile[]; can_edit: boolean; can_check: boolean; can_verify_bank: boolean };
type Dicts = { profile_kinds: Record<string, string>; check_status: Record<string, string>; country_fields: Record<string, { key: string; title: string }[]> };

const FIELD_T: Record<string, string> = { kind: "тип", title: "название", is_default: "по умолчанию", country: "страна", names: "наименования", opf: "ОПФ", reg_number: "рег. номер", tax_number: "налоговый номер",
  kpp: "КПП", legal_address: "юр. адрес", actual_address: "факт. адрес", postal_address: "почтовый адрес", director: "руководитель", signatory_name: "подписант", signatory_position: "должность",
  signatory_basis: "основание", contacts: "контакты", documents: "документы", powers_of_attorney: "доверенности", extra: "страновые поля", lawyer_comment: "комментарий юриста", is_active: "активен",
  check_status: "проверка", currency: "валюта", beneficiary: "получатель", bank_name: "банк", bank_address: "адрес банка", account: "счёт", bik: "БИК", swift: "SWIFT", corr_account: "к/с",
  corr_bank: "банк-корреспондент", purpose: "назначение", status: "статус", valid_from: "с", valid_to: "по" };
const ACTION_T: Record<string, string> = { auto_created: "создан из карточки", created: "создан", update: "изменение", check: "проверка", archived: "в архив", verified: "реквизиты подтверждены" };
const str = (v: unknown) => (v == null || v === "" ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v));

const EMPTY_BANK: Partial<Bank> = { title: "", currency: "RUB", country: "", beneficiary: "", bank_name: "", bank_address: "", account: "", bik: "", swift: "", corr_account: "", corr_bank: "", purpose: "", status: "active" };

function History({ items }: { items: Hist[] }) {
  if (!items.length) return null;
  return (
    <details style={{ fontSize: 12, marginTop: 6 }}><summary className="muted" style={{ cursor: "pointer" }}>История изменений · {items.length}</summary>
      <table style={{ fontSize: 12, marginTop: 4 }}><tbody>{items.map((h, i) => (
        <tr key={i}><td style={{ whiteSpace: "nowrap" }}>{fmtDate(h.at)}</td><td>{h.by_name || "система"}</td><td>{ACTION_T[h.action] || h.action}</td>
          <td>{Object.entries(h.fields || {}).map(([k, [a, b]]) => <div key={k}><span className="muted">{FIELD_T[k] || k}:</span> {str(a)} → <b>{str(b)}</b></div>)}</td></tr>))}</tbody></table>
    </details>);
}

/** Карточка одного банковского профиля: просмотр, правка, архив, подтверждение. */
function BankCard({ b, canEdit, canVerify, onSaved, onMsg }: { b: Bank; canEdit: boolean; canVerify: boolean; onSaved: () => void; onMsg: (m: string) => void }) {
  const [edit, setEdit] = useState(false);
  const [f, setF] = useState<Partial<Bank>>(b);
  useEffect(() => setF(b), [b]);
  const save = () => api.put(`/api/legal/banks/${b.id}`, f).then(() => { setEdit(false); onSaved(); }).catch((e) => onMsg((e as Error).message));
  const inp = (k: keyof Bank, t: string, w = 160) => <label className="f">{t}<input style={{ width: w }} value={String(f[k] ?? "")} onChange={(e) => setF({ ...f, [k]: e.target.value })} /></label>;
  const archived = b.status === "archived";
  return (
    <div style={{ border: "1px solid var(--line)", borderLeft: `4px solid ${archived ? "var(--line)" : b.verified ? "var(--ok)" : "var(--warn)"}`, borderRadius: 8, padding: "8px 12px", marginBottom: 6, opacity: archived ? .7 : 1, background: "#fff" }}>
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <div><b>{b.title || b.bank_name}</b> <span className="badge" style={{ marginLeft: 4 }}>{b.currency}</span>
          {archived ? <span className="badge" style={{ marginLeft: 4 }}>архив</span> : b.verified ? <span className="badge ok" style={{ marginLeft: 4 }} title={`${b.verified_by_name} · ${fmtDate(b.verified_at)}`}>реквизиты подтверждены</span> : <span className="badge warn" style={{ marginLeft: 4 }}>не подтверждены</span>}
          <div className="muted" style={{ fontSize: 12 }}>{[b.bank_name, b.account && `счёт ${b.account}`, b.bik && `БИК ${b.bik}`, b.swift && `SWIFT ${b.swift}`, b.corr_account && `к/с ${b.corr_account}`, b.purpose].filter(Boolean).join(" · ")}</div></div>
        <div className="row" style={{ gap: 6 }}>
          {!archived && canVerify && !b.verified && <button className="btn ghost" style={{ padding: "3px 10px", fontSize: 12 }} onClick={() => api.post(`/api/legal/banks/${b.id}/verify`).then(onSaved).catch((e) => onMsg((e as Error).message))}>Подтвердить</button>}
          {canEdit && !archived && <button className="btn ghost" style={{ padding: "3px 10px", fontSize: 12 }} onClick={() => setEdit(!edit)}>{edit ? "свернуть" : "изменить"}</button>}
          {canEdit && !archived && <button className="btn ghost" style={{ padding: "3px 10px", fontSize: 12, color: "var(--muted)", borderColor: "var(--line)" }} onClick={() => { if (confirm("Перевести реквизиты в архив? В новые документы они подставляться не будут.")) api.post(`/api/legal/banks/${b.id}/archive`).then(onSaved).catch((e) => onMsg((e as Error).message)); }}>в архив</button>}
        </div>
      </div>
      {edit && (
        <div style={{ marginTop: 8 }}>
          <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>{inp("title", "Название", 150)}{inp("currency", "Валюта", 60)}{inp("country", "Страна", 70)}{inp("beneficiary", "Получатель платежа", 220)}{inp("bank_name", "Банк", 220)}{inp("bank_address", "Адрес банка", 260)}
            {inp("account", "Счёт", 200)}{inp("bik", "БИК", 100)}{inp("swift", "SWIFT", 110)}{inp("corr_account", "Корр. счёт", 200)}{inp("corr_bank", "Банк-корреспондент", 200)}{inp("purpose", "Назначение", 220)}
            <label className="f">Действует с<input type="date" value={String(f.valid_from || "").slice(0, 10)} onChange={(e) => setF({ ...f, valid_from: e.target.value || null })} /></label>
            <label className="f">по<input type="date" value={String(f.valid_to || "").slice(0, 10)} onChange={(e) => setF({ ...f, valid_to: e.target.value || null })} /></label></div>
          <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>Изменение счёта, банка, БИК/SWIFT снимает отметку «подтверждены» — ФД или юрист подтвердит заново.</div>
          <div className="row" style={{ marginTop: 6, gap: 6 }}><button className="btn" onClick={save}>Сохранить</button><button className="btn ghost" onClick={() => { setEdit(false); setF(b); }}>Отмена</button></div>
        </div>)}
      <History items={b.history} />
    </div>);
}

/** Один юридический профиль: форма по группам полей, банковские профили, проверка юристом, история. */
function ProfileCard({ p, dicts, canEdit, canCheck, canVerify, onSaved, onMsg, open, onToggle }: { p: Profile; dicts: Dicts; canEdit: boolean; canCheck: boolean; canVerify: boolean; onSaved: () => void; onMsg: (m: string) => void; open: boolean; onToggle: () => void }) {
  const [f, setF] = useState<Profile>(p);
  const [dirty, setDirty] = useState(false);
  const [newBank, setNewBank] = useState<Partial<Bank> | null>(null);
  useEffect(() => { setF(p); setDirty(false); }, [p]);
  const set = (x: Partial<Profile>) => { setF({ ...f, ...x }); setDirty(true); };
  const setName = (lang: keyof Names, k: "full" | "short", v: string) => set({ names: { ...f.names, [lang]: { ...f.names[lang], [k]: v } } });
  const save = () => {
    const { id, owner_kind, owner_id, kind_title, check_status, check_status_title, checked_at, checked_by_name, country_fields, banks, summary, history, ...body } = f;
    void id; void owner_kind; void owner_id; void kind_title; void check_status; void check_status_title; void checked_at; void checked_by_name; void country_fields; void banks; void summary; void history;
    api.put(`/api/legal/profiles/${p.id}`, body).then(() => { setDirty(false); onSaved(); onMsg("Профиль сохранён"); }).catch((e) => onMsg((e as Error).message));
  };
  const inp = (k: keyof Profile, t: string, ph = "") => <label className="f">{t}<input value={String(f[k] ?? "")} placeholder={ph} disabled={!canEdit} onChange={(e) => set({ [k]: e.target.value } as Partial<Profile>)} /></label>;
  const isRu = f.kind === "ru_customer" || f.kind === "own" || f.country === "RU";
  const isCn = f.kind === "cn_factory";
  const checkColor = p.check_status === "checked" ? "ok" : p.check_status === "issues" ? "bad" : "warn";
  return (
    <div style={{ border: "1px solid var(--line)", borderLeft: `4px solid ${p.is_default ? "var(--ok)" : "var(--line)"}`, borderRadius: 8, padding: "8px 12px", marginBottom: 8, background: "#fff", opacity: p.is_active ? 1 : .6 }}>
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8, cursor: "pointer" }} onClick={onToggle}>
        <div><b>{p.title || p.names.ru.short || p.names.ru.full || "Профиль"}</b> <span className="badge info" style={{ marginLeft: 4 }}>{p.kind_title}</span>
          {p.is_default && <span className="badge ok" style={{ marginLeft: 4 }}>по умолчанию</span>}{!p.is_active && <span className="badge" style={{ marginLeft: 4 }}>архив</span>}
          <span className={`badge ${checkColor}`} style={{ marginLeft: 4 }} title={p.checked_at ? `${p.checked_by_name} · ${fmtDate(p.checked_at)}` : ""}>{p.check_status_title}</span>
          <div className="muted" style={{ fontSize: 12 }}>{p.summary || "реквизиты не заполнены"}{p.banks.filter((b) => b.status === "active").length ? ` · банк: ${p.banks.filter((b) => b.status === "active").map((b) => `${b.currency}${b.verified ? " ✓" : ""}`).join(", ")}` : " · банковских реквизитов нет"}</div></div>
        <span className="muted" style={{ fontSize: 12 }}>{open ? "свернуть ▴" : "подробнее ▾"}</span>
      </div>
      {open && (
        <div style={{ marginTop: 8 }}>
          <div className="grid cols-3">
            {inp("title", "Название профиля", "Головной офис / Филиал …")}
            <label className="f">Тип контрагента<select value={f.kind} disabled={!canEdit} onChange={(e) => set({ kind: e.target.value })}>{Object.entries(dicts.profile_kinds).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select></label>
            <label className="f">Страна / по умолчанию<div className="row"><input style={{ width: 70 }} value={f.country} disabled={!canEdit} onChange={(e) => set({ country: e.target.value.toUpperCase() })} />
              <label className="row" style={{ fontSize: 12 }}><input type="checkbox" checked={f.is_default} disabled={!canEdit} onChange={(e) => set({ is_default: e.target.checked })} /> подставлять в договоры</label></div></label>
          </div>
          <div className="muted" style={{ fontSize: 11, letterSpacing: ".4px", textTransform: "uppercase", margin: "8px 0 2px" }}>Наименования</div>
          <div className="grid cols-3">
            <label className="f">Полное (RU)<input value={f.names.ru.full} disabled={!canEdit} onChange={(e) => setName("ru", "full", e.target.value)} /></label>
            <label className="f">Краткое (RU)<input value={f.names.ru.short} disabled={!canEdit} onChange={(e) => setName("ru", "short", e.target.value)} /></label>
            {inp("opf", "Организационно-правовая форма", "ООО / АО / Co., Ltd.")}
            <label className="f">Полное (EN)<input value={f.names.en.full} disabled={!canEdit} onChange={(e) => setName("en", "full", e.target.value)} /></label>
            <label className="f">Краткое (EN)<input value={f.names.en.short} disabled={!canEdit} onChange={(e) => setName("en", "short", e.target.value)} /></label>
            <label className="f">Наименование (中文)<input value={f.names.zh.full} disabled={!canEdit} onChange={(e) => setName("zh", "full", e.target.value)} /></label>
          </div>
          <div className="muted" style={{ fontSize: 11, letterSpacing: ".4px", textTransform: "uppercase", margin: "8px 0 2px" }}>Регистрация</div>
          <div className="grid cols-3">
            {inp("tax_number", isRu ? "ИНН" : isCn ? "Единый код соц. кредита (统一社会信用代码)" : "Налоговый номер")}
            {isRu ? inp("kpp", "КПП") : inp("reg_number", isCn ? "Регистрационный номер (если отличается)" : "Регистрационный номер")}
            {isRu && inp("reg_number", "ОГРН / ОГРНИП")}
            {inp("legal_address", "Юридический адрес")}{inp("actual_address", isCn ? "Адрес (中文)" : "Фактический адрес")}{inp("postal_address", "Почтовый адрес")}
            {(dicts.country_fields[f.kind] || []).map((cf) => <label key={cf.key} className="f">{cf.title}<input value={f.extra[cf.key] || ""} disabled={!canEdit} onChange={(e) => set({ extra: { ...f.extra, [cf.key]: e.target.value } })} /></label>)}
          </div>
          <div className="muted" style={{ fontSize: 11, letterSpacing: ".4px", textTransform: "uppercase", margin: "8px 0 2px" }}>Руководитель и подписант</div>
          <div className="grid cols-3">
            {inp("director", isCn ? "Законный представитель / руководитель" : "Руководитель")}{inp("signatory_name", "ФИО подписанта")}{inp("signatory_position", "Должность подписанта")}
            {inp("signatory_basis", "Действует на основании", "Устава / доверенности № … от …")}
          </div>
          <div className="muted" style={{ fontSize: 11, letterSpacing: ".4px", textTransform: "uppercase", margin: "8px 0 2px" }}>Доверенности и регистрационные документы</div>
          <table style={{ fontSize: 12 }}><thead><tr><th>Доверенность: кому</th><th>№</th><th>Выдана</th><th>Действует до</th><th>Полномочия</th>{canEdit && <th />}</tr></thead>
            <tbody>{(f.powers_of_attorney || []).map((d, i) => <tr key={i}>
              {(["person", "number", "issued_on", "valid_until", "scope"] as const).map((k) => <td key={k}><input type={k.includes("_on") || k.includes("until") ? "date" : "text"} value={d[k] || ""} disabled={!canEdit} style={{ width: k === "scope" ? 220 : k === "person" ? 180 : 120 }}
                onChange={(e) => set({ powers_of_attorney: f.powers_of_attorney.map((x, j) => (j === i ? { ...x, [k]: e.target.value } : x)) })} /></td>)}
              {canEdit && <td><button className="btn ghost" style={{ padding: "0 6px" }} onClick={() => set({ powers_of_attorney: f.powers_of_attorney.filter((_, j) => j !== i) })}>×</button></td>}</tr>)}
              {canEdit && <tr><td colSpan={6}><button className="btn ghost" style={{ padding: "2px 8px", fontSize: 12 }} onClick={() => set({ powers_of_attorney: [...(f.powers_of_attorney || []), { person: "", number: "", issued_on: "", valid_until: "", scope: "" }] })}>+ доверенность</button></td></tr>}</tbody></table>
          <table style={{ fontSize: 12, marginTop: 4 }}><thead><tr><th>Документ</th><th>№</th><th>Дата</th><th>Действует до</th>{canEdit && <th />}</tr></thead>
            <tbody>{(f.documents || []).map((d, i) => <tr key={i}>
              {(["title", "number", "issued_on", "valid_until"] as const).map((k) => <td key={k}><input type={k.includes("_on") || k.includes("until") ? "date" : "text"} value={d[k] || ""} disabled={!canEdit} style={{ width: k === "title" ? 260 : 120 }}
                onChange={(e) => set({ documents: f.documents.map((x, j) => (j === i ? { ...x, [k]: e.target.value } : x)) })} /></td>)}
              {canEdit && <td><button className="btn ghost" style={{ padding: "0 6px" }} onClick={() => set({ documents: f.documents.filter((_, j) => j !== i) })}>×</button></td>}</tr>)}
              {canEdit && <tr><td colSpan={5}><button className="btn ghost" style={{ padding: "2px 8px", fontSize: 12 }} onClick={() => set({ documents: [...(f.documents || []), { title: "", number: "", issued_on: "", valid_until: "" }] })}>+ документ (устав, свидетельство, лицензия…)</button></td></tr>}</tbody></table>
          <div className="muted" style={{ fontSize: 11, letterSpacing: ".4px", textTransform: "uppercase", margin: "8px 0 2px" }}>Контактные лица</div>
          <table style={{ fontSize: 12 }}><tbody>{(f.contacts || []).map((c, i) => <tr key={i}>
            {(["name", "position", "phone", "email"] as const).map((k) => <td key={k}><input placeholder={{ name: "имя", position: "должность", phone: "телефон", email: "e-mail" }[k]} value={c[k] || ""} disabled={!canEdit} style={{ width: 170 }}
              onChange={(e) => set({ contacts: f.contacts.map((x, j) => (j === i ? { ...x, [k]: e.target.value } : x)) })} /></td>)}
            {canEdit && <td><button className="btn ghost" style={{ padding: "0 6px" }} onClick={() => set({ contacts: f.contacts.filter((_, j) => j !== i) })}>×</button></td>}</tr>)}
            {canEdit && <tr><td colSpan={5}><button className="btn ghost" style={{ padding: "2px 8px", fontSize: 12 }} onClick={() => set({ contacts: [...(f.contacts || []), { name: "", position: "", phone: "", email: "" }] })}>+ контакт</button></td></tr>}</tbody></table>
          <label className="f" style={{ marginTop: 6 }}>Комментарий юриста<textarea rows={2} value={f.lawyer_comment} disabled={!canEdit && !canCheck} onChange={(e) => set({ lawyer_comment: e.target.value })} /></label>
          <div className="row" style={{ marginTop: 8, gap: 6, flexWrap: "wrap" }}>
            {canEdit && <button className="btn" disabled={!dirty} onClick={save}>Сохранить профиль</button>}
            {canCheck && <>
              <button className="btn secondary" onClick={() => api.post(`/api/legal/profiles/${p.id}/check`, { status: "checked", comment: f.lawyer_comment }).then(onSaved).catch((e) => onMsg((e as Error).message))}>Проверен юристом</button>
              <button className="btn ghost" style={{ color: "var(--bad)" }} onClick={() => api.post(`/api/legal/profiles/${p.id}/check`, { status: "issues", comment: f.lawyer_comment }).then(onSaved).catch((e) => onMsg((e as Error).message))}>Есть замечания</button></>}
            {canEdit && p.is_active && <button className="btn ghost" style={{ marginLeft: "auto", color: "var(--muted)", borderColor: "var(--line)" }} onClick={() => { if (confirm("Перевести профиль в архив? В новые договоры он подставляться не будет.")) api.put(`/api/legal/profiles/${p.id}`, { is_active: false, is_default: false }).then(onSaved).catch((e) => onMsg((e as Error).message)); }}>в архив</button>}
          </div>
          <div className="row" style={{ justifyContent: "space-between", margin: "12px 0 4px" }}>
            <b style={{ fontSize: 13 }}>Банковские реквизиты <span className="muted" style={{ fontWeight: 400 }}>· {p.banks.length || "нет"}</span></b>
            {canEdit && <button className="btn add" style={{ padding: "3px 10px", fontSize: 12 }} onClick={() => setNewBank({ ...EMPTY_BANK, currency: isRu ? "RUB" : isCn ? "CNY" : "USD", country: f.country })}>Счёт</button>}
          </div>
          {newBank && (
            <div style={{ border: "1px dashed var(--info)", borderRadius: 8, padding: 10, marginBottom: 6 }}>
              <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
                {(["title", "currency", "country", "beneficiary", "bank_name", "bank_address", "account", "bik", "swift", "corr_account", "corr_bank", "purpose"] as const).map((k) => (
                  <label key={k} className="f">{FIELD_T[k] ? FIELD_T[k][0].toUpperCase() + FIELD_T[k].slice(1) : k}<input style={{ width: k === "currency" ? 60 : k === "country" ? 70 : ["bank_name", "bank_address", "beneficiary", "purpose"].includes(k) ? 220 : 150 }} value={String(newBank[k] ?? "")} onChange={(e) => setNewBank({ ...newBank, [k]: e.target.value })} /></label>))}
              </div>
              <div className="row" style={{ marginTop: 6, gap: 6 }}>
                <button className="btn" disabled={!newBank.bank_name && !newBank.account} onClick={() => api.post(`/api/legal/profiles/${p.id}/banks`, newBank).then(() => { setNewBank(null); onSaved(); }).catch((e) => onMsg((e as Error).message))}>Добавить счёт</button>
                <button className="btn ghost" onClick={() => setNewBank(null)}>Отмена</button></div>
            </div>)}
          {p.banks.map((b) => <BankCard key={b.id} b={b} canEdit={canEdit} canVerify={canVerify} onSaved={onSaved} onMsg={onMsg} />)}
          <History items={p.history} />
        </div>)}
    </div>);
}

export default function LegalProfileBlock({ ownerKind, ownerId, title = "Юридический профиль" }: { ownerKind: "client" | "factory" | "own"; ownerId: string; title?: string }) {
  const [data, setData] = useState<Resp | null>(null);
  const [dicts, setDicts] = useState<Dicts | null>(null);
  const [msg, setMsg] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [denied, setDenied] = useState(false);
  const load = () => api.get<Resp>(`/api/legal/profiles?owner_kind=${ownerKind}&owner_id=${ownerId}`).then((r) => { setData(r); if (open === null && r.items.length === 1) setOpen(r.items[0].id); })
    .catch((e) => { if ((e as { status?: number }).status === 403) setDenied(true); else setMsg((e as Error).message); });
  useEffect(() => { setOpen(null); load(); }, [ownerKind, ownerId]);
  useEffect(() => { api.get<Dicts>("/api/legal/dictionaries").then(setDicts).catch(() => undefined); }, []);
  if (denied) return null;
  if (!data || !dicts) return <div className="card muted">{msg || "Загрузка юридического профиля…"}</div>;
  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <h2 style={{ margin: 0 }}>{title} <span className="muted" style={{ fontWeight: 400, fontSize: 13 }}>· {data.items.filter((p) => p.is_active).length || "нет"}</span></h2>
        {data.can_edit && <button className="btn add" onClick={() => api.post<Profile>("/api/legal/profiles", { owner_kind: ownerKind, owner_id: ownerId, title: "Новый профиль", kind: data.items[0]?.kind || "other", country: data.items[0]?.country || "RU" })
          .then((p) => { setOpen(p.id); load(); }).catch((e) => setMsg((e as Error).message))}>Профиль</button>}
      </div>
      <p className="muted" style={{ margin: "4px 0 8px", fontSize: 12 }}>Юрлицо, регистрация, адреса, подписант и доверенности, банковские реквизиты со статусом и подтверждением. Профиль «по умолчанию» подставляется в договоры; первый профиль создан из карточки автоматически.</p>
      {msg && <div className="muted" style={{ fontSize: 12 }}>{msg}</div>}
      {data.items.map((p) => <ProfileCard key={p.id} p={p} dicts={dicts} canEdit={data.can_edit} canCheck={data.can_check} canVerify={data.can_verify_bank} onSaved={load} onMsg={setMsg} open={open === p.id} onToggle={() => setOpen(open === p.id ? null : p.id)} />)}
    </div>);
}
