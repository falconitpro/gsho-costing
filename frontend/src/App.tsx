import AssistantDrawer from "./components/AssistantDrawer";
import { SupportAdmin, SupportButton, SupportList, SupportTicketRoute } from "./pages/SupportPage";
import { useEffect, useState } from "react";
import FloatingScrollbar from "./components/FloatingScrollbar";
import NotificationBell from "./components/NotificationBell";
import InfoBar from "./components/InfoBar";
import { BrowserRouter, NavLink, Navigate, Route, Routes, useLocation, useNavigate, useParams } from "react-router-dom";
import { api, setDevUser } from "./api";
import type { Me } from "./types";
import DealsList from "./pages/DealsList";
import WorkplacePage from "./pages/WorkplacePage";
import DealCard from "./pages/DealCard";
import TasksPage from "./pages/TasksPage";
import SummaryPage from "./pages/SummaryPage";
import FinanceQuestionPage from "./pages/FinanceQuestionPage";
import FinancePage from "./pages/FinancePage";
import BoardPage from "./pages/BoardPage";
import SettingsHub from "./pages/SettingsHub";
import SecuritySettingsPage from "./pages/SecuritySettingsPage";
import StepUpDialog from "./components/StepUpDialog";
import SessionExpiryWarning from "./components/SessionExpiryWarning";
import CounterpartiesPage from "./pages/CounterpartiesPage";
import LegalPage from "./pages/LegalPage";
import CatalogHub from "./pages/CatalogHub";
import SupplyPage from "./pages/PartsSetupPage";
import { PartsRequestView } from "./components/PartsRequests";
import LoginPage from "./pages/LoginPage";

export const MeContext = { current: null as Me | null };

function PartsRequestRoute({ me }: { me: Me }) {
  const { id } = useParams();
  return <PartsRequestView me={me} id={id || ""} />;
}

/** Неавторизованный пользователь → страница входа с запомненным адресом; после входа — возврат на него. */
function RequireLogin() {
  const loc = useLocation();
  const next = loc.pathname + loc.search;
  return <Navigate to={`/login${next && next !== "/" ? `?next=${encodeURIComponent(next)}` : ""}`} replace />;
}
function AfterLogin() {
  const loc = useLocation();
  const next = new URLSearchParams(loc.search).get("next") || "/";
  return <Navigate to={next.startsWith("/") && !next.startsWith("//") ? next : "/"} replace />;
}
function LoginRoute({ onLoggedIn }: { onLoggedIn: () => Promise<void> }) {
  const nav = useNavigate();
  const loc = useLocation();
  const next = new URLSearchParams(loc.search).get("next") || "/";
  return <LoginPage onLoggedIn={async () => { setDevUser(""); await onLoggedIn(); nav(next.startsWith("/") && !next.startsWith("//") ? next : "/", { replace: true }); }} />;
}

