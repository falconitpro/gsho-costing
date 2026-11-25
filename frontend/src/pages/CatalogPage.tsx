import TemplateEditor from "../components/TemplateEditor";
import { useEffect, useState } from "react";
import { lbl, CATALOG_STATUS, GLOSSARY_KIND } from "../labels";
import { api, fmtDate, fmtRub, designation } from "../api";
import { can, type Me } from "../types";
import CatalogCard from "../components/CatalogCard";
import CatalogAdd from "../components/CatalogAdd";
import { askPrompt } from "../components/Dialogs";
import { useNewUiFlag } from "../uiMode";

type Tree = { factory_id: string; factory: string; types: { machine_type: string; models: { id: string; model: string; trademark?: string; brand_name: string; status: string; executions: { id: string; name: string; brand_name: string; status: string; params: number; images: number; source_deal_id: string | null }[] }[] }[] }[];
type Exec = { id: string; name: string; brand_name: string; status: string; tech_params: { no: number; name_ru: string; unit: string; value_ru: string; required: boolean; local_ru: boolean }[]; images: unknown[];
  model: { id: string; model: string; trademark?: string; brand_name: string; machine_type: string; factory_id: string } | null; source_deal_id: string | null;
  factory_prices?: { on: string; unit_price: number; currency: string; production_term: { value?: number; unit?: string }; source: string; deal_id: string }[];
  client_prices?: { on: string; deal_number: string; unit_price_rub: number; total_rub: number; qty: number; outcome: string | null; delivery_term: string }[] };
type Gl = { id: string; term_ru: string; term_zh: string; kind: string; status: string };

