import { useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "../api";

// Доверенности компаний группы: реестр (статусы, сроки, истекающие), конструктор (представитель → вид и срок → полномочия → ограничения),
// печать DOCX/PDF, копии и сканы, отзыв с уведомлением. Оформляет и отзывает юрист или ЗГД; остальным — реестр без паспортных данных.

type Passport = { series?: string; number?: string; issued_by?: string; issued_on?: string; code?: string };
type Attorney = { full_name?: string; position?: string; birth_date?: string; birth_place?: string; passport?: Passport; address?: string; phone?: string; email?: string };
type Limits = { amount_limit?: number | string; currency?: string; counterparties?: string; territory?: string; substitution?: boolean; extra?: string };
type PoaFile = { id: string; kind: string; kind_title: string; name: string; size: number; uploaded_by_name: string; uploaded_at: string };
type Poa = {
  id: string; entity_id: string; entity_name: string; profile_id: string | null; number: string; kind: string; kind_title: string; status: string; status_title: string;
  issued_on: string | null; valid_to: string | null; days_left: number | null; expiring: boolean; place: string; principal: Record<string, string>; attorney_user_id: string | null;
  attorney: Attorney; powers: { code?: string; text?: string }[]; powers_resolved: { n: number; text: string }[]; limits: Limits; files: PoaFile[];
  history: { at: string; by_name: string; action: string; comment: string }[]; revoked_at: string | null; revoked_by_name: string; revoke_reason: string; notes: string; created_by_name: string; created_at: string;
};
type Registry = {
  items: Poa[]; summary: Record<string, number>; statuses: Record<string, string>; kinds: Record<string, string>; entities: { id: string; name: string }[]; can_issue: boolean; sensitive_visible: boolean;
  employees: { id: string; display_name: string; position: string }[]; templates: { id: string; kind: string; title: string; version: number }[];
};
type Catalog = { powers: { code: string; title: string; items: { code: string; text: string }[] }[]; kinds: Record<string, string>; statuses: Record<string, string>; file_kinds: Record<string, string> };

const STATUS_CLASS: Record<string, string> = { draft: "", active: "ok", expired: "warn", revoked: "bad", cancelled: "" };
const d = (s?: string | null) => (s ? new Date(s).toLocaleDateString("ru-RU") : "—");
const ACTION: Record<string, string> = { created: "создана", edited: "изменена", issued: "оформлена", file: "файл", file_removed: "файл удалён", revoked: "отозвана", cancelled: "аннулирована", expired: "срок истёк" };

export default function PoaBlock({ entityId, compact }: { entityId?: string; compact?: boolean }) {
  const [reg, setReg] = useState<Registry | null>(null);
  const [cat, setCat] = useState<Catalog | null>(null);
  const [msg, setMsg] = useState("");
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<Poa | null>(null);
  const [creating, setCreating] = useState(false);
  const load = () => api.get<Registry>(`/api/legal/poa?entity_id=${entityId || ""}&status=${status}&q=${encodeURIComponent(q)}`).then(setReg).catch((e) => setMsg((e as Error).message));
  useEffect(() => { load(); }, [entityId, status, q]);
  useEffect(() => { api.get<Catalog>("/api/legal/poa/catalog").then(setCat).catch(() => undefined); }, []);
  const reload = (p?: Poa) => { load(); if (p) setOpen(p); };
  if (!reg) return <div className="card">{msg || "Загрузка…"}</div>;
  const s = reg.summary;
  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <div>
          <b>Доверенности{entityId ? "" : " компаний группы"}</b>
          <div className="muted" style={{ fontSize: 12 }}>
            действует: <b>{s.active || 0}</b>{s.expiring ? <> · <span style={{ color: "var(--warn)" }}>истекает в 30 дней: <b>{s.expiring}</b></span></> : null} · черновиков: {s.draft || 0} · отозвано: {s.revoked || 0} · срок истёк: {s.expired || 0}
          </div>
        </div>
        <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
          <input placeholder="номер, представитель…" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 180 }} />
          <select value={status} onChange={(e) => setStatus(e.target.value)}><option value="">все статусы</option>{Object.entries(reg.statuses).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
          {reg.can_issue && <button className="btn add" onClick={() => setCreating(true)}>Доверенность</button>}
        </div>
      </div>
      {msg && <div className="alert error" style={{ marginTop: 8 }}>{msg}</div>}
      {!reg.can_issue && <p className="muted" style={{ fontSize: 12, margin: "6px 0 0" }}>Оформляет и отзывает доверенности юрист или ЗГД. {reg.sensitive_visible ? "" : "Паспортные данные представителей скрыты."}</p>}
      <table className="table" style={{ marginTop: 8, fontSize: 13 }}>
        <thead><tr><th>Номер</th>{!entityId && <th>Компания</th>}<th>Представитель</th><th>Вид</th><th>Выдана</th><th>Действует до</th><th>Статус</th><th>Копии</th></tr></thead>
        <tbody>
          {reg.items.length === 0 && <tr><td colSpan={8} className="muted">Доверенностей пока нет{reg.can_issue ? " — нажмите «+ Доверенность»" : ""}.</td></tr>}
          {reg.items.map((p) => (
            <tr key={p.id} onClick={() => setOpen(p)} style={{ cursor: "pointer", background: p.expiring ? "var(--warn-bg)" : undefined }}>
              <td><b>{p.number || "проект"}</b></td>
              {!entityId && <td>{p.entity_name}</td>}
              <td>{p.attorney.full_name || "—"}<div className="muted" style={{ fontSize: 11 }}>{p.attorney.position}</div></td>
              <td>{p.kind_title}</td>
              <td>{d(p.issued_on)}</td>
              <td>{d(p.valid_to)}{p.status === "active" && p.days_left !== null && <div className="muted" style={{ fontSize: 11, color: p.expiring ? "var(--warn)" : undefined }}>{p.days_left >= 0 ? `осталось ${p.days_left} дн.` : "истекла"}</div>}</td>
              <td><span className={`badge ${STATUS_CLASS[p.status] || ""}`}>{p.status_title}</span></td>
              <td className="muted">{p.files.filter((f) => f.kind === "signed_scan").length ? "скан ✓" : p.files.length ? `${p.files.length}` : "—"}</td>
            </tr>))}
        </tbody>
      </table>
      {!compact && <p className="muted" style={{ fontSize: 11, marginTop: 6 }}>Номер присваивается при оформлении: Д-&lt;код компании&gt;-&lt;год&gt;-&lt;порядковый&gt;. За 30 и 7 дней до истечения юристы, ЗГД и представитель получают уведомление; по истечении срока статус меняется автоматически. Отозванная доверенность остаётся в реестре с уведомлением об отмене.</p>}
      {creating && cat && <PoaEditor reg={reg} cat={cat} entityId={entityId} onClose={() => setCreating(false)} onSaved={(p) => { setCreating(false); reload(p); }} />}
      {open && cat && (open.status === "draft" && reg.can_issue
        ? <PoaEditor reg={reg} cat={cat} entityId={open.entity_id} poa={open} onClose={() => setOpen(null)} onSaved={(p) => reload(p)} onChanged={() => { setOpen(null); load(); }} />
        : <PoaView p={open} reg={reg} cat={cat} onClose={() => setOpen(null)} onChanged={(p) => reload(p)} />)}
    </div>
  );
}

