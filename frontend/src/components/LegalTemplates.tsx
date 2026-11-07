import { useEffect, useState } from "react";
import { api, fmtDate } from "../api";

// Юридический блок, этап 2: библиотека DOCX-шаблонов (договоры закупки, спецификации, протокол разногласий, техспецификация)
// и база примеров. Загрузка → найденные переменные и неизвестные каталогу → тестовое формирование на образцовых данных.

type Kind = { code: string; title: string; deal_kind: string; country: string; language: string; loops: string[] };
type Tpl = { id: string; kind: string; kind_title: string; title: string; version: number; language: string; language_title: string; country: string; deal_kind: string; is_active: boolean; is_example: boolean;
  variables: { variables?: string[]; loops?: Record<string, string[]>; unknown?: string[]; unknown_loops?: Record<string, string[]> }; notes: string; file_name: string; file_exists: boolean; uploaded_by_name: string; uploaded_at: string };
type Cat = { group: string; loop: boolean; title: string; fields: { name: string; key: string; title: string }[] };
type Resp = { kinds: Kind[]; example_kinds: Record<string, string>; languages: Record<string, string>; items: Tpl[]; catalog: Cat[]; can_edit: boolean };
type Check = { replaced: number; unresolved: string[]; loops: Record<string, number>; leftovers: string[]; ok: boolean };

