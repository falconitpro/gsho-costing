import { useEffect, useState } from "react";
import { api, fmtDate, fmtRub } from "../api";
import type { Deal, Me } from "../types";
import LegalSaleWizard from "./LegalSaleWizard";

// Юридический блок, этап 3: вкладка «Юридические документы» в сделке — пакеты (реестр), мастер закупочного пакета,
// экран подтверждения договорного комплекта ЗИП, предпросмотр источников и проверки перед формированием.

type Bank = { id: string; title: string; currency: string; bank_name: string; account: string; swift?: string; verified: boolean };
type Prof = { id: string; title: string; is_default: boolean; summary: string; check_status: string; banks: Bank[] };
type Options = { suppliers: { id: string; name: string; short_name: string; country: string; positions: number; units: number; currency: string; amount: number }[];
  entities: { id: string; name: string; code: string; default: boolean; currency: string; profiles: Prof[] }[];
  supplier_profiles: Record<string, { profiles: Prof[]; default_language: string; contract_kind: string; zip_set: { confirmed: boolean; lines: number; selected: number } }>;
  templates: { id: string; kind: string; title: string; version: number; language: string; country: string; unknown: string[] }[]; languages: Record<string, string>; doc_titles: Record<string, string>;
  can_choose_entity: boolean; can_build: boolean; deal_version: { id: string | null }; contract_number: string; contract_date: string | null };
type ZipLine = { id: string; position_id?: string; position?: string; article: string; name: string; name_zh?: string; assembly?: string; unit: string; qty: number; unit_price: number | null; currency: string;
  interval_hours: number | null; kind: string; lead_days: number | null; included: boolean; selected: boolean; source: string; comment?: string };
type ZipSet = { id: string; lines: ZipLine[]; pricing_mode: string; confirmed_at: string | null; confirmed_by_name: string; rows: unknown[]; can_edit: boolean };
type Preview = { context: { buyer: Party; seller: Party; contract: Record<string, string>; items: Item[]; zip: unknown[]; totals: Record<string, number | string | boolean>; sources: Record<string, string>; language: string; zip_set: { confirmed: boolean } };
  checks: { errors: string[]; warnings: string[]; ok: boolean }; templates: Record<string, { id: string; title: string; version: number; language: string } | null> };
type Party = { name_ru: string; name_en: string; name_zh: string; tax_number: string; kpp: string; reg_number: string; legal_address: string; signatory: string; signatory_position: string; signatory_basis: string;
  bank: { title: string; bank_name: string; account: string; currency: string; verified: boolean; swift: string; bik: string } };
type Item = { n: number; designation: string; qty: number; unit_price: number; amount: number; currency: string; options: string; hs_code: string; production_term: string; warranty: string; local_options_excluded: string[] };
type Pkg = { id: string; kind: string; version_no: number; deal_version_no: number | null; status: string; status_title: string; files: { kind: string; title: string; file_name: string; template: { title: string; version: number } | null }[];
  checks: { warnings: string[]; documents?: { ok: boolean; files: Record<string, { ok: boolean; tables?: number; problems: string[] }> } }; approvals: { at: string; by_name: string; from: string; to: string; comment: string }[]; created_by_name: string; created_at: string; counterparty_name: string; language: string;
  totals: { amount?: number; currency?: string }; contract: { number?: string }; next_statuses: string[]; superseded_by: string | null };

const KIND_SHORT: Record<string, string> = { purchase_contract: "Договор", spec_equipment: "Прил. 1 — оборудование", spec_zip: "Прил. 2 — ЗИП", client_contract_copy: "Рабочая копия договора", disagreements_protocol: "Протокол разногласий", tech_spec_client: "Техспецификация" };
const num = (v: unknown) => (v === "" || v == null ? null : Number(String(v).replace(",", ".")));

