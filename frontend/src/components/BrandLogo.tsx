import { NavLink, useLocation } from "react-router-dom";

/**
 * D-174 (ТЗ H-BRAND-01): единый блок оригинального логотипа BERGHAUS.
 * Векторные файлы /brand/vector без обрезки и фильтров; пропорции — по файлу (width/height: auto).
 *  - full-dark  — полный логотип для тёмной боковой панели (белый + красный);
 *  - full-light — полный логотип для светлой верхней панели и экрана безопасности (чёрный + красный);
 *  - mark       — знак «BHA» без надписи для свёрнутой панели 64 px; полный логотип в это время — в верхней панели.
 */
type Variant = "full-dark" | "full-light" | "mark";

// векторный логотип (26.11.2026, по поручению владельца перерисован из logo-main.png): public/brand/vector, README там же
const SRC: Record<Variant, { src: string; srcSet?: string }> = {
  "full-dark": { src: "/brand/vector/berghaus-logo-on-dark.svg" },
  "full-light": { src: "/brand/vector/berghaus-logo-color.svg" },
  mark: { src: "/brand/vector/berghaus-mark-on-dark.svg" },
};

export function BrandImg({ variant }: { variant: Variant }) {
  const s = SRC[variant];
  return <img className={`brand-img brand-img-${variant}`} src={s.src} srcSet={s.srcSet} alt="BERGHAUS" decoding="async" />;
}

/** Ссылка на рабочее место. На самом рабочем месте (/) повторный щелчок ничего не перезагружает. */
export default function BrandLogo({ variant, placement, extra }: { variant: Variant; placement: string; extra?: Variant }) {
  const loc = useLocation();
  return (
    <NavLink to="/" end className={`brand-logo brand-${placement}`} aria-label="BERGHAUS — рабочее место" title="Рабочее место"
      onClick={(e) => { if (loc.pathname === "/" && !loc.search) e.preventDefault(); }}>
      <BrandImg variant={variant} />
      {extra && <BrandImg variant={extra} />}
    </NavLink>);
}
