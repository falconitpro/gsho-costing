import { useEffect, useMemo, useState } from "react";
import { api, fmtDate } from "../api";
import { Crumbs, useQueryParam } from "../components/Levels";
import { askPrompt } from "../components/Dialogs";
import { Empty, ErrorState, Loading } from "../components/States";
import { can, type Me } from "../types";
import { setNewUi, useNewUiFlag } from "../uiMode";

/** D-205 (L1): блок «Персонал» — сотрудники, оргструктура, должности, штатное расписание. Только новый вид.
 *  Кадровые изменения — события с датой действия: история не переписывается, ошибочное событие отменяется с причиной.
 *  Видны только работодатели из области пользователя (настраивает ЗГД во вкладке «Доступ»). */

type Employer = { id: string; code: string; name: string; country: string; is_employer: boolean; hr_timezone: string; hr_external_id: string };
type Dept = { id: string; employer_id: string; parent_id: string | null; code: string; name: string; head_employee_id: string | null; valid_from: string; valid_to: string | null };
type Pos = { id: string; employer_id: string; code: string; name: string; category: string; is_active: boolean };
type Slot = { id: string; employer_id: string; department_id: string; position_id: string; fte: number; work_location: string; valid_from: string;
  valid_to: string | null; supersedes_id: string | null; comment: string; occupied: number };
type Asg = { department_id?: string; position_id?: string; staff_slot_id?: string | null; rate?: number; department: string; position: string };
type Ev = { id: string; kind: string; effective_from: string; effective_to: string | null; data: Record<string, unknown> & { department?: string; position?: string };
  cancels_id: string | null; cancelled: boolean; comment: string; recorded_by: string; recorded_at: string };
type Employment = { id: string; employer_id: string; employer: string; kind: string; start_date: string; end_date: string | null; external_id: string;
  status: string; assignment: Asg; events?: Ev[] };
type Employee = { id: string; personnel_no: string; last_name: string; first_name: string; middle_name: string; full_name: string; user_id: string | null;
  user: string; status: string; current: Employment | null; employments: Employment[] };

const STATUS: Record<string, [string, string]> = { active: ["работает", "ok"], planned: ["выходит", "info"], terminated: ["уволен", ""] };
const EV_TITLE: Record<string, string> = { hire: "Приём", transfer: "Перевод", rate: "Ставка", terminate: "Увольнение", cancel: "Отмена события" };
const EMP_KIND: Record<string, string> = { main: "основное место", internal_part: "внутреннее совместительство", external_part: "внешнее совместительство" };
const CAT: Record<string, string> = { manager: "руководитель", specialist: "специалист", worker: "рабочий", other: "другое" };
const today = () => new Date().toLocaleDateString("sv-SE", { timeZone: "Europe/Moscow" });
const mainEmployer = (es: Employer[]) => (es.find((e) => e.code === "ru") || es[0]).id;   // по умолчанию — компания в РФ, где основной штат
const rate = (v: unknown) => (v == null ? "" : `${String(v).replace(".", ",")} ст.`);

export default function HrPage({ me }: { me: Me }) {
  const nv = useNewUiFlag();
  const [tab, setTab] = useQueryParam("tab");
  const [employers, setEmployers] = useState<Employer[] | null>(null);
  const [err, setErr] = useState("");
  const isAdmin = can(me, "access_admin", 2);
  const loadEmployers = () => api.get<Employer[]>("/api/hr/employers").then(setEmployers).catch((e) => setErr((e as Error).message));
  useEffect(() => { if (nv && can(me, "hr_core")) loadEmployers(); }, [nv, me]);
  if (!nv) return (
    <div><Crumbs items={[{ label: "Рабочее место", to: "/" }, { label: "Персонал" }]} />
      <Empty title="Раздел «Персонал» — в новом виде интерфейса" hint="Сотрудники, оргструктура и штатное расписание открываются только в новом виде."
        action={<button className="btn" onClick={() => setNewUi(true)}>Включить новый вид</button>} /></div>);
  if (!can(me, "hr_core")) return <div><h1>Персонал</h1><Empty title="Нет доступа к разделу «Персонал»" hint="Доступ выдаёт ЗГД: группа «Персонал» в «Уровнях доступа» и юрлица во вкладке «Доступ»." /></div>;
  if (!employers) return err ? <ErrorState hint={err} /> : <Loading />;
  const tabs: [string, string][] = [["people", "Сотрудники"], ["org", "Оргструктура"], ["slots", "Штатное расписание"], ...(isAdmin ? [["access", "Доступ"] as [string, string]] : [])];
  const cur = tabs.some(([k]) => k === tab) ? tab! : "people";
  return (
    <div className="hr-page">
      <Crumbs items={[{ label: "Рабочее место", to: "/" }, { label: "Персонал" }]} />
      <div className="page-head"><h1>Персонал</h1><span className="muted">кадровое ядро · без паспортных и зарплатных данных</span></div>
      <div className="tabs" role="tablist">{tabs.map(([k, l]) => (
        <button key={k} role="tab" aria-selected={cur === k} className={cur === k ? "active" : ""} onClick={() => setTab(k === "people" ? null : k, { drop: ["e"] })}>{l}</button>))}</div>
      {employers.length === 0 && cur !== "access"
        ? <Empty title="Нет работодателей в вашей области" hint={isAdmin ? "Отметьте компании группы работодателями во вкладке «Доступ»." : "Попросите ЗГД добавить юрлица в вашу область доступа."} />
        : cur === "people" ? <People me={me} employers={employers} /> : cur === "org" ? <Org me={me} employers={employers} />
          : cur === "slots" ? <Slots me={me} employers={employers} /> : <Access onChange={loadEmployers} />}
    </div>);
}

