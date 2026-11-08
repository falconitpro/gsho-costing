import { useEffect, useState, type ReactNode } from "react";
import { api } from "../api";

// Претензии по договорам: реестр, карточка (строки требований, подпись КД / ЗГД, отправка, ответ, суд / закрытие), печатная форма.

type Line = { kind: string; kind_title?: string; key?: string; penalty_key?: string; title: string; amount: number | null; days?: number; due_on?: string | null; rate_text?: string };
export type ClaimRow = { id: string; contract_id: string; contract_number: string; cp_name: string; direction: string; direction_title: string; number: string; status: string; status_title: string;
  currency: string; debt: number; penalty: number; total: number; subject: string; sent_on: string | null; answer_due: string | null; answer_overdue: boolean; result: string; result_title: string;
  result_amount: number | null; source: string; signer_name: string; created_at: string | null };
type Claim = ClaimRow & { lines: Line[]; demand: string; answer_days: number; issued_on: string | null; signer_role: string; signed_at: string | null; sent_via: string; track: string;
  answer_on: string | null; answer_text: string; files: { id: string; kind: string; name: string; uploaded_by_name: string; uploaded_at: string }[];
  history: { at: string; by_name: string; action: string; comment: string }[]; statuses: Record<string, string>;
  catalog: { results: Record<string, string>; sent_via: Record<string, string>; line_kinds: Record<string, string> }; can_work: boolean; can_sign: boolean };

const d = (s?: string | null) => (s ? new Date(s).toLocaleDateString("ru-RU") : "—");
const money = (v: number | null | undefined, cur = "") => (v == null ? "—" : `${v.toLocaleString("ru-RU", { maximumFractionDigits: 2 })}${cur ? " " + cur : ""}`);
const TONE: Record<string, string> = { draft: "", signing: "warn", signed: "info", sent: "info", received: "warn", answered: "ok", court: "bad", closed: "", cancelled: "" };
const FILE_KINDS: Record<string, string> = { form: "Печатная форма", signed_scan: "Подписанный скан", post_receipt: "Почтовая квитанция / опись", answer: "Ответ на претензию", incoming: "Входящая претензия", other: "Прочее" };

export default function ClaimsBlock() {
  const [data, setData] = useState<{ items: ClaimRow[]; summary: Record<string, number> } | null>(null);
  const [f, setF] = useState({ direction: "", status: "", q: "" });
  const [open, setOpen] = useState<string | null>(new URLSearchParams(window.location.search).get("open"));
  const [msg, setMsg] = useState("");
  const load = () => api.get<{ items: ClaimRow[]; summary: Record<string, number> }>(`/api/legal/claims?${new URLSearchParams(f)}`).then(setData).catch((e) => setMsg((e as Error).message));
  useEffect(() => { load(); }, [f.direction, f.status, f.q]);
  if (!data) return <div className="card">{msg || "Загрузка…"}</div>;
  const s = data.summary;
  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <div>
          <b>Реестр претензий</b>
          <div className="muted" style={{ fontSize: 12 }}>в работе: <b>{s.open || 0}</b> · черновики: {s.draft || 0}{s.signing ? <> · <span style={{ color: "var(--warn)" }}>на подписи: <b>{s.signing}</b></span></> : null}
            {s.answer_overdue ? <> · <span style={{ color: "var(--bad)" }}>истёк срок ответа: <b>{s.answer_overdue}</b></span></> : null}
            {s.outgoing_rub ? <> · наши требования: <b>{money(s.outgoing_rub, "₽")}</b></> : null}{s.incoming_rub ? <> · к нам: <b style={{ color: "var(--bad)" }}>{money(s.incoming_rub, "₽")}</b></> : null}</div>
        </div>
        <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
          <input placeholder="номер, договор, контрагент…" value={f.q} onChange={(e) => setF({ ...f, q: e.target.value })} style={{ width: 200 }} />
          <select value={f.direction} onChange={(e) => setF({ ...f, direction: e.target.value })}><option value="">все</option><option value="outgoing">исходящие</option><option value="incoming">входящие</option></select>
          <select value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}><option value="">все статусы</option>
            {[["draft", "черновик"], ["signing", "на подписи"], ["signed", "подписана"], ["sent", "отправлена"], ["received", "получена"], ["answered", "ответ"], ["court", "суд"], ["closed", "закрыта"]].map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select>
        </div>
      </div>
      {msg && <div className="alert error" style={{ marginTop: 8 }}>{msg}</div>}
      <ClaimsTable items={data.items} onOpen={setOpen} empty="Претензий нет. Черновик исходящей претензии появляется сам на 5-й день просрочки контрагента; вручную — во вкладке «Исполнение» карточки договора." />
      {open && <ClaimCard id={open} onClose={() => { setOpen(null); load(); }} />}
    </div>);
}

