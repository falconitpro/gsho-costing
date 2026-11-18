import { useEffect, useState } from "react";
import { api, fmtDate, fmtRub } from "../api";

// Рабочее место логиста (D-073): адреса отгрузки и доставки, выбранные ЗГД характеристики, запрос логистическим
// компаниям (Excel / PDF / почта) и их ответы.

export type WsField = { code: string; name: string; unit: string; value: string; requirement: string };
export type WsPos = { position_id: string; machine_type: string; designation: string; qty: number; rows: WsField[] };
export type Quote = { id: string; carrier: string; amount: number; currency: string; transit_days: number | null; valid_until: string; scope: string;
  comment: string; file_name: string; storage_path: string; at: string; by_name: string };
type Carrier = { name: string; email: string; phone?: string; comment?: string };
type Req = { id: string; no: string; at: string; by_name: string; answer_by: string; sent_to: string[]; files: { manual?: boolean }; services?: string[] };
type Adr = { pickup: { factory_id: string; factory: string; card: string; address: string; edited: boolean }[]; delivery_address: string; delivery_source: string;
  client_addresses: { id: string; address: string; status: string; deal_number?: string }[] };
export type CmpRow = { carrier: string; lines: Record<string, { id: string; amount: number; currency: string; rub: number; transit_days: number | null; valid_until: string; comment: string }>;
  total_rub: number; transit_days: number | null; missing: string[]; complete: boolean; mode: string };
export type Compare = { services: string[]; rows: CmpRow[]; best: string | null; fastest: string | null; chosen: string; chosen_at: string; waiting: string[];
  chosen_status: string; chosen_comment: string; gd_comment: string; gd_decision_at: string };
export type Workspace = { fields: WsPos[]; addresses: Adr; carriers: Carrier[]; requests: Req[]; quotes: Quote[]; mail_enabled: boolean; can_choose_fields: boolean; can_edit: boolean;
  services: string[]; service_options: { code: string; title: string }[]; compare: Compare };

export const SCOPES: Record<string, string> = { door_to_door: "Завод → пункт поставки (единой ставкой)", intl_freight: "До границы РФ", rf_delivery: "Доставка по РФ",
  insurance: "Страхование", customs: "Таможня в РФ (брокер, СВХ)", export: "Экспортное оформление" };
const CURS = ["RUB", "CNY", "USD", "EUR"];

/** Выбранные ЗГД характеристики позиции — компактно, для расчёта доставки и таможни. */
export function LogistFields({ pos, canChoose, onChoose }: { pos?: WsPos; canChoose: boolean; onChoose: () => void }) {
  if (!pos) return null;
  const filled = pos.rows.filter((r) => r.value || r.requirement).length;
  return (
    <div className="lw-fields">
      <div className="lw-fields-head"><b>Данные для логиста</b> <span className="muted">· заполнено {filled} из {pos.rows.length} · все характеристики — на вкладке «Позиции и характеристики»</span>
        {canChoose && <button className="btn ghost" onClick={onChoose}>⚙ выбрать поля</button>}</div>
      <div className="lw-fields-grid">{pos.rows.map((r) => (
        <div key={(r.code || "") + r.name} className={"lw-f" + (r.value || r.requirement ? "" : " lw-f-empty")}>
          <span className="muted">{r.name}</span><b>{r.value || r.requirement || "—"}{(r.value || r.requirement) && r.unit ? ` ${r.unit}` : ""}</b>
          {!r.value && r.requirement && <span className="muted lw-f-note">требование клиента</span>}</div>))}
        {pos.rows.length === 0 && <span className="muted">Поля не выбраны{canChoose ? " — нажмите «выбрать поля»" : ""}.</span>}</div>
    </div>);
}

