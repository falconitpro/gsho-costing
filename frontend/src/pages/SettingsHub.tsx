import { useLocation, useNavigate } from "react-router-dom";
import type { Me } from "../types";
import { MarkupsBlock } from "./PartsSetupPage";
import ChainRulesBlock from "../components/ChainRulesBlock";
import SettingsPage from "./SettingsPage";
import TeamPage from "./TeamPage";
import AccessPage from "./AccessPage";
import AdminPage from "./AdminPage";
import BackupStatusBlock from "../components/BackupStatusBlock";
import AppErrorsPage from "../components/AppErrorsPage";
import SecuritySettingsPage from "./SecuritySettingsPage";
import SecurityAdminPage from "./SecurityAdminPage";
import AccessReviewPage from "./AccessReviewPage";
import SecurityAuditPage from "./SecurityAuditPage";
import { WorkplaceSettings } from "./WorkplacePage";
import { Crumbs, EntryGrid } from "../components/Levels";
import { useNewUiFlag } from "../uiMode";
import { useEffect, useRef } from "react";
import { useAutoTableCards } from "../components/useTableLabels";

// «Настройки» одним разделом: ставки и нормативы, сотрудники, уровни доступа, администрирование.
// Доступ к каждому блоку — как и раньше (уровни доступа и администрирование — ЗГД / админ / ФД); блок виден только тем, кому он доступен.

type Sec = "workplace" | "security" | "rates" | "team" | "access" | "admin" | "security_admin" | "access_review" | "security_audit" | "backup" | "app_errors";

const SEC_HINT: Record<Sec, string> = {
  workplace: "блоки главной страницы: порядок, видимость, обязательные по ролям",
  security: "пароль, вход с кодом из приложения, активные входы",
  rates: "ставки и нормативы, наценки между компаниями группы, правила внутригрупповых цепочек",
  team: "сотрудники, роли, пароли для входа",
  access: "уровни доступа по разделам и исключения",
  admin: "сотрудники и роли, курсы валют, шаблоны, журналы действий и интеграций, точность оценок, просрочки",
  security_admin: "безопасность учётных записей сотрудников",
  access_review: "периодический пересмотр прав сотрудников",
  security_audit: "журнал безопасности и предупреждения",
  backup: "состояние резервных копий",
  app_errors: "сбои сервера и страниц у сотрудников",
};

// Новый вид (G-01): секции сгруппированы по смыслу; состав и видимость секций — те же, что выше
const GROUPS: [string, Sec[]][] = [
  ["Личное", ["workplace", "security"]],
  ["Команда и доступ", ["team", "access", "access_review", "security_admin"]],
  ["Компания", ["rates", "admin"]],
  ["Контроль и надёжность", ["security_audit", "backup", "app_errors"]],
];

// секции со своим заголовком в карточке — общий заголовок над ними не дублируем
const OWN_TITLE: Sec[] = ["security_admin", "access_review", "security_audit", "backup"];

