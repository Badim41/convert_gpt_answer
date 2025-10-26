import re
from num2words import num2words

CURRENCY_MAP = {
    '$': ('доллар', 'доллара', 'долларов'),
    '€': ('евро', 'евро', 'евро'),
    '£': ('фунт', 'фунта', 'фунтов'),
    '₽': ('рубль', 'рубля', 'рублей'),
    '¥': ('иена', 'иены', 'иен'),
}

WORD_RE = re.compile(r"[^\W\d_]+", flags=re.UNICODE)
TERM_CHARS = ".!?"
OP_NEAR = set("+-*/=")

def declension_ru(n, forms):
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
    s = s.replace('\u00A0', ' ').strip()
    if ',' in s and '.' in s:
        if s.rfind('.') > s.rfind(','):
            thousands_sep = ','
            decimal_sep = '.'
        else:
            thousands_sep = '.'
            decimal_sep = ','
    elif ',' in s:
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
    int_part, frac_part = parse_number_str(s)
    if int_part == '':
        return s
    n = int(int_part)
    words = num2words(n, lang='ru')
    if frac_part:
        frac_trim = frac_part.rstrip('0')
        if frac_trim == '':
            return words
        # Для дробной части: если более одной цифры — читать как число (шестьсот тринадцать)
        if len(frac_trim) > 1:
            frac_words = num2words(int(frac_trim), lang='ru')
        else:
            frac_words = num2words(int(frac_trim), lang='ru')
        return f"{words} запятая {frac_words}"
    return words

def replace_currency_symbols(text):
    """
    Различаем случаи:
      - raw вида '44,613' (одна запятая, правая часть ровно 3 цифры и левая <=3) — считаем запятую десятичной.
        В таком случае ожидаем формат: "<int_words> запятая <frac_words> <currency_word>"
      - в остальных случаях (например '1,234.50' или '1,234.50' с точкой) — если есть дробная часть по точке,
        формат: "<int_words> <currency_word> запятая <frac_words>"
    """
    def decide_int_frac_from_raw(raw):
        raw = raw.strip()
        # случай: одна запятая, нет точки, правая часть ровно 3 цифры и левая не больше 3 -> интерпретируем как DECIMAL with comma
        if raw.count(',') == 1 and '.' not in raw:
            left, right = raw.split(',', 1)
            left_digits = re.sub(r'\D', '', left)
            right_digits = re.sub(r'\D', '', right)
            if len(right_digits) == 3 and 0 < len(left_digits) <= 3:
                return left_digits, right_digits, True
        # иначе — используем общий парсер
        int_part, frac_part = parse_number_str(raw)
        # если мы получили дробную часть из точки — пометим как point_decimal = True
        point_decimal = ('.' in raw) and (frac_part != '')
        return int_part, frac_part, point_decimal

    def repl_sym_before(m):
        sym = m.group('symbol')
        raw_num = m.group('number')
        int_part, frac_part, comma_decimal = decide_int_frac_from_raw(raw_num)
        if int_part == '':
            return m.group(0)
        n = int(int_part)
        int_words = num2words(n, lang='ru')
        forms = CURRENCY_MAP.get(sym, (sym, sym, sym))
        curword = declension_ru(n, forms)
        if frac_part:
            frac_trim = frac_part.rstrip('0')
            if frac_trim == '':
                # нет значимой дробной части
                if comma_decimal:
                    return f" {int_words} {curword} "
                else:
                    return f" {int_words} {curword} "
            # читаем дробную часть как целое число (например 613 -> шестьсот тринадцать)
            frac_words = num2words(int(frac_trim), lang='ru')
            if comma_decimal:
                # формат: int запятая frac currency
                return f" {int_words} запятая {frac_words} {forms[2]} "
            else:
                # точечная дробь или иная — ставим валюту после целой части
                return f" {int_words} {curword} запятая {frac_words} "
        else:
            return f" {int_words} {curword} "

    text = re.sub(r'(?P<symbol>[$€£₽¥])\s*(?P<number>[\d\s\.,]+)', repl_sym_before, text)

    def repl_num_after(m):
        raw_num = m.group('number')
        curr = m.group('curr')
        int_part, frac_part, comma_decimal = decide_int_frac_from_raw(raw_num)
        if int_part == '':
            return m.group(0)
        n = int(int_part)
        int_words = num2words(n, lang='ru')
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
            frac_trim = frac_part.rstrip('0')
            if frac_trim == '':
                if comma_decimal:
                    return f" {int_words} {forms[2]} "
                else:
                    return f" {int_words} {forms[2]} "
            frac_words = num2words(int(frac_trim), lang='ru')
            if comma_decimal:
                return f" {int_words} запятая {frac_words} {forms[2]} "
            else:
                return f" {int_words} {curword} запятая {frac_words} "
        else:
            return f" {int_words} {forms[2]} "

    text = re.sub(
        r'(?P<number>[\d\s\.,]+)\s*(?P<curr>USD|EUR|GBP|RUB|руб\.|руб|RUR|USD|EUR|GBP)',
        repl_num_after,
        text,
        flags=re.IGNORECASE
    )
    return text

