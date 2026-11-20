import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";

/** Единый диалог CRM вместо системных окон браузера (D-108): askConfirm / askPrompt / askAlert возвращают Promise.
 *  Вызывается из любого обработчика без провайдера: окно рисуется в отдельном корне поверх страницы.
 *  Esc и щелчок по затемнению — «Отмена»; Enter — «ОК» (в многострочном поле — Ctrl+Enter). */

type Kind = "confirm" | "prompt" | "alert";
type Opts = { title?: string; ok?: string; cancel?: string; danger?: boolean; multiline?: boolean; required?: boolean };

function titleOf(kind: Kind, msg: string, o: Opts): string {
  if (o.title) return o.title;
  if (kind === "alert") return "Сообщение";
  if (kind === "prompt") return /\?/.test(msg) ? "Подтвердите действие" : "Введите значение";
  return /удал|аннулир|отозва|отмен|убрать|архив/i.test(msg) ? "Подтвердите действие" : "Подтверждение";
}

function Dialog({ kind, msg, def, o, done }: { kind: Kind; msg: string; def: string; o: Opts; done: (v: boolean | string | null) => void }) {
  const [val, setVal] = useState(def);
  const box = useRef<HTMLDivElement | null>(null);
  const field = useRef<HTMLInputElement & HTMLTextAreaElement>(null);
  const danger = o.danger ?? (kind === "confirm" && /удал|аннулир|отозва|сторно/i.test(msg));
  const multiline = o.multiline ?? (kind === "prompt" && /комментар|причин|почему/i.test(msg));
  const cancel = () => done(kind === "prompt" ? null : false);
  const ok = () => { if (kind === "prompt" && o.required && !val.trim()) return; done(kind === "prompt" ? val : true); };
  useEffect(() => {
    (field.current || box.current?.querySelector<HTMLElement>("button[data-ok]"))?.focus();
    if (field.current && def) field.current.select();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); cancel(); }
      else if (e.key === "Enter" && (!multiline || e.ctrlKey || e.metaKey) && (e.target as HTMLElement)?.tagName !== "BUTTON") { e.preventDefault(); ok(); }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  });
  const title = titleOf(kind, msg, o);
  return (
    <div className="sec-overlay top" onClick={cancel}>
      <div ref={box} className={`card poa-modal sec-modal${danger ? " danger" : ""}`} role={kind === "alert" ? "alertdialog" : "dialog"} aria-modal="true"
        aria-label={title} onClick={(e) => e.stopPropagation()} style={{ maxWidth: 520 }}>
        <div className="poa-modal-head"><b>{title}</b><button className="btn ghost" data-close onClick={cancel} aria-label="Закрыть">✕</button></div>
        <div className="sec-modal-body">
          <div style={{ whiteSpace: "pre-wrap", lineHeight: 1.45 }}>{msg}</div>
          {kind === "prompt" && (multiline
            ? <textarea ref={field} rows={3} value={val} onChange={(e) => setVal(e.target.value)} style={{ width: "100%", marginTop: 10 }} />
            : <input ref={field} value={val} onChange={(e) => setVal(e.target.value)} style={{ width: "100%", marginTop: 10 }} />)}
          <div className="row" style={{ justifyContent: "flex-end", gap: 8, marginTop: 14 }}>
            {kind !== "alert" && <button className="btn ghost" onClick={cancel}>{o.cancel || "Отмена"}</button>}
            <button className="btn" data-ok disabled={kind === "prompt" && !!o.required && !val.trim()} onClick={ok}>
              {o.ok || (kind === "alert" ? "Понятно" : danger ? "Да, выполнить" : "ОК")}</button>
          </div>
        </div>
      </div>
    </div>);
}

function open(kind: Kind, msg: string, def: string, o: Opts): Promise<boolean | string | null> {
  return new Promise((resolve) => {
    const host = document.createElement("div");
    document.body.appendChild(host);
    const root = createRoot(host);
    const prev = document.activeElement as HTMLElement | null;
    const done = (v: boolean | string | null) => {
      root.unmount();
      host.remove();
      prev?.focus?.();
      resolve(v);
    };
    root.render(<Dialog kind={kind} msg={msg} def={def} o={o} done={done} />);
  });
}

/** Да / нет. Как window.confirm, но окно CRM. */
export const askConfirm = (msg: string, o: Opts = {}) => open("confirm", msg, "", o) as Promise<boolean>;
/** Ввод строки; null — отмена. Как window.prompt, но окно CRM. */
export const askPrompt = (msg: string, def = "", o: Opts = {}) => open("prompt", msg, def ?? "", o) as Promise<string | null>;
/** Сообщение с кнопкой «Понятно». */
export const askAlert = (msg: string, o: Opts = {}) => open("alert", msg, "", o).then(() => undefined);
