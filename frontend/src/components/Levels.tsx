import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api";

// «Матрёшка»: общие элементы уровней навигации. Уровень выражается в адресе (query-параметры), поэтому обновление
// страницы, «Назад» браузера и прямые ссылки возвращают на тот же уровень. Бизнес-данных здесь нет — только представление.

export type Crumb = { label: ReactNode; to?: string };

/** Путь до текущего уровня. Все звенья, кроме последнего, — ссылки (клик возвращает на уровень выше). */
export function Crumbs({ items }: { items: Crumb[] }) {
  const list = items.filter((c) => c.label !== null && c.label !== undefined && c.label !== "");
  if (list.length < 2) return null;
  return (
    <nav className="crumbs" aria-label="Путь">
      {list.map((c, i) => {
        const last = i === list.length - 1;
        return (
          <Fragment key={i}>
            {i > 0 && <span className="sep" aria-hidden="true">→</span>}
            {last || !c.to ? <span aria-current={last ? "page" : undefined} className={last ? "cur" : ""}>{c.label}</span> : <Link to={c.to}>{c.label}</Link>}
          </Fragment>);
      })}
    </nav>);
}

/**
 * Один query-параметр как состояние уровня. Переключение — новая запись в истории (кнопка «Назад» возвращает прежний
 * уровень); остальные параметры адреса сохраняются. `replace` — для уточнений без смены уровня (фильтры, поиск).
 */
export function useQueryParam(key: string): [string | null, (v: string | null, opts?: { replace?: boolean; drop?: string[] }) => void] {
  const [sp, setSp] = useSearchParams();
  const value = sp.get(key);
  const set = (v: string | null, opts?: { replace?: boolean; drop?: string[] }) => {
    const n = new URLSearchParams(sp);
    if (v == null || v === "") n.delete(key); else n.set(key, v);
    (opts?.drop || []).forEach((k) => n.delete(k));
    if (n.toString() === sp.toString()) return;
    setSp(n, { replace: !!opts?.replace });
  };
  return [value, set];
}

export type Entry = {
  key: string;
  title: string;
  /** куда ведёт заголовок блока (раздел или следующий уровень) */
  to: string;
  /** пояснение, что внутри (видно сразу, не во всплывающей подсказке) */
  hint?: string;
  /** главные счётчики блока; tone — цвет статуса из текущей палитры */
  stats?: { value: ReactNode; label: string; tone?: "bad" | "warn" | "ok" | "info" | ""; to?: string }[];
  /** приоритетный объект — ссылка сразу на объект */
  top?: { label: ReactNode; sub?: ReactNode; to: string; tone?: string } | null;
  /** маркер внимания на блоке */
  attention?: string;
  /** дополнительные ссылки (следующий уровень, «Все данные») */
  links?: { label: string; to: string }[];
  big?: boolean;
};

const TONE: Record<string, string> = { bad: "var(--bad)", warn: "var(--warn)", ok: "var(--ok)", info: "var(--info)", "": "inherit" };

/** Крупные входы в подразделы: заголовок — переход в раздел, строки — сразу к объекту. */
export function EntryGrid({ entries, className }: { entries: Entry[]; className?: string }) {
  return (
    <div className={`lv-grid ${className || ""}`}>
      {entries.map((e) => (
        <section key={e.key} className={`lv-entry ${e.big ? "big" : ""} ${e.attention ? "att" : ""}`} data-entry={e.key}>
          <div className="lv-entry-head">
            <Link to={e.to} className="lv-entry-title">{e.title} <span aria-hidden="true">→</span></Link>
            {e.attention && <span className="badge bad">{e.attention}</span>}
          </div>
          {e.hint && <div className="muted lv-hint">{e.hint}</div>}
          {!!e.stats?.length && (
            <div className="lv-stats">{e.stats.map((s, i) => {
              const body = <><b style={{ color: TONE[s.tone || ""] }}>{s.value}</b><small>{s.label}</small></>;
              return s.to ? <Link key={i} to={s.to} className="lv-stat">{body}</Link> : <div key={i} className="lv-stat">{body}</div>;
            })}</div>)}
          {e.top && (
            <Link to={e.top.to} className="lv-top" style={{ borderLeftColor: e.top.tone ? TONE[e.top.tone] : undefined }}>
              <span>{e.top.label}</span>{e.top.sub && <small className="muted">{e.top.sub}</small>}
            </Link>)}
          {!!e.links?.length && <div className="lv-links">{e.links.map((l) => <Link key={l.to + l.label} to={l.to}>{l.label} →</Link>)}</div>}
        </section>))}
    </div>);
}

