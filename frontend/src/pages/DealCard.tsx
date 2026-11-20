import { useEffect, useState } from "react";
import { ActionMenu, Crumbs, EntryGrid, useUiMode, type Entry } from "../components/Levels";
import { toneOf } from "../colors";
import ChecksPanel from "../components/ChecksPanel";
import { NowLine, POST_WIN_STATUSES, SaleSummary, StageStrip, dealPhase } from "../components/DealHeader";
import { lbl, FEED_KIND } from "../labels";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, ApiError, fmtDate, fmtRub, designation } from "../api";
import { DELIVERY_START, PRODUCTION_START, STATUS_TITLES, TERM_UNITS, can, type Deal, type Me, type Position } from "../types";
import ApprovalScreen from "./ApprovalScreen";
import LogisticsBlock from "./LogisticsBlock";
import ServiceBlock from "./ServiceBlock";
import PositionForm from "./PositionForm";
import Questionnaire from "../components/Questionnaire";
import TechParamsTable from "../components/TechParamsTable";
import KpActions from "../components/KpActions";
import KpRevisions from "../components/KpRevisions";
import PlanFactTab from "../components/PlanFactTab";
import OutcomePanel from "../components/OutcomePanel";
import WaitingPanel from "../components/WaitingPanel";
import ExecutionTab from "../components/ExecutionTab";
import DocumentsTab, { REVIEW_BADGE } from "../components/DocumentsTab";
import LegalDocsTab from "../components/LegalDocsTab";
import { ImagesBlock, LocalOptionsBlock, OffersBlock, RelatedDeals } from "../components/ImagesLocalOptions";
import { ModelDocsBlock, ZipBlock } from "../components/PositionExtras";
import AcceptanceTab from "../components/AcceptanceTab";
import CurrentOfferPanel from "../components/CurrentOfferPanel";
import { payText } from "../components/PaymentTermsEditor";
import { askConfirm, askPrompt } from "../components/Dialogs";

type Tab = "overview" | "acceptance" | "main" | "positions" | "offers" | "logistics" | "service" | "pricing" | "execution" | "planfact" | "documents" | "legal" | "catalog" | "feed" | "versions";
const POST_WIN = ["contract", "execution", "closed"];
type Phase = "sale" | "contract";
const SALE_TABS: Tab[] = ["main", "positions", "offers", "logistics", "service", "pricing", "catalog"];
const CONTRACT_TABS: Tab[] = ["execution", "acceptance", "planfact", "documents", "legal"];
const TAB_TITLES: Record<Tab, string> = { main: "Шапка и условия", positions: "Позиции и характеристики", offers: "Заводы и ответы", logistics: "Логистика и таможня",
  service: "Сервис", pricing: "Расчёт и утверждение", catalog: "В каталог", execution: "Контракт, этапы и даты", acceptance: "Приёмка машин",
  planfact: "Платежи и план/факт", documents: "Документы", legal: "Юридические документы", feed: "Лента", versions: "Версии и файлы", overview: "Обзор" };
// что внутри раздела — видно на обзоре сразу, не во всплывающей подсказке
const TAB_HINTS: Partial<Record<Tab, string>> = { main: "клиент, контакт, валюта, сервис, альтернативы, условия оплаты", positions: "техника, количество, сроки производства, технические характеристики, фото, опции, ЗИП",
  offers: "ответы заводов, цены, выбор предложения", logistics: "маршрут, перевозка, таможня, утилизационный сбор", service: "расчёт сервиса до наработки",
  pricing: "себестоимость, цена, условия оплаты, визы, КП и торги", catalog: "перенос моделей и характеристик сделки в каталог", execution: "реквизиты контрактов, сканы, этапы, ключевые сроки",
  acceptance: "готовность, предварительная и окончательная приёмка", planfact: "график платежей, поступления, отклонения", documents: "файлы клиента, проверка юриста, пакеты документов",
  legal: "договоры, спецификации, доверенности по сделке", feed: "переписка, события и комментарии по сделке", versions: "версии расчёта, Excel, КП" };

