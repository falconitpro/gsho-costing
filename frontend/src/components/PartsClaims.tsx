import { useEffect, useState } from "react";
import { api, fmtDate, fmtRub } from "../api";
import { can, type Me } from "../types";

// Гарантийные претензии к заводу: деталь, наработка, фото → ответ завода → компенсация деталью, деньгами или скидкой (ТЗ запчастей, раздел 9).

type Comp = { id: string; kind: string; qty?: number; amount?: number; currency?: string; amount_rub?: number; note: string; received_on: string | null; po_numbers?: string[] };
type Claim = { id: string; number: string; supplier_id: string; supplier_name: string; client_name: string; machine_title: string; deal_number: string; request_number: string;
  failure_on: string | null; machine_hours: number | null; description: string; status: string; status_title: string; answer_due: string | null; sent_at: string | null;
  answer: string; overdue: boolean; created_at: string; cost_rub: number; compensated_rub: number; close_reason: string;
  lines: { part_id: string | null; article: string; name: string; qty: number; cost_rub: number | null }[]; photos: { i: number; name: string }[]; compensations: Comp[] };
type Ref = { id: string; name: string; short_name?: string };

const KIND: Record<string, string> = { part: "деталью", money: "деньгами", discount: "скидкой на следующий заказ" };
const EMPTY = { supplier_id: "", client_id: "", failure_on: "", machine_hours: "", description: "", lines: "" };

