# Этап 0 — инвентаризация маршрутов (08 §1)

Сгенерировано `docs/operations/qa_inventory_routes.py` интроспекцией FastAPI-приложения на `main@30c4390` (11.11.2026), локально, без данных; скрипт только читает код.

Итого 112 маршрутов: upload 32, download 31, прочие выгрузки/файловые пути 14, auth 10, с уведомлениями 25.

Колонки: «Зависимости» — FastAPI-зависимости (кроме `get_db`); «Объектная проверка» — найденные в теле функции загрузки объекта с `user`, `can_*`, `*_or_404`, `HTTPException(403/404)`. Это эвристика: на этапе 3 каждую строку проверяют вручную и тестом на IDOR.

| Метод | Путь | Обработчик | Тип | Зависимости | Объектная проверка | audit() | Требуемый hook |
|---|---|---|---|---|---|---|---|
| GET | `/api/auth/callback` | `auth.callback` | auth | get_settings | — | да | сессии/MFA/пароли (этап 2, SQL 014) |
| GET | `/api/auth/dev-users` | `auth.dev_users` | auth | get_current_user, get_settings | `HTTPException(404` | — | сессии/MFA/пароли (этап 2, SQL 014) |
| GET | `/api/auth/login` | `auth.login` | auth | get_settings | — | — | сессии/MFA/пароли (этап 2, SQL 014) |
| POST | `/api/auth/logout` | `auth.logout` | auth | — (нет) | — | — | сессии/MFA/пароли (этап 2, SQL 014) |
| GET | `/api/auth/me` | `auth.me` | auth | get_current_user, get_settings | — | — | сессии/MFA/пароли (этап 2, SQL 014) |
| POST | `/api/auth/password` | `auth.change_password` | auth | get_current_user, get_settings | `HTTPException(403`, `_check_strength` | да | сессии/MFA/пароли (этап 2, SQL 014) |
| POST | `/api/auth/password-login` | `auth.password_login` | auth | get_settings | — | да | сессии/MFA/пароли (этап 2, SQL 014) |
| GET | `/api/auth/providers` | `auth.providers` | auth | get_settings | — | — | сессии/MFA/пароли (этап 2, SQL 014) |
| GET | `/api/auth/roles` | `auth.roles` | auth | — (нет) | — | — | сессии/MFA/пароли (этап 2, SQL 014) |
| PUT | `/api/auth/users/{user_id}/password` | `auth.set_password` | auth | get_current_user, get_settings | `HTTPException(403`, `HTTPException(404`, `_check_strength` | да | сессии/MFA/пароли (этап 2, SQL 014) |
| GET | `/api/catalog/documents/{doc_id}` | `model_docs.get_doc` | download | get_current_user, get_settings | `HTTPException(404` | — | журнал скачивания + статус проверки (этап 3, SQL 015) |
| GET | `/api/catalog/images/{attachment_id}` | `catalog.catalog_image` | download | get_current_user, get_settings | `HTTPException(404` | — | журнал скачивания + статус проверки (этап 3, SQL 015) |
| GET | `/api/deals/rfq/template/file` | `deals.rfq_template_file` | download | get_current_user, get_settings | — | — | журнал скачивания + статус проверки (этап 3, SQL 015) |
| GET | `/api/deals/{deal_id}/acceptance/{uid}/act.docx` | `acceptance.act_docx` | download | get_current_user, get_settings | `_unit` | — | журнал скачивания + статус проверки (этап 3, SQL 015) |
| GET | `/api/deals/{deal_id}/acceptance/{uid}/act/file` | `acceptance.act_file_get` | download | get_current_user, get_settings | `HTTPException(404`, `_unit` | — | журнал скачивания + статус проверки (этап 3, SQL 015) |
| GET | `/api/deals/{deal_id}/acceptance/{uid}/files/{i}` | `acceptance.get_file` | download | get_current_user, get_settings | `HTTPException(404`, `_unit` | — | журнал скачивания + статус проверки (этап 3, SQL 015) |
| GET | `/api/deals/{deal_id}/kp/file` | `pricing.kp_file` | download | get_current_user, get_settings | `get_deal_or_404` | — | журнал скачивания + статус проверки (этап 3, SQL 015) |
| GET | `/api/deals/{deal_id}/positions/{pos_id}/rfq/file` | `tech_params.rfq_with_requirements` | download | get_current_user, get_settings | `HTTPException(403`, `get_deal_or_404` | — | журнал скачивания + статус проверки (этап 3, SQL 015) |
| GET | `/api/deals/{deal_id}/positions/{pos_id}/tech-params/request/file` | `tech_params.request_file` | download | get_current_user, get_settings | `build_request_xlsx`, `get_deal_or_404` | — | журнал скачивания + статус проверки (этап 3, SQL 015) |
| GET | `/api/files/{path:path}` | `tasks_files_planfact.get_file` | download | get_current_user, get_settings | `HTTPException(403`, `HTTPException(404`, `can_see_deal` | — | журнал скачивания + статус проверки (этап 3, SQL 015) |
| GET | `/api/finance/questions/{qid}/files/{fid}` | `finance.question_file_get` | download | get_current_user, get_settings | `HTTPException(404`, `_can_see_full`, `is_assignee` | — | журнал скачивания + статус проверки (этап 3, SQL 015) |
| GET | `/api/legal/claims/{clid}/files/{fid}` | `legal_claims.download` | download | get_current_user, get_settings | `HTTPException(404`, `_claim` | — | журнал скачивания + статус проверки (этап 3, SQL 015) |
| GET | `/api/legal/claims/{clid}/print` | `legal_claims.print_claim` | download | get_current_user, get_settings | `_claim` | — | журнал скачивания + статус проверки (этап 3, SQL 015) |
| GET | `/api/legal/contracts/{cid}/files/{fid}` | `legal_contracts.download_file` | download | get_current_user, get_settings | `HTTPException(404`, `_get` | — | журнал скачивания + статус проверки (этап 3, SQL 015) |
| GET | `/api/legal/packages/{pid}/files/{kind}` | `legal_packages.package_file` | download | get_current_user, get_settings | `HTTPException(404`, `_pkg` | — | журнал скачивания + статус проверки (этап 3, SQL 015) |
| GET | `/api/legal/packages/{pid}/zip` | `legal_packages.package_zip` | download | get_current_user, get_settings | `_pkg` | — | журнал скачивания + статус проверки (этап 3, SQL 015) |
| GET | `/api/legal/poa/{pid}/files/{fid}` | `legal_poa.download_poa_file` | download | _reader, get_current_user, get_settings | `HTTPException(403`, `HTTPException(404` | — | журнал скачивания + статус проверки (этап 3, SQL 015) |
| GET | `/api/legal/poa/{pid}/preview` | `legal_poa.preview_poa` | download | _reader, get_current_user, get_settings | `HTTPException(403` | — | журнал скачивания + статус проверки (этап 3, SQL 015) |
| GET | `/api/legal/profiles/{pid}/card` | `legal_profiles.company_card` | download | _reader, get_current_user, get_settings | — | да | журнал скачивания + статус проверки (этап 3, SQL 015) |
| GET | `/api/legal/profiles/{pid}/documents/{fid}` | `legal_profiles.download_profile_document` | download | _reader, get_current_user, get_settings | `HTTPException(404` | — | журнал скачивания + статус проверки (этап 3, SQL 015) |
| GET | `/api/legal/templates/{tid}/file` | `legal_templates.download_template` | download | _reader, get_current_user, get_settings | `HTTPException(404` | — | журнал скачивания + статус проверки (этап 3, SQL 015) |
| GET | `/api/legal/templates/{tid}/test-render` | `legal_templates.test_render` | download | _reader, get_current_user, get_settings | — | — | журнал скачивания + статус проверки (этап 3, SQL 015) |
| GET | `/api/parts/catalog/{pid}/photo/{i}` | `parts_catalog.part_photo` | download | get_current_user, get_settings | `HTTPException(404`, `_part_or_404` | — | журнал скачивания + статус проверки (этап 3, SQL 015) |
| GET | `/api/parts/claims/{cid}/photos/{i}` | `parts_warranty.claim_photo_get` | download | _reader, get_current_user, get_settings | `HTTPException(404`, `_claim_or_404` | — | журнал скачивания + статус проверки (этап 3, SQL 015) |
| GET | `/api/parts/claims/{cid}/xlsx` | `parts_warranty.claim_xlsx` | download | _reader, get_current_user, get_settings | `_claim_or_404` | — | журнал скачивания + статус проверки (этап 3, SQL 015) |
| GET | `/api/parts/fleet/machines/{mid}/files/{uid}/{i}` | `parts_fleet.machine_file` | download | _reader, get_current_user, get_settings | `HTTPException(404`, `_m_or_404` | — | журнал скачивания + статус проверки (этап 3, SQL 015) |
| GET | `/api/parts/po/{pid}/xlsx` | `parts_po.po_xlsx` | download | _reader, get_current_user, get_settings | `HTTPException(403`, `_po_or_404` | — | журнал скачивания + статус проверки (этап 3, SQL 015) |
| GET | `/api/parts/requests/kp/{kid}/docx` | `parts_requests.kp_docx` | download | get_current_user, get_settings | `_kp_or_404` | — | журнал скачивания + статус проверки (этап 3, SQL 015) |
| GET | `/api/parts/rfq/{rfq_id}/xlsx` | `parts_rfq.rfq_xlsx` | download | _viewer, get_current_user, get_settings | `_check_access`, `_rfq_or_404` | — | журнал скачивания + статус проверки (этап 3, SQL 015) |
| GET | `/api/parts/stock/balances/xlsx` | `parts_stock.balances_xlsx` | download | _reader, get_current_user, get_settings | `_balances` | — | журнал скачивания + статус проверки (этап 3, SQL 015) |
| GET | `/api/parts/stock/deviations/{did}/photos/{i}` | `parts_stock.photo` | download | _reader, get_current_user, get_settings | `HTTPException(404`, `_dev_or_404` | — | журнал скачивания + статус проверки (этап 3, SQL 015) |
| GET | `/api/admin/hs/export` | `admin.hs_export` | export? | get_current_user, get_settings, require_roles | — | — | журнал выгрузки (этап 3, SQL 015) |
| GET | `/api/deals/{deal_id}/kp/revisions/{rev_id}/file` | `pricing.kp_revision_file` | export? | get_current_user, get_settings | `HTTPException(404`, `get_deal_or_404` | — | журнал выгрузки (этап 3, SQL 015) |
| GET | `/api/deals/{deal_id}/legal/zip-set/{factory_id}` | `legal_packages.get_zip_set` | export? | get_current_user, get_settings | `HTTPException(404`, `_deal`, `can_edit` | — | журнал выгрузки (этап 3, SQL 015) |
| PUT | `/api/deals/{deal_id}/legal/zip-set/{factory_id}` | `legal_packages.put_zip_set` | export? | get_current_user, get_settings | `HTTPException(403`, `_deal` | да | журнал выгрузки (этап 3, SQL 015) |
| POST | `/api/deals/{deal_id}/legal/zip-set/{factory_id}/confirm` | `legal_packages.confirm_zip_set` | export? | get_current_user, get_settings | `HTTPException(403`, `_deal` | да | журнал выгрузки (этап 3, SQL 015) |
| POST | `/api/deals/{deal_id}/legal/zip-set/{factory_id}/reset` | `legal_packages.reset_zip_set` | export? | get_current_user, get_settings | `HTTPException(403`, `_deal` | — | журнал выгрузки (этап 3, SQL 015) |
| GET | `/api/deals/{deal_id}/positions/{pos_id}/zip` | `model_docs.get_zip` | export? | get_current_user, get_settings | `_pos` | — | журнал выгрузки (этап 3, SQL 015) |
| PUT | `/api/deals/{deal_id}/positions/{pos_id}/zip` | `model_docs.put_zip` | export? | get_current_user, get_settings | `HTTPException(403`, `_pos` | да | журнал выгрузки (этап 3, SQL 015) |
| GET | `/api/deals/{deal_id}/positions/{pos_id}/zip/for-hours` | `model_docs.zip_hours_json` | export? | get_current_user, get_settings | `_pos` | — | журнал выгрузки (этап 3, SQL 015) |
| GET | `/api/deals/{deal_id}/positions/{pos_id}/zip/for-hours.xlsx` | `model_docs.zip_hours_xlsx` | export? | get_current_user, get_settings | `_pos` | — | журнал выгрузки (этап 3, SQL 015) |
| GET | `/api/deals/{deal_id}/positions/{pos_id}/zip/template.xlsx` | `model_docs.zip_template` | export? | get_current_user, get_settings | `_pos` | — | журнал выгрузки (этап 3, SQL 015) |
| GET | `/api/finance/export` | `finance.export_xlsx` | export? | get_current_user, get_settings | `HTTPException(404`, `Visibility`, `_audit`, `build` | — | журнал выгрузки (этап 3, SQL 015) |
| DELETE | `/api/legal/contracts/{cid}/files/{fid}` | `legal_contracts.delete_file` | export? | get_current_user, get_settings | `HTTPException(403`, `HTTPException(404`, `_can_edit`, `_get` | — | журнал выгрузки (этап 3, SQL 015) |
| DELETE | `/api/legal/poa/{pid}/files/{fid}` | `legal_poa.delete_poa_file` | export? | _issuer, get_current_user, get_settings | `HTTPException(404` | — | журнал выгрузки (этап 3, SQL 015) |
| POST | `/api/admin/hs/upload` | `admin.hs_upload` | upload | get_current_user, get_settings, require_roles | — | да | антивирус + карантин (этап 3, SQL 015) |
| POST | `/api/admin/templates/{kind}` | `admin.upload_template` | upload | get_current_user, get_settings, require_roles | — | да | антивирус + карантин (этап 3, SQL 015) |
| POST | `/api/catalog/equipment/import/apply` | `catalog.catalog_import_apply` | upload | _catalog_contributor, get_current_user, get_settings | — | — | антивирус + карантин (этап 3, SQL 015) |
| POST | `/api/catalog/equipment/import/preview` | `catalog.catalog_import_preview` | upload | _catalog_contributor, get_current_user, get_settings | — | — | антивирус + карантин (этап 3, SQL 015) |
| POST | `/api/catalog/models/{model_id}/documents` | `model_docs.upload_doc` | upload | _writer, get_current_user, get_settings | `HTTPException(404`, `_store` | — | антивирус + карантин (этап 3, SQL 015) |
| POST | `/api/deals/{deal_id}/acceptance/{uid}/act/file` | `acceptance.act_file` | upload | get_current_user, get_settings | `_unit` | — | антивирус + карантин (этап 3, SQL 015) |
| POST | `/api/deals/{deal_id}/acceptance/{uid}/files` | `acceptance.add_file` | upload | get_current_user, get_settings | `_unit`, `position_model` | — | антивирус + карантин (этап 3, SQL 015) |
| POST | `/api/deals/{deal_id}/contract-scans` | `documents.upload_contract_scan` | upload | get_current_user, get_settings | `HTTPException(403`, `_can_scan`, `_save`, `_scans` | — | антивирус + карантин (этап 3, SQL 015) |
| POST | `/api/deals/{deal_id}/documents` | `documents.upload_document` | upload | get_current_user, get_settings | `HTTPException(403`, `_can_upload`, `_save`, `get_deal_or_404` | — | антивирус + карантин (этап 3, SQL 015) |
| POST | `/api/deals/{deal_id}/legal/sale/upload` | `legal_packages.sale_upload` | upload | get_current_user, get_settings | `_deal`, `_save` | — | антивирус + карантин (этап 3, SQL 015) |
| POST | `/api/deals/{deal_id}/offers/{offer_id}/images` | `images_options.upload_offer_image` | upload | get_current_user, get_settings | `HTTPException(403`, `HTTPException(404`, `ensure_folders`, `get_deal_or_404` | да | антивирус + карантин (этап 3, SQL 015) |
| POST | `/api/deals/{deal_id}/positions/{pos_id}/documents` | `model_docs.pos_upload` | upload | _writer, get_current_user, get_settings | `_pos`, `_store`, `position_model` | — | антивирус + карантин (этап 3, SQL 015) |
| POST | `/api/deals/{deal_id}/positions/{pos_id}/images` | `images_options.upload_image` | upload | get_current_user, get_settings | `HTTPException(403`, `ensure_folders`, `get_deal_or_404` | да | антивирус + карантин (этап 3, SQL 015) |
| POST | `/api/deals/{deal_id}/positions/{pos_id}/tech-params/import/apply` | `tech_params.import_apply` | upload | get_current_user, get_settings | `HTTPException(404`, `_can_edit`, `apply_import`, `ensure_folders` | да | антивирус + карантин (этап 3, SQL 015) |
| POST | `/api/deals/{deal_id}/positions/{pos_id}/tech-params/import/preview` | `tech_params.import_preview` | upload | get_current_user, get_settings | `_can_edit`, `get_deal_or_404` | — | антивирус + карантин (этап 3, SQL 015) |
| POST | `/api/deals/{deal_id}/positions/{pos_id}/zip/import` | `model_docs.zip_import` | upload | get_current_user, get_settings | `HTTPException(403`, `_pos` | да | антивирус + карантин (этап 3, SQL 015) |
| POST | `/api/deals/{deal_id}/rfq/import` | `deals.rfq_import` | upload | get_current_user, get_settings | `ensure_default_variant`, `ensure_folders`, `get_deal_or_404`, `offer_from_position` | да | антивирус + карантин (этап 3, SQL 015) |
| POST | `/api/deals/{deal_id}/rfq/preview` | `deals.rfq_preview` | upload | get_current_user, get_settings | `get_deal_or_404` | — | антивирус + карантин (этап 3, SQL 015) |
| POST | `/api/finance/imports/preview` | `finance.import_preview` | upload | get_current_user, get_settings | `preview` | — | антивирус + карантин (этап 3, SQL 015) |
| POST | `/api/finance/questions/{qid}/files` | `finance.question_file` | upload | get_current_user, get_settings | `HTTPException(404`, `_audit`, `is_assignee` | — | антивирус + карантин (этап 3, SQL 015) |
| POST | `/api/legal/claims/{clid}/files` | `legal_claims.upload` | upload | get_current_user, get_settings | `HTTPException(403`, `_can_work`, `_claim`, `_out` | — | антивирус + карантин (этап 3, SQL 015) |
| POST | `/api/legal/contracts/{cid}/files` | `legal_contracts.upload_file` | upload | get_current_user, get_settings | `HTTPException(403`, `_can_edit`, `_get`, `_out` | да | антивирус + карантин (этап 3, SQL 015) |
| POST | `/api/legal/packages/{pid}/signed-scan` | `legal_packages.package_signed_scan` | upload | get_current_user, get_settings | `HTTPException(403`, `_pkg`, `_save` | да | антивирус + карантин (этап 3, SQL 015) |
| POST | `/api/legal/poa/{pid}/files` | `legal_poa.upload_poa_file` | upload | _issuer, get_current_user, get_settings | — | да | антивирус + карантин (этап 3, SQL 015) |
| POST | `/api/legal/profiles/{pid}/documents` | `legal_profiles.upload_profile_document` | upload | _editor, get_current_user, get_settings | — | да | антивирус + карантин (этап 3, SQL 015) |
| POST | `/api/legal/templates` | `legal_templates.upload_template` | upload | _editor, get_current_user, get_settings | — | да | антивирус + карантин (этап 3, SQL 015) |
| POST | `/api/parts/claims/{cid}/photos` | `parts_warranty.claim_photo` | upload | _editor, get_current_user, get_settings | `_claim_or_404` | — | антивирус + карантин (этап 3, SQL 015) |
| POST | `/api/parts/import/moysklad-products` | `parts_catalog.import_products` | upload | _importer, get_current_user, get_settings | `apply_products` | да | антивирус + карантин (этап 3, SQL 015) |
| POST | `/api/parts/import/stock` | `parts_catalog.import_stock` | upload | _importer, get_current_user, get_settings | — | да | антивирус + карантин (этап 3, SQL 015) |
| POST | `/api/parts/requests/parse-file` | `parts_requests.parse_file` | upload | get_current_user, get_settings | `HTTPException(403` | — | антивирус + карантин (этап 3, SQL 015) |
| POST | `/api/parts/rfq/{rfq_id}/import` | `parts_rfq.rfq_import` | upload | _viewer, get_current_user, get_settings | `_after_answer`, `_answer_target`, `_rfq_payload` | да | антивирус + карантин (этап 3, SQL 015) |
| POST | `/api/parts/stock/deviations/{did}/photos` | `parts_stock.add_photo` | upload | _editor, get_current_user, get_settings | `_dev_or_404` | — | антивирус + карантин (этап 3, SQL 015) |
| POST | `/api/deals/{deal_id}/acceptance/{uid}/decision` | `acceptance.decide` | notify | get_current_user, get_settings | `_mark_stage`, `_to_park`, `_unit` | да | не трогать; для поддержки — app-only helper (этап 4) |
| POST | `/api/deals/{deal_id}/delete` | `deal_trash.delete_deal` | notify | get_current_user, get_settings | `HTTPException(403`, `HTTPException(404` | да | не трогать; для поддержки — app-only helper (этап 4) |
| POST | `/api/deals/{deal_id}/legal-review` | `documents.legal_review` | notify | get_current_user, get_settings | `HTTPException(403`, `get_deal_or_404` | да | не трогать; для поддержки — app-only helper (этап 4) |
| POST | `/api/deals/{deal_id}/submit` | `pricing.submit` | notify | get_current_user, get_settings, require_roles | `_finalize_approval`, `_finalize_contract_change`, `apply_transition`, `get_deal_or_404` | да | не трогать; для поддержки — app-only helper (этап 4) |
| POST | `/api/legal/poa/{pid}/issue` | `legal_poa.issue_poa` | notify | _issuer, get_current_user, get_settings | — | да | не трогать; для поддержки — app-only helper (этап 4) |
| POST | `/api/legal/poa/{pid}/revoke` | `legal_poa.revoke_poa` | notify | _issuer, get_current_user, get_settings | — | да | не трогать; для поддержки — app-only helper (этап 4) |
| GET | `/api/notifications` | `notifications.list_notifications` | notify | get_current_user, get_settings | `_base` | — | не трогать; для поддержки — app-only helper (этап 4) |
| POST | `/api/notifications/read` | `notifications.mark_read` | notify | get_current_user, get_settings | `unread_count` | — | не трогать; для поддержки — app-only helper (этап 4) |
| GET | `/api/notifications/unread-count` | `notifications.unread_count` | notify | get_current_user, get_settings | `_base` | — | не трогать; для поддержки — app-only helper (этап 4) |
| POST | `/api/parts/markup-policies/{policy_id}/approve` | `parts_setup.approve_policy` | notify | get_current_user, get_settings | `_policy_or_404` | да | не трогать; для поддержки — app-only helper (этап 4) |
| POST | `/api/parts/markup-policies/{policy_id}/reject` | `parts_setup.reject_policy` | notify | get_current_user, get_settings | `_policy_or_404` | да | не трогать; для поддержки — app-only helper (этап 4) |
| POST | `/api/parts/markup-policies/{policy_id}/submit` | `parts_setup.submit_policy` | notify | _markup_reader, get_current_user, get_settings | `_policy_or_404` | да | не трогать; для поддержки — app-only helper (этап 4) |
| POST | `/api/parts/requests/{rid}/approve` | `parts_requests.approve` | notify | get_current_user, get_settings | `HTTPException(403`, `_payload`, `_req_or_404` | да | не трогать; для поддержки — app-only helper (этап 4) |
| POST | `/api/parts/requests/{rid}/kp` | `parts_requests.create_kp` | notify | get_current_user, get_settings | `HTTPException(403`, `_payload`, `_req_or_404` | да | не трогать; для поддержки — app-only helper (этап 4) |
| POST | `/api/parts/stock/inventories/{iid}/submit` | `parts_inventory.submit` | notify | _counter, get_current_user, get_settings | `_inv_or_404` | да | не трогать; для поддержки — app-only helper (этап 4) |
| POST | `/api/parts/stock/receipts` | `parts_stock.create_receipt` | notify | _editor, get_current_user, get_settings | `HTTPException(404`, `add_stock` | да | не трогать; для поддержки — app-only helper (этап 4) |
| POST | `/api/refs/factories/{factory_id}/approve` | `references.approve_factory` | notify | _approver, get_current_user, get_settings | `HTTPException(404`, `_factory_out` | да | не трогать; для поддержки — app-only helper (этап 4) |
| POST | `/api/refs/factories/{factory_id}/reject` | `references.reject_factory` | notify | _approver, get_current_user, get_settings | `HTTPException(404`, `_factory_out` | да | не трогать; для поддержки — app-only helper (этап 4) |
| POST | `/api/refs/factory-changes/{change_id}/approve` | `references.approve_change` | notify | _approver, get_current_user, get_settings | `_change_or_404`, `_factory_out` | да | не трогать; для поддержки — app-only helper (этап 4) |
| POST | `/api/refs/factory-changes/{change_id}/reject` | `references.reject_change` | notify | _approver, get_current_user, get_settings | `_change_or_404`, `_factory_out` | да | не трогать; для поддержки — app-only helper (этап 4) |
| PUT | `/api/team/{user_id}/contacts` | `team.set_contacts` | notify | get_current_user, get_settings | `HTTPException(403`, `HTTPException(404`, `_can_channels` | да | не трогать; для поддержки — app-only helper (этап 4) |
| PUT | `/api/team/{user_id}/deputy` | `team.set_deputy` | notify | get_current_user, get_settings | `HTTPException(403`, `HTTPException(404`, `_can_channels` | да | не трогать; для поддержки — app-only helper (этап 4) |
| POST | `/api/team/{user_id}/pending/decide` | `team.decide_pending` | notify | get_current_user, get_settings | `HTTPException(403`, `HTTPException(404` | да | не трогать; для поддержки — app-only helper (этап 4) |
| POST | `/api/team/{user_id}/test` | `team.test_notification` | notify | get_current_user, get_settings | `HTTPException(403`, `HTTPException(404`, `_can_channels` | — | не трогать; для поддержки — app-only helper (этап 4) |
| GET | `/api/workplace` | `workplace.workplace` | notify | get_current_user, get_settings | `_deals`, `_my_tasks`, `can_lock`, `can_see_deal` | — | не трогать; для поддержки — app-only helper (этап 4) |
