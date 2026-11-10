import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api";
import OffboardDialog from "../components/OffboardDialog";
import SecurityModal from "../components/SecurityModal";

/** «Настройки → Безопасность сотрудников» (docs/operations/10 §7), право security_admin: входы, сброс TOTP, временный пароль,
 *  отключение и включение доступа. Каждое действие подтверждается (step-up) и проверяется на сервере. */
type U = { id: string; display_name: string; email: string; roles: { code: string; title: string }[]; is_active: boolean; offboarded_at: string | null;
  last_seen_at: string | null; mfa_enabled: boolean; password_set: boolean; password_must_change: boolean; active_sessions: number };
type Sess = { sid: string; device: string; ip_prefix: string; created_at: string | null; last_seen_at: string | null; auth_method: string; mfa_level: string; remember: boolean };
const fmt = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "Europe/Moscow" });
const when = (iso: string | null) => (iso ? fmt.format(new Date(iso)) : "—");
const errText = (e: unknown) => (e instanceof ApiError ? e.message : "Не удалось выполнить действие");

function UserCard({ u, canEdit, meId, onClose, onChanged }: { u: U; canEdit: boolean; meId: string; onClose: () => void; onChanged: () => void }) {
  const [sessions, setSessions] = useState<Sess[]>([]);
  const [msg, setMsg] = useState(""); const [err, setErr] = useState("");
  const [temp, setTemp] = useState("");
  const [dlg, setDlg] = useState<"offboard" | "reactivate" | "mfa" | "temp" | null>(null);
  const load = useCallback(async () => { try { setSessions((await api.get<{ items: Sess[] }>(`/api/security/users/${u.id}/sessions`)).items); } catch (x) { setErr(errText(x)); } }, [u.id]);
  useEffect(() => { load(); }, [load]);
  const act = async (fn: () => Promise<void>) => { setErr(""); setMsg(""); try { await fn(); } catch (x) { setErr(errText(x)); } };
  const self = u.id === meId;
  return (
    <SecurityModal title={u.display_name} onClose={onClose} width={760}>
      <div className="muted" style={{ marginBottom: 8 }}>{u.email} · {u.roles.map((r) => r.title).join(", ") || "без роли"} · последняя активность {when(u.last_seen_at)}</div>
      <div className="row" style={{ marginBottom: 10 }}>
        {u.is_active ? <span className="badge ok">доступ включён</span> : <span className="badge bad">доступ отключён {u.offboarded_at ? when(u.offboarded_at) : ""}</span>}
        {u.mfa_enabled ? <span className="badge ok">код из приложения</span> : <span className="badge">без кода</span>}
        {u.password_must_change && <span className="badge warn">временный пароль</span>}
      </div>
      {err && <div className="alert error" role="alert">{err}</div>}
      {msg && <div className="alert ok" role="status">{msg}</div>}
      <h3>Активные входы ({sessions.length})</h3>
      {sessions.length === 0 ? <p className="muted">Нет активных входов.</p> : <div className="backup-table"><table>
        <thead><tr><th>Устройство</th><th>Активность</th><th>Вход</th><th>Адрес</th><th></th></tr></thead>
        <tbody>{sessions.map((s) => <tr key={s.sid}><td>{s.device}{s.mfa_level !== "none" && " · с кодом"}</td><td>{when(s.last_seen_at)}</td><td>{when(s.created_at)}</td><td><code>{s.ip_prefix || "—"}</code></td>
          <td>{canEdit && <button className="btn ghost" onClick={() => act(async () => { setSessions((await api.del<{ items: Sess[] }>(`/api/security/users/${u.id}/sessions/${s.sid}`)).items); setMsg("Сессия завершена"); onChanged(); })}>Завершить</button>}</td></tr>)}</tbody>
      </table></div>}
      {canEdit && <div className="sec-danger">
        <h3 style={{ marginTop: 0 }}>Доступ сотрудника</h3>
        <div className="row">
          {sessions.length > 0 && <button className="btn secondary" onClick={() => act(async () => { const r = await api.post<{ revoked: number }>(`/api/security/users/${u.id}/sessions/revoke-all`); setMsg(`Завершено входов: ${r.revoked}`); load(); onChanged(); })}>Завершить все входы</button>}
          {u.mfa_enabled && !self && <button className="btn secondary" onClick={() => setDlg("mfa")}>Сбросить код из приложения</button>}
          {u.is_active && !self && <button className="btn secondary" onClick={() => setDlg("temp")}>Выдать временный пароль</button>}
          {u.is_active && !self && <button className="btn" onClick={() => setDlg("offboard")}>Отключить доступ</button>}
          {!u.is_active && <button className="btn secondary" onClick={() => setDlg("reactivate")}>Включить доступ</button>}
        </div>
        {self && <p className="muted" style={{ fontSize: 13 }}>Свои код и доступ меняет другой администратор безопасности.</p>}
      </div>}
      {dlg === "mfa" && <SecurityModal title="Сбросить код из приложения?" onClose={() => setDlg(null)} width={460}>
        <p>Используйте, если сотрудник потерял телефон. Все его входы завершатся; при следующем входе он настроит код заново.</p>
        <div className="row" style={{ justifyContent: "flex-end" }}><button className="btn secondary" onClick={() => setDlg(null)}>Отмена</button>
          <button className="btn" onClick={() => act(async () => { const r = await api.post<{ sessions_revoked: number }>(`/api/security/users/${u.id}/mfa/reset`); setDlg(null); setMsg(`Код сброшен, завершено входов: ${r.sessions_revoked}`); load(); onChanged(); })}>Сбросить</button></div>
      </SecurityModal>}
      {dlg === "temp" && <SecurityModal title="Временный пароль" onClose={() => { setDlg(null); setTemp(""); }} width={460}>
        {!temp ? <>
          <p>Будет создан случайный пароль. Все входы сотрудника завершатся; при входе он обязан сменить пароль.</p>
          <div className="row" style={{ justifyContent: "flex-end" }}><button className="btn secondary" onClick={() => setDlg(null)}>Отмена</button>
            <button className="btn" onClick={() => act(async () => { setTemp((await api.post<{ temporary_password: string }>(`/api/security/users/${u.id}/password-reset`)).temporary_password); load(); onChanged(); })}>Создать</button></div>
        </> : <>
          <div className="alert warn">Передайте пароль сотруднику лично или по телефону. Больше он не покажется.</div>
          <div className="row"><code className="backup-code" style={{ fontSize: 16 }}>{temp}</code><button className="btn secondary" onClick={() => navigator.clipboard?.writeText(temp)}>Скопировать</button></div>
          <div className="row" style={{ justifyContent: "flex-end", marginTop: 10 }}><button className="btn" onClick={() => { setDlg(null); setTemp(""); }}>Готово</button></div>
        </>}
      </SecurityModal>}
      {(dlg === "offboard" || dlg === "reactivate") && <OffboardDialog user={u} mode={dlg} onClose={() => setDlg(null)} onDone={() => { onChanged(); onClose(); }} />}
    </SecurityModal>
  );
}

