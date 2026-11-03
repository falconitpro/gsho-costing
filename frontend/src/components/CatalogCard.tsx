import { useEffect, useState } from "react";
import { api, designation, fmtDate, fmtRub } from "../api";
import type { TechParamRow } from "../types";
import TechParamsView from "./TechParamsView";

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
type Card = { id: string; model: string; trademark: string; brand_name: string; machine_type: string; status: string; factory: string;
  logistics_params: Record<string, { name?: string; value?: unknown; unit?: string }>; maintenance_regulations: { name: string; interval_hours: number; operations?: { name: string; hours: number }[] }[];
  default_hours_per_month?: number | null; hs?: { code: string; description?: string; duty_pct?: number | null; vat_pct?: number | null; on?: string; deal?: string } | null; executions: Exec[]; with_prices: boolean; factory_prices?: FPrice[]; client_prices?: CPrice[]; analytics?: Analytics };

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
export default function CatalogCard({ modelId, execId, onExec }: { modelId: string; execId?: string; onExec?: (id: string) => void }) {
  const [card, setCard] = useState<Card | null>(null);
  const [tab, setTab] = useState("tech");
  const [ex, setEx] = useState(execId || "");
  const [err, setErr] = useState("");
  useEffect(() => { setCard(null); setErr(""); api.get<Card>(`/api/catalog/models/${modelId}/card`).then((c) => { setCard(c); setEx(execId || c.executions[0]?.id || ""); }).catch((e) => setErr(e.message)); }, [modelId]);
  useEffect(() => { if (execId) setEx(execId); }, [execId]);
  if (err) return <div className="alert error">{err}</div>;
  if (!card) return <div className="card muted">Загрузка…</div>;
  const e = card.executions.find((x) => x.id === ex) || card.executions[0];
  const images = card.executions.flatMap((x) => x.images.map((im) => ({ ...im, exec: x.name })));
  const an = card.analytics;
  const tabs: [string, string][] = [["tech", "Технические характеристики"], ["photo", `Фото и чертежи (${images.length})`], ["more", "Опции, регламенты, логистика"],
    ...(card.with_prices ? [["prices", "Цены и сроки — список"], ["analytics", "Аналитическая справка"]] as [string, string][] : [])];
  const pickExec = (id: string) => { setEx(id); onExec?.(id); };

  return (
    <div className="card">
      <h2 style={{ marginTop: 0 }}>{card.machine_type} <b>{designation(card)}</b> {card.brand_name && <span className="badge accent">{card.brand_name}</span>}</h2>
      <div className="muted" style={{ marginBottom: 8 }}>{card.factory} · исполнений: {card.executions.length}</div>
      <div className="tabs" style={{ marginBottom: 10 }}>{tabs.map(([k, t]) => <button key={k} className={tab === k ? "active" : ""} onClick={() => setTab(k)}>{t}</button>)}</div>

      {tab === "tech" && (
        <>
          {card.executions.length > 1 && <label className="row" style={{ marginBottom: 6 }}>Исполнение
            <select value={e?.id} onChange={(x) => pickExec(x.target.value)}>{card.executions.map((x) => <option key={x.id} value={x.id}>{x.name}{x.status !== "approved" ? " (на проверке)" : ""}</option>)}</select></label>}
          {e ? <TechParamsView rows={e.tech_params.map((r, i) => ({ ...r, no: r.no ?? i + 1, customer_requirement_ru: "", comment_ru: r.comment_ru || "" }))} /> : <div className="muted">Исполнений нет</div>}
        </>)}

      {tab === "photo" && (images.length === 0 ? <div className="muted">Фото и чертежей пока нет — они попадают в каталог из позиций сделок при занесении исполнения.</div> : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: 10 }}>
          {images.map((im, i) => (
            <a key={i} href={im.url} target="_blank" rel="noreferrer" style={{ border: "1px solid #eee", borderRadius: 4, padding: 6, textDecoration: "none", color: "inherit" }}>
              {isImage(im.file_name) && im.url ? <img src={im.url} alt={im.caption || im.file_name} style={{ width: "100%", height: 140, objectFit: "contain", background: "#fafafa" }} />
                : <div style={{ height: 140, display: "flex", alignItems: "center", justifyContent: "center", background: "#fafafa" }}>{(im.file_name || "файл").split(".").pop()?.toUpperCase()}</div>}
              <div style={{ fontSize: 12, marginTop: 4 }}>{im.caption || im.file_name}</div>
              <div className="muted" style={{ fontSize: 11 }}>{im.exec}{im.kind ? ` · ${im.kind}` : ""}</div>
            </a>))}
        </div>))}

      {tab === "more" && (
        <div className="grid cols-2">
          <div><h3>Опции РФ (доукомплектование)</h3>
            {e && e.local_options.length ? <ul>{e.local_options.map((o, i) => <li key={i}>{o.name}{o.cost != null && ` — ${fmtRub(o.cost)} ${o.currency || ""}`}{o.install_days ? `, установка ${o.install_days} дн.` : ""}</li>)}</ul> : <div className="muted">нет</div>}
            <h3>Регламенты ТО</h3>
            {card.maintenance_regulations.length ? <table><tbody>{card.maintenance_regulations.map((r, i) => <tr key={i}><td>{r.name}</td><td className="num">каждые {r.interval_hours} м·ч</td>
              <td className="muted" style={{ fontSize: 12 }}>{(r.operations || []).map((o) => `${o.name} (${o.hours} ч)`).join("; ")}</td></tr>)}</tbody></table> : <div className="muted">не заданы</div>}
            {card.default_hours_per_month && <div className="muted">Наработка: {card.default_hours_per_month} м·ч в месяц</div>}
          </div>
          <div><h3>Код ТН ВЭД</h3>
            {card.hs ? <div><b>{card.hs.code}</b>{card.hs.description && <> — {card.hs.description}</>}
              <div className="muted" style={{ fontSize: 12 }}>{card.hs.duty_pct != null && `пошлина ${card.hs.duty_pct} % · `}{card.hs.vat_pct != null && `НДС ${card.hs.vat_pct} % · `}внёс логист{card.hs.deal && ` по сделке ${card.hs.deal}`}{card.hs.on && `, ${fmtDate(card.hs.on)}`}</div></div>
              : <div className="muted">ещё не задан — появится после расчёта логистики по этой модели</div>}
            <h3>Данные для логистики</h3>
            {Object.keys(card.logistics_params).length ? <table><tbody>{Object.entries(card.logistics_params).map(([code, f]) => <tr key={code}><td>{f.name || code}</td><td className="num"><b>{String(f.value ?? "—")}</b></td><td className="muted">{(f.unit || "").split(" / ")[0]}</td></tr>)}</tbody></table>
              : <div className="muted">нет</div>}
          </div>
        </div>)}

      {tab === "prices" && card.with_prices && (
        <>
          <h3>Цены и сроки завода</h3>
          {card.factory_prices?.length ? <div style={{ overflowX: "auto" }}><table><thead><tr><th>Дата</th><th>Исполнение</th><th className="num">Цена</th><th>Базис</th><th>Срок производства</th><th>Оплата заводу</th><th>Источник</th><th>Сделка</th></tr></thead>
            <tbody>{card.factory_prices.map((h, i) => <tr key={i}><td>{fmtDate(h.on)}</td><td>{h.execution || "—"}</td><td className="num"><b>{fmtRub(h.unit_price)}</b> {h.currency}</td><td>{h.basis}</td>
              <td>{h.production_term?.value != null ? `${h.production_term.value} ${UNITS[h.production_term.unit || "calendar_days"] || ""}` : "—"}</td>
              <td className="muted" style={{ fontSize: 12 }}>{(h.factory_payments || []).map((p) => `${p.share_pct} %`).join(" / ") || "—"}</td>
              <td className="muted">{({ rfq: "RFQ", offer: "ответ завода", fact: "факт", price_list: "прайс" } as Record<string, string>)[h.source] || h.source}</td>
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
    </div>
  );
}
