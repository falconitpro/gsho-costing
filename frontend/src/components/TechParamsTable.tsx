import { Fragment, useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { Position, TechParamRow } from "../types";

type Preview = { sheets: { name: string; rows: unknown[][]; total_rows: number; mapping: { header_row: number; first_row: number; columns: Record<string, number>; source?: string } }[];
  columns: { key: string; title: string }[] };
type Factory = { id: string; name: string };

const COLS: { key: keyof TechParamRow; title: string; w?: number }[] = [
  { key: "no", title: "№", w: 50 }, { key: "name_ru", title: "Параметр" }, { key: "unit", title: "Ед.", w: 70 },
  { key: "customer_requirement_ru", title: "Требования заказчика" }, { key: "value_ru", title: "Значение" }, { key: "comment_ru", title: "Комментарий" },
];

/** Таблица теххарактеристик позиции (задача 4): ручной ввод, импорт файла с предпросмотром, запрос заводу (задача 9), отметки «обязательно»/«в России». */
export default function TechParamsTable({ dealId, position, canEdit, onChanged }: { dealId: string; position: Position; canEdit: boolean; onChanged: () => void }) {
  const base = `/api/deals/${dealId}/positions/${position.id}/tech-params`;
  const [rows, setRows] = useState<TechParamRow[]>([]);
  const [missing, setMissing] = useState<string[]>([]);
  const [dirty, setDirty] = useState(false);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const [wizard, setWizard] = useState<"requirements" | "factory_answer" | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [sheet, setSheet] = useState(0);
  const [mapping, setMapping] = useState<{ header_row: number; first_row: number; last_row: string; columns: Record<string, string> }>({ header_row: 0, first_row: 1, last_row: "", columns: {} });
  const [selected, setSelected] = useState<Set<number> | null>(null);
  const [offerId, setOfferId] = useState("");
  const [offers, setOffers] = useState<{ id: string; factory: string; factory_id: string; status: string }[]>([]);
  const [factories, setFactories] = useState<Factory[]>([]);
  const [reqFactories, setReqFactories] = useState<string[]>([position.factory_id]);
  const [reqImages, setReqImages] = useState<string[]>([]);
  const [sending, setSending] = useState(false);
  const [clientChecked, setClientChecked] = useState(false);
  const [answerOffer, setAnswerOffer] = useState<string>("");    // после импорта ответа завода — предложить загрузить его фото/чертежи
  const [offerFiles, setOfferFiles] = useState<FileList | null>(null);
  const [closed, setClosed] = useState<Record<string, boolean>>({});   // свёрнутые разделы

  const load = () => api.get<{ rows: TechParamRow[]; missing_required: string[] }>(base).then((r) => { setRows(r.rows); setMissing(r.missing_required); setDirty(false); }).catch((e) => setErr(e.message));
  useEffect(() => { load(); api.get<Factory[]>("/api/refs/factories").then(setFactories); api.get<typeof offers>(`/api/deals/${dealId}/offers`).then(setOffers).catch(() => setOffers([])); }, [position.id]);

  const upd = (i: number, patch: Partial<TechParamRow>) => { setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r))); setDirty(true); };
  const addRow = () => { setRows((rs) => [...rs, { no: (rs[rs.length - 1]?.no || 0) + 1, code: "", name_ru: "", name_zh: "", unit: "", customer_requirement_ru: "", value_ru: "", value_zh: "", comment_ru: "", required: false, local_ru: false, section: rs[rs.length - 1]?.section || "Дополнительные параметры" }]); setDirty(true); };
  const delRow = (i: number) => { setRows((rs) => rs.filter((_, j) => j !== i)); setDirty(true); };
  async function save() {
    try { await api.put(base, rows); setMsg("Таблица сохранена"); setErr(""); await load(); onChanged(); } catch (e) { setErr((e as Error).message); }
  }
  async function doPreview() {
    if (!file) return;
    try {
      const p = await api.upload<Preview>(`${base}/import/preview`, file, { target: wizard || "requirements", offer_id: offerId });
      setPreview(p); setSheet(0);
      const m = p.sheets[0].mapping;
      setMapping({ header_row: m.header_row, first_row: m.first_row, last_row: "", columns: Object.fromEntries(Object.entries(m.columns).map(([k, v]) => [k, String(v)])) });
      setSelected(null); setErr("");
    } catch (e) { setErr((e as Error).message); }
  }
  async function doApply() {
    if (!file || !preview) return;
    const mp = { sheet: preview.sheets[sheet].name, header_row: mapping.header_row, first_row: mapping.first_row, last_row: mapping.last_row ? Number(mapping.last_row) : undefined,
      columns: Object.fromEntries(Object.entries(mapping.columns).filter(([, v]) => v !== "").map(([k, v]) => [k, Number(v)])) };
    try {
      const r = await api.upload<{ matched: number; added: number; unmatched: { row: number; name: string }[]; conflicts: unknown[]; offer_id?: string }>(`${base}/import/apply`, file,
        { mapping: JSON.stringify(mp), target: wizard || "requirements", selected_rows: selected ? [...selected].join(",") : "", offer_id: offerId });
      setMsg(`Загружено: сопоставлено ${r.matched}, добавлено ${r.added}` + (r.unmatched.length ? `, не сопоставлено ${r.unmatched.length}` : "") + (r.conflicts.length ? `, заменено требований ${r.conflicts.length}` : ""));
      if (wizard === "factory_answer") {   // задача 10: фото и чертежи из ответа завода — к ответу-кандидату
        const oid = r.offer_id || offers.find((o) => o.factory_id === position.factory_id)?.id || "";
        setAnswerOffer(oid);
      }
      setWizard(null); setPreview(null); setFile(null); setErr(""); await load(); onChanged();
    } catch (e) { setErr((e as Error).message); }
  }
  async function uploadOfferFiles() {
    if (!offerFiles || !answerOffer) return;
    try {
      for (const f of Array.from(offerFiles)) await api.upload(`/api/deals/${dealId}/offers/${answerOffer}/images`, f, { kind: /\.(pdf|dwg|dxf)$/i.test(f.name) ? "drawing" : "photo" });
      setMsg(`Загружено файлов завода: ${offerFiles.length} (источник «завод», отмечены как «есть логотип завода»)`); setAnswerOffer(""); setOfferFiles(null); onChanged();
    } catch (e) { setErr((e as Error).message); }
  }
  async function sendRequest() {
    setSending(true);
    try {
      const r = await api.post<{ sent: { factory: string; wecom: string | null; mail: string | null; machine_translations: unknown[] }[] }>(`${base}/request`,
        { factory_ids: reqFactories, image_ids: reqImages, via_wecom: true, via_mail: true, request_images: true, client_files_checked: clientChecked });
      setMsg("Запрос отправлен: " + r.sent.map((s) => `${s.factory} (WeCom: ${s.wecom || "—"}, почта: ${s.mail || "—"}${s.machine_translations.length ? `, машинный перевод: ${s.machine_translations.length} строк — проверьте глоссарий` : ""})`).join("; "));
      setErr(""); api.get<typeof offers>(`/api/deals/${dealId}/offers`).then(setOffers); onChanged();
    } catch (e) { setErr((e as ApiError).message); } finally { setSending(false); }
  }

  const sh = preview?.sheets[sheet];
  const filled = rows.filter((r) => r.value_ru).length;
  const images = position.images || [];
  const clientSelected = images.filter((im) => reqImages.includes(im.id) && im.source === "client");
  return (
    <div>
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 6 }}>
        <span className="muted">Теххарактеристики: заполнено значений {filled} из {rows.length}
          {missing.length > 0 && <> · <span className="badge bad">не заполнены обязательные: {missing.slice(0, 4).join(", ")}{missing.length > 4 ? " …" : ""}</span></>}
          {rows.some((r) => r.translation_status === "machine") && <> · <span className="badge warn">машинный перевод — проверьте</span></>}
          {rows.some((r) => r.req_status === "mismatch") && <> · <span className="badge" style={{ background: "#ffd9b3" }}>не соответствует требованию: {rows.filter((r) => r.req_status === "mismatch").length}</span></>}</span>
        {canEdit && (
          <div className="row">
            <button className="btn ghost" onClick={() => { setWizard("requirements"); setPreview(null); setOfferId(""); }}>Импорт требований клиента</button>
            <button className="btn ghost" onClick={() => { setWizard("factory_answer"); setPreview(null); setOfferId(""); }}>Импорт ответа завода</button>
            <a className="btn ghost" href={`${base}/request/file`}>Файл запроса RU+ZH</a>
            <a className="btn ghost" href={`/api/deals/${dealId}/positions/${position.id}/rfq/file`} title="шаблон RFQ с требованиями заказчика из таблицы; строки «в РФ» помечены">RFQ с требованиями</a>
            <button className="btn ghost" onClick={addRow}>+ строка</button>
            <button className="btn" onClick={save} disabled={!dirty}>Сохранить таблицу</button>
          </div>
        )}
      </div>
      {err && <div className="alert error">{err}</div>}
      {msg && <div className="alert info">{msg}</div>}
      <div style={{ overflowX: "auto" }}>
        <table>
          <thead><tr>{COLS.map((c) => <th key={c.key} style={{ width: c.w }}>{c.title}</th>)}<th title="обязательный параметр">Обяз.</th><th title="реализуется в России — не идёт заводу">В РФ</th>{canEdit && <th />}</tr></thead>
          <tbody>{rows.map((r, i) => {
            const sec = r.section || "Дополнительные параметры";
            const head = i === 0 || (rows[i - 1].section || "Дополнительные параметры") !== sec;
            const inSec = rows.filter((x) => (x.section || "Дополнительные параметры") === sec);
            const header = head && (
              <tr key={"s" + i} style={{ background: "#f1f1f1", cursor: "pointer" }} onClick={() => setClosed({ ...closed, [sec]: !closed[sec] })}>
                <td colSpan={COLS.length + 2 + (canEdit ? 1 : 0)} style={{ fontWeight: 700, padding: "6px 8px" }}>
                  {closed[sec] ? "▸" : "▾"} {sec} <span className="muted" style={{ fontWeight: 400 }}>· заполнено {inSec.filter((x) => x.value_ru || x.value_zh).length} из {inSec.length}
                    {inSec.some((x) => x.req_status === "mismatch") && ` · не соответствует требованию: ${inSec.filter((x) => x.req_status === "mismatch").length}`}</span></td></tr>);
            if (closed[sec]) return <Fragment key={r.id || i}>{header}</Fragment>;
            const empty = !r.value_ru && !r.value_zh;
            const bg = r.local_ru ? "#eef4ff" : empty && r.required ? "#fde7e7" : empty ? "#fff6d6" : r.translation_status === "machine" ? "#f3e8ff" : undefined;
            // значение завода не соответствует требованию заказчика — оранжевая ячейка, соответствует — зелёная рамка
            const vStyle = (cKey: string) => cKey !== "value_ru" ? {} : r.req_status === "mismatch" ? { background: "#ffd9b3", borderColor: "#e08a00", fontWeight: 600 } : r.req_status === "match" ? { borderColor: "#2e9e5b" } : {};
            const vTitle = r.req_status === "mismatch" ? `Не соответствует требованию заказчика: «${r.customer_requirement_ru}»` : r.req_status === "match" ? "Соответствует требованию заказчика" : undefined;
            return (
              <Fragment key={r.id || i}>{header}
              <tr style={{ background: bg }}>
                {COLS.map((c) => (
                  <td key={c.key} style={{ padding: 3 }}>
                    {canEdit ? <input style={{ width: "100%", padding: 4, ...vStyle(c.key) }} value={String(r[c.key] ?? "")} type={c.key === "no" ? "number" : "text"}
                      onChange={(e) => upd(i, { [c.key]: c.key === "no" ? Number(e.target.value) : e.target.value } as Partial<TechParamRow>)}
                      title={c.key === "name_ru" && r.name_zh ? r.name_zh : c.key === "value_ru" ? [vTitle, r.value_zh ? `ZH: ${r.value_zh}` : ""].filter(Boolean).join(" · ") || undefined : undefined} />
                      : <span style={{ padding: "1px 4px", borderRadius: 3, ...vStyle(c.key) }} title={c.key === "value_ru" ? vTitle : undefined}>{String(r[c.key] ?? "")}</span>}
                    {c.key === "value_ru" && r.value_zh && r.value_zh !== r.value_ru && <div className="muted" style={{ fontSize: 11 }}>{r.value_zh}{r.translation_status === "machine" && canEdit && <> · <a href="#" onClick={(e) => { e.preventDefault(); api.post(`${base}/verify/${r.id}`).then(load); }}>перевод верный</a></>}</div>}
                  </td>
                ))}
                <td style={{ textAlign: "center" }}><input type="checkbox" checked={!!r.required} disabled={!canEdit} onChange={(e) => upd(i, { required: e.target.checked })} /></td>
                <td style={{ textAlign: "center" }}><input type="checkbox" checked={!!r.local_ru} disabled={!canEdit} onChange={(e) => upd(i, { local_ru: e.target.checked })} /></td>
                {canEdit && <td><button className="btn ghost" style={{ padding: "2px 6px" }} onClick={() => delRow(i)}>×</button></td>}
              </tr></Fragment>);
          })}</tbody>
        </table>
      </div>
      <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>Цвета: красный — обязательный параметр без значения, жёлтый — не заполнено, фиолетовый — машинный перевод (проверьте), синий — реализуется в России (в запрос заводу не попадает). Значение: оранжевое — не соответствует требованию заказчика, зелёная рамка — соответствует. Разделы сворачиваются щелчком по заголовку.</div>

      {canEdit && (
        <div className="card" style={{ marginTop: 10 }}>
          <b>Запрос заводу (RU + ZH)</b> <span className="muted">— требования заказчика, пустой столбец «Значение», просьба приложить фото и чертежи. Отправка через WeCom и e-mail; по каждому заводу создаётся ответ-кандидат.</span>
          <div className="row" style={{ marginTop: 6 }}>
            <select multiple value={reqFactories} onChange={(e) => setReqFactories([...e.target.selectedOptions].map((o) => o.value))} style={{ minWidth: 260, height: 80 }}>
              {factories.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
            </select>
            {images.length > 0 && (
              <select multiple value={reqImages} onChange={(e) => setReqImages([...e.target.selectedOptions].map((o) => o.value))} style={{ minWidth: 220, height: 80 }} title="файлы клиента, которые приложить к запросу">
                {images.map((im) => <option key={im.id} value={im.id}>{im.caption || im.file_name || im.kind} ({im.source})</option>)}
              </select>
            )}
            <button className="btn" onClick={sendRequest} disabled={sending || reqFactories.length === 0 || (clientSelected.length > 0 && !clientChecked)}>Отправить запрос {reqFactories.length > 1 ? `${reqFactories.length} заводам` : "заводу"}</button>
          </div>
          {clientSelected.length > 0 && (
            <div className="alert warn" style={{ marginTop: 6 }}>
              На файлах клиента ({clientSelected.map((im) => im.caption || im.file_name).join(", ")}) могут быть его название, логотип и контакты — проверьте их перед отправкой заводу.
              <label className="row" style={{ marginTop: 4 }}><input type="checkbox" checked={clientChecked} onChange={(e) => setClientChecked(e.target.checked)} /> файлы проверены, данных клиента нет</label>
            </div>
          )}
          {offers.length > 0 && <div className="muted" style={{ marginTop: 6 }}>Запросы: {offers.map((o) => `${o.factory} — ${o.status === "answered" ? "ответ получен" : o.status === "selected" ? "отобран" : o.status === "rejected" ? "не выбран" : "ожидание"}`).join("; ")}</div>}
        </div>
      )}

      {answerOffer && (
        <div className="card" style={{ marginTop: 10 }}>
          <b>Фото и чертежи из ответа завода</b> <span className="muted">— загрузите файлы, приложенные заводом: они сохранятся к его ответу, при отборе завода попадут в позицию с отметкой «есть логотип завода».</span>
          <div className="row" style={{ marginTop: 6 }}>
            <input type="file" multiple accept=".jpg,.jpeg,.png,.gif,.webp,.pdf,.dwg,.dxf,.svg,.bmp,.tif,.tiff" onChange={(e) => setOfferFiles((e.target as HTMLInputElement).files)} />
            <button className="btn secondary" disabled={!offerFiles?.length} onClick={uploadOfferFiles}>Загрузить</button>
            <button className="btn ghost" onClick={() => setAnswerOffer("")}>не сейчас</button>
          </div>
        </div>
      )}

      {wizard && (
        <div className="card" style={{ borderColor: "var(--accent)" }}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <h2 style={{ margin: 0 }}>{wizard === "requirements" ? "Импорт требований клиента" : "Импорт ответа завода"}</h2>
            <button className="btn ghost" onClick={() => { setWizard(null); setPreview(null); }}>закрыть</button>
          </div>
          <p className="muted">Шаг 1 — файл (xlsx / csv) → шаг 2 — предпросмотр, строка заголовков, столбцы и строки для загрузки. Сопоставление строк: по № и названию, затем по названию (с синонимами глоссария), затем по №. {wizard === "factory_answer" && "Значения на китайском переводятся через глоссарий, затем переводчик (машинный перевод помечается)."}</p>
          <div className="row">
            <input type="file" accept=".xlsx,.xlsm,.csv" onChange={(e) => setFile(e.target.files?.[0] || null)} />
            {wizard === "factory_answer" && offers.length > 0 && (
              <select value={offerId} onChange={(e) => setOfferId(e.target.value)}>
                <option value="">ответ завода позиции ({factories.find((f) => f.id === position.factory_id)?.name || "—"})</option>
                {offers.map((o) => <option key={o.id} value={o.id}>ответ: {o.factory}</option>)}
              </select>
            )}
            <button className="btn secondary" onClick={doPreview} disabled={!file}>Предпросмотр</button>
          </div>
          {preview && sh && (
            <div style={{ marginTop: 10 }}>
              {sh.mapping.source === "saved" && <div className="alert info">Сопоставление столбцов подставлено по прошлой загрузке файла такого же формата от {wizard === "factory_answer" ? "этого завода" : "этого клиента"} — проверьте и при необходимости поправьте.</div>}
              <div className="row">
                {preview.sheets.length > 1 && <label className="f">Лист<select value={sheet} onChange={(e) => { const i = Number(e.target.value); setSheet(i); const m = preview.sheets[i].mapping; setMapping({ header_row: m.header_row, first_row: m.first_row, last_row: "", columns: Object.fromEntries(Object.entries(m.columns).map(([k, v]) => [k, String(v)])) }); }}>{preview.sheets.map((s, i) => <option key={s.name} value={i}>{s.name}</option>)}</select></label>}
                <label className="f">Строка заголовков<input type="number" value={mapping.header_row} onChange={(e) => setMapping({ ...mapping, header_row: Number(e.target.value) })} style={{ width: 80 }} /></label>
                <label className="f">Первая строка данных<input type="number" value={mapping.first_row} onChange={(e) => setMapping({ ...mapping, first_row: Number(e.target.value) })} style={{ width: 80 }} /></label>
                <label className="f">Последняя строка<input type="number" value={mapping.last_row} placeholder="до конца" onChange={(e) => setMapping({ ...mapping, last_row: e.target.value })} style={{ width: 90 }} /></label>
                {preview.columns.filter((c) => wizard === "factory_answer" ? !["customer_requirement_ru"].includes(c.key) : c.key !== "value_zh").map((c) => (
                  <label className="f" key={c.key}>{c.title}
                    <select value={mapping.columns[c.key] ?? ""} onChange={(e) => setMapping({ ...mapping, columns: { ...mapping.columns, [c.key]: e.target.value } })}>
                      <option value="">—</option>
                      {(sh.rows[mapping.header_row] || []).map((h, j) => <option key={j} value={j}>{j + 1}: {String(h ?? "").slice(0, 24) || "(пусто)"}</option>)}
                    </select></label>
                ))}
              </div>
              <div style={{ maxHeight: 320, overflow: "auto", marginTop: 8 }}>
                <table>
                  <thead><tr><th style={{ width: 40 }}><input type="checkbox" checked={!selected} onChange={(e) => setSelected(e.target.checked ? null : new Set())} title="все строки" /></th><th>#</th>{(sh.rows[mapping.header_row] || []).map((h, j) => <th key={j}>{String(h ?? "")}</th>)}</tr></thead>
                  <tbody>{sh.rows.map((r, i) => i >= mapping.first_row && (!mapping.last_row || i < Number(mapping.last_row)) && (
                    <tr key={i} style={{ opacity: selected && !selected.has(i) ? 0.45 : 1 }}>
                      <td><input type="checkbox" checked={!selected || selected.has(i)} onChange={(e) => { const s = new Set(selected || sh.rows.map((_, k) => k).filter((k) => k >= mapping.first_row)); e.target.checked ? s.add(i) : s.delete(i); setSelected(s); }} /></td>
                      <td className="muted">{i}</td>{r.map((c, j) => <td key={j}>{String(c ?? "")}</td>)}
                    </tr>))}</tbody>
                </table>
              </div>
              <div className="row" style={{ marginTop: 8 }}><button className="btn" onClick={doApply} disabled={!mapping.columns.name_ru && !mapping.columns.no}>Загрузить выбранные строки</button><span className="muted">строк: {sh.total_rows}</span></div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
