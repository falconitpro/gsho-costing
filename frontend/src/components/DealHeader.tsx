import { fmtDate, fmtRub } from "../api";
import type { Deal } from "../types";
import { STAGE } from "../colors";

// Шапка карточки сделки: полоса этапов «до выигрыша | после выигрыша», строка «Сейчас» и итог продажи.

export type Now = {
  task: { title: string; who: string; due: string; overdue_days: number; block: string } | null;
  open_tasks: number;
  exec?: { phase: string; done: number; total: number; overdue: { title: string; days: number }[] };
  sale?: { won_on: string | null; price_rub: number | null; price_amount: number | null; price_currency: string | null; margin_pct: number | null; approved_by: string; kp_version: number | null };
};

export const POST_WIN_STATUSES = ["won", "contract", "execution", "closed"];
const PRE = [
  { key: "request", title: "Заявка", statuses: ["draft"] },
  { key: "calc", title: "Расчёт", statuses: ["logistics_calc", "service_calc", "calc_ready"] },
  { key: "approval", title: "Утверждение", statuses: ["cd_approval", "fd_visa", "gd_visa", "rework"] },
  { key: "kp", title: "КП", statuses: ["approved", "kp_sent"] },
];
const POST = [
  { key: "contract", title: "Контракт" }, { key: "production", title: "Производство" }, { key: "acceptance", title: "Приёмка" },
  { key: "shipment", title: "Отгрузка" }, { key: "delivery", title: "Доставка" }, { key: "closed", title: "Закрыта" },
];

export function dealPhase(deal: Deal): "sale" | "contract" { return POST_WIN_STATUSES.includes(deal.status) ? "contract" : "sale"; }

function Chip({ state, title, sub }: { state: "done" | "current" | "todo" | "lost"; title: string; sub?: string }) {
  // спокойные оттенки: пройдено — светло-зелёный, текущий — синий, впереди — серый, проиграна — мягкий красный
  const [bg, fg] = STAGE[state];
  const st = state === "current" ? { background: bg, color: fg, fontWeight: 700, outline: "3px solid var(--info-bg)" } : { background: bg, color: fg };
  return <span style={{ ...st, borderRadius: 10, padding: "2px 10px", fontSize: 12, whiteSpace: "nowrap", lineHeight: "18px" }} title={sub}>{title}{state === "done" ? " ✓" : ""}</span>;
}

export function StageStrip({ deal, now }: { deal: Deal; now?: Now }) {
  // D-116: показывается только текущая фаза; другая фаза — одной отметкой (этапы — в подсказке)
  const post = POST_WIN_STATUSES.includes(deal.status);
  const lost = ["lost", "cancelled"].includes(deal.status);
  const preIdx = PRE.findIndex((p) => p.statuses.includes(deal.status));
  const execPhase = now?.exec?.phase || (deal.status === "closed" ? "closed" : "contract");
  const postIdx = POST.findIndex((p) => p.key === execPhase);
  const sep = <span style={{ color: "#c9ced3", fontSize: 11 }}>—</span>;
  const note = { fontSize: 12, fontWeight: 600 as const, whiteSpace: "nowrap" as const };
  return (
    <div className="row dh-strip" style={{ gap: 6, flexWrap: "wrap", alignItems: "center" }}>
      {post
        ? <span style={{ ...note, color: "var(--ok)" }} title={PRE.map((p) => p.title).join(" → ")}>Продажа ✓ выиграна{now?.sale?.won_on ? " " + fmtDate(now.sale.won_on) : ""}</span>
        : PRE.map((p, i) => <span key={p.key} className="row" style={{ gap: 6 }}>
            <Chip title={p.title} state={lost || preIdx > i ? "done" : preIdx === i ? "current" : "todo"} />{i < PRE.length - 1 && sep}</span>)}
      <span style={{ color: "#c9ced3" }}>→</span>
      {lost ? <span style={{ ...note, color: "var(--bad)" }}>{deal.status === "lost" ? "Проиграна" : "Отменена"}</span>
        : !post ? <span className="muted" style={{ fontSize: 12, whiteSpace: "nowrap" }} title={POST.map((p) => p.title).join(" → ")}>после выигрыша: контракт и исполнение · {POST.length} этапов</span>
        : POST.map((p, i) => <span key={p.key} className="row" style={{ gap: 6 }}>
            <Chip title={p.title} state={deal.status === "closed" ? "done" : postIdx > i ? "done" : postIdx === i ? "current" : "todo"} />{i < POST.length - 1 && sep}</span>)}
    </div>
  );
}

export function NowLine({ now, onGo }: { now?: Now; onGo: (block: string) => void }) {
  if (!now || (!now.task && !(now.exec?.overdue.length))) return null;
  const t = now.task;
  const late = now.exec?.overdue || [];
  return (
    <div className="row dh-now" style={{ gap: 10, flexWrap: "wrap", fontSize: 13 }}>
      <b>Сейчас:</b>
      {t && <span>{t.title} · <b>{t.who}</b> · срок <b style={{ color: t.overdue_days > 0 ? "var(--bad)" : undefined }}>{fmtDate(t.due)}</b>{t.overdue_days > 0 && <span style={{ color: "var(--bad)", fontWeight: 600 }}> (просрочено {t.overdue_days} дн.)</span>}</span>}
      {now.open_tasks > 1 && <span className="muted">ещё задач: {now.open_tasks - 1}</span>}
      {late.map((x, i) => <span key={i} style={{ color: "var(--bad)", fontWeight: 600 }}>{x.title.toLowerCase()} — просрочен {x.days} дн.</span>)}
      {t && <a href="#" style={{ marginLeft: "auto", fontWeight: 600 }} onClick={(e) => { e.preventDefault(); onGo(t.block); }}>Перейти →</a>}
    </div>
  );
}

export function SaleSummary({ now }: { now?: Now }) {
  const s = now?.sale;
  if (!s) return null;
  const price = s.price_currency && s.price_currency !== "RUB" && s.price_amount != null ? `${fmtRub(s.price_amount)} ${s.price_currency}` : s.price_rub != null ? `${fmtRub(s.price_rub)} RUB` : "";
  const parts = [s.won_on && `выиграна ${fmtDate(s.won_on)}`, price && `цена ${price}`, s.margin_pct != null && `маржа ${s.margin_pct} %`, s.approved_by && `утвердил ${s.approved_by}`, s.kp_version && `КП v${s.kp_version}`].filter(Boolean);
  return <span className="muted" style={{ fontSize: 12 }}>Продажа закрыта: {parts.join(" · ")}</span>;
}
