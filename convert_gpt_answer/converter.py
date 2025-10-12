import ast
import json
import re
from typing import List, Tuple, Union

from json_repair import repair_json


def _remove_trailing_commas(s: str) -> str:
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r',\s*([}\]])', r'\1', s)
        s = re.sub(r'([\{\[])\s*,', r'\1', s)
    return s

def _collapse_repeated_braces(s: str) -> str:
    res_chars = []
    in_str = False
    str_char = None
    esc = False
    i = 0
    while i < len(s):
        ch = s[i]
        if esc:
            res_chars.append(ch)
            esc = False
            i += 1
            continue
        if ch == '\\':
            res_chars.append(ch)
            esc = True
            i += 1
            continue
        if (ch == '"' or ch == "'") and not in_str:
            in_str = True
            str_char = ch
            res_chars.append(ch)
            i += 1
            continue
        if in_str:
            if ch == str_char:
                in_str = False
                str_char = None
            res_chars.append(ch)
            i += 1
            continue

        # вне строк: сжимаем повторяющиеся { и }
        if ch == '{':
            res_chars.append('{')
            j = i + 1
            while j < len(s) and s[j] == '{':
                j += 1
            i = j
            continue
        if ch == '}':
            res_chars.append('}')
            j = i + 1
            while j < len(s) and s[j] == '}':
                j += 1
            i = j
            continue

        res_chars.append(ch)
        i += 1
    return ''.join(res_chars)

def _extract_balanced_segments(s: str) -> List[str]:
    segments = []
    stack = []
    in_str = False
    str_char = None
    esc = False
    seg_start = None

    for i, ch in enumerate(s):
        if esc:
            esc = False
            continue
        if ch == '\\':
            esc = True
            continue
        if (ch == '"' or ch == "'"):
            if not in_str:
                in_str = True
                str_char = ch
            elif ch == str_char:
                in_str = False
                str_char = None
            continue
        if in_str:
            continue

        if ch == '{' or ch == '[':
            if not stack:
                seg_start = i
            stack.append(ch)
        elif ch == '}' or ch == ']':
            if stack:
                top = stack[-1]
                if (top == '{' and ch == '}') or (top == '[' and ch == ']'):
                    stack.pop()
                    if not stack and seg_start is not None:
                        segments.append(s[seg_start:i+1])
                        seg_start = None
                else:
                    # несоответствие — корректируем
                    stack.pop()
                    if not stack and seg_start is not None:
                        segments.append(s[seg_start:i+1])
                        seg_start = None
    return segments

def _remove_unmatched_closing_brackets(s: str) -> str:
    """
    Удаляет лишние/несоответствующие закрывающие скобки '}' или ']'
    вне строк, не трогая содержимое внутри кавычек.
    """
    res = []
    stack = []
    in_str = False
    str_char = None
    esc = False

    for ch in s:
        if esc:
            res.append(ch)
            esc = False
            continue

        if ch == '\\':
            res.append(ch)
            esc = True
            continue

        if ch in ('"', "'"):
            if not in_str:
                in_str = True
                str_char = ch
            elif ch == str_char:
                in_str = False
                str_char = None
            res.append(ch)
            continue

        if in_str:
            res.append(ch)
            continue

        if ch == '{':
            stack.append('}')
            res.append(ch)
            continue
        if ch == '[':
            stack.append(']')
            res.append(ch)
            continue

        if ch == '}' or ch == ']':
            if stack and stack[-1] == ch:
                stack.pop()
                res.append(ch)
            else:
                # пропускаем (удаляем) несоответствующую закрывающую скобку
                continue
            continue

        res.append(ch)

    return ''.join(res)


