import { useEffect, useState } from "react";
import { lbl, CATALOG_STATUS, GLOSSARY_KIND } from "../labels";
import { api, fmtDate, fmtRub, designation } from "../api";
import { can, type Me } from "../types";
import CatalogCard from "../components/CatalogCard";
import CatalogAdd from "../components/CatalogAdd";

type Tree = { factory_id: string; factory: string; types: { machine_type: string; models: { id: string; model: string; trademark?: string; brand_name: string; status: string; executions: { id: string; name: string; brand_name: string; status: string; params: number; images: number; source_deal_id: string | null }[] }[] }[] }[];
type Exec = { id: string; name: string; brand_name: string; status: string; tech_params: { no: number; name_ru: string; unit: string; value_ru: string; required: boolean; local_ru: boolean }[]; images: unknown[];
  model: { id: string; model: string; trademark?: string; brand_name: string; machine_type: string; factory_id: string } | null; source_deal_id: string | null;
  factory_prices?: { on: string; unit_price: number; currency: string; production_term: { value?: number; unit?: string }; source: string; deal_id: string }[];
  client_prices?: { on: string; deal_number: string; unit_price_rub: number; total_rub: number; qty: number; outcome: string | null; delivery_term: string }[] };
type Gl = { id: string; term_ru: string; term_zh: string; kind: string; status: string };
type TplRow = { id?: string; no: number; code: string; name_ru: string; name_zh: string; unit: string; required: boolean; section: string };

