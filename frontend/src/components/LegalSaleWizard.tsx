import { useEffect, useState } from "react";
import { api, fmtDate } from "../api";

// Юридический блок, этап 4: мастер «Договор заказчика» — загрузка DOCX, рабочая копия с реквизитами БЕРГ-ХАУС (подстановки с подтверждением),
// юридический анализ (правила + ИИ), протокол разногласий и техническая спецификация.

type Bank = { id: string; title: string; currency: string; bank_name: string; account: string; verified: boolean };
type Prof = { id: string; title: string; is_default: boolean; summary: string; check_status?: string; banks?: Bank[]; selected_in_contract?: boolean };
type Options = { sources: { id: string; file_name: string; uploaded_at: string; comment: string }[]; own_entity: { id: string; name: string } | null; own_profiles: Prof[]; client_profiles: Prof[];
  templates: { id: string; kind: string; title: string; version: number; language: string; unknown: string[] }[]; doc_titles: Record<string, string>;
  ai: { provider: string; model: string; configured: boolean; title: string }; can_build: boolean; brand_company: boolean;
  frameworks?: { id: string; number: string; cp_number: string; signed_on: string | null; specs: number; remaining: number | null; currency: string }[] };
type Rep = { id: string; kind: string; where: string; old: string; new: string; confidence: string; reason: string; apply: boolean };
type Finding = { rule: string; group: string; group_title: string; severity: string; severity_title: string; clause: string; client_text: string; our_text: string; comment: string; risk: string; rationale: string; source: string };
type Preview = { context: { seller: Party; buyer: Party; items: { n: number; designation: string; qty: number; options: string; production_term: string; warranty: string }[]; params: unknown[]; options: unknown[]; totals: { amount: number | null; currency: string }; sources: Record<string, string>; brand_company: boolean; variant: { name: string } };
  templates: Record<string, { title: string; version: number } | null>; source?: { id: string; file_name: string }; replacements?: Rep[]; replacement_stats?: { high: number; medium: number; seller_blocks: number; tables: number };
  clauses?: { no: string; section: string; text: string }[]; findings?: Finding[]; ai?: { used: boolean; warning: string | null; count: number }; checks: { errors: string[]; warnings: string[]; ok: boolean } };
type Party = { name_ru: string; tax_number: string; kpp: string; legal_address: string; signatory: string; signatory_position: string; bank: { bank_name: string; account: string; verified: boolean } };

const SEV_COLOR: Record<string, string> = { critical: "var(--bad)", major: "var(--warn)", minor: "var(--muted)" };
const REP_KIND: Record<string, string> = { inn: "ИНН", kpp: "КПП", ogrn: "ОГРН", okpo: "ОКПО", legal_address: "юр. адрес", postal_address: "почтовый адрес", account: "р/с", corr: "к/с", bik: "БИК", bank: "банк", phone: "телефон", email: "e-mail", inline: "реквизиты в строке", name: "наименование", signatory: "подписант", preamble: "преамбула" };

