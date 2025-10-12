import re

from num2words import num2words

CURRENCY_MAP = {
    '$': ('доллар', 'доллара', 'долларов'),
    '€': ('евро', 'евро', 'евро'),
    '£': ('фунт', 'фунта', 'фунтов'),
    '₽': ('рубль', 'рубля', 'рублей'),
    '¥': ('иена', 'иены', 'иен'),
}


def declension_ru(n, forms):
    """Выбирает правильную форму слова по правилу русского языка."""
    try:
        n = abs(int(n))
    except:
        return forms[2]
    n_mod100 = n % 100
    if 11 <= n_mod100 <= 19:
        return forms[2]
    n_mod10 = n % 10
    if n_mod10 == 1:
        return forms[0]
    if 2 <= n_mod10 <= 4:
        return forms[1]
    return forms[2]


def parse_number_str(s):
    """Возвращает (int_part_str, frac_part_str) после разбора тысячных/десятичных сепараторов."""
    s = s.replace('\u00A0', ' ').strip()
    # определяем, что является разделителем тысяч, а что десятичным
    if ',' in s and '.' in s:
        if s.rfind('.') > s.rfind(','):
            thousands_sep = ','
            decimal_sep = '.'
        else:
            thousands_sep = '.'
            decimal_sep = ','
    elif ',' in s:
        # если запятые стоят как 1,234,567 — считаем их тысячными
        if re.match(r'^\d{1,3}(?:,\d{3})+(?:,\d+)?$', s):
            thousands_sep = ','
            decimal_sep = None
        else:
            thousands_sep = None
            decimal_sep = ','
    elif '.' in s:
        if re.match(r'^\d{1,3}(?:\.\d{3})+(?:\.\d+)?$', s):
            thousands_sep = '.'
            decimal_sep = None
        else:
            thousands_sep = None
            decimal_sep = '.'
    else:
        thousands_sep = None
        decimal_sep = None

    ns = s
    if thousands_sep:
        ns = ns.replace(thousands_sep, '')
    if decimal_sep:
        ns = ns.replace(decimal_sep, '.')
    ns = ns.replace(' ', '')
    if '.' in ns:
        int_part, frac_part = ns.split('.', 1)
    else:
        int_part, frac_part = ns, ''
    int_part = re.sub(r'\D', '', int_part)
    frac_part = re.sub(r'\D', '', frac_part)
    return int_part, frac_part


def number_to_words_ru(s):
    """Переводит число (строку) в слова (русский). Дробная часть — как 'запятая один два'."""
    int_part, frac_part = parse_number_str(s)
    if int_part == '':
        return s
    n = int(int_part)
    words = num2words(n, lang='ru')
    if frac_part:
        # дробную часть произносим по-цифрам после слова "запятая"
        digits_words = ' '.join(num2words(int(d), lang='ru') for d in frac_part)
        return f"{words} запятая {digits_words}"
    return words


def replace_currency_symbols(text):
    """Обрабатывает случаи вида '$444,613' и '444,613 USD' и т.п."""

    # символ перед числом, например $444,613
    def repl_sym_before(m):
        sym = m.group('symbol')
        num = m.group('number')
        int_part, frac_part = parse_number_str(num)
        if int_part == '':
            return m.group(0)
        n = int(int_part)
        words = num2words(n, lang='ru')
        forms = CURRENCY_MAP.get(sym, (sym, sym, sym))
        curword = declension_ru(n, forms)
        if frac_part:
            frac_words = ' '.join(num2words(int(d), lang='ru') for d in frac_part)
            return f"{words} {curword} запятая {frac_words}"
        return f"{words} {curword}"

    text = re.sub(r'(?P<symbol>[$€£₽¥])\s*(?P<number>[\d\s\.,]+)', repl_sym_before, text)

    # число перед аббревиатурой валюта: "444,613 USD" или "444,613 руб."
    def repl_num_after(m):
        num = m.group('number')
        curr = m.group('curr')
        int_part, frac_part = parse_number_str(num)
        if int_part == '':
            return m.group(0)
        n = int(int_part)
        words = num2words(n, lang='ru')
        curr_low = curr.lower()
        if curr_low in ('usd',):
            forms = CURRENCY_MAP.get('$')
        elif curr_low in ('eur',):
            forms = CURRENCY_MAP.get('€')
        elif curr_low in ('gbp',):
            forms = CURRENCY_MAP.get('£')
        elif curr_low in ('rub', 'руб', 'руб.', 'rur'):
            forms = CURRENCY_MAP.get('₽')
        else:
            forms = (curr, curr, curr)
        curword = declension_ru(n, forms)
        if frac_part:
            frac_words = ' '.join(num2words(int(d), lang='ru') for d in frac_part)
            return f"{words} {curword} запятая {frac_words}"
        return f"{words} {curword}"

    text = re.sub(r'(?P<number>[\d\s\.,]+)\s*(?P<curr>USD|EUR|GBP|RUB|руб\.|руб|RUB|USD|EUR|GBP)', repl_num_after, text,
                  flags=re.IGNORECASE)
    return text


def normalize_text_for_tts(text: str) -> str:
    """Главная функция: нормализует валюты и все числа."""
    text = text.replace('\u00A0', ' ')
    text = replace_currency_symbols(text)

    # затем оставшиеся числа (без валют)
    def repl_num(m):
        s = m.group(0)
        return number_to_words_ru(s)

    text = re.sub(r'\d[\d\s\.,]*\d|\b\d+\b', repl_num, text)

    # убрать лишние пробелы и привести знаки к аккуратному виду:
    text = re.sub(r'\s+', ' ', text).strip()
    # убрать пробел перед знаками пунктуации
    text = re.sub(r'\s+([,.;:?!])', r'\1', text)
    # вставить пробел после знаков пунктуации, если их нет
    text = re.sub(r'([,.;:?!])(?=[^\s"\'\)\]\}])', r'\1 ', text)
    return text


if __name__ == "__main__":
    sample = "доступно 2 956, общий баланс 3420, что эквивалентно $44,613"
    print(normalize_text_for_tts(sample))