/** Задача 7: каталог производитель → тип → модель → исполнение, подтверждение КД/ЗГД, история цен; задача 4: глоссарий и шаблоны (обязательность). */
export default function CatalogPage({ me, embedded, openModelId }: { me: Me; embedded?: boolean; openModelId?: string }) {
  const [tab, setTab] = useState<"tree" | "add" | "pending" | "glossary" | "templates" | "archive">(() => (new URLSearchParams(window.location.search).get("tab") === "templates" ? "templates" : "tree"));
  const [mergeExec, setMergeExec] = useState("");
  const [selType, setSelType] = useState("");
  const [selBrand, setSelBrand] = useState("");
  const [cmp, setCmp] = useState<string[]>([]);
  const [showCmp, setShowCmp] = useState(false);
  const [archive, setArchive] = useState<{ models: { id: string; model: string; trademark?: string; brand_name: string }[]; executions: { id: string; name: string; model_id: string }[] }>({ models: [], executions: [] });
  const [tree, setTree] = useState<Tree>([]);
  const [exec, setExec] = useState<Exec | null>(null);
  const [modelId, setModelId] = useState(openModelId || "");   // карточка машины справа
  useEffect(() => { if (openModelId) { setModelId(openModelId); setTab("tree"); } }, [openModelId]);
  const [pending, setPending] = useState<{ id: string; name: string; model: string; source_deal_id: string | null; created_at: string }[]>([]);
  const [gl, setGl] = useState<Gl[]>([]);
  const [glQ, setGlQ] = useState("");
  const [glNew, setGlNew] = useState({ term_ru: "", term_zh: "", kind: "param" });
  const [types, setTypes] = useState<{ id: string; name: string; status: string }[]>([]);
  const [msg, setMsg] = useState("");
  const editor = can(me, "catalog", 2);
  const nv = useNewUiFlag();
  const contributor = me.roles.some((r) => ["manager", "cd", "gd"].includes(r.code));
  const loadTree = () => api.get<Tree>(`/api/catalog/tree?include_pending=${editor}`).then(setTree);
  const loadGl = () => api.get<Gl[]>(`/api/refs/glossary?q=${encodeURIComponent(glQ)}`).then(setGl).catch(() => setGl([]));
  useEffect(() => { loadTree(); api.get<typeof types>("/api/refs/machine-types").then(setTypes); if (editor) api.get<typeof pending>("/api/catalog/pending").then(setPending); }, []);
  useEffect(() => { if (tab === "glossary") loadGl(); if (tab === "archive") loadArchive(); }, [tab]);
  const loadArchive = () => api.get<typeof archive>("/api/catalog/archive").then(setArchive).catch(() => setArchive({ models: [], executions: [] }));
  const allModels = tree.flatMap((f) => f.types.flatMap((t) => t.models.map((m) => ({ ...m, machine_type: t.machine_type, factory: f.factory, factory_id: f.factory_id }))));
  const allExecs = allModels.flatMap((m) => m.executions.map((e) => ({ ...e, model: m.model, factory: m.factory })));
  async function act(fn: () => Promise<unknown>, ok: string) {
    try { await fn(); setMsg(ok); loadTree(); if (tab === "archive") loadArchive(); } catch (e) { setMsg((e as Error).message); }
  }
  const openExec = (id: string) => api.get<Exec>(`/api/catalog/executions/${id}`).then((x) => { setExec(x); if (x.model?.id) setModelId(x.model.id); });
  async function decide(id: string, approve: boolean) {
    const brand = approve && editor ? (await askPrompt("Наименование под брендом компании (можно оставить пустым):", "")) ?? "" : "";
    try { await api.post(`/api/catalog/executions/${id}/decision`, { approve, brand_name: brand || null }); setMsg(approve ? "Исполнение занесено в каталог" : "Отклонено"); api.get<typeof pending>("/api/catalog/pending").then(setPending); loadTree(); if (exec?.id === id) openExec(id); }
    catch (e) { setMsg((e as Error).message); }
  }
  return (
    <>
      {!embedded && <h1>Каталог оборудования</h1>}
      {msg && <div className="alert info">{msg}</div>}
      <div className={"tabs" + (nv ? " eq-subtabs" : "")}>
        <button className={tab === "tree" ? "active" : ""} onClick={() => setTab("tree")}>{nv ? "Модели и исполнения" : "Тип → бренд → модель → исполнение"}</button>
        {contributor && <button className={tab === "add" ? "active" : ""} onClick={() => setTab("add")}>+ Добавить оборудование</button>}
        {editor && <button className={tab === "pending" ? "active" : ""} onClick={() => setTab("pending")}>На подтверждение ({pending.length + types.filter((t) => t.status === "pending").length})</button>}
        {can(me, "glossary") && <button className={tab === "glossary" ? "active" : ""} onClick={() => setTab("glossary")}>Глоссарий RU ↔ CN</button>}
        <button className={tab === "templates" ? "active" : ""} onClick={() => setTab("templates")}>Шаблоны технических характеристик</button>
        {editor && <button className={tab === "archive" ? "active" : ""} onClick={() => setTab("archive")}>Архив</button>}
      </div>
      {tab === "tree" && (() => {
        const brandOf = (m: { trademark?: string; factory: string }) => (m.trademark || m.factory || "").trim();
        const typeList = [...new Set(allModels.map((m) => m.machine_type || "без типа"))].sort((a, b) => a.localeCompare(b, "ru"));
        const byType = allModels.filter((m) => !selType || (m.machine_type || "без типа") === selType);
        const brandList = [...new Set(byType.map(brandOf))].sort((a, b) => a.localeCompare(b, "ru"));
        const modelList = byType.filter((m) => !selBrand || brandOf(m) === selBrand).sort((a, b) => designation(a).localeCompare(designation(b), "ru"));
        const curModel = allModels.find((m) => m.id === modelId);
        const cmpBar = nv && cmp.length > 0 && (
          <div className="eq-cmpbar" role="region" aria-label="Сравнение исполнений">
            <span className="eq-cmpbar-l">Сравнение:</span>
            {cmp.map((id) => { const x = allExecs.find((e) => e.id === id); return <span key={id} className="eq-chip">{x ? `${x.model} › ${x.name}` : id.slice(0, 6)}<button type="button" aria-label="Убрать из сравнения" onClick={() => setCmp(cmp.filter((y) => y !== id))}>×</button></span>; })}
            <button className="btn" disabled={cmp.length < 2} onClick={() => setShowCmp(true)}>{cmp.length < 2 ? "Добавьте ещё одно исполнение" : `Сравнить (${cmp.length})`}</button>
            <button className="btn ghost" onClick={() => { setCmp([]); setShowCmp(false); }}>Очистить</button>
          </div>);
        if (nv) return (
        <>
          {showCmp && cmp.length > 1 && <CompareTable ids={cmp} onClose={() => setShowCmp(false)} />}
          {!modelId ? <EqBrowser models={allModels} empty={tree.length === 0} onOpen={(id) => { setModelId(id); setExec(null); window.scrollTo({ top: 0 }); }} /> : (
            <>
              <div className="eq-head card">
                <button type="button" className="btn ghost eq-back" onClick={() => { setModelId(""); setExec(null); }}>← Все модели</button>
                {curModel && <div className="eq-head-t"><span className="muted">{curModel.machine_type} · {curModel.factory}</span>
                  <b>{designation(curModel)}{curModel.brand_name && <span className="badge accent">{curModel.brand_name}</span>}{curModel.status === "pending" && <span className="badge warn">на проверке</span>}</b></div>}
                {curModel && curModel.executions.length > 0 && <div className="eq-execs" role="group" aria-label="Исполнения">
                  <span className="muted">Исполнения:</span>
                  {curModel.executions.map((e) => (
                    <span key={e.id} className={"eq-exec" + (exec?.id === e.id ? " on" : "")}>
                      <button type="button" aria-pressed={exec?.id === e.id} onClick={() => (exec?.id === e.id ? setExec(null) : openExec(e.id))}>{e.name}<small>{e.params} парам.{e.images ? ` · ${e.images} фото` : ""}</small>{e.status === "pending" && <i className="badge warn">на проверке</i>}</button>
                      <button type="button" className={"eq-cmp" + (cmp.includes(e.id) ? " on" : "")} title={cmp.includes(e.id) ? "убрать из сравнения" : "добавить к сравнению"} aria-pressed={cmp.includes(e.id)}
                        onClick={() => setCmp(cmp.includes(e.id) ? cmp.filter((y) => y !== e.id) : [...cmp, e.id])}>{cmp.includes(e.id) ? "✓ в сравнении" : "+ сравнить"}</button>
                    </span>))}
                </div>}
              </div>
              {exec && editor && (exec.status === "pending" || exec.status === "approved") && <div className="card eq-admin">
                <div className="row eq-exechead">
                  <h2>{exec.name} {exec.brand_name && <span className="badge accent">{exec.brand_name}</span>} <span className={"badge " + (exec.status === "approved" ? "ok" : "warn")}>{lbl(CATALOG_STATUS, exec.status)}</span></h2>
                  {exec.status === "pending" && <div className="row"><button className="btn" onClick={() => decide(exec.id, true)}>Подтвердить</button><button className="btn ghost" onClick={() => decide(exec.id, false)}>Отклонить</button></div>}
                </div>
                {exec.status === "pending" && <p className="muted eq-execnote">Перед подтверждением проверьте и при необходимости исправьте данные в карточке ниже — после подтверждения исполнение попадёт в подбор при вводе заявок.</p>}
                {exec.status === "approved" && (
                  <div className="row eq-execact">
                    <select value={mergeExec} onChange={(e) => setMergeExec(e.target.value)} title="дубль: сделки и история цен перейдут в выбранное исполнение">
                      <option value="">объединить дубль с исполнением…</option>
                      {allExecs.filter((x) => x.id !== exec.id).map((x) => <option key={x.id} value={x.id}>{x.model} › {x.name}</option>)}</select>
                    <button className="btn ghost" disabled={!mergeExec} onClick={() => act(() => api.post(`/api/catalog/executions/${exec.id}/merge`, { into_id: mergeExec }).then(() => { setExec(null); setMergeExec(""); }), "Исполнение объединено")}>объединить</button>
                    <button className="btn ghost" onClick={() => act(() => api.patch(`/api/catalog/executions/${exec.id}`, { status: "archived" }).then(() => setExec(null)), "Исполнение в архиве")}>в архив</button>
                  </div>)}
              </div>}
              <CatalogCard modelId={modelId} execId={exec?.id} onExec={(id) => openExec(id)} models={allModels.map((x) => ({ id: x.id, label: `${designation(x)} (${x.factory.slice(0, 24)})` }))} onChanged={() => { loadTree(); setModelId(""); setExec(null); }} />
            </>)}
          {cmpBar}
        </>);
        return (
        <>
          <div className="card">
            {tree.length === 0 ? <div className="muted">Каталог пуст — модели появляются после отправки КП (менеджер предлагает, КД/ЗГД подтверждает)</div> : (
              <div className="row" style={{ flexWrap: "wrap", alignItems: "flex-end" }}>
                <label className="f">Тип оборудования<select value={selType} onChange={(e) => { setSelType(e.target.value); setSelBrand(""); setModelId(""); setExec(null); }}>
                  <option value="">все типы ({typeList.length})</option>{typeList.map((t) => <option key={t} value={t}>{t}</option>)}</select></label>
                <label className="f">Бренд<select value={selBrand} onChange={(e) => { setSelBrand(e.target.value); setModelId(""); setExec(null); }}>
                  <option value="">все бренды ({brandList.length})</option>{brandList.map((b) => <option key={b} value={b}>{b}</option>)}</select></label>
                <label className="f">Модель<select value={modelId} onChange={(e) => { setModelId(e.target.value); setExec(null); }} style={{ minWidth: 220 }}>
                  <option value="">— выберите модель ({modelList.length}) —</option>
                  {modelList.map((m) => <option key={m.id} value={m.id}>{designation(m)}{m.brand_name ? ` · ${m.brand_name}` : ""}{m.status === "pending" ? " · на проверке" : ""}{!selBrand ? ` — ${brandOf(m)}` : ""}</option>)}</select></label>
                {curModel && curModel.executions.length > 0 && <label className="f">Исполнение<select value={exec?.id || ""} onChange={(e) => e.target.value ? openExec(e.target.value) : setExec(null)} style={{ minWidth: 200 }}>
                  <option value="">— исполнение —</option>{curModel.executions.map((e) => <option key={e.id} value={e.id}>{e.name} ({e.params} парам.){e.status === "pending" ? " · на проверке" : ""}</option>)}</select></label>}
                {exec && !cmp.includes(exec.id) && <button className="btn ghost" onClick={() => setCmp([...cmp, exec.id])}>+ к сравнению</button>}
                {cmp.length > 0 && <span className="row" style={{ flexWrap: "wrap" }}>
                  <span className="muted">сравнение:</span>{cmp.map((id) => { const x = allExecs.find((e) => e.id === id); return (
                    <span key={id} className="badge">{x ? `${x.model} › ${x.name}` : id.slice(0, 6)} <a href="#" onClick={(e) => { e.preventDefault(); setCmp(cmp.filter((y) => y !== id)); }}>×</a></span>); })}
                  <button className="btn" disabled={cmp.length < 2} onClick={() => setShowCmp(true)}>Сравнить ({cmp.length})</button></span>}
              </div>)}
          </div>
          {showCmp && cmp.length > 1 && <CompareTable ids={cmp} onClose={() => setShowCmp(false)} />}
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
                <CatalogCard modelId={modelId} execId={exec?.id} onExec={(id) => openExec(id)} models={allModels.map((x) => ({ id: x.id, label: `${designation(x)} (${x.factory.slice(0, 24)})` }))} onChanged={() => { loadTree(); setModelId(""); setExec(null); }} />
              </>
            ) : <div className="card muted">Выберите тип оборудования, бренд и модель — откроется карточка машины: технические характеристики, фото и чертежи, документы, опции и регламенты{editor ? ", цены и аналитическая справка" : ""}. Исполнения разных моделей и брендов можно добавить к сравнению.</div>}
          </div>
        </>); })()}
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
      {tab === "pending" && <PendingTypes types={types} reload={() => api.get<typeof types>("/api/refs/machine-types").then(setTypes)} />}
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
              <td className="row">{can(me, "glossary", 2) && g.status === "machine" && <button className="btn ghost" onClick={async () => { const z = (await askPrompt("Перевод CN:", g.term_zh)); if (z !== null) api.post(`/api/refs/glossary/${g.id}/confirm?term_zh=${encodeURIComponent(z)}`).then(loadGl); }}>подтвердить</button>}
                {can(me, "glossary", 2) && <button className="btn ghost" onClick={() => api.del(`/api/refs/glossary/${g.id}`).then(loadGl)}>×</button>}</td></tr>)}
              {gl.length === 0 && <tr><td colSpan={5} className="muted">Пусто</td></tr>}</tbody></table>
        </div>
      )}
      {tab === "templates" && <TemplateEditor editor={editor} types={types} onTypesChanged={() => api.get<typeof types>("/api/refs/machine-types").then(setTypes)} />}
    </>
  );
}

