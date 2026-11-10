import { ReactNode, useEffect, useRef } from "react";

/** Модальное окно в стиле существующих (poa-modal): затемнение, Esc закрывает, фокус на первое поле, role="dialog". */
export default function SecurityModal({ title, onClose, children, width = 520, danger = false, top = false }: { title: string; onClose: () => void; children: ReactNode; width?: number; danger?: boolean; top?: boolean }) {
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    const first = ref.current?.querySelector<HTMLElement>("input, textarea, select, button:not([data-close])");
    first?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); prev?.focus?.(); };
  }, [onClose]);
  return (
    <div className={`sec-overlay${top ? " top" : ""}`} onClick={onClose}>
      <div ref={ref} className={`card poa-modal sec-modal${danger ? " danger" : ""}`} role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()} style={{ maxWidth: width }}>
        <div className="poa-modal-head"><b>{title}</b><button className="btn ghost" data-close onClick={onClose} aria-label="Закрыть">✕</button></div>
        <div className="sec-modal-body">{children}</div>
      </div>
    </div>
  );
}