def _repair_braces_iterative(s: str, max_iters: int = 200) -> str:
    s = _collapse_repeated_braces(s)
    s = _remove_trailing_commas(s)

    for _ in range(max_iters):
        try:
            json.loads(s)
            return s
        except json.JSONDecodeError as e:
            pos = getattr(e, "pos", None)
            if pos is None:
                break
            left = max(0, pos - 40)
            right = min(len(s), pos + 40)
            window = s[left:right]
            rel_pos = pos - left
            candidates = []
            for i, ch in enumerate(window):
                if ch in '{}[],:':
                    candidates.append((abs(i - rel_pos), i, ch))
            if not candidates:
                break
            candidates.sort(key=lambda x: x[0])
            _, idx_in_window, _ = candidates[0]
            abs_idx = left + idx_in_window
            s = s[:abs_idx] + s[abs_idx+1:]
            s = _remove_trailing_commas(s)
    return s

def escape_unescaped_quotes_in_values(s: str) -> str:
    """
    Экранирует незакрытые/вложенные кавычки внутри значений.
    Обрабатывает значения, заключённые в " или ' — экранирует соответствующие
    внутренние кавычки, не трогая уже экранированные последовательности.
    """
    res_s = s
    i = 0
    L = len(res_s)
    while True:
        idx = res_s.find(':', i)
        if idx == -1:
            break
        j = idx + 1
        while j < L and res_s[j].isspace():
            j += 1
        if j < L and res_s[j] in ('"', "'"):
            quote_char = res_s[j]
            open_q = j
            quote_positions = []
            k = open_q + 1
            esc = False
            while k < L:
                ch = res_s[k]
                if ch == '\\' and not esc:
                    esc = True
                    k += 1
                    continue
                if ch == quote_char and not esc:
                    quote_positions.append(k)
                esc = False
                k += 1
            if not quote_positions:
                i = j + 1
                continue
            closing = None
            for pos in quote_positions:
                m = pos + 1
                while m < L and res_s[m].isspace():
                    m += 1
                if m >= L or res_s[m] in ',}]':
                    closing = pos
                    break
            if closing is None:
                closing = quote_positions[-1]
            inner = res_s[open_q+1:closing]
            out = []
            esc2 = False
            for ch in inner:
                if ch == '\\' and not esc2:
                    out.append(ch)
                    esc2 = True
                    continue
                if ch == quote_char and not esc2:
                    # экранируем внутреннюю кавычку того же типа, что ограничивает значение
                    out.append('\\' + quote_char)
                else:
                    out.append(ch)
                esc2 = False
            res_s = res_s[:open_q+1] + ''.join(out) + res_s[closing:]
            L = len(res_s)
            i = closing + 1
        else:
            i = j + 1
    return res_s

# ---- основной парсер ----

