import type React from "react";
import FilePreviewHost from "./components/FilePreview";
import AssistantDrawer from "./components/AssistantDrawer";
import { SupportAdmin, SupportList, SupportTicketRoute } from "./pages/SupportPage";
import { HelpMenu, UserMenu } from "./components/HeaderMenus";
import RoleNav from "./components/RoleNav";
import ErrorBoundary from "./components/ErrorBoundary";
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
import NegotiationPage from "./pages/NegotiationPage";
import TasksPage from "./pages/TasksPage";
import SummaryPage from "./pages/SummaryPage";
import FinanceQuestionPage from "./pages/FinanceQuestionPage";
import FinancePage from "./pages/FinancePage";
import BoardPage from "./pages/BoardPage";
import SettingsHub from "./pages/SettingsHub";
import SecuritySettingsPage from "./pages/SecuritySettingsPage";
import StepUpDialog from "./components/StepUpDialog";
import SessionExpiryWarning from "./components/SessionExpiryWarning";
import CounterpartiesRedirect from "./pages/CounterpartiesPage";
import LegalPage from "./pages/LegalPage";
import CatalogHub from "./pages/CatalogHub";
import SupplyPage from "./pages/PartsSetupPage";
import OfficePage from "./pages/OfficePage";
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

/** D-117: ошибка одного экрана не роняет шапку и меню; при переходе на другой адрес экран открывается заново. */
function RouteBoundary({ children }: { children: React.ReactNode }) {
  const loc = useLocation();
  return <ErrorBoundary key={loc.pathname}>{children}</ErrorBoundary>;
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
          {me && <RoleNav me={me} />}
          <div className="account">
            {me && has("cd", "fd", "gd") && <InfoBar />}
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
            {me && <NotificationBell />}
            {me && <HelpMenu canAssistant={perm("assistant_use")} onAssistant={() => setAiOpen(true)} />}
            {me && <UserMenu me={me} onLogout={logout} />}
          </div>
        </div>
      </header>
      <FloatingScrollbar />
      <StepUpDialog />
      {me && <FilePreviewHost me={me} />}
      {me && perm("assistant_use") && <AssistantDrawer open={aiOpen} onClose={() => setAiOpen(false)} />}
      <SessionExpiryWarning />
      <main>
        {me && (
          <RouteBoundary>
          <Routes>
            <Route path="/login" element={<AfterLogin />} />
            <Route path="/" element={<WorkplacePage me={me} />} />
            <Route path="/deals" element={<DealsList me={me} />} />
            <Route path="/deals/parts/:id" element={<PartsRequestRoute me={me} />} />
            <Route path="/deals/:id" element={<DealCard me={me} />} />
            <Route path="/deals/:id/negotiation" element={<NegotiationPage me={me} />} />
            <Route path="/tasks" element={<TasksPage />} />
            <Route path="/finance/questions/:id" element={<FinanceQuestionPage />} />
            <Route path="/board" element={<BoardPage />} />
            <Route path="/summary" element={<SummaryPage me={me} />} />
            <Route path="/finance" element={<FinancePage me={me} />} />
            <Route path="/counterparties" element={<CounterpartiesRedirect />} />
            <Route path="/legal" element={<LegalPage me={me} />} />
            <Route path="/clients" element={<CounterpartiesRedirect type="clients" />} />

            <Route path="/team" element={<Navigate to="/settings?s=team" replace />} />
            <Route path="/settings" element={<SettingsHub me={me} />} />
            <Route path="/support" element={<SupportList />} />
            <Route path="/support/admin" element={<SupportAdmin />} />
            <Route path="/support/:id" element={<SupportTicketRoute />} />
            <Route path="/catalog" element={<CatalogHub me={me} />} />
            <Route path="/factories" element={<CounterpartiesRedirect type="suppliers" />} />
            <Route path="/supply" element={<SupplyPage me={me} />} />
            <Route path="/office" element={<OfficePage me={me} />} />
            <Route path="/parts/setup" element={<Navigate to="/supply" replace />} />
            <Route path="/access" element={<Navigate to="/settings?s=access" replace />} />
            <Route path="/admin" element={<Navigate to="/settings?s=admin" replace />} />
          </Routes>
          </RouteBoundary>
        )}
      </main>
    </BrowserRouter>
  );
}
