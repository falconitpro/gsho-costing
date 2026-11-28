import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, fmtDate, getDevUser } from "../api";
import { Crumbs, useQueryParam } from "../components/Levels";
import { askPrompt } from "../components/Dialogs";
import { Empty, ErrorState, Loading } from "../components/States";
import OffboardDialog from "../components/OffboardDialog";
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
  user: string; status: string; current: Employment | null; employments: Employment[]; user_active: boolean; offboard_needed: boolean };

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
  const tabs: [string, string][] = [["people", "Сотрудники"], ["summary", "Сводка"], ["org", "Оргструктура"], ["slots", "Штатное расписание"], ["adm", "Допуски"], ...(isAdmin ? [["access", "Доступ"] as [string, string]] : [])];
  const cur = tabs.some(([k]) => k === tab) ? tab! : "people";
  return (
    <div className="hr-page">
      <Crumbs items={[{ label: "Рабочее место", to: "/" }, { label: "Персонал" }]} />
      <div className="page-head"><h1>Персонал</h1><span className="muted">личные данные и оплата труда хранятся зашифрованными; просмотры записываются в журнал</span></div>
      <div className="tabs" role="tablist">{tabs.map(([k, l]) => (
        <button key={k} role="tab" aria-selected={cur === k} className={cur === k ? "active" : ""} onClick={() => setTab(k === "people" ? null : k, { drop: ["e"] })}>{l}</button>))}</div>
      {employers.length === 0 && cur !== "access"
        ? <Empty title="Нет работодателей в вашей области" hint={isAdmin ? "Отметьте компании группы работодателями во вкладке «Доступ»." : "Попросите ЗГД добавить юрлица в вашу область доступа."} />
        : cur === "people" ? <People me={me} employers={employers} /> : cur === "org" ? <Org me={me} employers={employers} />
          : cur === "slots" ? <Slots me={me} employers={employers} /> : cur === "summary" ? <Summary /> : cur === "adm" ? <AdmOverview me={me} /> : <Access onChange={loadEmployers} />}
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
  if (open) return <EmployeeCard id={open} me={me} employers={employers} onBack={() => { setOpen(null, { drop: ["s"] }); load(); }} />;
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
                <td className="pc-name"><button className="hr-name" onClick={(ev) => { ev.stopPropagation(); setOpen(r.id); }}>{r.full_name}</button><div className="pc-sub">{r.personnel_no}{r.user ? ` · вход: ${r.user}` : ""}</div></td>
                <td data-label="Подразделение · должность">{c?.assignment.department || "—"}<div className="pc-sub">{c?.assignment.position} {rate(c?.assignment.rate)}</div></td>
                <td data-label="Работодатель">{c?.employer}<div className="pc-sub">{EMP_KIND[c?.kind || "main"]}</div></td>
                <td data-label="Состояние"><span className={`badge ${tone}`}>{st}</span>{r.offboard_needed && <span className="badge bad">вход не отключён</span>}{c?.end_date && <div className="pc-sub">по {fmtDate(c.end_date)}</div>}</td>
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
      <p className="muted">Паспорт, адреса, оплату труда и сканы документов заполните в карточке сотрудника после приёма. Кадровый приказ регистрируется в Канцелярии, как сейчас.</p>
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
  const [sec, setSec] = useQueryParam("s");
  const [off, setOff] = useState(false);
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
  const secs: [string, string][] = [["jobs", "Кадры"], ...(can(me, "hr_personal") ? [["personal", "Личные данные"] as [string, string]] : []),
    ...(can(me, "hr_payroll") ? [["pay", "Оплата труда"] as [string, string]] : []), ["adm", "Допуски и обучение"], ["docs", "Документы и сканы"]];
  const curSec = secs.some(([k]) => k === sec) ? sec! : "jobs";
  const head = (
    <>
      <div className="row"><button className="btn ghost" onClick={onBack}>← к списку</button></div>
      <div className="page-head"><h2>{e.full_name}</h2><span className={`badge ${tone}`}>{st}</span><span className="muted">{e.personnel_no}</span></div>
      {e.offboard_needed && <div className="alert warn hr-off" role="alert">
        <span>Сотрудник уволен, а вход в CRM ({e.user}) ещё действует.</span>
        {can(me, "security_admin", 2) ? <button className="btn primary" onClick={() => setOff(true)}>Отключить доступ</button>
          : <span className="muted">Отключает администратор безопасности — ему уже отправлено уведомление.</span>}</div>}
      {off && e.user_id && <OffboardDialog user={{ id: e.user_id, display_name: e.user }} mode="offboard" onClose={() => setOff(false)} onDone={load} />}
      <div className="tabs hr-sec" role="tablist">{secs.map(([k, l]) => (
        <button key={k} role="tab" aria-selected={curSec === k} className={curSec === k ? "active" : ""} onClick={() => setSec(k === "jobs" ? null : k)}>{l}</button>))}</div>
    </>);
  if (curSec === "personal") return <div className="hr-card">{head}<PersonalTab e={e} me={me} /></div>;
  if (curSec === "pay") return <div className="hr-card">{head}<PayTab e={e} me={me} /></div>;
  if (curSec === "docs") return <div className="hr-card">{head}<DocsTab e={e} /></div>;
  if (curSec === "adm") return <div className="hr-card">{head}<AdmTab e={e} me={me} /></div>;
  return (
    <div className="hr-card">
      {head}
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
      {kind === "terminate" && <p className="muted">Вход в CRM не отключается сам: после последнего рабочего дня в карточке появится кнопка «Отключить доступ», администратору безопасности придёт напоминание.</p>}
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
              <label className="hr-check"><input type="checkbox" checked={p.is_active} onChange={(e) => setP({ ...p, is_active: e.target.checked })} />действует</label>
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

/* ───────── личные данные (зашифрованы на сервере; каждый просмотр — в журнале) */

type Personal = Record<string, string | null | { name: string; birth_date: string | null }[] | Record<string, string>[]>;
const P_GROUPS: [string, [string, string, string?][]][] = [
  ["Основное", [["birth_date", "Дата рождения", "date"], ["birth_place", "Место рождения"], ["gender", "Пол", "gender"], ["citizenship", "Гражданство"], ["marital_status", "Семейное положение"]]],
  ["Паспорт", [["passport_series", "Серия"], ["passport_number", "Номер"], ["passport_issue_date", "Дата выдачи", "date"], ["passport_division_code", "Код подразделения"], ["passport_issued_by", "Кем выдан", "wide"]]],
  ["Другие документы", [["snils", "СНИЛС"], ["inn", "ИНН"], ["foreign_passport_number", "Загранпаспорт, номер"], ["foreign_passport_valid_to", "Загранпаспорт действует до", "date"],
    ["driver_license_number", "Водительское, номер"], ["driver_license_categories", "Категории"], ["driver_license_valid_to", "Водительское действует до", "date"]]],
  ["Адреса и связь", [["reg_address", "Адрес регистрации", "wide"], ["fact_address", "Адрес проживания", "wide"], ["personal_phone", "Личный телефон"], ["personal_email", "Личная почта"]]],
  ["Экстренный контакт", [["emergency_name", "ФИО"], ["emergency_relation", "Кем приходится"], ["emergency_phone", "Телефон"]]],
  ["Воинский учёт", [["military_category", "Категория запаса"], ["military_rank", "Звание"], ["military_vus", "ВУС"], ["military_office", "Военкомат", "wide"]]],
];

function PersonalTab({ e, me }: { e: Employee; me: Me }) {
  const [d, setD] = useState<{ data: Personal; filled: string[]; updated_by: string; updated_at: string | null; warnings: string[] } | null>(null);
  const [f, setF] = useState<Personal | null>(null);
  const [msg, setMsg] = useState("");
  const edit = can(me, "hr_personal", 2);
  useEffect(() => { api.get<NonNullable<typeof d>>(`/api/hr/employees/${e.id}/personal`).then((r) => { setD(r); }).catch((x) => setMsg((x as Error).message)); }, [e.id]);
  if (!d) return msg ? <ErrorState hint={msg} /> : <Loading />;
  const v = f || d.data;
  const set = (k: string, val: unknown) => setF({ ...(f || d.data), [k]: val as string });
  const save = async () => {
    try { const r = await api.put<NonNullable<typeof d>>(`/api/hr/employees/${e.id}/personal`, f); setD(r); setF(null); setMsg("Сохранено"); }
    catch (x) { setMsg((x as Error).message); }
  };
  const children = (v.children as { name: string; birth_date: string | null }[]) || [];
  const edu = (v.education as Record<string, string>[]) || [];
  const field = ([k, label, type]: [string, string, string?]) => (
    <label key={k} className={`f${type === "wide" ? " hr-wide" : ""}`}>{label}
      {type === "gender" ? <select value={String(v[k] || "")} disabled={!edit} onChange={(x) => set(k, x.target.value)}><option value="">—</option><option value="m">мужской</option><option value="f">женский</option></select>
        : <input type={type === "date" ? "date" : "text"} value={String(v[k] || "")} disabled={!edit} onChange={(x) => set(k, x.target.value || (type === "date" ? null : ""))} />}
    </label>);
  return (
    <div>
      <p className="muted">Хранятся зашифрованными; каждый просмотр записывается в журнал. {d.updated_at ? `Изменено: ${d.updated_by}, ${fmtDate(d.updated_at)}.` : "Ещё не заполнено."}</p>
      {msg && <div className="alert info" role="status">{msg}</div>}
      {d.warnings.length > 0 && <div className="alert warn" role="alert">{d.warnings.join(" · ")}</div>}
      {P_GROUPS.map(([title, fields]) => (
        <section key={title} className="card hr-form"><h3>{title}</h3><div className="grid cols-3">{fields.map(field)}</div></section>))}
      <section className="card hr-form"><h3>Дети</h3>
        {children.map((c, i) => (
          <div key={i} className="grid cols-3">
            <label className="f">Имя<input value={c.name} disabled={!edit} onChange={(x) => set("children", children.map((y, j) => (j === i ? { ...y, name: x.target.value } : y)))} /></label>
            <label className="f">Дата рождения<input type="date" value={c.birth_date || ""} disabled={!edit} onChange={(x) => set("children", children.map((y, j) => (j === i ? { ...y, birth_date: x.target.value || null } : y)))} /></label>
            {edit && <div className="row"><button className="btn ghost" onClick={() => set("children", children.filter((_, j) => j !== i))}>убрать</button></div>}
          </div>))}
        {edit && <button className="btn add" onClick={() => set("children", [...children, { name: "", birth_date: null }])}>Ребёнок</button>}
      </section>
      <section className="card hr-form"><h3>Образование</h3>
        {edu.map((c, i) => (
          <div key={i} className="grid cols-3">
            {([["level", "Уровень"], ["institution", "Учебное заведение"], ["year", "Год окончания"], ["specialty", "Специальность"], ["diploma", "Диплом №"]] as [string, string][]).map(([k, l]) => (
              <label key={k} className="f">{l}<input value={c[k] || ""} disabled={!edit} onChange={(x) => set("education", edu.map((y, j) => (j === i ? { ...y, [k]: x.target.value } : y)))} /></label>))}
            {edit && <div className="row"><button className="btn ghost" onClick={() => set("education", edu.filter((_, j) => j !== i))}>убрать</button></div>}
          </div>))}
        {edit && <button className="btn add" onClick={() => set("education", [...edu, { level: "", institution: "", year: "", specialty: "", diploma: "" }])}>Образование</button>}
      </section>
      <section className="card hr-form"><h3>Примечание</h3>
        <textarea aria-label="Примечание" value={String(v.notes || "")} disabled={!edit} onChange={(x) => set("notes", x.target.value)} rows={3} /></section>
      {edit && <div className="row hr-sticky"><button className="btn primary" disabled={!f} onClick={save}>Сохранить личные данные</button>
        {f && <button className="btn ghost" onClick={() => setF(null)}>отменить изменения</button>}</div>}
    </div>);
}

/* ───────── оплата труда (зашифрована; записи только добавляются) */

type PayRec = { id: string; employment_id: string; kind: string; title: string; effective_from: string; effective_to: string | null; cancels_id: string | null;
  cancelled: boolean; data: Record<string, string | number | null>; comment: string; recorded_by: string; recorded_at: string };
type PayCur = { salary: Record<string, string | number> | null; allowances: Record<string, string | number | null>[]; bank: Record<string, string> | null };
const PERIOD: Record<string, string> = { month: "в месяц", hour: "в час", day: "в день", shift: "за смену" };
const money = (a: unknown, c: unknown) => `${Number(a || 0).toLocaleString("ru-RU")} ${c === "RUB" || !c ? "₽" : String(c)}`;
const payWhat = (k: string, d: Record<string, unknown>) => k === "salary" ? `${money(d.amount, d.currency)} ${PERIOD[String(d.period)] || ""}`
  : k === "allowance" ? `${d.name}: ${d.percent != null ? `${String(d.percent).replace(".", ",")} %` : money(d.amount, d.currency)}`
    : k === "bank" ? [d.bank_name, d.bik ? `БИК ${d.bik}` : "", d.account ? `счёт ${d.account}` : "", d.card ? `карта ${d.card}` : ""].filter(Boolean).join(" · ") : "";

function PayTab({ e, me }: { e: Employee; me: Me }) {
  const [d, setD] = useState<{ current: Record<string, PayCur>; records: PayRec[] } | null>(null);
  const [on, setOn] = useState("");
  const [add, setAdd] = useState<{ m: string; kind: string } | null>(null);
  const [msg, setMsg] = useState("");
  const edit = can(me, "hr_payroll", 2);
  const load = () => api.get<NonNullable<typeof d>>(`/api/hr/employees/${e.id}/pay${on ? `?on=${on}` : ""}`).then(setD).catch((x) => setMsg((x as Error).message));
  useEffect(() => { load(); }, [e.id, on]);
  if (!d) return msg ? <ErrorState hint={msg} /> : <Loading />;
  const cancel = async (r: PayRec) => {
    const why = await askPrompt(`Отменить «${r.title}» с ${fmtDate(r.effective_from)}? Запись останется в истории. Причина:`, "", { title: "Отмена записи", required: true });
    if (!why) return;
    try { setD(await api.post(`/api/hr/employments/${r.employment_id}/pay`, { kind: "cancel", cancels_id: r.id, comment: why })); } catch (x) { setMsg((x as Error).message); }
  };
  return (
    <div>
      <p className="muted">Суммы и реквизиты хранятся зашифрованными; просмотр записывается в журнал. Официальный расчёт зарплаты остаётся в ЗУП.</p>
      {msg && <div className="alert warn" role="alert">{msg}</div>}
      <div className="row hr-filters"><label className="f">На дату<input type="date" value={on} onChange={(x) => setOn(x.target.value)} /></label></div>
      {e.employments.slice().reverse().map((m) => {
        const c = d.current[m.id];
        const recs = d.records.filter((r) => r.employment_id === m.id);
        return (
          <section key={m.id} className="card hr-emp">
            <div className="row hr-emp-head"><b>{m.employer}</b><span className="muted">{EMP_KIND[m.kind]} · с {fmtDate(m.start_date)}{m.end_date ? ` по ${fmtDate(m.end_date)}` : ""}</span></div>
            <div className="grid cols-3 hr-pay-now">
              <div><div className="pc-sub">Оклад / тариф</div><b>{c?.salary ? payWhat("salary", c.salary) : "—"}</b></div>
              <div><div className="pc-sub">Надбавки</div>{c?.allowances.length ? c.allowances.map((a, i) => <div key={i}>{payWhat("allowance", a)}</div>) : "—"}</div>
              <div><div className="pc-sub">Банковские реквизиты</div>{c?.bank ? payWhat("bank", c.bank) : "—"}</div>
            </div>
            {edit && m.status !== "terminated" && !add && <div className="row">
              <button className="btn ghost" onClick={() => setAdd({ m: m.id, kind: "salary" })}>Оклад / тариф</button>
              <button className="btn ghost" onClick={() => setAdd({ m: m.id, kind: "allowance" })}>Надбавка</button>
              <button className="btn ghost" onClick={() => setAdd({ m: m.id, kind: "bank" })}>Реквизиты</button></div>}
            {add?.m === m.id && <PayForm m={m} kind={add.kind} onDone={(r) => { setAdd(null); if (r) setD(r); }} />}
            {recs.length > 0 && <table className="pc-table nv-cards hr-events">
              <thead><tr><th>Запись</th><th>Действует</th><th>Что</th><th>Внесено</th><th /></tr></thead>
              <tbody>{recs.map((r) => (
                <tr key={r.id} className={r.cancelled ? "hr-cancelled" : ""}>
                  <td className="pc-name">{r.title}{r.cancelled && <span className="badge">отменено</span>}</td>
                  <td data-label="Действует">с {fmtDate(r.effective_from)}{r.effective_to ? ` по ${fmtDate(r.effective_to)}` : ""}</td>
                  <td data-label="Что">{r.kind === "cancel" ? (() => { const t = recs.find((x) => x.id === r.cancels_id); return t ? `отменяет «${t.title}» с ${fmtDate(t.effective_from)}` : ""; })() : payWhat(r.kind, r.data)}
                    {r.comment && <div className="pc-sub">{r.comment}</div>}</td>
                  <td data-label="Внесено">{r.recorded_by}<div className="pc-sub">{fmtDate(r.recorded_at)}</div></td>
                  <td>{edit && !r.cancelled && r.kind !== "cancel" && <button className="btn ghost" onClick={() => cancel(r)}>отменить</button>}</td>
                </tr>))}</tbody>
            </table>}
          </section>);
      })}
    </div>);
}

function PayForm({ m, kind, onDone }: { m: Employment; kind: string; onDone: (r?: { current: Record<string, PayCur>; records: PayRec[] }) => void }) {
  const [f, setF] = useState({ effective_from: today() < m.start_date ? m.start_date : today(), effective_to: "", amount: "", currency: "RUB", period: "month", name: "", percent: "",
    bank_name: "", bik: "", account: "", card: "", comment: "" });
  const [msg, setMsg] = useState("");
  const save = async () => {
    const b: Record<string, unknown> = { kind, effective_from: f.effective_from, effective_to: f.effective_to || null, comment: f.comment };
    if (kind === "salary") Object.assign(b, { amount: Number(f.amount), currency: f.currency, period: f.period });
    if (kind === "allowance") Object.assign(b, { name: f.name, percent: f.percent ? Number(f.percent) : null, amount: f.amount ? Number(f.amount) : null, currency: f.currency });
    if (kind === "bank") Object.assign(b, { bank_name: f.bank_name, bik: f.bik, account: f.account, card: f.card });
    try { onDone(await api.post(`/api/hr/employments/${m.id}/pay`, b)); } catch (x) { setMsg((x as Error).message); }
  };
  const inp = (k: keyof typeof f, label: string, type = "text") => (
    <label className="f">{label}<input type={type} value={f[k]} onChange={(x) => setF({ ...f, [k]: x.target.value })} /></label>);
  return (
    <div className="card hr-form">
      <h3>{{ salary: "Оклад / тариф", allowance: "Надбавка", bank: "Банковские реквизиты" }[kind]}</h3>
      {msg && <div className="alert warn" role="alert">{msg}</div>}
      <div className="grid cols-3">
        {inp("effective_from", "Действует с *", "date")}
        {kind !== "bank" && inp("effective_to", "По (если временно)", "date")}
        {kind === "salary" && <>{inp("amount", "Сумма *", "number")}
          <label className="f">За<select value={f.period} onChange={(x) => setF({ ...f, period: x.target.value })}>{Object.entries(PERIOD).map(([k, l]) => <option key={k} value={k}>{l.replace(/^в |^за /, "")}</option>)}</select></label>
          <label className="f">Валюта<select value={f.currency} onChange={(x) => setF({ ...f, currency: x.target.value })}><option>RUB</option><option>KGS</option><option>CNY</option><option>USD</option></select></label></>}
        {kind === "allowance" && <>{inp("name", "Название *")}{inp("percent", "Процент", "number")}{inp("amount", "или сумма", "number")}</>}
        {kind === "bank" && <>{inp("bank_name", "Банк")}{inp("bik", "БИК")}{inp("account", "Счёт")}{inp("card", "Карта (если выплата на карту)")}</>}
        {inp("comment", "Основание / комментарий")}
      </div>
      <div className="row"><button className="btn primary" onClick={save}>Сохранить</button><button className="btn ghost" onClick={() => onDone()}>отмена</button></div>
    </div>);
}

/* ───────── документы и сканы (файлы зашифрованы в хранилище) */

type DocKind = { code: string; title: string; data_class: string; can_read: boolean; can_edit: boolean };
type HDoc = { id: string; kind: string; kind_title: string; data_class: string; title: string; number: string; issued_on: string | null; valid_until: string | null;
  file_name: string; size: number; archived: boolean; comment: string; uploaded_by: string; uploaded_at: string; expired: boolean };
const CLASS_TITLE: Record<string, string> = { personal: "Личные документы", payroll: "Оплата труда", core: "Кадровые документы" };

async function openBlob(url: string, name: string) {
  const headers: Record<string, string> = {};
  const dev = getDevUser();
  if (dev) headers["X-Dev-User"] = dev;
  const res = await fetch(url, { headers, credentials: "include" });
  if (!res.ok) { const d = await res.json().catch(() => null); throw new Error(typeof d?.detail === "string" ? d.detail : `Ошибка ${res.status}`); }
  const href = URL.createObjectURL(await res.blob());
  const w = window.open(href, "_blank", "noopener");
  if (!w) { const a = document.createElement("a"); a.href = href; a.download = name; document.body.appendChild(a); a.click(); a.remove(); }
  setTimeout(() => URL.revokeObjectURL(href), 60000);
}

function DocsTab({ e }: { e: Employee }) {
  const [kinds, setKinds] = useState<DocKind[]>([]);
  const [d, setD] = useState<{ items: HDoc[]; hidden: number } | null>(null);
  const [all, setAll] = useState(false);
  const [f, setF] = useState({ kind: "", number: "", issued_on: "", valid_until: "", comment: "" });
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const load = () => api.get<NonNullable<typeof d>>(`/api/hr/employees/${e.id}/documents`).then(setD).catch((x) => setMsg((x as Error).message));
  useEffect(() => { load(); api.get<DocKind[]>("/api/hr/document-kinds").then(setKinds).catch(() => setKinds([])); }, [e.id]);
  if (!d) return msg ? <ErrorState hint={msg} /> : <Loading />;
  const editable = kinds.filter((k) => k.can_edit);
  const upload = async () => {
    const fd = new FormData();
    files.forEach((x) => fd.append("files", x));
    Object.entries(f).forEach(([k, v]) => v && fd.append(k, v));
    setBusy(true);
    try { await api.form(`/api/hr/employees/${e.id}/documents`, fd); setFiles([]); setF({ ...f, number: "", issued_on: "", valid_until: "", comment: "" }); setMsg("Загружено"); load(); }
    catch (x) { setMsg((x as Error).message); } finally { setBusy(false); }
  };
  const archive = async (x: HDoc) => { try { await api.patch(`/api/hr/documents/${x.id}`, { archived: !x.archived }); load(); } catch (er) { setMsg((er as Error).message); } };
  const items = d.items.filter((x) => all || !x.archived);
  const groups = (["personal", "payroll", "core"] as const).map((c) => [c, items.filter((x) => x.data_class === c)] as const).filter(([, xs]) => xs.length);
  const have = new Set(d.items.filter((x) => !x.archived).map((x) => x.kind));
  const missing = ["passport", "snils", "inn", "labor_contract", "consent_pd"].filter((k) => !have.has(k) && kinds.some((x) => x.code === k && x.can_read));
  return (
    <div>
      <p className="muted">Сканы проверяются антивирусом, хранятся зашифрованными и не удаляются — устаревший снимается с актуальных. Каждый просмотр записывается в журнал.</p>
      {msg && <div className="alert info" role="status">{msg}</div>}
      {missing.length > 0 && <div className="alert warn" role="status">Нет сканов: {missing.map((k) => kinds.find((x) => x.code === k)?.title).join(", ")}</div>}
      {editable.length > 0 && <section className="card hr-form"><h3>Загрузить скан</h3>
        <div className="grid cols-3">
          <label className="f">Документ *<select value={f.kind} onChange={(x) => setF({ ...f, kind: x.target.value })}><option value="">— выберите —</option>
            {(["personal", "payroll", "core"] as const).map((c) => editable.some((k) => k.data_class === c) && (
              <optgroup key={c} label={CLASS_TITLE[c]}>{editable.filter((k) => k.data_class === c).map((k) => <option key={k.code} value={k.code}>{k.title}</option>)}</optgroup>))}</select></label>
          <label className="f">Номер<input value={f.number} onChange={(x) => setF({ ...f, number: x.target.value })} /></label>
          <label className="f">Дата выдачи<input type="date" value={f.issued_on} onChange={(x) => setF({ ...f, issued_on: x.target.value })} /></label>
          <label className="f">Действует до<input type="date" value={f.valid_until} onChange={(x) => setF({ ...f, valid_until: x.target.value })} /></label>
          <label className="f">Комментарий<input value={f.comment} onChange={(x) => setF({ ...f, comment: x.target.value })} /></label>
          <label className="f">Файлы * (несколько страниц — несколько файлов)<input type="file" multiple accept="image/*,application/pdf,.doc,.docx" onChange={(x) => setFiles(Array.from(x.target.files || []))} /></label>
        </div>
        <div className="row"><button className="btn primary" disabled={!f.kind || !files.length || busy} onClick={upload}>{busy ? "Загружаю…" : `Загрузить${files.length > 1 ? ` (${files.length})` : ""}`}</button></div>
      </section>}
      <div className="row hr-filters"><label className="hr-check"><input type="checkbox" checked={all} onChange={(x) => setAll(x.target.checked)} />показать снятые с актуальных</label>
        {d.hidden > 0 && <span className="muted">ещё {d.hidden} — в классах, к которым у вас нет доступа</span>}</div>
      {groups.length === 0 ? <Empty title="Сканов пока нет" /> : groups.map(([c, xs]) => (
        <section key={c} className="card hr-emp"><h3>{CLASS_TITLE[c]}</h3>
          <table className="pc-table nv-cards hr-events">
            <thead><tr><th>Документ</th><th>Номер</th><th>Срок</th><th>Загружено</th><th /></tr></thead>
            <tbody>{xs.map((x) => (
              <tr key={x.id} className={x.archived ? "hr-cancelled" : ""}>
                <td className="pc-name"><button className="hr-name" onClick={() => openBlob(`/api/hr/documents/${x.id}/file`, x.file_name).catch((er) => setMsg((er as Error).message))}>{x.title}</button>
                  <div className="pc-sub">{x.file_name} · {Math.max(1, Math.round(x.size / 1024))} КБ</div></td>
                <td data-label="Номер">{x.number || "—"}{x.issued_on && <div className="pc-sub">выдан {fmtDate(x.issued_on)}</div>}</td>
                <td data-label="Срок">{x.valid_until ? <>до {fmtDate(x.valid_until)}{x.expired && <span className="badge bad">истёк</span>}</> : "—"}</td>
                <td data-label="Загружено">{x.uploaded_by}<div className="pc-sub">{fmtDate(x.uploaded_at)}</div></td>
                <td><button className="btn ghost" onClick={() => api.download(`/api/hr/documents/${x.id}/file?download=1`, x.file_name).catch((er) => setMsg((er as Error).message))}>скачать</button>
                  {kinds.find((k) => k.code === x.kind)?.can_edit && <button className="btn ghost" onClick={() => archive(x)}>{x.archived ? "вернуть" : "снять"}</button>}</td>
              </tr>))}</tbody>
          </table>
        </section>))}
    </div>);
}

/* ───────── допуски, обучение, инструктажи, медосмотры (D-206) — общий справочник и общая таблица с сервисом */

type AdmKind = { code: string; name: string; category: string; valid_months: number | null; is_draft: boolean };
type AdmKinds = { categories: Record<string, string>; results: Record<string, string>; items: AdmKind[] };
type Adm = { id: string; permit_code: string; name: string; category: string; number: string; issued_on: string | null; valid_until: string | null;
  result: string; result_title: string; comment: string; state: string; employee_id?: string; employee?: string; personnel_no?: string };
const ADM_STATE: Record<string, [string, string]> = { ok: ["действует", "ok"], expiring: ["истекает", "warn"], expired: ["истёк", "bad"], unfit: ["не годен", "bad"] };

function useAdmKinds() {
  const [k, setK] = useState<AdmKinds | null>(null);
  const load = () => api.get<AdmKinds>("/api/hr/admission-kinds").then(setK).catch(() => setK(null));
  useEffect(() => { load(); }, []);
  return { kinds: k, reload: load };
}

function AdmTab({ e, me }: { e: Employee; me: Me }) {
  const { kinds } = useAdmKinds();
  const [d, setD] = useState<{ items: Adm[]; has_login: boolean } | null>(null);
  const [f, setF] = useState<{ permit_code: string; number: string; issued_on: string; valid_until: string; result: string; comment: string } | null>(null);
  const [msg, setMsg] = useState("");
  const edit = can(me, "hr_core", 2);
  const load = () => api.get<NonNullable<typeof d>>(`/api/hr/employees/${e.id}/admissions`).then(setD).catch((x) => setMsg((x as Error).message));
  useEffect(() => { load(); }, [e.id]);
  if (!d || !kinds) return msg ? <ErrorState hint={msg} /> : <Loading />;
  const kind = kinds.items.find((k) => k.code === f?.permit_code);
  const save = async () => {
    if (!f) return;
    try { await api.put(`/api/hr/employees/${e.id}/admissions`, { ...f, issued_on: f.issued_on || null, valid_until: f.valid_until || null }); setF(null); load(); }
    catch (x) { setMsg((x as Error).message); }
  };
  const del = async (a: Adm) => {
    const ok = await askPrompt(`Удалить «${a.name}»? Запись останется в журнале действий. Напишите «да»:`, "", { title: "Удаление записи", required: true });
    if (ok?.trim().toLowerCase() !== "да") return;
    try { await api.del(`/api/hr/admissions/${a.id}`); load(); } catch (x) { setMsg((x as Error).message); }
  };
  const groups = Object.entries(kinds.categories).map(([c, t]) => [c, t, d.items.filter((x) => x.category === c)] as const).filter(([, , xs]) => xs.length);
  return (
    <div>
      <p className="muted">Общие с сервисом: {d.has_login ? "назначая сотрудника на наряд, сервис проверяет эти допуски." : "у сотрудника нет входа в CRM — сервис его на наряды не назначает, но сроки видны здесь и в сводке."} Медосмотр — только итог и срок, без диагноза. Сканы удостоверений — во вкладке «Документы и сканы».</p>
      {msg && <div className="alert warn" role="alert">{msg}</div>}
      {edit && !f && <div className="row"><button className="btn add primary" onClick={() => setF({ permit_code: "", number: "", issued_on: today(), valid_until: "", result: "", comment: "" })}>Допуск, обучение или медосмотр</button></div>}
      {f && <div className="card hr-form"><h3>Запись</h3>
        <div className="grid cols-3">
          <label className="f">Вид *<select value={f.permit_code} onChange={(x) => setF({ ...f, permit_code: x.target.value, result: "" })}><option value="">— выберите —</option>
            {Object.entries(kinds.categories).map(([c, t]) => <optgroup key={c} label={t}>{kinds.items.filter((k) => k.category === c).map((k) => <option key={k.code} value={k.code}>{k.name}{k.valid_months ? ` · ${k.valid_months} мес.` : ""}</option>)}</optgroup>)}</select></label>
          <label className="f">Номер удостоверения / протокола<input value={f.number} onChange={(x) => setF({ ...f, number: x.target.value })} /></label>
          <label className="f">Дата выдачи / прохождения<input type="date" value={f.issued_on} onChange={(x) => setF({ ...f, issued_on: x.target.value })} /></label>
          <label className="f">Действует до<input type="date" value={f.valid_until} placeholder="по справочнику" onChange={(x) => setF({ ...f, valid_until: x.target.value })} /></label>
          {kind?.category === "medical" && <label className="f">Итог<select value={f.result} onChange={(x) => setF({ ...f, result: x.target.value })}><option value="">—</option>
            {Object.entries(kinds.results).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>}
          <label className="f">Комментарий<input value={f.comment} onChange={(x) => setF({ ...f, comment: x.target.value })} /></label>
        </div>
        <p className="muted">Срок не указан — считается от даты выдачи по справочнику{kind?.valid_months ? ` (${kind.valid_months} мес.)` : ""}; нет срока в справочнике — бессрочно. Тот же вид у сотрудника уже есть — запись продлевается.</p>
        <div className="row"><button className="btn primary" disabled={!f.permit_code} onClick={save}>Сохранить</button><button className="btn ghost" onClick={() => setF(null)}>отмена</button></div>
      </div>}
      {groups.length === 0 ? <Empty title="Допусков пока нет" /> : groups.map(([c, t, xs]) => (
        <section key={c} className="card hr-emp"><h3>{t}</h3>
          <table className="pc-table nv-cards hr-events">
            <thead><tr><th>Вид</th><th>Номер</th><th>Выдан</th><th>Действует до</th><th>Состояние</th><th /></tr></thead>
            <tbody>{xs.map((a) => { const [st, tone] = ADM_STATE[a.state] || [a.state, ""]; return (
              <tr key={a.id}>
                <td className="pc-name">{a.name}<div className="pc-sub">{a.permit_code}{a.result_title ? ` · ${a.result_title}` : ""}</div>{a.comment && <div className="pc-sub">{a.comment}</div>}</td>
                <td data-label="Номер">{a.number || "—"}</td>
                <td data-label="Выдан">{a.issued_on ? fmtDate(a.issued_on) : "—"}</td>
                <td data-label="Действует до">{a.valid_until ? fmtDate(a.valid_until) : "бессрочно"}</td>
                <td data-label="Состояние"><span className={`badge ${tone}`}>{st}</span></td>
                <td>{edit && <><button className="btn ghost" onClick={() => setF({ permit_code: a.permit_code, number: a.number, issued_on: today(), valid_until: "", result: a.result, comment: "" })}>продлить</button>
                  <button className="btn ghost" onClick={() => del(a)}>удалить</button></>}</td>
              </tr>); })}</tbody>
          </table>
        </section>))}
    </div>);
}

function AdmOverview({ me }: { me: Me }) {
  const { kinds, reload } = useAdmKinds();
  const [days, setDays] = useState("30");
  const [d, setD] = useState<{ items: Adm[] } | null>(null);
  const [nk, setNk] = useState<{ code: string; name: string; category: string; valid_months: string } | null>(null);
  const [msg, setMsg] = useState("");
  const nav = useNavigate();
  const edit = can(me, "hr_core", 2);
  useEffect(() => { api.get<{ items: Adm[] }>(`/api/hr/admissions/overview?days=${days || 30}`).then(setD).catch((x) => setMsg((x as Error).message)); }, [days]);
  const addKind = async () => {
    if (!nk) return;
    try { await api.post("/api/hr/admission-kinds", { ...nk, valid_months: nk.valid_months ? Number(nk.valid_months) : null }); setNk(null); reload(); setMsg("Добавлено в справочник"); }
    catch (x) { setMsg((x as Error).message); }
  };
  return (
    <div>
      {msg && <div className="alert info" role="status">{msg}</div>}
      <section className="card">
        <div className="row hr-filters"><h3>Требуют внимания</h3>
          <label className="f">Истекают в ближайшие, дней<input type="number" min="1" max="365" value={days} onChange={(x) => setDays(x.target.value)} /></label></div>
        {!d ? <Loading /> : d.items.length === 0 ? <Empty title="Всё действует" hint="Нет истёкших, истекающих допусков и отметок «не годен»." /> : (
          <table className="pc-table nv-cards hr-table">
            <thead><tr><th>Сотрудник</th><th>Допуск, обучение, медосмотр</th><th>Действует до</th><th>Состояние</th></tr></thead>
            <tbody>{d.items.map((a) => { const [st, tone] = ADM_STATE[a.state] || [a.state, ""]; return (
              <tr key={a.id} className="hr-row" onClick={() => nav(`/hr?e=${a.employee_id}&s=adm`)}>
                <td className="pc-name">{a.employee}<div className="pc-sub">{a.personnel_no}</div></td>
                <td data-label="Что">{a.name}<div className="pc-sub">{a.permit_code}{a.result_title ? ` · ${a.result_title}` : ""}</div></td>
                <td data-label="Действует до">{a.valid_until ? fmtDate(a.valid_until) : "—"}</td>
                <td data-label="Состояние"><span className={`badge ${tone}`}>{st}</span></td>
              </tr>); })}</tbody>
          </table>)}
      </section>
      {kinds && <section className="card">
        <div className="row"><h3>Справочник</h3>{edit && !nk && <button className="btn add" onClick={() => setNk({ code: "", name: "", category: "training", valid_months: "" })}>Вид</button>}</div>
        <p className="muted">Общий с сервисом: виды работ в сервисе ссылаются на эти коды. Новые виды отсюда сразу видны и в сервисе.</p>
        {nk && <div className="hr-form"><div className="grid cols-3">
          <label className="f">Код *<input value={nk.code} placeholder="MED-PER" onChange={(x) => setNk({ ...nk, code: x.target.value })} /></label>
          <label className="f">Название *<input value={nk.name} onChange={(x) => setNk({ ...nk, name: x.target.value })} /></label>
          <label className="f">Категория<select value={nk.category} onChange={(x) => setNk({ ...nk, category: x.target.value })}>{Object.entries(kinds.categories).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
          <label className="f">Срок действия, мес.<input type="number" min="1" value={nk.valid_months} placeholder="бессрочно" onChange={(x) => setNk({ ...nk, valid_months: x.target.value })} /></label>
        </div><div className="row"><button className="btn primary" disabled={!nk.code.trim() || !nk.name.trim()} onClick={addKind}>Добавить</button><button className="btn ghost" onClick={() => setNk(null)}>отмена</button></div></div>}
        <ul className="hr-tree">{kinds.items.map((k) => (
          <li key={k.code}><span>{k.name}</span><span className="muted"> · {k.code} · {kinds.categories[k.category] || k.category} · {k.valid_months ? `${k.valid_months} мес.` : "бессрочно"}</span>
            {k.is_draft && <span className="badge">черновик</span>}</li>))}</ul>
      </section>}
    </div>);
}

/* ───────── сводка (D-207): численность, штат, движение, чего не хватает */

type SumEmp = { employer_id: string; employer: string; people: number; rate: number; fte_plan: number; fte_busy: number; vacant_slots: number; over_slots: number;
  hired_month: number; left_month: number; hired_prev: number; left_prev: number; departments: { name: string; people: number }[] };
type Sum = { on: string; employers: SumEmp[]; gaps: { employee_id: string; employee: string; personnel_no: string; docs: string[]; personal: string[] }[];
  gaps_scope: string[]; offboard: { employee_id: string; employee: string; user: string; end_date: string }[]; total: { people: number } };
const num = (v: number) => String(Math.round(v * 100) / 100).replace(".", ",");

function Summary() {
  const [d, setD] = useState<Sum | null>(null);
  const [on, setOn] = useState("");
  const [msg, setMsg] = useState("");
  const nav = useNavigate();
  useEffect(() => { api.get<Sum>(`/api/hr/summary${on ? `?on=${on}` : ""}`).then(setD).catch((x) => setMsg((x as Error).message)); }, [on]);
  if (!d) return msg ? <ErrorState hint={msg} /> : <Loading />;
  const open = (id: string, s = "") => nav(`/hr?e=${id}${s ? `&s=${s}` : ""}`);
  return (
    <div>
      <div className="row hr-filters"><label className="f">На дату<input type="date" value={on} onChange={(x) => setOn(x.target.value)} /></label>
        <span className="muted">работает сотрудников: {d.total.people}</span></div>
      {d.offboard.length > 0 && <div className="alert warn" role="alert"><b>Уволены, вход в CRM не отключён:</b>{" "}
        {d.offboard.map((o, i) => <span key={o.employee_id}>{i ? ", " : ""}<button className="hr-name" onClick={() => open(o.employee_id)}>{o.employee}</button> (уволен {fmtDate(o.end_date)})</span>)}</div>}
      <div className="grid cols-2 hr-sum">{d.employers.map((r) => (
        <section key={r.employer_id} className="card">
          <h3>{r.employer}</h3>
          <div className="grid cols-3 hr-kpi">
            <div><div className="pc-sub">Работают</div><b>{r.people}</b><div className="pc-sub">{num(r.rate)} ст.</div></div>
            <div><div className="pc-sub">Штат: занято / всего</div><b>{num(r.fte_busy)} / {num(r.fte_plan)}</b>
              <div className="pc-sub">{r.vacant_slots ? `вакансий: ${r.vacant_slots}` : "без вакансий"}{r.over_slots ? ` · сверх штата: ${r.over_slots}` : ""}</div></div>
            <div><div className="pc-sub">Приём / увольнение за месяц</div><b>+{r.hired_month} / −{r.left_month}</b><div className="pc-sub">прошлый: +{r.hired_prev} / −{r.left_prev}</div></div>
          </div>
          {r.departments.length > 0 && <ul className="hr-tree">{r.departments.map((x) => <li key={x.name}><span>{x.name}</span><span className="muted">{x.people}</span></li>)}</ul>}
        </section>))}</div>
      <section className="card">
        <h3>Чего не хватает до ввода реальных данных</h3>
        <p className="muted">Сканы: паспорт, СНИЛС, ИНН, трудовой договор, согласие на обработку ПДн. Личные данные: дата рождения, паспорт, СНИЛС, ИНН, адрес регистрации.
          {!d.gaps_scope.includes("personal") && " Личные данные и их сканы вам не видны — нет доступа."}</p>
        {d.gaps.length === 0 ? <Empty title="Всё заполнено" /> : (
          <table className="pc-table nv-cards hr-table">
            <thead><tr><th>Сотрудник</th><th>Нет сканов</th><th>Не заполнено</th></tr></thead>
            <tbody>{d.gaps.map((g) => (
              <tr key={g.employee_id} className="hr-row" onClick={() => open(g.employee_id, g.docs.length ? "docs" : "personal")}>
                <td className="pc-name">{g.employee}<div className="pc-sub">{g.personnel_no}</div></td>
                <td data-label="Нет сканов">{g.docs.join(", ") || "—"}</td>
                <td data-label="Не заполнено">{g.personal.join(", ") || "—"}</td>
              </tr>))}</tbody>
          </table>)}
      </section>
    </div>);
}
