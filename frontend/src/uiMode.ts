import { useEffect, useState } from "react";

/** D-159: переключатель «Новый вид» (мастер-ТЗ UX/UI A–H). Хранится в браузере сотрудника.
 *  D-212: по умолчанию — новый вид; прежний — только если сотрудник сам выбрал его кнопкой «Прежний вид» (временно, до его отключения).
 *  Все изменения нового вида включаются классом body.ui-new и разметкой оболочки — прежний вид не меняется. */
const KEY = "gsho.newUi";
const EVT = "gsho:ui-mode";

export function isNewUi(): boolean {
  try { return localStorage.getItem(KEY) !== "0"; } catch { return true; }
}

export function setNewUi(on: boolean): void {
  try { localStorage.setItem(KEY, on ? "1" : "0"); } catch { /* приватный режим — только на эту вкладку */ }
  window.dispatchEvent(new Event(EVT));
}

/** Только узнать, включён ли новый вид (для разметки экранов); класс body ставит App через useNewUi. */
export function useNewUiFlag(): boolean {
  const [on, setOn] = useState(isNewUi());
  useEffect(() => {
    const f = () => setOn(isNewUi());
    window.addEventListener(EVT, f);
    window.addEventListener("storage", f);
    return () => { window.removeEventListener(EVT, f); window.removeEventListener("storage", f); };
  }, []);
  return on;
}

export function useNewUi(): boolean {
  const [on, setOn] = useState(isNewUi());
  useEffect(() => {
    const f = () => setOn(isNewUi());
    window.addEventListener(EVT, f);
    window.addEventListener("storage", f);
    return () => { window.removeEventListener(EVT, f); window.removeEventListener("storage", f); };
  }, []);
  useEffect(() => { document.body.classList.toggle("ui-new", on); }, [on]);
  return on;
}

const SIDE = "gsho.sideCollapsed";
export function useSideCollapsed(): [boolean, (v: boolean) => void] {
  // нет выбора сотрудника — на экранах уже 1400 px панель свёрнута до иконок: таблицам нужна ширина
  const [v, setV] = useState(() => {
    try { const x = localStorage.getItem(SIDE); return x === null ? window.innerWidth < 1400 : x === "1"; } catch { return false; }
  });
  const set = (x: boolean) => { setV(x); try { localStorage.setItem(SIDE, x ? "1" : "0"); } catch { /* — */ } };
  return [v, set];
}
