"""Страна-производитель: справочник стран и приведение значения из файла к названию страны
(без провинции, города и прочего: «China, Shandong, Jinan» / «中国山东» / «CN» → «Китай»)."""
from __future__ import annotations

import re

# название (как выводим) → синонимы: EN, CN, коды ISO, прилагательные
COUNTRIES: dict[str, tuple[str, ...]] = {
    "Китай": ("china", "prc", "p.r.c", "p. r. china", "chinese", "cn", "chn", "кнр", "китайская народная республика", "китай", "中国", "中國", "中华人民共和国", "中华"),
    "Россия": ("russia", "russian federation", "ru", "rus", "рф", "российская федерация", "россия", "俄罗斯"),
    "Беларусь": ("belarus", "by", "blr", "белоруссия", "беларусь", "республика беларусь", "白俄罗斯"),
    "Казахстан": ("kazakhstan", "kz", "kaz", "казахстан", "哈萨克斯坦"),
    "Узбекистан": ("uzbekistan", "uz", "uzb", "узбекистан", "乌兹别克斯坦"),
    "Кыргызстан": ("kyrgyzstan", "kg", "киргизия", "кыргызстан", "吉尔吉斯斯坦"),
    "Армения": ("armenia", "am", "армения", "亚美尼亚"),
    "Монголия": ("mongolia", "mn", "монголия", "蒙古"),
    "Германия": ("germany", "deutschland", "de", "deu", "германия", "фрг", "德国"),
    "Швеция": ("sweden", "se", "swe", "швеция", "瑞典"),
    "Финляндия": ("finland", "fi", "fin", "финляндия", "芬兰"),
    "Норвегия": ("norway", "no", "nor", "норвегия", "挪威"),
    "Австрия": ("austria", "at", "aut", "австрия", "奥地利"),
    "Швейцария": ("switzerland", "ch", "che", "швейцария", "瑞士"),
    "Италия": ("italy", "it", "ita", "италия", "意大利"),
    "Франция": ("france", "fr", "fra", "франция", "法国"),
    "Испания": ("spain", "es", "esp", "испания", "西班牙"),
    "Великобритания": ("united kingdom", "great britain", "england", "uk", "gb", "gbr", "великобритания", "англия", "英国"),
    "Нидерланды": ("netherlands", "holland", "nl", "nld", "нидерланды", "голландия", "荷兰"),
    "Бельгия": ("belgium", "be", "bel", "бельгия", "比利时"),
    "Польша": ("poland", "pl", "pol", "польша", "波兰"),
    "Чехия": ("czech republic", "czechia", "cz", "cze", "чехия", "捷克"),
    "Словакия": ("slovakia", "sk", "svk", "словакия", "斯洛伐克"),
    "Сербия": ("serbia", "rs", "srb", "сербия", "塞尔维亚"),
    "Турция": ("turkey", "turkiye", "türkiye", "tr", "tur", "турция", "土耳其"),
    "Индия": ("india", "in", "ind", "индия", "印度"),
    "Япония": ("japan", "jp", "jpn", "япония", "日本"),
    "Республика Корея": ("south korea", "republic of korea", "korea", "kr", "kor", "корея", "южная корея", "республика корея", "韩国"),
    "США": ("united states", "usa", "u.s.a", "us", "america", "сша", "соединенные штаты", "соединённые штаты", "美国"),
    "Канада": ("canada", "ca", "can", "канада", "加拿大"),
    "Бразилия": ("brazil", "br", "bra", "бразилия", "巴西"),
    "Австралия": ("australia", "au", "aus", "австралия", "澳大利亚"),
    "ЮАР": ("south africa", "rsa", "za", "zaf", "юар", "южно-африканская республика", "南非"),
    "Иран": ("iran", "ir", "irn", "иран", "伊朗"),
    "Вьетнам": ("vietnam", "viet nam", "vn", "vnm", "вьетнам", "越南"),
}

# провинции, крупные города и производственные центры КНР — значит «Китай»
CHINA_PLACES = (
    "shandong", "jiangsu", "zhejiang", "hebei", "henan", "hubei", "hunan", "shanxi", "shaanxi", "liaoning", "jilin", "heilongjiang", "anhui",
    "fujian", "jiangxi", "guangdong", "guangxi", "sichuan", "guizhou", "yunnan", "gansu", "qinghai", "inner mongolia", "ningxia", "xinjiang",
    "beijing", "shanghai", "tianjin", "chongqing", "hainan", "xuzhou", "jinan", "yantai", "qingdao", "linyi", "jining", "weifang", "zibo",
    "taiyuan", "changsha", "zhengzhou", "shenyang", "wuxi", "suzhou", "luoyang", "tangshan", "baotou", "xiamen", "zhuzhou",
    "шаньдун", "цзянсу", "чжэцзян", "хэбэй", "хэнань", "шаньси", "шэньси", "ляонин", "сюйчжоу", "цзинань", "яньтай", "циндао", "линьи",
    "山东", "江苏", "浙江", "河北", "河南", "湖北", "湖南", "山西", "陕西", "辽宁", "吉林", "黑龙江", "安徽", "福建", "江西", "广东", "广西",
    "四川", "贵州", "云南", "甘肃", "内蒙古", "新疆", "北京", "上海", "天津", "重庆", "徐州", "济南", "烟台", "青岛", "临沂", "济宁", "潍坊", "太原", "长沙", "郑州",
)

_SPLIT = re.compile(r"[,;/\\|()\[\]（）、，·\-–—]+|\s+")


def country_names() -> list[str]:
    return list(COUNTRIES)


def normalize_country(value) -> str:
    """Название страны из справочника; не распознано — первая часть значения (до запятой / скобки), без провинций и адреса."""
    text = str(value or "").strip()
    if not text:
        return ""
    low = text.lower().replace("ё", "е")
    if text in COUNTRIES:
        return text
    tokens = [t for t in _SPLIT.split(low) if t]
    upper_tokens = {t.lower() for t in _SPLIT.split(text) if t and t.isupper()}   # коды стран пишут заглавными: CN, RU, USA
    joined = " ".join(tokens)
    for name, aliases in COUNTRIES.items():
        for a in aliases:
            a = a.lower().replace("ё", "е")
            if re.search(r"[一-鿿]", a):          # иероглифы — подстрокой
                if a in text:
                    return name
            elif len(a) <= 3:                              # коды — отдельным словом заглавными или всё значение («cn»)
                if a in upper_tokens or tokens == [a]:
                    return name
            elif re.search(rf"(^|\s){re.escape(a)}(\s|$)", joined) or low.startswith(a):
                return name
    if any((p in text) if re.search(r"[一-鿿]", p) else re.search(rf"(^|\s){re.escape(p)}(\s|$)", joined) for p in CHINA_PLACES):
        return "Китай"
    first = re.split(r"[,;/(（，]", text)[0].strip()
    return first[:1].upper() + first[1:] if first else text


def is_country_row(code: str, name: str) -> bool:
    """Строка «Страна-производитель» таблицы технических характеристик (код 1.3 шаблона RFQ или по названию)."""
    if (code or "").strip() == "1.3":
        return True
    n = (name or "").strip().lower()
    return n.startswith("страна") or n.startswith("country of origin") or n.startswith("country") or n.startswith("原产国") or n.startswith("生产国")
