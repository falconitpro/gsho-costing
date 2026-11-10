import { useState } from "react";
import { api } from "../api";

// Быстрое заведение поставщика прямо из выбора поставщика (запрос цены ГШО и запчастей). Заведённый менеджером — на утверждении КД/ЗГД;
// запросить у него цену можно после утверждения (правило «Поставщики»).

export type NewSupplier = { id: string; name: string; short_name: string; country: string; supplier_status: string; approval_status?: string };

export default function NewSupplierInline({ supplies, onCreated, onCancel }: { supplies: "gsho" | "parts"; onCreated: (f: NewSupplier, msg: string) => void; onCancel: () => void }) {
  const [f, setF] = useState({ name: "", country: "CN", email: "", contact: "" });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  async function save() {
    setBusy(true);
    try {
      const x = await api.post<NewSupplier>("/api/refs/factories", { name: f.name.trim(), country: f.country.trim() || "CN", supplies: [supplies],
        contacts: f.email.trim() || f.contact.trim() ? [{ name: f.contact.trim(), email: f.email.trim(), roles: ["rfq"], primary: true }] : [] });
      onCreated(x, x.approval_status === "pending"
        ? `Поставщик «${x.name}» заведён и отправлен на утверждение КД или ЗГД — запросить у него цену можно после утверждения`
        : `Поставщик «${x.name}» заведён и выбран`);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  return (
    <div style={{ border: "1px dashed var(--line)", borderRadius: 8, padding: 8, marginTop: 6, width: "100%" }}>
      <b style={{ fontSize: 13 }}>Новый поставщик</b>
      <div className="row" style={{ flexWrap: "wrap", gap: 6, marginTop: 4 }}>
        <input autoFocus placeholder="Название *" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} style={{ flex: "2 1 220px" }} />
        <input placeholder="Страна (CN, DE…)" value={f.country} onChange={(e) => setF({ ...f, country: e.target.value })} style={{ width: 110 }} />
        <input placeholder="Контакт" value={f.contact} onChange={(e) => setF({ ...f, contact: e.target.value })} style={{ flex: "1 1 140px" }} />
        <input placeholder="E-mail для запросов" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} style={{ flex: "1 1 180px" }} />
        <button className="btn" disabled={!f.name.trim() || busy} onClick={save}>Завести</button>
        <button className="btn ghost" onClick={onCancel}>отмена</button>
      </div>
      {err && <div className="muted" style={{ color: "var(--bad)", fontSize: 12 }}>{err}</div>}
      <div className="muted" style={{ fontSize: 11 }}>Полная карточка (реквизиты, WeCom, условия) — в разделе «Поставщики».</div>
    </div>);
}