export default function DealCard({ me }: { me: Me }) {
  const { id } = useParams();
  const nav = useNavigate();
  const wantTab = new URLSearchParams(useLocation().search).get("tab") as Tab | null;   // /deals/:id?tab=execution — из задач и уведомлений
  const [deal, setDeal] = useState<Deal | null>(null);
  const [tab, setTabState] = useState<Tab>("main");
  const [phase, setPhaseState] = useState<Phase>("sale");
  // вкладка задаёт фазу: «Продажа» (до выигрыша) или «Контракт и исполнение» (после)
  const setTab = (t: Tab) => { setTabState(t); if (SALE_TABS.includes(t)) setPhaseState("sale"); else if (CONTRACT_TABS.includes(t)) setPhaseState("contract"); };
  const setPhase = (p: Phase) => { setPhaseState(p); setTabState(p === "sale" ? "main" : "execution"); };
  const [err, setErr] = useState("");
  const [rfqPreview, setRfqPreview] = useState<Record<string, unknown> | null>(null);
  const [rfqFile, setRfqFile] = useState<File | null>(null);
  const [priceComment, setPriceComment] = useState("");
  const [variantName, setVariantName] = useState("");
  // D-071: из формы новой сделки — сразу заявка по выбранному типу техники
  const startPos = (() => { const q = new URLSearchParams(window.location.search); return q.get("newpos") === "1" ? { type: q.get("type") || "", qty: q.get("qty") || "1" } : null; })();
  const [posForm, setPosForm] = useState<"new" | Position | null>(startPos ? "new" : null);
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
  // «Матрёшка»: открытая вкладка — в адресе (?tab=…). «Назад» браузера возвращает прежнюю вкладку, обновление — остаётся на ней.
  const [sp, setSp] = useSearchParams();
  const urlTab = sp.get("tab") as Tab | null;
  const tabOk = (t: string | null): t is Tab => !!t && ([...SALE_TABS, ...CONTRACT_TABS, "feed", "versions"] as string[]).includes(t) && (t !== "pricing" || isMgmt);
  // «Новый вид»: /deals/:id без ?tab= — обзор сделки (крупные входы в рабочие разделы); «прежний вид» — сразу вкладка по статусу
  const [uiMode] = useUiMode();
  const classic = uiMode === "classic";
  useEffect(() => {     // адрес → вкладка (назад / вперёд / ссылка на ту же сделку)
    if (!deal) return;
    if (tabOk(urlTab)) { if (urlTab !== tab) { if (urlTab === "feed" || urlTab === "versions") setTabState(urlTab); else setTab(urlTab); } return; }
    if (!classic && !urlTab && tab !== "overview") setTabState("overview");     // «Назад» из раздела — к обзору
  }, [urlTab]);
  useEffect(() => {     // вкладка → адрес; первый выбор по статусу не плодит лишнюю запись в истории
    if (!deal) return;
    const n = new URLSearchParams(sp);
    if (tab === "overview") { if (sp.has("tab")) { n.delete("tab"); setSp(n, { replace: !tabOk(urlTab) }); } return; }
    if (urlTab === tab) return;
    n.set("tab", tab);
    setSp(n, { replace: classic && !tabOk(urlTab) });
  }, [tab, !!deal]);
  useEffect(() => {     // переключили на «прежний вид», стоя на обзоре, — открыть вкладку по статусу, как раньше
    if (deal && classic && tab === "overview") setTabState(dealPhase(deal) === "sale" ? "main" : "execution");
  }, [classic, !!deal]);

  const load = () => api.get<Deal>(`/api/deals/${id}`).then((d) => {
    setDeal(d); setWishes(d.client_payment_wishes || "");
    if (!deal) {   // первое открытие — фаза по статусу; ?tab=… из ссылок («Финансы», задачи, уведомления) открывает нужную вкладку
      const p = dealPhase(d); setPhaseState(p); setTabState(classic ? (p === "sale" ? "main" : "execution") : "overview");
      if (wantTab && ([...SALE_TABS, ...CONTRACT_TABS, "feed", "versions"] as string[]).includes(wantTab) && (wantTab !== "pricing" || isMgmt)) setTab(wantTab);
    }
  }).catch((e) => setErr(e.message));
  useEffect(() => { setDeal(null); setFeed([]); setVersions([]); load(); }, [id, me.id]);
  useEffect(() => {
    if (tab === "feed") api.get<typeof feed>(`/api/deals/${id}/feed`).then(setFeed);
    if (tab === "versions") api.get<typeof versions>(`/api/deals/${id}/versions`).then(setVersions);
    if (tab === "catalog") api.get<typeof cand>(`/api/deals/${id}/catalog/candidates`).then(setCand);
  }, [tab, id, me.id]);

  if (!deal) return !err ? <div className="muted">Загрузка…</div> : (
    <div className="card" style={{ maxWidth: 640, margin: "40px auto" }}>
      <h2 style={{ marginTop: 0 }}>Сделка недоступна</h2>
      <p>Сделки с таким адресом нет, она удалена или закрыта для вашей роли. Менеджер видит свои сделки; логист и сервис — сделки, переданные им на расчёт.</p>
      <p className="muted" style={{ fontSize: 13 }}>Если сделка нужна для работы — попросите КД или ЗГД назначить вас или выдать доступ («Настройки → Уровни доступа»). Ответ сервера: {err}</p>
      <div className="row" style={{ gap: 8 }}><button className="btn" onClick={() => nav("/deals")}>К списку сделок</button><button className="btn ghost" onClick={() => nav("/")}>На рабочее место</button></div>
    </div>);
  const isOwner = has("gd") || (has("manager", "cd") && deal.manager_id === me.id);
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
    const checks = deal!.checks || [];
    let body: { force?: boolean; comment?: string } = {};
    if (checks.length) {   // КД / ЗГД — отправка без полностью заполненных обязательных данных
      const items = deal!.check_items || [];
      const list = items.length ? items.map((x) => `• ${x.group}: ${[...x.fields, ...x.tech].join(", ")}`).join("\n") : `• ${checks.join("\n• ")}`;
      const c = (await askPrompt(`Не заполнено:\n${list}\n\nОтправить на расчёт всё равно? Логист и сервис увидят пометку в ленте. Комментарий (необязательно):`, ""));
      if (c === null) return;
      body = { force: true, comment: c };
    }
    try { await api.post(`/api/deals/${deal!.id}/send-to-calc`, body); setErr(""); load(); }
    catch (e) { const d = (e as ApiError).detail as { errors?: string[] }; setErr(d?.errors ? d.errors.join(" ") : (e as Error).message); }
  }
  async function removeOrArchive() {
    const draft = deal!.status === "draft";
    const reason = (await askPrompt(draft ? `Удалить черновик ${deal!.number}? Сделка пропадёт из списков и задач. Укажите причину:` : `Перенести сделку ${deal!.number} в архив? Её можно будет открыть и вернуть из архива. Укажите причину:`));
    if (reason === null) return;
    if (!reason.trim()) { setErr("Укажите причину"); return; }
    try {
      await api.post(`/api/deals/${deal!.id}/delete`, { reason });
      if (draft) nav("/deals"); else { setErr("Сделка перенесена в архив"); load(); }
    } catch (e) { setErr((e as Error).message); }
  }
  async function unarchive() {
    try { await api.post(`/api/deals/${deal!.id}/unarchive`); setErr("Сделка возвращена из архива"); load(); } catch (e) { setErr((e as Error).message); }
  }
  async function duplicate() {
    const comment = (await askPrompt("Дублировать заявку (новая заявка того же клиента: требования, технические характеристики, изображения, расчёты логиста и сервиса с пометкой «проверить»). Комментарий:")) ?? null;
    if (comment === null) return;
    try { const d = await api.post<Deal>(`/api/deals/${deal!.id}/duplicate`, { copy_calcs: true, comment }); nav(`/deals/${d.id}`); } catch (e) { setErr((e as Error).message); }
  }
  const posTerm = (p: Position) => {
    const t = p.production_term || {};
    if (t.unit !== "date" && !t.value) return "из ответа завода";
    const prod = t.unit === "date" ? `до ${fmtDate(t.fixed_date)}` : `${t.value} ${TERM_UNITS[t.unit || "calendar_days"]}`;
    return `${prod} ${PRODUCTION_START[t.start_event || "factory_contract"]}${t.fixed ? " · зафиксирован КД / ЗГД" : ""}`;
  };

  const phaseStart: Tab = phase === "sale" ? "main" : "execution";
  // доступные разделы сделки — один список и для строки вкладок, и для обзора (условия показа прежние)
  const saleTabs = ([["main", true], ["positions", true], ["offers", true], ["logistics", has("logistician", "cd", "fd", "gd")], ["service", !!deal.service_flag && has("service_head", "cd", "fd", "gd")],
    ["pricing", isMgmt], ["catalog", ["kp_sent", ...POST_WIN_STATUSES, "lost"].includes(deal.status) && (isOwner || has("cd"))]] as [Tab, boolean][]).filter((x) => x[1]).map((x) => x[0]);
  const contractTabs = ([["execution", true], ["acceptance", ["contract", "execution", "closed"].includes(deal.status) && has("manager", "service_head", "cd", "gd", "fd", "logistician")],
    ["planfact", true], ["documents", can(me, "legal_docs")], ["legal", can(me, "legal_docs")]] as [Tab, boolean][]).filter((x) => x[1]).map((x) => x[0]);
  const historyTabs = ([["feed", true], ["versions", true], ["documents", !POST_WIN_STATUSES.includes(deal.status) && can(me, "legal_docs")]] as [Tab, boolean][]).filter((x) => x[1]).map((x) => x[0]);
  const missing = (t: Tab) => (["draft", "rework"].includes(deal.status) ? (deal.check_items || []).filter((x) => x.tab === t) : []);
  const entryOf = (t: Tab): Entry => {
    const miss = missing(t);
    const stats: NonNullable<Entry["stats"]> = [];
    if (t === "positions") stats.push({ value: (deal.positions || []).length, label: "позиций" }, { value: (deal.positions || []).reduce((a, p) => a + (Number(p.qty) || 0), 0), label: "машин" });
    if (t === "offers" && deal.has_alternatives) stats.push({ value: (deal.variants || []).length, label: "предложений в КП" });
    if (t === "pricing") stats.push({ value: deal.current_version_no, label: "версия расчёта" });
    if (t === "execution" && deal.contract_number) stats.push({ value: deal.contract_number, label: "контракт с клиентом" });
    if (t === "execution" && deal.factory_contract_number) stats.push({ value: deal.factory_contract_number, label: "контракт с заводом" });
    // D-116: обзор показывает состояние, а не только описание раздела — где сейчас работа по сделке и чей ход
    const nt = deal.now?.task, nb = nt?.block || "";
    const nowTab: Tab | null = !nt ? null : nb.startsWith("stage:") || nb === "contract" ? "execution" : nb === "logistics" ? "logistics" : nb === "service" ? "service" : ["pricing", "visa", "gd", "kp"].includes(nb) ? "pricing" : "main";
    const here = nowTab === t && nt ? { label: <b>Сейчас: {nt.title}</b>, sub: `${nt.who} · срок ${fmtDate(nt.due)}${nt.overdue_days > 0 ? ` · просрочено ${nt.overdue_days} дн.` : ""}`, to: `/deals/${deal.id}?tab=${t}`, tone: nt.overdue_days > 0 ? "bad" : "info" } : null;
    return { key: t, title: TAB_TITLES[t], to: `/deals/${deal.id}?tab=${t}`, hint: here ? undefined : TAB_HINTS[t], stats, top: here,
      attention: miss.length ? `не заполнено: ${miss.reduce((a, x) => a + x.fields.length + x.tech.length, 0)}` : undefined };
  };
  return (
    <>
      <Crumbs items={tab === "overview" ? [{ label: "Рабочее место", to: "/" }, { label: "Сделки", to: "/deals" }, { label: deal.number }]
        : [{ label: "Рабочее место", to: "/" }, { label: "Сделки", to: "/deals" }, { label: deal.number, to: classic ? `/deals/${deal.id}?tab=${phaseStart}` : `/deals/${deal.id}` },
          ...(POST_WIN_STATUSES.includes(deal.status) && tab !== "feed" && tab !== "versions" ? [{ label: phase === "sale" ? "Продажа" : "Контракт и исполнение", to: `/deals/${deal.id}?tab=${phaseStart}` }] : []),
          { label: TAB_TITLES[tab] }]} />
      <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <div className="row" style={{ gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
            <h1 style={{ margin: 0 }}>{deal.number}</h1>
            <span style={{ fontSize: 15 }}>{deal.client_name}{deal.site && <span className="muted"> · {deal.site}</span>}</span>
            <span className="badge" style={{ background: toneOf(deal.status)[0], color: toneOf(deal.status)[1], fontWeight: 600 }}>{deal.status_detail || STATUS_TITLES[deal.status]}</span>
            {deal.parent_deal_id && <span className="badge">дубликат</span>}
            {deal.legal_review?.status && <span className={"badge " + (REVIEW_BADGE[deal.legal_review.status] || "")} title={deal.legal_review.comment || ""}>юрист: {{ in_review: "на проверке", ok: "замечаний нет", remarks: "есть замечания" }[deal.legal_review.status] || deal.legal_review.status}</span>}
          </div>
          <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>
            {(deal.positions || []).slice(0, 3).map((p) => `${p.qty} × ${designation(p)}`).join(" · ")}{(deal.positions || []).length > 3 && ` · ещё ${deal.positions.length - 3}`}
            {!POST_WIN_STATUSES.includes(deal.status) && <> · срок КП {fmtDate(deal.kp_due)}</>} · версия {deal.current_version_no}
            {deal.contract_number && ` · контракт ${deal.contract_number}`}{deal.factory_contract_number && ` · с заводом ${deal.factory_contract_number}`}
          </div>
        </div>
        <div className="row" style={{ flexWrap: "wrap", justifyContent: "flex-end" }}>
          {(editable || has("cd", "gd")) && ["draft", "rework"].includes(deal.status) && (
            <button className="btn" onClick={send} disabled={(deal.checks || []).length > 0 && !has("cd", "gd")} title={(deal.checks || []).join("\n")}>
              Отправить на расчёт{(deal.checks || []).length > 0 && has("cd", "gd") ? " (не всё заполнено)" : ""}</button>
          )}
          {(isOwner || has("cd")) && <button className="btn ghost" onClick={duplicate} title="Задача 9: новая заявка по тому же запросу клиента">Дублировать заявку</button>}
          {has("cd", "gd") && !deal.archived_at && (has("gd") || !["contract", "execution", "closed"].includes(deal.status)) && (
            <button className="btn secondary" onClick={removeOrArchive} title={deal.status === "draft" ? "удалить черновик сделки (КД, ЗГД)" : "удалить можно только черновик — сделка в другом статусе переносится в архив"}>
              {deal.status === "draft" ? "Удалить сделку" : "В архив"}</button>)}
          <div className="row" style={{ gap: 8, marginLeft: 6, alignItems: "center" }}>
            <ActionMenu label="Связано" onPick={(u) => nav(u)} items={[
              ...(deal.client_id ? [{ title: `Клиент ${deal.client_name}`, url: `/legal?tab=counterparties&type=clients&client=${deal.client_id}` }] : []),
              ...(can(me, "finance_section") ? [{ title: "Финансы по сделке", url: `/finance?tab=calendar&deal_id=${deal.id}` }] : []),
              ...(can(me, "legal_docs") ? [POST_WIN_STATUSES.includes(deal.status) ? { title: "Договоры и юридические документы", url: `/deals/${deal.id}?tab=legal` } : { title: "Документы клиента и юридические", url: `/deals/${deal.id}?tab=documents` }] : []),
              { title: "Реестр договоров", url: "/legal?tab=contracts" }, { title: "Мои задачи", url: "/tasks" }]} />
            <a href="#" style={{ fontSize: 12, fontWeight: tab === "feed" ? 700 : 400 }} onClick={(e) => { e.preventDefault(); setTabState("feed"); }}>Лента</a>
            <a href="#" style={{ fontSize: 12, fontWeight: tab === "versions" ? 700 : 400 }} onClick={(e) => { e.preventDefault(); setTabState("versions"); }}>Версии и файлы</a>
            {phase === "sale" && can(me, "legal_docs") && <a href="#" style={{ fontSize: 12, fontWeight: tab === "documents" ? 700 : 400 }} onClick={(e) => { e.preventDefault(); setTabState("documents"); }}>Документы</a>}
          </div>
        </div>
      </div>
      <div className="dh-box">
        <StageStrip deal={deal} now={deal.now} />
        <NowLine now={deal.now} onGo={(block) => {
          if (block.startsWith("stage:")) setTab("execution");
          else if (block === "logistics") setTab("logistics"); else if (block === "service") setTab("service");
          else if (["pricing", "visa", "gd"].includes(block)) setTab("pricing"); else if (block === "kp") setTab("pricing");
          else if (block === "contract") setTab("execution"); else setTab("main"); }} />
      </div>
      {deal.archived_at && (
        <div className="alert warn row" style={{ justifyContent: "space-between" }}>
          <span>Сделка в архиве с {fmtDate(deal.archived_at)}{deal.archive_reason ? `: ${deal.archive_reason}` : ""}</span>
          {has("cd", "gd") && <button className="btn ghost" onClick={unarchive}>Вернуть из архива</button>}
        </div>)}
      {err && <div className={"alert " + (err.startsWith("Сделка выиграна") || err.startsWith("Результат") || err.startsWith("Сделка возвращена") || err.startsWith("Сделка перенесена") || err.startsWith("Статус уточнён") ? "info" : "error")}>{err}</div>}
      {phase === "sale" && <WaitingPanel deal={deal} me={me} onDone={(m) => { setErr(m); load(); }} />}
      <OutcomePanel deal={deal} me={me} onDone={(m) => { setErr(m); load(); if (m.startsWith("Сделка выиграна")) setTab("execution"); }} />
      {phase === "sale" && <div style={{ margin: "6px 0" }}><KpActions dealId={deal.id} status={deal.status} canAct={isOwner || has("cd", "gd", "admin")} canMark={isOwner || has("gd")} onDone={() => { load(); }} /></div>}
      {["approved", "kp_sent"].includes(deal.status) && <KpRevisions dealId={deal.id} status={deal.status} onDone={() => { load(); }} readonly collapsible />}
      {(editable || has("cd", "gd")) && ["draft", "rework"].includes(deal.status) && (deal.check_items || []).length > 0 &&
        <ChecksPanel items={deal.check_items!} onGo={(it) => { setTab(it.tab as Tab); if (it.position_id) openPos(it.position_id); }} />}
      <RelatedDeals dealId={deal.id} me={me} />

      {(POST_WIN_STATUSES.includes(deal.status)) && (
        <div className="row" style={{ marginTop: 10, gap: 12, alignItems: "flex-end", flexWrap: "wrap" }}>
          <div style={{ display: "flex", background: "#e1e4e8", borderRadius: 8, padding: 3 }}>
            {(["sale", "contract"] as Phase[]).map((p) => (
              <button key={p} onClick={() => setPhase(p)} style={{ border: 0, cursor: "pointer", padding: "6px 16px", borderRadius: 6, fontSize: 13,
                background: phase === p ? "#fff" : "transparent", color: phase === p ? "inherit" : "var(--muted)", fontWeight: phase === p ? 700 : 400, boxShadow: phase === p ? "0 1px 2px rgba(0,0,0,.08)" : "none" }}>
                {p === "sale" ? "Продажа" : "Контракт и исполнение"}</button>))}
          </div>
          {phase === "contract" && <SaleSummary now={deal.now} />}
          {phase === "sale" && <span className="muted" style={{ fontSize: 12 }}>фаза продажи закрыта — данные только для чтения</span>}
        </div>)}
      <div className="tabs">
        {!classic && <button className={tab === "overview" ? "active" : ""} onClick={() => setTabState("overview")}>Обзор</button>}
        {(phase === "sale" ? saleTabs : contractTabs).map((t) => <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>{TAB_TITLES[t]}</button>)}
      </div>
      {tab === "overview" && (
        <>
          {POST_WIN_STATUSES.includes(deal.status) && <h2 style={{ margin: "4px 0 8px" }}>Контракт и исполнение</h2>}
          {POST_WIN_STATUSES.includes(deal.status) && <EntryGrid entries={contractTabs.map(entryOf)} />}
          {can(me, "price") && <CurrentOfferPanel dealId={deal.id} onOpen={saleTabs.includes("pricing") ? () => setTab("pricing") : undefined} />}
          <h2 style={{ margin: "4px 0 8px" }}>Продажа{POST_WIN_STATUSES.includes(deal.status) && <span className="muted" style={{ fontSize: 13, fontWeight: 400 }}> · фаза закрыта, данные только для чтения</span>}</h2>
          <EntryGrid entries={saleTabs.map(entryOf)} />
          <h2 style={{ margin: "4px 0 8px" }}>История и файлы</h2>
          <EntryGrid entries={historyTabs.map(entryOf)} />
        </>)}

      {tab === "main" && (
        <div className="grid cols-2">
          <div className="card">
            <h2 style={{ marginTop: 0 }}>Шапка</h2>
            <table><tbody>
              <tr><td>Клиент</td><td>{deal.client_name}</td></tr><tr><td>Контактное лицо</td><td>{deal.contact}</td></tr>
              <tr><td>Дата запроса</td><td>{fmtDate(deal.requested_at)}</td></tr>
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
            <div className="section-head">
              <h2>Позиции</h2>
              {editable && <button className="btn add" onClick={() => setPosForm("new")}>Добавить вручную / из каталога</button>}
            </div>
            <table>
              <thead><tr><th>Завод</th><th>Тип машины</th><th>Марка и модель</th><th className="num">Кол-во</th><th className="num">Цена за ед.</th><th>Валюта</th><th className="num">Опции</th><th>Базис</th><th>Срок производства</th><th>Срок клиенту</th><th>Справка по цене</th><th>Источник</th>{editable && <th />}</tr></thead>
              <tbody>{(deal.positions || []).map((p) => {
                const pr = (p.price_reference || {}) as { last_price?: number; deviation_pct?: number; comment?: string };
                const dt = p.delivery_term || {};
                return (<tr key={p.id} style={multi && shownPos?.id === p.id ? { background: "var(--accent-soft)" } : undefined}><td>{p.factory_id ? factories[p.factory_id] || p.factory_id.slice(0, 8) : <span className="muted">не выбран</span>}</td><td>{p.machine_type}</td><td>{multi ? <a href={"#pos-" + p.id} title="открыть технические характеристики позиции" onClick={(e) => { e.preventDefault(); openPos(p.id); }}
                    style={{ fontWeight: shownPos?.id === p.id ? 700 : 400 }}><b>{designation(p) || "модель — из ответа завода"}</b></a> : designation(p) ? <b>{designation(p)}</b> : <span className="muted">из ответа завода</span>}{p.execution_id && <span className="badge" title="из каталога"> каталог</span>}</td><td className="num">{p.qty}</td>
                  <td className="num">{p.unit_price ? fmtRub(p.unit_price) : <span className="muted">—</span>}</td><td>{p.currency}</td><td className="num">{fmtRub(p.options_price)}</td><td>{p.basis}</td><td>{posTerm(p)}</td>
                  <td className="muted">{TERM_UNITS[dt.unit || "calendar_days"]} {DELIVERY_START[dt.start_event || "client_contract"]} (+10 %, до недели)</td>
                  <td>{pr.last_price ? <>посл. {fmtRub(pr.last_price)} ({pr.deviation_pct} %) {pr.comment && <span className="muted">— {pr.comment}</span>}</> : <span className="muted">нет истории</span>}</td>
                  <td>{p.rfq_import_id ? <span className="badge accent">RFQ</span> : p.offer_id ? <span className="badge">ответ завода</span> : <span className="badge">вручную</span>}</td>
                  {editable && <td className="row"><button className="btn ghost" onClick={() => setPosForm(p)}>изменить</button>
                    <button className="btn ghost" onClick={async () => { if ((await askConfirm(`Удалить позицию ${designation(p)}?`))) api.del(`/api/deals/${deal.id}/positions/${p.id}`).then(load); }}>удалить</button></td>}</tr>);
              })}</tbody>
            </table>
          </div>
          {editable && posForm && (
            <PositionForm dealId={deal.id} position={posForm === "new" ? undefined : posForm} initial={posForm === "new" && startPos ? startPos : undefined}
              onDone={() => { setPosForm(null); if (startPos) window.history.replaceState(null, "", window.location.pathname); load(); }} onCancel={() => setPosForm(null)} />
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
              <div style={{ marginTop: 14 }}><ZipBlock dealId={deal.id} positionId={p.id} canEdit={has("manager", "service_head", "cd", "gd")} /></div>
              <div style={{ marginTop: 14 }}><ModelDocsBlock dealId={deal.id} positionId={p.id} canUpload={!me.roles.every((r) => r.code === "observer")} canRemove={has("cd", "gd")} /></div>
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
      {tab === "acceptance" && <AcceptanceTab dealId={deal.id} />}
      {tab === "planfact" && <PlanFactTab key={me.id} deal={deal} />}
      {tab === "documents" && <DocumentsTab deal={deal} me={me} onChanged={load} />}
      {tab === "legal" && <LegalDocsTab deal={deal} me={me} reload={load} />}

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
          {feed.map((f) => <div key={f.id} style={{ borderBottom: "1px solid var(--line)", padding: "6px 0" }}><span className="badge">{lbl(FEED_KIND, f.kind)}</span> <b>{f.subject}</b> <span className="muted">{f.counterparty} · {new Date(f.at).toLocaleString("ru-RU")}</span><div>{f.body}</div></div>)}
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
      <table><thead><tr><th>Код</th><th>Параметр</th><th>Значение</th><th>Ед.</th><th>Для логиста</th></tr></thead>
        <tbody>{Object.entries(fields).map(([code, f]) => (
          <tr key={code} style={{ background: missing.includes(code) ? "#ffe3e3" : undefined }}><td>{code}</td><td>{f.label_ru}</td><td>{f.value == null ? <span className="badge bad">пусто</span> : String(f.value)}</td><td>{f.unit}</td><td>{f.is_l ? "да" : ""}</td></tr>))}</tbody></table>
    </div>
  );
}
