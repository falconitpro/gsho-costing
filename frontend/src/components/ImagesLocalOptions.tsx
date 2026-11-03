import { useEffect, useState } from "react";
import { api, fmtRub } from "../api";
import { can, type LocalOption, type Me, type Position, type PositionImage } from "../types";

/** Задача 10: изображения и чертежи позиции. Загружают менеджер/КД/ГД; «В КП» и порядок — КД/ГД. */
export function ImagesBlock({ dealId, position, me, onChanged }: { dealId: string; position: Position; me: Me; onChanged: () => void }) {
  const base = `/api/deals/${dealId}/positions/${position.id}/images`;
  const [images, setImages] = useState<PositionImage[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [meta, setMeta] = useState({ kind: "photo", source: "own", caption: "", has_factory_logo: false });
  const [err, setErr] = useState("");
  const canUpload = can(me, "images", 2), canSelect = can(me, "kp_images_select", 2);
  const load = () => api.get<PositionImage[]>(base).then(setImages).catch(() => setImages([]));
  useEffect(() => { load(); }, [position.id]);
  async function upload() {
    if (!file) return;
    try { await api.upload(base, file, { kind: meta.kind, source: meta.source, caption: meta.caption, has_factory_logo: String(meta.has_factory_logo) }); setFile(null); setMeta({ ...meta, caption: "" }); setErr(""); load(); onChanged(); }
    catch (e) { setErr((e as Error).message); }
  }
  const patch = (img: PositionImage, body: Partial<PositionImage>) => api.patch(`${base}/${img.id}`, body).then(() => { load(); onChanged(); }).catch((e) => setErr(e.message));
  return (
    <div>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <b>Изображения и чертежи</b>
        <span className="muted">в КП: {images.filter((i) => i.in_kp).length} из {images.length}{!canSelect && " · выбор для КП делает КД/ГД"}</span>
      </div>
      {err && <div className="alert error">{err}</div>}
      <div className="row" style={{ marginTop: 6, flexWrap: "wrap" }}>
        {images.map((im) => (
          <div key={im.id} style={{ width: 200, border: "1px solid var(--line)", borderRadius: 6, padding: 6, background: im.in_kp ? "var(--accent-soft)" : "#fff" }}>
            {im.is_image ? <a href={im.url} target="_blank" rel="noreferrer"><img src={im.url} alt={im.caption} style={{ width: "100%", height: 110, objectFit: "cover", borderRadius: 4 }} /></a>
              : <a href={im.url} target="_blank" rel="noreferrer" className="badge">{im.file_name}</a>}
            <div style={{ fontSize: 12, marginTop: 4 }}>{im.caption || <span className="muted">без подписи</span>}</div>
            <div className="muted" style={{ fontSize: 11 }}>{im.kind} · {im.source}{im.has_factory_logo && " · логотип завода"}{im.send_to_factory && " · отправлено заводу"}</div>
            {canSelect && <label className="row" style={{ fontSize: 12 }}><input type="checkbox" checked={im.in_kp} onChange={(e) => patch(im, { in_kp: e.target.checked })} /> в КП
              {im.in_kp && <input type="number" value={im.kp_order} style={{ width: 50, padding: 2 }} onChange={(e) => patch(im, { kp_order: Number(e.target.value) })} title="порядок" />}</label>}
            {canUpload && <div className="row" style={{ fontSize: 12 }}><label><input type="checkbox" checked={im.has_factory_logo} onChange={(e) => patch(im, { has_factory_logo: e.target.checked })} /> логотип завода</label>
              <button className="btn ghost" style={{ padding: "1px 6px" }} onClick={() => api.del(`${base}/${im.id}`).then(load)}>удалить</button></div>}
          </div>
        ))}
        {images.length === 0 && <span className="muted">Файлов нет</span>}
      </div>
      {canUpload && (
        <div className="row" style={{ marginTop: 8 }}>
          <input type="file" accept=".jpg,.jpeg,.png,.gif,.webp,.pdf,.dwg,.dxf,.svg,.bmp,.tif,.tiff" capture="environment" onChange={(e) => setFile(e.target.files?.[0] || null)} />
          <select value={meta.kind} onChange={(e) => setMeta({ ...meta, kind: e.target.value })}><option value="photo">фото</option><option value="render">рендер</option><option value="drawing">чертёж</option><option value="scheme">схема</option></select>
          <select value={meta.source} onChange={(e) => setMeta({ ...meta, source: e.target.value })}><option value="own">свой</option><option value="client">от клиента</option><option value="factory">от завода</option></select>
          <input placeholder="подпись" value={meta.caption} onChange={(e) => setMeta({ ...meta, caption: e.target.value })} />
          <button className="btn secondary" onClick={upload} disabled={!file}>Загрузить</button>
        </div>
      )}
    </div>
  );
}

const EMPTY: Omit<LocalOption, "id" | "position_id" | "status"> = { name: "", qty: 1, supplier: "", cost: 0, currency: "RUB", supplier_lead_days: 30, needs_install: true, install_stage: "during_commissioning", installer: "service", install_days: 0, install_cost_rub: 0, comment: "", tech_param_id: null };

/** Задача 6: опции, реализуемые в России — не идут заводу, статья «Доукомплектование в РФ», задачи на закупку и установку. */
export function LocalOptionsBlock({ dealId, position, me, onChanged }: { dealId: string; position: Position; me: Me; onChanged: () => void }) {
  const base = `/api/deals/${dealId}/positions/${position.id}/local-options`;
  const [form, setForm] = useState({ ...EMPTY });
  const [edit, setEdit] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const canEdit = can(me, "local_options", 2);
  const canApprove = me.roles.some((r) => ["cd", "gd", "purchasing"].includes(r.code));
  const options = position.local_options || [];
  const paramRows = (position.tech_param_rows || []).filter((r) => r.local_ru || !options.some((o) => o.tech_param_id === r.id));
  async function save() {
    try {
      if (edit) await api.patch(`${base}/${edit}`, form); else await api.post(base, form);
      setForm({ ...EMPTY }); setEdit(null); setErr(""); onChanged();
    } catch (e) { setErr((e as Error).message); }
  }
  return (
    <div>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <b>Опции, реализуемые в России</b>
        <span className="muted">не попадают в запрос заводу · статья «Доукомплектование в РФ» · задачи: КП поставщиков (менеджер), установка (сервис), утверждение стоимости (КД); на стадии контракта — «заказать» и «установить»</span>
      </div>
      {err && <div className="alert error">{err}</div>}
      {options.length > 0 && (
        <table style={{ marginTop: 6 }}>
          <thead><tr><th>Опция</th><th className="num">Кол-во</th><th>Поставщик</th><th className="num">Стоимость</th><th className="num">Срок поставщика, дн</th><th>Установка</th><th className="num">Монтаж, дн / RUB</th><th>Статус</th>{canEdit && <th />}</tr></thead>
          <tbody>{options.map((o) => (
            <tr key={o.id}>
              <td>{o.name}{o.comment && <div className="muted" style={{ fontSize: 11 }}>{o.comment}</div>}</td><td className="num">{o.qty}</td><td>{o.supplier || "—"}</td>
              <td className="num">{o.cost ? `${fmtRub(o.cost)} ${o.currency}` : <span className="badge warn">нет КП</span>}</td><td className="num">{o.supplier_lead_days}</td>
              <td>{o.needs_install ? (o.install_stage === "before_handover" ? "до передачи клиенту (+ к сроку поставки)" : "на месте во время ПНР") + ` · ${o.installer === "service" ? "сервис" : "подрядчик"}` : "не требуется"}</td>
              <td className="num">{o.install_days} / {fmtRub(o.install_cost_rub)}</td>
              <td><span className={"badge " + (o.status === "approved" || o.status === "installed" ? "ok" : o.status === "estimate" ? "warn" : "")}>{{ estimate: "оценка", approved: "утверждена", ordered: "заказана", received: "получена", installed: "установлена" }[o.status] || o.status}</span></td>
              {canEdit && <td className="row">
                <button className="btn ghost" style={{ padding: "2px 6px" }} onClick={() => { setEdit(o.id); setForm({ ...o }); }}>изменить</button>
                {canApprove && o.status === "estimate" && o.cost > 0 && <button className="btn ghost" style={{ padding: "2px 6px" }} onClick={() => api.patch(`${base}/${o.id}`, { status: "approved" }).then(onChanged)}>утвердить</button>}
                <button className="btn ghost" style={{ padding: "2px 6px" }} onClick={() => api.del(`${base}/${o.id}`).then(onChanged)}>×</button></td>}
            </tr>))}</tbody>
        </table>
      )}
      {canEdit && (
        <div className="grid cols-3" style={{ marginTop: 8 }}>
          <label className="f">Наименование<input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
          <label className="f">Из таблицы технических характеристик (строка станет «в РФ»)
            <select value={form.tech_param_id || ""} onChange={(e) => { const r = paramRows.find((x) => x.id === e.target.value); setForm({ ...form, tech_param_id: e.target.value || null, name: form.name || r?.name_ru || "" }); }}>
              <option value="">—</option>{paramRows.map((r) => <option key={r.id} value={r.id}>{r.no}. {r.name_ru}</option>)}</select></label>
          <label className="f">Количество<input type="number" value={form.qty} onChange={(e) => setForm({ ...form, qty: Number(e.target.value) })} /></label>
          <label className="f">Поставщик<input value={form.supplier} onChange={(e) => setForm({ ...form, supplier: e.target.value })} /></label>
          <label className="f">Стоимость за ед. / валюта<div className="row"><input type="number" value={form.cost} onChange={(e) => setForm({ ...form, cost: Number(e.target.value) })} />
            <select value={form.currency} onChange={(e) => setForm({ ...form, currency: e.target.value })}><option>RUB</option><option>CNY</option><option>USD</option><option>EUR</option></select></div></label>
          <label className="f">Срок поставки поставщика, дн<input type="number" value={form.supplier_lead_days} onChange={(e) => setForm({ ...form, supplier_lead_days: Number(e.target.value) })} /></label>
          <label className="f">Установка<div className="row"><input type="checkbox" checked={form.needs_install} onChange={(e) => setForm({ ...form, needs_install: e.target.checked })} />
            <select value={form.install_stage} disabled={!form.needs_install} onChange={(e) => setForm({ ...form, install_stage: e.target.value })}>
              <option value="during_commissioning">на месте во время ПНР (срок не меняется)</option><option value="before_handover">до передачи клиенту (удлиняет срок поставки)</option></select></div></label>
          <label className="f">Кто устанавливает / дней / стоимость монтажа, RUB<div className="row">
            <select value={form.installer} onChange={(e) => setForm({ ...form, installer: e.target.value })}><option value="service">сервис</option><option value="contractor">подрядчик</option></select>
            <input type="number" value={form.install_days} style={{ width: 70 }} onChange={(e) => setForm({ ...form, install_days: Number(e.target.value) })} />
            <input type="number" value={form.install_cost_rub} style={{ width: 110 }} onChange={(e) => setForm({ ...form, install_cost_rub: Number(e.target.value) })} /></div></label>
          <label className="f">Комментарий<input value={form.comment} onChange={(e) => setForm({ ...form, comment: e.target.value })} /></label>
          <div className="row" style={{ alignItems: "end" }}><button className="btn secondary" onClick={save} disabled={!form.name}>{edit ? "Сохранить" : "+ Добавить опцию РФ"}</button>{edit && <button className="btn ghost" onClick={() => { setEdit(null); setForm({ ...EMPTY }); }}>отмена</button>}</div>
        </div>
      )}
      {options.length === 0 && !canEdit && <div className="muted">Нет опций, реализуемых в России</div>}
    </div>
  );
}

/** Задача 9: ответы заводов — сравнение «параметр × завод», отправка на отбор, отбор КД/ГД. */
export function OffersBlock({ dealId, position, me, onChanged }: { dealId: string; position: Position; me: Me; onChanged: () => void }) {
  type Cmp = { offers: { offer_id: string; factory: string; model: string; unit_price?: number; currency: string; production_term: Record<string, unknown>; status: string; answered: boolean; images: number; selected_deal_id: string | null }[];
    rows: { no: number; name_ru: string; unit: string; requirement: string; required: boolean; cells: { value: string; status: string; comment: string }[] }[] };
  const [cmp, setCmp] = useState<Cmp | null>(null);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [msg, setMsg] = useState("");
  const canSelect = can(me, "factory_offers", 2);
  const isMgr = me.roles.some((r) => r.code === "manager" || r.code === "gd");
  const load = () => api.get<Cmp>(`/api/deals/${dealId}/offers/compare?position_id=${position.id}`).then(setCmp).catch(() => setCmp(null));
  useEffect(() => { load(); }, [position.id]);
  if (!cmp || cmp.offers.length === 0) return <div className="muted">Запросов заводам по этой позиции ещё нет — отправьте запрос из таблицы технических характеристик (можно нескольким заводам).</div>;
  const color: Record<string, string> = { match: "#e6f5ec", mismatch: "#fde7e7", empty: "#fff6d6", unknown: "" };
  async function select() {
    try {
      const r = await api.post<{ created_deals: { number: string }[] }>(`/api/deals/${dealId}/offers/select`, { offer_ids: [...sel], copy_calcs: true });
      setMsg("Отобрано. " + (r.created_deals.length ? `Созданы связанные заявки: ${r.created_deals.map((d) => d.number).join(", ")}` : "")); onChanged(); load();
    } catch (e) { setMsg((e as Error).message); }
  }
  return (
    <div>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <b>Ответы заводов — сравнение «параметр × завод»</b>
        <div className="row">
          {isMgr && <button className="btn ghost" onClick={() => api.post(`/api/deals/${dealId}/offers/submit-compare`).then(() => setMsg("Сравнение отправлено КД на отбор заводов"))}>Отправить КД на отбор</button>}
          {canSelect && <button className="btn" disabled={sel.size === 0} onClick={select}>Отобрать для КП ({sel.size})</button>}
        </div>
      </div>
      {msg && <div className="alert info">{msg}</div>}
      <div className="muted" style={{ fontSize: 11 }}>Первый отобранный ответ остаётся в этой заявке, остальные становятся отдельными заявками (дублирование) и отдельными КП. Неотобранные остаются в истории цен завода.</div>
      <div style={{ overflowX: "auto", marginTop: 6 }}>
        <table>
          <thead><tr><th>№</th><th>Параметр</th><th>Требование</th>{cmp.offers.map((o) => (
            <th key={o.offer_id}>{canSelect && o.status !== "selected" && <input type="checkbox" checked={sel.has(o.offer_id)} onChange={(e) => { const s = new Set(sel); e.target.checked ? s.add(o.offer_id) : s.delete(o.offer_id); setSel(s); }} />} {o.factory}
              <div style={{ fontWeight: 400, fontSize: 11 }}>{o.model || "—"}{o.unit_price != null && <> · {fmtRub(o.unit_price)} {o.currency}</>}{o.production_term?.value != null && <> · {String(o.production_term.value)} {String(o.production_term.unit || "")}</>}
                <div><span className={"badge " + (o.status === "selected" ? "ok" : o.answered ? "" : "warn")}>{o.status === "selected" ? "отобран" : o.status === "rejected" ? "не выбран" : o.answered ? "ответ получен" : "ожидание"}</span>{o.images > 0 && ` · ${o.images} изобр.`}</div>
                {can(me, "images", 2) && o.status !== "selected" && (
                  <label style={{ cursor: "pointer", textDecoration: "underline" }} title="фото и чертежи, приложенные заводом к ответу">+ фото/чертежи завода
                    <input type="file" multiple hidden accept=".jpg,.jpeg,.png,.gif,.webp,.pdf,.dwg,.dxf,.svg" onChange={async (e) => {
                      const files: File[] = Array.from((e.target as HTMLInputElement).files || []);
                      try { for (const f of files) await api.upload(`/api/deals/${dealId}/offers/${o.offer_id}/images`, f); setMsg(`${o.factory}: загружено файлов ${files.length}`); load(); onChanged(); }
                      catch (er) { setMsg((er as Error).message); }
                    }} /></label>)}</div></th>))}</tr></thead>
          <tbody>{cmp.rows.map((r) => (
            <tr key={r.no + r.name_ru}><td>{r.no}</td><td>{r.name_ru}{r.required && <span style={{ color: "var(--accent)" }}> *</span>} <span className="muted">{r.unit}</span></td><td className="muted">{r.requirement}</td>
              {r.cells.map((c, j) => <td key={j} style={{ background: color[c.status] }} title={c.comment}>{c.value || "—"}</td>)}</tr>))}</tbody>
        </table>
      </div>
    </div>
  );
}

/** Связанные заявки по одному запросу клиента (задача 9). */
export function RelatedDeals({ dealId, me }: { dealId: string; me: Me }) {
  const [rows, setRows] = useState<{ id: string; number: string; status: string; outcome: string | null; models: string[]; price_rub?: number; current: boolean }[]>([]);
  useEffect(() => { api.get<typeof rows>(`/api/deals/${dealId}/related`).then(setRows).catch(() => setRows([])); }, [dealId]);
  if (rows.length < 2) return null;
  return (
    <div className="card">
      <b>Связанные заявки по этому запросу клиента</b> <span className="muted">— при выигрыше одной остальные закрываются автоматически</span>
      <table style={{ marginTop: 6 }}><tbody>{rows.map((r) => (
        <tr key={r.id} style={{ fontWeight: r.current ? 700 : 400 }}><td><a href={`/deals/${r.id}`}>{r.number}</a></td><td>{r.models.join(", ")}</td><td><span className="badge">{r.status}</span> {r.outcome}</td>
          {can(me, "price") && <td className="num">{r.price_rub != null ? fmtRub(r.price_rub) : "—"}</td>}</tr>))}</tbody></table>
    </div>
  );
}