function ZipSetEditor({ dealId, factoryId, onChanged }: { dealId: string; factoryId: string; onChanged: () => void }) {
  const base = `/api/deals/${dealId}/legal/zip-set/${factoryId}`;
  const [zs, setZs] = useState<ZipSet | null>(null);
  const [lines, setLines] = useState<ZipLine[]>([]);
  const [mode, setMode] = useState("included");
  const [dirty, setDirty] = useState(false);
  const [msg, setMsg] = useState("");
  const load = () => api.get<ZipSet>(base).then((r) => { setZs(r); setLines(r.lines); setMode(r.pricing_mode); setDirty(false); }).catch((e) => setMsg((e as Error).message));
  useEffect(() => { load(); }, [factoryId]);
  if (!zs) return <div className="muted">{msg || "Загрузка…"}</div>;
  const upd = (i: number, p: Partial<ZipLine>) => { setLines(lines.map((l, j) => (j === i ? { ...l, ...p } : l))); setDirty(true); };
  const save = () => api.put<ZipSet>(base, { lines: lines.map((l) => ({ ...l, qty: num(l.qty) ?? 0, unit_price: num(l.unit_price), interval_hours: num(l.interval_hours), lead_days: num(l.lead_days) })), pricing_mode: mode })
    .then((r) => { setZs(r); setLines(r.lines); setDirty(false); setMsg("Комплект сохранён — подтвердите его"); onChanged(); }).catch((e) => setMsg((e as Error).message));
  const confirmSet = () => api.post<ZipSet>(`${base}/confirm`).then((r) => { setZs(r); setMsg("Договорный комплект ЗИП подтверждён"); onChanged(); }).catch((e) => setMsg((e as Error).message));
  const sel = lines.filter((l) => l.selected);
  const total = sel.reduce((a, l) => a + (num(l.unit_price) || 0) * (num(l.qty) || 0), 0);
  const inp = (i: number, k: keyof ZipLine, w: number) => <input style={{ width: w }} value={String(lines[i][k] ?? "")} disabled={!zs.can_edit} onChange={(e) => upd(i, { [k]: e.target.value } as Partial<ZipLine>)} />;
  return (
    <div className="card" style={{ borderLeft: `4px solid ${zs.confirmed_at ? "var(--ok)" : "var(--warn)"}` }}>
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <div><b>Договорный комплект ЗИП</b> <span className="muted" style={{ fontSize: 12 }}>· выбрано {sel.length} из {lines.length}{total ? ` · ${fmtRub(total)} ${sel[0]?.currency || ""}` : ""}</span>
          {zs.confirmed_at ? <span className="badge ok" style={{ marginLeft: 8 }}>подтверждён {fmtDate(zs.confirmed_at)} · {zs.confirmed_by_name}</span> : <span className="badge warn" style={{ marginLeft: 8 }}>не подтверждён</span>}</div>
        {zs.can_edit && <div className="row" style={{ gap: 6 }}>
          <button className="btn ghost" style={{ padding: "3px 10px", fontSize: 12 }} onClick={() => { setLines([...lines, { id: "", article: "", name: "", unit: "шт", qty: 1, unit_price: null, currency: lines[0]?.currency || "CNY", interval_hours: null, kind: "supply", lead_days: null, included: mode === "included", selected: true, source: "manual" }]); setDirty(true); }}>Строка вручную</button>
          <button className="btn ghost" style={{ padding: "3px 10px", fontSize: 12 }} title="заново взять ЗИП из сервисного блока и RFQ (ручные строки останутся)" onClick={() => { if (window.confirm("Предзаполнить заново из сервисного ЗИП и RFQ? Выбор и правки строк из источников будут сброшены.")) api.post<ZipSet>(`${base}/reset`).then((r) => { setZs(r); setLines(r.lines); setDirty(false); onChanged(); }); }}>Из источников</button>
          <button className="btn secondary" disabled={!dirty} onClick={save}>Сохранить</button>
          <button className="btn" disabled={dirty || !!zs.confirmed_at} onClick={confirmSet}>Подтвердить комплект</button></div>}
      </div>
      <p className="muted" style={{ fontSize: 12, margin: "4px 0 6px" }}>Источник — ЗИП позиций (сервисный блок) и лист «ЗИП на ТО» из RFQ. В приложение № 2 попадают только отмеченные строки; состав хранится отдельно и расчёт себестоимости не меняет.
        <label className="row" style={{ display: "inline-flex", marginLeft: 10, gap: 4 }}>ЗИП <select value={mode} disabled={!zs.can_edit} onChange={(e) => { setMode(e.target.value); setDirty(true); }}><option value="included">включён в стоимость оборудования</option><option value="separate">оплачивается отдельно</option></select></label></p>
      {msg && <div className="muted" style={{ fontSize: 12 }}>{msg}</div>}
      <div style={{ overflowX: "auto" }}>
        <table style={{ fontSize: 12 }}>
          <thead><tr><th>В договор</th><th>Позиция</th><th>Вид</th><th>Артикул</th><th>Наименование</th><th>Ед.</th><th className="num">Кол-во</th><th className="num">Цена</th><th>Валюта</th><th className="num">Замена, м·ч</th><th>Источник</th>{zs.can_edit && <th />}</tr></thead>
          <tbody>{lines.map((l, i) => (
            <tr key={l.id || i} style={{ opacity: l.selected ? 1 : .55 }}>
              <td><input type="checkbox" checked={l.selected} disabled={!zs.can_edit} onChange={(e) => upd(i, { selected: e.target.checked })} /></td>
              <td className="muted" style={{ fontSize: 11 }}>{l.position || "—"}</td>
              <td><select value={l.kind} disabled={!zs.can_edit} onChange={(e) => upd(i, { kind: e.target.value })}><option value="supply">комплект поставки</option><option value="to">ЗИП на ТО</option></select></td>
              <td>{inp(i, "article", 100)}</td><td>{inp(i, "name", 200)}</td><td>{inp(i, "unit", 45)}</td><td>{inp(i, "qty", 60)}</td><td>{inp(i, "unit_price", 80)}</td>
              <td><select value={l.currency} disabled={!zs.can_edit} onChange={(e) => upd(i, { currency: e.target.value })}>{["CNY", "USD", "EUR", "RUB", "KGS"].map((c) => <option key={c}>{c}</option>)}</select></td>
              <td>{inp(i, "interval_hours", 70)}</td>
              <td className="muted" style={{ fontSize: 11 }}>{{ service: "сервис", rfq: "RFQ", manual: "вручную", excel: "Excel" }[l.source] || l.source}</td>
              {zs.can_edit && <td><button className="btn ghost" style={{ padding: "0 6px" }} onClick={() => { setLines(lines.filter((_, j) => j !== i)); setDirty(true); }}>×</button></td>}
            </tr>))}
            {lines.length === 0 && <tr><td colSpan={12} className="muted">Строк нет — ЗИП в позициях и RFQ не задан. Добавьте вручную или подтвердите пустой комплект (приложение № 2 будет без позиций).</td></tr>}</tbody>
        </table>
      </div>
    </div>);
}

