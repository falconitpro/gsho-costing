import { useEffect, useState } from "react";
import { api } from "../api";

// Наборы реквизитов клиента для договоров: юрлицо/филиал, банк, адреса, подписант. Один — по умолчанию.
// Подтягиваются в блок «Реквизиты контракта» сделки автоматически (набор по умолчанию) или выбором вручную.

export type ReqSet = { id?: string; title: string; is_default: boolean; legal_name: string; short_name: string; inn: string; kpp: string; ogrn: string;
  legal_address: string; postal_address: string; bank_name: string; bik: string; account: string; corr_account: string;
  signatory_name: string; signatory_position: string; signatory_basis: string; phone: string; email: string; comment: string };

export const EMPTY_REQ: ReqSet = { title: "", is_default: false, legal_name: "", short_name: "", inn: "", kpp: "", ogrn: "", legal_address: "", postal_address: "",
  bank_name: "", bik: "", account: "", corr_account: "", signatory_name: "", signatory_position: "", signatory_basis: "", phone: "", email: "", comment: "" };

const GROUPS: { title: string; fields: [keyof ReqSet, string, string?][] }[] = [
  { title: "Юридическое лицо", fields: [["legal_name", "Полное наименование"], ["short_name", "Краткое наименование"], ["inn", "ИНН"], ["kpp", "КПП"], ["ogrn", "ОГРН / ОГРНИП"]] },
  { title: "Адреса", fields: [["legal_address", "Юридический адрес"], ["postal_address", "Почтовый адрес"]] },
  { title: "Банк", fields: [["bank_name", "Банк"], ["bik", "БИК"], ["account", "Расчётный счёт"], ["corr_account", "Корр. счёт"]] },
  { title: "Подписант", fields: [["signatory_name", "ФИО подписанта"], ["signatory_position", "Должность"], ["signatory_basis", "Действует на основании", "Устава / доверенности № … от …"]] },
  { title: "Связь", fields: [["phone", "Телефон"], ["email", "E-mail"], ["comment", "Комментарий"]] },
];

/** Короткая строка набора: наименование · ИНН/КПП · банк · подписант. */
export function reqSummary(r: Partial<ReqSet>): string {
  return [r.legal_name || r.short_name, r.inn && `ИНН ${r.inn}${r.kpp ? ` / КПП ${r.kpp}` : ""}`, r.bank_name && `${r.bank_name}${r.account ? ` …${r.account.slice(-4)}` : ""}`,
    r.signatory_name && `подписывает ${r.signatory_name}${r.signatory_position ? ` (${r.signatory_position})` : ""}`].filter(Boolean).join(" · ");
}

