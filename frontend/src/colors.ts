// Цвета статусов сделок и этапов: спокойные светлые оттенки, читаемый тёмный текст.
// Красный бренда остаётся для акцентов интерфейса; статусы различаются по смыслу: серый — черновик,
// голубой — расчёт, сиреневый — утверждение, янтарный — ожидание ответа, зелёный — выиграна/закрыта,
// бирюзовый — контракт, синий — исполнение, мягкий красный — проигрыш и переделка.
export type Tone = [bg: string, fg: string];

export const STATUS_COLOR: Record<string, Tone> = {
  draft: ["#f0f1f3", "#4b5563"],
  logistics_calc: ["#e4eefa", "#1d4f91"], service_calc: ["#e4eefa", "#1d4f91"], calc_ready: ["#d9e7f7", "#1d4f91"],
  cd_approval: ["#ece8f7", "#4e3d8f"], fd_visa: ["#ece8f7", "#4e3d8f"], gd_visa: ["#ece8f7", "#4e3d8f"], rework: ["#fdebdd", "#9a4a12"],
  approved: ["#e0dcf3", "#4e3d8f"], kp_sent: ["#fdf1dc", "#8a5a00"],
  won: ["#e4f3e9", "#1f6b3a"], contract: ["#def2f0", "#0f5f5a"], execution: ["#e4eefa", "#1d4f91"], closed: ["#d5e9db", "#174f2b"],
  lost: ["#fdecec", "#b3261e"], cancelled: ["#f0f1f3", "#8a8f98"],
};
export const toneOf = (status: string): Tone => STATUS_COLOR[status] || ["#f0f1f3", "#4b5563"];

/** Этапы (полоса в шапке сделки, шаги контракта): пройдено / текущий / впереди / проиграна. */
export const STAGE: Record<"done" | "current" | "todo" | "lost", Tone> = {
  done: ["#e4f3e9", "#1f6b3a"], current: ["#1d4f91", "#fff"], todo: ["#f0f1f3", "#6b7280"], lost: ["#fdecec", "#b3261e"],
};
export const PRIORITY_COLOR: Record<number, string> = { 0: "#b9c0c9", 1: "#6b7280", 2: "#8a5a00", 3: "#b3261e" };