export function ClaimsTable({ items, onOpen, empty }: { items: ClaimRow[]; onOpen: (id: string) => void; empty: string }) {
  return (
    <div style={{ overflowX: "auto" }}>
      <table className="table" style={{ marginTop: 8, fontSize: 13 }}>
        <thead><tr><th>Номер</th><th>Договор · контрагент</th><th>Суть</th><th className="num">Сумма</th><th>Отправлена</th><th>Ответ до</th><th>Статус</th></tr></thead>
        <tbody>
          {items.length === 0 && <tr><td colSpan={7} className="muted">{empty}</td></tr>}
          {items.map((r) => (
            <tr key={r.id} onClick={() => onOpen(r.id)} style={{ cursor: "pointer" }}>
              <td style={{ whiteSpace: "nowrap" }}><b>{r.number}</b><div className="muted" style={{ fontSize: 11 }}>{r.direction === "outgoing" ? "исходящая" : "входящая"}{r.source === "auto" ? " · авто" : ""}</div></td>
              <td>{r.contract_number}<div className="muted" style={{ fontSize: 11 }}>{r.cp_name}</div></td>
              <td>{r.subject}</td>
              <td className="num" style={{ whiteSpace: "nowrap" }}><b>{money(r.total, r.currency)}</b>{r.penalty ? <div className="muted" style={{ fontSize: 11 }}>неустойка {money(r.penalty)}</div> : null}</td>
              <td>{d(r.sent_on)}</td>
              <td style={{ whiteSpace: "nowrap", color: r.answer_overdue ? "var(--bad)" : undefined }}>{d(r.answer_due)}{r.answer_overdue && <div style={{ fontSize: 11 }}>срок истёк</div>}</td>
              <td><span className={`badge ${TONE[r.status] || ""}`}>{r.status_title}</span>{r.result_title && <div className="muted" style={{ fontSize: 11 }}>{r.result_title}</div>}</td>
            </tr>))}
        </tbody>
      </table>
    </div>);
}

