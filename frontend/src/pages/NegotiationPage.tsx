import { Crumbs } from "../components/Levels";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { STATUS_TITLES, type Deal, type Me } from "../types";
import NegotiationTab from "../components/NegotiationTab";

/** Отдельная страница «Торги» (D-082): только данные торгов — без расчёта, вкладок сделки и прочего. КД и ЗГД. */
export default function NegotiationPage({ me }: { me: Me }) {
  const { id } = useParams();
  const [deal, setDeal] = useState<Deal | null>(null);
  const [err, setErr] = useState("");
  const load = () => api.get<Deal>(`/api/deals/${id}`).then(setDeal).catch((e) => setErr((e as Error).message));
  useEffect(() => { load(); }, [id]);
  const allowed = me.roles.some((r) => r.code === "cd" || r.code === "gd");
  useEffect(() => { document.title = deal ? `Торги · ${deal.number}` : "Торги"; }, [deal]);
  if (!allowed) return <div className="card"><div className="alert warn">Торги доступны только КД и ЗГД.</div><Link to={`/deals/${id}`}>← к сделке</Link></div>;
  if (err) return <div className="card"><div className="alert error">{err}</div></div>;
  if (!deal) return <div className="card muted">Загрузка…</div>;
  return (
    <div className="ngp">
      <Crumbs items={[{ label: "Рабочее место", to: "/" }, { label: "Сделки", to: "/deals" }, { label: deal.number, to: `/deals/${deal.id}` }, { label: "Расчёт и утверждение", to: `/deals/${deal.id}?tab=pricing` }, { label: "Торги" }]} />
      <div className="ngp-head">
        <Link to={`/deals/${deal.id}?tab=pricing`} className="ngp-back">← к сделке</Link>
        <div className="ngp-title">
          <h1>Торги <span>{deal.number}</span></h1>
          <div className="muted">{deal.client_name}{deal.site ? ` · ${deal.site}` : ""} · <span className="badge accent">{STATUS_TITLES[deal.status] || deal.status}</span></div>
        </div>
        <div className="ngp-links">
          <Link to={`/deals/${deal.id}?tab=pricing`}>Расчёт и утверждение</Link>
          <Link to={`/deals/${deal.id}?tab=positions`}>Позиции</Link>
          <Link to={`/deals/${deal.id}?tab=feed`}>Лента</Link>
        </div>
      </div>
      <NegotiationTab dealId={deal.id} variants={deal.variants || []} onChanged={load} />
    </div>);
}
