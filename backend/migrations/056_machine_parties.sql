-- 056 (D-186, K-3): стороны по машине — собственник, лизингодатель, эксплуатант с датами (решение 2 владельца, D-180).
-- Расширение схемы и заполнение: каждой машине — одна главная сторона «собственник» = её текущий клиент.
-- client_machines.client_id остаётся главным клиентом машины; прежние API работают как раньше.
BEGIN;

CREATE TABLE IF NOT EXISTS machine_parties (
    id VARCHAR(36) PRIMARY KEY,
    machine_id VARCHAR(36) NOT NULL REFERENCES client_machines(id) ON DELETE CASCADE,
    client_id VARCHAR(36) NOT NULL REFERENCES clients(id),
    role VARCHAR(16) NOT NULL DEFAULT 'owner',
    valid_from DATE,
    valid_to DATE,
    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
    comment TEXT NOT NULL DEFAULT '',
    created_by VARCHAR(36) REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_machine_parties_machine_active ON machine_parties (machine_id, valid_to);
CREATE INDEX IF NOT EXISTS ix_machine_parties_machine_id ON machine_parties (machine_id);
CREATE INDEX IF NOT EXISTS ix_machine_parties_client_id ON machine_parties (client_id);

INSERT INTO machine_parties (id, machine_id, client_id, role, valid_from, is_primary, comment)
SELECT gen_random_uuid()::text, m.id, m.client_id, 'owner', m.commissioned_on, TRUE, 'заполнено при переходе (056)'
FROM client_machines m
WHERE NOT EXISTS (SELECT 1 FROM machine_parties p WHERE p.machine_id = m.id);

COMMIT;
