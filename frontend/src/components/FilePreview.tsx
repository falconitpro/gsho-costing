import { useEffect, useState } from "react";
import { api } from "../api";
import { can, type Me } from "../types";

// Просмотр файлов сделки в системе (D-077): любая ссылка на /api/files/… открывает просмотр (изображения, PDF, видео,
// Word / Excel — как PDF); скачивание — отдельной кнопкой, если оно открыто для роли, иначе — запрос ЗГД.

const IMG = /\.(png|jpe?g|gif|webp|bmp|heic)$/i;
const VID = /\.(mp4|webm|mov)$/i;
const PDF = /\.pdf$/i;
const OFFICE = /\.(docx?|xlsx?|pptx|odt|ods|rtf)$/i;
export const isPreviewable = (name: string) => IMG.test(name) || VID.test(name) || PDF.test(name) || OFFICE.test(name);
/** Ссылка на файл для показа в <img>/<video>: просмотр, не скачивание. */
export const viewUrl = (u?: string) => (u && u.startsWith("/api/files/") && !u.includes("view=1") ? `${u}${u.includes("?") ? "&" : "?"}view=1` : u || "");

type Item = { href: string; name: string; other?: boolean };

export default function FilePreviewHost({ me }: { me: Me }) {
  const [list, setList] = useState<Item[]>([]);
  const [i, setI] = useState(0);
  const [reason, setReason] = useState("");
  const [msg, setMsg] = useState("");
  const [asking, setAsking] = useState(false);
  const mayDownload = can(me, "file_download");

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey) return;
      const a = (e.target as HTMLElement)?.closest?.("a") as HTMLAnchorElement | null;
      if (!a || a.dataset.download !== undefined) return;
      const href = a.getAttribute("href") || "";
      const other = !href.startsWith("/api/files/") && /^\/api\/.+\/(files|photo|photos|images)\//.test(href);   // приёмка, каталог, парк — фото и видео
      if (!href.startsWith("/api/files/") && !other) return;
      const nameOf = (x: HTMLAnchorElement) => (x.getAttribute("title") || x.textContent || "").trim() || decodeURIComponent((x.getAttribute("href") || "").split("?")[0].split("/").pop() || "файл");
      const path = href.split("?")[0];
      const fileName = /\.[a-z0-9]{2,5}$/i.test(nameOf(a)) ? nameOf(a) : decodeURIComponent(path.split("/").pop() || "");
      if (other && !(IMG.test(fileName) || VID.test(fileName) || a.querySelector("img"))) return;
      if (!isPreviewable(fileName) && !isPreviewable(path)) {
        if (mayDownload) return;               // непросматриваемый файл — обычное скачивание, если оно открыто
      }
      e.preventDefault();
      // галерея: все ссылки на файлы на странице, по порядку
      const seen = new Set<string>();
      const all: Item[] = [];
      if (other) { setList([{ href, name: fileName, other: true }]); setI(0); setMsg(""); setReason(""); setAsking(false); return; }
      document.querySelectorAll<HTMLAnchorElement>('a[href^="/api/files/"]').forEach((x) => {
        const h = (x.getAttribute("href") || "").split("?")[0];
        if (seen.has(h) || x.dataset.download !== undefined) return;
        seen.add(h);
        const n = /\.[a-z0-9]{2,5}$/i.test(nameOf(x)) ? nameOf(x) : decodeURIComponent(h.split("/").pop() || "");
        all.push({ href: h, name: n });
      });
      const idx = Math.max(0, all.findIndex((x) => x.href === path));
      setList(all.length ? all : [{ href: path, name: fileName }]); setI(idx); setMsg(""); setReason(""); setAsking(false);
    };
    document.addEventListener("click", onClick, true);
    return () => document.removeEventListener("click", onClick, true);
  }, [mayDownload]);
  useEffect(() => {
    if (!list.length) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setList([]);
      if (e.key === "ArrowRight") setI((x) => Math.min(list.length - 1, x + 1));
      if (e.key === "ArrowLeft") setI((x) => Math.max(0, x - 1));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [list]);
  if (!list.length) return null;
  const it = list[i];
  const src = viewUrl(it.href);
  const ask = () => api.post<{ status: string }>("/api/files-download-requests", { path: it.href.replace(/^\/api\/files\//, ""), reason })
    .then((r) => setMsg(r.status === "approved" ? "Скачивание разрешено." : "Запрос отправлен ЗГД. Когда его одобрят, скачивание откроется на 24 часа — придёт уведомление."))
    .catch((e) => setMsg((e as Error).message));

  return (
    <div className="fp" onClick={(e) => { if (e.target === e.currentTarget) setList([]); }}>
      <div className="fp-bar">
        <b className="fp-name" title={it.name}>{it.name}</b>
        {list.length > 1 && <span className="fp-count">{i + 1} / {list.length}</span>}
        <span style={{ flex: 1 }} />
        {mayDownload || it.other ? <a className="btn" data-download href={it.href} download>⬇ Скачать</a>
          : asking ? <span className="fp-ask"><input placeholder="зачем нужен файл (необязательно)" value={reason} onChange={(e) => setReason(e.target.value)} />
              <button className="btn" onClick={ask}>Отправить запрос</button></span>
            : <button className="btn ghost fp-light" onClick={() => setAsking(true)} title="скачивание закрыто для вашей роли">Запросить скачивание</button>}
        <button className="btn ghost fp-light" onClick={() => setList([])}>✕</button>
      </div>
      {msg && <div className="fp-msg">{msg}</div>}
      <div className="fp-body">
        {i > 0 && <button className="fp-nav fp-prev" onClick={() => setI(i - 1)}>‹</button>}
        {it.other && !VID.test(it.name) ? <img key={src} src={src} alt={it.name} />
          : IMG.test(it.name) || IMG.test(it.href) ? <img key={src} src={src} alt={it.name} />
          : VID.test(it.name) || VID.test(it.href) ? <video key={src} src={src} controls controlsList="nodownload" />
            : PDF.test(it.name) || PDF.test(it.href) || OFFICE.test(it.name) || OFFICE.test(it.href) ? <Pages key={src} src={src} name={it.name} />
              : <div className="fp-none">Просмотр этого типа файла в системе недоступен.{mayDownload ? " Скачайте файл кнопкой вверху." : " Запросите скачивание."}</div>}
        {i < list.length - 1 && <button className="fp-nav fp-next" onClick={() => setI(i + 1)}>›</button>}
      </div>
    </div>);
}

/** PDF, Word, Excel — постранично: сервер отдаёт страницы картинками. */
function Pages({ src, name }: { src: string; name: string }) {
  const [n, setN] = useState<number | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { api.get<{ pages: number }>(`${src}&info=1`).then((r) => setN(r.pages)).catch((e) => setErr((e as Error).message)); }, [src]);
  if (err) return <div className="fp-none">{err}</div>;
  if (n == null) return <div className="fp-none">Готовим просмотр…</div>;
  return <div className="fp-pages">{Array.from({ length: n }, (_, k) => <img key={k} src={`${src}&page=${k + 1}`} alt={`${name}, стр. ${k + 1}`} loading="lazy" />)}</div>;
}
