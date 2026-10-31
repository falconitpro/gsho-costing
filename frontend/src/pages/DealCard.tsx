import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, ApiError, fmtDate, fmtRub } from "../api";
import { STATUS_TITLES, type Deal, type Me, type Position } from "../types";
import ApprovalScreen from "./ApprovalScreen";
import LogisticsBlock from "./LogisticsBlock";
import ServiceBlock from "./ServiceBlock";
import PositionForm from "./PositionForm";
import Questionnaire from "../components/Questionnaire";

type Tab = "main" | "positions" | "logistics" | "service" | "pricing" | "feed" | "versions";

export default function DealCard({ me }: { me: Me }) {
  const { id } = useParams();
  const [deal, setDeal] = useState<Deal | null>(null);
  const [tab, setTab] = useState<Tab>("main");
  const [err, setErr] = useState("");
  const [rfqPreview, setRfqPreview] = useState<Record<string, unknown> | null>(null);
  const [rfqFile, setRfqFile] = useState<File | null>(null);
  const [priceComment, setPriceComment] = useState("");
  const [variantName, setVariantName] = useState("");
  const [posForm, setPosForm] = useState<"new" | Position | null>(null);
  const [factories, setFactories] = useState<Record<string, string>>({});
  useEffect(() => { api.get<{ id: string; name: string }[]>("/api/refs/factories").then((fs) => setFactories(Object.fromEntries(fs.map((f) => [f.id, f.name])))); }, [posForm]);
  const [feed, setFeed] = useState<{ id: string; kind: string; subject: string; body: string; at: string; counterparty: string }[]>([]);
  const [versions, setVersions] = useState<{ id: string; version_no: number; reason: string; created_at: string; excel_path?: string; excel_manager_path: string; kp_path: string }[]>([]);
  const has = (...r: string[]) => me.roles.some((x) => r.includes(x.code));
  const isMgmt = has("cd", "fd", "gd");

  const load = () => api.get<Deal>(`/api/deals/${id}`).then(setDeal).catch((e) => setErr(e.message));
  // перезагружаем карточку при смене пользователя: набор полей зависит от роли (серверная фильтрация)
  useEffect(() => { setDeal(null); setFeed([]); setVersions([]); setTab("main"); load(); }, [id, me.id]);
  useEffect(() => {
    if (tab === "feed") api.get<typeof feed>(`/api/deals/${id}/feed`).then(setFeed);
    if (tab === "versions") api.get<typeof versions>(`/api/deals/${id}/versions`).then(setVersions);
  }, [tab, id, me.id]);

  if (!deal) return <div className="muted">{err || "Загрузка…"}</div>;
  const editable = has("manager", "gd") && ["draft", "rework", "approved", "kp_sent"].includes(deal.status);

  async function preview() {
    if (!rfqFile) return;
    try { setRfqPreview(await api.upload(`/api/deals/${deal!.id}/rfq/preview`, rfqFile)); setErr(""); } catch (e) { setErr((e as Error).message); }
  }
  async function importRfq() {
    if (!rfqFile) return;
    try {
      const r = await api.upload<{ catalog_diff: Record<string, unknown>; warnings: unknown[] }>(`/api/deals/${deal!.id}/rfq/import`, rfqFile, { price_comment: priceComment });
      setRfqPreview(null); setRfqFile(null); setErr(Object.keys(r.catalog_diff).length ? "Есть различия с каталогом — см. позицию" : ""); load();
    } catch (e) {
      const d = (e as ApiError).detail as { message?: string; errors?: { message: string }[]; price_reference?: { deviation_pct: number } };
      setErr(d?.message + (d?.errors ? ": " + d.errors.map((x) => x.message).join("; ") : "") + (d?.price_reference ? ` (отклонение ${d.price_reference.deviation_pct} %)` : ""));
    }
  }
  async function addVariant() {
    await api.post(`/api/deals/${deal!.id}/variants`, { name: variantName, position_ids: (deal!.positions || []).map((p) => p.id) });
    setVariantName(""); load();
  }
  async function send() {
    try { await api.post(`/api/deals/${deal!.id}/send-to-calc`); setErr(""); load(); }
    catch (e) { const d = (e as ApiError).detail as { errors?: string[] }; setErr(d?.errors ? d.errors.join(" ") : (e as Error).message); }
  }

  return (
    <>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div>
          <h1 style={{ marginBottom: 2 }}>{deal.number} · {deal.client_name}</h1>
          <span className="badge accent">{STATUS_TITLES[deal.status]}</span> <span className="muted">{deal.site} · срок КП {fmtDate(deal.kp_due)} · версия {deal.current_version_no}</span>
        </div>
        {editable && deal.status !== "approved" && deal.status !== "kp_sent" && (
          <button className="btn" onClick={send} disabled={(deal.checks || []).length > 0} title={(deal.checks || []).join("\n")}>Отправить на расчёт</button>
        )}
        {has("manager", "gd") && deal.status === "approved" && (
          <button className="btn" onClick={() => api.post(`/api/deals/${deal.id}/kp-sent`, { recipients: ["client"], sent_via: "outlook" }).then(load)}>КП отправлено</button>
        )}
        {has("manager", "gd") && deal.status === "kp_sent" && (
          <div className="row">
            {["won", "lost", "cancelled"].map((o) => (
              <button key={o} className="btn secondary" onClick={() => { const reason = prompt("Причина исхода:"); if (reason) api.post(`/api/deals/${deal.id}/outcome`, { outcome: o, reason }).then(load); }}>
                {o === "won" ? "Выиграна" : o === "lost" ? "Проиграна" : "Отменена"}</button>))}
          </div>
        )}
      </div>
      {err && <div className="alert error">{err}</div>}
      {(deal.checks || []).length > 0 && editable && <div className="alert warn">Перед отправкой на расчёт: {deal.checks!.join(" ")}</div>}

      <div className="tabs">
        <button className={tab === "main" ? "active" : ""} onClick={() => setTab("main")}>Шапка и условия</button>
        <button className={tab === "positions" ? "active" : ""} onClick={() => setTab("positions")}>Позиции и RFQ</button>
        {has("logistician", "cd", "fd", "gd") && <button className={tab === "logistics" ? "active" : ""} onClick={() => setTab("logistics")}>Логистика и таможня</button>}
        {deal.service_flag && has("service_head", "cd", "fd", "gd") && <button className={tab === "service" ? "active" : ""} onClick={() => setTab("service")}>Сервис</button>}
        {isMgmt && <button className={tab === "pricing" ? "active" : ""} onClick={() => setTab("pricing")}>Расчёт и утверждение</button>}
        <button className={tab === "feed" ? "active" : ""} onClick={() => setTab("feed")}>Лента</button>
        <button className={tab === "versions" ? "active" : ""} onClick={() => setTab("versions")}>Версии и файлы</button>
      </div>

      {tab === "main" && (
        <div className="grid cols-2">
          <div className="card">
            <h2 style={{ marginTop: 0 }}>Шапка</h2>
            <table><tbody>
              <tr><td>Клиент</td><td>{deal.client_name}</td></tr><tr><td>Контактное лицо</td><td>{deal.contact}</td></tr>
              <tr><td>Объект</td><td>{deal.site}</td></tr><tr><td>Дата запроса</td><td>{fmtDate(deal.requested_at)}</td></tr>
              <tr><td>Базовая валюта</td><td>{deal.base_currency}</td></tr>
              <tr><td>Сервис до наработки</td><td>{deal.service_flag ? `да, ${deal.target_hours} м·ч` : "нет"}</td></tr>
            </tbody></table>
          </div>
          <div className="card">
            <h2 style={{ marginTop: 0 }}>Условия оплаты клиента</h2>
            <PaymentsEditor deal={deal} editable={editable} onSaved={load} />
          </div>
        </div>
      )}

      {tab === "positions" && (
        <>
          <div className="card">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <h2 style={{ marginTop: 0 }}>Позиции</h2>
              {editable && <button className="btn" onClick={() => setPosForm("new")}>+ Добавить вручную</button>}
            </div>
            <table>
              <thead><tr><th>Завод</th><th>Тип / модель</th><th className="num">Кол-во</th><th className="num">Цена за ед.</th><th>Валюта</th><th className="num">Опции</th><th>Базис</th><th className="num">Произв., дн</th><th>Справка по цене</th><th>Источник</th>{editable && <th />}</tr></thead>
              <tbody>{(deal.positions || []).map((p) => {
                const pr = (p.price_reference || {}) as { last_price?: number; deviation_pct?: number; comment?: string };
                return (<tr key={p.id}><td>{factories[p.factory_id] || p.factory_id.slice(0, 8)}</td><td>{p.machine_type} <b>{p.model}</b></td><td className="num">{p.qty}</td>
                  <td className="num">{fmtRub(p.unit_price)}</td><td>{p.currency}</td><td className="num">{fmtRub(p.options_price)}</td><td>{p.basis}</td><td className="num">{p.production_days}</td>
                  <td>{pr.last_price ? <>посл. {fmtRub(pr.last_price)} ({pr.deviation_pct} %) {pr.comment && <span className="muted">— {pr.comment}</span>}</> : <span className="muted">нет истории</span>}</td>
                  <td>{(p as Position & { rfq_import_id?: string | null }).rfq_import_id ? <span className="badge accent">RFQ</span> : <span className="badge">вручную</span>}</td>
                  {editable && <td className="row"><button className="btn ghost" onClick={() => setPosForm(p)}>изменить</button>
                    <button className="btn ghost" onClick={() => { if (confirm(`Удалить позицию ${p.model}?`)) api.del(`/api/deals/${deal.id}/positions/${p.id}`).then(load); }}>удалить</button></td>}</tr>);
              })}</tbody>
            </table>
          </div>
          {editable && posForm && (
            <PositionForm dealId={deal.id} position={posForm === "new" ? undefined : posForm} onDone={() => { setPosForm(null); load(); }} onCancel={() => setPosForm(null)} />
          )}
          {editable && (
            <div className="card">
              <h2 style={{ marginTop: 0 }}>Импорт RFQ (двуязычный шаблон RU-ZH)</h2>
              <p className="muted">Позицию можно завести двумя способами: загрузить заполненный заводом файл RFQ или ввести поля вручную (кнопка «Добавить вручную»). Импортированные значения потом тоже можно править вручную.</p>
              <div className="row">
                <input type="file" accept=".xlsx" onChange={(e) => setRfqFile(e.target.files?.[0] || null)} />
                <button className="btn secondary" onClick={preview} disabled={!rfqFile}>Предпросмотр</button>
                <input placeholder="комментарий к отклонению цены (если > порога)" value={priceComment} onChange={(e) => setPriceComment(e.target.value)} style={{ width: 300 }} />
                <button className="btn" onClick={importRfq} disabled={!rfqFile}>Импортировать</button>
              </div>
              {rfqPreview && <RfqPreview data={rfqPreview} />}
            </div>
          )}
          {(deal.positions || []).map((p) => (
            <div className="card" key={"q" + p.id}>
              <h2 style={{ marginTop: 0 }}>Опросник: {p.machine_type} {p.model} × {p.qty}</h2>
              <Questionnaire position={p} />
              {Array.isArray(p.options) && p.options.length > 0 && <div className="muted" style={{ marginTop: 6 }}>Опции (на 1 машину): {p.options.map((o: { name?: string; value?: number | null }) => `${o.name}${o.value != null ? ` — ${fmtRub(o.value)} ${p.currency}` : " (цена не указана)"}`).join("; ")}</div>}
            </div>
          ))}
          <div className="card">
            <h2 style={{ marginTop: 0 }}>Варианты комплектации / условий</h2>
            <ul>{(deal.variants || []).map((v) => <li key={v.id}>{v.name} <span className="muted">({v.position_ids.length} поз.)</span>{v.price_rub != null && <> — цена {fmtRub(v.price_rub)} RUB</>}</li>)}</ul>
            {editable && <div className="row"><input placeholder="Название варианта" value={variantName} onChange={(e) => setVariantName(e.target.value)} /><button className="btn secondary" onClick={addVariant} disabled={!variantName}>Добавить вариант (все позиции)</button></div>}
          </div>
        </>
      )}

      {tab === "logistics" && <LogisticsBlock key={me.id} deal={deal} me={me} reload={load} />}
      {tab === "service" && <ServiceBlock key={me.id} deal={deal} me={me} reload={load} />}
      {tab === "pricing" && <ApprovalScreen key={me.id} deal={deal} me={me} reload={load} />}

      {tab === "feed" && (
        <div className="card">
          {feed.length === 0 && <div className="muted">Переписка с заводом (WeCom) и клиентом (Outlook) появится здесь</div>}
          {feed.map((f) => <div key={f.id} style={{ borderBottom: "1px solid var(--line)", padding: "6px 0" }}><span className="badge">{f.kind}</span> <b>{f.subject}</b> <span className="muted">{f.counterparty} · {new Date(f.at).toLocaleString("ru-RU")}</span><div>{f.body}</div></div>)}
        </div>
      )}
      {tab === "versions" && (
        <div className="card">
          <table><thead><tr><th>Версия</th><th>Причина</th><th>Дата</th><th>Excel-расчёт</th><th>КП</th></tr></thead>
            <tbody>{versions.map((v) => (
              <tr key={v.id}><td>v{v.version_no}</td><td>{v.reason}</td><td>{new Date(v.created_at).toLocaleString("ru-RU")}</td>
                <td>{v.excel_path && <><a href={`/api/files/${v.excel_path}`}>полный</a> · </>}{v.excel_manager_path && <a href={`/api/files/${v.excel_manager_path}`}>версия менеджера</a>}</td>
                <td>{v.kp_path && <a href={`/api/files/${v.kp_path}`}>КП.docx</a>}</td></tr>))}</tbody></table>
        </div>
      )}
    </>
  );
}

