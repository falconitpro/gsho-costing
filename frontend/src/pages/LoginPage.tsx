import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { api, ApiError } from "../api";


// Страница входа CRM БЕРГ-ХАУС по утверждённому макету «Красный монолит / ПДМ».
// Вход по логину (e-mail) и паролю — POST /api/auth/password-login; дополнительно Microsoft 365 (OIDC), на тестовом стенде — «Войти как».

type Providers = { password: boolean; m365: boolean; dev: boolean; support: string };
type DevUser = { email: string; display_name: string };

const Ic = {
  user: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="12" cy="8" r="4" /><path d="M4 20c0-3.3 3.6-6 8-6s8 2.7 8 6" /></svg>,
  lock: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="5" y="11" width="14" height="10" rx="1" /><path d="M8 11V8a4 4 0 0 1 8 0v3" /></svg>,
  eye: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z" /><circle cx="12" cy="12" r="3" /></svg>,
  eyeOff: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3 3l18 18" /><path d="M10.6 5.3A11 11 0 0 1 12 5c6.5 0 10 7 10 7a17 17 0 0 1-3.2 4.1M6.6 6.6A16.6 16.6 0 0 0 2 12s3.5 7 10 7c1.6 0 3-.4 4.2-1" /><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2" /></svg>,
  arrow: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M4 12h15" /><path d="M13 6l6 6-6 6" /></svg>,
  warn: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="M12 8v5" /><path d="M12 16h.01" /></svg>,
  check: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M4 12.5l5 5L20 7" /></svg>,
};

/** Фон: плоскости макета с фактурой породы из эталона (локальные assets plane-red / plane-black, без элементов интерфейса),
 *  поверх — локальная сцена (рельеф, ПДМ, тёплый ромб). Эталон 1920 × 1200, обрезка по центру. Геометрия плоскостей — как в макете. */
const BLACK_PLANE = "1139,0 1920,0 1920,572 1635,300 1063,871 732,458";
function Background() {
  return (
    <>
      <svg className="bh-bg" viewBox="0 0 1920 1200" preserveAspectRatio="xMidYMid slice" aria-hidden="true" focusable="false">
        <defs><clipPath id="bh-black-clip"><polygon points={BLACK_PLANE} /></clipPath></defs>
        <rect width="1920" height="1200" fill="#c40303" />
        <image href="/login/plane-red.webp" x="0" y="0" width="1920" height="1200" preserveAspectRatio="none" />
        <polygon points={BLACK_PLANE} fill="#060606" stroke="#060606" strokeWidth="2" strokeLinejoin="miter" />
        <image href="/login/plane-black.webp" x="732" y="0" width="1188" height="871" preserveAspectRatio="none" clipPath="url(#bh-black-clip)" />
        <polygon points="1635,300 1920,572 1920,1200 1310,1200 1058,871" fill="#f4ebe2" />
        <image href="/login/scene.webp" x="0" y="280" width="1920" height="920" preserveAspectRatio="none" />
      </svg>
      <div className="bh-bg-mobile" aria-hidden="true" />
    </>
  );
}

