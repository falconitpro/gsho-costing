import { useEffect, useState } from "react";
import FloatingScrollbar from "./components/FloatingScrollbar";
import { BrowserRouter, NavLink, Navigate, Route, Routes } from "react-router-dom";
import { api, getDevUser, setDevUser } from "./api";
import type { Me } from "./types";
import DealsList from "./pages/DealsList";
import DealCard from "./pages/DealCard";
import TasksPage from "./pages/TasksPage";
import BoardPage from "./pages/BoardPage";
import SettingsPage from "./pages/SettingsPage";
import ClientsPage from "./pages/ClientsPage";
import TeamPage from "./pages/TeamPage";
import AccessPage from "./pages/AccessPage";
import CatalogPage from "./pages/CatalogPage";
import FactoriesPage from "./pages/FactoriesPage";
import AdminPage from "./pages/AdminPage";
import PartsSetupPage from "./pages/PartsSetupPage";

export const MeContext = { current: null as Me | null };

export default function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [devUsers, setDevUsers] = useState<{ email: string; display_name: string; roles: string[] }[]>([]);
  const [err, setErr] = useState("");

  async function load() {
    try {
      const m = await api.get<Me>("/api/auth/me");
      setMe(m);
      MeContext.current = m;
      setErr("");
    } catch (e) {
      setMe(null);
      setErr(String((e as Error).message));
    }
  }
  useEffect(() => {
    api.get<typeof devUsers>("/api/auth/dev-users").then(setDevUsers).catch(() => setDevUsers([]));
    load();
  }, []);

  const has = (...roles: string[]) => !!me && me.roles.some((r) => roles.includes(r.code));
  const perm = (g: string, level = 1) => !!me && (me.perms?.[g] ?? 0) >= level;

  return (
    <BrowserRouter>
      <header className="top">
        <div className="top-row">
          <NavLink to="/deals" className="brand">
            <img src="/brand/logo-main-96.png" srcSet="/brand/logo-main.png 2x" alt="Berghaus" />
            <span>Себестоимость ГШО</span>
          </NavLink>
          <div className="account">
            {devUsers.length > 0 && (
              <label className="dev-switch" title="тестовый стенд: войти под другой учётной записью">
                <span>Войти как</span>
                <select value={getDevUser()} onChange={(e) => { setDevUser(e.target.value); load(); }}>
                  <option value="">— выберите пользователя —</option>
                  {devUsers.map((u) => (
                    <option key={u.email} value={u.email}>{u.display_name}</option>
                  ))}
                </select>
              </label>
            )}
            {me && (
              <div className="me" title={`${me.display_name} — ${me.roles.map((r) => r.title).join(", ")}`}>
                <span className="avatar">{me.display_name.replace(/\(.*\)/, "").trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase()}</span>
                <span className="who"><b>{me.display_name.replace(/\s*\(.*\)\s*$/, "")}</b><small>{me.roles.map((r) => r.title).join(", ")}</small></span>
              </div>)}
            {!me && devUsers.length === 0 && <a className="btn" href="/api/auth/login">Войти через Microsoft 365</a>}
          </div>
        </div>
        {me && (
          <nav>
            <NavLink to="/deals">Сделки</NavLink>
            <NavLink to="/tasks">Мои задачи</NavLink>
            {has("cd", "fd", "gd", "observer") && <NavLink to="/board">Доска</NavLink>}
            <NavLink to="/clients">Клиенты</NavLink>

            <NavLink to="/team">Сотрудники</NavLink>
            <NavLink to="/catalog">Каталог</NavLink>
            <NavLink to="/factories">Заводы</NavLink>
            {has("cd", "fd", "gd", "admin", "logistician") && <NavLink to="/parts/setup">Запасные части</NavLink>}
            {has("admin", "fd", "gd", "cd") && <NavLink to="/settings">Настройки</NavLink>}
            {perm("access_admin", 2) && <NavLink to="/access">Уровни доступа</NavLink>}
            {has("admin", "gd", "fd") && <NavLink to="/admin">Администрирование</NavLink>}
          </nav>
        )}
      </header>
      <FloatingScrollbar />
      <main>
        {!me && err && <div className="alert info">{devUsers.length ? "Выберите пользователя в шапке (dev-режим)." : "Требуется вход."}</div>}
        {me && (
          <Routes>
            <Route path="/" element={<Navigate to="/deals" />} />
            <Route path="/deals" element={<DealsList me={me} />} />
            <Route path="/deals/:id" element={<DealCard me={me} />} />
            <Route path="/tasks" element={<TasksPage />} />
            <Route path="/board" element={<BoardPage />} />
            <Route path="/clients" element={<ClientsPage me={me} />} />

            <Route path="/team" element={<TeamPage me={me} />} />
            <Route path="/settings" element={<SettingsPage me={me} />} />
            <Route path="/catalog" element={<CatalogPage me={me} />} />
            <Route path="/factories" element={<FactoriesPage me={me} />} />
            <Route path="/parts/setup" element={<PartsSetupPage me={me} />} />
            {perm("access_admin", 2) && <Route path="/access" element={<AccessPage me={me} />} />}
            <Route path="/admin" element={<AdminPage me={me} />} />
          </Routes>
        )}
      </main>
    </BrowserRouter>
  );
}
