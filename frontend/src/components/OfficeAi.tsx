import { useState } from "react";
import { api, ApiError } from "../api";

/** D-134: ИИ для писем (Yandex AI Studio). ИИ предлагает — человек принимает: документ меняется только кнопкой «Применить». */
type Doc = { id: string; kind: string; status: string; body?: string; summary: string; letter_type: string; due_on: string | null; can_edit: boolean; confidential: boolean; category: string };
type Analyze = { summary: string; letter_type: string; due_on: string; due_quote?: string; requirements?: string[]; amounts?: string[]; dates?: string[]; refs?: string[]; suggested_role?: string; needs_reply?: boolean };
type Diff = { before: string; after: string; warnings: string[]; mode?: string; lang?: string };
type Check = { issues?: { level: string; text: string }[]; unanswered?: string[]; numbers_to_verify?: string[]; with_parent?: boolean };

const MODES: Record<string, string> = { business: "деловой стиль", softer: "мягче", firmer: "жёстче", shorter: "короче", grammar: "только грамматика" };
const LEVEL: Record<string, string> = { high: "важно", medium: "проверить", low: "мелочь" };

export default function OfficeAi({ d, letterTypes, roles, onPatch }: { d: Doc; letterTypes: Record<string, string>; roles: Record<string, string>; onPatch: (body: Record<string, unknown>) => Promise<void> }) {
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [an, setAn] = useState<Analyze | null>(null);
  const [diff, setDiff] = useState<Diff | null>(null);
  const [chk, setChk] = useState<Check | null>(null);
  const [mode, setMode] = useState("business");
  const [lang, setLang] = useState("zh");
  if (d.confidential || (d.kind === "order" && d.category === "hr")) return <div className="muted office-ai">ИИ: конфиденциальные документы и приказы по личному составу в ИИ не отправляются.</div>;
  const run = async (action: string, extra: Record<string, unknown> = {}) => {
    setBusy(action); setErr(""); setAn(null); setDiff(null); setChk(null);
    try {
      const r = await api.post<Record<string, unknown>>(`/api/office/docs/${d.id}/ai`, { action, ...extra });
      if (action === "analyze") setAn(r as unknown as Analyze);
      else if (action === "check") setChk(r as unknown as Check);
      else setDiff(r as unknown as Diff);
    } catch (e) { setErr(e instanceof ApiError ? e.message : "ИИ не ответил"); }
    setBusy("");
  };
  const draft = d.kind !== "in" && d.status === "draft" && d.can_edit;
  return (
    <div className="office-box office-ai">
      <div className="row"><b>ИИ</b><span className="muted">Yandex AI Studio · предлагает, применяете вы</span>
        {d.kind === "in" && <button className="btn ghost" disabled={!!busy} onClick={() => run("analyze")}>{busy === "analyze" ? "Читаю письмо…" : "Разобрать письмо"}</button>}
        {d.kind !== "in" && (d.body || "").trim() && <>
          {draft && <><select value={mode} onChange={(e) => setMode(e.target.value)}>{Object.entries(MODES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
            <button className="btn ghost" disabled={!!busy} onClick={() => run("edit", { mode })}>{busy === "edit" ? "Правлю…" : "Править текст"}</button></>}
          <button className="btn ghost" disabled={!!busy} onClick={() => run("check")}>{busy === "check" ? "Проверяю…" : "Проверить перед согласованием"}</button>
          <select value={lang} onChange={(e) => setLang(e.target.value)}><option value="zh">на китайский</option><option value="en">на английский</option></select>
          <button className="btn ghost" disabled={!!busy} onClick={() => run("translate", { lang })}>{busy === "translate" ? "Перевожу…" : "Перевести"}</button>
        </>}
      </div>
      {err && <div className="alert error">{err}</div>}
      {an && (
        <div className="office-ai-res">
          <div><span className="muted">Кратко</span> {an.summary}</div>
          <div><span className="muted">Вид</span> {letterTypes[an.letter_type] || "—"}{an.due_on ? <> · <span className="muted">срок из письма</span> <b>{new Date(an.due_on).toLocaleDateString("ru-RU")}</b>{an.due_quote ? ` («${an.due_quote}»)` : ""}</> : " · срока в письме нет"}</div>
          {!!an.requirements?.length && <div><span className="muted">Требуют</span> {an.requirements.join("; ")}</div>}
          {!!an.amounts?.length && <div><span className="muted">Суммы</span> {an.amounts.join("; ")}</div>}
          {!!an.refs?.length && <div><span className="muted">Ссылки</span> {an.refs.join("; ")}</div>}
          {an.suggested_role && <div><span className="muted">Кому поручить</span> {roles[an.suggested_role] || an.suggested_role}</div>}
          {d.can_edit && <div className="row">
            <button className="btn" onClick={() => onPatch({ summary: an.summary })}>Применить краткое</button>
            {an.letter_type && an.letter_type !== d.letter_type && <button className="btn ghost" onClick={() => onPatch({ letter_type: an.letter_type })}>Вид: {letterTypes[an.letter_type]}</button>}
            {an.due_on && an.due_on !== d.due_on && <button className="btn ghost" onClick={() => onPatch({ due_on: an.due_on })}>Срок: {new Date(an.due_on).toLocaleDateString("ru-RU")}</button>}
          </div>}
        </div>
      )}
      {diff && (
        <div className="office-ai-res">
          {diff.warnings.length > 0 && <div className="alert warn">{diff.warnings.join("; ")}</div>}
          <div className="office-ai-diff"><div><div className="muted">Было</div><pre>{diff.before}</pre></div><div><div className="muted">{diff.lang ? "Перевод" : "Стало"}</div><pre>{diff.after}</pre></div></div>
          <div className="row">
            {draft && !diff.lang && <button className="btn" onClick={() => onPatch({ body: diff.after }).then(() => setDiff(null))}>Принять правку</button>}
            {diff.lang && <button className="btn ghost" onClick={() => navigator.clipboard?.writeText(diff.after)}>Скопировать перевод</button>}
            {diff.lang && draft && <button className="btn ghost" onClick={() => onPatch({ body: `${diff.before}\n\n———\n\n${diff.after}` }).then(() => setDiff(null))}>Добавить перевод в текст</button>}
            <button className="btn ghost" onClick={() => setDiff(null)}>Отклонить</button>
          </div>
        </div>
      )}
      {chk && (
        <div className="office-ai-res">
          {!chk.with_parent && <div className="muted">Входящего с текстом нет — проверка только самого письма.</div>}
          {(chk.issues || []).length === 0 && (chk.unanswered || []).length === 0 ? <div className="alert ok">Замечаний нет</div> :
            <ul>{(chk.issues || []).map((x, i) => <li key={i}><span className={"badge" + (x.level === "high" ? " bad" : x.level === "medium" ? " warn" : "")}>{LEVEL[x.level] || x.level}</span> {x.text}</li>)}
              {(chk.unanswered || []).map((x, i) => <li key={`u${i}`}><span className="badge warn">без ответа</span> {x}</li>)}</ul>}
          {!!chk.numbers_to_verify?.length && <div className="muted">Числа, которых нет во входящем, — сверьте с расчётом: {chk.numbers_to_verify.join(", ")}</div>}
        </div>
      )}
    </div>
  );
}
