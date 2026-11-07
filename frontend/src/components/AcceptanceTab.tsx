import { useEffect, useState, type ChangeEvent } from "react";
import { api, fmtDate } from "../api";

// Приёмка машин на заводе: по каждой машине — номера, чек-лист, испытания, фото и видео, замечания, решение и акт.

type Prev = { prev_fail?: number[]; prev?: { round_no: number; result: string; comment: string } | null };
type Check = { id?: string; group: string; item: string; expected: string; result: string; comment: string } & Prev;
type Test = { id?: string; name: string; result: string; value: string; note: string } & Prev;
type FileRow = { i: number; name: string; kind: string; kind_title: string; title: string; item_id: string | null; at: string; round_no: number };
type Round = { round_no: number; mode: string; mode_title: string; is_final: boolean; inspected_on: string | null; inspector: string; decision: string; decision_title: string;
  decided_at: string | null; decided_by_name: string; comment: string; fails: string[]; open_remarks: number; act_number: string;
  checklist: Check[]; tests: Test[]; remarks: Remark[] };
type Remark = { id?: string; text: string; responsible: string; due: string; fixed_on: string | null; fixed_note: string; from_round?: number };
type Unit = { id: string; position_id: string; unit_no: number; designation: string; serial: string; engine_no: string; frame_no: string;
  components: { name: string; serial: string }[]; checklist: Check[]; tests: Test[]; remarks: Remark[]; files: FileRow[];
  mode: string | null; mode_title: string; is_final: boolean | null; round_no: number | null; history: Round[]; prev_fail_count: number;
  inspector: string; factory_rep: string; place: string; inspected_on: string | null; decision: string; decision_title: string; decided_at: string | null; decided_by_name: string;
  act: { number?: string; date?: string; signed_factory_on?: string; signed_ours_on?: string; file?: { name: string } }; comment: string; open_remarks: number;
  checked: number; to_check: number; machine_id: string | null };
type Data = { units: Unit[]; machines_total: number; can_edit: boolean; decisions: Record<string, string>; results: Record<string, string>;
  file_kinds: Record<string, string>; modes: Record<string, string>; to_model_kinds: string[] };
const MEDIA = ["photo", "video"];

/** Две кнопки: «Сфотографировать» (камера телефона) и «Из файла» (несколько файлов). */
function PhotoButtons({ onFiles, label = "фото" }: { onFiles: (fs: File[]) => void; label?: string }) {
  const pick = (e: ChangeEvent<HTMLInputElement>) => { const fs = Array.from(e.target.files || []); e.target.value = ""; if (fs.length) onFiles(fs); };
  return (<>
    <label className="btn ghost" style={{ cursor: "pointer", padding: "2px 8px" }} title="открыть камеру телефона">📷 Сфотографировать
      <input type="file" accept="image/*" capture="environment" style={{ display: "none" }} onChange={pick} /></label>
    <label className="btn ghost" style={{ cursor: "pointer", padding: "2px 8px" }}>📁 {label} из файла
      <input type="file" accept="image/*,video/*" multiple style={{ display: "none" }} onChange={pick} /></label>
  </>);
}

const DEC_BADGE: Record<string, string> = { accepted: "ok", accepted_remarks: "warn", rejected: "bad" };

