import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate } from "../api";
import { Crumbs } from "../components/Levels";
import { Empty, ErrorState, Loading } from "../components/States";
import { can, type Me } from "../types";

/** D-211: сверка переноса контактов в единый справочник и приёмка владельцем данных. Только просмотр + отметка о приёмке. */

type Legacy = { name: string; position: string; email: string; phone: string; mobile: string; bad_email: boolean };
type Dir = { id: string; name: string; position: string; email: string; phone: string; mobile: string; roles: string[]; active: boolean };
type Review = {
  sources: { source: string; title: string; rows: number; created: number; matched: number; ambiguous: number }[];
  pending: (Legacy & { client_id: string; client: string; source: string; outcome: string; outcome_title: string })[]; pending_total: number; pending_ambiguous: number;
  no_channel: (Dir & { client_id: string; client: string })[]; no_channel_total: number;
  dup_email: { client_id: string; client: string; email: string; names: string[] }[]; dup_email_total: number;
  bad_email: (Legacy & { client_id: string; client: string; source: string })[]; bad_email_total: number;
  sample: { client_id: string; client: string; legacy: (Legacy & { outcome: string })[]; directory: Dir[] }[]; sample_seed: string;
  directory_total: number; clients_with_legacy: number;
  accepted: null | { at: string; by_name: string; comment: string; checked_clients: number; counts: { pending: number; directory: number } };
};
const OUT: Record<string, [string, string]> = { created: ["создан", "ok"], matched: ["сопоставлен", "ok"], ambiguous: ["вручную", "warn"], pending: ["не перенесён", "warn"] };
const ch = (x: { email: string; phone: string; mobile: string }) => [x.email, x.phone, x.mobile].filter(Boolean).join(" · ") || "—";
const clientLink = (id: string) => `/clients?client=${id}`;

