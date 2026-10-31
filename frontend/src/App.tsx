import { useEffect, useState } from "react";
import { BrowserRouter, NavLink, Navigate, Route, Routes } from "react-router-dom";
import { api, getDevUser, setDevUser } from "./api";
import type { Me } from "./types";
import DealsList from "./pages/DealsList";
import DealCard from "./pages/DealCard";
import TasksPage from "./pages/TasksPage";
import BoardPage from "./pages/BoardPage";
import SettingsPage from "./pages/SettingsPage";
import ClientsPage from "./pages/ClientsPage";

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

  return (
    <BrowserRouter>
      <header className="top">
        <span className="brand">BergHaus · Себестоимость ГШО</span>
        {me && (
          <nav>
            <NavLink to="/deals">Сделки</NavLink>
            <NavLink to="/tasks">Мои задачи</NavLink>
            {has("cd", "fd", "gd", "observer") && <NavLink to="/board">Доска</NavLink>}
            <NavLink to="/clients">Клиенты</NavLink>
            {has("admin", "fd", "gd") && <NavLink to="/settings">Настройки</NavLink>}
          </nav>
        )}
        <span className="spacer" />
        {devUsers.length > 0 && (
          <select value={getDevUser()} onChange={(e) => { setDevUser(e.target.value); load(); }} title="dev: переключение пользователя">
            <option value="">— выберите пользователя (dev) —</option>
            {devUsers.map((u) => (
              <option key={u.email} value={u.email}>{u.display_name}</option>
            ))}
          </select>
        )}
        {me && <span className="muted">{me.display_name} · {me.roles.map((r) => r.title).join(", ")}</span>}
        {!me && devUsers.length === 0 && <a className="btn" href="/api/auth/login">Войти через Microsoft 365</a>}
      </header>
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
            <Route path="/settings" element={<SettingsPage me={me} />} />
          </Routes>
        )}
      </main>
    </BrowserRouter>
  );
}
