import { useEffect, useState } from "react";
import { api, fmtDate, fmtRub } from "../api";
import { COST_ITEMS, type Me } from "../types";
import { AUDIT_ACTION, AUDIT_ENTITY, INTEGRATION, OPERATION, STATUS, lbl } from "../labels";

type U = { id: string; email: string; display_name: string; roles: string[]; is_active: boolean; visa_limit_rub: number | null; last_seen_at: string | null };
type Role = { code: string; title: string };
type Hs = { id: string; code: string; description: string; duty_pct: number; vat_pct: number; util_fee: boolean; licence_required: boolean; certification: string[]; valid_from: string; version_file: string };
type Fx = { id: string; on: string; currency: string; rate: number; source: string; comment: string };
type FxSum = { currency: string; source: string; count: number; first: string; last: string };
type Tpl = { id: string; kind: string; version: number; is_active: boolean; file_name: string; file_exists: boolean; uploaded_at?: string; created_at?: string };
type Audit = { id: number; at: string; user_name: string; deal_number: string | null; entity: string; entity_id: string; action: string; before: unknown; after: unknown; comment: string };
type Integ = { id: string; at: string; system: string; operation: string; ok: boolean; details: unknown };

const SOURCE: Record<string, string> = { cbr: "ЦБ РФ", manual: "вручную" };
/** Значения журнала по-русски: статус сделки — названием, а не кодом. */
const human = (v: unknown): unknown => (v && typeof v === "object" && !Array.isArray(v) && "status" in (v as Record<string, unknown>)
  ? { ...(v as Record<string, unknown>), status: lbl(STATUS, String((v as Record<string, unknown>).status)) } : v);
const short = (v: unknown) => { const s = v == null ? "" : JSON.stringify(human(v)); return s.length > 160 ? s.slice(0, 160) + "…" : s; };

/** Администрирование: сотрудники и роли, справочник ТН ВЭД, курсы, шаблоны, журнал действий и интеграций, отчёт точности оценок. */
export default function AdminPage({ me, embedded = false }: { me: Me; embedded?: boolean }) {
  const has = (...r: string[]) => me.roles.some((x) => r.includes(x.code));
  const isAdmin = has("admin", "gd");
  const tabs: [string, string, boolean][] = [
    ["users", "Сотрудники и роли", isAdmin], ["fx", "Курсы валют", has("admin", "gd", "fd")],
    ["templates", "Шаблоны", isAdmin], ["audit", "Журнал действий", isAdmin], ["integrations", "Журнал интеграций", isAdmin],
    ["accuracy", "Точность оценок", has("fd", "gd")], ["overdue", "Просрочки сотрудников", has("gd")],
  ];
  const visible = tabs.filter((t) => t[2]);
  const [tab, setTab] = useState(visible[0]?.[0] || "");
  if (!visible.length) return <div className="alert info">Раздел доступен администратору, ЗГД и ФД.</div>;
  return (
    <>
      {!embedded && <h1>Администрирование</h1>}
      <div className="tabs">{visible.map(([k, t]) => <button key={k} className={tab === k ? "active" : ""} onClick={() => setTab(k)}>{t}</button>)}</div>
      {tab === "users" && <Users me={me} />}
      {tab === "fx" && <FxRates canManual={isAdmin} />}
      {tab === "templates" && <Templates />}
      {tab === "audit" && <AuditLog />}
      {tab === "integrations" && <Integrations />}
      {tab === "accuracy" && <Accuracy />}
      {tab === "overdue" && <Overdue />}
    </>
  );
}