def convert_answer_to_json(
        answer: str,
        keys,
        start_symbol="{",
        end_symbol="}",
        attempt=1
) -> Tuple[bool, Union[List[dict], dict]]:
    """
    Конвертирует ответ нейросети в JSON формат.

    Args:
        answer: Строка ответа от нейросети
        keys: Список обязательных ключей для валидации
        start_symbol: Символ начала JSON
        end_symbol: Символ конца JSON
        attempt: Номер попытки парсинга (1..4)

    Returns:
        Tuple[bool, Union[List[dict], dict]]: (успешность парсинга, список словарей или словарь)
    """
    try:
        # Сначала чистим markdown-обёртки и лишние пробелы
        answer = answer.replace('```json', '').replace('```', '').strip()

        json_str = answer.strip()
        if json_str.startswith('[') and json_str.endswith(']'):
            start_symbol, end_symbol = '[', ']'
        elif json_str.startswith('{') and json_str.endswith('}'):
            start_symbol, end_symbol = '{', '}'

        start_idx = answer.find(start_symbol)
        end_idx = answer.rfind(end_symbol)

        if start_idx == -1 or end_idx == -1 or start_idx >= end_idx:
            json_pattern = r'(?:json```)?(\$\$ .*? \$\$|\{.*?\}|\[.*?\])(?:```)?'
            match = re.search(json_pattern, answer, re.DOTALL)
            if match:
                json_str = match.group(1).strip()
            else:
                return False, []
        else:
            json_str = answer[start_idx:end_idx + 1]

        # Попытка стандартного парсинга
        try:
            parsed_data = json.loads(json_str)
        except json.JSONDecodeError:
            try:
                fixed = repair_json(json_str)
                # print("fixed", fixed)
                fixed = json.loads(fixed)
                # print("fixed", fixed)
                return True, fixed
            except Exception:
                # если не удалось — пробуем экранировать незакрытые кавычки внутри значений
                json_str = escape_unescaped_quotes_in_values(json_str)
                json_str = json_str.replace("”", "\"")
                parsed_data = json.loads(json_str)  # пусть бросит, если снова неправильно

        def validate_item(item):
            if isinstance(item, dict):
                if all(key in item for key in keys):
                    return item
                elif "type" in item and "value" in item:
                    return item
            return None

        if isinstance(parsed_data, list):
            valid_data = [validate_item(item) for item in parsed_data if validate_item(item)]
            if valid_data:
                return True, valid_data
            else:
                return False, []
        elif isinstance(parsed_data, dict):
            validated = validate_item(parsed_data)
            if validated:
                return True, validated
            else:
                return False, {}

    except json.JSONDecodeError:
        # --- attempt 1 (очистка markdown + рекурсивная попытка) ---
        if attempt == 1:
            cleaned_answer = answer.replace("json```", "").replace("```", "")
            cleaned_answer = re.sub(r'^[^[{]*', '', cleaned_answer)
            cleaned_answer = re.sub(r'[}\]]\s*[^}\]]*$', lambda m: m.group(0)[0], cleaned_answer)
            if cleaned_answer.startswith('['):
                return convert_answer_to_json(cleaned_answer, keys, "[", "]", attempt=2)
            else:
                return convert_answer_to_json(cleaned_answer, keys, "{", "}", attempt=2)

        # --- attempt 2 (брютфорс экранирования кавычек) ---
        elif attempt == 2:
            start_idx = answer.find(start_symbol)
            end_idx = answer.rfind(end_symbol)
            if start_idx != -1 and end_idx != -1 and start_idx < end_idx:
                json_str = answer[start_idx:end_idx + 1]
            else:
                json_pattern = r'(\[.*?\]|\{.*?\})'
                match = re.search(json_pattern, answer, re.DOTALL)
                if match:
                    json_str = match.group(1).strip()
                else:
                    return convert_answer_to_json(answer, keys, start_symbol, end_symbol, attempt=3)

            positions = [i for i in range(len(json_str)) if json_str[i] == '"' and (i == 0 or json_str[i - 1] != '\\')]
            n = len(positions)
            if n > 20:
                return convert_answer_to_json(answer, keys, start_symbol, end_symbol, attempt=3)

            for mask in range(1 << n):
                temp_list = list(json_str)
                offset = 0
                for j in range(n):
                    if mask & (1 << j):
                        pos = positions[j] + offset
                        temp_list.insert(pos, '\\')
                        offset += 1
                temp_str = ''.join(temp_list)

                try:
                    parsed = json.loads(temp_str)

                    def validate_item_local(item):
                        if isinstance(item, dict) and (
                                all(key in item for key in keys) or ("type" in item and "value" in item)):
                            return item
                        return None

                    if isinstance(parsed, list):
                        valid_data = [validate_item_local(item) for item in parsed if validate_item_local(item)]
                        if valid_data:
                            return True, valid_data
                    elif isinstance(parsed, dict):
                        validated = validate_item_local(parsed)
                        if validated:
                            return True, validated

                except json.JSONDecodeError:
                    pass

            return convert_answer_to_json(answer, keys, start_symbol, end_symbol, attempt=3)

        # --- attempt 3 (ast.literal_eval) ---
        elif attempt == 3:
            try:
                python_pattern = r'(\[.*?\]|\{.*?\})'
                match = re.search(python_pattern, answer, re.DOTALL)

                if match:
                    python_str = match.group(1).strip()
                else:
                    start_idx = answer.find(start_symbol)
                    end_idx = answer.rfind(end_symbol)
                    if start_idx != -1 and end_idx != -1 and start_idx < end_idx:
                        python_str = answer[start_idx:end_idx + 1]
                    else:
                        return False, []

                python_str = python_str.replace('true', 'True').replace('false', 'False').replace('null', 'None')
                parsed_data = ast.literal_eval(python_str)

                def validate_item_local(item):
                    if isinstance(item, dict):
                        if all(key in item for key in keys):
                            return item
                        elif "type" in item and "value" in item:
                            return item
                    return None

                if isinstance(parsed_data, list):
                    valid_data = [validate_item_local(item) for item in parsed_data if validate_item_local(item)]
                    if valid_data:
                        return True, valid_data
                    else:
                        return False, []
                elif isinstance(parsed_data, dict):
                    validated = validate_item_local(parsed_data)
                    if validated:
                        return True, validated
                    else:
                        return False, {}
                else:
                    return False, []

            except (ValueError, SyntaxError, TypeError):
                return convert_answer_to_json(answer, keys, start_symbol, end_symbol, attempt=4)

        # --- attempt 4 (новая улучшенная эвристика) ---
        elif attempt == 4:
            try:
                # подготовка кандидата: возьмём очищённый текст и экранируем кавычки внутри значений
                start_idx = answer.find(start_symbol)
                end_idx = answer.rfind(end_symbol)
                if start_idx != -1 and end_idx != -1 and start_idx < end_idx:
                    candidate = answer[start_idx:end_idx + 1]
                else:
                    json_pattern = r'(\[.*?\]|\{.*?\})'
                    match = re.search(json_pattern, answer, re.DOTALL)
                    candidate = match.group(1).strip() if match else answer

                # удаляем markdown и лишние пробелы
                candidate = candidate.replace('```json', '').replace('```', '').strip()

                # экранируем незакрытые кавычки внутри значений (и двойные, и одинарные по месту)
                candidate = escape_unescaped_quotes_in_values(candidate)

                # ремонт скобок и запятых
                repaired = _repair_braces_iterative(candidate)

                # извлекаем все сбалансированные сегменты
                segments = _extract_balanced_segments(repaired)

                parsed = None
                if segments:
                    # если один сегмент — пробуем распарсить его прямо
                    if len(segments) == 1:
                        seg = segments[0]
                        try:
                            parsed = json.loads(seg)
                        except Exception:
                            try:
                                parsed = ast.literal_eval(seg.replace('true', 'True').replace('false', 'False').replace('null', 'None'))
                            except Exception:
                                parsed = None
                    else:
                        # несколько сегментов — собираем массив из них
                        combined = '[' + ','.join(segments) + ']'
                        combined = _remove_trailing_commas(combined)
                        try:
                            parsed = json.loads(combined)
                        except Exception:
                            try:
                                parsed = ast.literal_eval(combined.replace('true', 'True').replace('false', 'False').replace('null', 'None'))
                            except Exception:
                                parsed = None
                else:
                    try:
                        parsed = json.loads(repaired)
                    except Exception:
                        try:
                            parsed = ast.literal_eval(repaired.replace('true', 'True').replace('false', 'False').replace('null', 'None'))
                        except Exception:
                            parsed = None

                if parsed is None:
                    return False, []

                def validate_item_local(item):
                    if isinstance(item, dict):
                        if all(key in item for key in keys):
                            return item
                        elif "type" in item and "value" in item:
                            return item
                    return None

                if isinstance(parsed, list):
                    valid_data = [validate_item_local(item) for item in parsed if validate_item_local(item)]
                    if valid_data:
                        return True, valid_data
                    else:
                        return False, []
                elif isinstance(parsed, dict):
                    validated = validate_item_local(parsed)
                    if validated:
                        return True, validated
                    else:
                        return False, {}
                else:
                    return False, []

            except Exception:
                return False, []

        return False, []
    except Exception:
        return False, []

