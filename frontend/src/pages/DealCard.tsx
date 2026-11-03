import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, ApiError, fmtDate, fmtRub, designation } from "../api";
import { DELIVERY_START, PRODUCTION_START, STATUS_TITLES, TERM_UNITS, can, type Deal, type Me, type Position } from "../types";
import ApprovalScreen from "./ApprovalScreen";
import LogisticsBlock from "./LogisticsBlock";
import ServiceBlock from "./ServiceBlock";
import PositionForm from "./PositionForm";
import Questionnaire from "../components/Questionnaire";
import TechParamsTable from "../components/TechParamsTable";
import KpActions from "../components/KpActions";
import ExecutionTab from "../components/ExecutionTab";
import { ImagesBlock, LocalOptionsBlock, OffersBlock, RelatedDeals } from "../components/ImagesLocalOptions";
import { payText } from "../components/PaymentTermsEditor";

type Tab = "main" | "positions" | "offers" | "logistics" | "service" | "pricing" | "execution" | "catalog" | "feed" | "versions";
const POST_WIN = ["contract", "execution", "closed"];

export default function DealCard({ me }: { me: Me }) {
  const { id } = useParams();
  const nav = useNavigate();
  const [deal, setDeal] = useState<Deal | null>(null);
  const [tab, setTab] = useState<Tab>("main");
  const [err, setErr] = useState("");
  const [rfqPreview, setRfqPreview] = useState<Record<string, unknown> | null>(null);
  const [rfqFile, setRfqFile] = useState<File | null>(null);
  const [priceComment, setPriceComment] = useState("");
  const [variantName, setVariantName] = useState("");
  const [posForm, setPosForm] = useState<"new" | Position | null>(null);
  // несколько позиций: наименование в перечне открывает таблицу технических характеристик этой позиции ниже
  const [activePos, setActivePos] = useState<string>("");
  const openPos = (id: string) => { setActivePos(id); setTimeout(() => document.getElementById("pos-" + id)?.scrollIntoView({ behavior: "smooth", block: "start" }), 50); };
  const [factories, setFactories] = useState<Record<string, string>>({});
  const [wishes, setWishes] = useState("");
  const [altChoice, setAltChoice] = useState<{ id: string; name: string; price_rub: number | null; positions: number }[] | null>(null);
  const [altKeep, setAltKeep] = useState("");
  useEffect(() => { api.get<{ id: string; name: string }[]>("/api/refs/factories").then((fs) => setFactories(Object.fromEntries(fs.map((f) => [f.id, f.name])))); }, [posForm]);
  const [feed, setFeed] = useState<{ id: string; kind: string; subject: string; body: string; at: string; counterparty: string }[]>([]);
  const [versions, setVersions] = useState<{ id: string; version_no: number; reason: string; created_at: string; excel_path?: string; excel_manager_path: string; kp_path: string; kp_meta?: Record<string, unknown> }[]>([]);
  const [cand, setCand] = useState<{ position_id: string; model: string; model_exists: boolean; execution_exists: boolean; pending_execution_id: string | null; params_filled: number; params_total: number }[]>([]);
  const [candSel, setCandSel] = useState<Record<string, boolean>>({});
  const has = (...r: string[]) => me.roles.some((x) => r.includes(x.code));
  const isMgmt = has("cd", "fd", "gd");

  const load = () => api.get<Deal>(`/api/deals/${id}`).then((d) => { setDeal(d); setWishes(d.client_payment_wishes || ""); }).catch((e) => setErr(e.message));
  useEffect(() => { setDeal(null); setFeed([]); setVersions([]); setTab("main"); load(); }, [id, me.id]);
  useEffect(() => {
    if (tab === "feed") api.get<typeof feed>(`/api/deals/${id}/feed`).then(setFeed);
    if (tab === "versions") api.get<typeof versions>(`/api/deals/${id}/versions`).then(setVersions);
    if (tab === "catalog") api.get<typeof cand>(`/api/deals/${id}/catalog/candidates`).then(setCand);
  }, [tab, id, me.id]);

  if (!deal) return <div className="muted">{err || "Загрузка…"}</div>;
  const isOwner = has("gd") || (has("manager") && deal.manager_id === me.id);
  const editable = isOwner && ["draft", "rework", "approved", "kp_sent"].includes(deal.status);
  const canTech = (isOwner || has("cd")) && can(me, "tech_params", 2) && !POST_WIN.includes(deal.status);

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
  async function outcome(o: string) {
    const reason = prompt("Причина исхода:");
    if (!reason) return;
    let won_variant_id: string | undefined;
    if (o === "won" && deal!.has_alternatives) {
      const names = deal!.variants.map((v, i) => `${i + 1} — ${v.name}`).join("\n");
      const n = Number(prompt(`Какое предложение выбрал клиент?\n${names}`, "1"));
      won_variant_id = deal!.variants[n - 1]?.id;
      if (!won_variant_id) return;
    }
    try { const r = await api.post<{ closed_related: string[] }>(`/api/deals/${deal!.id}/outcome`, { outcome: o, reason, won_variant_id }); setErr(r.closed_related?.length ? `Связанные заявки закрыты: ${r.closed_related.join(", ")}` : ""); load(); }
    catch (e) { setErr((e as Error).message); }
  }
  async function duplicate() {
    const comment = prompt("Дублировать заявку (новая заявка того же клиента: требования, технические характеристики, изображения, расчёты логиста и сервиса с пометкой «проверить»). Комментарий:") ?? null;
    if (comment === null) return;
    try { const d = await api.post<Deal>(`/api/deals/${deal!.id}/duplicate`, { copy_calcs: true, comment }); nav(`/deals/${d.id}`); } catch (e) { setErr((e as Error).message); }
  }
  const posTerm = (p: Position) => {
    const t = p.production_term || {};
    const prod = t.unit === "date" ? `до ${fmtDate(t.fixed_date)}` : `${t.value ?? p.production_days} ${TERM_UNITS[t.unit || "calendar_days"]}`;
    return `${prod} ${PRODUCTION_START[t.start_event || "factory_contract"]}`;
  };

  return (
    <>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div>
          <h1 style={{ marginBottom: 2 }}>{deal.number} · {deal.client_name}{deal.parent_deal_id && <span className="badge" style={{ marginLeft: 6 }}>дубликат</span>}</h1>
          <span className="badge accent">{STATUS_TITLES[deal.status]}</span> <span className="muted">{deal.site} · срок КП {fmtDate(deal.kp_due)} · версия {deal.current_version_no}{deal.contract_number && ` · контракт ${deal.contract_number}`}</span>
        </div>
        <div className="row">
          {editable && deal.status !== "approved" && deal.status !== "kp_sent" && (
            <button className="btn" onClick={send} disabled={(deal.checks || []).length > 0} title={(deal.checks || []).join("\n")}>Отправить на расчёт</button>
          )}
          {isOwner && deal.status === "approved" && (
            <button className="btn ghost" title="если КП отправили вручную из своей почты" onClick={() => api.post(`/api/deals/${deal.id}/kp-sent`, { recipients: ["client"], sent_via: "outlook" }).then(load)}>Отметить: КП отправлено вручную</button>
          )}
          {isOwner && deal.status === "kp_sent" && ["won", "lost", "cancelled"].map((o) => (
            <button key={o} className={"btn " + (o === "won" ? "" : "secondary")} onClick={() => outcome(o)}>{o === "won" ? "Выиграна → контракт" : o === "lost" ? "Проиграна" : "Отменена"}</button>))}
          {(isOwner || has("cd")) && <button className="btn ghost" onClick={duplicate} title="Задача 9: новая заявка по тому же запросу клиента">Дублировать заявку</button>}
        </div>
      </div>
      {err && <div className="alert error">{err}</div>}
      <div style={{ margin: "6px 0" }}><KpActions dealId={deal.id} status={deal.status} canAct={isOwner || has("cd", "gd", "admin")} onDone={() => { load(); }} /></div>
      {(deal.checks || []).length > 0 && editable && <div className="alert warn">Перед отправкой на расчёт: {deal.checks!.join(" ")}</div>}
      <RelatedDeals dealId={deal.id} me={me} />

      <div className="tabs">
        <button className={tab === "main" ? "active" : ""} onClick={() => setTab("main")}>Шапка и условия</button>
        <button className={tab === "positions" ? "active" : ""} onClick={() => setTab("positions")}>Позиции, технические характеристики, файлы</button>
        <button className={tab === "offers" ? "active" : ""} onClick={() => setTab("offers")}>Заводы и ответы</button>
        {has("logistician", "cd", "fd", "gd") && <button className={tab === "logistics" ? "active" : ""} onClick={() => setTab("logistics")}>Логистика и таможня</button>}
        {deal.service_flag && has("service_head", "cd", "fd", "gd") && <button className={tab === "service" ? "active" : ""} onClick={() => setTab("service")}>Сервис</button>}
        {isMgmt && <button className={tab === "pricing" ? "active" : ""} onClick={() => setTab("pricing")}>Расчёт и утверждение</button>}
        {POST_WIN.includes(deal.status) && <button className={tab === "execution" ? "active" : ""} onClick={() => setTab("execution")}>Контракт и исполнение</button>}
        {["kp_sent", ...POST_WIN, "lost"].includes(deal.status) && (isOwner || has("cd")) && <button className={tab === "catalog" ? "active" : ""} onClick={() => setTab("catalog")}>В каталог</button>}
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
              <tr><td>Альтернативные предложения в КП</td><td><label className="row"><input type="checkbox" checked={!!deal.has_alternatives} disabled={!editable} onChange={(e) => {
                  setErr("");
                  api.patch(`/api/deals/${deal.id}`, { has_alternatives: e.target.checked }).then(() => { setAltChoice(null); load(); }).catch((er) => {
                    const d = (er as ApiError).detail as { choose_variant?: { id: string; name: string; price_rub: number | null; positions: number }[] };
                    if (d?.choose_variant) { setAltChoice(d.choose_variant); setAltKeep(d.choose_variant[0]?.id || ""); } else setErr((er as Error).message);
                  });
                }} />
                {deal.has_alternatives ? "да — несколько предложений (А/Б) в одном КП" : "нет — одна сделка, одно КП"}</label>
                {altChoice && (   // задача 5: выключение альтернатив — выбор, какое предложение остаётся основным
                  <div className="alert warn" style={{ marginTop: 6 }}>
                    Останется одно предложение, остальные будут удалены вместе с их ценой и условиями. Какое оставить основным?
                    {altChoice.map((v) => (
                      <label key={v.id} className="row" style={{ marginTop: 4 }}><input type="radio" name="alt-keep" checked={altKeep === v.id} onChange={() => setAltKeep(v.id)} />
                        {v.name} <span className="muted">· позиций {v.positions}{v.price_rub != null && can(me, "price") ? ` · ${fmtRub(v.price_rub)} RUB` : ""}</span></label>))}
                    <div className="row" style={{ marginTop: 6 }}>
                      <button className="btn" disabled={!altKeep} onClick={() => api.patch(`/api/deals/${deal.id}`, { has_alternatives: false, keep_variant_id: altKeep }).then(() => { setAltChoice(null); load(); }).catch((er) => setErr((er as Error).message))}>Выключить альтернативы</button>
                      <button className="btn ghost" onClick={() => setAltChoice(null)}>отмена</button>
                    </div>
                  </div>
                )}</td></tr>
              {deal.kp_brand && <tr><td>Подача КП</td><td>{deal.kp_brand === "company" ? "под брендом компании" : "под маркой завода"}</td></tr>}
            </tbody></table>
          </div>
          <div className="card">
            <h2 style={{ marginTop: 0 }}>Условия оплаты клиента</h2>
            <p className="muted">Условия задаёт коммерческий директор (экран «Расчёт и утверждение»). Пока они не заданы, расчёт идёт по типовой схеме: 30 % после подписания, 70 % аккредитивом после пересечения границы. Менеджер может передать пожелания клиента.</p>
            <label className="f">Пожелания клиента по оплате
              <textarea rows={2} value={wishes} disabled={!editable} onChange={(e) => setWishes(e.target.value)} onBlur={() => wishes !== (deal.client_payment_wishes || "") && api.patch(`/api/deals/${deal.id}`, { client_payment_wishes: wishes }).then(load)} /></label>
            <div style={{ marginTop: 8 }}>{deal.payment_terms_set ? <span className="badge ok">условия заданы КД</span> : <span className="badge warn">типовая схема (КД не задал)</span>}</div>
            {(deal.client_payments || []).length > 0 && <table style={{ marginTop: 6 }}><tbody>{deal.client_payments.map((l) => <tr key={l.no}><td>{l.no}</td><td>{payText(l)}</td></tr>)}</tbody></table>}
            {deal.has_alternatives && deal.variants.filter((v) => v.client_payments?.length).map((v) => <div key={v.id} className="muted"><b>{v.name}:</b> {v.client_payments.map(payText).join("; ")}</div>)}
          </div>
        </div>
      )}

      {tab === "positions" && (() => { const multi = (deal.positions || []).length > 1;
        const shownPos = (deal.positions || []).find((x) => x.id === activePos) || (deal.positions || [])[0];
        return (
        <>
          <div className="card">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <h2 style={{ marginTop: 0 }}>Позиции</h2>
              {editable && <button className="btn" onClick={() => setPosForm("new")}>+ Добавить вручную / из каталога</button>}
            </div>
            <table>
              <thead><tr><th>Завод</th><th>Тип / модель</th><th className="num">Кол-во</th><th className="num">Цена за ед.</th><th>Валюта</th><th className="num">Опции</th><th>Базис</th><th>Срок производства</th><th>Срок клиенту</th><th>Справка по цене</th><th>Источник</th>{editable && <th />}</tr></thead>
              <tbody>{(deal.positions || []).map((p) => {
                const pr = (p.price_reference || {}) as { last_price?: number; deviation_pct?: number; comment?: string };
                const dt = p.delivery_term || {};
                return (<tr key={p.id} style={multi && shownPos?.id === p.id ? { background: "var(--accent-soft)" } : undefined}><td>{factories[p.factory_id] || p.factory_id.slice(0, 8)}</td><td>{multi ? <a href={"#pos-" + p.id} title="открыть технические характеристики позиции" onClick={(e) => { e.preventDefault(); openPos(p.id); }}
                    style={{ fontWeight: shownPos?.id === p.id ? 700 : 400 }}>{p.machine_type} <b>{designation(p)}</b></a> : <>{p.machine_type} <b>{designation(p)}</b></>}{p.execution_id && <span className="badge" title="из каталога"> каталог</span>}</td><td className="num">{p.qty}</td>
                  <td className="num">{fmtRub(p.unit_price)}</td><td>{p.currency}</td><td className="num">{fmtRub(p.options_price)}</td><td>{p.basis}</td><td>{posTerm(p)}</td>
                  <td className="muted">{TERM_UNITS[dt.unit || "calendar_days"]} {DELIVERY_START[dt.start_event || "client_contract"]} (+10 %, до недели)</td>
                  <td>{pr.last_price ? <>посл. {fmtRub(pr.last_price)} ({pr.deviation_pct} %) {pr.comment && <span className="muted">— {pr.comment}</span>}</> : <span className="muted">нет истории</span>}</td>
                  <td>{p.rfq_import_id ? <span className="badge accent">RFQ</span> : p.offer_id ? <span className="badge">ответ завода</span> : <span className="badge">вручную</span>}</td>
                  {editable && <td className="row"><button className="btn ghost" onClick={() => setPosForm(p)}>изменить</button>
                    <button className="btn ghost" onClick={() => { if (confirm(`Удалить позицию ${designation(p)}?`)) api.del(`/api/deals/${deal.id}/positions/${p.id}`).then(load); }}>удалить</button></td>}</tr>);
              })}</tbody>
            </table>
          </div>
          {editable && posForm && (
            <PositionForm dealId={deal.id} position={posForm === "new" ? undefined : posForm} onDone={() => { setPosForm(null); load(); }} onCancel={() => setPosForm(null)} />
          )}
          {editable && (
            <div className="card">
              <h2 style={{ marginTop: 0 }}>Импорт RFQ (двуязычный шаблон RU-CN)</h2>
              <p className="muted">Позицию можно завести тремя способами: выбрать исполнение из каталога, загрузить заполненный заводом файл RFQ или ввести поля вручную. Технические характеристики затем правятся в таблице ниже, а запрос заводу формируется из неё.</p>
              <div className="row">
                <input type="file" accept=".xlsx" onChange={(e) => setRfqFile(e.target.files?.[0] || null)} />
                <button className="btn secondary" onClick={preview} disabled={!rfqFile}>Предпросмотр</button>
                <input placeholder="комментарий к отклонению цены (если > порога)" value={priceComment} onChange={(e) => setPriceComment(e.target.value)} style={{ width: 300 }} />
                <button className="btn" onClick={importRfq} disabled={!rfqFile}>Импортировать</button>
              </div>
              {rfqPreview && <RfqPreview data={rfqPreview} />}
            </div>
          )}
          {(deal.positions || []).filter((p) => !multi || p.id === shownPos?.id).map((p) => (
            <div className="card" key={"q" + p.id} id={"pos-" + p.id} style={{ scrollMarginTop: 80 }}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <h2 style={{ marginTop: 0 }}>{p.machine_type} {designation(p)} × {p.qty}</h2>
                {multi && <div className="row">
                  <span className="muted">позиция {(deal.positions || []).findIndex((x) => x.id === p.id) + 1} из {(deal.positions || []).length}:</span>
                  <select value={p.id} onChange={(e) => openPos(e.target.value)}>
                    {(deal.positions || []).map((x, i) => <option key={x.id} value={x.id}>{i + 1}. {x.machine_type} {designation(x)}</option>)}</select>
                </div>}
              </div>
              {can(me, "tech_params") && <TechParamsTable dealId={deal.id} position={p} canEdit={canTech} canDelete={has("admin", "cd", "gd")} onChanged={load} />}
              <div style={{ marginTop: 14 }}>{can(me, "images") && <ImagesBlock dealId={deal.id} position={p} me={me} onChanged={load} />}</div>
              <div style={{ marginTop: 14 }}>{can(me, "local_options") && <LocalOptionsBlock dealId={deal.id} position={p} me={me} onChanged={load} />}</div>
              <details style={{ marginTop: 10 }}><summary className="muted" style={{ cursor: "pointer" }}>Опросник RFQ по кодам (как импортировано)</summary><Questionnaire position={p} /></details>
              {Array.isArray(p.options) && p.options.length > 0 && <div className="muted" style={{ marginTop: 6 }}>Опции завода (на 1 машину): {p.options.map((o: { name?: string; value?: number | null }) => `${o.name}${o.value != null ? ` — ${fmtRub(o.value)} ${p.currency}` : " (цена не указана)"}`).join("; ")}</div>}
            </div>
          ))}
          {deal.has_alternatives && (
            <div className="card">
              <h2 style={{ marginTop: 0 }}>Альтернативные предложения в КП</h2>
              <ul>{(deal.variants || []).map((v) => <li key={v.id}>{v.name} <span className="muted">({v.position_ids.length} поз.)</span>{v.price_rub != null && <> — цена {fmtRub(v.price_rub)} RUB</>}{deal.won_variant_id === v.id && <span className="badge ok"> выбрал клиент</span>}
                {editable && <button className="btn ghost" style={{ padding: "0 6px", marginLeft: 6 }} onClick={() => api.del(`/api/deals/${deal.id}/variants/${v.id}`).then(load)}>×</button>}</li>)}</ul>
              {editable && <div className="row"><input placeholder="Название предложения (А / Б)" value={variantName} onChange={(e) => setVariantName(e.target.value)} /><button className="btn secondary" onClick={addVariant} disabled={!variantName}>Добавить предложение (все позиции)</button></div>}
              <p className="muted">Минимум два предложения; состав позиций каждого можно изменить через API/КД. Условия оплаты и цену по каждому предложению задаёт КД.</p>
            </div>
          )}
        </>); })()}

      {tab === "offers" && (
        <>
          {(deal.positions || []).map((p) => <div className="card" key={"o" + p.id}><h2 style={{ marginTop: 0 }}>{designation(p)} × {p.qty}</h2><OffersBlock dealId={deal.id} position={p} me={me} onChanged={load} /></div>)}
          {(deal.positions || []).length === 0 && <div className="card muted">Сначала добавьте позицию</div>}
        </>
      )}

      {tab === "logistics" && <LogisticsBlock key={me.id} deal={deal} me={me} reload={load} />}
      {tab === "service" && <ServiceBlock key={me.id} deal={deal} me={me} reload={load} />}
      {tab === "pricing" && <ApprovalScreen key={me.id} deal={deal} me={me} reload={load} />}
      {tab === "execution" && <ExecutionTab key={me.id} deal={deal} me={me} reload={load} />}

      {tab === "catalog" && (
        <div className="card">
          <h2 style={{ marginTop: 0 }}>Занесение оборудования в каталог</h2>
          <p className="muted">Отметьте, что занести: новую модель и/или новое исполнение (набор технических характеристик). После подтверждения менеджера запись уходит КД/ЗГД на проверку; до подтверждения в подборе не участвует. История цен пишется автоматически.</p>
          <table><thead><tr><th /><th>Модель</th><th>Модель в каталоге</th><th>Исполнение</th><th>Параметров заполнено</th><th>Название исполнения</th></tr></thead>
            <tbody>{cand.map((c) => (
              <tr key={c.position_id}><td><input type="checkbox" checked={!!candSel[c.position_id]} disabled={c.execution_exists || !!c.pending_execution_id} onChange={(e) => setCandSel({ ...candSel, [c.position_id]: e.target.checked })} /></td>
                <td>{c.model}</td><td>{c.model_exists ? "есть" : <span className="badge warn">новая</span>}</td>
                <td>{c.execution_exists ? <span className="badge ok">уже есть (история дополнена)</span> : c.pending_execution_id ? <span className="badge warn">на проверке у КД</span> : <span className="badge">новое</span>}</td>
                <td>{c.params_filled} / {c.params_total}</td>
                <td><input placeholder={`${c.model} — ${deal.number}`} id={`nm-${c.position_id}`} /></td></tr>))}</tbody></table>
          <div className="row" style={{ marginTop: 8 }}>
            <button className="btn" disabled={!Object.values(candSel).some(Boolean)} onClick={() => {
              const ids = Object.keys(candSel).filter((k) => candSel[k]);
              const names = Object.fromEntries(ids.map((k) => [k, (document.getElementById(`nm-${k}`) as HTMLInputElement | null)?.value || ""]));
              api.post(`/api/deals/${deal.id}/catalog/propose`, { position_ids: ids, names }).then(() => { setErr(""); api.get<typeof cand>(`/api/deals/${deal.id}/catalog/candidates`).then(setCand); }).catch((e) => setErr(e.message));
            }}>Подтвердить и отправить КД</button>
          </div>
        </div>
      )}

      {tab === "feed" && (
        <div className="card">
          {feed.length === 0 && <div className="muted">Переписка с заводом (WeCom) и клиентом (Outlook), системные события появятся здесь</div>}
          {feed.map((f) => <div key={f.id} style={{ borderBottom: "1px solid var(--line)", padding: "6px 0" }}><span className="badge">{f.kind}</span> <b>{f.subject}</b> <span className="muted">{f.counterparty} · {new Date(f.at).toLocaleString("ru-RU")}</span><div>{f.body}</div></div>)}
        </div>
      )}
      {tab === "versions" && (
        <div className="card">
          <table><thead><tr><th>Версия</th><th>Причина</th><th>Дата</th><th>Excel-расчёт</th><th>КП</th></tr></thead>
            <tbody>{versions.map((v) => (
              <tr key={v.id}><td>v{v.version_no}</td><td>{v.reason}</td><td>{new Date(v.created_at).toLocaleString("ru-RU")}</td>
                <td>{v.excel_path && <><a href={`/api/files/${v.excel_path}`}>полный</a> · </>}{v.excel_manager_path && <a href={`/api/files/${v.excel_manager_path}`}>версия менеджера</a>}</td>
                <td>{v.kp_path && <a href={`/api/files/${v.kp_path}`}>КП.docx</a>}{!!v.kp_meta?.kp_brand && <span className="muted"> · {v.kp_meta.kp_brand === "company" ? "бренд компании" : "марка завода"}{Number(v.kp_meta.images_in_document) > 0 && `, изображений ${String(v.kp_meta.images_in_document)}`}</span>}</td></tr>))}</tbody></table>
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
