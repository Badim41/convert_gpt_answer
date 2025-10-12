# convert_gpt_answer

Базовый набор инструментов, особенно для работы с ChatGPT

## Возможности

- Конвертирование ответа нейросети в JSON
- Утилиты для строк
- Утилиты для изображений
- Таймер
- Нормализовать текст для TTS

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

Остальные примеры в соответствующих файлах:
- normalize_text_for_tts.py
- str_tools.py
- ...