/** Выпадающее меню действий (быстрые действия второго уровня): открывается кликом и с клавиатуры, Esc и клик вне — закрывают. */
export function ActionMenu({ label, items, onPick }: { label: string; items: { title: string; url: string }[]; onPick: (url: string) => void }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement | null>(null);
  const btn = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { setOpen(false); btn.current?.focus(); } };
    const onDown = (e: MouseEvent) => { if (root.current && !root.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    root.current?.querySelector<HTMLButtonElement>("ul button")?.focus();
    return () => { document.removeEventListener("keydown", onKey); document.removeEventListener("mousedown", onDown); };
  }, [open]);
  if (!items.length) return null;
  return (
    <div className="lv-menu" ref={root}>
      <button ref={btn} className="btn ghost" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}>{label} ▾</button>
      {open && <ul role="menu">{items.map((q) => <li key={q.url + q.title}><button role="menuitem" onClick={() => { setOpen(false); onPick(q.url); }}>{q.title}</button></li>)}</ul>}
    </div>);
}

// ---------- с 21.11.2026 — один вид интерфейса (D-115); код «прежнего вида» удалён (D-126). Полные виды — по ссылкам на экранах
// («все блоки в полном виде», «все шаги на одной странице», «Все колонки»).
try { localStorage.removeItem("gsho.uiMode"); } catch { /* приватный режим */ }

/**
 * Данные для счётчиков обзора: те же существующие запросы, что делают сами подразделы. Ошибка или отказ в доступе —
 * счётчика просто нет (вход остаётся), права проверяет сервер. `urls[key] = null` — запрос не делается.
 */
export function useFetched(urls: Record<string, string | null>, enabled = true): Record<string, unknown> {
  const [data, setData] = useState<Record<string, unknown>>({});
  const sig = enabled ? JSON.stringify(urls) : "";
  useEffect(() => {
    if (!sig) return;
    let alive = true;
    Object.entries(JSON.parse(sig) as Record<string, string | null>).forEach(([k, u]) => {
      if (u) api.get<unknown>(u).then((r) => { if (alive) setData((d) => ({ ...d, [k]: r })); }).catch(() => undefined);
    });
    return () => { alive = false; };
  }, [sig]);
  return data;
}

/**
 * «Основные / Все колонки» для реестра. В «Новом виде» реестр показывает ключевые колонки, полный набор — по кнопке
 * (выбор запоминается в браузере, ключ `storageKey`).
 * Данные и запросы не меняются: скрытые колонки просто не рисуются.
 */
export function useAllColumns(storageKey: string, hidden: string[]): [boolean, ReactNode] {
  const [want, setWant] = useState<boolean>(() => { try { return JSON.parse(localStorage.getItem(storageKey) || "false") === true; } catch { return false; } });
  const all = want || hidden.length === 0;
  const set = (v: boolean) => { setWant(v); try { localStorage.setItem(storageKey, JSON.stringify(v)); } catch { /* хранилище недоступно */ } };
  const el = hidden.length === 0 ? null : (
    <div className="row" style={{ gap: 8, margin: "8px 0", alignItems: "center", flexWrap: "wrap" }}>
      <span className="muted">Колонки:</span>
      <div className="seg" role="group" aria-label="Набор колонок">
        <button type="button" className={!all ? "on" : ""} aria-pressed={!all} onClick={() => set(false)}>Основные</button>
        <button type="button" className={all ? "on" : ""} aria-pressed={all} onClick={() => set(true)}>Все колонки</button>
      </div>
      {!all && <span className="muted" style={{ fontSize: 12 }}>скрыты: {hidden.join(", ")}</span>}
    </div>);
  return [all, el];
}