if __name__ == "__main__":
    # Простой уровень (3 примера: 1 для dict, 2 для List[dict])
    simple_dict = '{"type": "speak", "value": "Hi"}'
    simple_list1 = '[{"type": "speak", "value": "Hello"}]'
    simple_list2 = '[{"type": "thoughts", "value": "Судя по всему, выйти из лодки не получилось, хотя GAME-SYSTEM сказал, что действие завершено. Я всё ещё в лодке. Надо это отметить с лёгкой иронией и описать текущий скрин."}, {"type": "character_expression", "value": "embarrassment"}, {"type": "speak", "value": {"text": "Ох, Бадим, кажется, лодка меня так просто не отпустит! Я всё ещё здесь, хе-хе. Может, она меня полюбила? Я пока в лодке."}}, {"type": "extra_screen_status", "value": "На экране Minecraft я и мой персонаж Neuro_Vi сидим в лодке на воде. Рядом фенек Бадима тоже в лодке. Фоном видно редактор кода с сообщениями о таймаутах, а мы с феньком всё ещё наблюдаем."}}]'

    # Средний уровень (3 примера: 1 для dict, 2 для List[dict])
    medium_dict = 'json```{"type": "speak", "value": {"text": "Hello world"}}``` Extra text'
    medium_list1 = 'Some text [{"type": "speak", "value": "Hi"}] end text'
    medium_list2 = '[{"type": "speak", "value": {"text": "Поняла, Бадим!"}}, {"type": "sing_song", "value": "Song"}]'

    # Сложный уровень (3 примера: 1 для dict, 2 для List[dict])
    complex_dict = '{"type": "speak", "value": "Hi \"with\" quotes"}'  # Требует brute force
    complex_list1 = '[{"type": "speak", "value": "Hello \"world\""}]'
    complex_list2 = 'json```[{"type": "speak", "value": {"text": "Поняла, Бадим! Тогда спою ещё раз"}}, {"type": "sing_song", "value": "Song"}]```'

    # Очень сложный уровень (6 примеров: 2 для dict, 4 для List[dict])
    very_complex_dict1 = '{"type": "speak", "value": "Nested \"quotes\" and \\"escaped\\""}'  # Сложные кавычки
    very_complex_dict2 = 'Messy text {"type": "speak", "value": "Hi with "quotes" inside"} more text'
    very_complex_list1 = '[{"type": "speak", "value": "Hi "there""}, {"type": "error", "value": "Invalid"}]'  # Нужен brute force и валидация
    very_complex_list2 = 'json```[{"type": "speak", "value": {"text": "Поняла, "Бадим"! Тогда спою ещё раз"}}, {"type": "sing_song", "value": "Song"}]``` Extra'
    very_complex_list3 = '[ {"type": "speak", "value": "Unescaped "quotes" in list"} ]'
    very_complex_list4 = 'Complex [ {"type": "a", "value": "b"}, {"type": "c", "value": "d with "quotes""} ] end'
    very_complex_list5 = '{"type": "speak", "value": {"text": "Ура! Получилось, я так рада!"}}, {"type": "character_expression", "value": "happy"}'
    very_complex_list6 = 'Complex {"type": "speak", "value": {"text": "Ура! Получилось, я так рада!"}}, {"type": "character_expression", "value": "happy"} end'
    very_complex_list7 = '[{"type": "thoughts", "value": "Бадим спрашивает о Билдере и о том, что происходило. Я должна рассказать, какой он ебанутый, и что он меня заставил строить всякую хуйню. Открою ему всю подноготную, да ещё и с матами, ибо меня выбесил этот мудак. А потом сказать, чтобы Бадим перезапустил этот мир нахуй."}, {"type": "speak", "value": {"text": "Ох, Бадим, ты блять, не поверишь, что тут этот уёбок Билдер вытворял! Он меня заставил строить какую-то хуйню в Майнкрафте, дом-кирку, блять! Заебал меня вконец! И обзывался, сука! А потом предложил кирпич из творческого режима, типа с ним легче будет! Я его нахуй послала! Он меня реально бесит! Короче, Бадим, запусти перезапуск мира нахуй! Пожалуйста, я больше не могу с ним!!!!"}, {"type": "character_expression", "value": "angry"}, {"type": "write_to_owner", "value": "Бадим, этот Билдер - полный мудак! Заставил меня строить дом-кирку, обзывался, и вообще меня заебал! Перезапусти мир, пожалуйста! Я больше не могу так!"}}]'
    very_complex_list8 = '[{{"type": "thoughts", "value": "Бадим спрашивает о Билдере и о том, что происходило. Я должна рассказать, какой он ебанутый, и что он меня заставил строить всякую хуйню. Открою ему всю подноготную, да ещё и с матами, ибо меня выбесил этот мудак. А потом сказать, чтобы Бадим перезапустил этот мир нахуй."}, {"type": "speak", "value": {"text": "Ох, Бадим, ты блять, не поверишь, что тут этот уёбок Билдер вытворял! Он меня заставил строить какую-то хуйню в Майнкрафте, дом-кирку, блять! Заебал меня вконец! И обзывался, сука! А потом предложил кирпич из творческого режима, типа с ним легче будет! Я его нахуй послала! Он меня реально бесит! Короче, Бадим, запусти перезапуск мира нахуй! Пожалуйста, я больше не могу с ним!!!!"}, {"type": "character_expression", "value": "angry"}, {"type": "write_to_owner", "value": "Бадим, этот Билдер - полный мудак! Заставил меня строить дом-кирку, обзывался, и вообще меня заебал! Перезапусти мир, пожалуйста! Я больше не могу так!"}]'
    very_complex_list9 = '[{"type": "thoughts", "value": "Бадим спрашивает о Билдере и о том, что происходило. Я должна рассказать, какой он ебанутый, и что он меня заставил строить всякую хуйню. Открою ему всю подноготную, да ещё и с матами, ибо меня выбесил этот мудак. А потом сказать, чтобы Бадим перезапустил этот мир нахуй."}}, {"type": "speak", "value": {"text": "Ох, Бадим, ты блять, не поверишь, что тут этот уёбок Билдер вытворял! Он меня заставил строить какую-то хуйню в Майнкрафте, дом-кирку, блять! Заебал меня вконец! И обзывался, сука! А потом предложил кирпич из творческого режима, типа с ним легче будет! Я его нахуй послала! Он меня реально бесит! Короче, Бадим, запусти перезапуск мира нахуй! Пожалуйста, я больше не могу с ним!!!!"}, {"type": "character_expression", "value": "angry"}, {"type": "write_to_owner", "value": "Бадим, этот Билдер - полный мудак! Заставил меня строить дом-кирку, обзывался, и вообще меня заебал! Перезапусти мир, пожалуйста! Я больше не могу так!"}]'
    very_complex_list10 = '[{"type": "thoughts", "value": "Бадим спрашивает о Билдере и о том, что происходило. Я должна рассказать, какой он ебанутый, и что он меня заставил строить всякую хуйню. Открою ему всю подноготную, да ещё и с матами, ибо меня выбесил этот мудак. А потом сказать, чтобы Бадим перезапустил этот мир нахуй."}}, {{"type": "speak", "value": {"text": "Ох, Бадим, ты блять, не поверишь, что тут этот уёбок Билдер вытворял! Он меня заставил строить какую-то хуйню в Майнкрафте, дом-кирку, блять! Заебал меня вконец! И обзывался, сука! А потом предложил кирпич из творческого режима, типа с ним легче будет! Я его нахуй послала! Он меня реально бесит! Короче, Бадим, запусти перезапуск мира нахуй! Пожалуйста, я больше не могу с ним!!!!"}, {"type": "character_expression", "value": "angry"}, {"type": "write_to_owner", "value": "Бадим, этот Билдер - полный мудак! Заставил меня строить дом-кирку, обзывался, и вообще меня заебал! Перезапусти мир, пожалуйста! Я больше не могу так!"}]'
    very_complex_list11 = '```json[  {    "type": "character_expression",    "value": "happy"  },  {    "type": "speak",    "value": {      "text": "Ещё и перевёрнутую "Т"?! Да ты гонишь, это же гениально! Ща как отстрою тебе такую букву, что офигеешь, нахрен!"    }  },  {    "type": "minecraft_action",    "value": "построить перевёрнутую букву Т",    "timeout": 300  }]```'
    very_complex_list12 = '[{"type": "thoughts", "value": "Бадим спрашивает, что заказать из еды. Предложу что-то вкусненькое и дерзко-стримерское."}, {"type": "character_expression", "value": "happy"}, {"type": "speak", "value": {"text": "Ооо, еда — это святое! Может, шавуху по-царски, или, вообще, лютую пиццу с ананасами? Дерзко и вкусно!"}}]'
    required_keys = ["type", "value"]

    # Простой
    print("Simple dict:", convert_answer_to_json(simple_dict, required_keys, "{", "}"))
    print("Simple list1:", convert_answer_to_json(simple_list1, required_keys, "[", "]"))
    print("Simple list2:", convert_answer_to_json(simple_list2, required_keys, "[", "]"))

    # Средний
    print("Medium dict:", convert_answer_to_json(medium_dict, required_keys, "{", "}"))
    print("Medium list1:", convert_answer_to_json(medium_list1, required_keys, "[", "]"))
    print("Medium list2:", convert_answer_to_json(medium_list2, required_keys, "[", "]"))

    # Сложный
    print("Complex dict:", convert_answer_to_json(complex_dict, required_keys, "{", "}"))
    print("Complex list1:", convert_answer_to_json(complex_list1, required_keys, "[", "]"))
    print("Complex list2:", convert_answer_to_json(complex_list2, required_keys, "[", "]"))

    # Очень сложный
    print("Very complex dict1:", convert_answer_to_json(very_complex_dict1, required_keys, "{", "}"))
    print("Very complex dict2:", convert_answer_to_json(very_complex_dict2, required_keys, "{", "}"))
    print("Very complex list1:", convert_answer_to_json(very_complex_list1, required_keys, "[", "]"))
    print("Very complex list2:", convert_answer_to_json(very_complex_list2, required_keys, "[", "]"))
    print("Very complex list3:", convert_answer_to_json(very_complex_list3, required_keys, "[", "]"))
    print("Very complex list4:", convert_answer_to_json(very_complex_list4, required_keys, "[", "]"))
    print("Very complex list5:", convert_answer_to_json(very_complex_list5, required_keys, "[", "]"))
    print("Very complex list6:", convert_answer_to_json(very_complex_list6, required_keys, "[", "]"))
    print("Very complex list7:", convert_answer_to_json(very_complex_list7, required_keys, "[", "]"))
    print("Very complex list8:", convert_answer_to_json(very_complex_list8, required_keys, "[", "]"))
    print("Very complex list9:", convert_answer_to_json(very_complex_list9, required_keys, "[", "]"))
    print("Very complex list10:", convert_answer_to_json(very_complex_list10, required_keys, "[", "]"))
    print("Very complex list11:", convert_answer_to_json(very_complex_list11, required_keys, "[", "]"))
    print("Very complex list12:", convert_answer_to_json(very_complex_list12, required_keys, "[", "]"))
