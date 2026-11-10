import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import qrcode from "qrcode-generator";
import { api, ApiError } from "../api";
import type { Me } from "../types";
import SecurityModal from "../components/SecurityModal";

/** «Настройки → Безопасность» (docs/operations/10 §6): вход с кодом из приложения, коды восстановления, сессии, пароль.
 *  Используется и как обязательный экран, пока не сменён временный пароль или не настроен обязательный TOTP (focus). */

type Sess = { sid: string; current: boolean; device: string; ip_prefix: string; created_at: string | null; last_seen_at: string | null; expires_at: string | null; remember: boolean; auth_method: string; mfa_level: string };
const fmt = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "Europe/Moscow" });
const when = (iso: string | null) => (iso ? fmt.format(new Date(iso)) : "—");
const METHOD: Record<string, string> = { password: "пароль", oidc: "Microsoft 365", dev: "тестовый стенд", legacy: "до обновления", recovery: "код восстановления" };
const errText = (e: unknown) => (e instanceof ApiError ? e.message : "Не удалось выполнить действие");

function Qr({ text }: { text: string }) {
  const svg = useMemo(() => { const q = qrcode(0, "M"); q.addData(text); q.make(); return q.createSvgTag({ cellSize: 4, margin: 2, scalable: true }); }, [text]);
  return <div className="sec-qr" aria-label="QR-код для приложения" dangerouslySetInnerHTML={{ __html: svg }} />;
}

function RecoveryCodes({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  const [saved, setSaved] = useState(false);
  const [copied, setCopied] = useState(false);
  const text = `Коды восстановления CRM БЕРГ-ХАУС (каждый — один раз)\n${codes.join("\n")}\n`;
  const download = () => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
    a.download = "crm-recovery-codes.txt"; a.click(); URL.revokeObjectURL(a.href);
  };
  return (
    <div>
      <div className="alert warn">Сохраните коды сейчас — больше они не покажутся. Каждый код работает один раз, если телефон недоступен. Не храните их рядом с паролем.</div>
      <ol className="sec-codes">{codes.map((c) => <li key={c}><code>{c}</code></li>)}</ol>
      <div className="row">
        <button className="btn secondary" onClick={async () => { try { await navigator.clipboard.writeText(text); setCopied(true); } catch { setCopied(false); } }}>{copied ? "Скопировано" : "Скопировать"}</button>
        <button className="btn secondary" onClick={download}>Скачать .txt</button>
        <button className="btn secondary" onClick={() => window.print()}>Печать</button>
      </div>
      <label className="row" style={{ marginTop: 12 }}><input type="checkbox" checked={saved} onChange={(e) => setSaved(e.target.checked)} /> Я сохранил коды в надёжном месте</label>
      <div className="row" style={{ justifyContent: "flex-end", marginTop: 10 }}><button className="btn" disabled={!saved} onClick={onDone}>Готово</button></div>
    </div>
  );
}

function MfaWizard({ me, onClose }: { me: Me; onClose: (changed: boolean) => void }) {
  const [step, setStep] = useState<1 | 2 | 3 | 4>(1);
  const [password, setPassword] = useState("");
  const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string; replaces_existing: boolean } | null>(null);
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState<string[]>([]);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  async function start(e: FormEvent) {
    e.preventDefault(); setBusy(true); setErr("");
    try { setSetup(await api.post("/api/auth/mfa/setup", { password })); setPassword(""); setStep(3); } catch (x) { setErr(errText(x)); } finally { setBusy(false); }
  }
  async function confirm(e: FormEvent) {
    e.preventDefault(); setBusy(true); setErr("");
    try { setCodes((await api.post<{ recovery_codes: string[] }>("/api/auth/mfa/confirm", { code })).recovery_codes); setSetup(null); setStep(4); } catch (x) { setErr(errText(x)); } finally { setBusy(false); }
  }
  return (
    <SecurityModal title="Вход с кодом из приложения" onClose={() => onClose(step === 4)} width={560}>
      {step === 1 && <div>
        <p style={{ marginTop: 0 }}>После пароля система будет спрашивать 6-значный код из приложения на телефоне (Яндекс Ключ, Google Authenticator, Microsoft Authenticator и др.). Так украденный пароль не даст войти.</p>
        {me.security?.mfa_enabled && <div className="alert info">Код уже включён. Новая настройка заменит телефон только после проверки нового кода.</div>}
        <div className="row" style={{ justifyContent: "flex-end" }}><button className="btn" onClick={() => setStep(2)}>Начать</button></div>
      </div>}
      {step === 2 && <form onSubmit={start}>
        <label className="sec-field">Текущий пароль<input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} /></label>
        <p className="muted" style={{ fontSize: 13 }}>Если пароля у учётной записи нет (вход через Microsoft 365), оставьте поле пустым.</p>
        {err && <div className="alert error" role="alert">{err}</div>}
        <div className="row" style={{ justifyContent: "flex-end" }}><button className="btn" disabled={busy}>{busy ? "…" : "Дальше"}</button></div>
      </form>}
      {step === 3 && setup && <form onSubmit={confirm}>
        <div className="alert warn" style={{ fontSize: 13 }}>Не показывайте этот экран при демонстрации экрана и не делайте скриншот.</div>
        <div className="sec-qr-row">
          <Qr text={setup.otpauth_uri} />
          <div>
            <p style={{ marginTop: 0 }}>1. Отсканируйте QR-код в приложении или введите ключ вручную:</p>
            <div className="row"><code className="backup-code">{setup.secret.match(/.{1,4}/g)?.join(" ")}</code>
              <button type="button" className="btn secondary" onClick={async () => { try { await navigator.clipboard.writeText(setup.secret); setCopied(true); } catch { setCopied(false); } }}>{copied ? "Скопировано" : "Скопировать"}</button></div>
            <label className="sec-field" style={{ marginTop: 12 }}>2. Код из приложения
              <input inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} placeholder="000000" /></label>
          </div>
        </div>
        {err && <div className="alert error" role="alert">{err}</div>}
        <div className="row" style={{ justifyContent: "flex-end" }}><button className="btn" disabled={busy || code.length !== 6}>{busy ? "Проверка…" : "Включить"}</button></div>
      </form>}
      {step === 4 && <RecoveryCodes codes={codes} onDone={() => onClose(true)} />}
    </SecurityModal>
  );
}

