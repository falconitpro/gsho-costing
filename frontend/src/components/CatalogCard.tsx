import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { MeContext } from "../App";
import { lbl, IMAGE_KIND, PRICE_SOURCE } from "../labels";
import { api, designation, fmtDate, fmtRub } from "../api";
import type { TechParamRow } from "../types";
import TechParamsView from "./TechParamsView";
import { ImagesEditor, MoreEditor, TechEditor } from "./CatalogEdit";
import { ModelDocsBlock } from "./PositionExtras";

type Hist = { at: string | null; user: string; object: string; action: string; changes: { field: string; before: unknown; after: unknown }[]; comment: string; deal: string | null };
const fmtVal = (v: unknown) => (v == null || v === "" ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v));
type Img = { attachment_id?: string; caption?: string; kind?: string; source?: string; file_name?: string; url?: string };
type Exec = { id: string; name: string; brand_name: string; status: string; tech_params: TechParamRow[]; local_options: { name?: string; cost?: number; currency?: string; install_days?: number }[];
  images: Img[]; source_deal_id: string | null };
type FPrice = { on: string; unit_price: number; currency: string; basis: string; production_term: { value?: number; unit?: string }; production_days: number | null;
  factory_payments: { share_pct?: number; event?: string }[]; source: string; execution: string; deal_id: string | null; deal_number: string };
type CPrice = { on: string; deal_id: string; deal_number: string; client: string; variant: string; qty: number; unit_price_rub: number | null; total_rub: number | null;
  delivery_term: string; kp_brand: string; outcome: string | null; chosen: boolean | null; execution: string };
type Stats = { n: number; min?: number; max?: number; avg?: number };
type Analytics = {
  factory: Record<string, Stats & { first: { on: string; price: number }; last: { on: string; price: number }; change_pct: number | null }>;
  production_days: Stats & { last: number | null };
  client: { kp: number; deals: number; won: number; lost: number; open: number; win_rate_pct: number | null; price_rub: Stats; won_price_rub: Stats; lost_price_rub: Stats; qty_won: number; last: string | null };
  price_ratio?: { factory_rub: number; rate: number; currency: string; client_avg_rub: number; ratio: number | null };
};
type Card = { id: string; model: string; trademark: string; trademark_saved?: string; manually_checked?: boolean; brand_name: string; machine_type: string; status: string; factory: string;
  logistics_params: Record<string, { name?: string; value?: unknown; unit?: string }>; maintenance_regulations: { name: string; interval_hours: number; operations?: { name: string; hours: number }[] }[];
  default_hours_per_month?: number | null; acceptance_checklist?: { group: string; item: string }[]; hs?: { code: string; description?: string; duty_pct?: number | null; vat_pct?: number | null; on?: string; deal?: string } | null; executions: Exec[]; with_prices: boolean; factory_prices?: FPrice[]; client_prices?: CPrice[]; analytics?: Analytics };

const UNITS: Record<string, string> = { calendar_days: "кал. дн.", working_days: "раб. дн.", weeks: "нед.", date: "" };
const OUTCOME: Record<string, string> = { won: "выиграна", lost: "проиграна" };
const isImage = (n?: string) => /\.(png|jpe?g|gif|webp|bmp)$/i.test(n || "");
const pct = (v: number | null | undefined) => (v == null ? "—" : `${v > 0 ? "+" : ""}${String(v).replace(".", ",")} %`);

/** Мини-график цены завода во времени (по одной валюте). */
function Spark({ pts }: { pts: { on: string; v: number }[] }) {
  if (pts.length < 2) return null;
  const w = 260, h = 60, xs = pts.map((p) => new Date(p.on).getTime()), ys = pts.map((p) => p.v);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const X = (t: number) => (x1 === x0 ? w / 2 : ((t - x0) / (x1 - x0)) * (w - 10) + 5), Y = (v: number) => (y1 === y0 ? h / 2 : h - 5 - ((v - y0) / (y1 - y0)) * (h - 10));
  return (
    <svg width={w} height={h} style={{ display: "block" }} aria-label="динамика цены завода">
      <polyline fill="none" stroke="var(--accent)" strokeWidth={2} points={pts.map((p) => `${X(new Date(p.on).getTime())},${Y(p.v)}`).join(" ")} />
      {pts.map((p, i) => <circle key={i} cx={X(new Date(p.on).getTime())} cy={Y(p.v)} r={2.5} fill="var(--accent)"><title>{fmtDate(p.on)}: {fmtRub(p.v)}</title></circle>)}
    </svg>);
}