function Users({ me }: { me: Me }) {
  const [rows, setRows] = useState<U[]>([]);
  const [roles, setRoles] = useState<Role[]>([]);
  const [msg, setMsg] = useState("");
  const [q, setQ] = useState("");
  const [nu, setNu] = useState({ email: "", display_name: "", roles: [] as string[] });
  const load = () => api.get<U[]>("/api/admin/users").then(setRows).catch((e) => setMsg(e.message));
  useEffect(() => { load(); api.get<Role[]>("/api/auth/roles").then(setRoles); }, []);
  const patch = (u: U, body: Partial<U>) => api.patch(`/api/admin/users/${u.id}`, body).then(() => { setMsg(`Сохранено: ${u.display_name}`); load(); }).catch((e) => setMsg(e.message));
  const toggle = (list: string[], r: string) => (list.includes(r) ? list.filter((x) => x !== r) : [...list, r]);
  async function create() {
    try { await api.post("/api/admin/users", { ...nu, notify_channels: ["app"] }); setNu({ email: "", display_name: "", roles: [] }); setMsg("Сотрудник добавлен"); load(); }
    catch (e) { setMsg((e as Error).message); }
  }
  const shown = rows.filter((u) => !q || `${u.display_name} ${u.email}`.toLowerCase().includes(q.toLowerCase()));
  return (
    <div className="card">
      <p className="muted">Роли определяют права (матрица — на экране «Уровни доступа»). У сотрудника может быть несколько ролей. При входе через Microsoft 365 роли назначаются по группам (OIDC_GROUP_ROLE_MAP), а здесь их можно уточнить. Отключённый сотрудник не может войти; его история сохраняется.</p>
      {msg && <div className="alert info">{msg}</div>}
      <input placeholder="поиск: имя, e-mail" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 260, marginBottom: 8 }} />
      <table>
        <thead><tr><th>Сотрудник</th>{roles.map((r) => <th key={r.code} style={{ fontSize: 11 }}>{r.title}</th>)}<th>Лимит визы, ₽</th><th>Активен</th><th>Был в системе</th></tr></thead>
        <tbody>{shown.map((u) => (
          <tr key={u.id} style={{ opacity: u.is_active ? 1 : 0.5 }}>
            <td><b>{u.display_name}</b><div className="muted" style={{ fontSize: 12 }}>{u.email}</div></td>
            {roles.map((r) => <td key={r.code} style={{ textAlign: "center" }}><input type="checkbox" checked={u.roles.includes(r.code)}
              disabled={u.id === me.id && ["admin", "gd"].includes(r.code)} onChange={() => patch(u, { roles: toggle(u.roles, r.code) })} /></td>)}
            <td><input inputMode="decimal" defaultValue={u.visa_limit_rub ?? ""} style={{ width: 110 }} placeholder="—"
              onBlur={(e) => { const v = e.target.value.trim(); const n = v ? Number(v.replace(/\s/g, "").replace(",", ".")) : null; if (n !== u.visa_limit_rub) patch(u, { visa_limit_rub: n }); }} /></td>
            <td style={{ textAlign: "center" }}><input type="checkbox" checked={u.is_active} disabled={u.id === me.id} onChange={() => patch(u, { is_active: !u.is_active })} /></td>
            <td className="muted" style={{ fontSize: 12 }}>{u.last_seen_at ? fmtDate(u.last_seen_at) : "—"}</td>
          </tr>))}</tbody>
      </table>
      <h3>Добавить сотрудника</h3>
      <div className="row" style={{ flexWrap: "wrap" }}>
        <input placeholder="e-mail (логин)" value={nu.email} onChange={(e) => setNu({ ...nu, email: e.target.value })} />
        <input placeholder="Фамилия Имя" value={nu.display_name} onChange={(e) => setNu({ ...nu, display_name: e.target.value })} />
        {roles.map((r) => <label key={r.code} className="row"><input type="checkbox" checked={nu.roles.includes(r.code)} onChange={() => setNu({ ...nu, roles: toggle(nu.roles, r.code) })} /> {r.title}</label>)}
        <button className="btn" disabled={!nu.email.includes("@") || !nu.display_name.trim() || !nu.roles.length} onClick={create}>Добавить</button>
      </div>
      <p className="muted" style={{ fontSize: 12 }}>Контакты и каналы уведомлений сотрудника — в разделе «Сотрудники».</p>
    </div>
  );
}