export default function ContactsMigrationPage({ me }: { me: Me }) {
  const [d, setD] = useState<Review | null>(null);
  const [seed, setSeed] = useState("");
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const [comment, setComment] = useState("");
  const [msg, setMsg] = useState("");
  const load = (s = seed) => api.get<Review>(`/api/contacts-migration?sample=10${s ? `&seed=${encodeURIComponent(s)}` : ""}`).then((r) => { setD(r); setSeed(r.sample_seed); })
    .catch((x) => setMsg((x as Error).message));
  useEffect(() => { if (can(me, "client_contacts", 2)) load(); }, []);
  if (!can(me, "client_contacts", 2)) return <div><h1>Сверка переноса контактов</h1><Empty title="Нет доступа" hint="Сверку проводит редактор справочника контактов." /></div>;
  if (!d) return msg ? <ErrorState hint={msg} /> : <Loading />;
  const nChecked = Object.values(checked).filter(Boolean).length;
  const accept = async () => {
    try { await api.post("/api/contacts-migration/accept", { comment, sample_seed: seed, checked_clients: nChecked }); setMsg("Перенос принят"); load(); }
    catch (x) { setMsg((x as Error).message); }
  };
  return (
    <div className="cmr">
      <Crumbs items={[{ label: "Клиенты", to: "/clients" }, { label: "Сверка переноса контактов" }]} />
      <div className="page-head"><h1>Сверка переноса контактов</h1>
        {d.accepted ? <span className="badge ok">принято {fmtDate(d.accepted.at)} — {d.accepted.by_name}</span> : <span className="badge warn">не принято</span>}</div>
      <p className="muted">Перенос в единый справочник выполнен на рабочем сервере 27.11. Здесь — проверка результата: данные не меняются, кроме отметки о приёмке.
        Исправлять контакт — в карточке клиента.</p>
      {msg && <div className="alert info" role="status">{msg}</div>}

      <section className="card"><h3>Итоги переноса</h3>
        <table className="pc-table nv-cards"><thead><tr><th>Источник</th><th>Строк сейчас</th><th>Создано контактов</th><th>Сопоставлено</th><th>Вручную</th></tr></thead>
          <tbody>{d.sources.map((s) => (
            <tr key={s.source}><td className="pc-name">{s.title}</td><td data-label="Строк сейчас">{s.rows}</td><td data-label="Создано">{s.created}</td>
              <td data-label="Сопоставлено">{s.matched}</td><td data-label="Вручную">{s.ambiguous}</td></tr>))}</tbody></table>
        <p className="muted">В справочнике контактов: {d.directory_total}; клиентов с прежним списком: {d.clients_with_legacy}.</p>
      </section>

      <Block title="Не перенесено" total={d.pending_total} hint="Строки прежних списков, которых нет в журнале переноса (например, изменены после 27.11), и как их перенёс бы скрипт. «Вручную» — нет ФИО или совпадает только ФИО: разберите в карточке клиента.">
        <table className="pc-table nv-cards"><thead><tr><th>Клиент</th><th>Строка прежнего списка</th><th>Источник</th><th>Скрипт</th></tr></thead>
          <tbody>{d.pending.map((p, i) => (
            <tr key={i}><td className="pc-name"><Link to={clientLink(p.client_id)}>{p.client}</Link></td><td data-label="Строка">{p.name || "без ФИО"}<div className="pc-sub">{ch(p)}</div></td>
              <td data-label="Источник">{p.source}</td><td data-label="Скрипт"><span className={`badge ${p.outcome === "ambiguous" ? "warn" : ""}`}>{p.outcome_title}</span></td></tr>))}</tbody></table>
      </Block>

      <Block title="Нет канала связи" total={d.no_channel_total} hint="Действующие контакты без почты и телефонов — им нельзя отправить КП и позвонить.">
        <ul className="cmr-list">{d.no_channel.map((x) => <li key={x.id}><Link to={clientLink(x.client_id)}>{x.client}</Link> — {x.name}{x.position ? `, ${x.position}` : ""}</li>)}</ul>
      </Block>
      <Block title="Одна почта у разных людей одного клиента" total={d.dup_email_total} hint="Обычно — общий ящик или ошибка при вводе. Проверьте, кому на самом деле уходит КП.">
        <ul className="cmr-list">{d.dup_email.map((x, i) => <li key={i}><Link to={clientLink(x.client_id)}>{x.client}</Link> — {x.email}: {x.names.join(", ")}</li>)}</ul>
      </Block>
      <Block title="Почта с ошибкой в источнике" total={d.bad_email_total} hint="Такая почта не перенесена — контакт создан без неё. Уточните адрес и внесите в справочник.">
        <ul className="cmr-list">{d.bad_email.map((x, i) => <li key={i}><Link to={clientLink(x.client_id)}>{x.client}</Link> — {x.name}: «{x.email}» ({x.source})</li>)}</ul>
      </Block>

      <section className="card"><div className="row cmr-head"><h3>Контрольная выборка: прежний список и справочник</h3>
        <label className="f">Выборка<input value={seed} onChange={(e) => setSeed(e.target.value)} /></label>
        <button className="btn ghost" onClick={() => { setChecked({}); load(seed); }}>показать</button>
        <button className="btn ghost" onClick={() => { const s = Math.random().toString(36).slice(2, 8); setChecked({}); load(s); }}>другая выборка</button></div>
        <p className="muted">Сверьте по каждому клиенту, что люди, почта и телефоны совпадают, и отметьте «сверено».</p>
        {d.sample.length === 0 ? <Empty title="Нет клиентов с прежним списком" /> : d.sample.map((s) => (
          <div key={s.client_id} className="cmr-pair">
            <div className="row cmr-head"><b><Link to={clientLink(s.client_id)}>{s.client}</Link></b>
              <label className="cmr-check"><input type="checkbox" checked={!!checked[s.client_id]} onChange={(e) => setChecked({ ...checked, [s.client_id]: e.target.checked })} />сверено</label></div>
            <div className="grid cols-2">
              <div><div className="pc-sub">Прежний список</div><ul className="cmr-list">{s.legacy.map((x, i) => { const [t, tone] = OUT[x.outcome] || [x.outcome, ""];
                return <li key={i}>{x.name || "без ФИО"} <span className="pc-sub">{ch(x)}</span> <span className={`badge ${tone}`}>{t}</span></li>; })}</ul></div>
              <div><div className="pc-sub">Справочник</div><ul className="cmr-list">{s.directory.map((x) => <li key={x.id}>{x.name} <span className="pc-sub">{ch(x)}</span>{!x.active && <span className="badge">архив</span>}</li>)}</ul></div>
            </div>
          </div>))}
      </section>

      <section className="card"><h3>Приёмка</h3>
        {d.accepted && <p>Принято {fmtDate(d.accepted.at)}, {d.accepted.by_name}: «{d.accepted.comment}». Сверено клиентов в выборке: {d.accepted.checked_clients}.</p>}
        {d.pending_ambiguous > 0 ? <div className="alert warn" role="alert">Есть {d.pending_ambiguous} строк для ручного разбора — приёмка после них.</div> : <>
          <label className="f">Комментарий *<input value={comment} placeholder="выборка 10 клиентов сверена, замечаний нет" onChange={(e) => setComment(e.target.value)} /></label>
          <div className="row"><button className="btn primary" disabled={comment.trim().length < 3} onClick={accept}>{d.accepted ? "Принять повторно" : "Принять перенос"}</button>
            <span className="muted">сверено в выборке: {nChecked} из {d.sample.length}</span></div></>}
        <p className="muted">После приёмки назначим дату, с которой прежний список в карточке клиента перестанет показываться.</p>
      </section>
    </div>);
}

function Block({ title, total, hint, children }: { title: string; total: number; hint: string; children: React.ReactNode }) {
  return (
    <section className="card"><div className="row cmr-head"><h3>{title}</h3><span className={`badge ${total ? "warn" : "ok"}`}>{total}</span></div>
      <p className="muted">{hint}</p>{total ? children : <Empty title="Нет" />}{total > 300 && <p className="muted">Показаны первые 300.</p>}</section>);
}