export default function AcceptanceTab({ dealId }: { dealId: string }) {
  const base = `/api/deals/${dealId}/acceptance`;
  const [data, setData] = useState<Data | null>(null);
  const [cur, setCur] = useState<Unit | null>(null);
  const [msg, setMsg] = useState("");
  const load = () => api.get<Data>(base).then((d) => { setData(d); if (cur) setCur(d.units.find((u) => u.id === cur.id) || null); }).catch((e) => setMsg((e as Error).message));
  useEffect(() => { load(); }, [dealId]);
  if (!data) return <div className="card muted">{msg || "Загрузка…"}</div>;
  const accepted = data.units.filter((u) => ["accepted", "accepted_remarks"].includes(u.decision)).length;
  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 style={{ margin: 0 }}>Приёмка машин (онлайн и на заводе)</h2>
        <span className="muted">принято {accepted} из {data.machines_total} машин</span>
      </div>
      <p className="muted" style={{ marginTop: 4 }}>Приёмка — онлайн (по видео) или на заводе; приёмок может быть несколько: «Новая приёмка» сохраняет предыдущую в истории целиком, а пункты, не принятые ранее, выделяются. Фото при осмотре — с камеры телефона или из файла, в том числе к конкретному пункту чек-листа; паспорт, руководство, сертификаты и протоколы прикладываются здесь же и видны в карточке машины в парке. По каждой машине: заводской номер и номера агрегатов, чек-лист (по модели и сделке: технические характеристики, ЗИП, документы), испытания с фото и видео, замечания с ответственным и сроком, решение и акт. Принятая машина попадает в парк клиента для сервиса. Окончательный расчёт с заводом по позиции — только после приёмки всех её машин.</p>
      {msg && <div className="alert info">{msg}</div>}
      {data.units.length < data.machines_total && data.can_edit && (
        <button className="btn" onClick={() => api.post<Data & { created: number }>(`${base}/init`).then((d) => { setData(d); setMsg(`Созданы карточки приёмки: ${d.created}`); }).catch((e) => setMsg((e as Error).message))}>
          {data.units.length ? "Добавить карточки для новых машин" : "Начать приёмку — карточки на каждую машину"}</button>)}
      {data.units.length > 0 && (
        <table style={{ marginTop: 8 }}>
          <thead><tr><th>Машина</th><th>№</th><th>Приёмка</th><th>Заводской номер</th><th className="num">Проверено</th><th className="num">Замечаний</th><th>Решение</th><th>Акт</th></tr></thead>
          <tbody>{data.units.map((u) => (
            <tr key={u.id} style={{ cursor: "pointer", background: cur?.id === u.id ? "var(--accent-soft)" : undefined }} onClick={() => setCur(u)}>
              <td>{u.designation}</td><td>{u.unit_no}</td>
              <td style={{ fontSize: 12 }}>№ {u.round_no || 1} · {u.mode_title}{u.mode === "online" && !u.is_final ? " (предв.)" : ""}{u.history.length > 0 && <span className="muted"> · ранее {u.history.length}</span>}</td><td>{u.serial || <span className="muted">—</span>}</td><td className="num">{u.checked} / {u.to_check}</td>
              <td className="num">{u.open_remarks || ""}</td><td>{u.decision ? <span className={`badge ${DEC_BADGE[u.decision]}`}>{u.decision_title}</span> : <span className="muted">не принята</span>}</td>
              <td>{u.act?.number ? `№ ${u.act.number}` : ""}{u.act?.file ? " · скан" : ""}</td></tr>))}</tbody>
        </table>)}
      {cur && <UnitCard key={cur.id} base={base} u={cur} data={data} onSaved={(m) => { setMsg(m); load(); }} />}
    </div>
  );
}