function useRefs(employerId: string) {
  const [depts, setDepts] = useState<Dept[]>([]);
  const [pos, setPos] = useState<Pos[]>([]);
  const [slots, setSlots] = useState<Slot[]>([]);
  const load = () => {
    const q = employerId ? `?employer_id=${employerId}` : "";
    api.get<Dept[]>(`/api/hr/departments${q}`).then(setDepts).catch(() => setDepts([]));
    api.get<Pos[]>(`/api/hr/positions${q}`).then(setPos).catch(() => setPos([]));
    api.get<Slot[]>(`/api/hr/staff-slots${q}`).then(setSlots).catch(() => setSlots([]));
  };
  useEffect(load, [employerId]);
  return { depts, pos, slots, load };
}

function EmployerPick({ employers, value, onChange, all }: { employers: Employer[]; value: string; onChange: (v: string) => void; all?: boolean }) {
  if (employers.length === 1 && !all) return null;
  return <label className="f">Работодатель<select value={value} onChange={(e) => onChange(e.target.value)}>
    {all && <option value="">все</option>}{employers.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}</select></label>;
}

/* ───────── сотрудники */

function People({ me, employers }: { me: Me; employers: Employer[] }) {
  const [open, setOpen] = useQueryParam("e");
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("active");
  const [emp, setEmp] = useState("");
  const [rows, setRows] = useState<Employee[] | null>(null);
  const [msg, setMsg] = useState("");
  const [hire, setHire] = useState(false);
  const edit = can(me, "hr_core", 2);
  const load = () => {
    const p = new URLSearchParams({ status, q });
    if (emp) p.set("employer_id", emp);
    api.get<Employee[]>(`/api/hr/employees?${p}`).then(setRows).catch((e) => setMsg((e as Error).message));
  };
  useEffect(() => { const t = setTimeout(load, 250); return () => clearTimeout(t); }, [q, status, emp]);
  if (open) return <EmployeeCard id={open} me={me} employers={employers} onBack={() => { setOpen(null); load(); }} />;
  return (
    <div>
      {msg && <div className="alert info" role="status">{msg}</div>}
      <div className="row hr-filters">
        <label className="f">Поиск<input value={q} placeholder="фамилия или номер" onChange={(e) => setQ(e.target.value)} /></label>
        <label className="f">Состояние<select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="active">работают</option><option value="planned">выходят</option><option value="terminated">уволены</option><option value="all">все</option></select></label>
        <EmployerPick employers={employers} value={emp} onChange={setEmp} all />
        {edit && !hire && <button className="btn add primary" onClick={() => setHire(true)}>Принять сотрудника</button>}
      </div>
      {hire && <HireForm employers={employers} onDone={(id) => { setHire(false); if (id) setOpen(id); else load(); }} />}
      {!rows ? <Loading /> : rows.length === 0 ? <Empty title="Сотрудников нет" hint={edit ? "Примите первого сотрудника — карточка создаётся вместе с приёмом." : "Измените отбор."} /> : (
        <table className="pc-table nv-cards hr-table">
          <thead><tr><th>Сотрудник</th><th>Подразделение · должность</th><th>Работодатель</th><th>Состояние</th></tr></thead>
          <tbody>{rows.map((r) => {
            const c = r.current || r.employments[r.employments.length - 1];
            const [st, tone] = STATUS[r.status] || [r.status, ""];
            return (
              <tr key={r.id} className="hr-row" onClick={() => setOpen(r.id)}>
                <td className="pc-name"><button className="hr-name" onClick={(e) => { e.stopPropagation(); setOpen(r.id); }}>{r.full_name}</button><div className="pc-sub">{r.personnel_no}{r.user ? ` · вход: ${r.user}` : ""}</div></td>
                <td data-label="Подразделение · должность">{c?.assignment.department || "—"}<div className="pc-sub">{c?.assignment.position} {rate(c?.assignment.rate)}</div></td>
                <td data-label="Работодатель">{c?.employer}<div className="pc-sub">{EMP_KIND[c?.kind || "main"]}</div></td>
                <td data-label="Состояние"><span className={`badge ${tone}`}>{st}</span>{c?.end_date && <div className="pc-sub">по {fmtDate(c.end_date)}</div>}</td>
              </tr>);
          })}</tbody>
        </table>)}
    </div>);
}

type Hire = { employer_id: string; kind: string; start_date: string; staff_slot_id: string; department_id: string; position_id: string; rate: string; external_id: string; comment: string };