export default function SettingsHub({ me }: { me: Me }) {
  const loc = useLocation();
  const nav = useNavigate();
  const has = (...codes: string[]) => me.roles.some((r) => codes.includes(r.code));
  const perm = (g: string, lvl: number) => (me.perms?.[g] ?? 0) >= lvl;
  const nv = useNewUiFlag();
  const bodyRef = useRef<HTMLDivElement>(null);
  const navRef = useRef<HTMLElement>(null);
  useAutoTableCards(bodyRef, nv, loc.search);
  // на телефоне и планшете разделы — строкой с прокруткой: открытый раздел всегда в поле зрения
  useEffect(() => { navRef.current?.querySelector(".st-navitem.on")?.scrollIntoView?.({ block: "nearest", inline: "center" }); }, [loc.search]);
  const sections: [Sec, string, boolean][] = [
    ["workplace", "Рабочее место", true],
    ["security", "Безопасность", true],
    ["rates", "Ставки и нормативы", has("admin", "fd", "gd", "cd")],
    ["team", "Сотрудники", true],
    ["access", "Уровни доступа", perm("access_admin", 2)],
    ["admin", "Администрирование", has("admin", "gd", "fd")],
    ["security_admin", "Безопасность сотрудников", perm("security_admin", 1)],
    ["access_review", "Пересмотр прав", perm("security_admin", 1)],
    ["security_audit", "Журнал и предупреждения", perm("security_audit", 1)],
    ["backup", "Резервные копии", perm("backup_status", 1)],
    ["app_errors", "Ошибки системы", perm("security_audit", 1)],
  ];
  const visible = sections.filter(([, , ok]) => ok);
  const want = new URLSearchParams(loc.search).get("s") as Sec | null;
  // «Матрёшка»: /settings без ?s= — первый уровень, крупные категории, доступные пользователю; ?s=… — выбранная секция (как раньше)
  if ((!want || !visible.some(([k]) => k === want))) {
    return (
      <>
        <Crumbs items={[{ label: "Рабочее место", to: "/" }, { label: "Настройки" }]} />
        <div className="page-head"><h1>Настройки</h1>{want && <span className="badge warn">секция «{want}» недоступна — выберите другую</span>}</div>
        {nv ? GROUPS.map(([g, keys]) => {
          const items = visible.filter(([k]) => keys.includes(k));
          return items.length ? <section key={g} className="st-hubgroup"><h2 className="st-hubtitle">{g}</h2>
            <EntryGrid entries={items.map(([k, t]) => ({ key: k, title: t, to: `/settings?s=${k}`, hint: SEC_HINT[k] }))} /></section> : null;
        }) : <EntryGrid entries={visible.map(([k, t]) => ({ key: k, title: t, to: `/settings?s=${k}`, hint: SEC_HINT[k] }))} />}
      </>);
  }
  const cur: Sec = visible.some(([k]) => k === want) ? (want as Sec) : visible.find(([k]) => k !== "workplace" && k !== "security")?.[0] || "workplace";
  const curTitle = visible.find(([k]) => k === cur)?.[1] || "";
  const content = (<>
      {cur === "workplace" && <WorkplaceSettings />}
      {cur === "rates" && <><SettingsPage me={me} embedded />{has("cd", "fd", "gd") && <div className="card"><h2 style={{ marginTop: 0 }}>Наценки между компаниями группы</h2><MarkupsBlock me={me} /></div>}
        {has("cd", "fd", "gd") && <div className="card"><h2 style={{ marginTop: 0 }}>Правила внутригрупповых цепочек</h2><ChainRulesBlock /></div>}</>}
      {cur === "team" && <TeamPage me={me} embedded />}
      {cur === "access" && <AccessPage me={me} embedded />}
      {cur === "admin" && <AdminPage me={me} embedded />}
      {cur === "backup" && <BackupStatusBlock />}
      {cur === "app_errors" && <AppErrorsPage />}
      {cur === "security" && <SecuritySettingsPage me={me} onChanged={() => window.dispatchEvent(new CustomEvent("gsho:security-gate"))} />}
      {cur === "security_admin" && <SecurityAdminPage meId={me.id} />}
      {cur === "access_review" && <AccessReviewPage />}
      {cur === "security_audit" && <SecurityAuditPage />}
  </>);
  if (nv) {
    return (
      <>
        <Crumbs items={[{ label: "Рабочее место", to: "/" }, { label: "Настройки", to: "/settings" }, { label: curTitle }]} />
        <div className="page-head"><h1>Настройки</h1></div>
        <div className="st-layout">
          <nav className="st-nav" aria-label="Разделы настроек" ref={navRef}>
            {GROUPS.map(([g, keys]) => {
              const items = visible.filter(([k]) => keys.includes(k));
              return items.length ? <div key={g} className="st-navgroup"><div className="st-navtitle">{g}</div>
                {items.map(([k, t]) => <button key={k} type="button" className={"st-navitem" + (cur === k ? " on" : "")} aria-current={cur === k ? "page" : undefined} onClick={() => nav(`/settings?s=${k}`)}>{t}</button>)}
              </div> : null;
            })}
          </nav>
          <div className="st-body" ref={bodyRef}>
            {!OWN_TITLE.includes(cur) && <div className="st-bodyhead"><h2>{curTitle}</h2><span className="muted">{SEC_HINT[cur]}</span></div>}
            {content}
          </div>
        </div>
      </>);
  }
  return (
    <>
      {<Crumbs items={[{ label: "Рабочее место", to: "/" }, { label: "Настройки", to: "/settings" }, { label: curTitle }]} />}
      <div className="row" style={{ alignItems: "baseline", gap: 16, flexWrap: "wrap", marginBottom: 10 }}>
        <h1 style={{ margin: 0 }}>Настройки</h1>
        <div className="row" style={{ gap: 2, borderBottom: "1px solid var(--line)" }}>
          {visible.map(([k, t]) => (
            <button key={k} onClick={() => nav(`/settings?s=${k}`)} style={{ border: 0, background: "transparent", cursor: "pointer", padding: "8px 14px", fontSize: 15, fontFamily: "inherit",
              fontWeight: cur === k ? 700 : 500, color: cur === k ? "var(--ink)" : "var(--muted)", borderBottom: cur === k ? "3px solid var(--accent)" : "3px solid transparent", marginBottom: -1 }}>{t}</button>))}
        </div>
      </div>
      {content}
    </>
  );
}
