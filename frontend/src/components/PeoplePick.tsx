/** D-209: выбор сотрудников — список «чипов» и выпадающий список для добавления. ordered — порядок важен (цепочка согласования). */
export type Person = { id: string; name: string; note?: string };

export default function PeoplePick({ label, people, value, onChange, ordered, hint }: {
  label: string; people: Person[]; value: string[]; onChange: (v: string[]) => void; ordered?: boolean; hint?: string;
}) {
  const by = new Map(people.map((p) => [p.id, p]));
  const left = people.filter((p) => !value.includes(p.id));
  return (
    <div className="f people-pick">
      <span>{label}</span>
      <div className="pp-chips">
        {value.length === 0 && <span className="muted">—</span>}
        {value.map((id, i) => (
          <span key={id} className="pp-chip">{ordered ? `${i + 1}. ` : ""}{by.get(id)?.name || id}
            <button type="button" aria-label={`Убрать ${by.get(id)?.name || ""}`} onClick={() => onChange(value.filter((x) => x !== id))}>×</button></span>))}
      </div>
      <select aria-label={`${label}: добавить`} value="" onChange={(e) => e.target.value && onChange([...value, e.target.value])}>
        <option value="">+ добавить…</option>
        {left.map((p) => <option key={p.id} value={p.id}>{p.name}{p.note ? ` — ${p.note}` : ""}</option>)}
      </select>
      {hint && <span className="pc-sub">{hint}</span>}
    </div>);
}