// ------------------------------------------------------------------ конструктор (черновик)
function PoaEditor({ reg, cat, entityId, poa, onClose, onSaved, onChanged }: { reg: Registry; cat: Catalog; entityId?: string; poa?: Poa; onClose: () => void; onSaved: (p: Poa) => void; onChanged?: () => void }) {
  const [step, setStep] = useState(0);
  const [ent, setEnt] = useState(poa?.entity_id || entityId || reg.entities[0]?.id || "");
  const [userId, setUserId] = useState(poa?.attorney_user_id || "");
  const [att, setAtt] = useState<Attorney>(poa?.attorney || { passport: {} });
  const [kind, setKind] = useState(poa?.kind || "special");
  const [validTo, setValidTo] = useState(poa?.valid_to || "");
  const [place, setPlace] = useState(poa?.place || "г. Москва");
  const [sel, setSel] = useState<Set<string>>(new Set((poa?.powers || []).filter((x) => x.code).map((x) => x.code!)));
  const [custom, setCustom] = useState<string[]>((poa?.powers || []).filter((x) => !x.code && x.text).map((x) => x.text!));
  const [lim, setLim] = useState<Limits>(poa?.limits || { currency: "RUB", substitution: false });
  const [notes, setNotes] = useState(poa?.notes || "");
  const [tpl, setTpl] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string[]>([]);
  const [saved, setSaved] = useState<Poa | undefined>(poa);
  const tpls = reg.templates.filter((t) => t.kind === "power_of_attorney");
  const pickUser = (id: string) => {
    setUserId(id);
    const u = reg.employees.find((x) => x.id === id);
    if (u) setAtt((a) => ({ ...a, full_name: a.full_name || u.display_name, position: a.position || u.position }));
  };
  const body = () => ({ entity_id: ent, kind, place, valid_to: validTo || null, attorney_user_id: userId || null, attorney: att, notes,
    powers: [...Array.from(sel).map((code) => ({ code })), ...custom.filter((t) => t.trim()).map((text) => ({ text }))], limits: { ...lim, amount_limit: lim.amount_limit ? Number(lim.amount_limit) : null } });
  const save = async (): Promise<Poa | null> => {
    setBusy(true); setErr([]);
    try {
      const p = saved ? await api.put<Poa>(`/api/legal/poa/${saved.id}`, body()) : await api.post<Poa>("/api/legal/poa", body());
      setSaved(p); return p;
    } catch (e) { setErr([(e as Error).message]); return null; } finally { setBusy(false); }
  };
  const issue = async () => {
    const p = await save(); if (!p) return;
    const chk = await api.post<{ ok: boolean; errors: string[] }>(`/api/legal/poa/${p.id}/check`);
    if (!chk.ok) { setErr(chk.errors); return; }
    if (!window.confirm(`Оформить доверенность на ${att.full_name} до ${d(validTo)}? Номер будет присвоен, текст — зафиксирован.`)) return;
    setBusy(true);
    try { onSaved(await api.post<Poa>(`/api/legal/poa/${p.id}/issue`, { template_id: tpl || null })); }
    catch (e) { const det = (e as { detail?: { errors?: string[] } }).detail; setErr(det?.errors?.length ? det.errors : [(e as Error).message]); } finally { setBusy(false); }
  };
  const cancel = async () => { if (saved && window.confirm("Аннулировать черновик?")) { await api.post(`/api/legal/poa/${saved.id}/cancel`); onChanged?.(); } };
  const steps = ["Доверитель и представитель", "Вид и срок", "Полномочия", "Ограничения и печать"];
  const p = att.passport || {};
  const setP = (k: keyof Passport, v: string) => setAtt({ ...att, passport: { ...p, [k]: v } });
  return (
    <Modal title={saved ? `Черновик доверенности — ${att.full_name || ""}` : "Новая доверенность"} onClose={onClose} wide>
      <div className="row" style={{ gap: 4, marginBottom: 10, flexWrap: "wrap" }}>
        {steps.map((t, i) => <button key={t} className={`btn ${i === step ? "" : "ghost"}`} onClick={() => setStep(i)} style={{ fontSize: 12 }}>{i + 1}. {t}</button>)}
      </div>
      {err.length > 0 && <div className="alert error"><ul style={{ margin: 0, paddingLeft: 18 }}>{err.map((e) => <li key={e}>{e}</li>)}</ul></div>}
      {step === 0 && (
        <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <div>
            <label>Доверитель — компания группы<select value={ent} onChange={(e) => setEnt(e.target.value)} disabled={!!saved}>{reg.entities.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}</select></label>
            <p className="muted" style={{ fontSize: 12 }}>Реквизиты и подписант берутся из юридического профиля компании по умолчанию (карточка БЕРГ-ХАУС) и фиксируются в доверенности при оформлении.</p>
            <label>Представитель — сотрудник<select value={userId} onChange={(e) => pickUser(e.target.value)}><option value="">— вручную (не сотрудник) —</option>{reg.employees.map((u) => <option key={u.id} value={u.id}>{u.display_name}{u.position ? ` — ${u.position}` : ""}</option>)}</select></label>
            <label>ФИО полностью<input value={att.full_name || ""} onChange={(e) => setAtt({ ...att, full_name: e.target.value })} /></label>
            <label>Должность<input value={att.position || ""} onChange={(e) => setAtt({ ...att, position: e.target.value })} /></label>
            <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: 8 }}>
              <label>Дата рождения<input type="date" value={att.birth_date || ""} onChange={(e) => setAtt({ ...att, birth_date: e.target.value })} /></label>
              <label>Место рождения<input value={att.birth_place || ""} onChange={(e) => setAtt({ ...att, birth_place: e.target.value })} /></label>
            </div>
          </div>
          <div>
            <b style={{ fontSize: 13 }}>Паспорт</b>
            <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: 8 }}>
              <label>Серия<input value={p.series || ""} onChange={(e) => setP("series", e.target.value)} /></label>
              <label>Номер<input value={p.number || ""} onChange={(e) => setP("number", e.target.value)} /></label>
            </div>
            <label>Кем выдан<input value={p.issued_by || ""} onChange={(e) => setP("issued_by", e.target.value)} /></label>
            <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: 8 }}>
              <label>Дата выдачи<input type="date" value={p.issued_on || ""} onChange={(e) => setP("issued_on", e.target.value)} /></label>
              <label>Код подразделения<input value={p.code || ""} onChange={(e) => setP("code", e.target.value)} placeholder="770-001" /></label>
            </div>
            <label>Адрес регистрации<input value={att.address || ""} onChange={(e) => setAtt({ ...att, address: e.target.value })} /></label>
            <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: 8 }}>
              <label>Телефон<input value={att.phone || ""} onChange={(e) => setAtt({ ...att, phone: e.target.value })} /></label>
              <label>E-mail<input value={att.email || ""} onChange={(e) => setAtt({ ...att, email: e.target.value })} /></label>
            </div>
          </div>
        </div>)}
      {step === 1 && (
        <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <div>
            <label>Вид доверенности<select value={kind} onChange={(e) => setKind(e.target.value)}>{Object.entries(cat.kinds).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
            <p className="muted" style={{ fontSize: 12 }}>Генеральная — широкий круг полномочий; специальная — однородные действия (договоры, таможня, банк); разовая — одно действие.</p>
            <label>Действительна до<input type="date" value={validTo} onChange={(e) => setValidTo(e.target.value)} /></label>
            <div className="row" style={{ gap: 4, flexWrap: "wrap" }}>{[["3 мес.", 3], ["6 мес.", 6], ["1 год", 12], ["3 года", 36]].map(([t, m]) => <button key={t as string} className="btn ghost" style={{ fontSize: 12 }} onClick={() => { const x = new Date(); x.setMonth(x.getMonth() + (m as number)); setValidTo(x.toISOString().slice(0, 10)); }}>{t as string}</button>)}</div>
          </div>
          <div>
            <label>Место выдачи<input value={place} onChange={(e) => setPlace(e.target.value)} /></label>
            <label>Примечание (внутреннее, в текст не попадает)<textarea rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} /></label>
          </div>
        </div>)}
      {step === 2 && (
        <div>
          <p className="muted" style={{ fontSize: 12, marginTop: 0 }}>Отметьте полномочия из каталога (формулировки по сумме подставятся из ограничений) и при необходимости добавьте свои.</p>
          <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            {cat.powers.map((g) => (
              <div key={g.code} style={{ border: "1px solid var(--line)", borderRadius: 8, padding: "8px 12px" }}>
                <div className="row" style={{ justifyContent: "space-between" }}><b style={{ fontSize: 13 }}>{g.title}</b>
                  <button className="btn ghost" style={{ fontSize: 11 }} onClick={() => { const n = new Set(sel); const all = g.items.every((i) => n.has(i.code)); g.items.forEach((i) => (all ? n.delete(i.code) : n.add(i.code))); setSel(n); }}>{g.items.every((i) => sel.has(i.code)) ? "снять все" : "все"}</button></div>
                {g.items.map((i) => (
                  <label key={i.code} style={{ display: "flex", gap: 8, alignItems: "flex-start", fontSize: 13, fontWeight: 400, margin: "4px 0" }}>
                    <input type="checkbox" checked={sel.has(i.code)} onChange={(e) => { const n = new Set(sel); e.target.checked ? n.add(i.code) : n.delete(i.code); setSel(n); }} style={{ marginTop: 3 }} />
                    <span>{i.text.replace("{{amount_clause}}", lim.amount_limit ? ` на сумму не более ${Number(lim.amount_limit).toLocaleString("ru-RU")} ${lim.currency || "RUB"}` : "")}</span>
                  </label>))}
              </div>))}
          </div>
          <b style={{ fontSize: 13, display: "block", marginTop: 10 }}>Свои полномочия</b>
          {custom.map((t, i) => <div key={i} className="row" style={{ gap: 6, marginTop: 4 }}><input value={t} onChange={(e) => setCustom(custom.map((x, j) => (j === i ? e.target.value : x)))} style={{ flex: 1 }} /><button className="btn ghost" onClick={() => setCustom(custom.filter((_, j) => j !== i))}>×</button></div>)}
          <button className="btn ghost add" style={{ marginTop: 6, fontSize: 12 }} onClick={() => setCustom([...custom, ""])}>Полномочие</button>
          <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>Выбрано: {sel.size + custom.filter((x) => x.trim()).length}</div>
        </div>)}
      {step === 3 && (
        <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <div>
            <div className="grid" style={{ gridTemplateColumns: "2fr 1fr", gap: 8 }}>
              <label>Лимит суммы по одной сделке<input type="number" value={lim.amount_limit ?? ""} onChange={(e) => setLim({ ...lim, amount_limit: e.target.value })} placeholder="без ограничения" /></label>
              <label>Валюта<select value={lim.currency || "RUB"} onChange={(e) => setLim({ ...lim, currency: e.target.value })}>{["RUB", "USD", "EUR", "CNY", "KGS"].map((c) => <option key={c}>{c}</option>)}</select></label>
            </div>
            <label>Контрагенты (ограничение)<input value={lim.counterparties || ""} onChange={(e) => setLim({ ...lim, counterparties: e.target.value })} placeholder="только с АО «Полюс Алдан»…" /></label>
            <label>Территория действия<input value={lim.territory || ""} onChange={(e) => setLim({ ...lim, territory: e.target.value })} placeholder="Российская Федерация" /></label>
            <label style={{ display: "flex", gap: 8, alignItems: "center", fontWeight: 400 }}><input type="checkbox" checked={!!lim.substitution} onChange={(e) => setLim({ ...lim, substitution: e.target.checked })} /> с правом передоверия</label>
            <label>Прочие ограничения<textarea rows={2} value={lim.extra || ""} onChange={(e) => setLim({ ...lim, extra: e.target.value })} /></label>
          </div>
          <div>
            <b style={{ fontSize: 13 }}>Печатная форма</b>
            {tpls.length > 0 && <label>Шаблон DOCX<select value={tpl} onChange={(e) => setTpl(e.target.value)}><option value="">встроенная форма</option>{tpls.map((t) => <option key={t.id} value={t.id}>{t.title} (v{t.version})</option>)}</select></label>}
            {tpls.length === 0 && <p className="muted" style={{ fontSize: 12 }}>Своего шаблона вида «Доверенность» в библиотеке нет — используется встроенная форма. Загрузить шаблон можно во вкладке «Шаблоны и примеры» (переменные poa.*, principal.*, attorney.*, строки powers[]).</p>}
            {saved && <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
              <a className="btn ghost" href={`/api/legal/poa/${saved.id}/preview`}>Проект DOCX</a>
              <a className="btn ghost" href={`/api/legal/poa/${saved.id}/preview?fmt=pdf`} target="_blank" rel="noreferrer">Проект PDF</a>
            </div>}
            <p className="muted" style={{ fontSize: 12 }}>После «Оформить» присваивается номер, фиксируются дата, текст и реквизиты; DOCX и PDF сохраняются как копии. Далее — распечатать, подписать, загрузить скан.</p>
          </div>
        </div>)}
      <div className="row" style={{ justifyContent: "space-between", marginTop: 14, flexWrap: "wrap", gap: 6 }}>
        <div className="row" style={{ gap: 6 }}>
          {saved && <button className="btn ghost" onClick={cancel} style={{ color: "var(--bad)" }}>Аннулировать черновик</button>}
        </div>
        <div className="row" style={{ gap: 6 }}>
          {step > 0 && <button className="btn ghost" onClick={() => setStep(step - 1)}>Назад</button>}
          <button className="btn ghost" disabled={busy} onClick={async () => { const p = await save(); if (p && !saved) onSaved(p); }}>Сохранить черновик</button>
          {step < 3 ? <button className="btn" onClick={() => setStep(step + 1)}>Далее</button> : <button className="btn" disabled={busy} onClick={issue}>Оформить</button>}
        </div>
      </div>
    </Modal>
  );
}

