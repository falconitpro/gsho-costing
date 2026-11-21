import { useEffect, useRef, useState } from "react";
import { api, fmtDate } from "../api";
import { askConfirm } from "./Dialogs";

/** D-127: обсуждения сделки. Одна лента на сделку; раздел показывает свою часть, вкладка «Лента» — всё с фильтрами.
 *  Сообщение: текст, файлы и скриншоты (вставка из буфера), ответ, @упоминание, метки «вопрос / ответ», «ждём ответа до …», «зафиксировать». */

export type FeedItem = {
  id: string; kind: string; direction: string; section: string; subject: string; body: string; counterparty: string; at: string;
  author_id: string | null; author: string; parent_id: string | null; tags: string[]; mentions: { id: string; name: string }[];
  awaiting: boolean; due_on: string | null; overdue: boolean; resolved_at: string | null; pinned: boolean; edited: boolean; deleted: boolean;
  files: { id: string; name: string; size: number; url: string }[]; can_edit: boolean; can_delete: boolean;
};
export type Feed = { items: FeedItem[]; pinned: FeedItem[]; counts: Record<string, number>; open: Record<string, number>;
  sections: Record<string, string>; writable: string[]; tags: Record<string, string> };

const KIND: Record<string, string> = { comment: "", mail_in: "письмо получено", mail_out: "письмо отправлено", system: "событие", file: "файл", wecom: "WeCom" };
const QUESTION_TAGS = ["q_client", "q_factory", "q_carrier"];

function plusWorkdays(n: number): string {
  const d = new Date();
  while (n > 0) { d.setDate(d.getDate() + 1); if (d.getDay() !== 0 && d.getDay() !== 6) n--; }
  return d.toISOString().slice(0, 10);
}

export function DealFeed({ dealId, section, full = false, onChange }: { dealId: string; section?: string; full?: boolean; onChange?: (f: Feed) => void }) {
  const [feed, setFeed] = useState<Feed | null>(null);
  const [fSection, setFSection] = useState<string>(section ?? "all");
  const [fTag, setFTag] = useState("");
  const [onlyMsg, setOnlyMsg] = useState(!full);
  const [onlyOpen, setOnlyOpen] = useState(false);
  const [q, setQ] = useState("");
  const [err, setErr] = useState("");
  const reload = () => {
    const p = new URLSearchParams();
    if (fSection !== "all") p.set("section", fSection);
    if (fTag) p.set("tag", fTag);
    if (onlyMsg) p.set("messages", "true");
    if (onlyOpen) p.set("open", "true");
    if (q.trim()) p.set("q", q.trim());
    api.get<Feed>(`/api/deals/${dealId}/feed?${p}`).then((f) => { setFeed(f); onChange?.(f); }).catch((e) => setErr(String(e.message || e)));
  };
  useEffect(reload, [dealId, fSection, fTag, onlyMsg, onlyOpen]);
  const take = (f: Feed) => { reload(); return f; };
  if (!feed) return <div className="muted">{err || "Загрузка обсуждения…"}</div>;
  const sec = fSection === "all" ? "" : fSection;
  const canWrite = feed.writable.includes(sec);
  const byId = new Map(feed.items.map((i) => [i.id, i]));
  const roots = feed.items.filter((i) => !i.parent_id || !byId.has(i.parent_id));
  const replies = (id: string) => feed.items.filter((i) => i.parent_id === id).reverse();
  const pinned = feed.pinned.filter((p) => fSection === "all" || p.section === fSection);
  return (
    <div className="feed">
      {full && (
        <div className="feed-filters">
          <select value={fSection} onChange={(e) => setFSection(e.target.value)} title="Раздел сделки">
            <option value="all">Все разделы</option>
            {Object.entries(feed.sections).map(([k, v]) => <option key={k} value={k}>{v}{feed.counts[k] ? ` · ${feed.counts[k]}` : ""}{feed.open[k] ? ` · ждём ${feed.open[k]}` : ""}</option>)}
          </select>
          <select value={fTag} onChange={(e) => setFTag(e.target.value)} title="Метка">
            <option value="">Все метки</option>
            {Object.entries(feed.tags).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <label><input type="checkbox" checked={onlyMsg} onChange={(e) => setOnlyMsg(e.target.checked)} /> только сообщения и письма</label>
          <label><input type="checkbox" checked={onlyOpen} onChange={(e) => setOnlyOpen(e.target.checked)} /> ждём ответа</label>
          <input placeholder="поиск по тексту" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && reload()} />
        </div>
      )}
      {pinned.length > 0 && (
        <div className="feed-pinned">
          <b>Зафиксировано</b>
          {pinned.map((p) => <div key={p.id}>📌 {p.body || p.subject} <span className="muted">· {p.author} · {fmtDate(p.at)}{full && fSection === "all" ? ` · ${feed.sections[p.section] ?? ""}` : ""}</span></div>)}
        </div>
      )}
      {canWrite && <Composer dealId={dealId} section={sec} tags={feed.tags} onSent={(f) => take(f)} />}
      {!canWrite && <div className="muted">Вы можете читать обсуждение; писать в него могут участники сделки.</div>}
      {roots.length === 0 && <div className="muted">{full ? "Ничего не найдено" : "Сообщений пока нет — задайте вопрос, приложите скриншот или письмо клиента"}</div>}
      {roots.map((m) => (
        <div key={m.id} className="feed-thread">
          <Msg m={m} dealId={dealId} feed={feed} showSection={full && fSection === "all"} onDone={take} />
          {replies(m.id).map((r) => <div key={r.id} className="feed-reply"><Msg m={r} dealId={dealId} feed={feed} showSection={false} onDone={take} /></div>)}
          {canWrite && m.kind !== "system" && !m.deleted && <Composer dealId={dealId} section={m.section} tags={feed.tags} parent={m} onSent={(f) => take(f)} />}
        </div>
      ))}
    </div>
  );
}