export default function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [checked, setChecked] = useState(false);          // сессия проверена: до этого ничего не показываем, чтобы рабочее место не мелькало
  const [aiOpen, setAiOpen] = useState(false);            // помощник CRM: открыт ли (черновик сохраняется, пока вкладка открыта)
  const [devUsers, setDevUsers] = useState<{ email: string; display_name: string; roles: string[] }[]>([]);

  async function load() {
    try {
      const m = await api.get<Me>("/api/auth/me");
      setMe(m);
      MeContext.current = m;
      api.get<typeof devUsers>("/api/auth/dev-users").then(setDevUsers).catch(() => setDevUsers([]));   // тестовый стенд: переключатель учётных записей в шапке
    } catch (e) {
      setMe(null);
      MeContext.current = null;
      void e;
    } finally {
      setChecked(true);
    }
  }
  async function logout() {
    try { await api.post("/api/auth/logout"); } catch { /* сессии уже нет */ }
    setDevUser("");
    setMe(null);
    MeContext.current = null;
  }
  useEffect(() => {
    setDevUser("");          // заголовок X-Dev-User нужен только на один запрос переключения — дальше работает cookie сессии
    load();
    const onUnauth = () => { setDevUser(""); setMe(null); MeContext.current = null; };
    const onGate = () => { load(); };          // смена пароля / настройка TOTP / требование сервера — перечитать профиль
    window.addEventListener("gsho:unauthorized", onUnauth);
    window.addEventListener("gsho:security-gate", onGate);
    return () => { window.removeEventListener("gsho:unauthorized", onUnauth); window.removeEventListener("gsho:security-gate", onGate); };
  }, []);
  if (!checked) return null;
  if (!me) {
    return (
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginRoute onLoggedIn={load} />} />
          <Route path="*" element={<RequireLogin />} />
        </Routes>
      </BrowserRouter>);
  }

  // временный пароль или обязательный TOTP: пока не выполнено — только этот экран и «Выйти» (сервер закрывает остальное сам)
  const gate = me.security?.password_must_change ? "password" : me.security?.mfa_setup_required ? "mfa" : null;
  if (gate) {
    return (
      <BrowserRouter>
        <header className="top"><div className="top-row">
          <span className="brand"><img src="/brand/logo-main-96.png" srcSet="/brand/logo-main.png 2x" alt="Berghaus" /></span>
          <div className="account"><span className="muted">{me.display_name}</span><button className="btn ghost" style={{ padding: "4px 10px" }} onClick={logout}>Выйти</button></div>
        </div></header>
        <main><SecuritySettingsPage me={me} focus={gate} onChanged={load} /></main>
      </BrowserRouter>);
  }

  const has = (...roles: string[]) => !!me && me.roles.some((r) => roles.includes(r.code));
  const perm = (g: string, level = 1) => !!me && (me.perms?.[g] ?? 0) >= level;

  return (
    <BrowserRouter>
      <header className="top">
        <div className="top-row">
          <NavLink to="/" className="brand">
            <img src="/brand/logo-main-96.png" srcSet="/brand/logo-main.png 2x" alt="Berghaus" />
          </NavLink>
          <InfoBar />
          <div className="account">
            {devUsers.length > 0 && (
              <label className="dev-switch" title="тестовый стенд: посмотреть систему под другой учётной записью">
                <span>Войти как</span>
                <select value={me.email} onChange={async (e) => { setDevUser(e.target.value); await load(); setDevUser(""); }}>
                  {devUsers.map((u) => (
                    <option key={u.email} value={u.email}>{u.display_name}</option>
                  ))}
                </select>
              </label>
            )}
            {me && perm("assistant_use") && <button className="btn ghost ai-btn" onClick={() => setAiOpen(!aiOpen)} aria-expanded={aiOpen} title="Помощник CRM">Помощник</button>}
            {me && <SupportButton />}
            {me && <NotificationBell />}
            {me && (
              <div className="me" title={`${me.display_name} — ${me.roles.map((r) => r.title).join(", ")}`}>
                <span className="avatar">{me.display_name.replace(/\(.*\)/, "").trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase()}</span>
                <span className="who"><b>{me.display_name.replace(/\s*\(.*\)\s*$/, "")}</b><small>{me.roles.map((r) => r.title).join(", ")}</small></span>
              </div>)}
            {me && <button className="btn ghost" style={{ padding: "4px 10px" }} onClick={logout} title="Выйти из системы">Выйти</button>}
          </div>
        </div>
        {me && (
          <nav>
            <NavLink to="/" end>Рабочее место</NavLink>
            <NavLink to="/deals">Сделки</NavLink>
            <NavLink to="/tasks">Мои задачи</NavLink>
            {has("cd", "fd", "gd", "observer") && <NavLink to="/board">Доска</NavLink>}
            {has("cd", "fd", "gd", "logistician", "service_head", "purchasing", "manager") && <NavLink to="/summary">Сводка</NavLink>}
            {perm("finance_section") && <NavLink to="/finance">Финансы</NavLink>}
            <NavLink to="/counterparties">Контрагенты</NavLink>
            {has("cd", "fd", "gd", "lawyer", "admin", "manager") ? <NavLink to="/legal">Юридический блок</NavLink> : !has("observer") && <NavLink to="/legal?tab=contracts">Договоры</NavLink>}

            <NavLink to="/catalog">Каталог</NavLink>
            {has("cd", "fd", "gd", "admin", "logistician", "manager", "purchasing", "service_head") && <NavLink to="/supply">Снабжение и склад</NavLink>}
            <NavLink to="/settings">Настройки</NavLink>
          </nav>
        )}
      </header>
      <FloatingScrollbar />
      <StepUpDialog />
      {me && perm("assistant_use") && <AssistantDrawer open={aiOpen} onClose={() => setAiOpen(false)} />}
      <SessionExpiryWarning />
      <main>
        {me && (
          <Routes>
            <Route path="/login" element={<AfterLogin />} />
            <Route path="/" element={<WorkplacePage me={me} />} />
            <Route path="/deals" element={<DealsList me={me} />} />
            <Route path="/deals/parts/:id" element={<PartsRequestRoute me={me} />} />
            <Route path="/deals/:id" element={<DealCard me={me} />} />
            <Route path="/tasks" element={<TasksPage />} />
            <Route path="/finance/questions/:id" element={<FinanceQuestionPage />} />
            <Route path="/board" element={<BoardPage />} />
            <Route path="/summary" element={<SummaryPage me={me} />} />
            <Route path="/finance" element={<FinancePage me={me} />} />
            <Route path="/counterparties" element={<CounterpartiesPage me={me} />} />
            <Route path="/legal" element={<LegalPage me={me} />} />
            <Route path="/clients" element={<Navigate to="/counterparties?type=clients" replace />} />

            <Route path="/team" element={<Navigate to="/settings?s=team" replace />} />
            <Route path="/settings" element={<SettingsHub me={me} />} />
            <Route path="/support" element={<SupportList />} />
            <Route path="/support/admin" element={<SupportAdmin />} />
            <Route path="/support/:id" element={<SupportTicketRoute />} />
            <Route path="/catalog" element={<CatalogHub me={me} />} />
            <Route path="/factories" element={<Navigate to="/counterparties?type=suppliers" replace />} />
            <Route path="/supply" element={<SupplyPage me={me} />} />
            <Route path="/parts/setup" element={<Navigate to="/supply" replace />} />
            <Route path="/access" element={<Navigate to="/settings?s=access" replace />} />
            <Route path="/admin" element={<Navigate to="/settings?s=admin" replace />} />
          </Routes>
        )}
      </main>
    </BrowserRouter>
  );
}
