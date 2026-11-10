// Русские названия служебных кодов — всё, что видит пользователь, выводится по-русски.
import { STATUS_TITLES } from "./types";

type Dict = Record<string, string>;
/** Название по коду; неизвестный код выводится как есть (а пустой — прочерком). */
export const t = (dict: Dict, code: string | null | undefined, empty = "—") => (code == null || code === "" ? empty : dict[code] ?? dict[String(code).toLowerCase()] ?? String(code));

export const PRIORITY: Dict = { low: "низкий", normal: "обычный", medium: "обычный", high: "высокий", urgent: "срочный", critical: "срочный" };
export const ROLE: Dict = { manager: "менеджер", logistician: "логист", service_head: "сервис", cd: "КД", fd: "ФД", gd: "ЗГД", admin: "администратор",
  observer: "наблюдатель", purchasing: "закупки", lawyer: "юрист" };
export const OUTCOME: Dict = { won: "выиграна", lost: "проиграна", cancelled: "отменена", kp_sent: "КП отправлено", open: "без результата" };
export const STATUS: Dict = { ...STATUS_TITLES };
export const BLOCK: Dict = { finance_question: "уточнение «Финансов»", waiting: "ожидание результата", manager: "менеджер", logistics: "логистика", service: "сервис", pricing: "цена и условия", gd: "ЗГД", visa: "виза", kp: "КП",
  contract: "контракт", legal: "юрист", catalog: "каталог", offers: "отбор заводов", local_options: "опции РФ", execution: "исполнение", test: "проверка" };
export const blockTitle = (b: string | null | undefined) => {
  const s = String(b || "");
  if (s.startsWith("stage:")) return "этап исполнения";
  if (s.startsWith("local_option")) return "опции РФ";
  if (s.startsWith("catalog")) return "каталог";
  return t(BLOCK, s.split(":")[0]);
};
export const FEED_KIND: Dict = { system: "система", mail: "письмо", email: "письмо", wecom: "WeCom", note: "заметка", comment: "комментарий", file: "файл", kp: "КП",
  request: "запрос заводу", factory: "завод" };
export const IMAGE_KIND: Dict = { photo: "фото", render: "рендер", drawing: "чертёж", scheme: "схема" };
export const IMAGE_SOURCE: Dict = { own: "свой", client: "от клиента", factory: "от завода", catalog: "из каталога" };
export const FX_SOURCE: Dict = { cbr: "ЦБ РФ", cbr_stale: "последний загруженный курс ЦБ", manual: "введён вручную" };
export const CATALOG_STATUS: Dict = { approved: "подтверждено", pending: "на проверке", rejected: "отклонено", archived: "в архиве", merged: "объединено" };
export const PRICE_SOURCE: Dict = { rfq: "RFQ", offer: "ответ завода", fact: "факт", price_list: "прайс", manual: "вручную", catalog: "каталог" };
export const GLOSSARY_KIND: Dict = { param: "параметр", unit: "единица", value: "значение", phrase: "фраза" };
export const AUDIT_ENTITY: Dict = { deal: "сделка", position: "позиция", variant: "предложение", batch: "партия", setting: "настройка", user: "сотрудник",
  hs_codes: "ТН ВЭД", fx_rate: "курс", template: "шаблон", fact: "факт", access_rule: "уровень доступа", access_override: "исключение доступа",
  client: "клиент", factory: "завод", catalog: "каталог", execution: "исполнение", stage: "этап исполнения", tech_params: "технические характеристики",
  glossary: "глоссарий", kp: "КП", kp_revision: "редакция КП", image: "изображение", local_option: "опция РФ", offer: "ответ завода", logistics: "логистика",
  service: "сервис", team: "сотрудники", machine_type: "тип машины", equipment: "оборудование", deal_doc: "документ сделки" };
export const AUDIT_ACTION: Dict = { create: "создание", update: "изменение", delete: "удаление", status: "смена статуса", price: "цена", upload: "загрузка",
  upsert: "запись", manual: "ручной ввод", reset: "сброс", activate: "включение", contract: "реквизиты контракта", contract_step: "подэтап контракта",
  plan_fact_close: "закрытие план/факта", select: "отбор", merge: "объединение", approve: "подтверждение", reject: "отклонение", send: "отправка",
  import: "импорт", payment_terms: "условия оплаты", visa: "виза", rework: "возврат", actual: "факт этапа", refine: "уточнение даты", legal_review: "юридическая проверка", remove: "удаление", archive: "перенос в архив", unarchive: "возврат из архива", restore: "восстановление" };
export const INTEGRATION: Dict = { cbr: "ЦБ РФ", translator: "Яндекс Переводчик", yandex_translate: "Яндекс Переводчик", dadata: "DaData", registry: "реестр организаций",
  mail: "почта", smtp: "почта", wecom: "WeCom", graph: "Microsoft Graph", storage: "хранилище", hs_lookup: "поиск ТН ВЭД" };
export const OPERATION: Dict = { daily: "курс на день", history: "история курсов", translate: "перевод", lookup: "поиск", send: "отправка", upload: "загрузка",
  find_by_inn: "поиск по ИНН", branches: "филиалы" };
export const lbl = t;