// ------------------------------------------------------------------ создание из договора
export function NewClaimDialog({ contractId, available, direction, onClose, onCreated }: { contractId: string; available: Line[]; direction: "outgoing" | "incoming"; onClose: () => void; onCreated: (id: string) => void }) {
  const keys = Array.from(new Set(available.map((x) => x.key).filter(Boolean))) as string[];
  const [sel, setSel] = useState<string[]>(keys);
  const [manual, setManual] = useState<Line[]>(direction === "incoming" && available.length === 0 ? [{ kind: "penalty", title: "Неустойка по претензии контрагента", amount: null }] : []);
  const [number, setNumber] = useState("");
  const [issued, setIssued] = useState(new Date().toISOString().slice(0, 10));
  const [demand, setDemand] = useState("");
  const [err, setErr] = useState("");
  const chosen = available.filter((x) => x.key && sel.includes(x.key));
  const total = [...chosen, ...manual].reduce((s, x) => s + (Number(x.amount) || 0), 0);
  const save = () => api.post<{ id: string }>(`/api/legal/contracts/${contractId}/claims`, { direction, lines: [...chosen, ...manual], demand, number, issued_on: direction === "incoming" ? issued : null })
    .then((x) => onCreated(x.id)).catch((e) => setErr((e as Error).message));
  return (
    <Modal title={direction === "outgoing" ? "Новая претензия контрагенту" : "Входящая претензия (требования к нам)"} onClose={onClose}>
      <div className="poa-body">
        {err && <div className="alert error" style={{ marginBottom: 10 }}>{err}</div>}
        {keys.length > 0 && <div className="poa-section"><div className="poa-section-head"><b>Просроченные обязательства</b><span className="muted">с неустойкой на сегодня</span></div>
          {keys.map((k) => (
            <label key={k} className="row" style={{ gap: 8, fontSize: 13, alignItems: "flex-start", padding: "4px 0" }}>
              <input type="checkbox" style={{ width: "auto", minHeight: 0, marginTop: 3 }} checked={sel.includes(k)} onChange={(e) => setSel(e.target.checked ? [...sel, k] : sel.filter((x) => x !== k))} />
              <span>{available.filter((x) => x.key === k).map((x, i) => <div key={i}>{x.title} — <b>{x.amount != null ? money(x.amount) : "исполнить"}</b>{x.days ? <span className="muted"> · {x.days} дн.</span> : null}</div>)}</span>
            </label>))}
        </div>}
        <div className="poa-section" style={{ marginTop: 12 }}>
          <div className="poa-section-head" style={{ justifyContent: "space-between" }}><b>Дополнительные требования</b>
            <button className="btn ghost add" onClick={() => setManual([...manual, { kind: "penalty", title: "", amount: null }])}>Строка</button></div>
          {manual.map((m, i) => (
            <div key={i} className="row" style={{ gap: 6, marginBottom: 6 }}>
              <select value={m.kind} onChange={(e) => setManual(manual.map((x, j) => (j === i ? { ...x, kind: e.target.value } : x)))} style={{ width: 150 }}><option value="debt">долг</option><option value="penalty">неустойка / штраф</option><option value="performance">исполнение</option></select>
              <input placeholder="основание" value={m.title} onChange={(e) => setManual(manual.map((x, j) => (j === i ? { ...x, title: e.target.value } : x)))} style={{ flex: 1 }} />
              <input type="number" placeholder="сумма" value={m.amount ?? ""} onChange={(e) => setManual(manual.map((x, j) => (j === i ? { ...x, amount: e.target.value === "" ? null : Number(e.target.value) } : x)))} style={{ width: 120 }} />
              <button className="btn ghost" style={{ padding: "0 6px" }} onClick={() => setManual(manual.filter((_, j) => j !== i))}>×</button>
            </div>))}
          {direction === "incoming" && <div className="poa-form" style={{ marginTop: 8 }}>
            <F label="Входящий номер"><input value={number} onChange={(e) => setNumber(e.target.value)} /></F>
            <F label="Дата получения"><input type="date" value={issued} onChange={(e) => setIssued(e.target.value)} /></F></div>}
          {direction === "outgoing" && <F label="Ещё требуем (необязательно)"><textarea rows={2} value={demand} onChange={(e) => setDemand(e.target.value)} placeholder="например: подписать акт сверки" /></F>}
        </div>
      </div>
      <div className="poa-footer"><span className="muted">итого: <b>{money(total)}</b></span>
        <div className="row" style={{ gap: 8 }}><button className="btn ghost" onClick={onClose}>Отмена</button><button className="btn" disabled={!chosen.length && !manual.some((m) => m.title.trim())} onClick={save}>Создать</button></div></div>
    </Modal>);
}