function UnitCard({ base, u, data, onSaved }: { base: string; u: Unit; data: Data; onSaved: (m: string) => void }) {
  const [f, setF] = useState(u);
  const [dirty, setDirty] = useState(false);
  const [err, setErr] = useState("");
  const [dec, setDec] = useState({ decision: u.decision || "accepted", comment: u.comment || "" });
  const [act, setAct] = useState({ number: u.act?.number || "", on: u.act?.date || "", signed_factory_on: u.act?.signed_factory_on || "", signed_ours_on: u.act?.signed_ours_on || "" });
  const edit = data.can_edit;
  const url = `${base}/${u.id}`;
  const set = (p: Partial<Unit>) => { setF({ ...f, ...p }); setDirty(true); };
  const run = async (fn: () => Promise<unknown>, ok: string) => { try { await fn(); setErr(""); setDirty(false); onSaved(ok); } catch (e) { setErr((e as Error).message); } };
  const save = () => run(() => api.put(url, { serial: f.serial, engine_no: f.engine_no, frame_no: f.frame_no, components: f.components, checklist: f.checklist, tests: f.tests,
    remarks: f.remarks, inspector: f.inspector, factory_rep: f.factory_rep, place: f.place, inspected_on: f.inspected_on || null }), "Приёмка сохранена");
  const groups = [...new Set(f.checklist.map((x) => x.group))];
  const [onlyPrev, setOnlyPrev] = useState(false);
  const [doc, setDoc] = useState({ kind: "passport", title: "", to_model: false, language: "ru" });
  const [showHist, setShowHist] = useState<number | null>(null);
  const upload = async (fs: File[], extra: Record<string, string>, ok: string) => {
    for (const x of fs) { try { await api.upload(`${url}/files`, x, extra); } catch (er) { setErr((er as Error).message); return; } }
    onSaved(`${ok}: ${fs.length}`);
  };
  const uploadMedia = (fs: File[], item_id = "") => {
    (async () => { for (const x of fs) { try { await api.upload(`${url}/files`, x, { kind: x.type.startsWith("video") ? "video" : "photo", item_id }); } catch (er) { setErr((er as Error).message); return; } }
      onSaved(`Загружено фото / видео: ${fs.length}`); })();
  };
  const itemFiles = (id?: string) => (id ? u.files.filter((x) => x.item_id === id) : []);
  const prevBadge = (x: Prev) => (x.prev_fail && x.prev_fail.length > 0
    ? <span className="badge bad" style={{ marginLeft: 4, fontSize: 10 }} title={x.prev?.comment || ""}>не принято в приёмке № {x.prev_fail.join(", ")}</span>
    : x.prev?.result ? <span className="muted" style={{ fontSize: 10, marginLeft: 4 }}>ранее: {data.results[x.prev.result] || x.prev.result}</span> : null);
  const prevStyle = (x: Prev) => (x.prev_fail && x.prev_fail.length > 0 ? { background: "#f4f4f4", boxShadow: "inset 3px 0 0 #ff3131" } : undefined);
  const newRound = (mode: string) => {
    if (!confirm(`Начать новую приёмку (${data.modes[mode]})? Текущая приёмка № ${u.round_no || 1} сохранится в истории; результаты проверок — заново, не принятые ранее пункты будут выделены.`)) return;
    run(() => api.post(`${url}/rounds`, { mode, is_final: mode === "factory" }), `Начата приёмка № ${(u.round_no || 1) + 1} — ${data.modes[mode]}`);
  };
  const res = (v: string, on: (x: string) => void) => (
    <select value={v} disabled={!edit} onChange={(e) => on(e.target.value)} style={{ background: v === "fail" ? "#ffe3e3" : v === "ok" ? "#ededed" : undefined }}>
      <option value="">—</option>{Object.entries(data.results).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select>);
  return (
    <div style={{ borderTop: "2px solid var(--line)", marginTop: 12, paddingTop: 8 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h3 style={{ margin: 0 }}>{u.designation} — машина {u.unit_no}{u.decision && <span className={`badge ${DEC_BADGE[u.decision]}`} style={{ marginLeft: 6 }}>{u.decision_title}{u.decided_at ? `, ${fmtDate(u.decided_at)}` : ""}</span>}</h3>
        {edit && <button className="btn" disabled={!dirty} onClick={save}>Сохранить</button>}
      </div>
      {err && <div className="alert error">{err}</div>}
      <div className="row" style={{ flexWrap: "wrap", gap: 8, marginTop: 6, padding: "6px 8px", background: "#f6f8fa", borderRadius: 6 }}>
        <b>Приёмка № {u.round_no || 1}:</b>
        {(["online", "factory"] as const).map((m) => (
          <label key={m} className="row" style={{ gap: 4 }}><input type="radio" name={`mode-${u.id}`} checked={(u.mode || "factory") === m} disabled={!edit || !!u.decision || dirty}
            onChange={() => run(() => api.put(url, { mode: m }), `Вид приёмки: ${data.modes[m]}`)} />{m === "online" ? "Онлайн-приёмка" : "Приёмка на заводе"}</label>))}
        {(u.mode || "factory") === "online" && <label className="row" style={{ gap: 4 }} title="онлайн-приёмка засчитывается как окончательная (окончательный расчёт с заводом)">
          <input type="checkbox" checked={!!u.is_final} disabled={!edit || !!u.decision || dirty} onChange={(e) => run(() => api.put(url, { is_final: e.target.checked }), e.target.checked ? "Онлайн-приёмка — окончательная" : "Онлайн-приёмка — предварительная")} />
          окончательная</label>}
        {u.prev_fail_count > 0 && <span className="badge bad">ранее не принято пунктов: {u.prev_fail_count}</span>}
        {edit && u.decision && <span className="row" style={{ gap: 4, marginLeft: "auto" }}>Новая приёмка:
          <button className="btn secondary" style={{ padding: "2px 8px" }} onClick={() => newRound("online")}>онлайн</button>
          <button className="btn secondary" style={{ padding: "2px 8px" }} onClick={() => newRound("factory")}>на заводе</button></span>}
      </div>
      <div className="grid cols-3" style={{ marginTop: 6 }}>
        <label className="f">Заводской номер машины *<input disabled={!edit} value={f.serial} onChange={(e) => set({ serial: e.target.value })} /></label>
        <label className="f">Номер двигателя<input disabled={!edit} value={f.engine_no} onChange={(e) => set({ engine_no: e.target.value })} /></label>
        <label className="f">Номер рамы<input disabled={!edit} value={f.frame_no} onChange={(e) => set({ frame_no: e.target.value })} /></label>
        <label className="f">Дата приёмки<input type="date" disabled={!edit} value={f.inspected_on || ""} onChange={(e) => set({ inspected_on: e.target.value })} /></label>
        <label className="f">Принимал (от нас)<input disabled={!edit} value={f.inspector} onChange={(e) => set({ inspector: e.target.value })} /></label>
        <label className="f">Представитель завода<input disabled={!edit} value={f.factory_rep} onChange={(e) => set({ factory_rep: e.target.value })} /></label>
        <label className="f">Место<input disabled={!edit} value={f.place} onChange={(e) => set({ place: e.target.value })} placeholder="площадка завода" /></label>
      </div>
      <h4>Основные агрегаты</h4>
      {f.components.map((c, i) => (
        <div key={i} className="row"><input disabled={!edit} style={{ width: 180 }} value={c.name} onChange={(e) => set({ components: f.components.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)) })} />
          <input disabled={!edit} placeholder="серийный номер" style={{ width: 200 }} value={c.serial} onChange={(e) => set({ components: f.components.map((x, j) => (j === i ? { ...x, serial: e.target.value } : x)) })} />
          {edit && <button className="btn ghost" onClick={() => set({ components: f.components.filter((_, j) => j !== i) })}>×</button>}</div>))}
      {edit && <button className="btn ghost" onClick={() => set({ components: [...f.components, { name: "", serial: "" }] })}>+ агрегат</button>}

      <h4>Чек-лист</h4>
      <div className="row" style={{ marginBottom: 4, flexWrap: "wrap" }}>
        {edit && <button className="btn ghost" onClick={() => set({ checklist: f.checklist.map((x) => (x.result ? x : { ...x, result: "ok" })) })}>отметить остальные «соответствует»</button>}
        {u.prev_fail_count > 0 && <label className="row" style={{ gap: 4 }}><input type="checkbox" checked={onlyPrev} onChange={(e) => setOnlyPrev(e.target.checked)} />только не принятые ранее</label>}</div>
      <table style={{ fontSize: 12 }}><tbody>{groups.map((g) => [
        <tr key={"g" + g} style={{ background: "#f1f1f1" }}><td colSpan={4}><b>{g}</b></td></tr>,
        ...f.checklist.map((x, i) => x.group !== g || (onlyPrev && !(x.prev_fail || []).length) ? null : (
          <tr key={x.id || i} style={prevStyle(x)}><td>{x.item}{prevBadge(x)}</td><td className="muted">{x.expected}</td><td>{res(x.result, (v) => set({ checklist: f.checklist.map((y, j) => (j === i ? { ...y, result: v } : y)) }))}</td>
            <td><input disabled={!edit} placeholder="комментарий" style={{ width: 200 }} value={x.comment} onChange={(e) => set({ checklist: f.checklist.map((y, j) => (j === i ? { ...y, comment: e.target.value } : y)) })} /></td>
            <td style={{ whiteSpace: "nowrap" }}>{itemFiles(x.id).map((z) => <a key={z.i} href={`${url}/files/${z.i}`} target="_blank" rel="noreferrer" title={z.name}>{z.kind === "video" ? "🎬" : "📷"}</a>)}
              {edit && x.id && <><label style={{ cursor: "pointer", marginLeft: 4 }} title="сфотографировать к пункту">📷<input type="file" accept="image/*" capture="environment" style={{ display: "none" }}
                onChange={(e) => { const fs = Array.from(e.target.files || []); e.target.value = ""; if (fs.length) uploadMedia(fs, x.id!); }} /></label>
                <label style={{ cursor: "pointer", marginLeft: 4 }} title="фото / видео к пункту из файла">📁<input type="file" accept="image/*,video/*" multiple style={{ display: "none" }}
                onChange={(e) => { const fs = Array.from(e.target.files || []); e.target.value = ""; if (fs.length) uploadMedia(fs, x.id!); }} /></label></>}</td></tr>)),
      ])}</tbody></table>
      {edit && <button className="btn ghost" onClick={() => set({ checklist: [...f.checklist, { group: "Дополнительно", item: prompt("Пункт проверки") || "", expected: "", result: "", comment: "" }].filter((x) => x.item) })}>+ пункт</button>}

      <h4>Испытания</h4>
      <table style={{ fontSize: 12 }}><tbody>{f.tests.map((x, i) => (onlyPrev && !(x.prev_fail || []).length) ? null : (
        <tr key={x.id || i} style={prevStyle(x)}><td>{x.name}{prevBadge(x)}</td>
          <td><input disabled={!edit} placeholder="показатель (давление, скорость…)" style={{ width: 200 }} value={x.value} onChange={(e) => set({ tests: f.tests.map((y, j) => (j === i ? { ...y, value: e.target.value } : y)) })} /></td>
          <td>{res(x.result, (v) => set({ tests: f.tests.map((y, j) => (j === i ? { ...y, result: v } : y)) }))}</td>
          <td><input disabled={!edit} placeholder="примечание" style={{ width: 180 }} value={x.note} onChange={(e) => set({ tests: f.tests.map((y, j) => (j === i ? { ...y, note: e.target.value } : y)) })} /></td></tr>))}</tbody></table>
      {edit && <button className="btn ghost" onClick={() => { const n = prompt("Испытание"); if (n) set({ tests: [...f.tests, { name: n, result: "", value: "", note: "" }] }); }}>+ испытание</button>}

      <h4>Фото и видео осмотра</h4>
      <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
        {u.files.filter((x) => MEDIA.includes(x.kind)).map((x) => <a key={x.i} href={`${url}/files/${x.i}`} target="_blank" rel="noreferrer" title={`приёмка № ${x.round_no}`}>
          {x.kind === "video" ? "🎬" : "📷"} {x.name}{x.item_id ? " · к пункту" : ""}{x.round_no !== (u.round_no || 1) ? ` (№ ${x.round_no})` : ""}</a>)}
        {!u.files.some((x) => MEDIA.includes(x.kind)) && <span className="muted">нет</span>}
        {edit && <PhotoButtons label="фото / видео" onFiles={(fs) => uploadMedia(fs)} />}
      </div>

      <h4>Документы машины</h4>
      <div className="muted" style={{ fontSize: 12 }}>Паспорт, руководство, сертификаты, протоколы, гарантийный талон — видны в карточке машины в парке клиента.</div>
      {u.files.filter((x) => !MEDIA.includes(x.kind)).map((x) => (
        <div key={x.i} style={{ fontSize: 13 }}>📄 <b>{x.kind_title}</b>{x.title && ` — ${x.title}`}: <a href={`${url}/files/${x.i}`} target="_blank" rel="noreferrer">{x.name}</a>
          <span className="muted" style={{ fontSize: 11 }}> · {fmtDate(x.at)} · приёмка № {x.round_no}</span></div>))}
      {edit && <div className="row" style={{ flexWrap: "wrap", marginTop: 4 }}>
        <select value={doc.kind} onChange={(e) => setDoc({ ...doc, kind: e.target.value, to_model: data.to_model_kinds.includes(e.target.value) ? doc.to_model : false })}>
          {Object.entries(data.file_kinds).filter(([k]) => !MEDIA.includes(k)).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select>
        <input placeholder="название / номер (необязательно)" style={{ width: 200 }} value={doc.title} onChange={(e) => setDoc({ ...doc, title: e.target.value })} />
        {data.to_model_kinds.includes(doc.kind) && <label className="row" style={{ gap: 4 }} title="документ появится у модели в каталоге и во всех сделках с ней">
          <input type="checkbox" checked={doc.to_model} onChange={(e) => setDoc({ ...doc, to_model: e.target.checked })} />общий для модели</label>}
        <label className="btn ghost" style={{ cursor: "pointer", padding: "2px 8px" }}>📁 Приложить файл<input type="file" multiple style={{ display: "none" }}
          onChange={(e) => { const fs = Array.from(e.target.files || []); e.target.value = ""; if (fs.length) upload(fs, { kind: doc.kind, title: doc.title, to_model: String(doc.to_model), language: doc.language }, "Документов загружено").then(() => setDoc({ ...doc, title: "" })); }} /></label>
        <label className="btn ghost" style={{ cursor: "pointer", padding: "2px 8px" }} title="сфотографировать документ телефоном">📷 Сфотографировать<input type="file" accept="image/*" capture="environment" style={{ display: "none" }}
          onChange={(e) => { const fs = Array.from(e.target.files || []); e.target.value = ""; if (fs.length) upload(fs, { kind: doc.kind, title: doc.title }, "Документов загружено"); }} /></label>
      </div>}

      <h4>Замечания</h4>
      {f.remarks.map((r, i) => (
        <div key={r.id || i} className="row" style={{ flexWrap: "wrap", opacity: r.fixed_on ? 0.6 : 1 }}>
          <input disabled={!edit} style={{ width: 260 }} placeholder="замечание" value={r.text} onChange={(e) => set({ remarks: f.remarks.map((x, j) => (j === i ? { ...x, text: e.target.value } : x)) })} />
          <input disabled={!edit} style={{ width: 140 }} placeholder="ответственный" value={r.responsible} onChange={(e) => set({ remarks: f.remarks.map((x, j) => (j === i ? { ...x, responsible: e.target.value } : x)) })} />
          <label className="row">срок<input type="date" disabled={!edit} value={r.due || ""} onChange={(e) => set({ remarks: f.remarks.map((x, j) => (j === i ? { ...x, due: e.target.value } : x)) })} /></label>
          <label className="row">устранено<input type="date" disabled={!edit} value={r.fixed_on || ""} onChange={(e) => set({ remarks: f.remarks.map((x, j) => (j === i ? { ...x, fixed_on: e.target.value || null } : x)) })} /></label>
          <input disabled={!edit} style={{ width: 160 }} placeholder="как устранено" value={r.fixed_note} onChange={(e) => set({ remarks: f.remarks.map((x, j) => (j === i ? { ...x, fixed_note: e.target.value } : x)) })} />
        </div>))}
      {edit && <button className="btn ghost" onClick={() => set({ remarks: [...f.remarks, { text: "", responsible: "завод", due: "", fixed_on: null, fixed_note: "" }] })}>+ замечание</button>}

      {edit && (
        <div className="card" style={{ marginTop: 10, background: "#fafbfc" }}>
          <b>Решение по приёмке</b>{dirty && <span className="muted"> — сначала сохраните изменения</span>}
          <div className="row" style={{ flexWrap: "wrap", marginTop: 4 }}>
            <select value={dec.decision} onChange={(e) => setDec({ ...dec, decision: e.target.value })}>{Object.entries(data.decisions).map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select>
            <input style={{ minWidth: 280 }} placeholder="комментарий (для «не принята» — причина)" value={dec.comment} onChange={(e) => setDec({ ...dec, comment: e.target.value })} />
            <button className="btn" disabled={dirty} onClick={() => run(() => api.post(`${url}/decision`, dec), `Решение: ${data.decisions[dec.decision]}`)}>Принять решение</button>
          </div>
        </div>)}

      <h4>Акт приёмки</h4>
      <div className="row" style={{ flexWrap: "wrap" }}>
        <input disabled={!edit} placeholder="№ акта" style={{ width: 100 }} value={act.number} onChange={(e) => setAct({ ...act, number: e.target.value })} />
        <label className="row">дата<input type="date" disabled={!edit} value={act.on} onChange={(e) => setAct({ ...act, on: e.target.value })} /></label>
        <label className="row">подписан заводом<input type="date" disabled={!edit} value={act.signed_factory_on} onChange={(e) => setAct({ ...act, signed_factory_on: e.target.value })} /></label>
        <label className="row">подписан нами<input type="date" disabled={!edit} value={act.signed_ours_on} onChange={(e) => setAct({ ...act, signed_ours_on: e.target.value })} /></label>
        {edit && <button className="btn ghost" onClick={() => run(() => api.put(`${url}/act`, { number: act.number, on: act.on || null, signed_factory_on: act.signed_factory_on || null, signed_ours_on: act.signed_ours_on || null }), "Акт сохранён")}>сохранить</button>}
        <a className="btn ghost" href={`${url}/act.docx`}>Акт (Word)</a>
        {u.act?.file ? <a href={`${url}/act/file`}>скан: {u.act.file.name}</a> : <span className="muted">скан не загружен</span>}
        {edit && <label className="btn ghost" style={{ cursor: "pointer" }}>загрузить подписанный<input type="file" accept=".pdf,image/*" style={{ display: "none" }}
          onChange={(e) => { const x = e.target.files?.[0]; if (x) run(() => api.upload(`${url}/act/file`, x), "Скан акта загружен"); e.target.value = ""; }} /></label>}
      </div>
      {u.history.length > 0 && <>
        <h4>История приёмок</h4>
        <table style={{ fontSize: 12 }}><thead><tr><th>№</th><th>Вид</th><th>Дата</th><th>Принимал</th><th>Решение</th><th>Не принято</th><th>Замечаний открыто</th><th>Акт</th><th /></tr></thead>
          <tbody>{u.history.map((h) => [
            <tr key={h.round_no}><td>{h.round_no}</td><td>{h.mode_title}{h.mode === "online" && !h.is_final ? " (предв.)" : ""}</td><td>{fmtDate(h.inspected_on || h.decided_at)}</td>
              <td>{h.inspector}</td><td>{h.decision ? <span className={`badge ${DEC_BADGE[h.decision] || ""}`}>{h.decision_title}</span> : "без решения"}{h.comment && <div className="muted">{h.comment}</div>}</td>
              <td>{h.fails.length ? <span style={{ color: "#ff3131" }}>{h.fails.length}</span> : "—"}</td><td>{h.open_remarks || "—"}</td><td>{h.act_number && `№ ${h.act_number}`}</td>
              <td><a href="#" onClick={(e) => { e.preventDefault(); setShowHist(showHist === h.round_no ? null : h.round_no); }}>{showHist === h.round_no ? "свернуть" : "подробно"}</a></td></tr>,
            showHist === h.round_no && <tr key={"d" + h.round_no}><td colSpan={9} style={{ background: "#fafafa" }}>
              {h.fails.length > 0 && <div><b>Не соответствует:</b> {h.fails.join("; ")}</div>}
              {(h.remarks || []).filter((r) => r.text).length > 0 && <div><b>Замечания:</b> {(h.remarks || []).filter((r) => r.text).map((r) => `${r.text}${r.due ? ` (срок ${fmtDate(r.due)})` : ""}${r.fixed_on ? ` — устранено ${fmtDate(r.fixed_on)}` : ""}`).join("; ")}</div>}
              <div className="muted">Проверено пунктов: {(h.checklist || []).filter((x) => x.result).length} из {(h.checklist || []).length}, испытаний: {(h.tests || []).filter((x) => x.result).length} из {(h.tests || []).length}
                {h.decided_by_name && ` · решение: ${h.decided_by_name}, ${fmtDate(h.decided_at)}`}</div>
            </td></tr>])}</tbody></table></>}
      {u.machine_id && <p className="muted" style={{ marginTop: 6 }}>Машина в парке клиента (вкладка «Запасные части» → «Парк машин») — дальше эксплуатация и сервис.</p>}
    </div>
  );
}