export default function LegalTemplates() {
  const [data, setData] = useState<Resp | null>(null);
  const [msg, setMsg] = useState("");
  const [form, setForm] = useState({ kind: "purchase_contract_cn", title: "", language: "", country: "", notes: "" });
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [check, setCheck] = useState<Record<string, Check>>({});
  const [showCat, setShowCat] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const load = () => api.get<Resp>("/api/legal/templates").then(setData).catch((e) => setMsg((e as Error).message));
  useEffect(() => { load(); }, []);
  if (!data) return <div className="card muted">{msg || "Загрузка…"}</div>;
  const kindOf = (code: string) => data.kinds.find((k) => k.code === code);
  const isExample = (code: string) => code in data.example_kinds;
  async function upload() {
    if (!file) return;
    setBusy(true);
    try { await api.upload("/api/legal/templates", file, { kind: form.kind, title: form.title, language: form.language || kindOf(form.kind)?.language || "ru", country: form.country, notes: form.notes }); setFile(null); setForm({ ...form, title: "", notes: "" }); setMsg("Шаблон загружен"); load(); }
    catch (e) { setMsg((e as Error).message); } finally { setBusy(false); }
  }
  const runCheck = (t: Tpl) => api.get<Check>(`/api/legal/templates/${t.id}/test-check`).then((r) => setCheck({ ...check, [t.id]: r })).catch((e) => setMsg((e as Error).message));
  const groups = [...data.kinds.map((k) => ({ code: k.code, title: k.title, example: false })), ...Object.entries(data.example_kinds).map(([code, title]) => ({ code, title, example: true }))];
  return (
    <>
      {msg && <div className="alert info">{msg}</div>}
      <div className="grid" style={{ gridTemplateColumns: "minmax(0, 3fr) minmax(0, 2fr)", alignItems: "start" }}>
        <div>
          {groups.map((g) => {
            const rows = data.items.filter((t) => t.kind === g.code);
            return (
              <div key={g.code} className="card" style={{ padding: "10px 14px" }}>
                <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
                  <b>{g.title}</b>
                  <span className="muted" style={{ fontSize: 12 }}>{g.example ? `примеров: ${rows.length}` : rows.some((t) => t.is_active) ? `действует: ${rows.filter((t) => t.is_active).map((t) => `${t.language_title} v${t.version}`).join(", ")}` : <span style={{ color: "var(--warn)" }}>шаблон не загружен — пакет соберёт встроенный генератор</span>}</span>
                </div>
                {rows.map((t) => {
                  const unknown = [...(t.variables.unknown || []), ...Object.entries(t.variables.unknown_loops || {}).flatMap(([l, f]) => f.map((x) => `${l}: ${x}`))];
                  const ch = check[t.id];
                  return (
                    <div key={t.id} style={{ border: "1px solid var(--line)", borderLeft: `4px solid ${t.is_example ? "var(--line)" : t.is_active ? "var(--ok)" : "var(--line)"}`, borderRadius: 8, padding: "6px 10px", marginTop: 6, background: "#fff", opacity: t.is_active || t.is_example ? 1 : .6 }}>
                      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 6, cursor: "pointer" }} onClick={() => setOpen(open === t.id ? null : t.id)}>
                        <div><b>{t.title}</b> <span className="badge" style={{ marginLeft: 4 }}>v{t.version}</span> <span className="badge info" style={{ marginLeft: 2 }}>{t.language_title}</span>{t.country && <span className="badge" style={{ marginLeft: 2 }}>{t.country}</span>}
                          {!t.is_example && (t.is_active ? <span className="badge ok" style={{ marginLeft: 2 }}>действует</span> : <span className="badge" style={{ marginLeft: 2 }}>архив</span>)}
                          {unknown.length > 0 && <span className="badge warn" style={{ marginLeft: 2 }} title={unknown.join(", ")}>неизвестных переменных: {unknown.length}</span>}
                          <div className="muted" style={{ fontSize: 11 }}>{t.file_name} · {t.uploaded_by_name} · {fmtDate(t.uploaded_at)}{t.notes && ` · ${t.notes}`}</div></div>
                        <div className="row" style={{ gap: 6 }} onClick={(e) => e.stopPropagation()}>
                          <a className="btn ghost" style={{ padding: "3px 10px", fontSize: 12 }} href={`/api/legal/templates/${t.id}/file`}>Скачать</a>
                          {!t.is_example && <>
                            <button className="btn ghost" style={{ padding: "3px 10px", fontSize: 12 }} onClick={() => runCheck(t)}>Проверить</button>
                            <a className="btn ghost" style={{ padding: "3px 10px", fontSize: 12 }} href={`/api/legal/templates/${t.id}/test-render`} title="заполнить образцовыми данными и скачать DOCX">Тест DOCX</a>
                            <a className="btn ghost" style={{ padding: "3px 10px", fontSize: 12 }} href={`/api/legal/templates/${t.id}/test-render?fmt=pdf`} target="_blank" rel="noreferrer">Тест PDF</a>
                            {data.can_edit && !t.is_active && <button className="btn ghost" style={{ padding: "3px 10px", fontSize: 12 }} onClick={() => api.put(`/api/legal/templates/${t.id}`, { is_active: true }).then(load)}>Сделать действующим</button>}
                            {data.can_edit && t.is_active && <button className="btn ghost" style={{ padding: "3px 10px", fontSize: 12, color: "var(--muted)", borderColor: "var(--line)" }} onClick={() => api.put(`/api/legal/templates/${t.id}`, { is_active: false }).then(load)}>в архив</button>}</>}
                        </div>
                      </div>
                      {ch && <div style={{ fontSize: 12, marginTop: 4, padding: "4px 8px", borderRadius: 6, background: ch.ok ? "var(--ok-bg)" : "var(--warn-bg)" }}>
                        {ch.ok ? "✓ Все переменные подставлены" : "Есть пропуски"} · подставлено {ch.replaced}{Object.keys(ch.loops).length > 0 && ` · строки: ${Object.entries(ch.loops).map(([k, v]) => `${k} × ${v}`).join(", ")}`}
                        {ch.unresolved.length > 0 && <div>не заполнены: <b>{ch.unresolved.join(", ")}</b></div>}{ch.leftovers.length > 0 && <div>служебные теги без данных: {ch.leftovers.join(", ")}</div>}</div>}
                      {open === t.id && !t.is_example && (
                        <div style={{ fontSize: 12, marginTop: 6 }}>
                          <div><span className="muted">Переменные:</span> {(t.variables.variables || []).map((v) => <code key={v} style={{ marginRight: 6, color: (t.variables.unknown || []).includes(v) ? "var(--bad)" : undefined }}>{`{{${v}}}`}</code>)}</div>
                          {Object.entries(t.variables.loops || {}).map(([l, f]) => <div key={l}><span className="muted">Строки {`{{#${l}}}`}:</span> {f.map((x) => <code key={x} style={{ marginRight: 6, color: (t.variables.unknown_loops?.[l] || []).includes(x) ? "var(--bad)" : undefined }}>{`{{${x}}}`}</code>)}</div>)}
                          {unknown.length > 0 && <div style={{ color: "var(--bad)", marginTop: 4 }}>Неизвестные каталогу имена не будут заполнены — исправьте в шаблоне (список справа) и загрузите новую версию.</div>}
                        </div>)}
                    </div>);
                })}
              </div>);
          })}
        </div>
        <div>
          {data.can_edit && (
            <div className="card">
              <b>Загрузить шаблон или пример</b>
              <p className="muted" style={{ fontSize: 12, margin: "4px 0 8px" }}>Шаблон — DOCX с плейсхолдерами <code>{"{{buyer.name_ru}}"}</code>; повторяемая строка таблицы помечается <code>{"{{#items}}"}</code>. Вёрстка, таблицы, шрифты и колонтитулы сохраняются. Новая версия того же вида и языка становится действующей.</p>
              <label className="f">Вид<select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value, language: "" })}>
                <optgroup label="Шаблоны">{data.kinds.map((k) => <option key={k.code} value={k.code}>{k.title}</option>)}</optgroup>
                <optgroup label="База примеров">{Object.entries(data.example_kinds).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</optgroup></select></label>
              <div className="grid cols-2" style={{ marginTop: 6 }}>
                <label className="f">Язык<select value={form.language || kindOf(form.kind)?.language || "ru"} onChange={(e) => setForm({ ...form, language: e.target.value })}>{Object.entries(data.languages).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select></label>
                <label className="f">Страна<input value={form.country} placeholder={kindOf(form.kind)?.country || "—"} onChange={(e) => setForm({ ...form, country: e.target.value.toUpperCase() })} /></label>
              </div>
              <label className="f" style={{ marginTop: 6 }}>Название<input value={form.title} placeholder={groups.find((g) => g.code === form.kind)?.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></label>
              <label className="f" style={{ marginTop: 6 }}>Заметка<input value={form.notes} placeholder="что изменилось в этой версии" onChange={(e) => setForm({ ...form, notes: e.target.value })} /></label>
              <div className="row" style={{ marginTop: 8, gap: 8, flexWrap: "wrap" }}>
                <input type="file" accept={isExample(form.kind) ? ".docx,.doc,.pdf" : ".docx"} onChange={(e) => setFile(e.target.files?.[0] || null)} />
                <button className="btn" disabled={!file || busy} onClick={upload}>{busy ? "Загрузка…" : "Загрузить"}</button>
              </div>
            </div>)}
          <div className="card">
            <div className="row" style={{ justifyContent: "space-between" }}><b>Каталог переменных</b><button className="btn ghost" style={{ padding: "3px 10px", fontSize: 12 }} onClick={() => setShowCat(!showCat)}>{showCat ? "свернуть" : "показать"}</button></div>
            <p className="muted" style={{ fontSize: 12, margin: "4px 0 0" }}>В закупке покупатель (buyer) — компания группы, продавец (seller) — поставщик; в продаже клиенту продавец — компания РФ, покупатель — заказчик.</p>
            {showCat && data.catalog.map((g) => (
              <div key={g.group} style={{ marginTop: 8 }}>
                <div style={{ fontSize: 12, fontWeight: 700 }}>{g.title}</div>
                <table style={{ fontSize: 11 }}><tbody>{g.fields.map((f) => <tr key={f.name}><td><code>{g.loop ? `{{${f.key}}}` : `{{${f.name}}}`}</code></td><td className="muted">{f.title}</td></tr>)}</tbody></table>
              </div>))}
          </div>
        </div>
      </div>
    </>
  );
}