function RfqPreview({ data }: { data: Record<string, unknown> }) {
  const fields = data.fields as Record<string, { label_ru: string; value: unknown; unit: string; is_l: boolean }>;
  const missing = (data.missing_required as string[]) || [];
  const errors = (data.errors as { code: string; message: string; severity: string }[]) || [];
  return (
    <div style={{ marginTop: 10 }}>
      <div className="muted">Версия шаблона: {String(data.template_version)} · ЗИП: {(data.spare_parts as unknown[]).length} строк</div>
      {errors.map((e, i) => <div key={i} className={"alert " + (e.severity === "error" ? "error" : "warn")}>{e.message}</div>)}
      <table><thead><tr><th>Код</th><th>Параметр</th><th>Значение</th><th>Ед.</th><th>L</th></tr></thead>
        <tbody>{Object.entries(fields).map(([code, f]) => (
          <tr key={code} style={{ background: missing.includes(code) ? "#fde7e7" : undefined }}><td>{code}</td><td>{f.label_ru}</td><td>{f.value == null ? <span className="badge bad">пусто</span> : String(f.value)}</td><td>{f.unit}</td><td>{f.is_l ? "L" : ""}</td></tr>))}</tbody></table>
    </div>
  );
}

function PaymentsEditor({ deal, editable, onSaved }: { deal: Deal; editable: boolean; onSaved: () => void }) {
  const [schemes, setSchemes] = useState<{ id: string; name: string; lines: unknown[] }[]>([]);
  useEffect(() => { api.get<typeof schemes>("/api/refs/payment-schemes").then(setSchemes); }, []);
  const lines = deal.client_payments || [];
  return (
    <>
      {editable && (
        <div className="row" style={{ marginBottom: 8 }}>
          <span className="muted">Типовая схема:</span>
          {schemes.map((s) => <button key={s.id} className="btn ghost" onClick={() => api.patch(`/api/deals/${deal.id}`, { client_payments: s.lines }).then(onSaved)}>{s.name}</button>)}
        </div>
      )}
      <table><thead><tr><th>№</th><th>Доля</th><th>Событие</th><th>Смещение, дн</th><th>Гарантия</th></tr></thead>
        <tbody>{lines.map((l) => <tr key={l.no}><td>{l.no}</td><td>{l.share_pct != null ? `${l.share_pct} %` : fmtRub(l.amount)}</td><td>{l.event}</td><td>{l.offset_days}</td><td>{l.guarantee && l.guarantee !== "none" ? `${l.guarantee} ${l.guarantee_pct} % / ${l.guarantee_months} мес` : "—"}</td></tr>)}
          {lines.length === 0 && <tr><td colSpan={5} className="muted">Не заданы</td></tr>}</tbody></table>
    </>
  );
}
