import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import SecurityModal from "./SecurityModal";

/** Предупреждение за 5 минут до конца входа (D-041): после 8 ч без действий или через 12 ч от входа.
 *  Опрос /api/auth/session-status раз в минуту активностью не считается; «Продолжить работу» продлевает только бездействие. */
type Status = { session: boolean; now: string; ends_at: string; reason: "idle" | "absolute"; seconds_left: number };
const WARN = 5 * 60;

export default function SessionExpiryWarning() {
  const [endsAt, setEndsAt] = useState<number | null>(null);   // мс по часам браузера
  const [reason, setReason] = useState<Status["reason"]>("idle");
  const [left, setLeft] = useState<number>(Infinity);
  const [dismissed, setDismissed] = useState<number | null>(null);
  const timer = useRef<number | null>(null);

  const poll = useCallback(async () => {
    try {
      const s = await api.bg<Status>("/api/auth/session-status");
      if (!s.session) return;
      setEndsAt(Date.now() + s.seconds_left * 1000);          // без расхождения часов: считаем от ответа сервера
      setReason(s.reason);
    } catch { /* 401 обработает приложение: покажет вход */ }
  }, []);

  useEffect(() => {
    poll();
    const id = window.setInterval(poll, 60_000);
    return () => window.clearInterval(id);
  }, [poll]);

  useEffect(() => {
    if (timer.current) window.clearInterval(timer.current);
    if (endsAt == null) return;
    const tick = () => setLeft(Math.max(0, Math.round((endsAt - Date.now()) / 1000)));
    tick();
    timer.current = window.setInterval(tick, 1000);
    return () => { if (timer.current) window.clearInterval(timer.current); };
  }, [endsAt]);

  useEffect(() => { if (endsAt != null && left === 0) poll(); }, [left, endsAt, poll]);   // время вышло — сервер ответит 401, приложение покажет вход

  if (endsAt == null || left > WARN || dismissed === endsAt) return null;
  const mm = Math.floor(left / 60), ss = String(left % 60).padStart(2, "0");
  async function proceed() {
    try { await api.get("/api/auth/me"); } catch { /* 401 → вход */ }
    await poll();
  }
  return (
    <SecurityModal title="Скоро потребуется войти заново" onClose={() => setDismissed(endsAt)} width={440} top>
      <p style={{ marginTop: 0, fontSize: 18 }} role="timer" aria-live="polite">Осталось <b>{mm}:{ss}</b></p>
      {reason === "idle" ? <>
        <p>Вы давно ничего не делали в CRM. Чтобы не потерять несохранённые изменения, продолжите работу.</p>
        <div className="row" style={{ justifyContent: "flex-end" }}><button className="btn" onClick={proceed}>Продолжить работу</button></div>
      </> : <>
        <p>Вход действует не дольше 12 часов. Сохраните изменения — после этого CRM попросит пароль и код ещё раз.</p>
        <div className="row" style={{ justifyContent: "flex-end" }}><button className="btn" onClick={() => setDismissed(endsAt)}>Понятно</button></div>
      </>}
    </SecurityModal>
  );
}