/** Справочник ТН ВЭД — ведётся в разделе «Каталог» (вкладка «ТН ВЭД»). */
export function HsCodes() {
  const [data, setData] = useState<{ total: number; rows: Hs[] }>({ total: 0, rows: [] });
  const [q, setQ] = useState("");
  const [msg, setMsg] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [vf, setVf] = useState("");
  const empty = { code: "", description: "", duty_pct: "0", vat_pct: "22", util_fee: false, licence_required: false, certification: "" };
  const [f, setF] = useState(empty);
  const load = () => api.get<typeof data>(`/api/admin/hs?q=${encodeURIComponent(q)}`).then(setData).catch((e) => setMsg(e.message));
  useEffect(() => { const t = setTimeout(load, 250); return () => clearTimeout(t); }, [q]);
  async function upload() {
    if (!file) return;
    try {
      const r = await api.upload<{ imported: number; errors: string[] }>(`/api/admin/hs/upload${vf ? `?valid_from=${vf}` : ""}`, file);
      setMsg(`Загружено кодов: ${r.imported}${r.errors.length ? `; пропущено строк: ${r.errors.length} (${r.errors.slice(0, 5).join("; ")})` : ""}`); setFile(null); load();
    } catch (e) { setMsg((e as Error).message); }
  }
  async function save() {
    try {
      await api.post("/api/admin/hs", { ...f, duty_pct: Number(f.duty_pct.replace(",", ".")), vat_pct: Number(f.vat_pct.replace(",", ".")), certification: f.certification.split(",") });
      setMsg(`Код ${f.code} сохранён; прежние ставки закрыты сегодняшней датой`); setF(empty); load();
    } catch (e) { setMsg((e as Error).message); }
  }
  return (
    <div className="card">
      <p className="muted">Ставки по коду подставляются логисту; если логист вводит другую ставку, нужен комментарий. Новая загрузка не удаляет прежние ставки: старая запись закрывается датой, история сохраняется. Коды, которые логист ввёл вручную, тоже попадают в этот справочник.</p>
      {msg && <div className="alert info">{msg}</div>}
      <div className="grid cols-2">
        <div>
          <h3 style={{ marginTop: 0 }}>Загрузить файл CSV</h3>
          <div className="muted" style={{ fontSize: 12 }}>Столбцы через «;»: code; description; duty_pct; vat_pct; util_fee (да/нет); licence (да/нет); certification (через запятую). Образец формата — <a href="/api/admin/hs/export">выгрузка текущего справочника</a>.</div>
          <div className="row" style={{ marginTop: 6 }}>
            <input type="file" accept=".csv,text/csv" onChange={(e) => setFile(e.target.files?.[0] || null)} />
            <label className="row">действует с <input type="date" value={vf} onChange={(e) => setVf(e.target.value)} /></label>
            <button className="btn" disabled={!file} onClick={upload}>Загрузить</button>
          </div>
        </div>
        <div>
          <h3 style={{ marginTop: 0 }}>Добавить или изменить код</h3>
          <div className="row" style={{ flexWrap: "wrap" }}>
            <input placeholder="код (10 цифр)" value={f.code} onChange={(e) => setF({ ...f, code: e.target.value })} style={{ width: 120 }} />
            <input placeholder="описание" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} style={{ width: 260 }} />
            <label className="row">пошлина, % <input value={f.duty_pct} onChange={(e) => setF({ ...f, duty_pct: e.target.value })} style={{ width: 60 }} /></label>
            <label className="row">НДС, % <input value={f.vat_pct} onChange={(e) => setF({ ...f, vat_pct: e.target.value })} style={{ width: 60 }} /></label>
            <label className="row"><input type="checkbox" checked={f.util_fee} onChange={(e) => setF({ ...f, util_fee: e.target.checked })} /> утильсбор</label>
            <label className="row"><input type="checkbox" checked={f.licence_required} onChange={(e) => setF({ ...f, licence_required: e.target.checked })} /> лицензия</label>
            <input placeholder="сертификация: ТР ТС 010/2011, ПСМ" value={f.certification} onChange={(e) => setF({ ...f, certification: e.target.value })} style={{ width: 240 }} />
            <button className="btn" disabled={!f.code.trim() || !f.description.trim()} onClick={save}>Сохранить</button>
          </div>
        </div>
      </div>
      <div className="row" style={{ margin: "10px 0" }}>
        <input placeholder="поиск: начало кода или описание" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 300 }} />
        <span className="muted">действующих кодов: {data.total}{data.total > data.rows.length ? `, показаны первые ${data.rows.length}` : ""}</span>
      </div>
      <table>
        <thead><tr><th>Код</th><th>Описание</th><th className="num">Пошлина, %</th><th className="num">НДС, %</th><th>Утильсбор</th><th>Лицензия</th><th>Сертификация</th><th>Действует с</th><th>Источник</th><th /></tr></thead>
        <tbody>{data.rows.map((h) => (
          <tr key={h.id}><td><b>{h.code}</b></td><td style={{ fontSize: 12 }}>{h.description}</td><td className="num">{h.duty_pct}</td><td className="num">{h.vat_pct}</td>
            <td>{h.util_fee ? "да" : "нет"}</td><td>{h.licence_required ? "да" : "нет"}</td><td style={{ fontSize: 12 }}>{(h.certification || []).join(", ")}</td>
            <td>{fmtDate(h.valid_from)}</td><td className="muted" style={{ fontSize: 12 }}>{h.version_file}</td>
            <td><button className="btn ghost" onClick={() => setF({ code: h.code, description: h.description, duty_pct: String(h.duty_pct), vat_pct: String(h.vat_pct), util_fee: h.util_fee, licence_required: h.licence_required, certification: (h.certification || []).join(", ") })}>изменить</button></td></tr>))}</tbody>
      </table>
    </div>
  );
}

