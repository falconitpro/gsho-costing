import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api";
import { Loading } from "../States";

// D-175 (пакет A2, согласовано владельцем 26.11): «Последняя активность» — события по видимым сделкам (сервер: /api/workplace/activity).
// Не смешивается с задачами и поручениями; сумм и содержимого обсуждений нет.

type Item = { id: number; at: string; author: string; title: string; kind: string; deal_id: string; deal_number: string; client: string; url: string };
type Resp = { items: Item[]; next_before: string | null };

function when(iso: string): string {
  const d = new Date(iso);
  const msk = (x: Date) => x.toLocaleDateString("ru-RU", { timeZone: "Europe/Moscow" });
  const today = new Date();
  const y = new Date(today.getTime() - 86400000);
  const hm = d.toLocaleTimeString("ru-RU", { timeZone: "Europe/Moscow", hour: "2-digit", minute: "2-digit" });
  if (msk(d) === msk(today)) return `сегодня ${hm}`;
  if (msk(d) === msk(y)) return `вчера ${hm}`;
  return `${d.toLocaleDateString("ru-RU", { timeZone: "Europe/Moscow", day: "2-digit", month: "2-digit" })} ${hm}`;
}

export default function RecentActivity({ limit = 8 }: { limit?: number }) {
  const [items, setItems] = useState<Item[] | null>(null);
  const [next, setNext] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const load = (before?: string) => {
    setBusy(true);
    api.get<Resp>(`/api/workplace/activity?limit=${limit}${before ? `&before=${encodeURIComponent(before)}` : ""}`)
      .then((r) => { setItems((p) => (before && p ? [...p, ...r.items] : r.items)); setNext(r.next_before); })
      .catch((e) => setErr((e as Error).message)).finally(() => setBusy(false));
  };
  useEffect(() => { load(); }, []);   // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <section className="card wp-act" aria-label="Последняя активность">
      <div className="wp-exec-h"><h2>Последняя активность</h2><span className="muted">по сделкам, которые вы видите</span></div>
      {err ? <p className="muted">Не загрузилось: {err}</p> : items === null ? <Loading p /> : !items.length ? <p className="muted">Событий по сделкам пока нет.</p> :
        <ol className="wp-act-list">{items.map((i) => (
          <li key={i.id}>
            <time dateTime={i.at}>{when(i.at)}</time>
            <span className="wp-act-main"><Link to={i.url}>{i.title}</Link>
              <span className="muted">{i.deal_number}{i.client ? ` · ${i.client}` : ""}</span></span>
            <span className="wp-act-who">{i.author}</span>
          </li>))}</ol>}
      {next && items && <button type="button" className="btn ghost wp-act-more" disabled={busy} onClick={() => load(next)}>{busy ? "Загружаю…" : "Показать ещё"}</button>}
    </section>);
}