def replace_square_brackets(text: str) -> str:
    pattern = re.compile(r'\[([^\[\]]*)\]')
    prev = None
    while prev != text:
        prev = text
        text = pattern.sub(lambda m: f" в квадратных скобках {m.group(1).strip()} ", text)
    return text

def handle_minus_in_numeric_contexts(text: str) -> str:
    s = text.replace('−', '-')
    s = re.sub(r'([€$£₽¥])\s*-\s*([\d\s\.,]+)', r'минус \1\2', s)
    s = re.sub(r'(?:(?<=^)|(?<=[\(\[\{<]))-\s*(?=\d)', 'минус ', s)

    pat = re.compile(
        r'(?P<a>\d[\d\s\.,]*\d|\d)'
        r'(?P<lws>\s*)-(?P<rws>\s*)'
        r'(?P<b>\d[\d\s\.,]*\d|\d)'
    )

    def repl(m: re.Match) -> str:
        full = m.string
        a_start = m.start('a')
        b_end = m.end('b')

        j = b_end
        while j < len(full) and full[j].isspace():
            j += 1
        right_char = full[j] if j < len(full) else ''

        i = a_start - 1
        while i >= 0 and full[i].isspace():
            i -= 1
        left_char = full[i] if i >= 0 else ''

        spaced = (len(m.group('lws')) > 0) or (len(m.group('rws')) > 0)
        near = spaced or (right_char in OP_NEAR) or (left_char in OP_NEAR)

        if right_char.isalpha() or left_char.isalpha():
            near = False

        if near:
            return f"{m.group('a')} минус {m.group('b')}"
        else:
            return f"{m.group('a')} {m.group('b')}"

    s = pat.sub(repl, s)
    return s

def strip_operator_tail_after_num_minus(text: str) -> str:
    text = re.sub(
        r'(?P<a>\d[\d\s\.,]*\d|\d)\s*-\s*(?P<b>\d[\d\s\.,]*\d|\d)\s+[+\-*/\\]+\s*$',
        r'\g<a>-\g<b>',
        text
    )
    text = re.sub(
        r'(?P<a>\d[\d\s\.,]*\d|\d)\s+минус\s+(?P<b>\d[\d\s\.,]*\d|\d)\s+[+\-*/\\]+\s*$',
        r'\g<a> минус \g<b>',
        text
    )
    return text

def replace_numero(text: str) -> str:
    return re.sub(r'№\s*([\d\s\.,]+)', lambda m: f" номер {m.group(1)} ", text)

def replace_simple_symbols(text: str) -> str:
    mapping = {
        '#': 'решетка',
        '@': 'собака',
        '^': 'стрелка вверх',
        '<': 'меньше',
        '>': 'больше',
        '*': 'звёздочка',
        '/': 'слэш',
        '\\': 'бэкслэш',
        '=': 'ровно',
        '+': 'плюс'
    }
    chars = ''.join(mapping.keys())
    pattern = '[' + re.escape(chars) + ']'
    return re.sub(pattern, lambda m: f" {mapping[m.group(0)]} ", text)

