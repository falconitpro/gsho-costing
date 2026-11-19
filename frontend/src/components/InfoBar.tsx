import { useEffect, useState } from "react";
import { api } from "../api";

/** Курсы ЦБ РФ в шапке (КД, ФД, ЗГД) (всегда с датой, на которую установлен курс).
 *  Единственное место в системе, где курсы показываются «для сведения»; в расчётах — свои курсы на дату расчёта. */

type Rate = { currency: string; rate: number; on: string; prev_rate: number | null; prev_on: string | null; delta: number | null; next_rate: number | null; next_on: string | null; source: string };

const fmtShort = (iso: string) => { const [y, m, d] = iso.split("-"); return `${d}.${m}.${y}`; };
const fmtRate = (v: number) => v.toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const SIGN: Record<string, string> = { USD: "$", EUR: "€", CNY: "¥" };

export default function InfoBar() {
  const [rates, setRates] = useState<Rate[]>([]);

  useEffect(() => {
    let alive = true;
    const load = () => api.bg<{ rates: Rate[] }>("/api/refs/fx-today").then((d) => { if (alive) setRates(d.rates); }).catch(() => { /* курсы ещё не загружены — блок показывает только дату и время */ });
    load();
    const t = setInterval(load, 30 * 60_000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  const onDate = rates[0]?.on;
  if (rates.length === 0) return null;
  const title = rates.length === 0 ? "Курсы ЦБ РФ ещё не загружены" : rates.map((r) => {
    const base = `${r.currency}: ${fmtRate(r.rate)} ₽ — курс ЦБ РФ на ${fmtShort(r.on)}`;
    const prev = r.prev_rate != null && r.prev_on ? `; ${fmtShort(r.prev_on)}: ${fmtRate(r.prev_rate)} (${r.delta! >= 0 ? "+" : "−"}${fmtRate(Math.abs(r.delta!))})` : "";
    const next = r.next_rate != null && r.next_on ? `; на ${fmtShort(r.next_on)}: ${fmtRate(r.next_rate)}` : "";
    return base + prev + next;
  }).join("\n");

  return (
    <div className="infobar" title={title} aria-label="Курсы ЦБ РФ">
      {rates.length > 0 && (
        <div className="infobar-fx">
          {rates.map((r) => (
            <span key={r.currency} className="infobar-rate">
              <span className="cur">{SIGN[r.currency] || r.currency}</span>
              <b>{fmtRate(r.rate)}</b>
              {r.delta != null && Math.abs(r.delta) >= 0.005 && (
                <small className={r.delta > 0 ? "up" : "down"} aria-label={r.delta > 0 ? "вырос" : "снизился"}>{r.delta > 0 ? "▲" : "▼"}{fmtRate(Math.abs(r.delta))}</small>)}
            </span>))}
          {onDate && <span className="infobar-src">ЦБ РФ на {fmtShort(onDate).slice(0, 5)}</span>}
        </div>)}
    </div>
  );
}
