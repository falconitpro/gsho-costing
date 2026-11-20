

def test_action_rules_collected_from_code():
    """D-115: «Кто что может делать» собирается из кода — тексты отказов 403 и роли методов API."""
    from app.services import action_rules
    d = action_rules.collect()
    rules = [r for s in d["sections"] for r in s["rules"]]
    assert d["total"] == len(rules) > 100
    assert "Вернуть сделку в работу могут КД или ЗГД" in rules
    assert any(r.startswith("Виза этой ступени") for r in rules)
    assert [x["role"] for x in d["ladder"]] == ["КД", "ФД", "ЗГД"]
    assert all(s["rules"] or s["silent"] for s in d["sections"])