def normalize_text_for_tts(text: str) -> str:
    if not text:
        return ""

    text = text.replace('\u00A0', ' ')
    text = replace_square_brackets(text)
    text = handle_minus_in_numeric_contexts(text)
    text = strip_operator_tail_after_num_minus(text)
    text = replace_currency_symbols(text)
    text = replace_numero(text)
    text = replace_simple_symbols(text)

    letter = r'[A-Za-zА-Яа-яЁё]'
    text = re.sub(rf'(?<={letter})\.(?={letter})', ' точка ', text)

    def repl_num(m):
        return number_to_words_ru(m.group(0))
    text = re.sub(r'\b\d{1,3}(?:[ \u00A0]\d{3})+(?:[.,]\d+)?\b|\b\d+\b', repl_num, text)

    # привести пробелы и пунктуацию в порядок
    # text = text.replace(',', ' ')
    text = re.sub(r'\s+', ' ', text).strip()
    # исправить случаи слипшихся "иX" -> "и X"
    text = re.sub(r'\bи(?=[А-Яа-яЁё])', 'и ', text)

    text = re.sub(r'\s+([,.;:?!])', r'\1', text)
    text = re.sub(r'([,.;:?!])(?=[^\s"\'\)\]\}])', r'\1 ', text)
    text = re.sub(r'\s+', ' ', text).strip()

    return text

def normalize_register(text: str) -> str:
    res = []
    pos = 0
    sentence_start = True

    while True:
        m = WORD_RE.search(text, pos)
        if not m:
            res.append(text[pos:])
            break

        between = text[pos:m.start()]
        res.append(between)

        if any(ch in TERM_CHARS for ch in between):
            sentence_start = True

        if between and between[-1] in TERM_CHARS:
            res.append(" ")

        word = m.group(0)

        if word.isupper():
            norm = word
        else:
            norm = word[0].upper() + word[1:].lower() if sentence_start else word.lower()

        res.append(norm)
        pos = m.end()
        sentence_start = False

    return "".join(res)

# ---------------------------
# Тестирование: верные/неверные тесты
# ---------------------------
if __name__ == "__main__":
    tests = [
        ("5-7=", "пять минус семь ровно"),
        ("5-7 = ", "пять минус семь ровно"),
        ("5-7+/*/\\/", "пять минус семь плюс слэш звёздочка слэш бэкслэш слэш"),
        ("5-7", "пять семь"),
        ("5 - 7 этаж", "пять семь этаж"),
        ("доступно 2 956, общий баланс 3420, что эквивалентно $44,613",
         "доступно две тысячи девятьсот пятьдесят шесть, общий баланс три тысячи четыреста двадцать, что эквивалентно сорок четыре запятая шестьсот тринадцать долларов"),
        ("#тег №25 @user ^ 2<3 >1",
         "решетка тег номер двадцать пять собака user стрелка вверх два меньше три больше один"),
        ("secret.py + C++", "secret точка py плюс C плюс плюс"),
        ("Цена: $1,234.50 и 444,613 USD",
         "Цена: одна тысяча двести тридцать четыре запятая пять долларов и четыреста сорок четыре запятая шестьсот тринадцать долларов"),
    ]

    passed = []
    failed = []

    for inp, expected in tests:
        out = normalize_text_for_tts(inp)
        out_norm = re.sub(r'\s+', ' ', out).strip()
        exp_norm = re.sub(r'\s+', ' ', expected).strip()
        ok = out_norm == exp_norm
        record = {
            "input": inp,
            "expected": expected,
            "output": out,
            "status": "PASS" if ok else "FAIL"
        }
        if ok:
            passed.append(record)
        else:
            failed.append(record)

    print("=== РЕЗУЛЬТАТЫ ТЕСТОВ ===")
    print(f"Всего: {len(tests)}  Успешно: {len(passed)}  Провалено: {len(failed)}\n")

    if passed:
        print("--- ПРОШЛИ ---")
        for r in passed:
            print(f"[PASS] in: {r['input']!r} -> out: {r['output']!r}")
        print()

    if failed:
        print("--- ПРОВАЛИЛИСЬ ---")
        for r in failed:
            print(f"[FAIL] in: {r['input']!r}")
            print(f"  expected: {r['expected']!r}")
            print(f"  output:   {r['output']!r}")

