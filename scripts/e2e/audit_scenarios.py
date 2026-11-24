"""D-157: прохождение сценариев «Пульт ЗГД и поручения» со скриншотами и подписями (для аудитора).
Запуск на стенде разработки (APP_ENV=dev, заголовок X-Dev-User): python3 scripts/e2e/audit_scenarios.py <папка>.
Нужно: руководитель «Коммерции» — КД (cd@example.com), руководитель «Финансов» — ФД; создаются два новых поручения."""
MGR_ID = ""
import asyncio
import os
import json
import urllib.request
from playwright.async_api import async_playwright

B = "http://localhost:5173"
API = "http://localhost:8000"
GD, CD, MGR, FD, LAW = "gd@example.com", "cd@example.com", "manager@example.com", "fd@example.com", "lawyer@example.com"
import sys
OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/audit_shots"
CAP = []
N = [0]


def api(user, path):
    rq = urllib.request.Request(API + path, headers={"X-Dev-User": user})
    return json.loads(urllib.request.urlopen(rq).read())


async def main():
    async with async_playwright() as p:
        br = await p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
        errs = []
        pages = {}

        async def pg(user, w=1440, h=900):
            k = (user, w)
            if k not in pages:
                ctx = await br.new_context(viewport={"width": w, "height": h}, extra_http_headers={"X-Dev-User": user}, timezone_id="Europe/Moscow", locale="ru-RU")
                page = await ctx.new_page()
                page.on("console", lambda m: errs.append(f"{user}: {m.text}") if m.type == "error" else None)
                page.on("pageerror", lambda e: errs.append(f"{user}: {e}"))
                await page.goto(B + "/")
                await page.evaluate("localStorage.setItem('gsho.gdView','pult')")
                pages[k] = page
            return pages[k]

        async def shot(page, who, text, full=False, clip=None):
            N[0] += 1
            name = f"{N[0]:02d}.png"
            await page.wait_for_timeout(500)
            if clip:
                await page.screenshot(path=f"{OUT}/{name}", clip=clip)
            else:
                await page.screenshot(path=f"{OUT}/{name}", full_page=full)
            CAP.append({"file": name, "who": who, "text": text})

        async def card(page, aid):
            await page.goto(f"{B}/assignments?scope=all&status=any&open={aid}")
            await page.wait_for_selector(".asg-card h2")

        async def act(page, label, text=None, date=None, select=None):
            await page.click(f".asg-acts button:has-text('{label}')")
            if text is not None or date or select:
                await page.wait_for_selector(".asg-form")
                if text is not None:
                    await page.fill(".asg-form textarea", text)
                if date:
                    await page.fill(".asg-form input[type=date]", date)
                if select:
                    await page.select_option(".asg-form select", label=select)
            return page

        async def confirm(page, label):
            await page.click(f".asg-form .asg-actions button:has-text('{label}')")
            await page.wait_for_timeout(900)

        os.makedirs(OUT, exist_ok=True)
        global MGR_ID
        MGR_ID = api(MGR, "/api/auth/me")["id"]
        heads = {u["id"]: u for u in api(GD, "/api/management/assignees")["users"]}
        assert MGR_ID in heads, "нужен стенд с тестовыми пользователями (scripts/seed.py) и руководителем «Коммерции» = КД"

        # ---------- А. Пульт ЗГД
        g = await pg(GD)
        await g.reload(); await g.wait_for_selector(".wp-exec-queue", timeout=30000)
        await shot(g, "ЗГД", "А1. «Пульт» ЗГД: шапка с переключателем вида, быстрые действия, 6 показателей (период, время расчёта, качество данных), очередь «Требует моего вмешательства».")
        await shot(g, "ЗГД", "А2. «Пульт» целиком: очередь → поручения и направления → денежный поток, согласования, юр. блок, ближайшие сроки (тип, ответственный, «просрочено»/«сегодня»).", full=True)
        await g.click(".wp-exec-kpi >> nth=4")
        await shot(g, "ЗГД", "А3. Нажатие на «Дебиторка просрочена»: как считается, источник, время расчёта; список обязательств (каждое со ссылкой); суммы в валюте, рубли — справочно по курсу ЦБ с датой.")
        await g.keyboard.press("Escape")
        await g.click(".wp-exec-kpi >> nth=0")
        await shot(g, "ЗГД", "А4. «Активные сделки»: методика и полный список объектов, из которых сложилась цифра. Сделки без цены не считаются нулём (иначе — «частичные данные, сумма по X из Y»).")
        await g.keyboard.press("Escape")
        await g.click(".wp-exec-kpi >> nth=5")
        await shot(g, "ЗГД", "А5. «Исполнение»: просроченные этапы — отдельно от числа сделок и от сдвига срока; список ровно тех этапов, что посчитаны.")
        await g.keyboard.press("Escape")
        more = await g.query_selector(".wp-exec-more")
        if more:
            await more.click()
        q = await g.query_selector(".wp-exec-queue")
        bb = await q.bounding_box()
        await g.evaluate(f"window.scrollTo(0, {int(bb['y']) - 10})")
        await shot(g, "ЗГД", "А6. Очередь: объект и номер, направление, причина, сумма (в валюте + ₽ справочно), ответственный и руководитель направления, следующий шаг; проблемы одной сделки — в группе («ещё N…», раскрыта). Каждый платёж — отдельная строка.")
        await g.evaluate("window.scrollTo(0,0)")

        # ---------- Б. Оргструктура
        await g.click("button:has-text('Руководители')")
        await g.wait_for_selector(".sec-modal select")
        await shot(g, "ЗГД", "Б1. «Руководители и состав направлений»: руководитель каждого направления (не назначен — так и пишется); сотрудник закрепляется за подразделением, если оно не совпадает с ролью.")
        await g.keyboard.press("Escape")
        await g.goto(B + "/directions/sales"); await g.wait_for_selector(".dir-head")
        await shot(g, "ЗГД", "Б2. Карточка направления «Коммерция»: руководитель, результат и отклонения, объекты с отклонениями (ссылки), сотрудники («по роли»/«закреплён») с задачами и поручениями, поручения, история.", full=True)
        await g.goto(B + "/directions/finance"); await g.wait_for_selector(".dir-head")
        await shot(g, "ЗГД", "Б3. Карточка «Финансы»: временное замещение руководителя показано явно («замещение до ДД.ММ: ФИО»).", clip={"x": 0, "y": 0, "width": 1440, "height": 420})

        # ---------- В. Поручение напрямую сотруднику из сигнала
        await g.goto(B + "/"); await g.wait_for_selector(".wp-exec-queue")
        btn = g.locator(".wp-exec-queue button:has-text('Поручить')").first
        await btn.click()
        await g.wait_for_selector(".sec-modal")
        await g.check(".asg-mode input >> nth=1")
        await g.select_option(".sec-modal select >> nth=0", "sales")
        await g.select_option(".sec-modal select >> nth=1", value=MGR_ID)
        await g.fill(".sec-modal textarea >> nth=0", "")
        await shot(g, "ЗГД", "В1. «Поручить» из сигнала: источник подставлен; «напрямую сотруднику»; без ожидаемого результата кнопка «Поручить» недоступна (поле обязательно, сервер тоже проверяет).")
        await g.fill(".sec-modal textarea >> nth=0", "Клиент подтвердил дату оплаты письменно; дата внесена в этап; приложено письмо клиента")
        await shot(g, "ЗГД", "В2. Заполнено: что сделать, ожидаемый результат, срок (по московской дате, прошлое недоступно), приоритет. Руководитель сотрудника увидит поручение, но результат принимает ЗГД.")
        await g.click(".sec-modal .asg-actions button:has-text('Поручить')")
        await g.wait_for_timeout(1500)
        items = api(GD, "/api/management/assignments?scope=all&status=any")["items"]
        A = max(items, key=lambda x: x["number"])
        await g.reload(); await g.wait_for_selector(".wp-exec-queue")
        await g.evaluate(f"window.scrollTo(0, {int(bb['y']) - 10})")
        await shot(g, "ЗГД", f"В3. В строке сигнала вместо кнопок — номер поручения {A['number']} и его статус: повторно поручить то же нельзя по ошибке.")

        m = await pg(MGR)
        await m.goto(B + "/assignments"); await m.wait_for_selector(".asg-table, .asg-list")
        await shot(m, "Менеджер (исполнитель)", f"В4. Исполнитель: раздел «Поручения» (меню «Ещё») → «Мне»: {A['number']} в списке; уведомление пришло в колокольчик.")
        await card(m, A["id"])
        await shot(m, "Менеджер (исполнитель)", "В5. Карточка поручения у исполнителя: ожидаемый результат, автор, срок, объект-источник; доступно «Принять» и «Комментарий».")
        await act(m, "Принять"); await m.wait_for_timeout(800)
        await act(m, "Начать"); await m.wait_for_timeout(800)
        await act(m, "Заблокировано", text="")
        await m.click(".asg-form .asg-actions button:has-text('Заблокировано')"); await m.wait_for_timeout(700)
        await shot(m, "Менеджер (исполнитель)", "В6. Блокировка без причины не принимается: «Укажите причину».")
        await m.fill(".asg-form textarea", "Бухгалтер клиента в отпуске до 26.11, письмо отправлено")
        await confirm(m, "Заблокировано")
        await shot(m, "Менеджер (исполнитель)", "В7. Статус «заблокировано», причина видна в карточке; уведомление — автору и руководителю.")
        await act(m, "Снять блокировку"); await m.wait_for_timeout(800)
        await m.set_input_files(".asg-up input", files=[{"name": "письмо клиента 25.11.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4 demo"}])
        await m.wait_for_timeout(1200)
        await act(m, "Сдать на проверку", text="Клиент письмом подтвердил оплату 26.11; дата внесена в этап «Аванс»")
        await shot(m, "Менеджер (исполнитель)", "В8. Подтверждение приложено (проходит антивирус; скачать может только участник поручения), отчёт обязателен при сдаче.")
        await confirm(m, "Сдать на проверку")
        await shot(m, "Менеджер (исполнитель)", "В9. «На проверке»: исполнителю доступны только комментарии; ждёт решения автора.")

        c = await pg(CD)
        await card(c, A["id"])
        await shot(c, "КД (руководитель направления)", "В10. Руководитель направления исполнителя видит прямое поручение и может комментировать, но НЕ принимает результат (кнопки «Принять результат» нет; сервер тоже запрещает).")
        law = await pg(LAW)
        await law.goto(f"{B}/assignments?open={A['id']}"); await law.wait_for_timeout(1500)
        await shot(law, "Юрист (посторонний)", "В11. Посторонний сотрудник по прямой ссылке: «Поручение не найдено» (404 на сервере), файлы тоже недоступны.")

        await card(g, A["id"])
        await act(g, "Вернуть на доработку", text="Нужна выписка банка или письмо с датой платёжки")
        await confirm(g, "Вернуть на доработку")
        await shot(g, "ЗГД", "В12. ЗГД возвращает на доработку — только с причиной; исполнитель получает уведомление.")
        await card(m, A["id"])
        await act(m, "Сдать на проверку", text="Приложил письмо клиента с номером платёжки 1532 от 25.11")
        await confirm(m, "Сдать на проверку")
        await card(g, A["id"])
        await act(g, "Перенести срок", text="Ждём поступление по банку", date=(__import__("datetime").date.fromisoformat(A["due_on"]) + __import__("datetime").timedelta(days=2)).isoformat())
        await shot(g, "ЗГД", "В13. Перенос срока — только автор/ЗГД и только с причиной; первоначальный срок сохраняется.")
        await confirm(g, "Перенести срок")
        await act(g, "Принять результат"); await g.wait_for_timeout(900)
        await shot(g, "ЗГД", "В14. Результат принят — «выполнено». История: все действия с автором и временем (создано, принято, начато, заблокировано, снято, файл, сдано, возвращено, сдано, перенос срока, принято).", full=True)

        # ---------- Г. Поручение руководителю подразделения
        await g.goto(B + "/assignments?new=1"); await g.wait_for_selector(".sec-modal")
        await g.select_option(".sec-modal select >> nth=0", "sales")
        await g.fill(".sec-modal input >> nth=2", "Подготовить план продаж на декабрь по клиентам")
        await g.fill(".sec-modal textarea >> nth=0", "План по клиентам и суммам, согласован с менеджерами")
        await shot(g, "ЗГД", "Г1. Поручение руководителю подразделения: выбирается подразделение, система показывает назначенного руководителя (не назначен — поручить нельзя).")
        await g.click(".sec-modal .asg-actions button:has-text('Поручить')"); await g.wait_for_timeout(1500)
        items = api(GD, "/api/management/assignments?scope=all&status=any")["items"]
        Hh = max(items, key=lambda x: x["number"])
        await card(c, Hh["id"])
        await act(c, "Принять"); await c.wait_for_timeout(800)
        await act(c, "Передать исполнителю", select="Бочаров Егор (менеджер)")
        await shot(c, "КД (руководитель направления)", "Г2. Руководитель принимает и передаёт исполнителю: в списке только сотрудники его подразделения (справочник всех сотрудников ему не выдаётся).")
        await confirm(c, "Передать исполнителю")
        await card(m, Hh["id"])
        await shot(m, "Менеджер (исполнитель)", "Г3. Исполнитель видит поручение руководителя с отметкой «Исполнитель»; принимать поручение может только руководитель — исполнитель начинает работу и сдаёт результат.")
        await act(m, "Начать"); await m.wait_for_timeout(800)

        # ---------- Д. Контроль ЗГД
        await g.goto(B + "/"); await g.wait_for_selector(".wp-exec-asg")
        asg = await g.query_selector(".wp-exec-grid2")
        bb2 = await asg.bounding_box()
        await g.evaluate(f"window.scrollTo(0, {int(bb2['y']) - 10})")
        await shot(g, "ЗГД", "Д1. Блок «Поручения»: в работе / просрочено / срок сегодня / на проверке; направления с руководителями, замещением, результатом и отклонениями.")
        await g.click(".wp-exec-asg a >> nth=0"); await g.wait_for_selector(".asg-table, .asg-list")
        await shot(g, "ЗГД", "Д2. Счётчик открывает реестр с тем же фильтром («в работе») — число строк совпадает с цифрой на пульте (проверяется тестом).")
        await g.goto(B + "/"); await g.wait_for_selector(".wp-exec-queue", timeout=30000)
        await g.click(".wp-exec-tabs button:has-text('Все проблемы')"); await g.wait_for_timeout(1500)
        await shot(g, "ЗГД", "Д3. «Все проблемы»: фильтр по приоритету, группы по объекту; ниже — «Устранено за 30 дней» (когда появился, когда ушёл, сколько длился, поручение).", full=True)

        # ---------- Е. Ширины
        for w, h in ((1280, 900), (768, 1024), (390, 844), (360, 780)):
            r = await pg(GD, w, h)
            await r.goto(B + "/"); await r.wait_for_selector(".wp-exec-queue", timeout=30000)
            sw = await r.evaluate("document.documentElement.scrollWidth")
            await shot(r, "ЗГД", f"Е. Ширина {w}: пульт без горизонтальной прокрутки (ширина документа {sw} px).", full=(w <= 768))
        r = await pg(MGR, 390, 844)
        await card(r, A["id"])
        sw = await r.evaluate("document.documentElement.scrollWidth")
        await shot(r, "Менеджер", f"Е. Телефон 390: карточка поручения в одну колонку, история и файлы (ширина документа {sw} px).", full=True)

        json.dump({"captions": CAP, "errors": errs, "A": A["number"], "H": Hh["number"]}, open(OUT + "/captions.json", "w"), ensure_ascii=False, indent=1)
        print("shots", len(CAP), "errors", errs[:5])
        await br.close()

asyncio.run(main())
