import { useEffect, useState } from "react";
import { Crumbs } from "../components/Levels";
import type { Me } from "../types";
import { NEWS, markNewsSeen, seenNews } from "../whatsNew";

/** D-141: «Что изменилось» — заметки к обновлениям; по умолчанию — то, что важно моей роли. */
export default function WhatsNewPage({ me }: { me: Me }) {
  const [seen] = useState(() => seenNews(me.id));
  const [mine, setMine] = useState(true);
  const roles: string[] = me.roles.map((r) => r.code);
  useEffect(() => { markNewsSeen(me.id); }, [me.id]);
  const fmt = (d: string) => new Date(d).toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
  return (
    <div className="news">
      <Crumbs items={[{ label: "Рабочее место", to: "/" }, { label: "Что изменилось" }]} />
      <div className="page-head"><h1>Что изменилось</h1>
        <label className="row"><input type="checkbox" checked={mine} onChange={(e) => setMine(e.target.checked)} /> только важное для моей роли</label></div>
      {NEWS.map((n) => {
        const items = n.items.filter((i) => !mine || !i.roles || i.roles.some((r) => roles.includes(r)));
        if (!items.length) return null;
        return (
          <section key={n.id} className="card news-entry">
            <h2>{fmt(n.date)} · {n.title} {n.id > seen && <span className="badge accent">новое</span>}</h2>
            <ul>{items.map((i) => <li key={i.text}>{i.text}{me.roles.some((r) => ["gd", "admin"].includes(r.code)) && i.d && <span className="muted"> · {i.d}</span>}</li>)}</ul>
          </section>);
      })}
      <p className="muted">Вопрос по изменению — «?» → «Поддержка».</p>
    </div>
  );
}
