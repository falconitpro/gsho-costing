import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { Position } from "../types";

type Factory = { id: string; name: string; typical_production_days: number | null };

/** Поля RFQ, которые заполняются вручную, если файла от завода нет (коды по шаблону RFQ; L — уходят логисту). */
const TECH_FIELDS: { code: string; name: string; unit: string; L?: boolean }[] = [
  { code: "1.3", name: "Грузоподъёмность", unit: "т" }, { code: "1.4", name: "Объём кузова/ковша", unit: "м³" },
  { code: "1.5", name: "Длина", unit: "мм", L: true }, { code: "1.6", name: "Ширина", unit: "мм", L: true }, { code: "1.7", name: "Высота", unit: "мм", L: true },
  { code: "1.14", name: "Масса эксплуатационная", unit: "кг", L: true }, { code: "1.15", name: "Масса транспортная", unit: "кг", L: true },
  { code: "2.1", name: "Модель двигателя", unit: "", L: true }, { code: "2.2", name: "Мощность двигателя", unit: "кВт", L: true }, { code: "2.3", name: "Экологический класс", unit: "", L: true },
  { code: "14.3", name: "Упаковка", unit: "", L: true }, { code: "14.7", name: "Документация на русском", unit: "", L: true },
  { code: "14.8", name: "Обучение персонала", unit: "" }, { code: "14.9", name: "Сертификат ТР ТС 010/2011", unit: "", L: true },
];

const EMPTY = {
  factory_id: "", new_factory: "", machine_type: "", model: "", qty: 1, unit_price: "", currency: "CNY", options_price: "", options_text: "",
  basis: "FCA", delivery_point: "", production_days: 90, delivery_days: 10, warranty_months: "", warranty_hours: "", zip_price: "",
  advance_pct: 30, advance_days: 5, price_comment: "",
};