/** ЗГД выбирает характеристики, которые видит логист (общая настройка для всех сделок). */
export function FieldsChooser({ dealId, onClose }: { dealId: string; onClose: () => void }) {
  const [opts, setOpts] = useState<{ code: string; name: string; section: string }[]>([]);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [q, setQ] = useState("");
  const [err, setErr] = useState("");
  const k = (o: { code: string; name: string }) => o.code || o.name.toLowerCase();
  useEffect(() => { api.get<{ fields: { code: string; name: string }[]; options: { code: string; name: string; section: string }[] }>(`/api/refs/logistics-fields?deal_id=${dealId}`)
    .then((r) => { setOpts(r.options); setSel(new Set(r.fields.map(k))); }).catch((e) => setErr(e.message)); }, [dealId]);
  const shown = opts.filter((o) => !q || (o.name + o.code + o.section).toLowerCase().includes(q.toLowerCase()));
  const sections = [...new Set(shown.map((o) => o.section))];
  const save = () => api.put("/api/refs/logistics-fields", opts.filter((o) => sel.has(k(o))).map((o) => ({ code: o.code, name: o.name }))).then(onClose).catch((e) => setErr(e.message));
  return (
    <div className="lw-modal"><div className="card lw-modal-body">
      <div className="row" style={{ justifyContent: "space-between" }}><h3 style={{ margin: 0 }}>Какие характеристики видит логист</h3><button className="btn ghost" onClick={onClose}>×</button></div>
      <p className="muted" style={{ marginTop: 4 }}>Общая настройка для всех сделок (выбирает ЗГД). Выбрано: <b>{sel.size}</b>. Обычно нужны габариты, массы, страна производства, объём, тип упаковки.</p>
      {err && <div className="alert error">{err}</div>}
      <input placeholder="поиск по названию" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: "100%", marginBottom: 8 }} />
      <div className="lw-modal-list">{sections.map((s) => (
        <div key={s} style={{ marginBottom: 8 }}><b className="muted">{s}</b>
          {shown.filter((o) => o.section === s).map((o) => (
            <label key={k(o)} className="lw-opt"><input type="checkbox" checked={sel.has(k(o))} onChange={(e) => { const n = new Set(sel); if (e.target.checked) n.add(k(o)); else n.delete(k(o)); setSel(n); }} />
              {o.name} {o.code && <span className="muted">{o.code}</span>}</label>))}
        </div>))}</div>
      <div className="row" style={{ gap: 8, marginTop: 8 }}><button className="btn" onClick={save}>Сохранить</button><button className="btn ghost" onClick={onClose}>Отмена</button></div>
    </div></div>);
}

