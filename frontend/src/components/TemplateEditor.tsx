import { Fragment, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { UndoButton, useUndoHistory } from "../undo";
import { addBlock, blockOf, blockRange, insertAfter, moveBlock, moveRow, removeBlock, renameBlock, renumber } from "../rowOps";
import { askConfirm, askPrompt } from "./Dialogs";

export type TplRow = { id?: string; no: number; code: string; name_ru: string; name_zh: string; unit: string; required: boolean; section: string; _k?: string };
type Parsed = { rows: TplRow[]; blocks: number; warnings: string[]; sheet: string };
type Type = { id: string; name: string; status?: string };

const HEADER = "__header__";
let seq = 0;
const key = () => `n${++seq}`;
const withKeys = (rows: TplRow[]) => rows.map((r) => ({ ...r, _k: r._k || r.id || key() }));
const empty = (section = ""): TplRow => ({ no: 0, code: "", name_ru: "", name_zh: "", unit: "", required: false, section, _k: key() });

/** Редактор шаблона технических характеристик (D-070): блоки, порядок строк, вставка в любое место, Excel туда и обратно.
 *  Клавиши в поле «Параметр»: Enter — новая строка ниже, Alt+↑ / Alt+↓ — переместить строку. */
export default function TemplateEditor({ editor, types, onTypesChanged }: { editor: boolean; types: Type[]; onTypesChanged: () => void }) {
  const [typeId, setTypeId] = useState(() => (new URLSearchParams(window.location.search).get("tpl") === "header" ? HEADER : ""));
  const [rows, setRows] = useState<TplRow[]>([]);
  const [saved, setSaved] = useState("[]");
  const [inherited, setInherited] = useState(false);
  const [closed, setClosed] = useState<Record<string, boolean>>({});
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [pending, setPending] = useState<Parsed | null>(null);
  const [focusKey, setFocusKey] = useState<string | null>(null);
  const [newBlock, setNewBlock] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const inputs = useRef<Record<string, HTMLInputElement | null>>({});
  const history = useUndoHistory(rows, setRows, typeId);
  const dirty = JSON.stringify(rows.map(({ _k, ...r }) => r)) !== saved;

  const isHeader = typeId === HEADER;   // D-071: шапка заявки — общая для всех сделок, над шаблоном типа
  const load = (id = typeId) => api.get<{ rows: TplRow[]; inherited?: boolean }>(id === HEADER ? "/api/refs/request-header" : `/api/refs/tech-param-templates?machine_type_id=${id}`).then((r) => {
    const rs = withKeys(r.rows.map((x) => ({ ...x, code: x.code || "", name_zh: x.name_zh || "", unit: x.unit || "", section: x.section || "" })));
    setRows(rs); setSaved(JSON.stringify(rs.map(({ _k, ...x }) => x))); setInherited(!!r.inherited); history.accept(); setPending(null);
  }).catch((e) => setMsg({ ok: false, text: e.message }));
  useEffect(() => { load(typeId); }, [typeId]);
  useEffect(() => { if (focusKey) { inputs.current[focusKey]?.focus(); setFocusKey(null); } }, [focusKey, rows]);
  useEffect(() => {   // уход со страницы с несохранёнными правками
    const h = (e: BeforeUnloadEvent) => { if (dirty) { e.preventDefault(); e.returnValue = ""; } };
    window.addEventListener("beforeunload", h); return () => window.removeEventListener("beforeunload", h);
  }, [dirty]);

  const pickType = async (id: string) => { if (dirty && !(await askConfirm("Есть несохранённые правки шаблона. Перейти без сохранения?"))) return; setTypeId(id); };
  const upd = (i: number, patch: Partial<TplRow>) => setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const ins = (i: number) => { const r = empty(); setRows(insertAfter(rows, i, r)); setFocusKey(r._k!); };
  const mv = (i: number, d: -1 | 1) => { const k = rows[i]._k!; setRows(moveRow(rows, i, d)); setFocusKey(k); };
  const onKey = (i: number) => (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") { e.preventDefault(); ins(i); }
    else if (e.altKey && e.key === "ArrowUp") { e.preventDefault(); mv(i, -1); }
    else if (e.altKey && e.key === "ArrowDown") { e.preventDefault(); mv(i, 1); }
  };

  async function save() {
    const named = rows.filter((r) => r.name_ru.trim());
    if (named.length !== rows.length && !(await askConfirm(`Пустых строк: ${rows.length - named.length} — они не сохранятся. Продолжить?`))) return;
    try {
      await api.put(isHeader ? "/api/refs/request-header" : `/api/refs/tech-param-templates?machine_type_id=${typeId}`, renumber(named).map(({ _k, ...r }) => r));
      setMsg({ ok: true, text: isHeader ? "Шапка заявки сохранена — новые и открытые заново заявки показывают её" : inherited ? "Сохранён отдельный шаблон для этого типа машины" : "Шаблон сохранён" }); load();
    } catch (e) { setMsg({ ok: false, text: (e as Error).message }); }
  }
  async function upload(f: File) {
    const fd = new FormData(); fd.append("file", f);
    try { setPending(await api.form<Parsed>("/api/refs/tech-param-templates/parse", fd)); setMsg(null); }
    catch (e) { setMsg({ ok: false, text: (e as Error).message }); }
    if (fileRef.current) fileRef.current.value = "";
  }
  const applyImport = (mode: "replace" | "append") => {
    if (!pending) return;
    const add = withKeys(pending.rows.map((r) => ({ ...r, no: 0, _k: undefined })));
    setRows(mode === "replace" ? add : [...rows, ...add]); setPending(null);
    setMsg({ ok: true, text: `Из файла: ${pending.rows.length} параметров. Проверьте и нажмите «Сохранить шаблон».` });
  };
  const copyFrom = (id: string) => {
    if (id === "-") return;
    api.get<{ rows: TplRow[] }>(`/api/refs/tech-param-templates?machine_type_id=${id === "general" ? "" : id}`).then(async (r) => {
      if (rows.length && !(await askConfirm("Заменить строки на экране строками выбранного шаблона? Сохранится только после «Сохранить шаблон»."))) return;
      setRows(withKeys(r.rows.map((x) => ({ ...x, id: undefined, _k: undefined })))); setMsg({ ok: true, text: "Строки скопированы — поправьте и сохраните." });
    });
  };

  const blocks: number[] = rows.map((_, i) => i).filter((i) => i === 0 || blockOf(rows[i - 1]) !== blockOf(rows[i]));
  const allClosed = blocks.length > 0 && blocks.every((i) => closed[blockOf(rows[i])]);
  const curName = isHeader ? "Шапка заявки" : typeId ? types.find((t) => t.id === typeId)?.name : "Общий шаблон";

  return (
    <div className="card te">
      <div className="te-bar">
        <label className="te-type">Тип машины
          <select value={typeId} onChange={(e) => pickType(e.target.value)}>
            <option value={HEADER}>Шапка заявки (для всех сделок)</option>
            <option value="">Общий (типовой) шаблон техники</option>
            {types.map((t) => <option key={t.id} value={t.id}>{t.name}{t.status === "pending" ? " (на подтверждение)" : ""}</option>)}
          </select></label>
        {editor && <button className="btn ghost" onClick={async () => { const n = (await askPrompt("Новый тип машины:")); if (n) api.post("/api/refs/machine-types", { name: n }).then(onTypesChanged); }}>+ тип машины</button>}
        <span className="te-spacer" />
        <a className="btn ghost" href={isHeader ? "/api/refs/tech-param-templates/file?kind=header" : `/api/refs/tech-param-templates/file?machine_type_id=${typeId}`}>⬇ Скачать в Excel</a>
        {editor && <><button className="btn ghost" onClick={() => fileRef.current?.click()}>⬆ Загрузить из Excel</button>
          <input ref={fileRef} type="file" accept=".xlsx,.xlsm" hidden onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
          {!isHeader && <select className="te-copy" value="-" onChange={(e) => copyFrom(e.target.value)} title="взять строки другого шаблона за основу">
            <option value="-">Скопировать из…</option><option value="general">общего шаблона</option>
            {types.filter((t) => t.id !== typeId).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</select>}</>}
      </div>

      <p className="muted te-help">{isHeader
        ? <>Шапка заявки — поля, общие для всех сделок (сведения о запросе, коммерческие условия, сроки…). В форме позиции она стоит над таблицей характеристик выбранного типа техники. Тип техники, количество и пункт поставки в шапке есть всегда.</>
        : inherited
        ? <>У типа «{curName}» своего шаблона нет — показан общий. После «Сохранить шаблон» у типа появится свой, общий не изменится.</>
        : <>Шаблон «{curName}»: перечень параметров по блокам и их обязательность (правят КД и ЗГД). Новые позиции получают эти строки; менеджер может изменить обязательность в своей сделке.</>}
        {editor && <> В поле «Параметр»: <kbd>Enter</kbd> — новая строка ниже, <kbd>Alt</kbd>+<kbd>↑</kbd>/<kbd>↓</kbd> — переместить. Excel: колонки «Блок, Код, Параметр, 参数 (CN), Ед., Обязательный» или свой файл — колонки и блоки распознаются по заголовкам.</>}</p>

      {pending && (
        <div className="te-import">
          <b>Файл прочитан (лист «{pending.sheet}»): {pending.rows.length} параметров, блоков: {pending.blocks}.</b>
          {pending.warnings.map((w, i) => <div key={i} className="muted">{w}</div>)}
          <div className="row" style={{ gap: 8, marginTop: 6 }}>
            <button className="btn" onClick={() => applyImport("replace")}>Заменить строки шаблона</button>
            <button className="btn secondary" onClick={() => applyImport("append")}>Добавить в конец</button>
            <button className="btn ghost" onClick={() => setPending(null)}>Отмена</button>
          </div>
        </div>)}
      {msg && <div className={"alert " + (msg.ok ? "ok" : "error")}>{msg.text}</div>}

      <div className="te-tools">
        <span className="muted">Блоков: <b>{blocks.length}</b> · параметров: <b>{rows.length}</b> · обязательных: <b>{rows.filter((r) => r.required).length}</b></span>
        <span className="te-spacer" />
        <button className="btn ghost" onClick={() => setClosed(allClosed ? {} : Object.fromEntries(blocks.map((i) => [blockOf(rows[i]), true])))}>{allClosed ? "развернуть все" : "свернуть все"}</button>
        {editor && <UndoButton h={history} />}
      </div>

      <table className="te-table">
        <colgroup><col style={{ width: 44 }} /><col /><col style={{ width: "24%" }} /><col style={{ width: 90 }} /><col style={{ width: 64 }} /><col style={{ width: 58 }} />{editor && <col style={{ width: 132 }} />}</colgroup>
        <thead><tr><th>№</th><th>Параметр</th><th>参数 (CN)</th><th>Ед.</th><th title="обязательный параметр">Обяз.</th><th>Код</th>{editor && <th />}</tr></thead>
        <tbody>
          {rows.map((r, i) => {
            const b = blockOf(r);
            const start = blocks.includes(i);
            const [bs, be] = start ? blockRange(rows, i) : [0, 0];
            const inB = start ? rows.slice(bs, be) : [];
            const head = start && (
              <tr className="te-block">
                <td colSpan={editor ? 7 : 6}>
                  <div className="te-block-row">
                    <button className="te-caret" onClick={() => setClosed({ ...closed, [b]: !closed[b] })}>{closed[b] ? "▸" : "▾"}</button>
                    {editor ? <input className="te-block-name" value={r.section} placeholder="название блока" onChange={(e) => setRows(renameBlock(rows, i, e.target.value))} />
                      : <b>{b}</b>}
                    <span className="muted">{inB.length} парам.{inB.some((x) => x.required) ? ` · обязательных ${inB.filter((x) => x.required).length}` : ""}</span>
                    {editor && <label className="muted te-all"><input type="checkbox" checked={inB.every((x) => x.required)}
                      onChange={(e) => setRows(rows.map((x, k) => (k >= bs && k < be ? { ...x, required: e.target.checked } : x)))} /> все обязательные</label>}
                    <span className="te-spacer" />
                    {editor && <span className="te-acts">
                      <button title="блок выше" disabled={bs === 0} onClick={() => setRows(moveBlock(rows, i, -1))}>↑</button>
                      <button title="блок ниже" disabled={be >= rows.length} onClick={() => setRows(moveBlock(rows, i, 1))}>↓</button>
                      <button title="параметр в конец блока" onClick={() => ins(be - 1)}>+ параметр</button>
                      <button title="новый блок после этого" onClick={() => setNewBlock(String(i))}>+ блок</button>
                      <button title="удалить блок" className="te-del" onClick={async () => { if ((await askConfirm(`Удалить блок «${b}» (${inB.length} парам.)?`))) setRows(removeBlock(rows, i)); }}>×</button>
                    </span>}
                  </div>
                </td>
              </tr>);
            if (closed[b]) return <Fragment key={r._k}>{head}</Fragment>;
            return (
              <Fragment key={r._k}>{head}
                <tr className={r.name_ru.trim() ? "" : "te-empty"}>
                  <td className="muted">{i + 1}</td>
                  <td>{editor ? <input ref={(el) => { inputs.current[r._k!] = el; }} value={r.name_ru} placeholder="параметр" onKeyDown={onKey(i)} onChange={(e) => upd(i, { name_ru: e.target.value })} /> : r.name_ru}</td>
                  <td>{editor ? <input value={r.name_zh} onChange={(e) => upd(i, { name_zh: e.target.value })} /> : r.name_zh}</td>
                  <td>{editor ? <input value={r.unit} onChange={(e) => upd(i, { unit: e.target.value })} /> : r.unit}</td>
                  <td style={{ textAlign: "center" }}><input type="checkbox" checked={r.required} disabled={!editor} onChange={(e) => upd(i, { required: e.target.checked })} /></td>
                  <td className="muted te-code" title={r.code ? "поле опросника RFQ" : ""}>{r.code}</td>
                  {editor && <td><span className="te-acts">
                    <button title="выше (Alt+↑)" disabled={i === 0} onClick={() => mv(i, -1)}>↑</button>
                    <button title="ниже (Alt+↓)" disabled={i === rows.length - 1} onClick={() => mv(i, 1)}>↓</button>
                    <button title="вставить строку ниже (Enter)" onClick={() => ins(i)}>+</button>
                    <button title="удалить строку" className="te-del" onClick={() => setRows(rows.filter((_, j) => j !== i))}>×</button>
                  </span></td>}
                </tr>
              </Fragment>);
          })}
          {rows.length === 0 && <tr><td colSpan={editor ? 7 : 6} className="muted">Шаблон пуст — добавьте блок или загрузите файл Excel.</td></tr>}
        </tbody>
      </table>
      {editor && newBlock !== null && (
        <div className="te-newblock">
          <input autoFocus placeholder="название нового блока, например «Двигатель»" onKeyDown={(e) => {
            const v = (e.target as HTMLInputElement).value.trim();
            if (e.key === "Enter" && v) { const r = empty(v); setRows(addBlock(rows, v, r, newBlock === "end" ? undefined : Number(newBlock))); setNewBlock(null); setFocusKey(r._k!); }
            if (e.key === "Escape") setNewBlock(null);
          }} />
          <span className="muted">Enter — создать, Esc — отмена</span>
        </div>)}
      {editor && <div className="row" style={{ marginTop: 8 }}><button className="btn ghost" onClick={() => setNewBlock("end")}>+ блок в конец</button></div>}

      {editor && dirty && (
        <div className="te-save">
          <button className="btn" onClick={save}>Сохранить шаблон</button>
          <button className="btn ghost" onClick={async () => { if ((await askConfirm("Отменить все несохранённые правки?"))) load(); }}>Отменить правки</button>
          <span className="muted">есть несохранённые изменения{inherited ? " — сохранение создаст отдельный шаблон для типа" : ""}</span>
        </div>)}
    </div>
  );
}