function Msg({ m, dealId, feed, showSection, onDone }: { m: FeedItem; dealId: string; feed: Feed; showSection: boolean; onDone: (f: Feed) => void }) {
  const [edit, setEdit] = useState<string | null>(null);
  const url = `/api/deals/${dealId}/feed/messages/${m.id}`;
  const canAct = feed.writable.includes(m.section);
  return (
    <div className={"feed-msg" + (m.kind === "system" ? " feed-sys" : "")}>
      <div className="feed-head">
        <b>{m.author || (m.kind === "system" ? "Система" : "")}</b>
        {KIND[m.kind] && <span className="badge">{KIND[m.kind]}</span>}
        {showSection && <span className="badge">{feed.sections[m.section] ?? "Общее"}</span>}
        {m.tags.map((t) => <span key={t} className={"badge" + (t.startsWith("q_") ? " warn" : t.startsWith("a_") ? " ok" : "")}>{feed.tags[t] || t}</span>)}
        <span className="muted">{new Date(m.at).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}{m.edited ? " · исправлено" : ""}</span>
        {m.awaiting && !m.resolved_at && <span className={"badge " + (m.overdue ? "bad" : "warn")}>ждём ответа{m.due_on ? ` до ${fmtDate(m.due_on)}` : ""}</span>}
        {m.awaiting && m.resolved_at && <span className="badge ok">ответ получен</span>}
        {m.pinned && <span title="Зафиксировано">📌</span>}
      </div>
      {(m.subject || m.counterparty) && <div className="feed-subj">{m.subject}{m.counterparty && <span className="muted"> · {m.counterparty}</span>}</div>}
      {m.deleted ? <div className="muted">сообщение удалено</div> : edit !== null ? (
        <div className="feed-compose">
          <textarea value={edit} onChange={(e) => setEdit(e.target.value)} rows={3} />
          <div className="row"><button className="btn" onClick={() => api.patch<Feed>(url, { body: edit }).then((f) => { setEdit(null); onDone(f); })}>Сохранить</button>
            <button className="btn ghost" onClick={() => setEdit(null)}>Отмена</button></div>
        </div>
      ) : <div className="feed-body">{m.body}</div>}
      {m.mentions.length > 0 && <div className="muted">для: {m.mentions.map((x) => "@" + x.name).join(", ")}</div>}
      {m.files.length > 0 && <div className="feed-files">{m.files.map((f) => <a key={f.id} href={`${f.url}?view=1`} target="_blank" rel="noreferrer">📎 {f.name}</a>)}</div>}
      {!m.deleted && m.kind !== "system" && canAct && (
        <div className="feed-acts">
          <a href="#" onClick={(e) => { e.preventDefault(); api.post<Feed>(`${url}/pin?pinned=${!m.pinned}`).then(onDone); }}>{m.pinned ? "открепить" : "зафиксировать"}</a>
          {m.awaiting && !m.resolved_at && <a href="#" onClick={(e) => { e.preventDefault(); api.post<Feed>(`${url}/resolve`).then(onDone); }}>вопрос снят</a>}
          {m.can_edit && <a href="#" onClick={(e) => { e.preventDefault(); setEdit(m.body); }}>исправить</a>}
          {m.can_delete && <a href="#" onClick={async (e) => { e.preventDefault(); if (await askConfirm("Удалить сообщение?")) api.del<Feed>(url).then(onDone); }}>удалить</a>}
        </div>
      )}
    </div>
  );
}