function HireFields({ f, set, employers }: { f: Hire; set: (f: Hire) => void; employers: Employer[] }) {
  const { depts, pos, slots } = useRefs(f.employer_id);
  const free = slots.filter((s) => s.occupied < s.fte);
  const dn = (id: string) => depts.find((d) => d.id === id)?.name || "";
  const pn = (id: string) => pos.find((p) => p.id === id)?.name || "";
  return (
    <div className="grid cols-3">
      <EmployerPick employers={employers} value={f.employer_id} onChange={(v) => set({ ...f, employer_id: v, staff_slot_id: "", department_id: "", position_id: "" })} />
      <label className="f">Вид<select value={f.kind} onChange={(e) => set({ ...f, kind: e.target.value })}>
        {Object.entries(EMP_KIND).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
      <label className="f">Дата приёма *<input type="date" value={f.start_date} onChange={(e) => set({ ...f, start_date: e.target.value })} /></label>
      <label className="f">Штатная единица<select value={f.staff_slot_id} onChange={(e) => set({ ...f, staff_slot_id: e.target.value, department_id: "", position_id: "" })}>
        <option value="">— без штатной единицы —</option>
        {free.map((s) => <option key={s.id} value={s.id}>{dn(s.department_id)} · {pn(s.position_id)} · свободно {String(s.fte - s.occupied).replace(".", ",")}</option>)}</select></label>
      {!f.staff_slot_id && <>
        <label className="f">Подразделение *<select value={f.department_id} onChange={(e) => set({ ...f, department_id: e.target.value })}>
          <option value="">— выберите —</option>{depts.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</select></label>
        <label className="f">Должность *<select value={f.position_id} onChange={(e) => set({ ...f, position_id: e.target.value })}>
          <option value="">— выберите —</option>{pos.filter((p) => p.is_active).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
      </>}
      <label className="f">Ставка<input type="number" step="0.25" min="0.25" max="2" value={f.rate} onChange={(e) => set({ ...f, rate: e.target.value })} /></label>
      <label className="f">ID в ЗУП<input value={f.external_id} onChange={(e) => set({ ...f, external_id: e.target.value })} /></label>
      <label className="f">Основание / комментарий<input value={f.comment} placeholder="приказ № …" onChange={(e) => set({ ...f, comment: e.target.value })} /></label>
    </div>);
}

const hireBody = (f: Hire) => ({ employer_id: f.employer_id, kind: f.kind, start_date: f.start_date, staff_slot_id: f.staff_slot_id || null,
  department_id: f.department_id || null, position_id: f.position_id || null, rate: f.rate ? Number(f.rate) : null, external_id: f.external_id, comment: f.comment });
const hireOk = (f: Hire) => !!f.employer_id && !!f.start_date && (!!f.staff_slot_id || (!!f.department_id && !!f.position_id));

function HireForm({ employers, onDone, employeeId }: { employers: Employer[]; onDone: (id?: string) => void; employeeId?: string }) {
  const [p, setP] = useState({ last_name: "", first_name: "", middle_name: "" });
  const [f, setF] = useState<Hire>({ employer_id: employers.length ? mainEmployer(employers) : "", kind: "main", start_date: today(), staff_slot_id: "", department_id: "", position_id: "", rate: "1", external_id: "", comment: "" });
  const [msg, setMsg] = useState("");
  const save = async () => {
    try {
      const r = employeeId ? await api.post<Employee>(`/api/hr/employees/${employeeId}/employments`, hireBody(f))
        : await api.post<Employee>("/api/hr/employees", { ...p, hire: hireBody(f) });
      onDone(r.id);
    } catch (e) { setMsg((e as Error).message); }
  };
  return (
    <div className="card hr-form">
      <h3>{employeeId ? "Повторный приём или совместительство" : "Приём сотрудника"}</h3>
      {msg && <div className="alert warn" role="alert">{msg}</div>}
      {!employeeId && <div className="grid cols-3">
        <label className="f">Фамилия *<input value={p.last_name} onChange={(e) => setP({ ...p, last_name: e.target.value })} /></label>
        <label className="f">Имя *<input value={p.first_name} onChange={(e) => setP({ ...p, first_name: e.target.value })} /></label>
        <label className="f">Отчество<input value={p.middle_name} onChange={(e) => setP({ ...p, middle_name: e.target.value })} /></label>
      </div>}
      <HireFields f={f} set={setF} employers={employers} />
      <p className="muted">Паспорт, адрес и зарплата здесь не хранятся. Кадровый приказ регистрируется в Канцелярии, как сейчас.</p>
      <div className="row"><button className="btn primary" disabled={!hireOk(f) || (!employeeId && (!p.last_name.trim() || !p.first_name.trim()))} onClick={save}>Принять</button>
        <button className="btn ghost" onClick={() => onDone()}>отмена</button></div>
    </div>);
}

function EmployeeCard({ id, me, employers, onBack }: { id: string; me: Me; employers: Employer[]; onBack: () => void }) {
  const [e, setE] = useState<Employee | null>(null);
  const [on, setOn] = useState("");
  const [msg, setMsg] = useState("");
  const [act, setAct] = useState<{ m: Employment; kind: string } | null>(null);
  const [rehire, setRehire] = useState(false);
  const edit = can(me, "hr_core", 2);
  const load = () => api.get<Employee>(`/api/hr/employees/${id}${on ? `?on=${on}` : ""}`).then(setE).catch((x) => setMsg((x as Error).message));
  useEffect(() => { load(); }, [id, on]);
  if (!e) return msg ? <ErrorState hint={msg} /> : <Loading />;
  const cancel = async (m: Employment, ev: Ev) => {
    const why = await askPrompt(`Отменить «${EV_TITLE[ev.kind]}» с ${fmtDate(ev.effective_from)}? Событие останется в истории с отметкой. Причина:`, "", { title: "Отмена кадрового события", required: true });
    if (!why) return;
    try { setE(await api.post<Employee>(`/api/hr/employments/${m.id}/events`, { kind: "cancel", cancels_id: ev.id, comment: why })); } catch (x) { setMsg((x as Error).message); }
  };
  const [st, tone] = STATUS[e.status] || [e.status, ""];
  return (
    <div className="hr-card">
      <div className="row"><button className="btn ghost" onClick={onBack}>← к списку</button></div>
      <div className="page-head"><h2>{e.full_name}</h2><span className={`badge ${tone}`}>{st}</span><span className="muted">{e.personnel_no}</span></div>
      {msg && <div className="alert info" role="status">{msg}</div>}
      <div className="row hr-filters">
        <label className="f">Состояние на дату<input type="date" value={on} onChange={(x) => setOn(x.target.value)} /></label>
        {on && <button className="btn ghost" onClick={() => setOn("")}>сегодня</button>}
        <span className="muted">Учётная запись CRM: {e.user || "не связана"}</span>
        {edit && <UserLink e={e} onSaved={setE} />}
      </div>
      {e.employments.slice().reverse().map((m) => {
        const [ms, mt] = STATUS[m.status] || [m.status, ""];
        return (
          <section key={m.id} className="card hr-emp">
            <div className="row hr-emp-head"><b>{m.employer}</b><span className="muted">{EMP_KIND[m.kind]}</span><span className={`badge ${mt}`}>{ms}</span>
              <span className="muted">с {fmtDate(m.start_date)}{m.end_date ? ` по ${fmtDate(m.end_date)}` : ""}</span></div>
            <div className="hr-asg">{m.assignment.department || "—"} · {m.assignment.position || "—"} · {rate(m.assignment.rate)}{m.assignment.staff_slot_id ? " · по штатной единице" : ""}</div>
            {edit && m.status !== "terminated" && !act && <div className="row">
              <button className="btn ghost" onClick={() => setAct({ m, kind: "transfer" })}>Перевод</button>
              <button className="btn ghost" onClick={() => setAct({ m, kind: "rate" })}>Ставка</button>
              <button className="btn ghost" onClick={() => setAct({ m, kind: "terminate" })}>Увольнение</button></div>}
            {act?.m.id === m.id && <EventForm m={m} kind={act.kind} onDone={(r) => { setAct(null); if (r) setE(r); }} />}
            <table className="pc-table nv-cards hr-events">
              <thead><tr><th>Событие</th><th>Действует</th><th>Что</th><th>Внесено</th><th /></tr></thead>
              <tbody>{(m.events || []).map((ev) => (
                <tr key={ev.id} className={ev.cancelled ? "hr-cancelled" : ""}>
                  <td className="pc-name">{EV_TITLE[ev.kind] || ev.kind}{ev.cancelled && <span className="badge">отменено</span>}</td>
                  <td data-label="Действует">с {fmtDate(ev.effective_from)}{ev.effective_to ? ` по ${fmtDate(ev.effective_to)}` : ""}</td>
                  <td data-label="Что">{ev.kind === "cancel" && (() => { const t = (m.events || []).find((x) => x.id === ev.cancels_id);
                    return t ? <span>отменяет «{EV_TITLE[t.kind]}» с {fmtDate(t.effective_from)}</span> : null; })()}{[ev.data.department, ev.data.position, ev.data.rate != null ? rate(ev.data.rate) : "", String(ev.data.reason || "")].filter(Boolean).join(" · ")}
                    {ev.comment && <div className="pc-sub">{ev.comment}</div>}</td>
                  <td data-label="Внесено">{ev.recorded_by}<div className="pc-sub">{fmtDate(ev.recorded_at)}</div></td>
                  <td>{edit && !ev.cancelled && !["hire", "cancel"].includes(ev.kind) && <button className="btn ghost" onClick={() => cancel(m, ev)}>отменить</button>}</td>
                </tr>))}</tbody>
            </table>
          </section>);
      })}
      {edit && (rehire ? <HireForm employers={employers} employeeId={e.id} onDone={() => { setRehire(false); load(); }} />
        : <div className="row"><button className="btn add" onClick={() => setRehire(true)}>Повторный приём или совместительство</button></div>)}
    </div>);
}

function UserLink({ e, onSaved }: { e: Employee; onSaved: (e: Employee) => void }) {
  const [users, setUsers] = useState<{ id: string; display_name: string; email: string }[] | null>(null);
  const [msg, setMsg] = useState("");
  if (!users) return <button className="btn ghost" onClick={() => api.get<typeof users>("/api/hr/users").then(setUsers).catch((x) => setMsg((x as Error).message))}>{msg || "связать с учётной записью"}</button>;
  return <label className="f">Учётная запись<select value={e.user_id || ""} onChange={async (x) => {
    try { onSaved(await api.patch<Employee>(`/api/hr/employees/${e.id}`, { user_id: x.target.value || null })); setUsers(null); } catch (er) { setMsg((er as Error).message); setUsers(null); }
  }}><option value="">— не связана —</option>{(users || []).map((u) => <option key={u.id} value={u.id}>{u.display_name}</option>)}</select></label>;
}

function EventForm({ m, kind, onDone }: { m: Employment; kind: string; onDone: (e?: Employee) => void }) {
  const { depts, pos, slots } = useRefs(m.employer_id);
  const [f, setF] = useState({ effective_from: today(), effective_to: "", staff_slot_id: "", department_id: "", position_id: "", rate: String(m.assignment.rate ?? 1), reason: "", comment: "" });
  const [msg, setMsg] = useState("");
  const dn = (id: string) => depts.find((d) => d.id === id)?.name || "";
  const pn = (id: string) => pos.find((p) => p.id === id)?.name || "";
  const ok = kind === "rate" ? !!f.rate : kind === "terminate" ? !!f.effective_from : !!(f.staff_slot_id || f.department_id || f.position_id);
  const save = async () => {
    const body: Record<string, unknown> = { kind, effective_from: f.effective_from, comment: f.comment };
    if (kind === "transfer") Object.assign(body, { effective_to: f.effective_to || null, staff_slot_id: f.staff_slot_id || null, department_id: f.department_id || null, position_id: f.position_id || null });
    if (kind === "rate") Object.assign(body, { rate: Number(f.rate), effective_to: f.effective_to || null });
    if (kind === "terminate") body.reason = f.reason;
    try { onDone(await api.post<Employee>(`/api/hr/employments/${m.id}/events`, body)); } catch (e) { setMsg((e as Error).message); }
  };
  return (
    <div className="card hr-form">
      <h3>{EV_TITLE[kind]}</h3>
      {msg && <div className="alert warn" role="alert">{msg}</div>}
      <div className="grid cols-3">
        <label className="f">{kind === "terminate" ? "Последний рабочий день *" : "Действует с *"}<input type="date" value={f.effective_from} onChange={(e) => setF({ ...f, effective_from: e.target.value })} /></label>
        {kind !== "terminate" && <label className="f">По (для временного)<input type="date" value={f.effective_to} onChange={(e) => setF({ ...f, effective_to: e.target.value })} /></label>}
        {kind === "transfer" && <>
          <label className="f">Штатная единица<select value={f.staff_slot_id} onChange={(e) => setF({ ...f, staff_slot_id: e.target.value, department_id: "", position_id: "" })}>
            <option value="">— без штатной единицы —</option>
            {slots.filter((s) => s.occupied < s.fte || s.id === m.assignment.staff_slot_id).map((s) => <option key={s.id} value={s.id}>{dn(s.department_id)} · {pn(s.position_id)}</option>)}</select></label>
          {!f.staff_slot_id && <>
            <label className="f">Подразделение<select value={f.department_id} onChange={(e) => setF({ ...f, department_id: e.target.value })}>
              <option value="">— без изменений —</option>{depts.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</select></label>
            <label className="f">Должность<select value={f.position_id} onChange={(e) => setF({ ...f, position_id: e.target.value })}>
              <option value="">— без изменений —</option>{pos.filter((p) => p.is_active).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
          </>}
        </>}
        {kind === "rate" && <label className="f">Ставка *<input type="number" step="0.25" min="0.25" max="2" value={f.rate} onChange={(e) => setF({ ...f, rate: e.target.value })} /></label>}
        {kind === "terminate" && <label className="f">Основание<input value={f.reason} placeholder="по собственному желанию" onChange={(e) => setF({ ...f, reason: e.target.value })} /></label>}
        <label className="f">Комментарий<input value={f.comment} placeholder="приказ № …" onChange={(e) => setF({ ...f, comment: e.target.value })} /></label>
      </div>
      {kind === "terminate" && <p className="muted">Учётная запись CRM при увольнении не отключается автоматически — отключите её в «Безопасности сотрудников».</p>}
      <div className="row"><button className="btn primary" disabled={!ok} onClick={save}>Сохранить</button><button className="btn ghost" onClick={() => onDone()}>отмена</button></div>
    </div>);
}

/* ───────── оргструктура и должности */

function Org({ me, employers }: { me: Me; employers: Employer[] }) {
  const [emp, setEmp] = useState(mainEmployer(employers));
  const { depts, pos, load } = useRefs(emp);
  const [d, setD] = useState<{ id?: string; name: string; code: string; parent_id: string; valid_from: string; valid_to: string } | null>(null);
  const [p, setP] = useState<{ id?: string; name: string; code: string; category: string; is_active: boolean } | null>(null);
  const [msg, setMsg] = useState("");
  const edit = can(me, "hr_core", 2);
  const tree = useMemo(() => {
    const out: { d: Dept; level: number }[] = [];
    const walk = (parent: string | null, level: number) => depts.filter((x) => (x.parent_id || null) === parent).forEach((x) => { out.push({ d: x, level }); walk(x.id, level + 1); });
    walk(null, 0);
    depts.filter((x) => !out.some((o) => o.d.id === x.id)).forEach((x) => out.push({ d: x, level: 0 }));
    return out;
  }, [depts]);
  const saveD = async () => {
    if (!d) return;
    const body = { employer_id: emp, name: d.name, code: d.code, parent_id: d.parent_id || null, valid_from: d.valid_from || null, valid_to: d.valid_to || null };
    try { if (d.id) await api.patch(`/api/hr/departments/${d.id}`, body); else await api.post("/api/hr/departments", body); setD(null); load(); } catch (e) { setMsg((e as Error).message); }
  };
  const saveP = async () => {
    if (!p) return;
    try { if (p.id) await api.patch(`/api/hr/positions/${p.id}`, { ...p, employer_id: emp }); else await api.post("/api/hr/positions", { ...p, employer_id: emp }); setP(null); load(); } catch (e) { setMsg((e as Error).message); }
  };
  const actual = (x: Dept) => !x.valid_to || x.valid_to >= today();
  return (
    <div>
      {msg && <div className="alert warn" role="alert">{msg}</div>}
      <div className="row hr-filters"><EmployerPick employers={employers} value={emp} onChange={setEmp} /></div>
      <div className="grid cols-2 hr-org">
        <section className="card">
          <div className="row"><h3>Подразделения</h3>{edit && !d && <button className="btn add" onClick={() => setD({ name: "", code: "", parent_id: "", valid_from: today(), valid_to: "" })}>Подразделение</button>}</div>
          {d && <div className="hr-form">
            <div className="grid cols-2">
              <label className="f">Название *<input value={d.name} onChange={(e) => setD({ ...d, name: e.target.value })} /></label>
              <label className="f">Код<input value={d.code} onChange={(e) => setD({ ...d, code: e.target.value })} /></label>
              <label className="f">Входит в<select value={d.parent_id} onChange={(e) => setD({ ...d, parent_id: e.target.value })}>
                <option value="">— верхний уровень —</option>{depts.filter((x) => x.id !== d.id).map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></label>
              <label className="f">Действует с<input type="date" value={d.valid_from} onChange={(e) => setD({ ...d, valid_from: e.target.value })} /></label>
              <label className="f">Действует по<input type="date" value={d.valid_to} onChange={(e) => setD({ ...d, valid_to: e.target.value })} /></label>
            </div>
            <div className="row"><button className="btn primary" disabled={!d.name.trim()} onClick={saveD}>Сохранить</button><button className="btn ghost" onClick={() => setD(null)}>отмена</button></div>
          </div>}
          {tree.length === 0 ? <Empty title="Подразделений нет" /> : <ul className="hr-tree">{tree.map(({ d: x, level }) => (
            <li key={x.id} className={`hr-lvl-${Math.min(level, 4)}${actual(x) ? "" : " hr-cancelled"}`}>
              <span>{x.name}</span>{x.code && <span className="muted"> · {x.code}</span>}
              <span className="pc-sub"> с {fmtDate(x.valid_from)}{x.valid_to ? ` по ${fmtDate(x.valid_to)}` : ""}</span>
              {edit && <button className="btn ghost" onClick={() => setD({ id: x.id, name: x.name, code: x.code, parent_id: x.parent_id || "", valid_from: x.valid_from, valid_to: x.valid_to || "" })}>изменить</button>}
            </li>))}</ul>}
        </section>
        <section className="card">
          <div className="row"><h3>Должности</h3>{edit && !p && <button className="btn add" onClick={() => setP({ name: "", code: "", category: "worker", is_active: true })}>Должность</button>}</div>
          {p && <div className="hr-form">
            <div className="grid cols-2">
              <label className="f">Название *<input value={p.name} onChange={(e) => setP({ ...p, name: e.target.value })} /></label>
              <label className="f">Код<input value={p.code} onChange={(e) => setP({ ...p, code: e.target.value })} /></label>
              <label className="f">Категория<select value={p.category} onChange={(e) => setP({ ...p, category: e.target.value })}>
                {Object.entries(CAT).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
              <label className="f check"><input type="checkbox" checked={p.is_active} onChange={(e) => setP({ ...p, is_active: e.target.checked })} />действует</label>
            </div>
            <div className="row"><button className="btn primary" disabled={!p.name.trim()} onClick={saveP}>Сохранить</button><button className="btn ghost" onClick={() => setP(null)}>отмена</button></div>
          </div>}
          {pos.length === 0 ? <Empty title="Должностей нет" /> : <ul className="hr-tree">{pos.map((x) => (
            <li key={x.id} className={x.is_active ? "" : "hr-cancelled"}><span>{x.name}</span><span className="muted"> · {CAT[x.category]}</span>
              {edit && <button className="btn ghost" onClick={() => setP({ id: x.id, name: x.name, code: x.code, category: x.category, is_active: x.is_active })}>изменить</button>}</li>))}</ul>}
        </section>
      </div>
    </div>);
}

/* ───────── штатное расписание */

function Slots({ me, employers }: { me: Me; employers: Employer[] }) {
  const [emp, setEmp] = useState(mainEmployer(employers));
  const [on, setOn] = useState("");
  const { depts, pos } = useRefs(emp);
  const [rows, setRows] = useState<Slot[] | null>(null);
  const [f, setF] = useState<{ department_id: string; position_id: string; fte: string; work_location: string; valid_from: string } | null>(null);
  const [chg, setChg] = useState<{ s: Slot; valid_from: string; fte: string; work_location: string } | null>(null);
  const [msg, setMsg] = useState("");
  const edit = can(me, "hr_core", 2);
  const load = () => api.get<Slot[]>(`/api/hr/staff-slots?employer_id=${emp}${on ? `&on=${on}` : ""}`).then(setRows).catch((e) => setMsg((e as Error).message));
  useEffect(() => { load(); }, [emp, on]);
  const dn = (id: string) => depts.find((d) => d.id === id)?.name || "";
  const pn = (id: string) => pos.find((p) => p.id === id)?.name || "";
  const create = async () => {
    if (!f) return;
    try { await api.post("/api/hr/staff-slots", { employer_id: emp, ...f, fte: Number(f.fte) }); setF(null); load(); } catch (e) { setMsg((e as Error).message); }
  };
  const change = async () => {
    if (!chg) return;
    try { await api.post(`/api/hr/staff-slots/${chg.s.id}/change`, { valid_from: chg.valid_from, fte: Number(chg.fte), work_location: chg.work_location }); setChg(null); load(); }
    catch (e) { setMsg((e as Error).message); }
  };
  const total = (rows || []).reduce((a, s) => a + s.fte, 0);
  const busy = (rows || []).reduce((a, s) => a + s.occupied, 0);
  return (
    <div>
      {msg && <div className="alert warn" role="alert">{msg}</div>}
      <div className="row hr-filters">
        <EmployerPick employers={employers} value={emp} onChange={setEmp} />
        <label className="f">На дату<input type="date" value={on} onChange={(e) => setOn(e.target.value)} /></label>
        <span className="muted">ставок по штату: {String(total).replace(".", ",")} · занято: {String(busy).replace(".", ",")}</span>
        {edit && !f && <button className="btn add primary" onClick={() => setF({ department_id: "", position_id: "", fte: "1", work_location: "", valid_from: today() })}>Штатная единица</button>}
      </div>
      {f && <div className="card hr-form"><h3>Новая штатная единица</h3>
        <div className="grid cols-3">
          <label className="f">Подразделение *<select value={f.department_id} onChange={(e) => setF({ ...f, department_id: e.target.value })}>
            <option value="">— выберите —</option>{depts.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</select></label>
          <label className="f">Должность *<select value={f.position_id} onChange={(e) => setF({ ...f, position_id: e.target.value })}>
            <option value="">— выберите —</option>{pos.filter((p) => p.is_active).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
          <label className="f">Ставок *<input type="number" step="0.25" min="0.25" value={f.fte} onChange={(e) => setF({ ...f, fte: e.target.value })} /></label>
          <label className="f">Место работы<input value={f.work_location} placeholder="офис, карьер, склад" onChange={(e) => setF({ ...f, work_location: e.target.value })} /></label>
          <label className="f">Действует с<input type="date" value={f.valid_from} onChange={(e) => setF({ ...f, valid_from: e.target.value })} /></label>
        </div>
        <div className="row"><button className="btn primary" disabled={!f.department_id || !f.position_id || !f.fte} onClick={create}>Сохранить</button><button className="btn ghost" onClick={() => setF(null)}>отмена</button></div>
      </div>}
      {chg && <div className="card hr-form"><h3>Изменение штатной единицы с даты</h3>
        <p className="muted">{dn(chg.s.department_id)} · {pn(chg.s.position_id)}. Прежняя версия закроется днём раньше и останется в истории.</p>
        <div className="grid cols-3">
          <label className="f">С даты *<input type="date" value={chg.valid_from} onChange={(e) => setChg({ ...chg, valid_from: e.target.value })} /></label>
          <label className="f">Ставок<input type="number" step="0.25" min="0.25" value={chg.fte} onChange={(e) => setChg({ ...chg, fte: e.target.value })} /></label>
          <label className="f">Место работы<input value={chg.work_location} onChange={(e) => setChg({ ...chg, work_location: e.target.value })} /></label>
        </div>
        <div className="row"><button className="btn primary" onClick={change}>Сохранить</button><button className="btn ghost" onClick={() => setChg(null)}>отмена</button></div>
      </div>}
      {!rows ? <Loading /> : rows.length === 0 ? <Empty title="Штатных единиц нет" hint="Сначала заведите подразделения и должности во вкладке «Оргструктура»." /> : (
        <table className="pc-table nv-cards hr-table">
          <thead><tr><th>Подразделение · должность</th><th>Ставок</th><th>Занято</th><th>Место работы</th><th>Действует</th><th /></tr></thead>
          <tbody>{rows.map((s) => (
            <tr key={s.id}>
              <td className="pc-name">{dn(s.department_id)}<div className="pc-sub">{pn(s.position_id)}</div></td>
              <td data-label="Ставок">{String(s.fte).replace(".", ",")}</td>
              <td data-label="Занято">{String(s.occupied).replace(".", ",")} {s.occupied > s.fte ? <span className="badge bad">сверх штата</span> : s.occupied < s.fte ? <span className="badge info">вакансия</span> : null}</td>
              <td data-label="Место работы">{s.work_location || "—"}</td>
              <td data-label="Действует">с {fmtDate(s.valid_from)}{s.valid_to ? ` по ${fmtDate(s.valid_to)}` : ""}</td>
              <td>{edit && <button className="btn ghost" onClick={() => setChg({ s, valid_from: today(), fte: String(s.fte), work_location: s.work_location })}>изменить с даты</button>}</td>
            </tr>))}</tbody>
        </table>)}
    </div>);
}

/* ───────── доступ: работодатели и области (ЗГД) */

function Access({ onChange }: { onChange: () => void }) {
  const [all, setAll] = useState<Employer[] | null>(null);
  const [users, setUsers] = useState<{ id: string; display_name: string; email: string; is_active?: boolean }[]>([]);
  const [scopes, setScopes] = useState<Record<string, string[]>>({});
  const [msg, setMsg] = useState("");
  const load = () => {
    api.get<Employer[]>("/api/hr/employers?all=1").then(setAll).catch((e) => setMsg((e as Error).message));
    api.get<Record<string, string[]>>("/api/hr/scopes").then(setScopes).catch(() => setScopes({}));
    api.get<typeof users>("/api/admin/users").then(setUsers).catch(() => setUsers([]));
  };
  useEffect(load, []);
  if (!all) return msg ? <ErrorState hint={msg} /> : <Loading />;
  const emps = all.filter((x) => x.is_employer);
  const toggleEmployer = async (x: Employer) => {
    try { await api.patch(`/api/hr/employers/${x.id}`, { is_employer: !x.is_employer }); load(); onChange(); } catch (e) { setMsg((e as Error).message); }
  };
  const setTz = async (x: Employer, field: "hr_timezone" | "hr_external_id", v: string) => {
    try { await api.patch(`/api/hr/employers/${x.id}`, { [field]: v }); load(); } catch (e) { setMsg((e as Error).message); }
  };
  const toggleScope = async (uid: string, eid: string) => {
    const cur = scopes[uid] || [];
    const next = cur.includes(eid) ? cur.filter((x) => x !== eid) : [...cur, eid];
    try { await api.put(`/api/hr/scopes/${uid}`, { employer_ids: next }); setScopes({ ...scopes, [uid]: next }); } catch (e) { setMsg((e as Error).message); }
  };
  return (
    <div>
      {msg && <div className="alert warn" role="alert">{msg}</div>}
      <section className="card">
        <h3>Работодатели</h3>
        <p className="muted">Работодатель — компания группы. Часовой пояс нужен для табеля и смен, ID в ЗУП — для обмена с бухгалтерией.</p>
        <table className="pc-table nv-cards hr-table">
          <thead><tr><th>Компания</th><th>Работодатель</th><th>Часовой пояс</th><th>ID в ЗУП</th></tr></thead>
          <tbody>{all.map((x) => (
            <tr key={x.id}>
              <td className="pc-name">{x.name}<div className="pc-sub">{x.code} · {x.country}</div></td>
              <td data-label="Работодатель"><input type="checkbox" aria-label={`${x.name} — работодатель`} checked={x.is_employer} onChange={() => toggleEmployer(x)} /></td>
              <td data-label="Часовой пояс"><input aria-label="Часовой пояс" defaultValue={x.hr_timezone} disabled={!x.is_employer} onBlur={(e) => e.target.value !== x.hr_timezone && setTz(x, "hr_timezone", e.target.value)} /></td>
              <td data-label="ID в ЗУП"><input aria-label="ID в ЗУП" defaultValue={x.hr_external_id} disabled={!x.is_employer} onBlur={(e) => e.target.value !== x.hr_external_id && setTz(x, "hr_external_id", e.target.value)} /></td>
            </tr>))}</tbody>
        </table>
      </section>
      <section className="card">
        <h3>Кто видит каких работодателей</h3>
        <p className="muted">Отметка даёт доступ к кадровым данным юрлица — только тем, у кого есть группа «Персонал» в «Уровнях доступа». ЗГД видит всех.</p>
        {emps.length === 0 ? <Empty title="Сначала отметьте работодателей" /> : (
          <table className="pc-table nv-cards hr-table">
            <thead><tr><th>Сотрудник</th>{emps.map((x) => <th key={x.id}>{x.name}</th>)}</tr></thead>
            <tbody>{users.filter((u) => u.is_active !== false).map((u) => (
              <tr key={u.id}><td className="pc-name">{u.display_name}<div className="pc-sub">{u.email}</div></td>
                {emps.map((x) => <td key={x.id} data-label={x.name}><input type="checkbox" aria-label={`${u.display_name}: ${x.name}`} checked={(scopes[u.id] || []).includes(x.id)} onChange={() => toggleScope(u.id, x.id)} /></td>)}
              </tr>))}</tbody>
          </table>)}
      </section>
    </div>);
}