function PasswordForm({ forced, onChanged }: { forced: boolean; onChanged: (n: number) => void }) {
  const [cur, setCur] = useState(""); const [pw, setPw] = useState(""); const [rep, setRep] = useState("");
  const [err, setErr] = useState<string[]>([]); const [ok, setOk] = useState(""); const [busy, setBusy] = useState(false);
  const checks = [["от 12 до 128 символов", pw.length >= 12 && pw.length <= 128], ["буква и цифра", /\p{L}/u.test(pw) && /\d/.test(pw)], ["повтор совпадает", !!pw && pw === rep]] as const;
  async function submit(e: FormEvent) {
    e.preventDefault(); setBusy(true); setErr([]); setOk("");
    try {
      const r = await api.post<{ other_sessions_revoked: number }>("/api/auth/change-password", { current_password: cur, new_password: pw });
      setCur(""); setPw(""); setRep("");
      setOk(r.other_sessions_revoked ? `Пароль изменён. Завершено других входов: ${r.other_sessions_revoked}.` : "Пароль изменён.");
      onChanged(r.other_sessions_revoked);
    } catch (x) {
      const d = x instanceof ApiError ? (x.detail as { problems?: string[] }) : null;
      setErr(d?.problems?.length ? d.problems : [errText(x)]);
    } finally { setBusy(false); }
  }
  return (
    <form onSubmit={submit} className="sec-pw">
      {forced && <div className="alert warn">Администратор выдал временный пароль. Задайте свой, чтобы продолжить работу.</div>}
      <label className="sec-field">{forced ? "Временный пароль" : "Текущий пароль"}<input type="password" autoComplete="current-password" value={cur} onChange={(e) => setCur(e.target.value)} /></label>
      <label className="sec-field">Новый пароль<input type="password" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} /></label>
      <label className="sec-field">Повтор нового пароля<input type="password" autoComplete="new-password" value={rep} onChange={(e) => setRep(e.target.value)} /></label>
      <ul className="sec-checks">{checks.map(([t, v]) => <li key={t} className={v ? "ok" : ""}>{v ? "✓" : "○"} {t}</li>)}
        <li className="muted">не распространённый, не совпадает с логином и именем, не один из 5 прежних — проверит сервер</li></ul>
      {err.length > 0 && <div className="alert error" role="alert">Пароль не подходит: {err.join("; ")}</div>}
      {ok && <div className="alert ok" role="status">{ok}</div>}
      <button className="btn" disabled={busy || !checks.every(([, v]) => v)}>{busy ? "Сохранение…" : "Сменить пароль"}</button>
    </form>
  );
}