export default function PositionForm({ dealId, position, onDone, onCancel }: { dealId: string; position?: Position; onDone: () => void; onCancel?: () => void }) {
  const [factories, setFactories] = useState<Factory[]>([]);
  const [f, setF] = useState<typeof EMPTY>(() => position ? {
    ...EMPTY, factory_id: position.factory_id, machine_type: position.machine_type, model: position.model, qty: position.qty,
    unit_price: String(position.unit_price), currency: position.currency, options_price: String(position.options_price || ""), basis: position.basis,
    production_days: position.production_days, delivery_days: position.delivery_days,
  } : EMPTY);
  const [tech, setTech] = useState<Record<string, string>>(() => {
    const t: Record<string, string> = {};
    const src = { ...(position?.tech_params || {}), ...(position?.logistics_params || {}) } as Record<string, { value?: unknown }>;
    for (const k of TECH_FIELDS) t[k.code] = src[k.code]?.value != null ? String(src[k.code].value) : "";
    return t;
  });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.get<Factory[]>("/api/refs/factories").then(setFactories); }, []);

  const set = (patch: Partial<typeof EMPTY>) => setF({ ...f, ...patch });
  const valid = (f.factory_id || f.new_factory) && f.model && Number(f.unit_price) > 0 && f.qty > 0;

  async function save() {
    setBusy(true);
    try {
      let factory_id = f.factory_id;
      if (!factory_id && f.new_factory) {
        const nf = await api.post<Factory>("/api/refs/factories", { name: f.new_factory });
        factory_id = nf.id;
      }
      const tech_params: Record<string, unknown> = {}, logistics_params: Record<string, unknown> = {};
      for (const k of TECH_FIELDS) {
        if (!tech[k.code]) continue;
        const v = isNaN(Number(tech[k.code].replace(",", "."))) ? tech[k.code] : Number(tech[k.code].replace(",", "."));
        (k.L ? logistics_params : tech_params)[k.code] = { name: k.name, value: v, unit: k.unit, is_l: !!k.L };
      }
      const body = {
        factory_id, model: f.model, machine_type: f.machine_type, qty: Number(f.qty), unit_price: Number(f.unit_price), currency: f.currency,
        options: f.options_text ? f.options_text.split(";").map((s) => ({ name: s.trim() })).filter((o) => o.name) : [],
        options_price: Number(f.options_price) || 0, basis: f.basis, delivery_point: f.delivery_point,
        production_days: Number(f.production_days), delivery_days: Number(f.delivery_days),
        warranty_months: f.warranty_months ? Number(f.warranty_months) : null, warranty_hours: f.warranty_hours ? Number(f.warranty_hours) : null,
        zip_price: f.zip_price ? Number(f.zip_price) : null,
        factory_payments: [{ no: 1, share_pct: Number(f.advance_pct), event: "contract", offset_days: Number(f.advance_days) },
                           { no: 2, share_pct: 100 - Number(f.advance_pct), event: "readiness", offset_days: 0 }],
        price_comment: f.price_comment, tech_params, logistics_params,
      };
      if (position) await api.patch(`/api/deals/${dealId}/positions/${position.id}`, body);
      else await api.post(`/api/deals/${dealId}/positions`, body);
      setErr(""); onDone();
    } catch (e) {
      const d = (e as ApiError).detail as { message?: string; price_reference?: { last_price: number; deviation_pct: number } };
      setErr((d?.message || (e as Error).message) + (d?.price_reference ? ` — последняя цена ${d.price_reference.last_price}, отклонение ${d.price_reference.deviation_pct} %` : ""));
    } finally { setBusy(false); }
  }

  return (
    <div className="card" style={{ borderColor: "var(--accent)" }}>
      <h2 style={{ marginTop: 0 }}>{position ? `Позиция ${position.model}: ручная правка` : "Новая позиция вручную (без файла RFQ)"}</h2>
      <div className="grid cols-3">
        <label className="f">Завод (0.7)
          <select value={f.factory_id} onChange={(e) => set({ factory_id: e.target.value, new_factory: "" })}>
            <option value="">— выбрать или ввести новый —</option>
            {factories.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
          </select>
          {!f.factory_id && <input placeholder="новый завод: полное наименование" value={f.new_factory} onChange={(e) => set({ new_factory: e.target.value })} />}
        </label>
        <label className="f">Тип машины (1.1)<input value={f.machine_type} onChange={(e) => set({ machine_type: e.target.value })} placeholder="Шахтный самосвал" /></label>
        <label className="f">Модель (1.2)<input value={f.model} onChange={(e) => set({ model: e.target.value })} /></label>
        <label className="f">Количество (13.4)<input type="number" min={1} value={f.qty} onChange={(e) => set({ qty: Number(e.target.value) })} /></label>
        <label className="f">Цена за единицу (13.5)<div className="row"><input type="number" value={f.unit_price} onChange={(e) => set({ unit_price: e.target.value })} />
          <select value={f.currency} onChange={(e) => set({ currency: e.target.value })}><option>CNY</option><option>USD</option><option>EUR</option><option>RUB</option></select></div></label>
        <label className="f">Опции (12.x): перечень через «;» и сумма за всё кол-во
          <input value={f.options_text} onChange={(e) => set({ options_text: e.target.value })} placeholder="кондиционер; пожаротушение" />
          <input type="number" value={f.options_price} onChange={(e) => set({ options_price: e.target.value })} placeholder="0" /></label>
        <label className="f">Базис (13.2) и пункт поставки (13.3)<div className="row">
          <select value={f.basis} onChange={(e) => set({ basis: e.target.value })}>{["EXW", "FCA", "CPT", "DAP", "DDP"].map((b) => <option key={b}>{b}</option>)}</select>
          <input value={f.delivery_point} onChange={(e) => set({ delivery_point: e.target.value })} placeholder="Маньчжурия" /></div></label>
        <label className="f">Производство от аванса, дн (14.1)<input type="number" value={f.production_days} onChange={(e) => set({ production_days: Number(e.target.value) })} /></label>
        <label className="f">Доставка до пункта поставки, дн (14.2)<input type="number" value={f.delivery_days} onChange={(e) => set({ delivery_days: Number(e.target.value) })} /></label>
        <label className="f">Гарантия: мес (14.4) / м·ч (14.5)<div className="row"><input type="number" value={f.warranty_months} onChange={(e) => set({ warranty_months: e.target.value })} placeholder="12" />
          <input type="number" value={f.warranty_hours} onChange={(e) => set({ warranty_hours: e.target.value })} placeholder="2000" /></div></label>
        <label className="f">Цена ЗИП на ТО за всё кол-во (13.8)<input type="number" value={f.zip_price} onChange={(e) => set({ zip_price: e.target.value })} /></label>
        <label className="f">Оплата заводу: аванс, % / через дней после договора; остаток — по готовности<div className="row">
          <input type="number" value={f.advance_pct} onChange={(e) => set({ advance_pct: Number(e.target.value) })} /><input type="number" value={f.advance_days} onChange={(e) => set({ advance_days: Number(e.target.value) })} /></div></label>
        <label className="f">Комментарий к цене (если отклонение от истории выше порога)<input value={f.price_comment} onChange={(e) => set({ price_comment: e.target.value })} /></label>
      </div>
      <details style={{ marginTop: 10 }}>
        <summary className="muted">Технические данные для логиста и КП (разделы RFQ 1–14; L — метка логиста)</summary>
        <div className="grid cols-3" style={{ marginTop: 8 }}>
          {TECH_FIELDS.map((k) => (
            <label className="f" key={k.code}>{k.code} {k.name}{k.unit ? `, ${k.unit}` : ""} {k.L && <span className="badge">L</span>}
              <input value={tech[k.code]} onChange={(e) => setTech({ ...tech, [k.code]: e.target.value })} /></label>
          ))}
        </div>
      </details>
      {err && <div className="alert error">{err}</div>}
      <div className="row" style={{ marginTop: 10 }}>
        <button className="btn" onClick={save} disabled={!valid || busy}>{position ? "Сохранить изменения" : "Добавить позицию"}</button>
        {onCancel && <button className="btn secondary" onClick={onCancel}>Отмена</button>}
      </div>
    </div>
  );
}