type CmpExec = { id: string; name: string; brand_name: string; tech_params: { code?: string; name_ru: string; unit: string; value_ru: string; section?: string }[];
  model: { model: string; trademark?: string; brand_name: string; machine_type: string } | null };

/** Сравнение исполнений (в том числе разных брендов): параметры по коду или названию, различия подсвечены. */
function CompareTable({ ids, onClose }: { ids: string[]; onClose: () => void }) {
  const [list, setList] = useState<CmpExec[]>([]);
  const [onlyDiff, setOnlyDiff] = useState(false);
  useEffect(() => { Promise.all(ids.map((id) => api.get<CmpExec>(`/api/catalog/executions/${id}`))).then(setList).catch(() => setList([])); }, [ids.join(",")]);
  const key = (r: { code?: string; name_ru: string }) => (r.code || "").trim() || r.name_ru.trim().toLowerCase();
  const rows: { key: string; name: string; unit: string; section: string }[] = [];
  const seen = new Set<string>();
  for (const e of list) for (const r of e.tech_params || []) {
    const k = key(r);
    if (!k || seen.has(k) || !(r.name_ru || "").trim()) continue;
    seen.add(k); rows.push({ key: k, name: r.name_ru, unit: (r.unit || "").split(" / ")[0], section: r.section || "" });
  }
  const val = (e: CmpExec, k: string) => (e.tech_params || []).find((r) => key(r) === k)?.value_ru || "";
  const shown = rows.filter((r) => { const vs = list.map((e) => val(e, r.key)); return vs.some(Boolean) && (!onlyDiff || new Set(vs).size > 1); });
  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between" }}><h2 style={{ margin: 0 }}>Сравнение исполнений</h2>
        <div className="row"><label className="row"><input type="checkbox" checked={onlyDiff} onChange={(e) => setOnlyDiff(e.target.checked)} /> только различия</label><button className="btn ghost" onClick={onClose}>закрыть</button></div></div>
      <div style={{ overflowX: "auto", marginTop: 6 }}>
        <table style={{ fontSize: 12 }}>
          <thead><tr><th>Параметр</th><th>Ед.</th>{list.map((e) => <th key={e.id} style={{ minWidth: 140 }}>{e.model ? `${e.model.trademark ? e.model.trademark + " " : ""}${e.model.model}` : ""}<div className="muted" style={{ fontWeight: 400 }}>{e.name}{e.model?.brand_name ? ` · ${e.model.brand_name}` : ""}</div></th>)}</tr></thead>
          <tbody>{shown.map((r) => { const vs = list.map((e) => val(e, r.key)); const diff = new Set(vs).size > 1; return (
            <tr key={r.key} style={{ background: diff ? "#f4f4f4" : undefined }}><td>{r.name}</td><td className="muted">{r.unit}</td>{vs.map((v, i) => <td key={i}>{v || <span className="muted">—</span>}</td>)}</tr>); })}</tbody>
        </table>
      </div>
      <div className="muted" style={{ fontSize: 11 }}>Жёлтым — параметры, которые различаются.</div>
    </div>
  );
}

