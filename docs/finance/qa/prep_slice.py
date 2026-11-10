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
