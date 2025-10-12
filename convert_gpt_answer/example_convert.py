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