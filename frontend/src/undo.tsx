import { useEffect, useRef, useState, type CSSProperties } from "react";

/**
 * «Отменить последнее действие» для любой формы или таблицы: история снимков состояния.
 * state — объект с редактируемыми значениями; restore — вернуть снимок в состояние; resetKey — смена записи (история очищается).
 * Набор текста подряд (пауза < 0,8 с) — одно действие.
 */
export function useUndoHistory<T>(state: T, restore: (s: T) => void, resetKey: unknown = null) {
  const stack = useRef<string[]>([]);
  const last = useRef<string | null>(null);
  const lastAt = useRef(0);
  const restoring = useRef(false);
  const [, tick] = useState(0);
  const cur = JSON.stringify(state);

  useEffect(() => { stack.current = []; last.current = null; tick((x) => x + 1); }, [resetKey]);
  useEffect(() => {
    if (last.current === null) { last.current = cur; return; }
    if (cur === last.current) return;
    if (restoring.current) { restoring.current = false; last.current = cur; return; }
    const now = Date.now();
    if (now - lastAt.current > 800 || !stack.current.length) {
      stack.current.push(last.current);
      if (stack.current.length > 100) stack.current.shift();
      tick((x) => x + 1);
    }
    lastAt.current = now;
    last.current = cur;
  }, [cur]);

  const undo = () => {
    const prev = stack.current.pop();
    if (prev === undefined) return;
    restoring.current = true;
    lastAt.current = 0;
    restore(JSON.parse(prev) as T);
    tick((x) => x + 1);
  };
  /** Принять текущее состояние как исходное (после загрузки с сервера), не записывая это в историю. */
  const accept = () => { last.current = null; };
  return { undo, canUndo: stack.current.length > 0, steps: stack.current.length, accept };
}

export function UndoButton({ h, style }: { h: { undo: () => void; canUndo: boolean; steps: number }; style?: CSSProperties }) {
  return (
    <button type="button" className="btn ghost" style={style} disabled={!h.canUndo} onClick={h.undo}
      title={h.canUndo ? `Вернуть состояние до последнего изменения (доступно шагов: ${h.steps})` : "Пока нечего отменять"}>
      ↶ Отменить последнее действие
    </button>
  );
}