export default function ClientRequisites({ clientId, canEdit }: { clientId: string; canEdit: boolean }) {
  const url = `/api/refs/clients/${clientId}/requisites`;
  const [items, setItems] = useState<ReqSet[]>([]);
  const [fromCard, setFromCard] = useState<ReqSet | null>(null);
  const [edit, setEdit] = useState<number | null>(null);
  const [dirty, setDirty] = useState(false);
  const [msg, setMsg] = useState("");
  const load = () => api.get<{ items: ReqSet[]; from_card: ReqSet }>(url).then((r) => { setItems(r.items); setFromCard(r.from_card); setDirty(false); setEdit(null); }).catch((e) => setMsg((e as Error).message));
  useEffect(() => { load(); }, [clientId]);
  const upd = (i: number, p: Partial<ReqSet>) => { setItems(items.map((x, j) => (j === i ? { ...x, ...p } : p.is_default ? { ...x, is_default: false } : x))); setDirty(true); };
  async function save() {
    try { const r = await api.put<{ items: ReqSet[] }>(url, { items }); setItems(r.items); setDirty(false); setEdit(null); setMsg("Реквизиты сохранены"); }
    catch (e) { setMsg((e as Error).message); }
  }
  const add = (base: Partial<ReqSet>) => { const n = { ...EMPTY_REQ, ...base, id: undefined, is_default: items.length === 0 }; setItems([...items, n]); setEdit(items.length); setDirty(true); };
  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <h2 style={{ margin: 0 }}>Реквизиты для договоров <span className="muted" style={{ fontWeight: 400, fontSize: 13 }}>· {items.length || "нет"}</span></h2>
        {canEdit && <div className="row" style={{ gap: 6 }}>
          {fromCard && <button className="btn ghost" style={{ padding: "4px 10px", fontSize: 12 }} title="заполнить из ЕГРЮЛ: наименование, ИНН/КПП, ОГРН, адрес, руководитель" onClick={() => add({ ...fromCard, title: "Основной", is_default: items.length === 0 })}>Набор из карточки</button>}
          <button className="btn add" onClick={() => add({})}>Набор</button>
          <button className="btn" disabled={!dirty} onClick={save}>Сохранить</button></div>}
      </div>
      <p className="muted" style={{ margin: "4px 0 8px", fontSize: 12 }}>Юрлицо или филиал, банк, адреса, подписант. Набор «по умолчанию» подставляется в реквизиты контракта сделки автоматически; другой можно выбрать вручную.</p>
      {msg && <div className="muted" style={{ fontSize: 12 }}>{msg}</div>}
      {items.length === 0 && <div className="muted">Наборов нет{canEdit ? " — добавьте «Набор из карточки» и дополните банк и подписанта" : ""}.</div>}
      {items.map((r, i) => (
        <div key={r.id || i} style={{ border: "1px solid var(--line)", borderLeft: `4px solid ${r.is_default ? "var(--ok)" : "var(--line)"}`, borderRadius: 8, padding: "8px 12px", marginBottom: 8, background: "#fff" }}>
          <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8, cursor: "pointer" }} onClick={() => setEdit(edit === i ? null : i)}>
            <div><b>{r.title || r.legal_name || "Без названия"}</b>{r.is_default && <span className="badge ok" style={{ marginLeft: 8 }}>по умолчанию</span>}
              <div className="muted" style={{ fontSize: 12 }}>{reqSummary(r) || "реквизиты не заполнены"}</div></div>
            <span className="muted" style={{ fontSize: 12 }}>{edit === i ? "свернуть ▴" : canEdit ? "изменить ▾" : "подробнее ▾"}</span>
          </div>
          {edit === i && (
            <div style={{ marginTop: 8 }}>
              <div className="grid cols-3">
                <label className="f">Название набора<input value={r.title} disabled={!canEdit} placeholder="напр. Головной офис / Филиал Норильск" onChange={(e) => upd(i, { title: e.target.value })} /></label>
                <label className="f">По умолчанию<div className="row"><input type="checkbox" checked={r.is_default} disabled={!canEdit} onChange={(e) => upd(i, { is_default: e.target.checked })} /> подставлять в договоры</div></label>
              </div>
              {GROUPS.map((g) => (
                <div key={g.title} style={{ marginTop: 6 }}>
                  <div className="muted" style={{ fontSize: 11, letterSpacing: ".4px", textTransform: "uppercase", margin: "6px 0 2px" }}>{g.title}</div>
                  <div className="grid cols-3">{g.fields.map(([k, t, ph]) => (
                    <label key={k} className="f">{t}<input value={String(r[k] ?? "")} placeholder={ph || ""} disabled={!canEdit} onChange={(e) => upd(i, { [k]: e.target.value } as Partial<ReqSet>)} /></label>))}</div>
                </div>))}
              {canEdit && <div className="row" style={{ marginTop: 8 }}><button className="btn ghost" style={{ color: "var(--bad)" }} onClick={() => { setItems(items.filter((_, j) => j !== i)); setEdit(null); setDirty(true); }}>Удалить набор</button></div>}
            </div>)}
        </div>))}
    </div>
  );
}