function FxRates({ canManual }: { canManual: boolean }) {
  const [cur, setCur] = useState("CNY");
  const [rows, setRows] = useState<Fx[]>([]);
  const [sum, setSum] = useState<FxSum[]>([]);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [m, setM] = useState({ on: new Date().toISOString().slice(0, 10), rate: "", comment: "" });
  const load = () => { api.get<Fx[]>(`/api/admin/fx?currency=${cur}&days=60`).then(setRows).catch((e) => setMsg(e.message)); api.get<FxSum[]>("/api/admin/fx/summary").then(setSum).catch(() => undefined); };
  useEffect(() => { load(); }, [cur]);
  async function run(url: string, ok: (r: Record<string, unknown>) => string) {
    setBusy(true);
    try { setMsg(ok(await api.post<Record<string, unknown>>(url))); load(); } catch (e) { setMsg((e as Error).message); } finally { setBusy(false); }
  }
  async function manual() {
    try { await api.post("/api/admin/fx/manual", { on: m.on, currency: cur, rate: Number(m.rate.replace(",", ".")), comment: m.comment }); setMsg("Ручной курс сохранён: в расчётах на эту дату он имеет приоритет над курсом ЦБ"); setM({ ...m, rate: "", comment: "" }); load(); }
    catch (e) { setMsg((e as Error).message); }
  }
  return (
    <div className="card">
      <p className="muted">Курсы ЦБ загружаются автоматически раз в час: курс на сегодня и история за 5 лет, если её почти нет. История нужна для тренда, сезонности и волатильности в прогнозе курса. Ручной курс — на случай недоступности ЦБ: в расчёт на эту дату попадает он, с предупреждением.</p>
      {msg && <div className="alert info">{msg}</div>}
      <h3 style={{ marginTop: 0 }}>Загружено</h3>
      <table><thead><tr><th>Валюта</th><th>Источник</th><th className="num">Записей</th><th>Первая дата</th><th>Последняя дата</th></tr></thead>
        <tbody>{sum.map((s) => <tr key={s.currency + s.source}><td>{s.currency}</td><td>{SOURCE[s.source] || s.source}</td><td className="num">{fmtRub(s.count)}</td><td>{fmtDate(s.first)}</td><td>{fmtDate(s.last)}</td></tr>)}</tbody></table>
      <div className="row" style={{ margin: "10px 0", flexWrap: "wrap" }}>
        <select value={cur} onChange={(e) => setCur(e.target.value)}>{["CNY", "USD", "EUR", "KGS"].map((c) => <option key={c}>{c}</option>)}</select>
        <button className="btn secondary" disabled={busy} onClick={() => run("/api/admin/fx/load", (r) => `Курсы ЦБ на сегодня: ${Object.entries((r.rates as Record<string, number>) || {}).filter(([k]) => ["CNY", "USD", "EUR", "KGS"].includes(k)).map(([k, v]) => `${k} ${v}`).join(", ")}`)}>Загрузить курс ЦБ на сегодня</button>
        <button className="btn ghost" disabled={busy} onClick={() => run(`/api/admin/fx/history?currency=${cur}&start=${new Date(Date.now() - 5 * 365 * 864e5).toISOString().slice(0, 10)}`, (r) => `${cur}: догружено дат из истории ЦБ — ${r.loaded}`)}>Догрузить историю {cur} за 5 лет</button>
      </div>
      {canManual && (
        <div className="row" style={{ flexWrap: "wrap", marginBottom: 10 }}>
          <b>Ручной курс {cur}:</b>
          <input type="date" value={m.on} onChange={(e) => setM({ ...m, on: e.target.value })} />
          <input inputMode="decimal" placeholder="₽ за 1" value={m.rate} onChange={(e) => setM({ ...m, rate: e.target.value })} style={{ width: 100 }} />
          <input placeholder="основание (обязательно)" value={m.comment} onChange={(e) => setM({ ...m, comment: e.target.value })} style={{ width: 260 }} />
          <button className="btn" disabled={!m.rate || !m.comment.trim()} onClick={manual}>Сохранить</button>
        </div>)}
      <table><thead><tr><th>Дата</th><th className="num">Курс, ₽</th><th>Источник</th><th>Комментарий</th></tr></thead>
        <tbody>{rows.map((r) => <tr key={r.id}><td>{fmtDate(r.on)}</td><td className="num">{r.rate.toFixed(4).replace(".", ",")}</td><td>{SOURCE[r.source] || r.source}</td><td className="muted">{r.comment}</td></tr>)}</tbody></table>
    </div>
  );
}