export default function PartsClaims({ me }: { me: Me }) {
  const edit = me.roles.some((r) => ["manager", "service_head", "cd", "gd", "purchasing"].includes(r.code));
  const seesCost = can(me, "cost");
  const [list, setList] = useState<Claim[]>([]);
  const [openOnly, setOpenOnly] = useState(true);
  const [cur, setCur] = useState<Claim | null>(null);
  const [form, setForm] = useState<typeof EMPTY | null>(null);
  const [sups, setSups] = useState<Ref[]>([]);
  const [clients, setClients] = useState<Ref[]>([]);
  const [msg, setMsg] = useState("");
  const [comp, setComp] = useState({ kind: "part", amount: "", currency: "CNY", note: "" });
  const [answer, setAnswer] = useState({ status: "accepted", answer: "" });
  const load = () => api.get<Claim[]>(`/api/parts/claims?open_only=${openOnly}`).then(setList).catch((e) => setMsg((e as Error).message));
  useEffect(() => { api.get<Ref[]>("/api/refs/factories").then(setSups).catch(() => setSups([])); api.get<Ref[]>("/api/refs/clients").then(setClients).catch(() => setClients([])); }, []);
  useEffect(() => { load(); }, [openOnly]);
  async function act(fn: () => Promise<Claim>, ok = "") { try { setCur(await fn()); if (ok) setMsg(ok); load(); } catch (e) { setMsg((e as Error).message); } }
  function create() {
    if (!form) return;
    const lines = form.lines.split("\n").map((s) => s.split(/\t|;/)).filter((x) => x[0]?.trim())
      .map((x) => ({ article: x[0].trim(), qty: Number((x[1] || "1").replace(",", ".")) || 1, cost_rub: x[2] ? Number(x[2].replace(",", ".")) : null }));
    act(() => api.post<Claim>("/api/parts/claims", { supplier_id: form.supplier_id, client_id: form.client_id || null, failure_on: form.failure_on || null,
      machine_hours: form.machine_hours ? Number(form.machine_hours) : null, description: form.description, lines }), "Претензия создана — приложите фото и отправьте заводу")
      .then(() => setForm(null));
  }
  const c = cur;
  return (
    <>
      {msg && <div className="alert info">{msg}</div>}
      <div className="card">
        <div className="row"><h2 style={{ margin: 0 }}>Гарантийные претензии к заводам</h2>
          <label className="row" style={{ marginLeft: "auto" }}><input type="checkbox" checked={openOnly} onChange={(e) => setOpenOnly(e.target.checked)} /> только открытые</label>
          {edit && <button className="btn add" onClick={() => { setForm({ ...EMPTY }); setCur(null); }}>Претензия</button>}</div>
        <table style={{ marginTop: 8 }}>
          <thead><tr><th>Номер</th><th>Дата</th><th>Завод</th><th>Клиент / машина</th><th>Статус</th><th>Ответ до</th>{seesCost && <><th className="num">Расход, ₽</th><th className="num">Компенсировано, ₽</th></>}</tr></thead>
          <tbody>{list.map((x) => (
            <tr key={x.id} style={{ cursor: "pointer", background: cur?.id === x.id ? "var(--accent-soft)" : undefined }} onClick={() => { setCur(x); setForm(null); }}>
              <td><b>{x.number}</b></td><td>{fmtDate(x.created_at)}</td><td>{x.supplier_name}</td><td>{x.client_name}{x.machine_title && <div className="muted" style={{ fontSize: 11 }}>{x.machine_title}</div>}</td>
              <td>{x.status_title}</td><td style={{ color: x.overdue ? "var(--bad)" : undefined }}>{fmtDate(x.answer_due)}</td>
              {seesCost && <><td className="num">{fmtRub(x.cost_rub)}</td><td className="num">{fmtRub(x.compensated_rub)}</td></>}</tr>))}
            {list.length === 0 && <tr><td colSpan={8} className="muted">Претензий нет</td></tr>}</tbody>
        </table>
        <p className="muted" style={{ fontSize: 12 }}>Претензию по гарантийной заявке клиента удобнее создать из карточки заявки — строки и машина подставятся сами.</p>
      </div>

      {form && (
        <div className="card">
          <h2 style={{ marginTop: 0 }}>Новая претензия</h2>
          <div className="grid cols-3">
            <label className="f">Завод *<select value={form.supplier_id} onChange={(e) => setForm({ ...form, supplier_id: e.target.value })}><option value="">— выберите —</option>{sups.map((s) => <option key={s.id} value={s.id}>{s.short_name || s.name}</option>)}</select></label>
            <label className="f">Клиент<select value={form.client_id} onChange={(e) => setForm({ ...form, client_id: e.target.value })}><option value="">—</option>{clients.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
            <label className="f">Дата отказа<input type="date" value={form.failure_on} onChange={(e) => setForm({ ...form, failure_on: e.target.value })} /></label>
            <label className="f">Наработка, м·ч<input inputMode="decimal" value={form.machine_hours} onChange={(e) => setForm({ ...form, machine_hours: e.target.value })} /></label>
          </div>
          <label className="f">Описание неисправности<textarea rows={2} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></label>
          <label className="f">Детали: артикул ⇥ количество ⇥ стоимость замены, ₽ (по строке)<textarea rows={4} value={form.lines} onChange={(e) => setForm({ ...form, lines: e.target.value })} /></label>
          <div className="row"><button className="btn" disabled={!form.supplier_id || !form.lines.trim()} onClick={create}>Создать</button><button className="btn ghost" onClick={() => setForm(null)}>отмена</button></div>
        </div>)}

      {c && !form && (
        <div className="card">
          <div className="row" style={{ justifyContent: "space-between" }}><h2 style={{ margin: 0 }}>{c.number} · {c.supplier_name}</h2><span className="muted">{c.status_title}</span></div>
          <p className="muted" style={{ marginTop: 4 }}>{[c.client_name, c.machine_title, c.machine_hours != null && `${c.machine_hours} м·ч`, c.failure_on && `отказ ${fmtDate(c.failure_on)}`,
            c.request_number && `заявка ${c.request_number}`, c.deal_number && `сделка ГШО ${c.deal_number}`].filter(Boolean).join(" · ")}</p>
          {c.description && <p>{c.description}</p>}
          <table><thead><tr><th>Артикул</th><th>Наименование</th><th className="num">Кол-во</th>{seesCost && <th className="num">Стоимость замены, ₽</th>}</tr></thead>
            <tbody>{c.lines.map((l, i) => <tr key={i}><td>{l.article}{!l.part_id && <span className="muted"> (нет в каталоге)</span>}</td><td>{l.name}</td><td className="num">{l.qty}</td>{seesCost && <td className="num">{l.cost_rub != null ? fmtRub(l.cost_rub) : "—"}</td>}</tr>)}</tbody></table>
          <div className="row" style={{ marginTop: 6, flexWrap: "wrap" }}>
            <a className="btn ghost" href={`/api/parts/claims/${c.id}/xlsx`}>Excel для завода</a>
            {c.photos.map((p) => <a key={p.i} href={`/api/parts/claims/${c.id}/photos/${p.i}`} target="_blank" rel="noreferrer">{p.name}</a>)}
            {edit && c.status !== "closed" && <label className="btn ghost" style={{ cursor: "pointer" }}>+ фото<input type="file" accept="image/*" capture="environment" style={{ display: "none" }}
              onChange={(e) => { const f = e.target.files?.[0]; if (f) act(() => api.upload<Claim>(`/api/parts/claims/${c.id}/photos`, f), "Фото добавлено"); e.target.value = ""; }} /></label>}
            {edit && c.status === "draft" && <button className="btn" onClick={() => { const d = prompt("Срок ответа завода, дней", "14"); if (d) act(() => api.post<Claim>(`/api/parts/claims/${c.id}/sent`, { answer_days: Number(d) }), "Отмечено: отправлена заводу"); }}>Отправлена заводу</button>}
          </div>
          {edit && ["sent", "accepted", "partial", "rejected"].includes(c.status) && (
            <div className="row" style={{ marginTop: 8, flexWrap: "wrap" }}>
              <b>Ответ завода:</b>
              <select value={answer.status} onChange={(e) => setAnswer({ ...answer, status: e.target.value })}><option value="accepted">признана</option><option value="partial">частично</option><option value="rejected">отказ</option></select>
              <input style={{ minWidth: 260 }} placeholder="что ответил завод" value={answer.answer} onChange={(e) => setAnswer({ ...answer, answer: e.target.value })} />
              <button className="btn ghost" onClick={() => act(() => api.post<Claim>(`/api/parts/claims/${c.id}/answer`, answer), "Ответ сохранён")}>Сохранить</button>
            </div>)}
          {c.answer && <p className="muted">Завод: {c.answer}</p>}
          <h3>Компенсация</h3>
          {c.compensations.length === 0 && <div className="muted">Не назначена</div>}
          {c.compensations.map((x) => (
            <div key={x.id} className="row"><span>{KIND[x.kind]}{x.kind === "part" ? ` — ${x.qty} шт., заказ ${(x.po_numbers || []).join(", ")}` : ` — ${x.amount} ${x.currency}`}{x.note && ` · ${x.note}`}</span>
              {x.received_on ? <span className="badge">получено {fmtDate(x.received_on)}</span>
                : x.kind === "part" ? <span className="muted">отметится при приёмке заказа на складе</span>
                : edit && <button className="btn ghost" onClick={() => act(() => api.post<Claim>(`/api/parts/claims/${c.id}/compensations/${x.id}/received`, {}), "Компенсация получена")}>получено</button>}</div>))}
          {edit && ["accepted", "partial"].includes(c.status) && (
            <div className="row" style={{ marginTop: 6, flexWrap: "wrap" }}>
              <select value={comp.kind} onChange={(e) => setComp({ ...comp, kind: e.target.value })}>{Object.entries(KIND).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select>
              {comp.kind !== "part" && <><input inputMode="decimal" style={{ width: 100 }} placeholder="сумма" value={comp.amount} onChange={(e) => setComp({ ...comp, amount: e.target.value })} />
                <select value={comp.currency} onChange={(e) => setComp({ ...comp, currency: e.target.value })}>{["CNY", "USD", "EUR", "RUB"].map((x) => <option key={x}>{x}</option>)}</select></>}
              <input placeholder="комментарий" value={comp.note} onChange={(e) => setComp({ ...comp, note: e.target.value })} />
              <button className="btn" onClick={() => act(() => api.post<Claim>(`/api/parts/claims/${c.id}/compensations`, { ...comp, amount: comp.amount ? Number(comp.amount) : null }),
                comp.kind === "part" ? "Создан заказ заводу со строками «гарантийная замена» по цене 0 — отправьте его во вкладке «Заказы поставщикам»" : "Компенсация добавлена")}>Добавить</button>
            </div>)}
          {edit && c.status !== "closed" && <button className="btn ghost" style={{ marginTop: 8 }} onClick={() => { const r = prompt("Причина закрытия", c.status === "rejected" ? "отказ завода" : "компенсировано"); if (r !== null) act(() => api.post<Claim>(`/api/parts/claims/${c.id}/close`, { reason: r }), "Претензия закрыта"); }}>закрыть претензию</button>}
          {c.close_reason && <p className="muted">{c.close_reason}</p>}
        </div>)}
    </>
  );
}
