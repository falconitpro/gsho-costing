import { useState } from "react";

/** D-132: ссылка «Задать пароль» — поля при создании сотрудника и результат отправки (ушло на почту или ссылка для передачи вручную). */
export type LinkResult = { sent: boolean; to: string; expires_at: string; reason: string; url: string | null };

export function InviteFields({ login, on, email, onChange }: { login: string; on: boolean; email: string; onChange: (on: boolean, email: string) => void }) {
  return (
    <div className="pwlink-fields">
      <label className="row"><input type="checkbox" checked={on} onChange={(e) => onChange(e.target.checked, email)} /> отправить ссылку «Задать пароль»</label>
      {on && <label className="row">на адрес<input type="email" value={email} placeholder={login || "e-mail из профиля"} onChange={(e) => onChange(on, e.target.value)} /></label>}
      {on && <span className="muted">пусто — на логин; можно указать личную почту, пока нет корпоративной</span>}
    </div>
  );
}

export function LinkResultBox({ r, onClose }: { r: LinkResult; onClose?: () => void }) {
  const [copied, setCopied] = useState(false);
  const until = new Date(r.expires_at).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  return (
    <div className={"alert " + (r.sent ? "ok" : "warn") + " pwlink-result"} role="status">
      {r.sent ? <span>Ссылка «Задать пароль» отправлена на <b>{r.to}</b>, действует до {until}.</span> : <>
        <span>Письмо не отправлено ({r.reason}). Передайте ссылку сотруднику лично или в мессенджере — она одноразовая и действует до {until}:</span>
        <div className="row"><code className="pwlink-url">{r.url}</code>
          <button className="btn secondary" onClick={() => { navigator.clipboard?.writeText(r.url || ""); setCopied(true); }}>{copied ? "Скопировано" : "Скопировать"}</button></div>
      </>}
      {onClose && <button className="btn ghost" onClick={onClose}>Готово</button>}
    </div>
  );
}