/** Новые типы техники на утверждение (D-071): утвердить (можно исправить название) или отклонить с переводом позиций на другой тип. */
function PendingTypes({ types, reload }: { types: { id: string; name: string; status: string }[]; reload: () => void }) {
  const pend = types.filter((t) => t.status === "pending");
  const [names, setNames] = useState<Record<string, string>>({});
  const [repl, setRepl] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState("");
  if (!pend.length) return null;
  const run = (p: Promise<unknown>, ok: string) => p.then(() => { setMsg(ok); reload(); }).catch((e) => setMsg((e as Error).message));
  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <h3 style={{ marginTop: 0 }}>Новые типы техники</h3>
      <p className="muted" style={{ marginTop: 0 }}>Предложены менеджерами при вводе заявки. Пока тип не утверждён и у него нет своего шаблона, позиции заполняются по типовому шаблону.</p>
      {msg && <div className="alert ok">{msg}</div>}
      <table><thead><tr><th>Тип (можно исправить)</th><th>Если отклонить — перевести позиции на</th><th /></tr></thead>
        <tbody>{pend.map((t) => (
          <tr key={t.id}>
            <td><input value={names[t.id] ?? t.name} style={{ width: "100%" }} onChange={(e) => setNames({ ...names, [t.id]: e.target.value })} /></td>
            <td><select value={repl[t.id] || ""} onChange={(e) => setRepl({ ...repl, [t.id]: e.target.value })}>
              <option value="">— не переводить (если позиций нет) —</option>
              {types.filter((x) => x.status !== "pending").map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></td>
            <td className="row" style={{ gap: 6 }}>
              <button className="btn" onClick={() => run(api.post(`/api/refs/machine-types/${t.id}/approve`, { name: names[t.id] ?? t.name }), `Тип «${names[t.id] ?? t.name}» утверждён`)}>Утвердить</button>
              <button className="btn ghost" onClick={() => run(api.post(`/api/refs/machine-types/${t.id}/reject`, { replace_with: repl[t.id] || null }), `Тип «${t.name}» отклонён`)}>Отклонить</button>
            </td>
          </tr>))}</tbody></table>
    </div>);
}


