from app.adapters.company_registry import RegistryNotConfigured, inn_valid, make_registry, parse_dadata


def test_inn_checksum():
    assert inn_valid("7707083893") and inn_valid("500100732259")
    assert not inn_valid("7707083890") and not inn_valid("123") and not inn_valid("77070838931")


def test_parse_dadata_party():
    s = {"value": "ООО «РОМАШКА»", "data": {"inn": "7707083893", "kpp": "770701001", "ogrn": "1027700132195", "okved": "46.69", "type": "LEGAL",
                                           "name": {"short_with_opf": "ООО «РОМАШКА»", "full_with_opf": "ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ «РОМАШКА»"},
                                           "management": {"name": "Иванов Иван Иванович", "post": "ГЕНЕРАЛЬНЫЙ ДИРЕКТОР"},
                                           "state": {"status": "ACTIVE", "registration_date": 1262304000000},
                                           "address": {"value": "г Москва", "unrestricted_value": "101000, г Москва, ул Мясницкая, д 1"}}}
    c = parse_dadata(s)
    assert c.name == "ООО «РОМАШКА»" and c.kpp == "770701001" and c.director == "ГЕНЕРАЛЬНЫЙ ДИРЕКТОР, Иванов Иван Иванович"
    assert c.address.startswith("101000") and c.status == "ACTIVE" and c.registered_on == "2010-01-01"


def test_registry_without_key_is_explicit():
    import pytest
    with pytest.raises(RegistryNotConfigured):
        make_registry("dadata", "")
    with pytest.raises(RegistryNotConfigured):
        make_registry("none").find_by_inn("7707083893")
