-- Ежедневная сводка «Ваши приоритеты» (docs/operations/02 §9, 07; этап 5). Сама сводка не хранится — считается заново;
-- здесь только отметки пользователя (закрепить / отложить / скрыть) для конкретной версии источника.
-- Одна строка на пользователя и элемент (D-053): при изменении источника отметки сбрасываются, а не копятся по версиям.
-- Аддитивно, повторный запуск безопасен. НЕ запускается автоматически (D-019): на стенде с APP_ENV=dev схему создаёт init_db().
-- 018 зарезервирован под ИИ-окно (этап 6, D-042).
BEGIN;
CREATE TABLE IF NOT EXISTS briefing_item_states (
    id               VARCHAR(36)  PRIMARY KEY,
    user_id          VARCHAR(36)  NOT NULL REFERENCES users (id),
    item_key         VARCHAR(120) NOT NULL,
    source_signature VARCHAR(64)  NOT NULL,
    pinned_at        TIMESTAMPTZ,
    snoozed_until    TIMESTAMPTZ,
    hidden_at        TIMESTAMPTZ,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_briefing_state_user_item UNIQUE (user_id, item_key)
);
CREATE INDEX IF NOT EXISTS ix_briefing_item_states_user_id ON briefing_item_states (user_id);
COMMIT;