function Templates() {
  const [data, setData] = useState<{ kinds: Record<string, string>; templates: Tpl[] }>({ kinds: {}, templates: [] });
  const [msg, setMsg] = useState("");
  const load = () => api.get<typeof data>("/api/admin/templates").then(setData).catch((e) => setMsg(e.message));
  useEffect(() => { load(); }, []);
  async function upload(kind: string, file: File | undefined) {
    if (!file) return;
    try { const t = await api.upload<Tpl>(`/api/admin/templates/${kind}`, file); setMsg(`Загружена версия ${t.version}: ${data.kinds[kind]}`); load(); }
    catch (e) { setMsg((e as Error).message); }
  }
  const post = (url: string, ok: string) => api.post(url).then(() => { setMsg(ok); load(); }).catch((e) => setMsg(e.message));
  return (
    <div className="card">
      <p className="muted">Если загруженного шаблона нет, используется встроенный шаблон системы. Шаблон RFQ проверяется при загрузке: файл должен разбираться как опросник и содержать обязательные поля. КП сейчас собирается кодом по бренд-буку; загруженный шаблон КП хранится, но генератор его пока не использует.</p>
      {msg && <div className="alert info">{msg}</div>}
      {Object.entries(data.kinds).map(([kind, title]) => {
        const list = data.templates.filter((t) => t.kind === kind);
        const active = list.find((t) => t.is_active);
        return (
          <div key={kind} style={{ marginBottom: 14 }}>
            <h3 style={{ marginBottom: 4 }}>{title}</h3>
            <div className="row" style={{ flexWrap: "wrap" }}>
              <span>Действует: {active ? <b>версия {active.version} — {active.file_name}</b> : <b>встроенный шаблон системы</b>}</span>
              {kind === "rfq" && <a className="btn ghost" href="/api/deals/rfq/template/file">Скачать действующий</a>}
              <label className="btn secondary" style={{ cursor: "pointer" }}>Загрузить новую версию<input type="file" hidden onChange={(e) => upload(kind, e.target.files?.[0])} /></label>
              {active && <button className="btn ghost" onClick={() => post(`/api/admin/templates/${kind}/reset`, "Возвращён встроенный шаблон")}>Вернуть встроенный</button>}
            </div>
            {list.length > 0 && <table style={{ marginTop: 4 }}><tbody>{list.map((t) => (
              <tr key={t.id}><td>версия {t.version}</td><td>{t.file_name}{!t.file_exists && <span className="badge bad">файл не найден</span>}</td><td className="muted">{fmtDate(t.uploaded_at || t.created_at)}</td>
                <td>{t.is_active ? <span className="badge ok">действует</span> : t.file_exists && <button className="btn ghost" onClick={() => post(`/api/admin/templates/${t.id}/activate`, `Действует версия ${t.version}`)}>сделать действующей</button>}</td></tr>))}</tbody></table>}
          </div>);
      })}
    </div>
  );
}

