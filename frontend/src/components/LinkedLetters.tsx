import { useEffect, useState } from "react";
import { api } from "../api";

/** D-136: письма Канцелярии, привязанные к договору или претензии (видны только доступные сотруднику). */
type Row = { id: string; kind: string; number: string; reg_date: string | null; subject: string; counterparty: string; status_title: string };

export default function LinkedLetters({ contractId, claimId }: { contractId?: string; claimId?: string }) {
  const [rows, setRows] = useState<Row[] | null>(null);
  useEffect(() => {
    const q = claimId ? `claim_id=${claimId}` : `contract_id=${contractId}`;
    api.get<Row[]>(`/api/office/linked?${q}`).then(setRows).catch(() => setRows([]));
  }, [contractId, claimId]);
  if (!rows) return null;
  return (
    <div className="poa-section linked-letters">
      <div className="poa-section-head"><b>Письма</b> <span className="muted">— входящие и исходящие Канцелярии по {claimId ? "претензии" : "договору"}</span></div>
      {rows.length === 0 ? <div className="muted">Писем нет. Привязать письмо к {claimId ? "претензии" : "договору"} можно в карточке письма в Канцелярии.</div> : (
        <table className="office-table">
          <tbody>{rows.map((r) => (
            <tr key={r.id}><td>{r.kind === "in" ? "Вх." : "Исх."} <a href={`/office?t=${r.kind}&doc=${r.id}`}>{r.number || "черновик"}</a></td>
              <td>{r.reg_date ? new Date(r.reg_date).toLocaleDateString("ru-RU") : ""}</td><td>{r.subject}</td><td>{r.counterparty}</td><td>{r.status_title}</td></tr>))}
          </tbody>
        </table>)}
    </div>
  );
}