/** Адреса отгрузки и доставки — вводимые данные, вверху блока. */
export function LogistAddresses({ dealId, ws, reload }: { dealId: string; ws: Workspace; reload: () => void }) {
  const edit = ws.can_edit;
  const init = () => ({ pickup: Object.fromEntries(ws.addresses.pickup.map((p) => [p.factory_id, p.address])), delivery: ws.addresses.delivery_address, propose: true });
  const [adr, setAdr] = useState(init);
  const [msg, setMsg] = useState<{ ok: boolean; t: string } | null>(null);
  useEffect(() => { setAdr(init()); }, [ws.addresses]);
  const known = new Set(ws.addresses.client_addresses.map((a) => a.address.trim().toLowerCase()));
  const isNew = !!adr.delivery.trim() && !known.has(adr.delivery.trim().toLowerCase());
  const saveAdr = () => api.put<{ proposed: boolean }>(`/api/deals/${dealId}/logistics/addresses`, { pickup: adr.pickup, delivery_address: adr.delivery, propose_to_client: adr.propose })
    .then((r) => { setMsg({ ok: true, t: r.proposed ? "Адреса сохранены. Новый адрес доставки отправлен в карточку клиента на утверждение ЗГД." : "Адреса сохранены." }); reload(); })
    .catch((e) => setMsg({ ok: false, t: (e as Error).message }));
  return (
    <div className="card lw">
      {msg && <div className={"alert " + (msg.ok ? "ok" : "error")}>{msg.t}</div>}
      <h3 className="lw-h">Адреса</h3>
      <div className="lw-grid">
        {ws.addresses.pickup.map((p) => (
          <label key={p.factory_id} className="pf-field"><span className="pf-label">Отгрузка: {p.factory} {p.card && adr.pickup[p.factory_id] !== p.card ? "· изменён (в карточке завода — другой)" : "· из карточки завода"}</span>
            <input value={adr.pickup[p.factory_id] || ""} disabled={!edit} placeholder="адрес завода — нет в карточке" onChange={(e) => setAdr({ ...adr, pickup: { ...adr.pickup, [p.factory_id]: e.target.value } })} /></label>))}
        {ws.addresses.pickup.length === 0 && <div className="muted pf-field">Отгрузка: завод в позициях ещё не выбран.</div>}
        <label className="pf-field"><span className="pf-label">Доставка клиенту {ws.addresses.delivery_source ? `· ${ws.addresses.delivery_source}` : ""}</span>
          <input list="lw-client-adr" value={adr.delivery} disabled={!edit} placeholder="адрес доставки" onChange={(e) => setAdr({ ...adr, delivery: e.target.value })} />
          <datalist id="lw-client-adr">{ws.addresses.client_addresses.map((a) => <option key={a.id} value={a.address}>{a.status === "pending" ? "на утверждении" : "из карточки клиента"}</option>)}</datalist>
          {edit && isNew && <label className="muted lw-sub"><input type="checkbox" checked={adr.propose} onChange={(e) => setAdr({ ...adr, propose: e.target.checked })} /> добавить в карточку клиента как адрес доставки (после утверждения ЗГД)</label>}</label>
      </div>
      {edit && <div className="row" style={{ marginTop: 8 }}><button className="btn secondary" onClick={saveAdr}>Сохранить адреса</button></div>}
    </div>);
}