function AuditLog() {
  const [rows, setRows] = useState<Audit[]>([]);
  const [entities, setEntities] = useState<string[]>([]);
  const [f, setF] = useState({ entity: "", q: "", limit: "200" });
  const [open, setOpen] = useState<number | null>(null);
  const load = () => api.get<Audit[]>(`/api/admin/audit?limit=${f.limit}&entity=${encodeURIComponent(f.entity)}&q=${encodeURIComponent(f.q)}`).then(setRows);
  useEffect(() => { api.get<string[]>("/api/admin/audit/entities").then(setEntities); }, []);
  useEffect(() => { const t = setTimeout(load, 300); return () => clearTimeout(t); }, [f]);
  return (
    <div className="card">
      <p className="muted">Все изменения данных: кто, когда, что было и что стало. Журнал только для чтения, изменить записи нельзя (это защищено на уровне базы данных).</p>
      <div className="row" style={{ marginBottom: 8, flexWrap: "wrap" }}>
        <select value={f.entity} onChange={(e) => setF({ ...f, entity: e.target.value })}><option value="">все объекты</option>{entities.map((e) => <option key={e} value={e}>{lbl(AUDIT_ENTITY, e)}</option>)}</select>
        <input placeholder="поиск: сделка, сотрудник, значение" value={f.q} onChange={(e) => setF({ ...f, q: e.target.value })} style={{ width: 280 }} />
        <select value={f.limit} onChange={(e) => setF({ ...f, limit: e.target.value })}>{["100", "200", "500", "1000"].map((n) => <option key={n} value={n}>последние {n}</option>)}</select>
      </div>
      <table><thead><tr><th>Когда</th><th>Кто</th><th>Сделка</th><th>Объект</th><th>Действие</th><th>Было → стало</th></tr></thead>
        <tbody>{rows.map((r) => (
          <tr key={r.id} onClick={() => setOpen(open === r.id ? null : r.id)} style={{ cursor: "pointer" }}>
            <td style={{ whiteSpace: "nowrap", fontSize: 12 }}>{new Date(r.at).toLocaleString("ru-RU")}</td><td>{r.user_name}</td><td>{r.deal_number || ""}</td>
            <td style={{ fontSize: 12 }}>{lbl(AUDIT_ENTITY, r.entity)}<div className="muted">{r.entity_id}</div></td><td>{lbl(AUDIT_ACTION, r.action)}{r.comment && <div className="muted" style={{ fontSize: 12 }}>{r.comment}</div>}</td>
            <td style={{ fontSize: 11, maxWidth: 520 }}>{open === r.id
              ? <pre style={{ whiteSpace: "pre-wrap", margin: 0 }}>{JSON.stringify({ было: human(r.before), стало: human(r.after) }, null, 1)}</pre>
              : <>{r.before != null && <span className="muted">{short(r.before)} → </span>}{short(r.after)}</>}</td></tr>))}</tbody></table>
    </div>
  );
}

