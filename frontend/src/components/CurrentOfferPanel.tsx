import { useEffect, useState } from "react";
import { api, fmtCur, fmtDate } from "../api";

/** «Действующее предложение» (D-103): цена, которую клиент видит сейчас, и сверка мест хранения цены. Только чтение. */
type Money = { net_rub: number; gross_rub: number; net: number; gross: number; unit_net: number; unit_gross: number };
type Variant = {
  id: string; name: string; units: number; currency: string; currency_set: boolean; rate: number | null; vat_pct: number;
  price: Money | null; first: Money | null; change_pct: number | null; negotiated: boolean; won: boolean;
  source: { kind: "revision" | "version" | "draft"; title: string; on: string | null; sent: boolean };
  places: { key: string; title: string; price_rub: number | null }[];
  issues: { level: "info" | "warn" | "error"; text: string }[];
};
type Data = { approved: boolean; variants: Variant[]; issues_count: number };

const ICON = { info: "ℹ", warn: "⚠", error: "⛔" } as const;

export default function CurrentOfferPanel({ dealId, onOpen }: { dealId: string; onOpen?: () => void }) {
  const [d, setD] = useState<Data | null>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => { api.get<Data>(`/api/deals/${dealId}/current-offer`).then(setD).catch(() => setD(null)); }, [dealId]);
  if (!d || !d.variants.length) return null;
  const many = d.variants.length > 1;
  return (
    <div className="card" style={{ margin: "4px 0 12px" }} data-testid="current-offer">
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <h3 style={{ margin: 0 }}>Действующее предложение</h3>
        {d.issues_count > 0
          ? <a href="#" style={{ fontSize: 12 }} onClick={(e) => { e.preventDefault(); setOpen(true); }}>⚠ расхождения: {d.issues_count}</a>
          : <span className="muted" style={{ fontSize: 12 }}>цена сходится во всех местах</span>}
        <a href="#" className="muted" style={{ fontSize: 12, marginLeft: "auto" }} onClick={(e) => { e.preventDefault(); setOpen(!open); }}>
          {open ? "скрыть сверку" : "откуда цена"}</a>
        {onOpen && <a href="#" style={{ fontSize: 12 }} onClick={(e) => { e.preventDefault(); onOpen(); }}>расчёт и КП →</a>}
      </div>
      {d.variants.map((v) => {
        const p = v.price;
        const cur = v.currency;
        return (
          <div key={v.id} style={{ marginTop: 8 }}>
            {many && <div style={{ fontWeight: 600, fontSize: 13 }}>{v.name}{v.won && <span className="muted"> · выбрал клиент</span>}</div>}
            {p && (
              <div style={{ display: "flex", gap: 24, flexWrap: "wrap", alignItems: "baseline" }}>
                <div>
                  <div style={{ fontSize: 20, fontWeight: 700 }}>{fmtCur(p.unit_net)} {cur}</div>
                  <div className="muted" style={{ fontSize: 12 }}>за 1 шт. без НДС · с НДС {v.vat_pct} % — {fmtCur(p.unit_gross)} {cur}</div>
                </div>
                {v.units > 1 && <div>
                  <div style={{ fontSize: 15, fontWeight: 600 }}>{fmtCur(p.net)} {cur}</div>
                  <div className="muted" style={{ fontSize: 12 }}>за {v.units} шт. без НДС · с НДС {fmtCur(p.gross)} {cur}</div>
                </div>}
                {cur !== "RUB" && <div>
                  <div style={{ fontSize: 15 }}>{fmtCur(p.net_rub)} RUB</div>
                  <div className="muted" style={{ fontSize: 12 }}>в рублях по курсу цены {v.rate ? fmtCur(v.rate) : "—"}</div>
                </div>}
                <div className="muted" style={{ fontSize: 12 }}>
                  источник: {v.source.title}{v.source.on ? ` от ${fmtDate(v.source.on)}` : ""}
                  {v.change_pct ? ` · ${v.change_pct > 0 ? "+" : ""}${String(v.change_pct).replace(".", ",")} % к первой цене КП` : ""}
                </div>
              </div>)}
            {open && (
              <div style={{ marginTop: 6 }}>
                <table className="table" style={{ fontSize: 12, width: "auto" }}>
                  <thead><tr><th>Где хранится цена</th><th style={{ textAlign: "right" }}>без НДС, RUB</th><th></th></tr></thead>
                  <tbody>{v.places.map((x) => {
                    const same = x.price_rub != null && p && Math.abs(x.price_rub - p.net_rub) < 1;
                    return <tr key={x.key}><td>{x.title}</td><td style={{ textAlign: "right" }}>{x.price_rub == null ? "—" : fmtCur(x.price_rub)}</td>
                      <td className="muted">{same ? "= действующей" : ""}</td></tr>;
                  })}</tbody>
                </table>
                {v.issues.map((i, k) => <div key={k} style={{ fontSize: 12, marginTop: 2 }} className={i.level === "info" ? "muted" : ""}>{ICON[i.level]} {i.text}</div>)}
              </div>)}
          </div>);
      })}
    </div>);
}
