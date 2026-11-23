import { useEffect, useState } from "react";
import { api } from "../../api";
import SecurityModal from "../SecurityModal";

// Новое поручение ЗГД (D-154): руководителю подразделения или напрямую сотруднику. Права и проверки — на сервере.
export type AsgPrefill = { title?: string; body?: string; source_url?: string; source_key?: string; source_title?: string; kind?: "task" | "explain"; priority?: string; assignee_id?: string };
type U = { id: string; name: string; position: string; direction: string };
type Ref = { users: U[]; heads: Record<string, string | null>; directions: { key: string; title: string }[] };

const plusDays = (n: number) => { const d = new Date(); d.setDate(d.getDate() + n); return d.toISOString().slice(0, 10); };

export default function AssignmentDialog({ prefill, onClose, onDone }: { prefill?: AsgPrefill; onClose: () => void; onDone: (id: string) => void }) {
  const p = prefill || {};
  const explain = p.kind === "explain";
  const [ref, setRef] = useState<Ref | null>(null);
  const [mode, setMode] = useState<"head" | "direct">(p.assignee_id ? "direct" : "head");
  const [dir, setDir] = useState("");
  const [who, setWho] = useState(p.assignee_id || "");
  const [title, setTitle] = useState(p.title || "");
  const [body, setBody] = useState(p.body || "");
  const [due, setDue] = useState(plusDays(explain ? 1 : 3));
  const [prio, setPrio] = useState(p.priority || "normal");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.get<Ref>("/api/management/assignees").then(setRef).catch((e: Error) => setErr(e.message)); }, []);
  const head = dir && ref ? ref.users.find((u) => u.id === ref.heads[dir]) : undefined;
  const target = mode === "head" ? head?.id : who;
  const submit = () => {
    if (!target) { setErr(mode === "head" ? "У подразделения не назначен руководитель — выберите «сотруднику» или назначьте руководителя в «Направлениях»" : "Выберите сотрудника"); return; }
    setBusy(true); setErr("");
    api.post<{ id: string }>("/api/management/assignments", { title, body, mode, assignee_id: target, direction_key: dir, due_on: due, priority: prio,
      kind: p.kind || "task", source_url: p.source_url || "", source_key: p.source_key || "", source_title: p.source_title || "" })
      .then((r) => onDone(r.id)).catch((e: Error) => { setErr(e.message); setBusy(false); });
  };
  return (
    <SecurityModal title={explain ? "Запросить пояснение" : "Новое поручение"} onClose={onClose} width={560}>
      {p.source_title && <p className="muted asg-src">По сигналу: {p.source_title}</p>}
      <div className="asg-mode" role="radiogroup" aria-label="Кому">
        <label><input type="radio" checked={mode === "head"} onChange={() => setMode("head")} /> руководителю подразделения</label>
        <label><input type="radio" checked={mode === "direct"} onChange={() => setMode("direct")} /> напрямую сотруднику</label>
      </div>
      <label className="f"><span>Подразделение{mode === "direct" ? " (необязательно — определится по роли)" : ""}</span>
        <select value={dir} onChange={(e) => setDir(e.target.value)}>
          <option value="">{mode === "head" ? "выберите" : "по роли сотрудника"}</option>
          {ref?.directions.map((x) => <option key={x.key} value={x.key}>{x.title}</option>)}
        </select></label>
      {mode === "head" && dir && <p className={head ? "muted" : "bad"}>{head ? `Руководитель: ${head.name}${head.position ? `, ${head.position}` : ""}` : "Руководитель не назначен"}</p>}
      {mode === "direct" && <label className="f"><span>Сотрудник</span>
        <select value={who} onChange={(e) => setWho(e.target.value)}>
          <option value="">выберите</option>
          {ref?.users.filter((u) => !dir || u.direction === dir).map((u) => <option key={u.id} value={u.id}>{u.name}{u.position ? ` — ${u.position}` : ""}</option>)}
        </select></label>}
      {mode === "direct" && <p className="muted asg-hint">Руководитель подразделения сотрудника увидит поручение, но принимать результат будете вы.</p>}
      <label className="f"><span>{explain ? "Что пояснить" : "Что сделать"}</span><input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={255} /></label>
      <label className="f"><span>Подробности</span><textarea rows={3} value={body} onChange={(e) => setBody(e.target.value)} /></label>
      <div className="asg-row">
        <label className="f"><span>Срок</span><input type="date" value={due} min={plusDays(0)} onChange={(e) => setDue(e.target.value)} /></label>
        <label className="f"><span>Приоритет</span><select value={prio} onChange={(e) => setPrio(e.target.value)}>
          <option value="critical">критично</option><option value="high">высокий</option><option value="normal">обычный</option></select></label>
      </div>
      {err && <p className="bad">{err}</p>}
      <div className="asg-actions"><button className="btn ghost" onClick={onClose}>Отмена</button>
        <button className="btn add" disabled={busy || !title.trim()} onClick={submit}>{explain ? "Запросить" : "Поручить"}</button></div>
    </SecurityModal>);
}
