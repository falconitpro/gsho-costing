import { useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "../api";

// Доверенности компаний группы: реестр (статусы, сроки, истекающие), конструктор (представитель → вид и срок → полномочия → ограничения),
// печать DOCX/PDF, копии и сканы, отзыв с уведомлением. Оформляют и отзывают юрист, ЗГД и ФД; остальным — реестр без паспортных данных.

type Passport = { series?: string; number?: string; issued_by?: string; issued_on?: string; code?: string };
type Attorney = { full_name?: string; position?: string; birth_date?: string; birth_place?: string; citizenship?: string; inn?: string; passport?: Passport; address?: string; phone?: string; email?: string };
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
      {!reg.can_issue && <p className="muted" style={{ fontSize: 12, margin: "6px 0 0" }}>Оформляет и отзывает доверенности юрист, ЗГД или ФД. {reg.sensitive_visible ? "" : "Паспортные данные представителей скрыты."}</p>}
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
  const F = ({ label, children, span }: { label: string; children: ReactNode; span?: number }) => <label className="f" style={span ? { gridColumn: `span ${span}` } : undefined}><span>{label}</span>{children}</label>;
  const Section = ({ title, hint, children, wide }: { title: string; hint?: string; children: ReactNode; wide?: boolean }) => (
    <div className={`poa-section${wide ? " wide" : ""}`}><div className="poa-section-head"><b>{title}</b>{hint && <span className="muted">{hint}</span>}</div>{children}</div>);
  const months = (m: number) => { const x = new Date(); x.setMonth(x.getMonth() + m); return x.toISOString().slice(0, 10); };
  const nSel = sel.size + custom.filter((x) => x.trim()).length;
  return (
    <Modal title={saved ? `Черновик доверенности — ${att.full_name || ""}` : "Новая доверенность"} onClose={onClose} wide>
      <div className="poa-steps">
        {steps.map((t, i) => <button key={t} className={`poa-step ${i === step ? "cur" : ""} ${i < step ? "done" : ""}`} onClick={() => setStep(i)}><span className="n">{i + 1}</span><span>{t}</span></button>)}
      </div>
      {err.length > 0 && <div className="alert error" style={{ marginBottom: 12 }}><ul style={{ margin: 0, paddingLeft: 18 }}>{err.map((e) => <li key={e}>{e}</li>)}</ul></div>}
      <div className="poa-body">
        {step === 0 && (
          <div className="poa-cols">
            <Section title="Доверитель" hint="реквизиты и подписант — из профиля компании по умолчанию, фиксируются при оформлении" wide>
              <div className="poa-form">
                <F label="Компания группы" span={2}><select value={ent} onChange={(e) => setEnt(e.target.value)} disabled={!!saved}>{reg.entities.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}</select></F>
              </div>
            </Section>
            <Section title="Представитель" hint="сотрудник из списка или вручную">
              <div className="poa-form">
                <F label="Сотрудник" span={2}><select value={userId} onChange={(e) => pickUser(e.target.value)}><option value="">— не сотрудник, заполнить вручную —</option>{reg.employees.map((u) => <option key={u.id} value={u.id}>{u.display_name}{u.position ? ` — ${u.position}` : ""}</option>)}</select></F>
                <F label="ФИО полностью" span={2}><input value={att.full_name || ""} onChange={(e) => setAtt({ ...att, full_name: e.target.value })} placeholder="Фамилия Имя Отчество" /></F>
                <F label="Должность" span={2}><input value={att.position || ""} onChange={(e) => setAtt({ ...att, position: e.target.value })} /></F>
                <F label="Дата рождения"><input type="date" value={att.birth_date || ""} onChange={(e) => setAtt({ ...att, birth_date: e.target.value })} /></F>
                <F label="Место рождения"><input value={att.birth_place || ""} onChange={(e) => setAtt({ ...att, birth_place: e.target.value })} /></F>
                <F label="Гражданство"><input value={att.citizenship || ""} onChange={(e) => setAtt({ ...att, citizenship: e.target.value })} placeholder="Российская Федерация" /></F>
                <F label="ИНН физлица"><input value={att.inn || ""} onChange={(e) => setAtt({ ...att, inn: e.target.value })} placeholder="необязательно" /></F>
              </div>
            </Section>
            <Section title="Паспорт и контакты" hint="виден только юристу, ЗГД, ФД и администратору">
              <div className="poa-form">
                <F label="Серия"><input value={p.series || ""} onChange={(e) => setP("series", e.target.value)} placeholder="45 10" /></F>
                <F label="Номер"><input value={p.number || ""} onChange={(e) => setP("number", e.target.value)} placeholder="123456" /></F>
                <F label="Кем выдан" span={2}><input value={p.issued_by || ""} onChange={(e) => setP("issued_by", e.target.value)} /></F>
                <F label="Дата выдачи"><input type="date" value={p.issued_on || ""} onChange={(e) => setP("issued_on", e.target.value)} /></F>
                <F label="Код подразделения"><input value={p.code || ""} onChange={(e) => setP("code", e.target.value)} placeholder="770-001" /></F>
                <F label="Адрес регистрации" span={2}><input value={att.address || ""} onChange={(e) => setAtt({ ...att, address: e.target.value })} /></F>
                <F label="Телефон"><input value={att.phone || ""} onChange={(e) => setAtt({ ...att, phone: e.target.value })} placeholder="+7 …" /></F>
                <F label="E-mail"><input value={att.email || ""} onChange={(e) => setAtt({ ...att, email: e.target.value })} /></F>
              </div>
            </Section>
          </div>)}
        {step === 1 && (
          <div className="poa-cols">
            <Section title="Вид и срок действия" hint="генеральная — широкий круг полномочий; специальная — однородные действия; разовая — одно действие">
              <div className="poa-form">
                <F label="Вид доверенности" span={2}><select value={kind} onChange={(e) => setKind(e.target.value)}>{Object.entries(cat.kinds).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></F>
                <F label="Действительна до"><input type="date" value={validTo} onChange={(e) => setValidTo(e.target.value)} /></F>
                <label className="f"><span>Быстрый выбор срока</span>
                  <div className="poa-chips">{([["3 мес.", 3], ["6 мес.", 6], ["1 год", 12], ["3 года", 36]] as [string, number][]).map(([t, m]) => <button key={t} type="button" className={`poa-chip ${validTo === months(m) ? "on" : ""}`} onClick={() => setValidTo(months(m))}>{t}</button>)}</div></label>
              </div>
            </Section>
            <Section title="Прочее">
              <div className="poa-form">
                <F label="Место выдачи" span={2}><input value={place} onChange={(e) => setPlace(e.target.value)} /></F>
                <F label="Примечание — внутреннее, в текст доверенности не попадает" span={2}><textarea rows={4} value={notes} onChange={(e) => setNotes(e.target.value)} /></F>
              </div>
            </Section>
          </div>)}
        {step === 2 && (
          <>
            <p className="muted poa-note">Отметьте полномочия из каталога — формулировки с лимитом суммы подставятся из ограничений (шаг 4). Свои формулировки добавляются ниже.</p>
            <div className="poa-cols">
              {cat.powers.map((g) => {
                const all = g.items.every((i) => sel.has(i.code)), cnt = g.items.filter((i) => sel.has(i.code)).length;
                return (
                  <Section key={g.code} title={g.title} hint={cnt ? `выбрано ${cnt} из ${g.items.length}` : undefined}>
                    <button type="button" className="poa-link" onClick={() => { const n = new Set(sel); g.items.forEach((i) => (all ? n.delete(i.code) : n.add(i.code))); setSel(n); }}>{all ? "снять все" : "выбрать все"}</button>
                    <div className="poa-checks">
                      {g.items.map((i) => (
                        <label key={i.code} className="poa-check">
                          <input type="checkbox" checked={sel.has(i.code)} onChange={(e) => { const n = new Set(sel); e.target.checked ? n.add(i.code) : n.delete(i.code); setSel(n); }} />
                          <span>{i.text.replace("{{amount_clause}}", lim.amount_limit ? ` на сумму не более ${Number(lim.amount_limit).toLocaleString("ru-RU")} ${lim.currency || "RUB"}` : "")}</span>
                        </label>))}
                    </div>
                  </Section>);
              })}
              <Section title="Свои полномочия" hint="по одному в строке" wide>
                {custom.length === 0 && <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>Пока нет — добавьте, если каталога не хватает.</div>}
                {custom.map((t, i) => <div key={i} className="poa-custom"><input value={t} onChange={(e) => setCustom(custom.map((x, j) => (j === i ? e.target.value : x)))} placeholder="получать корреспонденцию в отделениях АО «Почта России»" /><button type="button" className="btn ghost" title="убрать" onClick={() => setCustom(custom.filter((_, j) => j !== i))}>×</button></div>)}
                <button type="button" className="btn ghost add" style={{ fontSize: 12, marginTop: 4 }} onClick={() => setCustom([...custom, ""])}>Полномочие</button>
              </Section>
            </div>
            <div className="muted poa-note" style={{ marginBottom: 0 }}>Выбрано полномочий: <b>{nSel}</b></div>
          </>)}
        {step === 3 && (
          <div className="poa-cols">
            <Section title="Ограничения" hint="попадают в текст доверенности">
              <div className="poa-form">
                <F label="Лимит суммы по одной сделке"><input type="number" min={0} value={lim.amount_limit ?? ""} onChange={(e) => setLim({ ...lim, amount_limit: e.target.value })} placeholder="без ограничения" /></F>
                <F label="Валюта лимита"><select value={lim.currency || "RUB"} onChange={(e) => setLim({ ...lim, currency: e.target.value })}>{["RUB", "USD", "EUR", "CNY", "KGS"].map((c) => <option key={c}>{c}</option>)}</select></F>
                <F label="Контрагенты" span={2}><input value={lim.counterparties || ""} onChange={(e) => setLim({ ...lim, counterparties: e.target.value })} placeholder="только во взаимоотношениях с …" /></F>
                <F label="Территория действия" span={2}><input value={lim.territory || ""} onChange={(e) => setLim({ ...lim, territory: e.target.value })} placeholder="Российская Федерация" /></F>
                <F label="Прочие ограничения" span={2}><textarea rows={3} value={lim.extra || ""} onChange={(e) => setLim({ ...lim, extra: e.target.value })} /></F>
                <label className="poa-check" style={{ gridColumn: "span 2" }}><input type="checkbox" checked={!!lim.substitution} onChange={(e) => setLim({ ...lim, substitution: e.target.checked })} /><span>С правом передоверия</span></label>
              </div>
            </Section>
            <Section title="Печатная форма" hint="DOCX и PDF сохраняются как копии при оформлении">
              <div className="poa-form">
                <F label="Шаблон DOCX" span={2}><select value={tpl} onChange={(e) => setTpl(e.target.value)} disabled={tpls.length === 0}><option value="">встроенная форма</option>{tpls.map((t) => <option key={t.id} value={t.id}>{t.title} (v{t.version})</option>)}</select></F>
              </div>
              {tpls.length === 0 && <p className="muted" style={{ fontSize: 12, margin: "8px 0 0" }}>Своего шаблона вида «Доверенность» в библиотеке нет — используется встроенная форма. Загрузить можно во вкладке «Шаблоны и примеры» (переменные poa.*, principal.*, attorney.*, строки powers[]).</p>}
              <div className="poa-summary">
                <div><span className="muted">Представитель</span><b>{att.full_name || "—"}</b></div>
                <div><span className="muted">Вид</span><b>{cat.kinds[kind]}</b></div>
                <div><span className="muted">Действует до</span><b>{d(validTo)}</b></div>
                <div><span className="muted">Полномочий</span><b>{nSel}</b></div>
              </div>
              {saved && <div className="row" style={{ gap: 8, marginTop: 12 }}>
                <a className="btn ghost" href={`/api/legal/poa/${saved.id}/preview`}>Проект DOCX</a>
                <a className="btn ghost" href={`/api/legal/poa/${saved.id}/preview?fmt=pdf`} target="_blank" rel="noreferrer">Проект PDF</a>
              </div>}
              {!saved && <p className="muted" style={{ fontSize: 12, margin: "10px 0 0" }}>Сохраните черновик — появятся ссылки на проект DOCX и PDF для просмотра перед оформлением.</p>}
            </Section>
          </div>)}
      </div>
      <div className="poa-footer">
        <div>{saved && <button className="btn ghost" onClick={cancel} style={{ color: "var(--bad)" }}>Аннулировать черновик</button>}</div>
        <div className="row" style={{ gap: 8 }}>
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
  const [info, setInfo] = useState("");
  const a = p.attorney, pr = p.principal, lim = p.limits;
  const run = (fn: () => Promise<Poa>) => fn().then(onChanged).catch((e) => setErr((e as Error).message));
  const hasScan = useMemo(() => p.files.some((f) => f.kind === "signed_scan"), [p.files]);
  const canRevoke = reg.can_issue && (p.status === "active" || p.status === "expired");
  const Row = ({ k, v }: { k: string; v: ReactNode }) => <div className="poa-kv"><span className="muted">{k}</span><span>{v || "—"}</span></div>;
  return (
    <Modal title={`Доверенность ${p.number || "(проект)"}`} onClose={onClose} wide>
      <div className="poa-body view">
        <div className="poa-head-line">
          <span className={`badge ${STATUS_CLASS[p.status] || ""}`}>{p.status_title}</span><span className="badge">{p.kind_title}</span>
          <span className="muted">{p.entity_name} · выдана {d(p.issued_on)} · действует до {d(p.valid_to)}{p.status === "active" && p.days_left !== null ? ` · осталось ${p.days_left} дн.` : ""}</span>
        </div>
        {p.status === "revoked" && <div className="alert error">Отозвана {d(p.revoked_at)} ({p.revoked_by_name}): {p.revoke_reason}</div>}
        {p.expiring && <div className="alert warn">Срок истекает — при необходимости оформите новую на основе этой («Продлить»).</div>}
        {err && <div className="alert error">{err}</div>}
        {info && <div className="alert ok">{info}</div>}
        <div className="poa-cols">
          <div className="poa-section">
            <div className="poa-section-head"><b>Доверитель</b></div>
            <Row k="Наименование" v={pr.name} />
            <Row k="ИНН / КПП" v={`${pr.inn || "—"}${pr.kpp ? ` / ${pr.kpp}` : ""}`} />
            <Row k="ОГРН" v={pr.ogrn} />
            <Row k="Адрес" v={pr.address} />
            <Row k="Подписант" v={`${pr.signatory_position || ""} ${pr.signatory || ""}`.trim()} />
            <Row k="Основание" v={pr.signatory_basis || "Устава"} />
            <Row k="Место выдачи" v={p.place} />
          </div>
          <div className="poa-section">
            <div className="poa-section-head"><b>Представитель</b>{!reg.sensitive_visible && <span className="muted">паспортные данные скрыты</span>}</div>
            <Row k="ФИО" v={a.full_name} />
            <Row k="Должность" v={a.position} />
            {reg.sensitive_visible && <>
              <Row k="Дата рождения" v={a.birth_date ? d(a.birth_date) : ""} />
              <Row k="Паспорт" v={a.passport?.series || a.passport?.number ? `${a.passport?.series || ""} ${a.passport?.number || ""}${a.passport?.issued_by ? `, выдан ${a.passport.issued_by}` : ""}${a.passport?.issued_on ? ` ${d(a.passport.issued_on)}` : ""}${a.passport?.code ? `, код ${a.passport.code}` : ""}` : ""} />
              <Row k="Адрес" v={a.address} />
            </>}
            <Row k="Телефон" v={a.phone} />
            <Row k="E-mail" v={a.email} />
          </div>
          <div className="poa-section">
            <div className="poa-section-head"><b>Полномочия</b><span className="muted">{p.powers_resolved.length}</span></div>
            <ol className="poa-powers">{p.powers_resolved.map((x) => <li key={x.n}>{x.text}</li>)}</ol>
          </div>
          <div className="poa-section">
            <div className="poa-section-head"><b>Ограничения</b></div>
            <Row k="Лимит суммы" v={lim.amount_limit ? `${Number(lim.amount_limit).toLocaleString("ru-RU")} ${lim.currency || "RUB"} по одной сделке` : "без ограничения"} />
            <Row k="Передоверие" v={lim.substitution ? "с правом передоверия" : "без права передоверия"} />
            <Row k="Контрагенты" v={lim.counterparties} />
            <Row k="Территория" v={lim.territory} />
            <Row k="Прочее" v={lim.extra} />
          </div>
          <div className="poa-section wide">
            <div className="poa-section-head" style={{ justifyContent: "space-between" }}>
              <span><b>Копии и сканы</b> {p.status === "active" && !hasScan && <span className="muted">· подписанный скан ещё не загружен</span>}</span>
              <span className="row" style={{ gap: 8 }}>
                {reg.sensitive_visible && p.status !== "draft" && <a className="btn ghost" href={`/api/legal/poa/${p.id}/preview`}>Печать DOCX</a>}
                {reg.sensitive_visible && p.status !== "draft" && <a className="btn ghost" href={`/api/legal/poa/${p.id}/preview?fmt=pdf`} target="_blank" rel="noreferrer">Печать PDF</a>}
                {reg.can_issue && <>
                  <select value={fileKind} onChange={(e) => setFileKind(e.target.value)}>{Object.entries(cat.file_kinds).filter(([k]) => k !== "issued" && k !== "draft").map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
                  <label className="btn ghost">Загрузить<input type="file" accept=".pdf,.jpg,.jpeg,.png,.tif,.tiff,.docx,.zip" style={{ display: "none" }}
                    onChange={(e) => { const f = e.target.files?.[0]; if (f) run(() => api.upload<Poa>(`/api/legal/poa/${p.id}/files`, f, { kind: fileKind })); e.target.value = ""; }} /></label></>}
              </span>
            </div>
            {p.files.length === 0 ? <div className="muted" style={{ fontSize: 13 }}>Файлов нет.</div> : (
              <table className="table poa-files"><thead><tr><th>Вид</th><th>Файл</th><th>Размер</th><th>Загрузил</th><th>Дата</th><th></th></tr></thead>
                <tbody>{p.files.map((f) => (
                  <tr key={f.id}><td>{f.kind_title}</td><td><a href={`/api/legal/poa/${p.id}/files/${f.id}`} target="_blank" rel="noreferrer">{f.name}</a></td><td>{Math.max(1, Math.round(f.size / 1024))} КБ</td><td>{f.uploaded_by_name}</td><td>{d(f.uploaded_at)}</td>
                    <td style={{ textAlign: "right" }}>{reg.can_issue && f.kind !== "issued" && <button className="btn ghost" style={{ padding: "0 8px" }} title="удалить файл" onClick={() => { if (window.confirm(`Удалить файл ${f.name}?`)) run(() => api.del<Poa>(`/api/legal/poa/${p.id}/files/${f.id}`)); }}>×</button>}</td></tr>))}
                </tbody></table>)}
          </div>
          {revoking && (
            <div className="poa-section wide" style={{ borderColor: "var(--bad)" }}>
              <div className="poa-section-head"><b>Отзыв доверенности № {p.number}</b><span className="muted">необратимо; формируется уведомление об отмене, представитель и юристы получат уведомление</span></div>
              <div className="poa-form">
                <label className="f" style={{ gridColumn: "span 2" }}><span>Причина отзыва</span><input autoFocus value={reason} onChange={(e) => setReason(e.target.value)} placeholder="прекращение трудовых отношений / изменение полномочий / …" /></label>
              </div>
              <div className="row" style={{ gap: 8, marginTop: 10 }}>
                <button className="btn" disabled={reason.trim().length < 3} onClick={() => { if (window.confirm("Отозвать доверенность?")) run(() => api.post<Poa>(`/api/legal/poa/${p.id}/revoke`, { reason })).then(() => setRevoking(false)); }}>Подтвердить отзыв</button>
                <button className="btn ghost" onClick={() => setRevoking(false)}>Отмена</button>
              </div>
            </div>)}
          <details className="poa-section wide"><summary style={{ cursor: "pointer", fontSize: 13 }}><b>История</b> <span className="muted">({p.history.length})</span>{p.notes ? <span className="muted"> · примечание: {p.notes}</span> : null}</summary>
            <ul className="poa-history">{p.history.map((h, i) => <li key={i}><span className="muted">{new Date(h.at).toLocaleString("ru-RU")}</span> · {h.by_name} · {ACTION[h.action] || h.action}{h.comment ? ` — ${h.comment}` : ""}</li>)}</ul>
          </details>
        </div>
      </div>
      {reg.can_issue && (
        <div className="poa-footer">
          <div>{canRevoke && !revoking && <button className="btn ghost" style={{ color: "var(--bad)" }} onClick={() => setRevoking(true)}>Отозвать</button>}</div>
          <div className="row" style={{ gap: 8 }}>
            {p.status === "active" && p.profile_id && <button className="btn ghost" title="подставить представителя подписантом в юридический профиль компании с основанием «доверенности № … от …»"
              onClick={() => { if (window.confirm(`Сделать ${a.full_name} подписантом компании по этой доверенности?`)) api.post(`/api/legal/poa/${p.id}/use-as-basis?profile_id=${p.profile_id}`).then(() => setInfo("Подписант в профиле компании обновлён")).catch((e) => setErr((e as Error).message)); }}>Основание для подписанта</button>}
            {(p.status === "active" || p.status === "expired" || p.status === "revoked") && <button className="btn" onClick={() => run(() => api.post<Poa>(`/api/legal/poa/${p.id}/renew`))} title="новый черновик с теми же представителем, полномочиями и ограничениями">Продлить — новая на основе</button>}
          </div>
        </div>)}
    </Modal>
  );
}

function Modal({ title, children, onClose, wide }: { title: string; children: ReactNode; onClose: () => void; wide?: boolean }) {
  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.35)", zIndex: 50, display: "flex", alignItems: "flex-start", justifyContent: "center", padding: "40px 16px", overflow: "auto" }}>
      <div className="card poa-modal" onClick={(e) => e.stopPropagation()} style={{ width: "100%", maxWidth: wide ? 1040 : 640 }}>
        <div className="poa-modal-head"><b>{title}</b><button className="btn ghost" onClick={onClose} title="закрыть">✕</button></div>
        {children}
      </div>
    </div>
  );
}