function PartyCard({ title, p, source, bankSource }: { title: string; p: Party; source: string; bankSource: string }) {
  if (!p || !p.name_ru && !p.name_en) return <div className="card" style={{ borderLeft: "4px solid var(--bad)" }}><b>{title}</b><div className="muted">профиль не выбран</div></div>;
  return (
    <div className="card" style={{ padding: "10px 14px" }}>
      <b>{title}</b> <span className="muted" style={{ fontSize: 11 }}>· {source}</span>
      <div style={{ fontSize: 13, marginTop: 4 }}><b>{p.name_ru || p.name_en}</b>{p.name_zh && <span className="muted"> · {p.name_zh}</span>}</div>
      <div className="muted" style={{ fontSize: 12 }}>{[p.tax_number && `ИНН/рег. ${p.tax_number}${p.kpp ? "/" + p.kpp : ""}`, p.reg_number && p.reg_number !== p.tax_number && `ОГРН/рег. № ${p.reg_number}`, p.legal_address].filter(Boolean).join(" · ")}</div>
      <div className="muted" style={{ fontSize: 12 }}>{p.signatory ? `подписывает ${p.signatory}${p.signatory_position ? ", " + p.signatory_position : ""}${p.signatory_basis ? ", на основании " + p.signatory_basis : ""}` : <span style={{ color: "var(--bad)" }}>подписант не указан</span>}</div>
      <div style={{ fontSize: 12, marginTop: 4 }}>{p.bank?.account ? <>Банк: {p.bank.bank_name} · {p.bank.account} · {p.bank.currency}{p.bank.swift ? ` · SWIFT ${p.bank.swift}` : ""}{p.bank.bik ? ` · БИК ${p.bank.bik}` : ""} {p.bank.verified ? <span className="badge ok">подтверждены</span> : <span className="badge warn">не подтверждены</span>}
        <span className="muted" style={{ fontSize: 11 }}> · {bankSource}</span></> : <span style={{ color: "var(--bad)" }}>банковский профиль не выбран</span>}</div>
    </div>);
}

