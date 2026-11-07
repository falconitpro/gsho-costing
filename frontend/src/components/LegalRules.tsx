import { useEffect, useState } from "react";
import { api } from "../api";

// Юридический блок: правила анализа договоров (версия, группы проверок) и ИИ-провайдер (выбор — ЗГД; ключи только в backend/.env).

type Ai = { provider: string; model: string; providers: Record<string, string>; default_models: Record<string, string>; configured: Record<string, boolean>; can_edit: boolean;
  rules_version: string; rules_count: number; groups: Record<string, string>; env_hint: Record<string, string> };

export default function LegalRules() {
  const [ai, setAi] = useState<Ai | null>(null);
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [msg, setMsg] = useState("");
  const [test, setTest] = useState<{ ok: boolean; message?: string; provider?: string; model?: string; answer?: string } | null>(null);
  const load = () => api.get<Ai>("/api/legal/ai").then((r) => { setAi(r); setProvider(r.provider); setModel(r.model); }).catch((e) => setMsg((e as Error).message));
  useEffect(() => { load(); }, []);
  if (!ai) return <div className="card muted">{msg || "Загрузка…"}</div>;
  return (
    <div className="grid" style={{ gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)", alignItems: "start" }}>
      <div className="card">
        <h2 style={{ marginTop: 0 }}>ИИ-анализ договоров</h2>
        <p className="muted" style={{ fontSize: 12 }}>Модель дополняет замечания правил при разборе договора заказчика. Все замечания — черновик, проверяет юрист. Ключи API хранятся только в <code>backend/.env</code> на сервере и здесь не показываются.</p>
        <label className="f">Провайдер<select value={provider} disabled={!ai.can_edit} onChange={(e) => { setProvider(e.target.value); setModel(""); }}>
          {Object.entries(ai.providers).map(([k, t]) => <option key={k} value={k}>{t}{k !== "none" ? (ai.configured[k] ? " — настроен" : " — ключ не задан") : ""}</option>)}</select></label>
        {provider !== "none" && <label className="f" style={{ marginTop: 6 }}>Модель<input value={model} disabled={!ai.can_edit} placeholder={ai.default_models[provider] || ""} onChange={(e) => setModel(e.target.value)} />
          <span className="muted" style={{ fontSize: 11 }}>пусто — модель по умолчанию ({ai.default_models[provider]}); для YandexGPT можно указать yandexgpt-lite/latest или gpt://&lt;folder&gt;/…</span></label>}
        {provider !== "none" && !ai.configured[provider] && <div className="alert warn" style={{ fontSize: 12 }}>Ключ не задан. На сервере в <code>backend/.env</code> нужны: {ai.env_hint[provider]}; затем <code>docker compose … up -d backend</code>.</div>}
        <div className="row" style={{ marginTop: 8, gap: 6, flexWrap: "wrap" }}>
          {ai.can_edit && <button className="btn" onClick={() => api.put<Ai>("/api/legal/ai", { provider, model }).then((r) => { setAi(r); setMsg("Сохранено"); }).catch((e) => setMsg((e as Error).message))}>Сохранить</button>}
          <button className="btn secondary" onClick={() => { setTest(null); api.post<typeof test>("/api/legal/ai/test").then(setTest).catch((e) => setMsg((e as Error).message)); }}>Проверить соединение</button>
          {!ai.can_edit && <span className="muted" style={{ fontSize: 12 }}>провайдера и модель выбирает ЗГД</span>}
        </div>
        {msg && <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>{msg}</div>}
        {test && <div className={"alert " + (test.ok ? "ok" : "error")} style={{ fontSize: 12 }}>{test.ok ? `✓ ${test.provider} · ${test.model} отвечает: «${test.answer}»` : test.message}</div>}
      </div>
      <div className="card">
        <h2 style={{ marginTop: 0 }}>Правила анализа <span className="muted" style={{ fontWeight: 400, fontSize: 13 }}>· версия {ai.rules_version} · правил: {ai.rules_count}</span></h2>
        <p className="muted" style={{ fontSize: 12 }}>Профиль «БЕРГ-ХАУС — продавец» применяется к договорам заказчиков. Каждое правило ищет пункт по теме, проверяет рискованные формулировки и предлагает редакцию в пользу продавца; при отсутствии положения — предлагает добавить пункт. Критичность: критический / существенный / технический. Версия правил записывается в каждый пакет.</p>
        <div style={{ fontSize: 13 }}><b>Группы проверок</b>
          <ul style={{ margin: "4px 0 0", paddingLeft: 18, columns: 2 }}>{Object.values(ai.groups).map((g) => <li key={g}>{g}</li>)}</ul></div>
        <p className="muted" style={{ fontSize: 12, marginTop: 8 }}>Примеры договоров заказчиков и протоколов для настройки правил загружаются во вкладке «Шаблоны и примеры» (вид «Пример …»). Редактирование правил из интерфейса — следующий шаг после накопления примеров.</p>
      </div>
    </div>
  );
}
