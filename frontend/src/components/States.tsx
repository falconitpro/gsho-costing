import type { ReactNode } from "react";

/** D-160 (мастер-ТЗ UX/UI, пакет H): общие состояния экрана. Разметка совпадает с прежней («card muted»),
 *  поэтому прежний вид не меняется; в «Новом виде» (body.ui-new) загрузка показывается заготовкой без скачков,
 *  пусто / ошибка / нет доступа — единым блоком с пояснением и следующим шагом. */

type LoadingProps = { card?: boolean; muted?: boolean; msg?: ReactNode; text?: string; className?: string; p?: boolean };

/** Пока данных нет: msg (обычно текст ошибки) показывается как есть, иначе — «Загрузка…». */
export function Loading({ card, muted = true, msg, text = "Загрузка…", className, p }: LoadingProps) {
  const cls = [className, card && "card", muted && "muted", !msg && "st-loading"].filter(Boolean).join(" ");
  const Tag = p ? "p" : "div";
  return msg ? <Tag className={cls}>{msg}</Tag> : <Tag className={cls} role="status" aria-busy="true">{text}</Tag>;
}

type BoxProps = { title: ReactNode; hint?: ReactNode; action?: ReactNode; card?: boolean };

/** Пусто: что не найдено и что можно сделать (для отбора — «Сбросить отбор»). */
export function Empty({ title, hint, action, card = true }: BoxProps) {
  return (
    <div className={`${card ? "card " : ""}st-box st-empty`}>
      <div className="st-title">{title}</div>
      {hint && <div className="muted">{hint}</div>}
      {action && <div className="st-actions">{action}</div>}
    </div>
  );
}

/** Ошибка загрузки: понятный текст и «Повторить». */
export function ErrorState({ title = "Не удалось загрузить данные", hint, onRetry }: { title?: ReactNode; hint?: ReactNode; onRetry?: () => void }) {
  return (
    <div className="alert error st-box st-error" role="alert">
      <div className="st-title">{title}</div>
      {hint && <div>{hint}</div>}
      {onRetry && <div className="st-actions"><button type="button" className="btn ghost" onClick={onRetry}>Повторить</button></div>}
    </div>
  );
}

/** Нет доступа: без раскрытия закрытых данных. */
export function NoAccess({ hint = "Если доступ нужен для работы, обратитесь к администратору." }: { hint?: ReactNode }) {
  return (
    <div className="card st-box st-noaccess" role="note">
      <div className="st-title">Нет доступа к этому разделу</div>
      <div className="muted">{hint}</div>
    </div>
  );
}
