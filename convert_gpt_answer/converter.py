import ast
import json
import re
from typing import List, Tuple, Union, Any

from json_repair import repair_json


def _remove_trailing_commas(s: str) -> str:
    s = re.sub(r',\s*([}\]])', r'\1', s)
    s = re.sub(r'([\{\[])\s*,', r'\1', s)
    return s


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
        if ch in ('"', "'"):
            if not in_str:
                in_str = True
                str_char = ch
            elif ch == str_char:
                in_str = False
                str_char = None
            continue
        if in_str:
            continue

        if ch in ('{', '['):
            if not stack:
                seg_start = i
            stack.append(ch)
        elif ch in ('}', ']'):
            if stack:
                top = stack[-1]
                if (top == '{' and ch == '}') or (top == '[' and ch == ']'):
                    stack.pop()
                    if not stack and seg_start is not None:
                        segments.append(s[seg_start:i + 1])
                        seg_start = None
                else:
                    stack.pop()  # Простой фикс дисбаланса
    return segments


def escape_unescaped_quotes_in_values(s: str) -> str:
    """
    Ищет паттерны "key": "value" и экранирует кавычки внутри value.
    """

    def replace_value(match):
        prefix = match.group(1)  # "key": "
        content = match.group(2)  # value content
        suffix = match.group(3)  # "
        # Экранируем двойные кавычки, если они не экранированы
        fixed_content = re.sub(r'(?<!\\)"', r'\"', content)
        return f'{prefix}{fixed_content}{suffix}'

    # Ищем значения в двойных кавычках после двоеточия
    s = re.sub(r'(":\s*")(.+?)("\s*(?:,|}|\]))', replace_value, s, flags=re.DOTALL)
    return s


def convert_answer_to_json(
        answer: str,
        keys: List[str] = None,
        start_symbol: str = "{",
        end_symbol: str = "}",
        attempt: int = 1
) -> Tuple[bool, Any]:
    if keys is None:
        keys = []

    def validate_data(data: Any) -> Any:
        """Проверяет данные согласно списку обязательных ключей."""
        if not keys:
            return data

        if isinstance(data, list):
            valid_list = [item for item in data if validate_item(item)]
            return valid_list if valid_list else None
        return data if validate_item(data) else None

    def validate_item(item: Any) -> bool:
        if not keys:
            return True
        if isinstance(item, dict):
            # Проверка на наличие всех ключей ИЛИ стандартного формата LLM
            if all(k in item for k in keys):
                return True
            if "type" in item and "value" in item:
                return True
        return False

    try:
        # 1. Предварительная очистка
        cleaned = answer.replace('```json', '').replace('```', '').strip()

        # 2. Попытка через json_repair (самый мощный инструмент)
        repaired = repair_json(cleaned)
        try:
            data = json.loads(repaired)
            valid = validate_data(data)
            if valid is not None: return True, valid
        except:
            pass

        # 3. Попытка через сегментацию (если выдано несколько объектов без [])
        segments = _extract_balanced_segments(cleaned)
        if len(segments) > 1:
            combined = "[" + ",".join(segments) + "]"
            try:
                data = json.loads(repair_json(combined))
                valid = validate_data(data)
                if valid is not None: return True, valid
            except:
                pass

        # 4. Агрессивная чистка кавычек и попытка через AST (для Python-like JSON)
        cleaned_v2 = escape_unescaped_quotes_in_values(cleaned)
        cleaned_v2 = cleaned_v2.replace('true', 'True').replace('false', 'False').replace('null', 'None')
        try:
            # Ищем что-то похожее на структуру внутри текста
            match = re.search(r'(\[.*\]|\{.*\})', cleaned_v2, re.DOTALL)
            if match:
                data = ast.literal_eval(match.group(1))
                valid = validate_data(data)
                if valid is not None: return True, valid
        except:
            pass

        # 5. Если ничего не помогло, пробуем json_repair на очищенном фрагменте
        match = re.search(r'(\[.*\]|\{.*\})', cleaned, re.DOTALL)
        if match:
            try:
                data = json.loads(repair_json(match.group(1)))
                valid = validate_data(data)
                if valid is not None: return True, valid
            except:
                pass

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
    very_complex_list13 = '["README.md", "datasets/blank/big_dataset_with_code_neuro_vi.json", "images/RAG-summer.png", "requirements.txt"]'

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
    print("Very complex list13:", convert_answer_to_json(very_complex_list13, [], "[", "]"))
