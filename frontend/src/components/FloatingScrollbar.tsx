import { useEffect, useRef, useState } from "react";

/** Плавающий горизонтальный ползунок: если таблица или страница шире экрана, а их собственная полоса прокрутки
 *  ниже видимой области, внизу окна показывается полоса прокрутки влево-вправо — не нужно листать в самый низ. */
type Target = { el: HTMLElement | null; left: number; width: number; scrollWidth: number };

const SELECTOR = '[style*="overflow-x"], [style*="overflow: auto"], .scroll-x';

function pick(): Target | null {
  const vh = window.innerHeight;
  for (const node of Array.from(document.querySelectorAll<HTMLElement>(SELECTOR))) {
    if (node.scrollWidth <= node.clientWidth + 2) continue;
    const r = node.getBoundingClientRect();
    if (r.top < vh - 40 && r.bottom > vh && r.width > 0) return { el: node, left: r.left, width: r.width, scrollWidth: node.scrollWidth };
  }
  const doc = document.scrollingElement as HTMLElement | null;
  if (doc && doc.scrollWidth > doc.clientWidth + 2 && window.scrollY + vh < doc.scrollHeight - 4)
    return { el: null, left: 0, width: doc.clientWidth, scrollWidth: doc.scrollWidth };
  return null;
}

export default function FloatingScrollbar() {
  const [t, setT] = useState<Target | null>(null);
  const bar = useRef<HTMLDivElement | null>(null);
  const cur = useRef<Target | null>(null);
  const syncing = useRef(false);

  useEffect(() => {
    let raf = 0;
    const update = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const n = pick();
        const p = cur.current;
        if (!n && !p) return;
        if (!n || !p || n.el !== p.el || Math.round(n.left) !== Math.round(p.left) || Math.round(n.width) !== Math.round(p.width) || n.scrollWidth !== p.scrollWidth) {
          cur.current = n; setT(n);
        }
        if (n && bar.current) {
          const sl = n.el ? n.el.scrollLeft : window.scrollX;
          if (Math.abs(bar.current.scrollLeft - sl) > 1) { syncing.current = true; bar.current.scrollLeft = sl; }
        }
      });
    };
    const mo = new MutationObserver(update);
    mo.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["style", "class"] });
    window.addEventListener("scroll", update, true);
    window.addEventListener("resize", update);
    const iv = window.setInterval(update, 1000);
    update();
    return () => { mo.disconnect(); window.removeEventListener("scroll", update, true); window.removeEventListener("resize", update); clearInterval(iv); cancelAnimationFrame(raf); };
  }, []);

  if (!t) return null;
  const onScroll = () => {
    if (syncing.current) { syncing.current = false; return; }
    const sl = bar.current?.scrollLeft || 0;
    if (cur.current?.el) cur.current.el.scrollLeft = sl; else window.scrollTo({ left: sl, top: window.scrollY });
  };
  return (
    <div ref={bar} className="floating-scroll" onScroll={onScroll} style={{ left: t.left, width: t.width }} title="прокрутка влево-вправо">
      <div style={{ width: t.scrollWidth, height: 1 }} />
    </div>
  );
}