export default function SecurityAdminPage({ meId }: { meId: string }) {
  const [items, setItems] = useState<U[]>([]);
  const [canEdit, setCanEdit] = useState(false);
  const [err, setErr] = useState("");
  const [q, setQ] = useState("");
  const [showInactive, setShowInactive] = useState(false);
  const [open, setOpen] = useState<U | null>(null);
  const load = useCallback(async () => {
    try { const r = await api.get<{ items: U[]; can_edit: boolean }>("/api/security/users"); setItems(r.items); setCanEdit(r.can_edit); setErr(""); }
    catch (x) { setErr(errText(x)); }
  }, []);
  useEffect(() => { load(); }, [load]);
  const list = items.filter((u) => (showInactive || u.is_active) && (!q || (u.display_name + " " + u.email).toLowerCase().includes(q.toLowerCase())));
  const active = items.filter((u) => u.is_active);
  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 style={{ margin: 0 }}>Безопасность сотрудников</h2>
        <div className="row"><input placeholder="Поиск сотрудника" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Поиск сотрудника" />
          <label className="row"><input type="checkbox" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} /> показать отключённых</label></div>
      </div>
      <p className="muted">С кодом из приложения: {active.filter((u) => u.mfa_enabled).length} из {active.length} активных. {canEdit ? "Действия подтверждаются кодом или паролем." : "Просмотр без права изменять."}</p>
      {err && <div className="alert error" role="alert">{err}</div>}
      <div className="backup-table"><table>
        <thead><tr><th>Сотрудник</th><th>Роли</th><th>Доступ</th><th>Код</th><th>Входов</th><th>Активность</th><th></th></tr></thead>
        <tbody>{list.map((u) => (
          <tr key={u.id} style={{ opacity: u.is_active ? 1 : 0.6 }}>
            <td><b>{u.display_name}</b><div className="muted" style={{ fontSize: 12 }}>{u.email}</div></td>
            <td style={{ fontSize: 13 }}>{u.roles.map((r) => r.title).join(", ")}</td>
            <td>{u.is_active ? (u.password_must_change ? <span className="badge warn">временный пароль</span> : <span className="badge ok">включён</span>) : <span className="badge bad">отключён</span>}</td>
            <td>{u.mfa_enabled ? <span className="badge ok">да</span> : <span className="muted">нет</span>}</td>
            <td>{u.active_sessions}</td>
            <td style={{ whiteSpace: "nowrap" }}>{when(u.last_seen_at)}</td>
            <td><button className="btn ghost" onClick={() => setOpen(u)}>Открыть</button></td>
          </tr>))}</tbody>
      </table></div>
      {open && <UserCard u={open} canEdit={canEdit} meId={meId} onClose={() => setOpen(null)} onChanged={load} />}
    </div>
  );
}