function Integrations() {
  const [rows, setRows] = useState<Integ[]>([]);
  const [sys, setSys] = useState("");
  useEffect(() => { api.get<Integ[]>("/api/admin/integrations/log?limit=300").then(setRows); }, []);
  const systems = [...new Set(rows.map((r) => r.system))];
  return (
    <div className="card">
      <p className="muted">Обмены с внешними сервисами: курсы ЦБ, Яндекс Переводчик, реестр организаций (DaData), почта, WeCom. Ошибки подсвечены.</p>
      <select value={sys} onChange={(e) => setSys(e.target.value)} style={{ marginBottom: 8 }}><option value="">все системы</option>{systems.map((s) => <option key={s} value={s}>{lbl(INTEGRATION, s)}</option>)}</select>
      <table><thead><tr><th>Когда</th><th>Система</th><th>Операция</th><th>Результат</th><th>Подробности</th></tr></thead>
        <tbody>{rows.filter((r) => !sys || r.system === sys).map((r) => (
          <tr key={r.id}><td style={{ whiteSpace: "nowrap", fontSize: 12 }}>{new Date(r.at).toLocaleString("ru-RU")}</td><td>{lbl(INTEGRATION, r.system)}</td><td>{lbl(OPERATION, r.operation)}</td>
            <td><span className={"badge " + (r.ok ? "ok" : "bad")}>{r.ok ? "успешно" : "ошибка"}</span></td><td style={{ fontSize: 11 }}>{short(r.details)}</td></tr>))}</tbody></table>
    </div>
  );
}

function Accuracy() {
  const [d, setD] = useState<{ deals: number; mape_by_item: Record<string, number> } | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { api.get<typeof d>("/api/reports/estimate-accuracy").then(setD).catch((e) => setErr(e.message)); }, []);
  if (err) return <div className="alert error">{err}</div>;
  if (!d) return null;
  return (
    <div className="card">
      <p className="muted">По сделкам с закрытым план/фактом: средняя ошибка оценки по статьям (|факт − план базового сценария| / план). Помогает понять, какие статьи систематически недооцениваются.</p>
      <p>Закрытых сделок: <b>{d.deals}</b></p>
      {Object.keys(d.mape_by_item).length === 0 ? <div className="muted">Пока нет данных: нужен хотя бы один закрытый план/факт с фактом по статьям.</div> : (
        <table><thead><tr><th>Статья</th><th className="num">Средняя ошибка, %</th></tr></thead>
          <tbody>{COST_ITEMS.filter(([, k]) => d.mape_by_item[k] != null).map(([n, k, t]) => <tr key={k}><td>{n}. {t}</td><td className="num">{d.mape_by_item[k].toFixed(1).replace(".", ",")}</td></tr>)}</tbody></table>)}
    </div>
  );
}

type OvSum = { user_id: string; name: string; roles: string[]; tasks: number; overdue: number; open_overdue: number; days_total: number; days_max: number; days_avg: number; overdue_pct: number };
type OvRow = { user_id: string; name: string; deal_id: string; deal_number: string; title: string; role: string; due: string; done_on: string | null; days_late: number; open: boolean };