/** Запросы логистическим компаниям, их ответы и выбор лучшего предложения — внизу блока, после вводимых данных. */
export default function LogistRequests({ dealId, ws, reload, onApplyChosen }: { dealId: string; ws: Workspace; reload: () => void; onApplyChosen?: (lines: Quote[]) => void }) {
  const edit = ws.can_edit;
  const [msg, setMsg] = useState<{ ok: boolean; t: string } | null>(null);
  const [days, setDays] = useState(3);
  const [to, setTo] = useState<string[]>([]);
  const [extra, setExtra] = useState("");
  const [text, setText] = useState("");
  const [carrierForm, setCarrierForm] = useState<Carrier | null>(null);
  const emptyQ = () => ({ carrier: "", amount: "", currency: "RUB", transit_days: "", valid_until: "", scope: ws.services[0] || "intl_freight", comment: "" });
  const [qf, setQf] = useState(emptyQ);
  const [qfile, setQfile] = useState<File | null>(null);
  const [pick, setPick] = useState<{ carrier: string; comment: string; need_gd: boolean } | null>(null);
  const [gdc, setGdc] = useState("");
  const isGD = ws.can_choose_fields;
  const ok = (t: string) => setMsg({ ok: true, t }), bad = (e: unknown) => setMsg({ ok: false, t: (e as Error).message });
  const svc = new Set(ws.services);
  const toggleSvc = (code: string, on: boolean) => {
    const next = ws.service_options.map((o) => o.code).filter((c) => (c === code ? on : svc.has(c)));
    api.put(`/api/deals/${dealId}/logistics/services`, { services: next }).then(reload).catch(bad);
  };
  const recipients = [...to, ...extra.split(/[,;\s]+/).filter((x) => x.includes("@"))];
  const send = (manual: boolean) => api.post(`/api/deals/${dealId}/logistics/request/send`, { emails: recipients, message: text, answer_days: days, manual })
    .then(() => { ok(manual ? "Отправка отмечена в сделке." : `Запрос отправлен: ${recipients.join(", ")}`); setTo([]); setExtra(""); reload(); }).catch(bad);
  const saveCarrier = () => { if (!carrierForm) return; api.put("/api/refs/logistics-carriers", [...ws.carriers, carrierForm]).then(() => { setCarrierForm(null); reload(); }).catch(bad); };
  async function addQuote() {
    const fd = new FormData();
    Object.entries(qf).forEach(([k, v]) => { if (v !== "") fd.append(k, k === "amount" ? String(Number(String(v).replace(",", ".")) || 0) : v); });
    if (qfile) fd.append("file", qfile);
    try { await api.form(`/api/deals/${dealId}/logistics/quotes`, fd); setQf({ ...emptyQ(), carrier: qf.carrier }); setQfile(null); ok(`Ответ ${qf.carrier} добавлен. Добавьте остальные услуги этой компании или следующую компанию.`); reload(); }
    catch (e) { bad(e); }
  }
  const delQuote = (q: Quote) => { if (window.confirm(`Удалить ответ ${q.carrier}?`)) api.del(`/api/deals/${dealId}/logistics/quotes/${q.id}`).then(reload).catch(bad); };
  const choose = (carrier: string, comment = "", need_gd = false) => api.post(`/api/deals/${dealId}/logistics/quotes/choose`, { carrier, comment, need_gd })
    .then(() => { setPick(null); ok(!carrier ? "Выбор снят." : need_gd && !isGD ? `Выбор «${carrier}» отправлен на согласование ЗГД.` : `Выбрано предложение: ${carrier}.`); reload(); })
    .catch(bad);
  const decide = (approve: boolean) => api.post(`/api/deals/${dealId}/logistics/quotes/choose/decision`, { approve, comment: gdc })
    .then(() => { setGdc(""); ok(approve ? "Выбор согласован." : "Выбор отклонён — логист выберет другое предложение."); reload(); }).catch(bad);
  const cmp = ws.compare;
  const cols = ws.service_options.filter((o) => svc.has(o.code));
  const linesOf = (r: CmpRow) => ws.quotes.filter((q) => Object.values(r.lines).some((l) => l.id === q.id));

  return (
    <div className="card lw">
      {msg && <div className={"alert " + (msg.ok ? "ok" : "error")}>{msg.t}</div>}
      <h3 className="lw-h"><span className="pf-num">1</span> Какие услуги нужны от логистических компаний</h3>
      <div className="lw-svc">{ws.service_options.map((o) => (
        <label key={o.code} className={"lw-svc-i" + (svc.has(o.code) ? " on" : "")}><input type="checkbox" disabled={!edit} checked={svc.has(o.code)} onChange={(e) => toggleSvc(o.code, e.target.checked)} />{o.title}</label>))}</div>
      <p className="muted lw-sub">Обычно таможенное оформление в РФ и экспортное оформление делаем без логистической компании — они не отмечены. В запрос и в сравнение попадают только отмеченные услуги.</p>

      <h3 className="lw-h"><span className="pf-num">2</span> Запрос логистическим компаниям</h3>
      <div className="lw-req">
        <div className="lw-req-col">
          <div className="row" style={{ gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <a className="btn ghost" href={`/api/deals/${dealId}/logistics/request/file?fmt=xlsx&answer_days=${days}`}>⬇ Excel</a>
            <a className="btn ghost" href={`/api/deals/${dealId}/logistics/request/file?fmt=pdf&answer_days=${days}`}>⬇ PDF</a>
            <label className="muted">ответ в течение <input type="number" min={1} max={30} value={days} style={{ width: 56 }} onChange={(e) => setDays(Number(e.target.value) || 3)} /> дн.</label>
          </div>
          <p className="muted lw-sub">Одна форма для всех компаний: адреса, базис, готовность груза, груз по позициям и таблица для ответа по отмеченным услугам.</p>
          {ws.requests.length > 0 && <table className="lw-table"><thead><tr><th>Запрос</th><th>Когда</th><th>Кому</th><th>Ответ до</th></tr></thead>
            <tbody>{[...ws.requests].reverse().map((r) => <tr key={r.id}><td>{r.no}{r.files?.manual && <span className="muted"> · вручную</span>}</td><td>{fmtDate(r.at)}<div className="muted" style={{ fontSize: 11 }}>{r.by_name}</div></td>
              <td>{r.sent_to.join(", ")}</td><td>{fmtDate(r.answer_by)}</td></tr>)}</tbody></table>}
          {cmp.waiting.length > 0 && <p className="lw-sub"><span className="badge warn">ждём ответа</span> {cmp.waiting.join(", ")}</p>}
        </div>
        {edit && <div className="lw-req-col">
          <b>Кому — можно сразу нескольким компаниям</b>
          <div className="lw-carriers">{ws.carriers.map((c) => (
            <label key={c.email || c.name} className="lw-opt"><input type="checkbox" disabled={!c.email} checked={to.includes(c.email)} onChange={(e) => setTo(e.target.checked ? [...to, c.email] : to.filter((x) => x !== c.email))} />
              {c.name} <span className="muted">{c.email || "нет почты"}</span></label>))}
            {ws.carriers.length > 1 && <button className="btn ghost" style={{ alignSelf: "flex-start", padding: "1px 8px" }} onClick={() => setTo(ws.carriers.map((c) => c.email).filter(Boolean))}>выбрать все</button>}
            {ws.carriers.length === 0 && <span className="muted">Логистических компаний пока нет — добавьте.</span>}</div>
          {carrierForm ? (
            <div className="lw-inline"><input placeholder="компания" value={carrierForm.name} onChange={(e) => setCarrierForm({ ...carrierForm, name: e.target.value })} />
              <input placeholder="e-mail" value={carrierForm.email} onChange={(e) => setCarrierForm({ ...carrierForm, email: e.target.value })} />
              <button className="btn" onClick={saveCarrier}>Добавить</button><button className="btn ghost" onClick={() => setCarrierForm(null)}>×</button></div>
          ) : <button className="btn ghost" onClick={() => setCarrierForm({ name: "", email: "" })}>+ логистическая компания</button>}
          <input placeholder="ещё адреса через запятую" value={extra} onChange={(e) => setExtra(e.target.value)} style={{ width: "100%", marginTop: 6 }} />
          <textarea placeholder="текст письма (необязательно)" value={text} rows={2} onChange={(e) => setText(e.target.value)} style={{ width: "100%", marginTop: 6 }} />
          <div className="row" style={{ gap: 8, marginTop: 6, flexWrap: "wrap" }}>
            <button className="btn" disabled={!ws.mail_enabled || !recipients.length} title={ws.mail_enabled ? "" : "почта не подключена"} onClick={() => send(false)}>✉ Отправить из системы{recipients.length > 1 ? ` (${recipients.length})` : ""}</button>
            <button className="btn ghost" disabled={!recipients.length} onClick={() => send(true)}>Отмечу: отправлено вручную</button>
          </div>
          {!ws.mail_enabled && <p className="muted lw-sub">Почта пока не подключена: скачайте Excel или PDF, отправьте своей почтой и отметьте отправку.</p>}
        </div>}
      </div>

      <h3 className="lw-h"><span className="pf-num">3</span> Ответы и выбор лучшего предложения</h3>
      {cmp.rows.length > 0 ? (
        <div style={{ overflowX: "auto" }}><table className="lw-table lw-cmp">
          <thead><tr><th>Компания</th>{cols.map((c) => <th key={c.code} className="num">{SCOPES[c.code] || c.title}</th>)}<th className="num">Итого, ₽ без НДС</th><th className="num">Срок, дн.</th><th /></tr></thead>
          <tbody>{cmp.rows.map((r) => { const chosen = cmp.chosen && cmp.chosen.toLowerCase() === r.carrier.toLowerCase();
            return (
              <tr key={r.carrier} className={chosen ? "lw-chosen" : r.carrier === cmp.best ? "lw-best" : ""}>
                <td><b>{r.carrier}</b>
                  <div>{chosen && <span className={"badge " + (cmp.chosen_status === "pending_gd" ? "warn" : cmp.chosen_status === "declined" ? "bad" : "accent")}>{cmp.chosen_status === "pending_gd" ? "на согласовании ЗГД" : cmp.chosen_status === "declined" ? "ЗГД отклонил" : "выбрано"}</span>} {r.carrier === cmp.best && <span className="badge ok">дешевле всех</span>} {r.carrier === cmp.fastest && <span className="badge">быстрее всех</span>}</div>
                  <div className="muted" style={{ fontSize: 11 }}>{r.mode}{!r.complete && r.missing.length ? ` · нет: ${r.missing.join(", ")}` : ""}</div></td>
                {cols.map((c) => { const l = r.lines[c.code]; return <td key={c.code} className="num">{l ? <>{fmtRub(l.amount)} {l.currency}{l.currency !== "RUB" && <div className="muted" style={{ fontSize: 11 }}>≈ {fmtRub(l.rub)} ₽</div>}
                  {l.valid_until && <div className="muted" style={{ fontSize: 11 }}>до {fmtDate(l.valid_until)}</div>}</> : <span className="muted">—</span>}</td>; })}
                <td className="num"><b>{r.total_rub ? fmtRub(r.total_rub) : "—"}</b></td><td className="num">{r.transit_days ?? "—"}</td>
                <td style={{ whiteSpace: "nowrap" }}>{edit && !chosen && <button className="btn ghost" disabled={!r.complete} title={r.complete ? "" : "предложение неполное"} onClick={() => setPick({ carrier: r.carrier, comment: "", need_gd: false })}>выбрать</button>}
                  {edit && chosen && onApplyChosen && <button className="btn secondary" disabled={cmp.chosen_status !== "approved"} title={cmp.chosen_status === "approved" ? "" : "после согласования ЗГД"} onClick={() => onApplyChosen(linesOf(r))}>в партию</button>}
                  {edit && chosen && <button className="btn ghost" onClick={() => choose("")}>снять</button>}</td>
              </tr>); })}</tbody></table></div>
      ) : <p className="muted">Ответов пока нет. Внесите ответ каждой компании по каждой услуге — система сравнит итоги в рублях по курсу ЦБ.</p>}
      {pick && (() => { const notBest = !!cmp.best && pick.carrier !== cmp.best; return (
        <div className="lw-pick">
          <b>Выбор: {pick.carrier}</b>{notBest && <span className="badge warn" style={{ marginLeft: 8 }}>не самое дешёвое — лучшее {cmp.best}</span>}
          <input placeholder={notBest ? "почему это предложение (обязательно): надёжность, сроки, опыт…" : "комментарий (необязательно)"} value={pick.comment} onChange={(e) => setPick({ ...pick, comment: e.target.value })} />
          {!isGD && <label className="lw-opt"><input type="checkbox" checked={pick.need_gd} onChange={(e) => setPick({ ...pick, need_gd: e.target.checked })} /> нужно согласование ЗГД (опишите в комментарии, что согласовать)</label>}
          <div className="row" style={{ gap: 6 }}>
            <button className="btn" disabled={(notBest || pick.need_gd) && !pick.comment.trim()} onClick={() => choose(pick.carrier, pick.comment, pick.need_gd)}>{pick.need_gd && !isGD ? "Отправить на согласование ЗГД" : "Выбрать"}</button>
            <button className="btn ghost" onClick={() => setPick(null)}>Отмена</button></div>
        </div>); })()}
      {cmp.chosen && cmp.chosen_status === "pending_gd" && (
        <div className="alert warn">Выбор «{cmp.chosen}» ждёт согласования ЗГД.{cmp.chosen_comment && <> Логист: {cmp.chosen_comment}</>}
          {isGD && <div className="row" style={{ gap: 6, marginTop: 6 }}><input style={{ flex: 1 }} placeholder="комментарий ЗГД (обязателен при отказе)" value={gdc} onChange={(e) => setGdc(e.target.value)} />
            <button className="btn" onClick={() => decide(true)}>Согласовать</button><button className="btn ghost" disabled={!gdc.trim()} onClick={() => decide(false)}>Отклонить</button></div>}</div>)}
      {cmp.chosen && cmp.chosen_status === "declined" && <div className="alert error">ЗГД отклонил выбор «{cmp.chosen}»: {cmp.gd_comment}. Выберите другое предложение.</div>}
      {cmp.chosen && cmp.chosen_status === "approved" && cmp.gd_decision_at && <div className="alert ok">ЗГД согласовал выбор «{cmp.chosen}» {fmtDate(cmp.gd_decision_at)}{cmp.gd_comment ? `: ${cmp.gd_comment}` : ""}.</div>}

      {edit && (
        <div className="lw-quote-form">
          <input placeholder="компания" list="lw-carrier-names" value={qf.carrier} onChange={(e) => setQf({ ...qf, carrier: e.target.value })} />
          <datalist id="lw-carrier-names">{ws.carriers.map((c) => <option key={c.name} value={c.name} />)}</datalist>
          <select value={qf.scope} onChange={(e) => setQf({ ...qf, scope: e.target.value })}>{ws.service_options.map((o) => <option key={o.code} value={o.code}>{SCOPES[o.code] || o.title}{svc.has(o.code) ? "" : " (не запрашивали)"}</option>)}</select>
          <input placeholder="стоимость" inputMode="decimal" value={qf.amount} onChange={(e) => setQf({ ...qf, amount: e.target.value })} />
          <select value={qf.currency} onChange={(e) => setQf({ ...qf, currency: e.target.value })}>{CURS.map((c) => <option key={c}>{c}</option>)}</select>
          <input placeholder="срок, дн." type="number" value={qf.transit_days} onChange={(e) => setQf({ ...qf, transit_days: e.target.value })} />
          <input type="date" title="действует до" value={qf.valid_until} onChange={(e) => setQf({ ...qf, valid_until: e.target.value })} />
          <input placeholder="условия / что не входит" value={qf.comment} onChange={(e) => setQf({ ...qf, comment: e.target.value })} />
          <label className="btn ghost lw-file">{qfile ? qfile.name.slice(0, 18) : "📎 файл ответа"}<input type="file" hidden onChange={(e) => setQfile(e.target.files?.[0] || null)} /></label>
          <button className="btn secondary" disabled={!qf.carrier.trim()} onClick={addQuote}>Добавить ответ</button>
        </div>)}
      {ws.quotes.length > 0 && <details style={{ marginTop: 8 }}><summary className="muted" style={{ cursor: "pointer" }}>Все ответы ({ws.quotes.length}) — файлы, условия, удаление</summary>
        <table className="lw-table"><thead><tr><th>Компания</th><th>Услуга</th><th className="num">Стоимость</th><th className="num">Срок</th><th>Условия</th><th>Файл</th><th>Внесён</th><th /></tr></thead>
          <tbody>{ws.quotes.map((q) => (
            <tr key={q.id}><td>{q.carrier}</td><td>{SCOPES[q.scope] || q.scope}</td><td className="num">{fmtRub(q.amount)} {q.currency}</td><td className="num">{q.transit_days ?? "—"}</td>
              <td className="muted" style={{ fontSize: 12 }}>{q.comment}</td><td>{q.storage_path ? <a href={`/api/files/${q.storage_path}`}>{q.file_name}</a> : "—"}</td>
              <td className="muted" style={{ fontSize: 11 }}>{fmtDate(q.at)} · {q.by_name}</td><td>{edit && <button className="btn ghost" onClick={() => delQuote(q)}>×</button>}</td></tr>))}</tbody></table></details>}
    </div>);
}
