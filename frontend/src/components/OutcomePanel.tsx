import { useState } from "react";
import { api } from "../api";
import type { Deal, Me } from "../types";

const REASONS: Record<string, string[]> = {
  won: ["лучшая цена", "лучшие сроки поставки", "технические характеристики", "условия оплаты", "сервис и гарантия", "отношения с клиентом"],
  lost: ["цена выше конкурентов", "сроки поставки", "технические характеристики", "условия оплаты", "клиент выбрал другого поставщика", "нет ответа клиента"],
  cancelled: ["клиент отказался от закупки", "перенос закупки", "изменились требования", "ошибочная заявка", "дубль заявки"],
};
const TITLES: Record<string, string> = { won: "Выиграна", lost: "Проиграна", cancelled: "Отменена" };

/** Результат сделки: явные кнопки «Выиграна / Проиграна / Отменена» для менеджера сделки, КД и ЗГД.
 *  «Выиграна» переводит сделку на стадию «Заключение контракта» с подэтапами; проигранную/отменённую КД или ЗГД могут вернуть в работу. */
export default function OutcomePanel({ deal, me, onDone }: { deal: Deal; me: Me; onDone: (msg: string) => void }) {
  const has = (...r: string[]) => me.roles.some((x) => r.includes(x.code));
  const isMgr = has("manager") && deal.manager_id === me.id;
  const canSet = isMgr || has("cd", "gd");
  const [open, setOpen] = useState<"" | "won" | "lost" | "cancelled" | "reopen">("");
  const [reason, setReason] = useState("");
  const [variant, setVariant] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const ready = ["approved", "kp_sent"].includes(deal.status);
  const closed = ["lost", "cancelled"].includes(deal.status);
  if (!canSet || !(ready || (closed && has("cd", "gd")))) return null;

  async function submit() {
    setBusy(true);
    try {
      if (open === "reopen") {
        await api.post(`/api/deals/${deal.id}/reopen`, { reason });
        onDone("Сделка возвращена в работу (статус «КП отправлено»)");
      } else {
        const r = await api.post<{ status: string; closed_related: string[] }>(`/api/deals/${deal.id}/outcome`, { outcome: open, reason, won_variant_id: variant || undefined });
        onDone(open === "won"
          ? `Сделка выиграна — стадия «Заключение контракта». Следующий шаг: подготовка проекта контракта (вкладка «Контракт и исполнение»).${r.closed_related?.length ? ` Связанные заявки закрыты: ${r.closed_related.join(", ")}.` : ""}`
          : `Результат: ${TITLES[open].toLowerCase()}.`);
      }
      setOpen(""); setReason(""); setVariant(""); setErr("");
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }

  return (
    <div className="card" style={{ borderColor: "var(--accent)" }}>
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
        <div>
          <h2 style={{ margin: 0 }}>Результат сделки</h2>
          <div className="muted" style={{ fontSize: 12 }}>
            {ready ? "Отметьте решение клиента. «Выиграна» переводит сделку на стадию «Заключение контракта»: подготовка проекта, согласование с клиентом, внутреннее согласование, подписание клиентом и заводом, затем исполнение."
              : `Сделка ${deal.status === "lost" ? "проиграна" : "отменена"}${deal.outcome_reason ? `: ${deal.outcome_reason}` : ""}. КД или ЗГД может вернуть её в работу.`}
          </div>
        </div>
        <div className="row">
          {ready && <>
            <button className="btn" style={{ background: "var(--ok)", borderColor: "var(--ok)" }} onClick={() => setOpen("won")}>Выиграна → заключение контракта</button>
            <button className="btn secondary" onClick={() => setOpen("lost")}>Проиграна</button>
            <button className="btn ghost" onClick={() => setOpen("cancelled")}>Отменена</button>
          </>}
          {closed && <button className="btn secondary" onClick={() => setOpen("reopen")}>Вернуть в работу</button>}
        </div>
      </div>
      {open && (
        <div style={{ marginTop: 10, borderTop: "1px solid var(--line)", paddingTop: 10 }}>
          <b>{open === "reopen" ? "Вернуть сделку в работу" : `Результат: ${TITLES[open]}`}</b>
          {open === "won" && deal.has_alternatives && (
            <label className="f" style={{ marginTop: 6 }}>Какое предложение выбрал клиент
              <select value={variant} onChange={(e) => setVariant(e.target.value)}><option value="">— выберите —</option>
                {deal.variants.filter((v) => !v.is_default).map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}</select></label>)}
          {open !== "reopen" && <div className="row" style={{ flexWrap: "wrap", marginTop: 6 }}>{REASONS[open].map((r) => (
            <button key={r} className={"btn ghost" + (reason === r ? " active" : "")} style={reason === r ? { borderColor: "var(--accent)" } : undefined} onClick={() => setReason(r)}>{r}</button>))}</div>}
          <div className="row" style={{ marginTop: 6 }}>
            <input placeholder={open === "reopen" ? "почему возвращаем (обязательно)" : "причина (выберите выше или впишите)"} value={reason} onChange={(e) => setReason(e.target.value)} style={{ width: 420 }} />
            <button className="btn" disabled={busy || !reason.trim() || (open === "won" && deal.has_alternatives && !variant)} onClick={submit}>Подтвердить</button>
            <button className="btn ghost" onClick={() => setOpen("")}>Отмена</button>
          </div>
          {err && <div className="alert error">{err}</div>}
        </div>)}
    </div>
  );
}