/** История просрочек выполнения задач по каждому сотруднику — только ЗГД. */
function Overdue() {
  const [d, setD] = useState<{ summary: OvSum[]; rows: OvRow[] } | null>(null);
  const [f, setF] = useState({ from: "", to: "" });
  const [sel, setSel] = useState("");
  const [err, setErr] = useState("");
  useEffect(() => {
    const qs = new URLSearchParams(); if (f.from) qs.set("date_from", f.from); if (f.to) qs.set("date_to", f.to);
    api.get<typeof d>(`/api/reports/overdue?${qs}`).then(setD).catch((e) => setErr(e.message));
  }, [f]);
  if (err) return <div className="alert error">{err}</div>;
  if (!d) return null;
  const rows = d.rows.filter((r) => !sel || r.user_id === sel);
  return (
    <div className="card">
      <p className="muted">Просрочка — задача выполнена позже срока или не выполнена, а срок прошёл. Дни просрочки — календарные. Раздел виден только ЗГД. Уведомления о просрочке получают исполнитель, КД и ЗГД (проверка раз в час, одно уведомление на задачу).</p>
      <div className="row" style={{ marginBottom: 8 }}>
        <label className="row">срок задачи с <input type="date" value={f.from} onChange={(e) => setF({ ...f, from: e.target.value })} /></label>
        <label className="row">по <input type="date" value={f.to} onChange={(e) => setF({ ...f, to: e.target.value })} /></label>
        {(f.from || f.to) && <button className="btn ghost" onClick={() => setF({ from: "", to: "" })}>за всё время</button>}
      </div>
      <table>
        <thead><tr><th>Сотрудник</th><th className="num">Задач</th><th className="num">Просрочено</th><th className="num">Доля</th><th className="num">Сейчас просрочено</th><th className="num">Средняя просрочка, дн.</th><th className="num">Максимум, дн.</th></tr></thead>
        <tbody>{d.summary.map((s) => (
          <tr key={s.user_id} onClick={() => setSel(sel === s.user_id ? "" : s.user_id)} style={{ cursor: "pointer", background: sel === s.user_id ? "var(--accent-soft)" : undefined }}>
            <td><b>{s.name}</b><div className="muted" style={{ fontSize: 12 }}>{s.roles.join(", ")}</div></td><td className="num">{s.tasks}</td>
            <td className="num">{s.overdue ? <span className="badge warn">{s.overdue}</span> : 0}</td><td className="num">{s.overdue_pct.toFixed(1).replace(".", ",")} %</td>
            <td className="num">{s.open_overdue ? <span className="badge bad">{s.open_overdue}</span> : 0}</td><td className="num">{s.days_avg.toFixed(1).replace(".", ",")}</td><td className="num">{s.days_max}</td></tr>))}</tbody>
      </table>
      <h3>{sel ? `Просроченные задачи: ${d.summary.find((s) => s.user_id === sel)?.name}` : "Все просроченные задачи"}</h3>
      {rows.length === 0 ? <div className="muted">Просрочек нет.</div> : (
        <table><thead><tr><th>Сотрудник</th><th>Сделка</th><th>Задача</th><th>Срок</th><th>Выполнена</th><th className="num">Просрочка, дн.</th></tr></thead>
          <tbody>{rows.map((r, i) => (
            <tr key={i}><td>{r.name}</td><td>{r.deal_id ? <a href={`/deals/${r.deal_id}`}>{r.deal_number}</a> : ""}</td><td>{r.title}</td><td>{fmtDate(r.due)}</td>
              <td>{r.open ? <span className="badge bad">не выполнена</span> : fmtDate(r.done_on)}</td><td className="num"><b>{r.days_late}</b></td></tr>))}</tbody></table>)}
    </div>
  );
}