/** Карточка машины в каталоге: технические характеристики, фото и чертежи, опции и регламенты;
 *  для КД и ЗГД — цены и сроки завода, цены клиентам (список) и аналитическая справка. */
export default function CatalogCard({ modelId, execId, onExec, models = [], onChanged }: { modelId: string; execId?: string; onExec?: (id: string) => void;
  models?: { id: string; label: string }[]; onChanged?: () => void }) {
  const [mergeInto, setMergeInto] = useState("");
  const [card, setCard] = useState<Card | null>(null);
  const [tab, setTab] = useState("tech");
  const [ex, setEx] = useState(execId || "");
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const [edit, setEdit] = useState<null | { model: string; machine_type: string; trademark: string; brand_name: string; comment: string }>(null);
  const [hist, setHist] = useState<Hist[] | null>(null);
  const [editing, setEditing] = useState<"" | "tech" | "more" | "photo">("");
  const [newDeal, setNewDeal] = useState<null | { client_id: string; qty: string; site: string; kp_due: string; comment: string }>(null);
  const [clients, setClients] = useState<{ id: string; name: string; sites?: { name: string }[] }[]>([]);
  const nav = useNavigate();
  const me = MeContext.current;
  const has = (...r: string[]) => !!me && me.roles.some((x) => r.includes(x.code));
  const canEdit = !!me && (me.perms?.catalog ?? 0) >= 2;
  const reload = () => api.get<Card>(`/api/catalog/models/${modelId}/card`).then((c) => { setCard(c); setEx((cur) => cur || execId || c.executions[0]?.id || ""); }).catch((e) => setErr(e.message));
  useEffect(() => { setCard(null); setErr(""); setEdit(null); setEditing(""); setHist(null); setNewDeal(null); setMsg(""); setEx(execId || ""); reload(); }, [modelId]);
  useEffect(() => { if (tab === "history") api.get<Hist[]>(`/api/catalog/models/${modelId}/history`).then(setHist).catch(() => setHist([])); }, [tab, modelId]);
  async function saveModel() {
    if (!edit || !card) return;
    try {
      await api.patch(`/api/catalog/models/${card.id}`, { model: edit.model, machine_type: edit.machine_type, trademark: edit.trademark, brand_name: edit.brand_name, comment: edit.comment });
      setEdit(null); setMsg("Модель сохранена; изменение записано в историю"); reload(); if (tab === "history") api.get<Hist[]>(`/api/catalog/models/${modelId}/history`).then(setHist);
    } catch (e) { setMsg((e as Error).message); }
  }
  async function createDeal() {
    if (!newDeal || !e) return;
    try {
      const d = await api.post<{ id: string; number: string }>(`/api/catalog/executions/${e.id}/create-deal`, { ...newDeal, qty: Number(newDeal.qty) || 1, kp_due: newDeal.kp_due || null });
      nav(`/deals/${d.id}`);
    } catch (x) { setMsg((x as Error).message); }
  }
  useEffect(() => { if (execId) setEx(execId); }, [execId]);
  if (err) return <div className="alert error">{err}</div>;
  if (!card) return <div className="card muted">Загрузка…</div>;
  const e = card.executions.find((x) => x.id === ex) || card.executions[0];
  const images = card.executions.flatMap((x) => x.images.map((im) => ({ ...im, exec: x.name })));
  const an = card.analytics;
  const tabs: [string, string][] = [["tech", "Технические характеристики"], ["photo", `Фото и чертежи (${images.length})`], ["docs", "Документы"], ["more", "Опции, регламенты, логистика"],
    ...(card.with_prices ? [["prices", "Цены и сроки — список"], ["analytics", "Аналитическая справка"]] as [string, string][] : []), ["history", "История изменений"]];
  const pickExec = (id: string) => { setEx(id); setEditing(""); onExec?.(id); };
  const done = (m: string) => { setEditing(""); if (m) { setMsg(m); reload(); } };

  return (
    <div className="card" style={{ overflowWrap: "break-word", minWidth: 0 }}>
      <h2 style={{ marginTop: 0 }}>{card.machine_type} <b>{designation(card)}</b> {card.brand_name && <span className="badge accent" title="наименование под брендом компании">{card.brand_name}</span>}</h2>
      {canEdit && (
        <div className="row" style={{ flexWrap: "wrap", marginBottom: 6, fontSize: 13 }}>
          <button className="btn ghost" title="собственное наименование для КП под брендом компании" onClick={() => { const b = prompt("Наименование под брендом компании (пусто — убрать):", card.brand_name || ""); if (b !== null) api.patch(`/api/catalog/models/${card.id}`, { brand_name: b, comment: "наименование под брендом" }).then(() => { setMsg("Наименование под брендом сохранено"); reload(); onChanged?.(); }).catch((x) => setMsg((x as Error).message)); }}>Под своим брендом…</button>
          {models.length > 1 && <><select value={mergeInto} onChange={(x) => setMergeInto(x.target.value)} style={{ maxWidth: 260 }} title="дубль: сделки и история цен перейдут в выбранную модель">
            <option value="">объединить дубль с моделью…</option>{models.filter((m) => m.id !== card.id).map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}</select>
            {mergeInto && <button className="btn ghost" onClick={() => api.post(`/api/catalog/models/${card.id}/merge`, { into_id: mergeInto }).then(() => { setMsg("Модели объединены; дубль в архиве"); onChanged?.(); }).catch((x) => setMsg((x as Error).message))}>объединить</button>}</>}
          <button className="btn ghost" onClick={() => { if (confirm(`Перенести модель ${card.model} в архив? В подборе участвовать не будет, восстановить можно на вкладке «Архив».`)) api.patch(`/api/catalog/models/${card.id}`, { status: "archived" }).then(() => onChanged?.()).catch((x) => setMsg((x as Error).message)); }}>Модель в архив</button>
        </div>)}
      <div className="muted" style={{ marginBottom: 8, whiteSpace: "normal" }}>{card.factory} · торговая марка: {card.trademark || "—"} · исполнений: {card.executions.length}{card.manually_checked ? "" : " · данные модели пока не проверены КД / ЗГД"}</div>
      {msg && <div className="alert info">{msg}</div>}
      <div className="row" style={{ marginBottom: 8, flexWrap: "wrap" }}>
        {has("manager", "cd", "gd") && e && e.status === "approved" && <button className="btn" onClick={() => { setNewDeal({ client_id: "", qty: "1", site: "", kp_due: "", comment: "" }); if (!clients.length) api.get<typeof clients>("/api/refs/clients").then(setClients); }}>Создать заявку для клиента по этой машине</button>}
        {canEdit && <button className="btn secondary" onClick={() => setEdit({ model: card.model, machine_type: card.machine_type || "", trademark: card.trademark_saved || card.trademark || "", brand_name: card.brand_name || "", comment: "" })}>Изменить модель</button>}
      </div>
      {newDeal && e && (
        <div className="card" style={{ background: "#fafbfc" }}>
          <b>Новая заявка: {designation(card)} — {e.name}</b>
          <div className="muted" style={{ fontSize: 12, margin: "4px 0" }}>В заявку попадут технические характеристики исполнения, фото и чертежи, опции РФ, данные для логиста, код ТН ВЭД, последние цена и срок производства завода. Дальше — обычный порядок: проверить, при необходимости запросить завод, отправить на расчёт.</div>
          <div className="row" style={{ flexWrap: "wrap" }}>
            <select value={newDeal.client_id} onChange={(x) => setNewDeal({ ...newDeal, client_id: x.target.value })}><option value="">— клиент —</option>{clients.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>
            <label className="row">кол-во <input type="number" min={1} value={newDeal.qty} onChange={(x) => setNewDeal({ ...newDeal, qty: x.target.value })} style={{ width: 70 }} /></label>
            <label className="row">срок КП <input type="date" value={newDeal.kp_due} onChange={(x) => setNewDeal({ ...newDeal, kp_due: x.target.value })} /></label>
            <input placeholder="комментарий" value={newDeal.comment} onChange={(x) => setNewDeal({ ...newDeal, comment: x.target.value })} style={{ width: 220 }} />
            <button className="btn" disabled={!newDeal.client_id} onClick={createDeal}>Создать заявку</button>
            <button className="btn ghost" onClick={() => setNewDeal(null)}>Отмена</button>
          </div>
        </div>)}
      {edit && (
        <div className="card" style={{ background: "#fafbfc" }}>
          <b>Изменить модель</b> <span className="muted" style={{ fontSize: 12 }}>— после проверки КД / ЗГД данные модели больше не меняются автоматически из сделок</span>
          <div className="grid cols-2" style={{ marginTop: 6 }}>
            <label className="f">Тип машины<input value={edit.machine_type} onChange={(x) => setEdit({ ...edit, machine_type: x.target.value })} placeholder="напр. Шахтная пассажиро-грузовая машина" /></label>
            <label className="f">Модель<input value={edit.model} onChange={(x) => setEdit({ ...edit, model: x.target.value })} /></label>
            <label className="f">Торговая марка завода<input value={edit.trademark} onChange={(x) => setEdit({ ...edit, trademark: x.target.value })} placeholder="напр. SHILI" /></label>
            <label className="f">Наименование под брендом компании<input value={edit.brand_name} onChange={(x) => setEdit({ ...edit, brand_name: x.target.value })} /></label>
          </div>
          <label className="f">Что исправлено и почему (в историю изменений)<input value={edit.comment} onChange={(x) => setEdit({ ...edit, comment: x.target.value })} style={{ width: "100%" }} /></label>
          <div className="row" style={{ marginTop: 6 }}><button className="btn" disabled={!edit.model.trim()} onClick={saveModel}>Сохранить</button><button className="btn ghost" onClick={() => setEdit(null)}>Отмена</button></div>
        </div>)}
      <div className="tabs" style={{ marginBottom: 10 }}>{tabs.map(([k, t]) => <button key={k} className={tab === k ? "active" : ""} onClick={() => setTab(k)}>{t}</button>)}</div>

      {tab === "tech" && (
        <>
          {card.executions.length > 1 && <label className="row" style={{ marginBottom: 6 }}>Исполнение
            <select value={e?.id} onChange={(x) => pickExec(x.target.value)}>{card.executions.map((x) => <option key={x.id} value={x.id}>{x.name}{x.status !== "approved" ? " (на проверке)" : ""}</option>)}</select></label>}
          {canEdit && e && editing !== "tech" && <button className="btn secondary" style={{ marginBottom: 6 }} onClick={() => setEditing("tech")}>Редактировать характеристики{e.status === "pending" ? " перед подтверждением" : ""}</button>}
          {canEdit && e && editing === "tech" ? <TechEditor key={e.id} execId={e.id} name={e.name} brand={e.brand_name} rows={e.tech_params} onDone={done} />
            : e ? <TechParamsView rows={e.tech_params.map((r, i) => ({ ...r, no: r.no ?? i + 1, customer_requirement_ru: "", comment_ru: r.comment_ru || "" }))} /> : <div className="muted">Исполнений нет</div>}
        </>)}

      {tab === "photo" && canEdit && e && editing !== "photo" && e.images.length > 0 && <button className="btn secondary" style={{ marginBottom: 6 }} onClick={() => setEditing("photo")}>Подписи, порядок, убрать — {e.name}</button>}
      {tab === "photo" && canEdit && e && editing === "photo" && <ImagesEditor key={e.id} execId={e.id} images={e.images} kinds={IMAGE_KIND} onDone={done} />}
      {tab === "photo" && editing !== "photo" && (images.length === 0 ? <div className="muted">Фото и чертежей пока нет — они попадают в каталог из позиций сделок при занесении исполнения.</div> : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: 10 }}>
          {images.map((im, i) => (
            <a key={i} href={im.url} target="_blank" rel="noreferrer" style={{ border: "1px solid #eee", borderRadius: 4, padding: 6, textDecoration: "none", color: "inherit" }}>
              {isImage(im.file_name) && im.url ? <img src={im.url} alt={im.caption || im.file_name} style={{ width: "100%", height: 140, objectFit: "contain", background: "#fafafa" }} />
                : <div style={{ height: 140, display: "flex", alignItems: "center", justifyContent: "center", background: "#fafafa" }}>{(im.file_name || "файл").split(".").pop()?.toUpperCase()}</div>}
              <div style={{ fontSize: 12, marginTop: 4 }}>{im.caption || im.file_name}</div>
              <div className="muted" style={{ fontSize: 11 }}>{im.exec}{im.kind ? ` · ${lbl(IMAGE_KIND, im.kind)}` : ""}</div>
            </a>))}
        </div>))}

      {tab === "docs" && <ModelDocsBlock modelId={card.id} canUpload={!!me && !me.roles.every((r) => r.code === "observer")} canRemove={canEdit} />}
      {tab === "more" && canEdit && editing !== "more" && <button className="btn secondary" style={{ marginBottom: 6 }} onClick={() => setEditing("more")}>Редактировать опции, регламенты, ТН ВЭД и логистику</button>}
      {tab === "more" && canEdit && editing === "more" && <MoreEditor key={(e?.id || "") + card.id} modelId={card.id} execId={e?.id} options={e?.local_options || []} hs={card.hs || null}
        logistics={card.logistics_params} regs={card.maintenance_regulations} hours={card.default_hours_per_month} checklist={card.acceptance_checklist} onDone={done} />}
      {tab === "more" && editing !== "more" && (() => {
        const unit = (u?: string) => { const x = (u || "").split(" / ")[0].trim(); return x === "—" ? "" : x; };
        const fmtV = (v: unknown) => (typeof v === "number" ? fmtRub(v) : String(v ?? "—"));
        const kv = { display: "grid", gridTemplateColumns: "minmax(0, 1.4fr) minmax(0, 1fr)", gap: "4px 12px", padding: "6px 0", borderBottom: "1px solid #eee", alignItems: "baseline" } as const;
        return (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))", gap: "8px 28px", minWidth: 0 }}>
          <section style={{ minWidth: 0 }}>
            <h3>Опции РФ (доукомплектование)</h3>
            {e && e.local_options.length ? e.local_options.map((o, i) => (
              <div key={i} style={kv}><div>{o.name}</div>
                <div style={{ whiteSpace: "nowrap" }}>{o.cost != null ? <b>{fmtRub(o.cost)} {o.currency || ""}</b> : <span className="muted">цена не указана</span>}
                  {o.install_days ? <div className="muted" style={{ fontSize: 12 }}>установка {o.install_days} дн.</div> : null}</div></div>)) : <div className="muted">нет</div>}
            <h3>Регламенты ТО</h3>
            {card.maintenance_regulations.length ? card.maintenance_regulations.map((r, i) => (
              <div key={i} style={{ padding: "6px 0", borderBottom: "1px solid #eee" }}>
                <div style={{ display: "flex", gap: 12, justifyContent: "space-between", alignItems: "baseline" }}><b>{r.name}</b><span style={{ whiteSpace: "nowrap" }}>каждые <b>{fmtRub(r.interval_hours)}</b> м·ч</span></div>
                {(r.operations || []).length > 0 && <ul style={{ margin: "4px 0 0", paddingLeft: 18, fontSize: 13 }}>{(r.operations || []).map((o, j) => <li key={j}>{o.name} <span className="muted" style={{ whiteSpace: "nowrap" }}>— {o.hours} ч</span></li>)}</ul>}
              </div>)) : <div className="muted">не заданы</div>}
            {card.default_hours_per_month ? <div style={{ marginTop: 6 }}>Наработка: <b>{fmtRub(card.default_hours_per_month)}</b> м·ч в месяц</div> : null}
          </section>
          <section style={{ minWidth: 0 }}>
            <h3>Код ТН ВЭД</h3>
            {card.hs ? <div><b style={{ fontSize: 15 }}>{card.hs.code}</b>{card.hs.description && <div>{card.hs.description}</div>}
              <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>{[card.hs.duty_pct != null && `пошлина ${card.hs.duty_pct} %`, card.hs.vat_pct != null && `НДС ${card.hs.vat_pct} %`,
                `внёс логист${card.hs.deal ? ` по сделке ${card.hs.deal}` : ""}${card.hs.on ? `, ${fmtDate(card.hs.on)}` : ""}`].filter(Boolean).join(" · ")}</div></div>
              : <div className="muted">ещё не задан — появится после расчёта логистики по этой модели</div>}
            <h3>Данные для логистики</h3>
            {Object.keys(card.logistics_params).length ? Object.entries(card.logistics_params).map(([code, f]) => (
              <div key={code} style={kv}><div>{f.name || code}</div><div><b>{fmtV(f.value)}</b>{unit(f.unit) && <span className="muted" style={{ whiteSpace: "nowrap" }}> {unit(f.unit)}</span>}</div></div>))
              : <div className="muted">нет</div>}
          </section>
        </div>); })()}

      {tab === "prices" && card.with_prices && (
        <>
          <h3>Цены и сроки завода</h3>
          {card.factory_prices?.length ? <div style={{ overflowX: "auto" }}><table><thead><tr><th>Дата</th><th>Исполнение</th><th className="num">Цена</th><th>Базис</th><th>Срок производства</th><th>Оплата заводу</th><th>Источник</th><th>Сделка</th></tr></thead>
            <tbody>{card.factory_prices.map((h, i) => <tr key={i}><td>{fmtDate(h.on)}</td><td>{h.execution || "—"}</td><td className="num"><b>{fmtRub(h.unit_price)}</b> {h.currency}</td><td>{h.basis}</td>
              <td>{h.production_term?.value != null ? `${h.production_term.value} ${UNITS[h.production_term.unit || "calendar_days"] || ""}` : "—"}</td>
              <td className="muted" style={{ fontSize: 12 }}>{(h.factory_payments || []).map((p) => `${p.share_pct} %`).join(" / ") || "—"}</td>
              <td className="muted">{({ ...PRICE_SOURCE } as Record<string, string>)[h.source] || h.source}</td>
              <td>{h.deal_id ? <a href={`/deals/${h.deal_id}`}>{h.deal_number || "сделка"}</a> : "—"}</td></tr>)}</tbody></table></div> : <div className="muted">Цен завода по модели ещё нет</div>}
          <h3>Цены клиентам</h3>
          {card.client_prices?.length ? <div style={{ overflowX: "auto" }}><table><thead><tr><th>Дата КП</th><th>Сделка</th><th>Клиент</th><th>Предложение</th><th className="num">Кол-во</th><th className="num">Цена за ед., RUB</th><th className="num">Сумма, RUB</th><th>Срок</th><th>Подача</th><th>Результат</th></tr></thead>
            <tbody>{card.client_prices.map((h, i) => <tr key={i}><td>{fmtDate(h.on)}</td><td><a href={`/deals/${h.deal_id}`}>{h.deal_number}</a></td><td>{h.client}</td><td>{h.variant}</td><td className="num">{h.qty}</td>
              <td className="num"><b>{fmtRub(h.unit_price_rub)}</b></td><td className="num">{fmtRub(h.total_rub)}</td><td className="muted" style={{ fontSize: 12 }}>{h.delivery_term}</td>
              <td className="muted">{h.kp_brand === "company" ? "бренд компании" : "марка завода"}</td>
              <td>{h.outcome === "won" ? <span className="badge ok">выиграна</span> : h.outcome === "lost" ? <span className="badge bad">проиграна</span> : <span className="muted">в работе</span>}</td></tr>)}</tbody></table></div>
            : <div className="muted">КП по модели ещё не было</div>}
        </>)}

      {tab === "analytics" && an && (
        <>
          <div className="grid cols-3">
            {Object.entries(an.factory).map(([cur, f]) => (
              <div key={cur} className="card" style={{ background: "#fafafa" }}>
                <div className="muted">Цена завода, {cur}</div>
                <div style={{ fontSize: 22, fontWeight: 700 }}>{fmtRub(f.last.price)}</div>
                <div className="muted" style={{ fontSize: 12 }}>последняя, {fmtDate(f.last.on)} · изменение с {fmtDate(f.first.on)}: {pct(f.change_pct)}</div>
                <div style={{ fontSize: 12 }}>мин. {fmtRub(f.min)} · сред. {fmtRub(f.avg)} · макс. {fmtRub(f.max)} · предложений: {f.n}</div>
                <Spark pts={(card.factory_prices || []).filter((h) => h.currency === cur).map((h) => ({ on: h.on, v: h.unit_price })).reverse()} />
              </div>))}
            <div className="card" style={{ background: "#fafafa" }}>
              <div className="muted">Срок производства</div>
              <div style={{ fontSize: 22, fontWeight: 700 }}>{an.production_days.last != null ? `${Math.round(an.production_days.last)} дн.` : "—"}</div>
              <div style={{ fontSize: 12 }}>{an.production_days.n ? `мин. ${Math.round(an.production_days.min!)} · сред. ${Math.round(an.production_days.avg!)} · макс. ${Math.round(an.production_days.max!)} кал. дн.` : "нет данных"}</div>
            </div>
            <div className="card" style={{ background: "#fafafa" }}>
              <div className="muted">КП клиентам</div>
              <div style={{ fontSize: 22, fontWeight: 700 }}>{an.client.kp}</div>
              <div style={{ fontSize: 12 }}>сделок {an.client.deals}: выиграно {an.client.won}, проиграно {an.client.lost}, в работе {an.client.open}
                {an.client.win_rate_pct != null && <> · доля побед <b>{String(an.client.win_rate_pct).replace(".", ",")} %</b></>}</div>
            </div>
          </div>
          <h3>Справка</h3>
          <ul>
            {Object.entries(an.factory).map(([cur, f]) => <li key={cur}>Цена завода ({cur}): последняя {fmtRub(f.last.price)} от {fmtDate(f.last.on)}; за период с {fmtDate(f.first.on)} {f.change_pct == null ? "одно предложение" : f.change_pct > 0 ? `выросла на ${pct(f.change_pct).slice(1)}` : f.change_pct < 0 ? `снизилась на ${pct(-f.change_pct).slice(1)}` : "не менялась"}; диапазон {fmtRub(f.min)} – {fmtRub(f.max)}.</li>)}
            {an.production_days.n > 0 && <li>Срок производства: обычно {Math.round(an.production_days.avg!)} кал. дн. (от {Math.round(an.production_days.min!)} до {Math.round(an.production_days.max!)}).</li>}
            {an.client.price_rub.n > 0 && <li>Цены клиентам за единицу: средняя {fmtRub(an.client.price_rub.avg)} RUB, диапазон {fmtRub(an.client.price_rub.min)} – {fmtRub(an.client.price_rub.max)} RUB; последняя КП {fmtDate(an.client.last)}.</li>}
            {an.client.won_price_rub.n > 0 && <li>Выигранные сделки: средняя цена {fmtRub(an.client.won_price_rub.avg)} RUB, продано {an.client.qty_won} ед.{an.client.lost_price_rub.n > 0 && <> Проигранные: средняя цена {fmtRub(an.client.lost_price_rub.avg)} RUB — {an.client.lost_price_rub.avg! > an.client.won_price_rub.avg! ? "выше" : "не выше"} выигранных.</>}</li>}
            {an.price_ratio?.ratio != null && <li>Средняя цена клиенту в {String(an.price_ratio.ratio).replace(".", ",")} раза выше последней цены завода в рублях ({fmtRub(an.price_ratio.factory_rub)} RUB по курсу ЦБ {String(an.price_ratio.rate).replace(".", ",")} ₽/{an.price_ratio.currency}). Это ориентир: без логистики, пошлин и сервиса.</li>}
            {!Object.keys(an.factory).length && an.client.price_rub.n === 0 && <li className="muted">Истории цен по модели пока нет.</li>}
          </ul>
        </>)}
      {tab === "history" && (hist == null ? <div className="muted">Загрузка…</div> : hist.length === 0 ? <div className="muted">Изменений пока нет.</div> : (
        <table><thead><tr><th>Когда</th><th>Кто</th><th>Что</th><th>Действие</th><th>Было → стало</th><th>Комментарий</th></tr></thead>
          <tbody>{hist.map((h, i) => (
            <tr key={i}><td style={{ whiteSpace: "nowrap", fontSize: 12 }}>{h.at ? new Date(h.at).toLocaleString("ru-RU") : ""}</td><td>{h.user}</td><td style={{ fontSize: 12 }}>{h.object}{h.deal && <div className="muted">сделка {h.deal}</div>}</td><td>{h.action}</td>
              <td style={{ fontSize: 12 }}>{h.changes.map((c, j) => <div key={j}><b>{c.field}:</b> <span className="muted">{fmtVal(c.before)}</span> → {fmtVal(c.after)}</div>)}</td><td style={{ fontSize: 12 }}>{h.comment}</td></tr>))}</tbody></table>))}
    </div>
  );
}
