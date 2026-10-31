import type { CashflowPoint } from "../types";
import { fmtRub } from "../api";

/** Накопленный остаток по неделям: столбики притоков/оттоков и линия остатка; отрицательная зона закрашена. */
export default function CashflowChart({ points, width = 720, height = 220 }: { points: CashflowPoint[]; width?: number; height?: number }) {
  if (!points.length) return <div className="muted">Нет данных</div>;
  const pad = { l: 70, r: 10, t: 10, b: 28 };
  const w = width - pad.l - pad.r, h = height - pad.t - pad.b;
  const bals = points.map((p) => p.balance);
  const flows = points.flatMap((p) => [p.inflow, -p.outflow]);
  const max = Math.max(0, ...bals, ...flows), min = Math.min(0, ...bals, ...flows);
  const y = (v: number) => pad.t + (max - v) / (max - min || 1) * h;
  const x = (i: number) => pad.l + (i / Math.max(points.length - 1, 1)) * w;
  const bw = Math.max(2, w / points.length - 2);
  const line = points.map((p, i) => `${i ? "L" : "M"}${x(i)},${y(p.balance)}`).join(" ");
  const minIdx = bals.indexOf(Math.min(...bals));
  const ticks = [max, (max + min) / 2, 0, min].filter((v, i, a) => a.indexOf(v) === i);
  return (
    <svg viewBox={`0 0 ${width} ${height}`} style={{ width: "100%", height: "auto", fontFamily: "inherit", fontSize: 11 }}>
      {ticks.map((t) => (
        <g key={t}><line x1={pad.l} x2={width - pad.r} y1={y(t)} y2={y(t)} stroke="#e4e4e4" />
          <text x={pad.l - 6} y={y(t) + 4} textAnchor="end" fill="#6b6b6b">{fmtRub(t / 1e6, 1)} млн</text></g>
      ))}
      {points.map((p, i) => (
        <g key={i}>
          {p.inflow > 0 && <rect x={x(i) - bw / 2} y={y(p.inflow)} width={bw} height={y(0) - y(p.inflow)} fill="#2e9e5b" opacity={0.5} />}
          {p.outflow > 0 && <rect x={x(i) - bw / 2} y={y(0)} width={bw} height={y(-p.outflow) - y(0)} fill="#ff3131" opacity={0.4} />}
        </g>
      ))}
      <path d={`${line} L${x(points.length - 1)},${y(0)} L${x(0)},${y(0)} Z`} fill="#ff3131" opacity={0.08} />
      <path d={line} fill="none" stroke="#000" strokeWidth={2} />
      <line x1={pad.l} x2={width - pad.r} y1={y(0)} y2={y(0)} stroke="#000" strokeWidth={1} />
      {bals[minIdx] < 0 && (
        <g><circle cx={x(minIdx)} cy={y(bals[minIdx])} r={4} fill="#ff3131" />
          <text x={Math.min(x(minIdx) + 6, width - 160)} y={y(bals[minIdx]) - 6} fill="#ff3131" fontWeight={700}>разрыв {fmtRub(bals[minIdx] / 1e6, 1)} млн</text></g>
      )}
      {[0, Math.floor(points.length / 2), points.length - 1].map((i) => (
        <text key={i} x={x(i)} y={height - 8} textAnchor="middle" fill="#6b6b6b">{new Date(points[i].on).toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", year: "2-digit" })}</text>
      ))}
    </svg>
  );
}
