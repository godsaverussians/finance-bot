from __future__ import annotations

import re

# Разрешаем только цифры и арифметику — eval ниже не увидит ничего другого.
_SAFE_EXPRESSION = re.compile(r"^[0-9+\-*/(). ]+$")
_THOUSAND_SUFFIX = re.compile(r"^(.*?)(к|k|тыс|тысяч[аи]?)\s*$")
_DIGIT_GAP = re.compile(r"(?<=\d)[\s\u00a0](?=\d)")

MAX_KOPECKS = 10**11  # миллиард рублей, защита от опечаток вида 1500000000000


def parse_amount(text: str) -> int | None:
    """Принимает '1500', '1 500,50', '1.5к', '300+450'. Возвращает копейки."""
    raw = (text or "").strip().lower()
    for junk in ("₽", "руб.", "рублей", "руб", "р.", "rub"):
        raw = raw.replace(junk, "")
    raw = raw.replace(",", ".").strip()

    multiplier = 1
    suffix = _THOUSAND_SUFFIX.match(raw)
    if suffix:
        raw = suffix.group(1)
        multiplier = 1000

    raw = _DIGIT_GAP.sub("", raw).strip()
    if not raw or not _SAFE_EXPRESSION.match(raw) or not re.search(r"\d", raw):
        return None

    try:
        value = eval(raw, {"__builtins__": {}}, {})  # noqa: S307 — вход отфильтрован выше
    except Exception:
        return None

    if not isinstance(value, (int, float)):
        return None

    kopecks = int(round(value * multiplier * 100))
    if kopecks <= 0 or kopecks > MAX_KOPECKS:
        return None
    return kopecks


def format_amount(kopecks: int) -> str:
    rubles, remainder = divmod(abs(kopecks), 100)
    whole = f"{rubles:,}".replace(",", " ")
    if remainder:
        return f"{whole},{remainder:02d} ₽"
    return f"{whole} ₽"
