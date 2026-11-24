import { useEffect, useState } from "react";
import { Loading } from "../components/States";

/** Ответ логистической компании по ссылке (D-107): без входа в CRM, только эта страница. */
type View = {
  no: string; answer_by: string; delivery: string; readiness: string; basis: string[];
  pickup: { factory: string; address: string }[];
  services: { code: string; title: string }[];
  positions: { machine_type: string; designation: string; qty: number; basis: string; delivery_point: string; rows: { name: string; unit: string; value: string }[] }[];
  currencies: string[];
  answer: null | { carrier: string; contact: string; valid_until: string; comment: string; lines: { scope: string; amount: number; currency: string; transit_days: number | null }[] };
};
type Line = { amount: string; currency: string; transit_days: string };

const ruDate = (s: string) => (s ? new Date(s).toLocaleDateString("ru-RU") : "—");

export default function PublicQuotePage({ token }: { token: string }) {
  const [v, setV] = useState<View | null>(null);
  const [err, setErr] = useState("");
  const [done, setDone] = useState("");
  const [busy, setBusy] = useState(false);
  const [carrier, setCarrier] = useState("");
  const [contact, setContact] = useState("");
  const [valid, setValid] = useState("");
  const [comment, setComment] = useState("");
  const [lines, setLines] = useState<Record<string, Line>>({});

  useEffect(() => {
    document.title = "Ответ на запрос расчёта доставки";
    fetch(`/api/public/quote/${encodeURIComponent(token)}`).then(async (r) => {
      const j = await r.json().catch(() => ({}));
      if (!r.ok) { setErr(typeof j.detail === "string" ? j.detail : "Ссылка не действует"); return; }
      const d = j as View;
      setV(d);
      const a = d.answer;
      if (a) { setCarrier(a.carrier || ""); setContact(a.contact || ""); setValid(a.valid_until || ""); setComment(a.comment || ""); }
      setLines(Object.fromEntries(d.services.map((s) => {
        const l = a?.lines.find((x) => x.scope === s.code);
        return [s.code, { amount: l ? String(l.amount) : "", currency: l?.currency || "RUB", transit_days: l?.transit_days ? String(l.transit_days) : "" }];
      })));
    }).catch(() => setErr("Сервер недоступен, попробуйте позже"));
  }, [token]);

  async function submit() {
    const ls = Object.entries(lines).filter(([, l]) => l.amount.trim())
      .map(([scope, l]) => ({ scope, amount: Number(l.amount.replace(/\s/g, "").replace(",", ".")), currency: l.currency, transit_days: l.transit_days ? Number(l.transit_days) : null }));
    if (!carrier.trim()) { setErr("Укажите название компании"); return; }
    if (!ls.length || ls.some((l) => !(l.amount > 0))) { setErr("Укажите стоимость хотя бы по одной услуге (число больше нуля)"); return; }
    setBusy(true); setErr("");
    try {
      const r = await fetch(`/api/public/quote/${encodeURIComponent(token)}`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ carrier, contact, valid_until: valid, comment, lines: ls }) });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) { const d = j.detail; setErr(typeof d === "string" ? d : Array.isArray(d) ? "Проверьте заполнение полей" : "Не удалось отправить"); return; }
      setDone(j.replaced ? "Ответ обновлён. Спасибо!" : "Ответ получен. Спасибо!");
    } catch { setErr("Сервер недоступен, попробуйте позже"); } finally { setBusy(false); }
  }

  const wrap = (body: React.ReactNode) => (
    <div style={{ maxWidth: 860, margin: "24px auto", padding: "0 16px", fontFamily: "inherit" }}>
      <div style={{ fontWeight: 700, fontSize: 13, letterSpacing: 0.4, color: "var(--accent)", marginBottom: 8 }}>ООО «БЕРГ-ХАУС»</div>
      {body}
    </div>);
  if (err && !v) return wrap(<div className="card"><h2 style={{ marginTop: 0 }}>Ответ на запрос</h2><p>{err}</p></div>);
  if (!v) return wrap(<Loading p />);
  if (done) return wrap(<div className="card"><h2 style={{ marginTop: 0 }}>{done}</h2><p className="muted">Ответ можно изменить по этой же ссылке до {ruDate(v.answer_by)} и ещё две недели после.</p>
    <button className="btn ghost" onClick={() => setDone("")}>Изменить ответ</button></div>);

  return wrap(
    <>
      <h1 style={{ fontSize: 22, margin: "0 0 4px" }}>Запрос на расчёт доставки № {v.no}</h1>
      <p className="muted" style={{ marginTop: 0 }}>Ответ просим до {ruDate(v.answer_by)}. Файл с расчётом, если он есть, пришлите, пожалуйста, ответом на письмо.</p>
      <div className="card">
        <div><b>Откуда:</b> {v.pickup.map((p, i) => <div key={i}>{p.factory}{p.address ? ` — ${p.address}` : ""}</div>)}</div>
        <div style={{ marginTop: 6 }}><b>Куда:</b> {v.delivery || "—"}</div>
        <div style={{ marginTop: 6 }}><b>Базис:</b> {v.basis.join(", ")}{v.readiness && <> · <b>готовность груза:</b> ~{ruDate(v.readiness)}</>}</div>
        <h3 style={{ margin: "12px 0 6px" }}>Груз</h3>
        {v.positions.map((p, i) => (
          <div key={i} style={{ marginBottom: 8 }}>
            <b>{p.qty} × {[p.machine_type, p.designation].filter(Boolean).join(" ")}</b>
            {p.rows.length > 0 && <div className="muted" style={{ fontSize: 13 }}>{p.rows.map((r) => `${r.name}: ${r.value}${r.unit ? " " + r.unit : ""}`).join(" · ")}</div>}
          </div>))}
      </div>
      <div className="card">
        <h3 style={{ marginTop: 0 }}>Ваш ответ</h3>
        <div className="row" style={{ gap: 12, flexWrap: "wrap" }}>
          <label className="f" style={{ flex: "1 1 260px" }}>Компания *<input value={carrier} onChange={(e) => setCarrier(e.target.value)} /></label>
          <label className="f" style={{ flex: "1 1 260px" }}>Контактное лицо, телефон<input value={contact} onChange={(e) => setContact(e.target.value)} /></label>
        </div>
        <table className="table" style={{ marginTop: 8 }}>
          <thead><tr><th>Услуга</th><th>Стоимость</th><th>Валюта</th><th>Срок, дней</th></tr></thead>
          <tbody>{v.services.map((s) => { const l = lines[s.code] || { amount: "", currency: "RUB", transit_days: "" };
            const set = (p: Partial<Line>) => setLines({ ...lines, [s.code]: { ...l, ...p } });
            return <tr key={s.code}><td>{s.title}</td>
              <td><input inputMode="decimal" value={l.amount} placeholder="не входит" style={{ width: 140 }} onChange={(e) => set({ amount: e.target.value })} /></td>
              <td><select value={l.currency} onChange={(e) => set({ currency: e.target.value })}>{v.currencies.map((c) => <option key={c}>{c}</option>)}</select></td>
              <td><input inputMode="numeric" value={l.transit_days} style={{ width: 80 }} onChange={(e) => set({ transit_days: e.target.value.replace(/\D/g, "") })} /></td></tr>; })}</tbody>
        </table>
        <p className="muted" style={{ fontSize: 12 }}>Суммы — без НДС. Оставьте стоимость пустой, если услугу не оказываете.</p>
        <div className="row" style={{ gap: 12, flexWrap: "wrap" }}>
          <label className="f">Предложение действует до<input type="date" value={valid} onChange={(e) => setValid(e.target.value)} /></label>
          <label className="f" style={{ flex: "1 1 360px" }}>Условия, что не входит<textarea rows={3} value={comment} onChange={(e) => setComment(e.target.value)} style={{ width: "100%" }} /></label>
        </div>
        {err && <div className="alert error" style={{ marginTop: 8 }}>{err}</div>}
        <button className="btn" style={{ marginTop: 10 }} disabled={busy} onClick={submit}>{v.answer ? "Обновить ответ" : "Отправить ответ"}</button>
      </div>
    </>);
}