type EqModel = { id: string; model: string; trademark?: string; brand_name: string; status: string; machine_type: string; factory: string;
  executions: { id: string; name: string; brand_name: string; status: string; params: number; images: number }[] };

/** «Новый вид» (D-169): каталог оборудования как витрина — слева типы техники, сверху бренды и поиск, справа карточки моделей
 *  с исполнениями, числом параметров и фото, метка «на проверке». Данные — то же дерево /api/catalog/tree. */
function EqBrowser({ models, empty, onOpen }: { models: EqModel[]; empty: boolean; onOpen: (id: string) => void }) {
  const [type, setType] = useState("");
  const [brand, setBrand] = useState("");
  const [q, setQ] = useState("");
  const brandOf = (m: EqModel) => (m.trademark || m.factory || "").trim();
  const types = [...new Set(models.map((m) => m.machine_type || "без типа"))].sort((a, b) => a.localeCompare(b, "ru"));
  const byType = models.filter((m) => !type || (m.machine_type || "без типа") === type);
  const brands = [...new Set(byType.map(brandOf))].sort((a, b) => a.localeCompare(b, "ru"));
  const ql = q.trim().toLowerCase();
  const shown = byType.filter((m) => (!brand || brandOf(m) === brand) && (!ql || [designation(m), m.brand_name, m.factory, m.machine_type, ...m.executions.map((e) => e.name)].join(" ").toLowerCase().includes(ql)))
    .sort((a, b) => designation(a).localeCompare(designation(b), "ru"));
  if (empty) return <div className="card st-box st-empty"><div className="st-title">Каталог оборудования пуст</div><div className="muted">Модели появляются после отправки КП (менеджер предлагает, КД/ЗГД подтверждает) или через «+ Добавить оборудование».</div></div>;
  const count = (t: string) => models.filter((m) => (m.machine_type || "без типа") === t).length;
  return (
    <div className="eq-browser">
      <nav className="eq-types card" aria-label="Тип техники">
        <div className="eq-types-h">Тип техники</div>
        <button type="button" className={!type ? "on" : ""} aria-pressed={!type} onClick={() => { setType(""); setBrand(""); }}>Все типы<span>{models.length}</span></button>
        {types.map((t) => <button key={t} type="button" className={type === t ? "on" : ""} aria-pressed={type === t} onClick={() => { setType(t); setBrand(""); }}>{t}<span>{count(t)}</span></button>)}
      </nav>
      <div className="eq-main">
        <div className="eq-filters">
          <input className="eq-q" placeholder="Модель, исполнение, завод…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Поиск по моделям" />
          {brands.length > 1 && <div className="pc-groups" role="group" aria-label="Бренд">
            <button type="button" className={!brand ? "on" : ""} aria-pressed={!brand} onClick={() => setBrand("")}>Все бренды</button>
            {brands.map((b) => <button key={b} type="button" className={brand === b ? "on" : ""} aria-pressed={brand === b} onClick={() => setBrand(brand === b ? "" : b)}>{b}</button>)}
          </div>}
        </div>
        <div className="pc-count">Моделей: <b>{shown.length}</b>{(q || brand || type) && <button type="button" className="dl-chip-all" onClick={() => { setQ(""); setBrand(""); setType(""); }}>сбросить отбор</button>}</div>
        {shown.length === 0 ? <div className="card st-box st-empty"><div className="st-title">По отбору моделей нет</div><div className="muted">Измените тип, бренд или строку поиска.</div></div> : (
          <div className="eq-grid">{shown.map((m) => {
            const pend = m.status === "pending" || m.executions.some((e) => e.status === "pending");
            const photos = m.executions.reduce((a, e) => a + (e.images || 0), 0);
            const params = Math.max(0, ...m.executions.map((e) => e.params || 0));
            return (
              <button key={m.id} type="button" className="eq-card" onClick={() => onOpen(m.id)}>
                <span className="eq-card-top"><span>{m.machine_type || "без типа"}</span>{pend && <i className="badge warn">на проверке</i>}</span>
                <b className="eq-card-title">{designation(m)}</b>
                {m.brand_name && <span className="eq-card-brand">под брендом: {m.brand_name}</span>}
                <span className="eq-card-fac">{m.factory}</span>
                <span className="eq-card-ex">{m.executions.slice(0, 3).map((e) => <i key={e.id}>{e.name.replace(designation(m), "").replace(/^\s*[—-]\s*/, "") || e.name}</i>)}{m.executions.length > 3 && <i>+{m.executions.length - 3}</i>}</span>
                <span className="eq-card-facts"><span>{m.executions.length} {m.executions.length === 1 ? "исполнение" : m.executions.length < 5 ? "исполнения" : "исполнений"}</span>{params > 0 && <span>{params} парам.</span>}<span>{photos ? `${photos} фото` : "без фото"}</span></span>
              </button>);
          })}</div>)}
      </div>
    </div>);
}