/** Задача 7: каталог производитель → тип → модель → исполнение, подтверждение КД/ЗГД, история цен; задача 4: глоссарий и шаблоны (обязательность). */
export default function CatalogPage({ me }: { me: Me }) {
  const [tab, setTab] = useState<"tree" | "add" | "pending" | "glossary" | "templates" | "archive">("tree");
  const [mergeExec, setMergeExec] = useState("");
  const [mergeModel, setMergeModel] = useState<Record<string, string>>({});
  const [archive, setArchive] = useState<{ models: { id: string; model: string; trademark?: string; brand_name: string }[]; executions: { id: string; name: string; model_id: string }[] }>({ models: [], executions: [] });
  const [tree, setTree] = useState<Tree>([]);
  const [exec, setExec] = useState<Exec | null>(null);
  const [modelId, setModelId] = useState("");   // карточка машины справа
  const [pending, setPending] = useState<{ id: string; name: string; model: string; source_deal_id: string | null; created_at: string }[]>([]);
  const [gl, setGl] = useState<Gl[]>([]);
  const [glQ, setGlQ] = useState("");
  const [glNew, setGlNew] = useState({ term_ru: "", term_zh: "", kind: "param" });
  const [types, setTypes] = useState<{ id: string; name: string; status: string }[]>([]);
  const [tplType, setTplType] = useState("");
  const [tpl, setTpl] = useState<TplRow[]>([]);
  const [msg, setMsg] = useState("");
  const editor = can(me, "catalog", 2);
  const contributor = me.roles.some((r) => ["manager", "cd", "gd"].includes(r.code));
  const loadTree = () => api.get<Tree>(`/api/catalog/tree?include_pending=${editor}`).then(setTree);
  const loadGl = () => api.get<Gl[]>(`/api/refs/glossary?q=${encodeURIComponent(glQ)}`).then(setGl).catch(() => setGl([]));
  const loadTpl = () => api.get<{ rows: TplRow[] }>(`/api/refs/tech-param-templates?machine_type_id=${tplType}`).then((r) => setTpl(r.rows));
  useEffect(() => { loadTree(); api.get<typeof types>("/api/refs/machine-types").then(setTypes); if (editor) api.get<typeof pending>("/api/catalog/pending").then(setPending); }, []);
  useEffect(() => { if (tab === "glossary") loadGl(); if (tab === "templates") loadTpl(); if (tab === "archive") loadArchive(); }, [tab, tplType]);
  const loadArchive = () => api.get<typeof archive>("/api/catalog/archive").then(setArchive).catch(() => setArchive({ models: [], executions: [] }));
  const allModels = tree.flatMap((f) => f.types.flatMap((t) => t.models.map((m) => ({ ...m, factory: f.factory, factory_id: f.factory_id }))));
  const allExecs = allModels.flatMap((m) => m.executions.map((e) => ({ ...e, model: m.model, factory: m.factory })));
  async function act(fn: () => Promise<unknown>, ok: string) {
    try { await fn(); setMsg(ok); loadTree(); if (tab === "archive") loadArchive(); } catch (e) { setMsg((e as Error).message); }
  }
  const openExec = (id: string) => api.get<Exec>(`/api/catalog/executions/${id}`).then((x) => { setExec(x); if (x.model?.id) setModelId(x.model.id); });
  async function decide(id: string, approve: boolean) {
    const brand = approve && editor ? prompt("Наименование под брендом компании (можно оставить пустым):", "") ?? "" : "";
    try { await api.post(`/api/catalog/executions/${id}/decision`, { approve, brand_name: brand || null }); setMsg(approve ? "Исполнение занесено в каталог" : "Отклонено"); api.get<typeof pending>("/api/catalog/pending").then(setPending); loadTree(); if (exec?.id === id) openExec(id); }
    catch (e) { setMsg((e as Error).message); }
  }
  return (
    <>
      <h1>Каталог оборудования</h1>
      {msg && <div className="alert info">{msg}</div>}
      <div className="tabs">
        <button className={tab === "tree" ? "active" : ""} onClick={() => setTab("tree")}>Производитель → тип → модель → исполнение</button>
        {contributor && <button className={tab === "add" ? "active" : ""} onClick={() => setTab("add")}>+ Добавить оборудование</button>}
        {editor && <button className={tab === "pending" ? "active" : ""} onClick={() => setTab("pending")}>На подтверждение ({pending.length})</button>}
        {can(me, "glossary") && <button className={tab === "glossary" ? "active" : ""} onClick={() => setTab("glossary")}>Глоссарий RU ↔ CN</button>}
        <button className={tab === "templates" ? "active" : ""} onClick={() => setTab("templates")}>Шаблоны технических характеристик</button>
        {editor && <button className={tab === "archive" ? "active" : ""} onClick={() => setTab("archive")}>Архив</button>}
      </div>
      {tab === "tree" && (
        <div className="grid" style={{ gridTemplateColumns: "380px 1fr" }}>
          <div className="card">
            {tree.length === 0 && <div className="muted">Каталог пуст — модели появляются после отправки КП (менеджер предлагает, КД/ЗГД подтверждает)</div>}
            {tree.map((f) => (
              <details key={f.factory_id} open><summary style={{ fontWeight: 700, cursor: "pointer" }}>{f.factory}</summary>
                {f.types.map((t) => (
                  <div key={t.machine_type} style={{ marginLeft: 12 }}><div className="muted">{t.machine_type}</div>
                    {t.models.map((m) => (
                      <div key={m.id} style={{ marginLeft: 12 }}>
                        <b style={{ cursor: "pointer", color: modelId === m.id ? "var(--accent)" : undefined, textDecoration: "underline dotted" }} title="открыть карточку машины"
                          onClick={() => { setModelId(m.id); setExec(null); }}>{designation(m)}</b>{m.brand_name && <span className="badge accent" style={{ marginLeft: 4 }}>{m.brand_name}</span>}{m.status === "pending" && <span className="badge warn"> на проверке</span>}
                        {editor && <button className="btn ghost" style={{ padding: "0 6px", marginLeft: 4 }} onClick={() => { const b = prompt("Наименование под брендом компании:", m.brand_name || ""); if (b !== null) api.patch(`/api/catalog/models/${m.id}`, { brand_name: b }).then(loadTree); }}>бренд</button>}
                        {editor && (
                          <div className="row" style={{ fontSize: 12, margin: "2px 0" }}>
                            <select value={mergeModel[m.id] || ""} onChange={(e) => setMergeModel({ ...mergeModel, [m.id]: e.target.value })} style={{ padding: 2, maxWidth: 170 }} title="объединить дубль модели с другой моделью">
                              <option value="">объединить с…</option>
                              {allModels.filter((x) => x.id !== m.id).map((x) => <option key={x.id} value={x.id}>{designation(x)} ({x.factory.slice(0, 18)})</option>)}</select>
                            {mergeModel[m.id] && <button className="btn ghost" style={{ padding: "0 6px" }} onClick={() => act(() => api.post(`/api/catalog/models/${m.id}/merge`, { into_id: mergeModel[m.id] }), `Модель ${m.model} объединена; дубль в архиве`)}>объединить</button>}
                            <button className="btn ghost" style={{ padding: "0 6px" }} onClick={() => act(() => api.patch(`/api/catalog/models/${m.id}`, { status: "archived" }), `Модель ${m.model} в архиве`)}>в архив</button>
                          </div>)}
                        {m.executions.map((e) => (
                          <div key={e.id} style={{ marginLeft: 12, cursor: "pointer", color: exec?.id === e.id ? "var(--accent)" : undefined }} onClick={() => openExec(e.id)}>
                            ↳ {e.name} <span className="muted">({e.params} парам., {e.images} изобр.)</span>{e.status === "pending" && <span className="badge warn">на проверке</span>}
                          </div>))}
                      </div>))}
                  </div>))}
              </details>))}
          </div>
          <div>
            {modelId ? (
              <>
                {exec && editor && (exec.status === "pending" || exec.status === "approved") && <div className="card">
                <div className="row" style={{ justifyContent: "space-between" }}>
                  <h2 style={{ marginTop: 0 }}>{exec.model ? designation(exec.model) : ""} — {exec.name} {exec.brand_name && <span className="badge accent">{exec.brand_name}</span>} <span className={"badge " + (exec.status === "approved" ? "ok" : "warn")}>{lbl(CATALOG_STATUS, exec.status)}</span></h2>
                  {editor && exec.status === "pending" && <div className="row"><button className="btn" onClick={() => decide(exec.id, true)}>Подтвердить</button><button className="btn ghost" onClick={() => decide(exec.id, false)}>Отклонить</button></div>}
                </div>
                {editor && exec.status === "pending" && <p className="muted" style={{ marginTop: 0 }}>Перед подтверждением можно исправить любые данные в карточке ниже: технические характеристики, фото и подписи, опции, регламенты, ТН ВЭД и данные для логистики, а также модель («Изменить модель»).</p>}
                {editor && exec.status === "approved" && (
                  <div className="row" style={{ marginBottom: 8 }}>
                    <select value={mergeExec} onChange={(e) => setMergeExec(e.target.value)} title="дубль: сделки и история цен перейдут в выбранное исполнение">
                      <option value="">объединить дубль с исполнением…</option>
                      {allExecs.filter((x) => x.id !== exec.id).map((x) => <option key={x.id} value={x.id}>{x.model} › {x.name}</option>)}</select>
                    <button className="btn ghost" disabled={!mergeExec} onClick={() => act(() => api.post(`/api/catalog/executions/${exec.id}/merge`, { into_id: mergeExec }).then(() => { setExec(null); setMergeExec(""); }), "Исполнения объединены; дубль в архиве")}>объединить</button>
                    <button className="btn ghost" onClick={() => act(() => api.patch(`/api/catalog/executions/${exec.id}`, { status: "archived" }).then(() => setExec(null)), "Исполнение в архиве")}>в архив</button>
                  </div>)}
                </div>}
                <CatalogCard modelId={modelId} execId={exec?.id} onExec={(id) => openExec(id)} />
              </>
            ) : <div className="card muted">Нажмите на машину слева — откроется карточка: технические характеристики, фото и чертежи, опции и регламенты{editor ? ", цены и аналитическая справка" : ""}.</div>}
          </div>
        </div>
      )}
      {tab === "archive" && (
        <div className="card">
          <p className="muted">Объединённые дубли и архивированные позиции каталога. В подборе при вводе заявок не участвуют; можно восстановить.</p>
          <h2>Модели</h2>
          <table><tbody>{archive.models.map((m) => <tr key={m.id}><td>{designation(m)}</td><td>{m.brand_name}</td>
            <td><button className="btn ghost" onClick={() => act(() => api.patch(`/api/catalog/models/${m.id}`, { status: "approved" }), "Модель восстановлена")}>восстановить</button></td></tr>)}
            {archive.models.length === 0 && <tr><td className="muted">нет</td></tr>}</tbody></table>
          <h2>Исполнения</h2>
          <table><tbody>{archive.executions.map((e) => <tr key={e.id}><td>{e.name}</td>
            <td><button className="btn ghost" onClick={() => act(() => api.patch(`/api/catalog/executions/${e.id}`, { status: "approved" }), "Исполнение восстановлено")}>восстановить</button></td></tr>)}
            {archive.executions.length === 0 && <tr><td className="muted">нет</td></tr>}</tbody></table>
        </div>
      )}
      {tab === "add" && <CatalogAdd editor={editor} onAdded={(m) => { setMsg(m); loadTree(); if (editor) api.get<typeof pending>("/api/catalog/pending").then(setPending); }} />}
      {tab === "pending" && (
        <div className="card">
          <table><thead><tr><th>Исполнение</th><th>Модель</th><th>Из сделки</th><th>Предложено</th><th /></tr></thead>
            <tbody>{pending.map((p) => <tr key={p.id}><td>{p.name}</td><td>{p.model}</td><td>{p.source_deal_id ? <a href={`/deals/${p.source_deal_id}`}>открыть</a> : "—"}</td><td>{fmtDate(p.created_at)}</td>
              <td className="row"><button className="btn ghost" onClick={() => { openExec(p.id); setTab("tree"); }}>посмотреть</button><button className="btn" onClick={() => decide(p.id, true)}>Подтвердить</button><button className="btn ghost" onClick={() => decide(p.id, false)}>Отклонить</button></td></tr>)}
              {pending.length === 0 && <tr><td colSpan={5} className="muted">Нет исполнений на подтверждение</td></tr>}</tbody></table>
        </div>
      )}
      {tab === "glossary" && (
        <div className="card">
          <p className="muted">Подтверждённые термины используются при переводе запросов заводу и ответов. Машинные переводы — на проверку: подтвердите или исправьте.</p>
          <div className="row"><input placeholder="поиск" value={glQ} onChange={(e) => setGlQ(e.target.value)} /><button className="btn secondary" onClick={loadGl}>Найти</button>
            {can(me, "glossary", 2) && <><input placeholder="термин RU" value={glNew.term_ru} onChange={(e) => setGlNew({ ...glNew, term_ru: e.target.value })} /><input placeholder="中文" value={glNew.term_zh} onChange={(e) => setGlNew({ ...glNew, term_zh: e.target.value })} />
              <select value={glNew.kind} onChange={(e) => setGlNew({ ...glNew, kind: e.target.value })}><option value="param">параметр</option><option value="unit">единица</option><option value="value">значение</option><option value="phrase">фраза</option></select>
              <button className="btn" disabled={!glNew.term_ru || !glNew.term_zh} onClick={() => api.post("/api/refs/glossary", { ...glNew, status: "confirmed" }).then(() => { setGlNew({ term_ru: "", term_zh: "", kind: "param" }); loadGl(); })}>Добавить</button></>}</div>
          <table style={{ marginTop: 8 }}><thead><tr><th>RU</th><th>CN</th><th>Тип</th><th>Статус</th><th /></tr></thead>
            <tbody>{gl.map((g) => <tr key={g.id} style={{ background: g.status === "machine" ? "#f3e8ff" : undefined }}><td>{g.term_ru}</td><td>{g.term_zh}</td><td>{lbl(GLOSSARY_KIND, g.kind)}</td><td><span className={"badge " + (g.status === "confirmed" ? "ok" : "warn")}>{g.status === "confirmed" ? "подтверждён" : "машинный"}</span></td>
              <td className="row">{can(me, "glossary", 2) && g.status === "machine" && <button className="btn ghost" onClick={() => { const z = prompt("Перевод CN:", g.term_zh); if (z !== null) api.post(`/api/refs/glossary/${g.id}/confirm?term_zh=${encodeURIComponent(z)}`).then(loadGl); }}>подтвердить</button>}
                {can(me, "glossary", 2) && <button className="btn ghost" onClick={() => api.del(`/api/refs/glossary/${g.id}`).then(loadGl)}>×</button>}</td></tr>)}
              {gl.length === 0 && <tr><td colSpan={5} className="muted">Пусто</td></tr>}</tbody></table>
        </div>
      )}
      {tab === "templates" && (
        <div className="card">
          <p className="muted">Перечень технических характеристик по типу машины и обязательность параметров (задают КД и ЗГД). Общий шаблон — фиксированные параметры RFQ (разделы 1–11). Менеджер может изменить обязательность в конкретной сделке.</p>
          <div className="row"><select value={tplType} onChange={(e) => setTplType(e.target.value)}><option value="">Общий шаблон</option>{types.map((t) => <option key={t.id} value={t.id}>{t.name}{t.status === "pending" ? " (на подтверждение)" : ""}</option>)}</select>
            {editor && <button className="btn add" onClick={() => { const n = prompt("Новый тип машины:"); if (n) api.post("/api/refs/machine-types", { name: n }).then(() => api.get<typeof types>("/api/refs/machine-types").then(setTypes)); }}>Тип машины</button>}
            {editor && <button className="btn" onClick={() => api.put(`/api/refs/tech-param-templates?machine_type_id=${tplType}`, tpl).then(() => setMsg("Шаблон сохранён")).catch((e) => setMsg(e.message))}>Сохранить шаблон</button>}
            {editor && <button className="btn add" onClick={() => setTpl([...tpl, { no: (tpl[tpl.length - 1]?.no || 0) + 1, code: "", name_ru: "", name_zh: "", unit: "", required: false, section: "" }])}>Строка</button>}</div>
          <table style={{ marginTop: 8 }}><thead><tr><th>№</th><th>Код</th><th>Параметр</th><th>参数</th><th>Ед.</th><th>Обязательный</th>{editor && <th />}</tr></thead>
            <tbody>{tpl.map((r, i) => (
              <tr key={r.id || i}><td>{r.no}</td><td>{r.code}</td>
                <td>{editor ? <input value={r.name_ru} style={{ width: "100%", padding: 3 }} onChange={(e) => setTpl(tpl.map((x, j) => j === i ? { ...x, name_ru: e.target.value } : x))} /> : r.name_ru}</td>
                <td>{editor ? <input value={r.name_zh} style={{ width: "100%", padding: 3 }} onChange={(e) => setTpl(tpl.map((x, j) => j === i ? { ...x, name_zh: e.target.value } : x))} /> : r.name_zh}</td>
                <td>{editor ? <input value={r.unit} style={{ width: 70, padding: 3 }} onChange={(e) => setTpl(tpl.map((x, j) => j === i ? { ...x, unit: e.target.value } : x))} /> : r.unit}</td>
                <td><input type="checkbox" checked={r.required} disabled={!editor} onChange={(e) => setTpl(tpl.map((x, j) => j === i ? { ...x, required: e.target.checked } : x))} /></td>
                {editor && <td><button className="btn ghost" style={{ padding: "0 6px" }} onClick={() => setTpl(tpl.filter((_, j) => j !== i))}>×</button></td>}</tr>))}</tbody></table>
        </div>
      )}
    </>
  );
}
