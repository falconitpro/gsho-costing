import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../api";

/** D-188: адресаты КП — только контакты клиента из единого справочника. Ничего не отмечено заранее; у каждого адреса —
 *  «Кому» / «Копия»; фильтр по ролям только подсказывает; перед отправкой — проверка на сервере (кто, адрес, роль, Кому / Копия).
 *  Адреса вне справочника не вводятся: контакт добавляется в карточке клиента. */

export type Candidate = { contact_id: string | null; link_id: string | null; name: string; position: string; roles_titles: string[]; is_primary: boolean;
  emails: string[]; source: string };
export type Picked = { to: string[]; cc: string[] };
type Checked = { kind: string; email: string; name: string; position: string; roles_titles: string[]; source: string };

export function newDispatchKey(): string {
  try { return crypto.randomUUID().replace(/-/g, ""); } catch { return `${Date.now()}${Math.random().toString(16).slice(2)}`; }
}

export default function KpRecipients({ dealId, candidates, clientId, onChange }: { dealId: string; candidates: Candidate[]; clientId?: string | null;
  onChange: (p: Picked, checked: boolean) => void }) {
  const [kind, setKind] = useState<Record<string, "" | "to" | "cc">>({});
  const [role, setRole] = useState("");
  const [checked, setChecked] = useState<Checked[] | null>(null);
  const [err, setErr] = useState("");
  const roles = useMemo(() => [...new Set(candidates.flatMap((c) => c.roles_titles))], [candidates]);
  const picked: Picked = { to: Object.entries(kind).filter(([, k]) => k === "to").map(([e]) => e), cc: Object.entries(kind).filter(([, k]) => k === "cc").map(([e]) => e) };
  useEffect(() => { setChecked(null); setErr(""); onChange(picked, false); }, [JSON.stringify(kind)]);   // eslint-disable-line react-hooks/exhaustive-deps
  const check = () => api.post<{ items: Checked[] }>(`/api/deals/${dealId}/kp/recipients/preview`, picked)
    .then((r) => { setChecked(r.items); setErr(""); onChange(picked, true); })
    .catch((e) => { const d = (e as ApiError).detail as { message?: string } | string; setErr(typeof d === "string" ? d : d?.message || (e as Error).message); setChecked(null); onChange(picked, false); });
  const shown = candidates.filter((c) => !role || c.roles_titles.includes(role));

  return (
    <div className="kr">
      <div className="kr-head">
        <b>Получатели</b>
        {roles.length > 0 && <div className="kr-roles" aria-label="Фильтр по ролям">
          <button className={"chip" + (role ? "" : " on")} onClick={() => setRole("")}>все</button>
          {roles.map((r) => <button key={r} className={"chip" + (role === r ? " on" : "")} onClick={() => setRole(r)}>{r}</button>)}
        </div>}
      </div>
      {candidates.length === 0 && <div className="alert warn">У клиента нет контактов с почтой.{clientId && <> <a href={`/legal?tab=counterparties&type=clients&client=${clientId}`} target="_blank" rel="noreferrer">Добавить контакт в карточке клиента</a></>}</div>}
      <ul className="kr-list">
        {shown.flatMap((c) => c.emails.map((em) => (
          <li key={em} className={kind[em] ? "kr-on" : ""}>
            <div className="kr-who"><b>{c.name || em}</b>{c.position && <span className="muted"> · {c.position}</span>}
              {c.roles_titles.map((t) => <span key={t} className="badge">{t}</span>)}{c.source !== "справочник" && <span className="badge warn" title="адрес из прежнего списка карточки клиента">прежний список</span>}
              <div className="muted">{em}</div></div>
            <select aria-label={`Как отправить ${em}`} value={kind[em] || ""} onChange={(e) => setKind({ ...kind, [em]: e.target.value as "" | "to" | "cc" })}>
              <option value="">не отправлять</option><option value="to">Кому</option><option value="cc">Копия</option></select>
          </li>)))}
      </ul>
      {clientId && candidates.length > 0 && <div className="muted kr-note">Нужного адресата нет? <a href={`/legal?tab=counterparties&type=clients&client=${clientId}`} target="_blank" rel="noreferrer">Добавьте контакт в карточке клиента</a> и откройте окно снова.</div>}
      <div className="row">
        <button className="btn secondary" disabled={picked.to.length === 0} onClick={check}>Проверить получателей</button>
        {picked.to.length === 0 && <span className="muted">Выберите хотя бы одного адресата «Кому».</span>}
      </div>
      {err && <div className="alert error">{err}</div>}
      {checked && (
        <table className="kr-preview"><thead><tr><th>Как</th><th>Кто</th><th>Адрес</th><th>Роль</th></tr></thead>
          <tbody>{checked.map((i) => <tr key={i.email}><td>{i.kind === "to" ? "Кому" : "Копия"}</td><td>{i.name}{i.position && <div className="muted">{i.position}</div>}</td>
            <td>{i.email}</td><td>{i.roles_titles.join(", ") || "—"}</td></tr>)}</tbody></table>)}
    </div>
  );
}
