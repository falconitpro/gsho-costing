// Операции со строками таблицы, разбитой на блоки (D-070): шаблон технических характеристик и таблица позиции.
// Блок — подряд идущие строки с одинаковым полем section. Все функции возвращают новый массив (для истории «Отменить»).

export type Sectioned = { section?: string };

export const blockOf = (r: Sectioned, fallback = "Без блока") => (r.section || "").trim() || fallback;

/** Строка вверх / вниз. На границе блока строка не меняется местами с соседом, а переходит в соседний блок. */
export function moveRow<T extends Sectioned>(rows: T[], i: number, dir: -1 | 1): T[] {
  const j = i + dir;
  if (j < 0 || j >= rows.length) return rows;
  const out = rows.slice();
  if (blockOf(out[i]) !== blockOf(out[j])) {
    out[i] = { ...out[i], section: out[j].section };
    return out;
  }
  [out[i], out[j]] = [out[j], out[i]];
  return out;
}

/** Вставить строку после i (i = -1 — в начало) в тот же блок. */
export function insertAfter<T extends Sectioned>(rows: T[], i: number, row: T): T[] {
  const section = i >= 0 ? rows[i].section : rows[0]?.section;
  const out = rows.slice();
  out.splice(i + 1, 0, { ...row, section });
  return out;
}

/** Индексы [начало, конец) блока, в котором строка i. */
export function blockRange(rows: Sectioned[], i: number): [number, number] {
  const b = blockOf(rows[i]);
  let s = i, e = i + 1;
  while (s > 0 && blockOf(rows[s - 1]) === b) s--;
  while (e < rows.length && blockOf(rows[e]) === b) e++;
  return [s, e];
}

/** Переставить блок целиком на место соседнего сверху / снизу. */
export function moveBlock<T extends Sectioned>(rows: T[], i: number, dir: -1 | 1): T[] {
  const [s, e] = blockRange(rows, i);
  if (dir < 0 && s === 0) return rows;
  if (dir > 0 && e >= rows.length) return rows;
  const [ns, ne] = dir < 0 ? blockRange(rows, s - 1) : blockRange(rows, e);
  const block = rows.slice(s, e), other = rows.slice(ns, ne);
  return dir < 0 ? [...rows.slice(0, ns), ...block, ...other, ...rows.slice(e)] : [...rows.slice(0, s), ...other, ...block, ...rows.slice(ne)];
}

export function renameBlock<T extends Sectioned>(rows: T[], i: number, name: string): T[] {
  const [s, e] = blockRange(rows, i);
  return rows.map((r, k) => (k >= s && k < e ? { ...r, section: name } : r));
}

export function removeBlock<T extends Sectioned>(rows: T[], i: number): T[] {
  const [s, e] = blockRange(rows, i);
  return [...rows.slice(0, s), ...rows.slice(e)];
}

/** Новый блок с одной пустой строкой — после блока строки i (или в конец). */
export function addBlock<T extends Sectioned>(rows: T[], name: string, empty: T, afterIndex?: number): T[] {
  const at = afterIndex == null || rows.length === 0 ? rows.length : blockRange(rows, afterIndex)[1];
  const out = rows.slice();
  out.splice(at, 0, { ...empty, section: name });
  return out;
}

/** Номера по порядку на экране. */
export const renumber = <T extends { no?: number }>(rows: T[]): T[] => rows.map((r, i) => ({ ...r, no: i + 1 }));