export default function LegalSaleWizard({ dealId, onBuilt, onMsg }: { dealId: string; onBuilt: () => void; onMsg: (m: string) => void }) {
  const [opt, setOpt] = useState<Options | null>(null);
  const [src, setSrc] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [ownProfile, setOwnProfile] = useState("");
  const [ownBank, setOwnBank] = useState("");
  const [clientProfile, setClientProfile] = useState("");
  const [tpl, setTpl] = useState<Record<string, string>>({});
  const [useAi, setUseAi] = useState(true);
  const [fw, setFw] = useState("");      // спецификация к рамочному договору с клиентом (D-169)
  const [notes, setNotes] = useState("");
  const [pv, setPv] = useState<Preview | null>(null);
  const [approved, setApproved] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState("");
  const [showClauses, setShowClauses] = useState(false);
  const load = () => api.get<Options>(`/api/deals/${dealId}/legal/sale/options`).then((o) => {
    setOpt(o);
    if (!src && o.sources[0]) setSrc(o.sources[0].id);
    const p = o.own_profiles.find((x) => x.is_default) || o.own_profiles[0];
    if (!ownProfile && p) { setOwnProfile(p.id); const b = p.banks?.find((x) => x.currency === "RUB" && x.verified) || p.banks?.find((x) => x.currency === "RUB") || p.banks?.[0]; setOwnBank(b?.id || ""); }
    const cp = o.client_profiles.find((x) => x.selected_in_contract) || o.client_profiles.find((x) => x.is_default) || o.client_profiles[0];
    if (!clientProfile && cp) setClientProfile(cp.id);
  }).catch((e) => onMsg((e as Error).message));
  useEffect(() => { load(); }, [dealId]);
  if (!opt) return <div className="muted">Загрузка…</div>;
  const body = () => ({ framework_contract_id: fw || null, source_attachment_id: src, own_profile_id: ownProfile || null, own_bank_id: ownBank || null, client_profile_id: clientProfile || null, use_ai: useAi, notes,
    templates: Object.fromEntries(Object.entries(tpl).filter(([, v]) => v !== "auto").map(([k, v]) => [k, v === "builtin" ? "" : v])), approved_replacements: [...approved] });
  async function upload() {
    if (!file) return;
    setBusy("upload");
    try { const r = await api.upload<{ id: string }>(`/api/deals/${dealId}/legal/sale/upload`, file, { comment: "договор заказчика" }); setFile(null); await load(); setSrc(r.id); setPv(null); onMsg("Договор заказчика загружен — исходник сохранён в папке 08 без изменений"); }
    catch (e) { onMsg((e as Error).message); } finally { setBusy(""); }
  }
  async function preview() {
    setBusy("preview");
    try { const r = await api.post<Preview>(`/api/deals/${dealId}/legal/sale/preview`, body()); setPv(r); setApproved(new Set((r.replacements || []).filter((x) => x.confidence === "high").map((x) => x.id))); }
    catch (e) { onMsg((e as Error).message); } finally { setBusy(""); }
  }
  async function build() {
    setBusy("build");
    try { await api.post(`/api/deals/${dealId}/legal/sale/build`, body()); onMsg("Клиентский пакет сформирован (черновик): рабочая копия договора, протокол разногласий, техспецификация — проверить юристу"); setPv(null); onBuilt(); }
    catch (e) { const d = (e as { detail?: unknown }).detail as { message?: string; errors?: string[] } | undefined; onMsg(d?.errors ? `${d.message}: ${d.errors.join("; ")}` : (e as Error).message); }
    finally { setBusy(""); }
  }
  const prof = opt.own_profiles.find((p) => p.id === ownProfile);
  const toggle = (id: string) => { const s = new Set(approved); if (s.has(id)) s.delete(id); else s.add(id); setApproved(s); };
  const sevCount = (sev: string) => (pv?.findings || []).filter((f) => f.severity === sev).length;
  return (
    <div className="card" style={{ borderColor: "var(--accent)" }}>
      <h3 style={{ marginTop: 0 }}>Договор заказчика: рабочая копия + протокол разногласий + техническая спецификация</h3>
      <div className="grid cols-3">
        <label className="f">1. Договор заказчика (DOCX)
          <select value={src} onChange={(e) => { setSrc(e.target.value); setPv(null); }}><option value="">— выберите загруженный —</option>{opt.sources.map((s) => <option key={s.id} value={s.id}>{s.file_name} · {fmtDate(s.uploaded_at)}</option>)}</select>
          <div className="row" style={{ marginTop: 4, gap: 6 }}><input type="file" accept=".docx" onChange={(e) => setFile(e.target.files?.[0] || null)} style={{ fontSize: 12 }} /><button className="btn ghost" style={{ padding: "3px 10px", fontSize: 12 }} disabled={!file || busy === "upload"} onClick={upload}>Загрузить</button></div></label>
        <label className="f">2. Продавец — {opt.own_entity?.name || "компания РФ"}
          <select value={ownProfile} onChange={(e) => { setOwnProfile(e.target.value); const p = opt.own_profiles.find((x) => x.id === e.target.value); setOwnBank(p?.banks?.[0]?.id || ""); setPv(null); }}>{opt.own_profiles.map((p) => <option key={p.id} value={p.id}>{p.title}{p.is_default ? " (по умолчанию)" : ""} · {p.check_status === "checked" ? "проверен" : "не проверен"}</option>)}</select>
          <select style={{ marginTop: 4 }} value={ownBank} onChange={(e) => { setOwnBank(e.target.value); setPv(null); }}><option value="">— банковский профиль (RUB) —</option>{(prof?.banks || []).map((b) => <option key={b.id} value={b.id}>{b.title} · {b.currency} · {b.bank_name} …{b.account.slice(-4)}{b.verified ? " ✓" : " (не подтверждён)"}</option>)}</select></label>
        <label className="f">Покупатель — профиль клиента
          <select value={clientProfile} onChange={(e) => { setClientProfile(e.target.value); setPv(null); }}>{opt.client_profiles.map((p) => <option key={p.id} value={p.id}>{p.title}{p.selected_in_contract ? " (в реквизитах контракта)" : p.is_default ? " (по умолчанию)" : ""}</option>)}</select>
          <span className="muted" style={{ fontSize: 11 }}>{opt.brand_company ? "Режим бренда компании: завод в техспецификации не раскрывается." : "Техспецификация — по выигранному предложению, без цен."}</span></label>
      </div>
      <div className="grid cols-3" style={{ marginTop: 6 }}>
        {(["disagreements_protocol", "tech_spec_client"] as const).map((k) => (
          <label key={k} className="f">Шаблон: {opt.doc_titles[k]}<select value={tpl[k] || "auto"} onChange={(e) => { setTpl({ ...tpl, [k]: e.target.value }); setPv(null); }}>
            <option value="auto">автоматически (действующий)</option><option value="builtin">встроенный генератор</option>{opt.templates.filter((t) => t.kind === k).map((t) => <option key={t.id} value={t.id}>{t.title} v{t.version}{t.unknown.length ? " ⚠" : ""}</option>)}</select></label>))}
        <label className="f">Оформление<select value={fw} onChange={(e) => setFw(e.target.value)}>
          <option value="">новый договор на сделку</option>
          {(opt.frameworks || []).map((f) => <option key={f.id} value={f.id}>спецификация к рамочному {f.cp_number || f.number}{f.signed_on ? ` от ${String(f.signed_on).slice(0, 10)}` : " (проект)"} · спецификаций {f.specs}{f.remaining != null ? ` · остаток ${f.remaining.toLocaleString("ru-RU")} ${f.currency}` : ""}</option>)}</select>
          {!(opt.frameworks || []).length && <span className="muted" style={{ fontSize: 11 }}>рамочных договоров с клиентом нет — отметьте договор «рамочный» в его карточке</span>}</label>
        <label className="f">Юридический анализ
          <label className="row" style={{ fontSize: 13, gap: 6 }}><input type="checkbox" checked={useAi} disabled={!opt.ai.configured} onChange={(e) => { setUseAi(e.target.checked); setPv(null); }} />
            правила системы{opt.ai.configured ? ` + ${opt.ai.title}${opt.ai.model ? ` (${opt.ai.model})` : ""}` : ""}</label>
          <span className="muted" style={{ fontSize: 11 }}>{opt.ai.configured ? "ИИ дополняет замечания правил; все замечания — черновик, проверить юристу" : `${opt.ai.title || "ИИ"} не настроен на сервере — только правила`}</span></label>
      </div>
      <div className="row" style={{ marginTop: 8, gap: 8 }}>
        <button className="btn secondary" disabled={!src || busy === "preview"} onClick={preview}>{busy === "preview" ? "Анализ…" : "3. Разобрать договор и проверить"}</button>
        <input value={notes} placeholder="заметка к пакету" onChange={(e) => setNotes(e.target.value)} style={{ flex: 1 }} />
      </div>
      {pv && (
        <div style={{ marginTop: 10 }}>
          <div className={"alert " + (pv.checks.ok ? "ok" : "error")} style={{ fontSize: 13 }}>
            <b>{pv.checks.ok ? "Проверки пройдены" : `Блокирующих ошибок: ${pv.checks.errors.length}`}</b>{pv.checks.warnings.length > 0 && ` · предупреждений: ${pv.checks.warnings.length}`}
            {pv.checks.errors.map((e, i) => <div key={i}>✖ {e}</div>)}{pv.checks.warnings.map((w, i) => <div key={i} className="muted">⚠ {w}</div>)}
          </div>
          {pv.replacements && (
            <div className="card" style={{ padding: "10px 14px" }}>
              <b>4. Реквизиты БЕРГ-ХАУС в рабочей копии</b> <span className="muted" style={{ fontSize: 12 }}>· найдено мест: {pv.replacements.length} (уверенно {pv.replacement_stats?.high}, требуют подтверждения {pv.replacement_stats?.medium}) · блоков Поставщика: {pv.replacement_stats?.seller_blocks}, таблиц: {pv.replacement_stats?.tables}</span>
              <p className="muted" style={{ fontSize: 12, margin: "4px 0 6px" }}>Исходный договор не изменяется. В рабочую копию подставляются только отмеченные места; остальной текст остаётся как у заказчика. Источник: {pv.context.sources.seller}, {pv.context.sources["seller.bank"]}.</p>
              {pv.replacements.length === 0 && <div className="muted">Места для реквизитов не найдены — в договоре нет блока «Поставщик» с ИНН/КПП/счётом. Рабочая копия будет без подстановок.</div>}
              <table style={{ fontSize: 12 }}><thead><tr><th>В копию</th><th>Что</th><th>Было</th><th>Станет</th><th>Уверенность</th></tr></thead>
                <tbody>{pv.replacements.map((r) => (
                  <tr key={r.id} style={{ opacity: approved.has(r.id) ? 1 : .6 }}>
                    <td><input type="checkbox" checked={approved.has(r.id)} onChange={() => toggle(r.id)} /></td><td>{REP_KIND[r.kind] || r.kind}</td>
                    <td className="muted" style={{ maxWidth: 260, wordBreak: "break-word" }}>{r.old}</td><td style={{ maxWidth: 300, wordBreak: "break-word" }}>{r.new}</td>
                    <td><span className={`badge ${r.confidence === "high" ? "ok" : "warn"}`} title={r.reason}>{r.confidence === "high" ? "уверенно" : "подтвердите"}</span></td></tr>))}</tbody></table>
            </div>)}
          {pv.findings && (
            <div className="card" style={{ padding: "10px 14px" }}>
              <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
                <b>5. Юридический анализ — замечания для протокола разногласий</b>
                <span style={{ fontSize: 12 }}><span style={{ color: "var(--bad)", fontWeight: 600 }}>критических {sevCount("critical")}</span> · <span style={{ color: "var(--warn)", fontWeight: 600 }}>существенных {sevCount("major")}</span> · технических {sevCount("minor")}
                  {pv.ai?.used && <span className="badge info" style={{ marginLeft: 6 }}>+ ИИ: {pv.ai.count}</span>}{pv.ai?.warning && <span className="badge warn" style={{ marginLeft: 6 }} title={pv.ai.warning}>ИИ недоступен</span>}</span>
              </div>
              <p className="muted" style={{ fontSize: 12, margin: "4px 0 6px" }}>Пунктов распознано: {pv.clauses?.length || 0} <a href="#" onClick={(e) => { e.preventDefault(); setShowClauses(!showClauses); }}>{showClauses ? "скрыть" : "показать"}</a>. В протокол попадают пункт, редакция заказчика, редакция БЕРГ-ХАУС и комментарий; риск и обоснование — только здесь.</p>
              {showClauses && <div style={{ maxHeight: 200, overflow: "auto", fontSize: 11, border: "1px solid var(--line)", borderRadius: 6, padding: 6, marginBottom: 6 }}>{pv.clauses?.map((c) => <div key={c.no}><b>{c.no}</b> <span className="muted">{c.section}</span> {c.text}</div>)}</div>}
              {pv.findings.length === 0 ? <div className="muted">Замечаний нет — протокол будет пустым.</div> : (
                <table style={{ fontSize: 12 }}><thead><tr><th>Пункт</th><th>Критичность</th><th>Редакция заказчика</th><th>Редакция БЕРГ-ХАУС</th><th>Риск (служебно)</th></tr></thead>
                  <tbody>{pv.findings.map((f, i) => (
                    <tr key={i}><td style={{ whiteSpace: "nowrap" }}><b>{f.clause}</b><div className="muted" style={{ fontSize: 10 }}>{f.group_title}</div></td>
                      <td><span style={{ color: SEV_COLOR[f.severity], fontWeight: 600 }}>{f.severity_title}</span>{f.source.startsWith("ai") && <div className="badge info" style={{ fontSize: 10 }}>ИИ</div>}</td>
                      <td className="muted" style={{ maxWidth: 260 }}>{f.client_text.slice(0, 300)}</td><td style={{ maxWidth: 320 }}>{f.our_text}</td><td className="muted" style={{ maxWidth: 200, fontSize: 11 }}>{f.risk}</td></tr>))}</tbody></table>)}
            </div>)}
          <div className="card" style={{ padding: "10px 14px" }}>
            <b>6. Техническая спецификация</b> <span className="muted" style={{ fontSize: 11 }}>· {pv.context.sources.items} · без цен завода, себестоимости и маржи</span>
            <table style={{ fontSize: 12, marginTop: 4 }}><thead><tr><th>№</th><th>Оборудование</th><th className="num">Кол-во</th><th>Опции</th><th>Срок / гарантия</th></tr></thead>
              <tbody>{pv.context.items.map((it) => <tr key={it.n}><td>{it.n}</td><td>{it.designation}</td><td className="num">{it.qty}</td><td className="muted">{it.options || "—"}</td><td className="muted">{it.production_term || "—"} / {it.warranty || "—"}</td></tr>)}</tbody></table>
            <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>Характеристик: {pv.context.params.length} · доукомплектование в РФ: {pv.context.options.length} · шаблоны: протокол — {pv.templates.disagreements_protocol ? `${pv.templates.disagreements_protocol.title} v${pv.templates.disagreements_protocol.version}` : "встроенный"}, техспецификация — {pv.templates.tech_spec_client ? `${pv.templates.tech_spec_client.title} v${pv.templates.tech_spec_client.version}` : "встроенный"}</div>
          </div>
          <div className="row" style={{ gap: 8 }}>
            <button className="btn" disabled={!pv.checks.ok || !opt.can_build || busy === "build"} onClick={build}>{busy === "build" ? "Формирование…" : "7. Сформировать клиентский пакет (черновик)"}</button>
            {!opt.can_build && <span className="muted" style={{ fontSize: 12 }}>формируют менеджер сделки, юрист, КД или ЗГД после выигрыша</span>}
          </div>
        </div>)}
    </div>
  );
}