function Composer({ dealId, section, tags, parent, onSent }: { dealId: string; section: string; tags: Record<string, string>; parent?: FeedItem; onSent: (f: Feed) => void }) {
  const [open, setOpen] = useState(!parent);
  const [body, setBody] = useState("");
  const [tag, setTag] = useState("");
  const [wait, setWait] = useState(false);
  const [due, setDue] = useState(plusWorkdays(2));
  const [files, setFiles] = useState<{ id: string; name: string }[]>([]);
  const [people, setPeople] = useState<{ id: string; name: string }[]>([]);
  const [mentions, setMentions] = useState<string[]>([]);
  const [busy, setBusy] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const mailRef = useRef<HTMLInputElement>(null);
  useEffect(() => { if (open) api.get<{ id: string; name: string }[]>(`/api/deals/${dealId}/feed/participants?section=${section}`).then(setPeople).catch(() => setPeople([])); }, [open, section]);
  if (!open) return <a href="#" className="feed-replylink" onClick={(e) => { e.preventDefault(); setOpen(true); }}>ответить</a>;
  const isQuestion = QUESTION_TAGS.includes(tag);
  const answerTag = parent ? ({ q_client: "a_client", q_factory: "a_factory", q_carrier: "a_carrier" } as Record<string, string>)[parent.tags.find((t) => t.startsWith("q_")) || ""] : "";
  const upload = async (list: FileList | File[]) => {
    setBusy("файлы…");
    try {
      for (const f of Array.from(list)) {
        const r = await api.upload<{ id: string; name: string }>(`/api/deals/${dealId}/feed/files`, f);
        setFiles((x) => [...x, r]);
      }
    } finally { setBusy(""); }
  };
  const send = async () => {
    setBusy("отправка…");
    try {
      const f = await api.post<Feed>(`/api/deals/${dealId}/feed/messages`, {
        section, body, tags: [tag || (parent && answerTag) || ""].filter(Boolean), parent_id: parent?.id ?? null,
        answers: !!parent?.awaiting, awaiting: wait || isQuestion, due_on: wait || isQuestion ? due : null, mentions, attachment_ids: files.map((x) => x.id) });
      setBody(""); setTag(""); setWait(false); setFiles([]); setMentions([]);
      if (parent) setOpen(false);
      onSent(f);
    } finally { setBusy(""); }
  };
  return (
    <div className="feed-compose" onPaste={(e) => { const fs = Array.from(e.clipboardData.files || []); if (fs.length) { e.preventDefault(); upload(fs); } }}
      onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); if (e.dataTransfer.files.length) upload(e.dataTransfer.files); }}>
      <textarea rows={parent ? 2 : 3} value={body} onChange={(e) => setBody(e.target.value)}
        placeholder={parent ? "Ответ…" : "Вопрос, ответ клиента или завода, заметка. Скриншот — вставьте из буфера (Ctrl+V) или перетащите файл"} />
      <div className="feed-tools">
        <select value={tag} onChange={(e) => setTag(e.target.value)} title="Метка">
          <option value="">{parent && answerTag ? tags[answerTag] : "без метки"}</option>
          {Object.entries(tags).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        {!parent && <label><input type="checkbox" checked={wait || isQuestion} disabled={isQuestion} onChange={(e) => setWait(e.target.checked)} /> ждём ответа до</label>}
        {!parent && (wait || isQuestion) && <input type="date" value={due} onChange={(e) => setDue(e.target.value)} />}
        {people.length > 0 && <select value="" onChange={(e) => e.target.value && setMentions((m) => [...new Set([...m, e.target.value])])} title="Упомянуть — придёт уведомление">
          <option value="">@ упомянуть…</option>{people.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select>}
        <button className="btn ghost" onClick={() => fileRef.current?.click()}>📎 файл</button>
        {!parent && <button className="btn ghost" title="Письмо клиента или завода (.eml, .msg) — в ленту раздела" onClick={() => mailRef.current?.click()}>✉ письмо</button>}
        <input ref={fileRef} type="file" multiple hidden onChange={(e) => e.target.files && upload(e.target.files)} />
        <input ref={mailRef} type="file" accept=".eml,.msg" hidden onChange={async (e) => {
          const f = e.target.files?.[0];
          if (!f) return;
          setBusy("письмо…");
          try { onSent(await api.upload<Feed>(`/api/deals/${dealId}/feed/mail`, f, { section, direction: "in", tags: tag })); } finally { setBusy(""); e.target.value = ""; }
        }} />
        <button className="btn" disabled={!!busy || (!body.trim() && files.length === 0)} onClick={send}>{busy || (parent ? "Ответить" : "Отправить")}</button>
        {parent && <button className="btn ghost" onClick={() => setOpen(false)}>Отмена</button>}
      </div>
      {(files.length > 0 || mentions.length > 0) && (
        <div className="muted">{files.map((f) => <span key={f.id} className="badge">📎 {f.name}</span>)}
          {mentions.map((id) => <span key={id} className="badge">@{people.find((p) => p.id === id)?.name}</span>)}</div>
      )}
    </div>
  );
}

/** Обсуждение раздела внизу вкладки: свёрнуто, в заголовке — число сообщений и открытых вопросов. */
export function SectionFeed({ dealId, section, title }: { dealId: string; section: string; title: string }) {
  const [open, setOpen] = useState(false);
  const [n, setN] = useState<{ c: number; o: number } | null>(null);
  useEffect(() => { api.get<Feed>(`/api/deals/${dealId}/feed?section=${section}&messages=true`).then((f) => setN({ c: f.counts[section] || 0, o: f.open[section] || 0 })).catch(() => setN(null)); }, [dealId, section]);
  if (n === null) return null;
  return (
    <div className="card feed-card">
      <a href="#" className="feed-toggle" onClick={(e) => { e.preventDefault(); setOpen(!open); }}>
        {open ? "▾" : "▸"} Обсуждение: {title}{n.c ? ` · ${n.c}` : ""}{n.o ? <span className="badge warn">ждём ответа: {n.o}</span> : null}
      </a>
      {open && <DealFeed dealId={dealId} section={section} onChange={(f) => setN({ c: f.counts[section] || 0, o: f.open[section] || 0 })} />}
    </div>
  );
}
