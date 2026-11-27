import { useEffect, useState } from "react";
import { api } from "../api";
import { Empty, ErrorState, Loading } from "./States";

/** D-200 (J-c): справочник узлов и агрегатов — общий (для любой техники) и по моделям ГШО. Только новый вид.
 *  Названия на трёх языках — для каталогов завода на английском и китайском (загрузка J-i). */
type Asm = { id: string; model_id: string | null; code: string; name_ru: string; name_en: string; name_cn: string; position: number; is_active: boolean; scope: string; used: number };
type Model = { id: string; title: string };

export default function AssembliesBlock() {
  const [models, setModels] = useState<Model[]>([]);
  const [model, setModel] = useState("");
  const [d, setD] = useState<{ items: Asm[]; can_edit: boolean } | null>(null);
  const [err, setErr] = useState("");
  const [ed, setEd] = useState<Partial<Asm> | null>(null);
  const [msg, setMsg] = useState("");
  useEffect(() => {
    api.get<{ types: { machine_type: string; models: { id: string; model: string; trademark?: string }[] }[] }[]>("/api/catalog/tree")
      .then((t) => setModels(t.flatMap((f) => f.types.flatMap((ty) => ty.models.map((m) => ({ id: m.id, title: `${ty.machine_type} ${m.trademark ? m.trademark + " " : ""}${m.model}` }))))))
      .catch(() => setModels([]));
  }, []);
  const load = () => api.get<{ items: Asm[]; can_edit: boolean }>(`/api/parts/assemblies?all=1${model ? `&model_id=${model}` : ""}`).then((r) => { setD(r); setErr(""); }).catch((e) => setErr((e as Error).message));
  useEffect(() => { load(); }, [model]);   // eslint-disable-line react-hooks/exhaustive-deps
  if (err && !d) return <ErrorState hint={err} onRetry={load} />;
  if (!d) return <Loading />;
  const save = async () => {
    if (!ed) return;
    const body = { model_id: ed.id ? ed.model_id ?? null : model || null, parent_id: null, code: ed.code || "", name_ru: ed.name_ru || "", name_en: ed.name_en || "", name_cn: ed.name_cn || "",
      position: ed.position || 0, is_active: ed.is_active ?? true };
    try { await (ed.id ? api.put(`/api/parts/assemblies/${ed.id}`, body) : api.post("/api/parts/assemblies", body)); setEd(null); setMsg("Сохранено"); load(); }
    catch (e) { setMsg((e as Error).message); }
  };
  const shown = d.items.filter((a) => (model ? true : a.scope === "general"));
  return (
    <div className="card">
      <p className="muted">Узлы для применимости запчастей. Общий справочник подходит для любой техники; у модели ГШО можно завести свои узлы. Переименование узла меняет его название во всех связях.</p>
      <div className="row asm-tools">
        <label className="f">Модель<select value={model} onChange={(e) => setModel(e.target.value)}>
          <option value="">— только общий справочник —</option>{models.map((m) => <option key={m.id} value={m.id}>{m.title}</option>)}</select></label>
        {d.can_edit && !ed && <button className="btn add primary" onClick={() => setEd({ is_active: true })}>{model ? "Узел модели" : "Узел"}</button>}
      </div>
      {msg && <div className="alert info" role="status">{msg}</div>}
      {ed && <div className="card sv-form">
        <div className="grid cols-3">
          <label className="f">Название *<input value={ed.name_ru || ""} onChange={(e) => setEd({ ...ed, name_ru: e.target.value })} /></label>
          <label className="f">English<input value={ed.name_en || ""} onChange={(e) => setEd({ ...ed, name_en: e.target.value })} /></label>
          <label className="f">中文<input value={ed.name_cn || ""} onChange={(e) => setEd({ ...ed, name_cn: e.target.value })} /></label>
          <label className="f">Код<input value={ed.code || ""} onChange={(e) => setEd({ ...ed, code: e.target.value })} /></label>
          <label className="f">Порядок<input type="number" value={ed.position || 0} onChange={(e) => setEd({ ...ed, position: Number(e.target.value) || 0 })} /></label>
          <label className="row"><input type="checkbox" checked={ed.is_active ?? true} onChange={(e) => setEd({ ...ed, is_active: e.target.checked })} /> используется</label>
        </div>
        <div className="row"><button className="btn primary" disabled={!(ed.name_ru || "").trim()} onClick={save}>Сохранить</button><button className="btn ghost" onClick={() => setEd(null)}>отмена</button></div>
      </div>}
      {shown.length === 0 ? <Empty title="Узлов нет" /> : (
        <table className="pc-table nv-cards sv-table">
          <thead><tr><th>Узел</th><th>English · 中文</th><th>Где</th><th>Связей</th><th /></tr></thead>
          <tbody>{shown.map((a) => (
            <tr key={a.id} className={a.is_active ? "" : "arch"}>
              <td className="pc-name">{a.name_ru}{a.code && <div className="pc-sub pc-mono">{a.code}</div>}</td>
              <td data-label="Переводы">{[a.name_en, a.name_cn].filter(Boolean).join(" · ") || <span className="muted">—</span>}</td>
              <td data-label="Где">{a.scope === "model" ? "модель" : "общий"}{!a.is_active && <span className="badge">не используется</span>}</td>
              <td data-label="Связей">{a.used}</td>
              <td>{d.can_edit && <button className="btn ghost" onClick={() => setEd(a)}>изменить</button>}</td>
            </tr>))}</tbody>
        </table>)}
    </div>);
}
