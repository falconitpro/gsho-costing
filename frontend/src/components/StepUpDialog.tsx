import { FormEvent, useEffect, useRef, useState } from "react";
import { api, ApiError, setStepUpHandler } from "../api";
import SecurityModal from "./SecurityModal";

/** Подтверждение опасного действия (docs/operations/04): код из приложения, если TOTP включён, иначе пароль.
 *  Регистрируется в api.ts: при ответе step_up_required запрос повторяется после подтверждения. Действует 10 минут. */
type Method = "totp" | "password" | "dev" | "none";

export default function StepUpDialog() {
  const [open, setOpen] = useState(false);
  const [method, setMethod] = useState<Method>("password");
  const [value, setValue] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const resolver = useRef<((ok: boolean) => void) | null>(null);

  useEffect(() => {
    setStepUpHandler(async () => {
      try { setMethod((await api.get<{ method: Method }>("/api/auth/step-up")).method); } catch { setMethod("password"); }
      setValue(""); setErr(""); setOpen(true);
      return new Promise<boolean>((res) => { resolver.current = res; });
    });
    return () => setStepUpHandler(null);
  }, []);

  const finish = (ok: boolean) => { setOpen(false); resolver.current?.(ok); resolver.current = null; };
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setErr("");
    try {
      await api.post("/api/auth/step-up", method === "totp" ? { code: value } : method === "password" ? { password: value } : {});
      finish(true);
    } catch (x) {
      setErr(x instanceof ApiError ? x.message : "Не удалось подтвердить");
    } finally { setBusy(false); }
  }
  if (!open) return null;
  return (
    <SecurityModal title="Подтвердите действие" onClose={() => finish(false)} width={420} top>
      <form onSubmit={submit}>
        <p className="muted" style={{ marginTop: 0 }}>Это действие меняет доступ сотрудников. Подтверждение действует 10 минут.</p>
        {method === "none" && <div className="alert warn">Для подтверждения задайте пароль или включите вход с кодом в разделе «Настройки → Безопасность».</div>}
        {method === "totp" && <label className="sec-field">Код из приложения
          <input inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={value} onChange={(e) => setValue(e.target.value.replace(/\D/g, ""))} placeholder="000000" /></label>}
        {method === "password" && <label className="sec-field">Ваш пароль
          <input type="password" autoComplete="current-password" value={value} onChange={(e) => setValue(e.target.value)} /></label>}
        {method === "dev" && <div className="alert info">Тестовый стенд: у этой демо-учётной записи нет пароля и кода — подтверждение без ввода.</div>}
        {err && <div className="alert error" role="alert">{err}</div>}
        <div className="row" style={{ justifyContent: "flex-end", marginTop: 12 }}>
          <button type="button" className="btn secondary" onClick={() => finish(false)}>Отмена</button>
          <button type="submit" className="btn" disabled={busy || method === "none" || (method !== "dev" && !value)}>{busy ? "Проверка…" : "Подтвердить"}</button>
        </div>
      </form>
    </SecurityModal>
  );
}