export default function LegalDocsTab({ deal, me, reload }: { deal: Deal; me: Me; reload: () => void }) {
  void me;
  const [opt, setOpt] = useState<Options | null>(null);
  const [pkgs, setPkgs] = useState<Pkg[]>([]);
  const [msg, setMsg] = useState("");
  const [wizard, setWizard] = useState(false);
  const [saleWizard, setSaleWizard] = useState(false);
  const [ch, setCh] = useState<{ factory_id: string; own_entity_id: string; own_profile_id: string; own_bank_id: string; cp_profile_id: string; cp_bank_id: string; language: string; templates: Record<string, string>; contract_number: string; contract_date: string; place: string; notes: string }>(
    { factory_id: "", own_entity_id: "", own_profile_id: "", own_bank_id: "", cp_profile_id: "", cp_bank_id: "", language: "", templates: {}, contract_number: "", contract_date: "", place: "" , notes: "" });
  const [preview, setPreview] = useState<Preview | null>(null);
  const [building, setBuilding] = useState(false);
  const [openPkg, setOpenPkg] = useState<string | null>(null);
  const loadOpt = () => api.get<Options>(`/api/deals/${deal.id}/legal/purchase/options`).then(setOpt).catch((e) => setMsg((e as Error).message));
  const loadPkgs = () => api.get<{ items: Pkg[] }>(`/api/deals/${deal.id}/legal/packages`).then((r) => setPkgs(r.items)).catch(() => setPkgs([]));
  useEffect(() => { loadOpt(); loadPkgs(); }, [deal.id]);
  useEffect(() => { if (opt && !ch.factory_id && opt.suppliers.length) start(opt.suppliers[0].id); }, [opt]);
  const start = (factoryId: string) => {
    if (!opt) return;
    const sp = opt.supplier_profiles[factoryId];
    const ent = opt.entities.find((e) => e.default) || opt.entities[0];
    const ownP = ent?.profiles.find((p) => p.is_default) || ent?.profiles[0];
    const cpP = sp?.profiles.find((p) => p.is_default) || sp?.profiles[0];
    const cur = opt.suppliers.find((s) => s.id === factoryId)?.currency || "CNY";
    const pickBank = (bs: Bank[] | undefined) => (bs?.find((b) => b.currency === cur && b.verified) || bs?.find((b) => b.currency === cur) || bs?.[0])?.id || "";
    setCh({ factory_id: factoryId, own_entity_id: ent?.id || "", own_profile_id: ownP?.id || "", own_bank_id: pickBank(ownP?.banks), cp_profile_id: cpP?.id || "", cp_bank_id: pickBank(cpP?.banks), language: sp?.default_language || "ru-en", templates: {},
      contract_number: opt.contract_number, contract_date: opt.contract_date ? String(opt.contract_date).slice(0, 10) : "", place: "", notes: "" });
    setPreview(null);
  };
  const body = () => ({ ...ch, own_entity_id: ch.own_entity_id || null, own_profile_id: ch.own_profile_id || null, own_bank_id: ch.own_bank_id || null, cp_profile_id: ch.cp_profile_id || null, cp_bank_id: ch.cp_bank_id || null, contract_date: ch.contract_date || null,
    templates: Object.fromEntries(Object.entries(ch.templates).filter(([, v]) => v !== "auto").map(([k, v]) => [k, v === "builtin" ? "" : v])) });
  const doPreview = () => api.post<Preview>(`/api/deals/${deal.id}/legal/purchase/preview`, body()).then(setPreview).catch((e) => setMsg((e as Error).message));
  const doBuild = async () => {
    setBuilding(true);
    try { await api.post(`/api/deals/${deal.id}/legal/purchase/build`, body()); setMsg("Пакет сформирован (черновик) — файлы в списке пакетов и в папке «08 Документы клиента и юридические»"); setWizard(false); setPreview(null); loadPkgs(); loadOpt(); reload(); }
    catch (e) { const d = (e as { detail?: unknown }).detail as { message?: string; errors?: string[] } | undefined; setMsg(d?.errors ? `${d.message}: ${d.errors.join("; ")}` : (e as Error).message); }
    finally { setBuilding(false); }
  };
  if (!opt) return <div className="muted">{msg || "Загрузка…"}</div>;
  const sup = opt.suppliers.find((s) => s.id === ch.factory_id);
  const sp = ch.factory_id ? opt.supplier_profiles[ch.factory_id] : undefined;
  const ent = opt.entities.find((e) => e.id === ch.own_entity_id);
  const ownP = ent?.profiles.find((p) => p.id === ch.own_profile_id);
  const cpP = sp?.profiles.find((p) => p.id === ch.cp_profile_id);
  const docKinds = ["purchase_contract", "spec_equipment", "spec_zip"];
  const tplFor = (doc: string) => opt.templates.filter((t) => (doc === "purchase_contract" ? t.kind === (sp?.contract_kind || "") || t.kind.startsWith("purchase_contract") : t.kind === doc));
  const active = pkgs.filter((p) => p.status !== "superseded" && p.status !== "archived");
  return (
    <>
      {msg && <div className="alert info" onClick={() => setMsg("")} style={{ cursor: "pointer" }}>{msg}</div>}
      <div className="card">
        <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
          <h2 style={{ margin: 0 }}>Юридические документы <span className="muted" style={{ fontWeight: 400, fontSize: 13 }}>· пакетов: {pkgs.length}</span></h2>
          <div className="row" style={{ gap: 6 }}>
            {opt.can_build && opt.suppliers.length > 0 && <button className={"btn " + (wizard ? "secondary" : "")} onClick={() => setWizard(!wizard)}>{wizard ? "Свернуть мастер" : "Закупка у поставщика"}</button>}
            {opt.can_build && <button className={"btn " + (saleWizard ? "secondary" : "")} onClick={() => setSaleWizard(!saleWizard)}>{saleWizard ? "Свернуть мастер" : "Договор заказчика"}</button>}
          </div>
        </div>
        {!opt.can_build && <p className="muted" style={{ fontSize: 12, margin: "4px 0 0" }}>Пакеты формируют менеджер сделки, юрист, КД или ЗГД после выигрыша сделки.</p>}
        {opt.suppliers.length === 0 && <p className="muted" style={{ fontSize: 12 }}>В позициях сделки нет поставщиков.</p>}
        {opt.suppliers.length > 1 && <p className="muted" style={{ fontSize: 12, margin: "4px 0 0" }}>В сделке {opt.suppliers.length} поставщика — на каждого формируется отдельный пакет; позиции разных поставщиков не объединяются.</p>}
      </div>

      {wizard && (
        <div className="card" style={{ borderColor: "var(--accent)" }}>
          <h3 style={{ marginTop: 0 }}>Закупочный пакет: договор + приложение № 1 (оборудование) + приложение № 2 (ЗИП)</h3>
          <div className="grid cols-3">
            <label className="f">1. Поставщик (из позиций сделки)<select value={ch.factory_id} onChange={(e) => start(e.target.value)}>{opt.suppliers.map((s) => <option key={s.id} value={s.id}>{s.short_name} · {s.positions} поз., {s.units} ед. · {fmtRub(s.amount)} {s.currency}</option>)}</select></label>
            <label className="f">2. Покупатель — компания группы{!opt.can_choose_entity && <span className="muted"> (выбор другой компании — ФД, ЗГД, КД)</span>}
              <select value={ch.own_entity_id} disabled={!opt.can_choose_entity} onChange={(e) => { const en = opt.entities.find((x) => x.id === e.target.value); const p = en?.profiles.find((x) => x.is_default) || en?.profiles[0]; setCh({ ...ch, own_entity_id: e.target.value, own_profile_id: p?.id || "", own_bank_id: p?.banks[0]?.id || "" }); setPreview(null); }}>
                {opt.entities.map((e) => <option key={e.id} value={e.id}>{e.name}{e.default ? " (по умолчанию)" : ""}</option>)}</select></label>
            <label className="f">Язык документов<select value={ch.language} onChange={(e) => { setCh({ ...ch, language: e.target.value }); setPreview(null); }}>{Object.entries(opt.languages).map(([k, t]) => <option key={k} value={k}>{t}{k === sp?.default_language ? " (по умолчанию для страны)" : ""}</option>)}</select></label>
            <label className="f">Юридический профиль покупателя<select value={ch.own_profile_id} onChange={(e) => { const p = ent?.profiles.find((x) => x.id === e.target.value); setCh({ ...ch, own_profile_id: e.target.value, own_bank_id: p?.banks[0]?.id || "" }); setPreview(null); }}>
              {(ent?.profiles || []).map((p) => <option key={p.id} value={p.id}>{p.title}{p.is_default ? " (по умолчанию)" : ""} · {p.check_status === "checked" ? "проверен" : "не проверен"}</option>)}</select></label>
            <label className="f">3. Банковский профиль покупателя *<select value={ch.own_bank_id} onChange={(e) => { setCh({ ...ch, own_bank_id: e.target.value }); setPreview(null); }}>
              <option value="">— выберите —</option>{(ownP?.banks || []).map((b) => <option key={b.id} value={b.id}>{b.title} · {b.currency} · {b.bank_name} …{b.account.slice(-4)}{b.verified ? " ✓" : " (не подтверждён)"}</option>)}</select></label>
            <label className="f">4. Юридический профиль поставщика<select value={ch.cp_profile_id} onChange={(e) => { const p = sp?.profiles.find((x) => x.id === e.target.value); setCh({ ...ch, cp_profile_id: e.target.value, cp_bank_id: p?.banks[0]?.id || "" }); setPreview(null); }}>
              {(sp?.profiles || []).map((p) => <option key={p.id} value={p.id}>{p.title} · {p.summary.slice(0, 60)}</option>)}</select></label>
            <label className="f">Банковский профиль поставщика *<select value={ch.cp_bank_id} onChange={(e) => { setCh({ ...ch, cp_bank_id: e.target.value }); setPreview(null); }}>
              <option value="">— выберите —</option>{(cpP?.banks || []).map((b) => <option key={b.id} value={b.id}>{b.title} · {b.currency} · {b.bank_name}{b.swift ? " · " + b.swift : ""}{b.verified ? " ✓" : " (не подтверждён)"}</option>)}</select></label>
            <label className="f">Номер договора<input value={ch.contract_number} placeholder="из реквизитов контракта" onChange={(e) => { setCh({ ...ch, contract_number: e.target.value }); setPreview(null); }} /></label>
            <label className="f">Дата / место<div className="row"><input type="date" value={ch.contract_date} onChange={(e) => { setCh({ ...ch, contract_date: e.target.value }); setPreview(null); }} /><input value={ch.place} placeholder="г. Москва" style={{ width: 120 }} onChange={(e) => { setCh({ ...ch, place: e.target.value }); setPreview(null); }} /></div></label>
          </div>
          <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>Банковские профили подсказаны по валюте договора ({sup?.currency}) и подтверждению — выбор виден и подтверждается вами. В документ записывается снимок реквизитов.</div>
          <div style={{ marginTop: 10 }}><b style={{ fontSize: 13 }}>5. Шаблоны</b> <span className="muted" style={{ fontSize: 12 }}>· действующий по виду и языку подставляется сам; можно выбрать другой или встроенный генератор</span>
            <div className="grid cols-3" style={{ marginTop: 4 }}>{docKinds.map((doc) => (
              <label key={doc} className="f">{opt.doc_titles[doc]}<select value={ch.templates[doc] || "auto"} onChange={(e) => { setCh({ ...ch, templates: { ...ch.templates, [doc]: e.target.value } }); setPreview(null); }}>
                <option value="auto">автоматически (действующий шаблон)</option><option value="builtin">встроенный генератор</option>
                {tplFor(doc).map((t) => <option key={t.id} value={t.id}>{t.title} v{t.version} · {t.language}{t.unknown.length ? " ⚠ неизвестные переменные" : ""}</option>)}</select></label>))}</div></div>
          <div style={{ marginTop: 10 }}>{ch.factory_id && <ZipSetEditor dealId={deal.id} factoryId={ch.factory_id} onChanged={() => { loadOpt(); setPreview(null); }} />}</div>
          <div className="row" style={{ marginTop: 8, gap: 8, flexWrap: "wrap" }}>
            <button className="btn secondary" onClick={doPreview}>6. Предпросмотр и проверка</button>
            <label className="f" style={{ flex: 1, minWidth: 200 }}><input value={ch.notes} placeholder="заметка к пакету (необязательно)" onChange={(e) => setCh({ ...ch, notes: e.target.value })} /></label>
          </div>
          {preview && (
            <div style={{ marginTop: 10 }}>
              <div className={"alert " + (preview.checks.ok ? "ok" : "error")} style={{ fontSize: 13 }}>
                <b>{preview.checks.ok ? "Проверки пройдены" : `Блокирующих ошибок: ${preview.checks.errors.length}`}</b>{preview.checks.warnings.length > 0 && ` · предупреждений: ${preview.checks.warnings.length}`}
                {preview.checks.errors.map((e, i) => <div key={i}>✖ {e}</div>)}{preview.checks.warnings.map((w, i) => <div key={i} className="muted">⚠ {w}</div>)}
              </div>
              <div className="grid cols-2">
                <PartyCard title="Покупатель" p={preview.context.buyer} source={preview.context.sources.buyer} bankSource={preview.context.sources["buyer.bank"]} />
                <PartyCard title="Продавец (поставщик)" p={preview.context.seller} source={preview.context.sources.seller} bankSource={preview.context.sources["seller.bank"]} />
              </div>
              <div className="card" style={{ padding: "10px 14px" }}>
                <b>Приложение № 1 — оборудование</b> <span className="muted" style={{ fontSize: 11 }}>· {preview.context.sources.items}</span>
                <table style={{ fontSize: 12, marginTop: 4 }}><thead><tr><th>№</th><th>Оборудование</th><th className="num">Кол-во</th><th className="num">Цена</th><th className="num">Сумма</th><th>ТН ВЭД</th><th>Срок / гарантия</th><th>Опции</th></tr></thead>
                  <tbody>{preview.context.items.map((it) => <tr key={it.n}><td>{it.n}</td><td>{it.designation}</td><td className="num">{it.qty}</td><td className="num">{fmtRub(it.unit_price)}</td><td className="num">{fmtRub(it.amount)} {it.currency}</td><td>{it.hs_code || <span style={{ color: "var(--warn)" }}>—</span>}</td>
                    <td className="muted">{it.production_term || "—"} / {it.warranty || "—"}</td><td className="muted">{it.options || "—"}{it.local_options_excluded.length > 0 && <div style={{ color: "var(--warn)" }}>опции РФ исключены: {it.local_options_excluded.join(", ")}</div>}</td></tr>)}
                    <tr className="total"><td colSpan={4}>Итого оборудование</td><td className="num">{fmtRub(preview.context.totals.equipment_amount as number)} {preview.context.totals.currency}</td><td colSpan={3} /></tr>
                    <tr><td colSpan={4} className="muted">ЗИП ({preview.context.zip.length} строк) · {preview.context.sources.zip} · {preview.context.totals.zip_included ? "включён в стоимость" : "оплачивается отдельно"}</td><td className="num">{fmtRub(preview.context.totals.zip_amount as number)}</td><td colSpan={3} /></tr>
                    <tr className="total"><td colSpan={4}>Сумма договора</td><td className="num">{fmtRub(preview.context.totals.amount as number)} {preview.context.totals.currency}</td><td colSpan={3} /></tr></tbody></table>
                <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>Условия оплаты: {preview.context.contract.payment_terms || "—"} <span style={{ fontSize: 11 }}>· {preview.context.sources["contract.payment_terms"]}</span> · базис {preview.context.contract.basis || "—"} {preview.context.contract.delivery_point} · срок производства {preview.context.contract.production_term || "—"}</div>
                <div className="muted" style={{ fontSize: 12 }}>Шаблоны: {docKinds.map((d) => `${KIND_SHORT[d]} — ${preview.templates[d] ? `${preview.templates[d]!.title} v${preview.templates[d]!.version}` : "встроенный генератор"}`).join("; ")}</div>
              </div>
              <div className="row" style={{ gap: 8 }}>
                <button className="btn" disabled={!preview.checks.ok || building} onClick={doBuild}>{building ? "Формирование…" : "7. Сформировать пакет (черновик)"}</button>
                {!preview.checks.ok && <span className="muted" style={{ fontSize: 12 }}>устраните ошибки и повторите предпросмотр</span>}
              </div>
            </div>)}
        </div>)}

      {saleWizard && <LegalSaleWizard dealId={deal.id} onBuilt={() => { setSaleWizard(false); loadPkgs(); reload(); }} onMsg={setMsg} />}

      {pkgs.length > 0 && (
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Пакеты документов</h3>
          {[...active, ...pkgs.filter((p) => !active.includes(p))].map((p) => (
            <div key={p.id} style={{ border: "1px solid var(--line)", borderLeft: `4px solid ${p.status === "signed" ? "var(--ok)" : p.status === "approved" ? "var(--info)" : p.status === "superseded" || p.status === "archived" ? "var(--line)" : "var(--warn)"}`, borderRadius: 8, padding: "8px 12px", marginBottom: 8, background: "#fff", opacity: p.status === "superseded" || p.status === "archived" ? .6 : 1 }}>
              <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8, cursor: "pointer" }} onClick={() => setOpenPkg(openPkg === p.id ? null : p.id)}>
                <div><b>{p.kind === "purchase" ? "Закупка" : "Продажа"} · {p.counterparty_name}</b> <span className="badge" style={{ marginLeft: 4 }}>v{p.version_no}</span> <span className={`badge ${p.status === "signed" || p.status === "approved" ? "ok" : p.status === "draft" || p.status === "needs_data" ? "warn" : "info"}`} style={{ marginLeft: 2 }}>{p.status_title}</span>
                  <div className="muted" style={{ fontSize: 12 }}>{p.contract.number ? `№ ${p.contract.number} · ` : ""}{p.totals.amount != null && `${fmtRub(p.totals.amount)} ${p.totals.currency} · `}версия сделки {p.deal_version_no ?? "—"} · {p.language} · {p.created_by_name} · {fmtDate(p.created_at)}</div></div>
                <div className="row" style={{ gap: 6 }} onClick={(e) => e.stopPropagation()}>
                  {p.files.map((f) => <a key={f.kind} className="btn ghost" style={{ padding: "3px 10px", fontSize: 12 }} href={`/api/legal/packages/${p.id}/files/${f.kind}`} title={f.template ? `по шаблону ${f.template.title} v${f.template.version}` : "встроенный генератор"}>{KIND_SHORT[f.kind] || f.title}</a>)}
                  <a className="btn ghost" style={{ padding: "3px 10px", fontSize: 12 }} href={`/api/legal/packages/${p.id}/zip`}>ZIP</a>
                </div>
              </div>
              {openPkg === p.id && (
                <div style={{ marginTop: 8, fontSize: 12 }}>
                  <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>{p.files.map((f) => <a key={f.kind + "pdf"} className="btn ghost" style={{ padding: "2px 8px", fontSize: 11 }} href={`/api/legal/packages/${p.id}/files/${f.kind}?fmt=pdf`} target="_blank" rel="noreferrer">PDF: {KIND_SHORT[f.kind]}</a>)}</div>
                  {p.checks.warnings.length > 0 && <div className="muted" style={{ marginTop: 4 }}>Предупреждения при формировании: {p.checks.warnings.join("; ")}</div>}
                  {p.checks.documents && <div className="muted" style={{ marginTop: 4 }}>Проверка файлов: {Object.entries(p.checks.documents.files).map(([k, r]) => `${KIND_SHORT[k] || k} — ${r.ok ? "✓" : "✖ " + r.problems.join("; ")}${r.tables ? ` (таблиц ${r.tables})` : ""}`).join(" · ")}</div>}
                  {(p.status === "approved" || p.status === "signed") && <div className="row" style={{ gap: 6, marginTop: 6, flexWrap: "wrap", alignItems: "center" }}><span className="muted">Подписанный скан:</span>
                    {p.files.filter((f) => f.kind === "signed_scan").map((f) => <a key={f.file_name} href={`/api/legal/packages/${p.id}/files/signed_scan`} target="_blank" rel="noreferrer">{f.file_name}</a>)}
                    <label className="btn ghost" style={{ padding: "2px 8px", fontSize: 11, cursor: "pointer" }}>приложить<input type="file" accept=".pdf,.jpg,.jpeg,.png,.tif,.tiff,.zip" style={{ display: "none" }}
                      onChange={(e) => { const fl = e.target.files?.[0]; if (fl) api.upload(`/api/legal/packages/${p.id}/signed-scan`, fl, {}).then(() => { loadPkgs(); reload(); }).catch((er) => setMsg((er as Error).message)); e.target.value = ""; }} /></label></div>}
                  {p.next_statuses.length > 0 && <div className="row" style={{ gap: 6, marginTop: 6, flexWrap: "wrap" }}><span className="muted">Перевести:</span>
                    {p.next_statuses.map((s) => <button key={s} className={"btn " + (s === "archived" || s === "draft" ? "ghost" : "secondary")} style={{ padding: "3px 10px", fontSize: 12 }}
                      onClick={() => { const c = s === "draft" || s === "archived" ? prompt("Комментарий / причина") : prompt("Комментарий (необязательно)") ?? null; if (c === null) return; api.post(`/api/legal/packages/${p.id}/status`, { status: s, comment: c }).then(() => { loadPkgs(); reload(); }).catch((e) => setMsg((e as Error).message)); }}>
                      {({ lawyer_review: "на проверку юристу", commercial_review: "на коммерческое согласование", approved: "утвердить", signed: "подписан", draft: "вернуть в черновик", archived: "в архив" } as Record<string, string>)[s] || s}</button>)}</div>}
                  <details style={{ marginTop: 6 }}><summary className="muted" style={{ cursor: "pointer" }}>История согласования · {p.approvals.length}</summary>
                    <table style={{ fontSize: 12 }}><tbody>{p.approvals.map((a, i) => <tr key={i}><td>{fmtDate(a.at)}</td><td>{a.by_name}</td><td>{a.from ? `${a.from} → ` : ""}{a.to}</td><td className="muted">{a.comment}</td></tr>)}</tbody></table></details>
                </div>)}
            </div>))}
        </div>)}
    </>
  );
}
