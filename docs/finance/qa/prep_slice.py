import httpx, json
B="http://localhost:8000"; H=lambda e:{"X-Dev-User":e}
LAW,GD="lawyer@example.com","evgeniy@dobrov.pro"
c=httpx.Client(base_url=B, timeout=30)
ent=c.get("/api/legal/contracts",headers=H(LAW)).json()["entities"][0]
k=c.post("/api/legal/contracts",headers=H(LAW),json={"kind":"client_supply","own_entity_id":ent["id"],"cp_name":"АО «Алданзолото»","cp_inn":"1402000001","subject":"поставка ГШО","amount":12500000,"currency":"RUB"}).json()
k=c.put(f"/api/legal/contracts/{k['id']}",headers=H(LAW),json={"payment_terms":[{"share_pct":20,"event":"contract","offset_days":5,"offset_kind":"working"},{"share_pct":10,"event":"contract","offset_days":30,"offset_kind":"calendar"},{"share_pct":70,"event":"act_signed","offset_days":10,"offset_kind":"calendar"}],"signatory":{"name":"Петров П. П.","basis":"Устава"},"law":{"claim_days":30},"signed_on":"2026-10-27"}).json()
c.post(f"/api/legal/contracts/{k['id']}/files",headers=H(LAW),files={"file":("scan.pdf",b"%PDF-1.4 x","application/pdf")},data={"kind":"signed_scan"})
r=c.post(f"/api/legal/contracts/{k['id']}/status",headers=H(LAW),json={"status":"active"}); print("contract", r.status_code, k["number"])
own=c.get("/api/legal/own",headers=H(GD)).json()["items"]; prof=next(e for e in own if e["id"]==ent["id"])["profiles"][0]
r=c.post(f"/api/legal/profiles/{prof['id']}/banks",headers=H(GD),json={"title":"Расчётный RUB","currency":"RUB","bank_name":"ПАО Сбербанк","account":"40702810400000012345","bik":"044525225"}); print("bank", r.status_code)
json.dump({"contract_id":k["id"],"number":k["number"]},open("prep.json","w"))
# сделка на стадии контракта с платёжным этапом и договором по ней (для сценария старой отметки оплаты)
import subprocess, os
cl=c.get("/api/refs/clients",headers=H("manager@example.com")).json()[0]
d=c.post("/api/deals",headers=H("manager@example.com"),json={"client_id":cl["id"],"site":"Карьер Северный"}).json()
code=f'''
import datetime
from app.db import SessionLocal
from app.models import Deal, ExecutionStage
from app.core.workflow import DealStatus
db=SessionLocal(); deal=db.get(Deal,"{d["id"]}"); deal.status=DealStatus.CONTRACT
db.add(ExecutionStage(deal_id=deal.id, code="client_advance", title="Аванс от клиента", sort=30, amount=4000000, currency="RUB", responsible_roles=["fd"], planned_on=datetime.date(2026,11,9)))
db.commit()
'''
env=dict(os.environ, DATABASE_URL="postgresql+psycopg://gsho:gsho@localhost:5432/gsho_qa")
subprocess.run(["python","-c",code],cwd=os.environ.get("BACKEND_DIR", "backend"),env=env,check=True,capture_output=True)
k2=c.post("/api/legal/contracts",headers=H(LAW),json={"kind":"client_supply","own_entity_id":ent["id"],"cp_name":"ООО «Северный карьер»","cp_inn":"2401000001","subject":"поставка ГШО","amount":20000000,"currency":"RUB","deal_id":d["id"]}).json()
c.put(f"/api/legal/contracts/{k2['id']}",headers=H(LAW),json={"payment_terms":[{"share_pct":20,"event":"contract","offset_days":5,"offset_kind":"working"},{"share_pct":80,"event":"act_signed","offset_days":10,"offset_kind":"calendar"}],"signatory":{"name":"С. С.","basis":"Устава"},"law":{"claim_days":30},"signed_on":"2026-11-03"})
c.post(f"/api/legal/contracts/{k2['id']}/files",headers=H(LAW),files={"file":("scan.pdf",b"%PDF-1.4 x","application/pdf")},data={"kind":"signed_scan"})
print("deal contract", c.post(f"/api/legal/contracts/{k2['id']}/status",headers=H(LAW),json={"status":"active"}).status_code)
json.dump({"contract_id":k["id"],"number":k["number"],"deal_id":d["id"],"deal_number":d["number"]},open("prep.json","w"))
# заказ поставщику с графиком оплат и партия с платежами (источники этапа 2)
code2 = '''
from app.db import SessionLocal
from app.models import PurchaseOrder, Factory, LegalEntity, Deal
from app.core.workflow import DealStatus
from sqlalchemy import select
db=SessionLocal(); sup=db.scalars(select(Factory)).first(); ent=db.scalars(select(LegalEntity)).first()
po=PurchaseOrder(number="ЗК-2026-0901", kind="external", supplier_id=sup.id, buyer_entity_id=ent.id, currency="CNY", status="sent"); db.add(po)
d=Deal(**{}) if False else None
db.commit(); print(po.id)
'''
po_id=subprocess.run(["python","-c",code2],cwd=os.environ.get("BACKEND_DIR", "backend"),env=env,check=True,capture_output=True,text=True).stdout.strip().splitlines()[-1]
print("po confirm", c.post(f"/api/parts/po/{po_id}/confirm",headers=H(GD),json={"supplier_ref":"PI-2026-77","payments":[{"due":"2026-11-14","amount":120000,"comment":"аванс 30 %"},{"due":"2026-12-10","amount":280000,"comment":"до отгрузки"}]}).status_code)
d2=c.post("/api/deals",headers=H("manager@example.com"),json={"client_id":cl["id"],"site":"Логистика тест"}).json()
subprocess.run(["python","-c",f'''
from app.db import SessionLocal
from app.models import Deal
from app.core.workflow import DealStatus
db=SessionLocal(); db.get(Deal,"{d2["id"]}").status=DealStatus.LOGISTICS_CALC; db.commit()'''],cwd=os.environ.get("BACKEND_DIR", "backend"),env=env,check=True,capture_output=True)
print("batch", c.post(f"/api/deals/{d2['id']}/logistics/batches",headers=H(GD),json={"name":"Партия 1","payments":[{"due":"2026-11-25","amount":450000,"currency":"RUB","payee":"ООО «Брокер Забайкальск»","comment":"брокер и СВХ"},{"due":"2026-11-28","amount":38000,"payee":"ТК «Восток»","comment":"фрахт"}]}).status_code)
print("undated", c.get("/api/finance/undated",headers=H("fd@example.com")).json()["parts"]["total"])
# заявка на запчасти: заказана клиентом, не отгружена — блок «ожидается, срок не согласован»
subprocess.run(["python","-c",f'''
from app.db import SessionLocal
from app.models import PartRequest, PartRequestLine, User
from sqlalchemy import select
db=SessionLocal(); m=db.scalar(select(User).where(User.email=="manager@example.com"))
r=PartRequest(number="ЗЧ-2026-0042", client_id="{cl["id"]}", manager_id=m.id, machine="XCMG XDR80T № 7", urgent=True); db.add(r); db.flush()
for i,(n,q,s) in enumerate([("Фильтр топливный",4,48000),("Ремкомплект гидроцилиндра",1,186500)]):
    db.add(PartRequestLine(request_id=r.id,no=i+1,raw_name=n,qty=q,price=s/q,status="ordered",calc={{"sum_rub":s}}))
db.commit()'''],cwd=os.environ.get("BACKEND_DIR", "backend"),env=env,check=True,capture_output=True)
print("undated after", c.get("/api/finance/undated",headers=H("fd@example.com")).json()["parts"]["total"])
