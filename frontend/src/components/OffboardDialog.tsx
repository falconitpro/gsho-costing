import { FormEvent, useEffect, useState } from "react";
import { api, ApiError } from "../api";
import SecurityModal from "./SecurityModal";

/** «Отключить доступ» / «Включить доступ» (docs/operations/10 §7): последствия с фактическими количествами из backend,
 *  подтверждение вводом ФИО и подтверждение действия (step-up — диалог откроется сам). Результат — фактические counts. */
type Preview = { active_sessions: number; open_tasks: number; active_overrides: number; deputy_links: number; finance_questions_open: number };
type Counts = { sessions_revoked: number; delegations_ended: number; overrides_ended: number; tasks_flagged: number; finance_questions_open: number };

export default function OffboardDialog({ user, mode, onClose, onDone }: { user: { id: string; display_name: string }; mode: "offboard" | "reactivate"; onClose: () => void; onDone: () => void }) {
  const [pv, setPv] = useState<Preview | null>(null);
  const [name, setName] = useState("");
  const [reason, setReason] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Counts | null>(null);
  const short = user.display_name.replace(/\s*\(.*\)\s*$/, "");
  useEffect(() => {
    if (mode === "offboard") api.get<{ offboard_preview: Preview | null }>(`/api/security/users/${user.id}`).then((c) => setPv(c.offboard_preview)).catch((e) => setErr(e instanceof ApiError ? e.message : "Не удалось загрузить данные"));
  }, [mode, user.id]);
  async function submit(e: FormEvent) {
    e.preventDefault(); setBusy(true); setErr("");
    try {
      const r = await api.post<{ counts?: Counts }>(`/api/security/users/${user.id}/${mode}`, { confirm_name: name, reason });
      if (mode === "offboard" && r.counts) setResult(r.counts); else { onDone(); onClose(); }
    } catch (x) { setErr(x instanceof ApiError ? x.message : "Не удалось выполнить действие"); } finally { setBusy(false); }
  }
  if (result) return (
    <SecurityModal title="Доступ отключён" onClose={() => { onDone(); onClose(); }}>
      <p>Доступ сотрудника «{short}» отключён. История, авторство и документы сохранены.</p>
      <ul className="sec-result">
        <li>Завершено сессий: <b>{result.sessions_revoked}</b></li>
        <li>Задач помечено «передать другому»: <b>{result.tasks_flagged}</b></li>
        <li>Завершено исключений прав: <b>{result.overrides_ended}</b></li>
        <li>Завершено замещений: <b>{result.delegations_ended}</b></li>
        {result.finance_questions_open > 0 && <li>Открытых финансовых вопросов (не закрыты, передайте вручную): <b>{result.finance_questions_open}</b></li>}
      </ul>
      <div className="row" style={{ justifyContent: "flex-end" }}><button className="btn" onClick={() => { onDone(); onClose(); }}>Готово</button></div>
    </SecurityModal>);
  return (
    <SecurityModal title={mode === "offboard" ? "Отключить доступ сотрудника" : "Включить доступ сотрудника"} onClose={onClose} danger={mode === "offboard"} width={540}>
      <form onSubmit={submit}>
        {mode === "offboard" ? <>
          <p style={{ marginTop: 0 }}><b>{user.display_name}</b> сразу потеряет доступ на всех устройствах. Учётная запись и история сохранятся.</p>
          {pv ? <ul className="sec-result">
            <li>Активных входов будет завершено: <b>{pv.active_sessions}</b></li>
            <li>Открытых задач — пометить «передать другому»: <b>{pv.open_tasks}</b></li>
            <li>Исключений прав — завершить: <b>{pv.active_overrides}</b></li>
            <li>Замещений — завершить: <b>{pv.deputy_links}</b></li>
            {pv.finance_questions_open > 0 && <li>Открытых финансовых вопросов (останутся открытыми): <b>{pv.finance_questions_open}</b></li>}
          </ul> : !err && <p className="muted">Подсчёт…</p>}
          <label className="sec-field">Причина (для журнала)<input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="например: увольнение" /></label>
        </> : <p style={{ marginTop: 0 }}>Старые входы и исключения прав <b>не восстановятся</b>: сотрудник войдёт заново, проверьте его роли и права.</p>}
        <label className="sec-field">Для подтверждения введите: <b>{short}</b>
          <input value={name} onChange={(e) => setName(e.target.value)} autoComplete="off" /></label>
        {err && <div className="alert error" role="alert">{err}</div>}
        <div className="row" style={{ justifyContent: "flex-end", marginTop: 10 }}>
          <button type="button" className="btn secondary" onClick={onClose}>Отмена</button>
          <button className={mode === "offboard" ? "btn" : "btn secondary"} disabled={busy || name.trim().toLowerCase() !== short.toLowerCase() && name.trim().toLowerCase() !== user.display_name.toLowerCase()}>
            {busy ? "…" : mode === "offboard" ? "Отключить доступ" : "Включить доступ"}</button>
        </div>
      </form>
    </SecurityModal>
  );
}