// ------------------------------------------------------------------ карточка оформленной / отозванной
function PoaView({ p, reg, cat, onClose, onChanged }: { p: Poa; reg: Registry; cat: Catalog; onClose: () => void; onChanged: (p: Poa) => void }) {
  const [reason, setReason] = useState("");
  const [revoking, setRevoking] = useState(false);
  const [fileKind, setFileKind] = useState("signed_scan");
  const [err, setErr] = useState("");
  const a = p.attorney, pr = p.principal, lim = p.limits;
  const run = (fn: () => Promise<Poa>) => fn().then(onChanged).catch((e) => setErr((e as Error).message));
  const groups = useMemo(() => p.files.reduce<Record<string, PoaFile[]>>((acc, f) => { (acc[f.kind] ||= []).push(f); return acc; }, {}), [p.files]);
  return (
    <Modal title={`Доверенность ${p.number || "(проект)"} — ${a.full_name || ""}`} onClose={onClose} wide>
      <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <span className={`badge ${STATUS_CLASS[p.status] || ""}`}>{p.status_title}</span><span className="badge">{p.kind_title}</span>
        <span className="muted" style={{ fontSize: 13 }}>{p.entity_name} · выдана {d(p.issued_on)} · до {d(p.valid_to)}{p.status === "active" && p.days_left !== null ? ` (осталось ${p.days_left} дн.)` : ""}</span>
      </div>
      {p.status === "revoked" && <div className="alert error" style={{ marginTop: 8 }}>Отозвана {d(p.revoked_at)} ({p.revoked_by_name}): {p.revoke_reason}</div>}
      {p.expiring && <div className="alert warn" style={{ marginTop: 8 }}>Срок истекает — при необходимости оформите новую на основе этой («Продлить»).</div>}
      {err && <div className="alert error" style={{ marginTop: 8 }}>{err}</div>}
      <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: 12, marginTop: 10, fontSize: 13 }}>
        <div>
          <b>Доверитель</b>
          <div>{pr.name}</div><div className="muted">ИНН {pr.inn}{pr.kpp ? ` / КПП ${pr.kpp}` : ""} · ОГРН {pr.ogrn}</div><div className="muted">{pr.address}</div>
          <div className="muted">в лице {pr.signatory_position} {pr.signatory}, действующего на основании {pr.signatory_basis || "Устава"}</div>
          <b style={{ display: "block", marginTop: 8 }}>Представитель</b>
          <div>{a.full_name}{a.position ? `, ${a.position}` : ""}</div>
          {reg.sensitive_visible ? <div className="muted">{a.birth_date ? `${d(a.birth_date)} г. р.` : ""}{a.passport?.series || a.passport?.number ? ` · паспорт ${a.passport?.series || ""} ${a.passport?.number || ""}` : ""}{a.address ? ` · ${a.address}` : ""}</div> : <div className="muted">паспортные данные скрыты</div>}
          <div className="muted">{[a.phone, a.email].filter(Boolean).join(" · ")}</div>
          <b style={{ display: "block", marginTop: 8 }}>Ограничения</b>
          <div className="muted">{lim.amount_limit ? `сумма до ${Number(lim.amount_limit).toLocaleString("ru-RU")} ${lim.currency || "RUB"} по сделке` : "без лимита суммы"} · {lim.substitution ? "с правом передоверия" : "без права передоверия"}{lim.territory ? ` · ${lim.territory}` : ""}{lim.counterparties ? ` · контрагенты: ${lim.counterparties}` : ""}{lim.extra ? ` · ${lim.extra}` : ""}</div>
        </div>
        <div>
          <b>Полномочия</b>
          <ol style={{ margin: "4px 0", paddingLeft: 20 }}>{p.powers_resolved.map((x) => <li key={x.n}>{x.text}</li>)}</ol>
        </div>
      </div>
      <div style={{ marginTop: 12 }}>
        <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 6 }}>
          <b style={{ fontSize: 13 }}>Копии и сканы</b>
          <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
            {p.status !== "draft" && reg.sensitive_visible && <a className="btn ghost" href={`/api/legal/poa/${p.id}/preview`} style={{ fontSize: 12 }}>Печать DOCX</a>}
            {p.status !== "draft" && reg.sensitive_visible && <a className="btn ghost" href={`/api/legal/poa/${p.id}/preview?fmt=pdf`} target="_blank" rel="noreferrer" style={{ fontSize: 12 }}>Печать PDF</a>}
            {reg.can_issue && <>
              <select value={fileKind} onChange={(e) => setFileKind(e.target.value)} style={{ fontSize: 12 }}>{Object.entries(cat.file_kinds).filter(([k]) => k !== "issued" && k !== "draft").map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
              <label className="btn ghost" style={{ fontSize: 12 }}>Загрузить<input type="file" accept=".pdf,.jpg,.jpeg,.png,.tif,.tiff,.docx,.zip" style={{ display: "none" }}
                onChange={(e) => { const f = e.target.files?.[0]; if (f) run(() => api.upload<Poa>(`/api/legal/poa/${p.id}/files`, f, { kind: fileKind })); e.target.value = ""; }} /></label></>}
          </div>
        </div>
        {p.files.length === 0 && <div className="muted" style={{ fontSize: 12 }}>файлов нет</div>}
        {Object.entries(groups).map(([k, fs]) => (
          <div key={k} style={{ fontSize: 13, marginTop: 4 }}><span className="muted">{fs[0].kind_title}:</span>{" "}
            {fs.map((f) => <span key={f.id} style={{ marginRight: 10 }}><a href={`/api/legal/poa/${p.id}/files/${f.id}`} target="_blank" rel="noreferrer">{f.name}</a> <span className="muted">({Math.max(1, Math.round(f.size / 1024))} КБ, {d(f.uploaded_at)})</span>
              {reg.can_issue && f.kind !== "issued" && <button className="btn ghost" style={{ fontSize: 11, padding: "0 4px" }} title="удалить файл" onClick={() => { if (window.confirm(`Удалить файл ${f.name}?`)) run(() => api.del<Poa>(`/api/legal/poa/${p.id}/files/${f.id}`)); }}>×</button>}</span>)}
          </div>))}
        {p.status === "active" && !groups.signed_scan && <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>Подписанный скан ещё не загружен.</div>}
      </div>
      {reg.can_issue && (
        <div style={{ marginTop: 12, borderTop: "1px solid var(--line)", paddingTop: 10 }}>
          <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
            {(p.status === "active" || p.status === "expired") && !revoking && <button className="btn ghost" style={{ color: "var(--bad)" }} onClick={() => setRevoking(true)}>Отозвать</button>}
            {(p.status === "active" || p.status === "expired" || p.status === "revoked") && <button className="btn ghost" onClick={() => run(() => api.post<Poa>(`/api/legal/poa/${p.id}/renew`))} title="новый черновик с теми же представителем, полномочиями и ограничениями">Продлить (новая на основе)</button>}
            {p.status === "active" && p.profile_id && <button className="btn ghost" title="подставить представителя подписантом в юридический профиль компании с основанием «доверенности № … от …»"
              onClick={() => { if (window.confirm(`Сделать ${a.full_name} подписантом компании по этой доверенности?`)) api.post(`/api/legal/poa/${p.id}/use-as-basis?profile_id=${p.profile_id}`).then(() => setErr("Подписант в профиле обновлён")).catch((e) => setErr((e as Error).message)); }}>Основание для подписанта</button>}
          </div>
          {revoking && (
            <div className="alert warn" style={{ marginTop: 8 }}>
              <b>Отзыв доверенности № {p.number}</b>
              <label>Причина<input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="прекращение трудовых отношений / изменение полномочий / …" /></label>
              <div className="row" style={{ gap: 6, marginTop: 6 }}>
                <button className="btn" disabled={reason.trim().length < 3} onClick={() => { if (window.confirm("Отозвать доверенность? Действие необратимо; представитель и юристы получат уведомление.")) run(() => api.post<Poa>(`/api/legal/poa/${p.id}/revoke`, { reason })).then(() => setRevoking(false)); }}>Подтвердить отзыв</button>
                <button className="btn ghost" onClick={() => setRevoking(false)}>Отмена</button>
              </div>
              <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>Формируется уведомление об отмене (DOCX) — направьте его представителю и контрагентам, которым предъявлялась доверенность.</div>
            </div>)}
        </div>)}
      <details style={{ marginTop: 10, fontSize: 12 }}><summary className="muted">История ({p.history.length})</summary>
        <ul style={{ margin: "4px 0", paddingLeft: 18 }}>{p.history.map((h, i) => <li key={i}>{new Date(h.at).toLocaleString("ru-RU")} · {h.by_name} · {ACTION[h.action] || h.action}{h.comment ? ` — ${h.comment}` : ""}</li>)}</ul>
        {p.notes && <div className="muted">Примечание: {p.notes}</div>}
      </details>
    </Modal>
  );
}

function Modal({ title, children, onClose, wide }: { title: string; children: ReactNode; onClose: () => void; wide?: boolean }) {
  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.35)", zIndex: 50, display: "flex", alignItems: "flex-start", justifyContent: "center", padding: "40px 16px", overflow: "auto" }}>
      <div className="card" onClick={(e) => e.stopPropagation()} style={{ width: "100%", maxWidth: wide ? 1000 : 640, background: "#fff" }}>
        <div className="row" style={{ justifyContent: "space-between", marginBottom: 8 }}><b style={{ fontSize: 16 }}>{title}</b><button className="btn ghost" onClick={onClose}>✕</button></div>
        {children}
      </div>
    </div>
  );
}
