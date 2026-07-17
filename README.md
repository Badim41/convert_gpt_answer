# convert_gpt_answer

Базовый набор инструментов, особенно для работы с ChatGPT

## Возможности

- Конвертирование ответа нейросети в JSON
- Утилиты для строк
- Утилиты для изображений
- Таймер
- Нормализовать текст для TTS
- **replacer.py** — утилита для автоматического применения изменений кода от нейросети в файлы проекта

## Установка
```bash
pip install git+https://github.com/Badim41/convert_gpt_answer.git
```
## Использование

```python
from convert_gpt_answer import convert_answer_to_json

simple_dicts = [
    '{"type": "speak", "value": "Hi"}',
    "{'type': 'speak', 'value': 'Hi'}",
    "json```{'type': 'speak', 'value': 'Hi'}```",
    '{"type": "speak", "value": "Hi, \\"Name\\""}'
]

for i, simple_dict in enumerate(simple_dicts, start=1):
    converted, json_data = convert_answer_to_json(
        simple_dict,
        keys=["type","value"],
        start_symbol="{",
        end_symbol="}"
    )
    if converted:
        print(f"Simple dict {i}: {json_data}")
    else:
        print(f"Error in convert: {json_data}")
```

## Пример вывода
```
Simple dict 1: {'type': 'speak', 'value': 'Hi'}
Simple dict 2: {'type': 'speak', 'value': 'Hi'}
Simple dict 3: {'type': 'speak', 'value': 'Hi'}
Simple dict 4: {'type': 'speak', 'value': 'Hi, "Name"'}
```

## Автоматическое применение правок кода (replacer.py)

Утилита `replacer.py` считывает ответ нейросети из консоли и автоматически вносит изменения в файлы проекта. Она умеет находить нужные фрагменты кода с помощью нечеткого поиска, а также автоматически выполнять PowerShell скрипты для создания новых файлов.

Лучше использовать в связке с https://github.com/Badim41/gpt_project_prompter

### Промпт для ChatGPT / LLM
Чтобы нейросеть выдавала ответ в совместимом формате, используйте в системном промпте (или в задаче) следующую инструкцию:

```text
# ФОРМАТ ВЫВОДА ПРАВОК
Все изменения существующего кода пиши строго в формате Search-and-Replace Block. 
Один блок должен содержать одну замену. Не пиши имя файла внутри блока поиска! replacer найдет место в проекте автоматически по уникальному коду.

Пример формата:
Search-and-Replace Block:
<<<<<<<
def old_function():
    print("Старый текст")
=======
def new_function():
    print("Первая строка замены")
    print("Вторая строка замены")
>>>>>>>

Важно: блок поиска (между <<<<<< и =======) должен с точностью до пробела и переноса строки соответствовать оригинальному коду в проекте.

## Новые файлы
Если требуется создать НОВЫЙ файл или ПАПКУ, обязательно пиши в самом начале ответа PowerShell-команду для генерации файла сразу с его полным содержимым. Используй Here-String для многострочного текста.

Пример создания файла:
❗️НОВЫЕ ФАЙЛ: Powershell create
$content = @'
import os
import sys

def main():
    print("Содержимое нового файла")
'@
$content | Out-File -FilePath "path/to/new_file.py" -Encoding utf8 -Force
```

### Как использовать replacer

1. Перейдите в корень вашего проекта, где нужно применить изменения.
2. Запустите модуль:
   ```bash
   python -m convert_gpt_answer.replacer
   ```
3. Скопируйте весь текст ответа нейросети (включая команды powershell и блоки `<<<< ==== >>>>`) и вставьте в консоль.
4. На новой пустой строке введите `.,,,` и нажмите **Enter**.
5. Утилита распознает скрипты, предложит их выполнить (y/n), а затем найдет нужные файлы и применит замену кода.

Остальные примеры в соответствующих файлах:
- normalize_text_for_tts.py
- str_tools.py
- ...