export default function LoginPage({ onLoggedIn, devUsers = [], onDevPick }: { onLoggedIn: () => void; devUsers?: DevUser[]; onDevPick?: (email: string) => void }) {
  const [prov, setProv] = useState<Providers | null>(null);
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [remember, setRemember] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [touched, setTouched] = useState(false);
  const loginRef = useRef<HTMLInputElement | null>(null);
  const uid = useId();
  const ids = { login: `${uid}-login`, pass: `${uid}-pass`, err: `${uid}-err`, lerr: `${uid}-lerr`, perr: `${uid}-perr` };
  useEffect(() => { api.get<Providers>("/api/auth/providers").then(setProv).catch(() => setProv({ password: true, m365: false, dev: false, support: "Для восстановления доступа обратитесь к администратору системы" })); }, []);
  useEffect(() => { document.title = "Вход — CRM БЕРГ-ХАУС"; loginRef.current?.focus(); }, []);

  const loginEmpty = touched && !login.trim();
  const passEmpty = touched && !password;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setTouched(true); setError(""); setInfo("");
    if (!login.trim() || !password) return;
    setBusy(true);
    try {
      await api.post("/api/auth/password-login", { login: login.trim(), password, remember });
      setPassword("");
      onLoggedIn();
    } catch (err) {
      const st = err instanceof ApiError ? err.status : 0;
      setError(err instanceof ApiError && (st === 401 || st === 429 || st === 422) ? err.message : "Не удалось выполнить вход. Проверьте подключение и повторите попытку");
      setBusy(false);
    }
  }
  const support = prov?.support || "Для восстановления доступа обратитесь к администратору системы";

  return (
    <div className="bh-login">
      <Background />
      <a className="bh-logo" href="/" aria-label="БЕРГ-ХАУС — на главную"><img src="/brand/logo-white.png" alt="БЕРГ-ХАУС" width="760" height="399" /></a>
      <div className="bh-corner" aria-hidden="true"><b>BERGHAUS</b>CORPORATE ECOSYSTEM<br />INTERNAL ACCESS ONLY</div>
      <div className="bh-vert" aria-hidden="true">ONE TEAM · ONE SYSTEM · ONE RESULT</div>
      <div className="bh-slogan" aria-hidden="true"><span>Система.</span><span>Контроль.</span><span>Результат.</span></div>
      <div className="bh-foot" aria-hidden="true"><b>БЕРГ-ХАУС</b>   ГОРНАЯ ТЕХНИКА • ВЭД • СЕРВИС</div>

      <main className="bh-main">
        <section className="bh-card" aria-labelledby={`${uid}-h`}>
          <h1 id={`${uid}-h`}>Вход</h1>
          <form onSubmit={submit} noValidate aria-describedby={error ? ids.err : undefined}>
            {error && <div className="bh-alert" role="alert" id={ids.err}>{Ic.warn}<span>{error}</span></div>}
            {info && <div className="bh-alert info" role="status">{Ic.warn}<span>{info}</span></div>}
            <label className="bh-field" htmlFor={ids.login}>
              <span>Логин</span>
              <div className={`bh-input${loginEmpty ? " bad" : ""}`}>
                <span className="ic">{Ic.user}</span>
                <input ref={loginRef} id={ids.login} name="username" type="text" inputMode="email" autoComplete="username" autoCapitalize="none" spellCheck={false} placeholder="name@berg-haus.com"
                  value={login} onChange={(e) => setLogin(e.target.value)} aria-invalid={loginEmpty || undefined} aria-describedby={loginEmpty ? ids.lerr : undefined} disabled={busy} />
              </div>
              {loginEmpty && <div className="bh-field-err" id={ids.lerr}>{Ic.warn}Укажите логин</div>}
            </label>
            <label className="bh-field" htmlFor={ids.pass}>
              <span>Пароль</span>
              <div className={`bh-input${passEmpty ? " bad" : ""}`}>
                <span className="ic">{Ic.lock}</span>
                <input id={ids.pass} name="password" type={show ? "text" : "password"} autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)}
                  aria-invalid={passEmpty || undefined} aria-describedby={passEmpty ? ids.perr : undefined} disabled={busy} />
                <button type="button" className="bh-eye" aria-label={show ? "Скрыть пароль" : "Показать пароль"} aria-pressed={show} onClick={() => setShow(!show)} tabIndex={0}>{show ? Ic.eyeOff : Ic.eye}</button>
              </div>
              {passEmpty && <div className="bh-field-err" id={ids.perr}>{Ic.warn}Укажите пароль</div>}
            </label>
            <div className="bh-row">
              <label className="bh-check"><input type="checkbox" name="remember" checked={remember} onChange={(e) => setRemember(e.target.checked)} /><span className="box">{Ic.check}</span><span>Запомнить меня</span></label>
              <button type="button" className="bh-link" onClick={() => { setError(""); setInfo(support); }}>Забыли пароль?</button>
            </div>
            <button type="submit" className="bh-submit" disabled={busy} aria-busy={busy}>
              <span style={{ display: "inline-flex", alignItems: "center" }}>Войти в систему{busy && <span className="bh-spin" aria-hidden="true" />}</span>
              <span className="arrow" aria-hidden="true">{Ic.arrow}</span>
            </button>
          </form>
          <div className="bh-secure"><span className="dot" aria-hidden="true" />Защищённый контур БЕРГ-ХАУС</div>
          {(prov?.m365 || (prov?.dev && devUsers.length > 0)) && (
            <div className="bh-alt">
              {prov?.m365 && <a href={`/api/auth/login`}>Войти через Microsoft 365</a>}
              {prov?.dev && devUsers.length > 0 && (
                <label className="bh-dev">Тестовый стенд: войти как
                  <select defaultValue="" onChange={(e) => e.target.value && onDevPick?.(e.target.value)} aria-label="Тестовый пользователь">
                    <option value="">— выберите —</option>{devUsers.map((u) => <option key={u.email} value={u.email}>{u.display_name}</option>)}
                  </select>
                </label>)}
            </div>)}
          <div className="bh-card-foot">
            <span>© 2026 BERGHAUS</span>
            <button type="button" onClick={() => { setError(""); setInfo(`Техническая поддержка: ${support.charAt(0).toLowerCase()}${support.slice(1)}`); }}>Техническая поддержка</button>
          </div>
        </section>
      </main>
    </div>
  );
}