export default function SecuritySettingsPage({ me, focus, onChanged }: { me: Me; focus?: "password" | "mfa"; onChanged: () => void }) {
  const [sessions, setSessions] = useState<Sess[]>([]);
  const [wizard, setWizard] = useState(false);
  const [codes, setCodes] = useState<string[] | null>(null);
  const [err, setErr] = useState("");
  const [confirmAll, setConfirmAll] = useState(false);
  const sec = me.security;
  const loadSessions = useCallback(async () => { try { setSessions((await api.get<{ items: Sess[] }>("/api/auth/sessions")).items); } catch { setSessions([]); } }, []);
  useEffect(() => { if (!focus) loadSessions(); }, [focus, loadSessions]);
  useEffect(() => { if (focus === "mfa") setWizard(true); }, [focus]);
  const act = async (fn: () => Promise<unknown>) => { setErr(""); try { await fn(); } catch (x) { setErr(errText(x)); } };

  if (focus === "password") return <div className="card sec-gate"><h2 style={{ marginTop: 0 }}>Смена временного пароля</h2><PasswordForm forced onChanged={() => onChanged()} /></div>;
  if (focus === "mfa") return (
    <div className="card sec-gate"><h2 style={{ marginTop: 0 }}>Нужен вход с кодом из приложения</h2>
      <p>В организации включён обязательный второй шаг входа. Настройте его — после этого откроются все разделы.</p>
      {!sec?.mfa_available && <div className="alert error">На сервере не настроен ключ шифрования — обратитесь к администратору.</div>}
      <button className="btn" onClick={() => setWizard(true)} disabled={!sec?.mfa_available}>Настроить</button>
      {wizard && <MfaWizard me={me} onClose={(changed) => { setWizard(false); if (changed) onChanged(); }} />}
    </div>);

  const others = sessions.filter((s) => !s.current);
  return (
    <>
      {err && <div className="alert error" role="alert">{err}</div>}
      <div className="card">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h2 style={{ margin: 0 }}>Вход с кодом из приложения {sec?.mfa_enabled ? <span className="badge ok">включён</span> : <span className="badge">выключен</span>}</h2>
          <button className="btn secondary" onClick={() => setWizard(true)} disabled={!sec?.mfa_available}>{sec?.mfa_enabled ? "Заменить телефон" : "Включить"}</button>
        </div>
        <p className="muted">{sec?.mfa_enabled ? `После пароля система спрашивает код из приложения. Кодов восстановления осталось: ${sec.recovery_left}.` : "Второй шаг входа защищает от украденного пароля."}
          {sec?.mfa_required ? " В организации он обязателен." : ""}</p>
        {!sec?.mfa_available && <div className="alert warn">На сервере не настроен ключ шифрования — включение недоступно.</div>}
        {sec?.mfa_enabled && <button className="btn secondary" onClick={() => act(async () => setCodes((await api.post<{ recovery_codes: string[] }>("/api/auth/mfa/recovery/regenerate")).recovery_codes))}>Новые коды восстановления</button>}
      </div>

      <div className="card">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h2 style={{ margin: 0 }}>Активные входы</h2>
          {others.length > 0 && <button className="btn secondary" onClick={() => setConfirmAll(true)}>Завершить все, кроме текущего</button>}
        </div>
        <div className="backup-table"><table>
          <thead><tr><th>Устройство</th><th>Последняя активность</th><th>Вход</th><th>Адрес</th><th>Способ</th><th></th></tr></thead>
          <tbody>{sessions.map((s) => (
            <tr key={s.sid}>
              <td>{s.device}{s.current && <span className="badge info" style={{ marginLeft: 6 }}>этот браузер</span>}</td>
              <td style={{ whiteSpace: "nowrap" }}>{when(s.last_seen_at)}</td><td style={{ whiteSpace: "nowrap" }}>{when(s.created_at)}</td>
              <td><code>{s.ip_prefix || "—"}</code></td>
              <td>{METHOD[s.auth_method] || s.auth_method}{s.mfa_level !== "none" && " + код"}{s.remember && ", запомнен"}</td>
              <td>{!s.current && <button className="btn ghost" onClick={() => act(async () => setSessions((await api.del<{ items: Sess[] }>(`/api/auth/sessions/${s.sid}`)).items))}>Завершить</button>}</td>
            </tr>))}</tbody>
        </table></div>
        <p className="muted" style={{ fontSize: 13 }}>Этот браузер завершается кнопкой «Выйти» в шапке.</p>
      </div>

      <div className="card"><h2 style={{ marginTop: 0 }}>Пароль</h2><PasswordForm forced={false} onChanged={() => loadSessions()} /></div>

      {wizard && <MfaWizard me={me} onClose={(changed) => { setWizard(false); if (changed) onChanged(); }} />}
      {codes && <SecurityModal title="Новые коды восстановления" onClose={() => setCodes(null)}><RecoveryCodes codes={codes} onDone={() => { setCodes(null); onChanged(); }} /></SecurityModal>}
      {confirmAll && <SecurityModal title="Завершить другие входы?" onClose={() => setConfirmAll(false)} width={440}>
        <p>Будут завершены входы на других устройствах: {others.length}. На них потребуется войти заново.</p>
        <div className="row" style={{ justifyContent: "flex-end" }}><button className="btn secondary" onClick={() => setConfirmAll(false)}>Отмена</button>
          <button className="btn" onClick={() => act(async () => { setSessions((await api.post<{ items: Sess[] }>("/api/auth/sessions/revoke-others")).items); setConfirmAll(false); })}>Завершить</button></div>
      </SecurityModal>}
    </>
  );
}