// ------------------------------------------------------------------ карточка
export function ClaimCard({ id, onClose }: { id: string; onClose: () => void }) {
  const [c, setC] = useState<Claim | null>(null);
  const [err, setErr] = useState("");
  const [info, setInfo] = useState("");
  const [comment, setComment] = useState("");
  const [send, setSend] = useState({ sent_on: new Date().toISOString().slice(0, 10), sent_via: "post", track: "" });
  const [ans, setAns] = useState({ result: "satisfied", amount: "", answer_on: new Date().toISOString().slice(0, 10) });
  const [fileKind, setFileKind] = useState("signed_scan");
  const load = () => api.get<Claim>(`/api/legal/claims/${id}`).then(setC).catch((e) => setErr((e as Error).message));
  useEffect(() => { load(); }, [id]);
  if (!c) return <Modal title="Претензия" onClose={onClose}><div className="poa-body">{err || "Загрузка…"}</div></Modal>;
  const step = (path: string, body: object = {}) => api.post<Claim>(`/api/legal/claims/${id}/${path}`, { comment, ...body }).then((x) => { setC(x); setErr(""); setComment(""); setInfo(x.status_title); }).catch((e) => setErr((e as Error).message));
  const out = c.direction === "outgoing";
  return (
    <Modal title={`Претензия ${c.number} — ${c.cp_name}`} onClose={onClose} wide>
      <div className="poa-body view">
        <div className="poa-head-line">
          <span className={`badge ${TONE[c.status] || ""}`}>{c.status_title}</span><span className="badge">{c.direction_title}</span>
          <span className="muted">договор {c.contract_number} · {c.subject}{c.signer_name ? ` · подписал ${c.signer_name}` : ""}</span>
        </div>
        {err && <div className="alert error">{err}</div>}
        {info && <div className="alert ok" onClick={() => setInfo("")}>{info}</div>}
        {c.answer_overdue && <div className="alert warn">Срок ответа истёк {d(c.answer_due)} — решите: суд, повторная претензия или закрыть.</div>}
        <div className="poa-section">
          <div className="poa-section-head" style={{ justifyContent: "space-between" }}><b>Требования</b>
            {c.status === "draft" && c.can_work && <button className="btn ghost" onClick={() => step("refresh")}>Пересчитать на сегодня</button>}</div>
          <table className="table" style={{ fontSize: 13 }}>
            <thead><tr><th>Вид</th><th>Основание</th><th>Срок</th><th className="num">Дней</th><th>Ставка</th><th className="num">Сумма</th></tr></thead>
            <tbody>{c.lines.map((x, i) => (
              <tr key={i}><td>{x.kind_title}</td><td>{x.title}</td><td>{d(x.due_on)}</td><td className="num">{x.days || ""}</td><td style={{ fontSize: 12 }}>{x.rate_text || ""}</td><td className="num"><b>{x.amount != null ? money(x.amount, c.currency) : "исполнить"}</b></td></tr>))}
              <tr><td colSpan={5}><b>Итого</b>{c.debt ? <span className="muted"> · долг {money(c.debt)}</span> : null}{c.penalty ? <span className="muted"> · неустойка {money(c.penalty)}</span> : null}</td><td className="num"><b>{money(c.total, c.currency)}</b></td></tr></tbody>
          </table>
          {c.demand && <div style={{ fontSize: 13, marginTop: 6 }}>Ещё требуем: {c.demand}</div>}
          <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>Срок ответа — {c.answer_days} дн. с получения{c.sent_on ? ` · отправлена ${d(c.sent_on)} ${c.catalog.sent_via[c.sent_via] || ""}${c.track ? `, трек ${c.track}` : ""}` : ""}{c.answer_due ? ` · ответ до ${d(c.answer_due)}` : ""}</div>
          {c.result && <div style={{ fontSize: 13, marginTop: 6 }}><b>Ответ {d(c.answer_on)}:</b> {c.result_title}{c.result === "partial" ? ` — ${money(c.result_amount, c.currency)}` : ""}{c.answer_text ? ` · ${c.answer_text}` : ""}</div>}
        </div>

        {out && c.status === "signed" && c.can_work && (
          <div className="poa-section" style={{ marginTop: 12 }}><div className="poa-section-head"><b>Отправка контрагенту</b><span className="muted">после отправки неустойки получат статус «предъявлена»</span></div>
            <div className="poa-form" style={{ gridTemplateColumns: "1fr 1fr 1fr auto", alignItems: "end" }}>
              <F label="Дата отправки"><input type="date" value={send.sent_on} onChange={(e) => setSend({ ...send, sent_on: e.target.value })} /></F>
              <F label="Способ"><select value={send.sent_via} onChange={(e) => setSend({ ...send, sent_via: e.target.value })}>{Object.entries(c.catalog.sent_via).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select></F>
              <F label="Трек / номер в ЭДО"><input value={send.track} onChange={(e) => setSend({ ...send, track: e.target.value })} /></F>
              <button className="btn" onClick={() => step("send", send)}>Отправлена</button>
            </div></div>)}
        {["sent", "received", "signed"].includes(c.status) && c.can_work && (
          <div className="poa-section" style={{ marginTop: 12 }}><div className="poa-section-head"><b>{out ? "Ответ контрагента" : "Наш ответ"}</b><span className="muted">удовлетворена → неустойки «признаны» (частично — пропорционально)</span></div>
            <div className="poa-form" style={{ gridTemplateColumns: "1fr 1fr 1fr auto", alignItems: "end" }}>
              <F label="Результат"><select value={ans.result} onChange={(e) => setAns({ ...ans, result: e.target.value })}>{Object.entries(c.catalog.results).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select></F>
              <F label="Признанная сумма"><input type="number" disabled={ans.result !== "partial"} value={ans.amount} onChange={(e) => setAns({ ...ans, amount: e.target.value })} /></F>
              <F label="Дата ответа"><input type="date" value={ans.answer_on} onChange={(e) => setAns({ ...ans, answer_on: e.target.value })} /></F>
              <button className="btn" onClick={() => step("answer", { ...ans, amount: ans.amount ? Number(ans.amount) : null })}>Записать</button>
            </div></div>)}

        <div className="poa-section" style={{ marginTop: 12 }}>
          <div className="poa-section-head" style={{ justifyContent: "space-between" }}><b>Документы</b>
            <span className="row" style={{ gap: 8 }}>
              {out && <><a className="btn ghost" href={`/api/legal/claims/${id}/print?fmt=docx`}>DOCX</a><a className="btn ghost" href={`/api/legal/claims/${id}/print?fmt=pdf`} target="_blank" rel="noreferrer">PDF</a></>}
              {c.can_work && <><select value={fileKind} onChange={(e) => setFileKind(e.target.value)}>{Object.entries(FILE_KINDS).filter(([k]) => k !== "form").map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select>
                <label className="btn ghost">Загрузить<input type="file" style={{ display: "none" }} onChange={(e) => { const fl = e.target.files?.[0]; if (fl) api.upload<Claim>(`/api/legal/claims/${id}/files`, fl, { kind: fileKind }).then(setC).catch((x) => setErr((x as Error).message)); e.target.value = ""; }} /></label></>}
            </span></div>
          {c.files.length === 0 ? <div className="muted" style={{ fontSize: 13 }}>{out ? "Печатная форма сохранится при подписании; затем загрузите подписанный скан и почтовую квитанцию." : "Загрузите скан входящей претензии и наш ответ."}</div> : (
            <table className="table poa-files"><tbody>{c.files.map((x) => <tr key={x.id}><td>{FILE_KINDS[x.kind] || x.kind}</td><td><a href={`/api/legal/claims/${id}/files/${x.id}`} target="_blank" rel="noreferrer">{x.name}</a></td><td>{x.uploaded_by_name}</td><td>{d(x.uploaded_at)}</td></tr>)}</tbody></table>)}
        </div>
        <div className="poa-section" style={{ marginTop: 12 }}><div className="poa-section-head"><b>История</b></div>
          <ul className="poa-history" style={{ marginTop: 0 }}>{c.history.map((h, i) => <li key={i}><span className="muted">{new Date(h.at).toLocaleString("ru-RU")}</span> · {h.by_name || "система"} · {h.comment || h.action}</li>)}</ul></div>
      </div>
      <div className="poa-footer">
        <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
          <input value={comment} onChange={(e) => setComment(e.target.value)} placeholder="комментарий / причина" style={{ width: 220 }} />
          {out && c.status === "draft" && c.can_work && <button className="btn" onClick={() => step("to-sign")}>На подпись КД / ЗГД</button>}
          {out && ["draft", "signing"].includes(c.status) && c.can_sign && <button className="btn" onClick={() => step("sign")}>Подписать</button>}
          {out && c.status === "signing" && c.can_work && <button className="btn ghost" onClick={() => step("status", { status: "draft" })}>Вернуть в черновик</button>}
          {["sent", "received", "answered"].includes(c.status) && c.can_work && <button className="btn ghost" style={{ color: "var(--bad)" }} onClick={() => step("status", { status: "court" })}>В суд</button>}
          {["sent", "received", "answered", "court"].includes(c.status) && c.can_work && <button className="btn ghost" onClick={() => step("status", { status: "closed" })}>Закрыть</button>}
          {["draft", "signing", "signed", "received"].includes(c.status) && c.can_work && <button className="btn ghost" onClick={() => step("status", { status: "cancelled" })}>Отменить</button>}
        </div>
        <a className="muted" href={`/legal?tab=contracts&open=${c.contract_id}`}>договор {c.contract_number} →</a>
      </div>
    </Modal>);
}

function F({ label, children }: { label: string; children: ReactNode }) {
  return <label className="f"><span>{label}</span>{children}</label>;
}
function Modal({ title, children, onClose, wide }: { title: string; children: ReactNode; onClose: () => void; wide?: boolean }) {
  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.35)", zIndex: 60, display: "flex", alignItems: "flex-start", justifyContent: "center", padding: "40px 16px", overflow: "auto" }}>
      <div className="card poa-modal" onClick={(e) => e.stopPropagation()} style={{ width: "100%", maxWidth: wide ? 1000 : 640 }}>
        <div className="poa-modal-head"><b>{title}</b><button className="btn ghost" onClick={onClose} title="закрыть">✕</button></div>
        {children}
      </div>
    </div>);
}
