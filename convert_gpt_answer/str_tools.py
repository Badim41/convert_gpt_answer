import hashlib
import os
import random
import re
import string


def random_string(length=8, seed=None, input_str=None):
    if not input_str is None:
        hash_object = hashlib.sha256(input_str.encode())
        hash_hex = hash_object.hexdigest()
        random.seed(hash_hex)

    elif not seed is None:
        random.seed(seed)

    return ''.join(random.choice(string.ascii_lowercase + string.digits) for _ in range(length))


def remove_emojis(text):
    emoji_pattern = re.compile("["
                               u"\U0001F600-\U0001F64F"  # смайлики эмодзи
                               u"\U0001F300-\U0001F5FF"  # символы и пиктограммы
                               u"\U0001F680-\U0001F6FF"  # символы транспорта и карты
                               u"\U0001F900-\U0001F9FF"  # дополнительные эмодзи
                               u"\U00002702-\U000027B0"  # символы с подсказками
                               u"\U000024C2-\U0001F251"  # дополнительные символы
                               u"\U0001F1E0-\U0001F1FF"  # флаги
                               "]+", flags=re.UNICODE)
    return emoji_pattern.sub(r'', text)


def try_remove(image_path: [str, list, None]):
    if not image_path:
        return
    if isinstance(image_path, str):
        image_paths = [image_path]
    else:
        image_paths = image_path

    for image_path_rm in image_paths:
        if os.path.exists(image_path_rm):
            try:
                os.remove(image_path_rm)
            except Exception as e:
                print(f"Warn: path {image_path_rm} cant be removed: {e}")
        else:
            print(f"Warn: path {image_path_rm} not exists")


def normalize_name(name: str) -> str:
    # Заменяем пробелы на "_"
    name = name.replace(" ", "_")
    # Оставляем только русские, английские буквы и цифры, а также "_"
    name = re.sub(r"[^a-zA-Zа-яА-Я0-9_]", "", name)
    return name.replace("__", "_").lower()


def remove_markdown_stars_and_content(text: str) -> str:
    # Полностью удаляем *...* и **...**
    return re.sub(r'\*{1,2}.*?\*{1,2}', '', text).strip()


if __name__ == "__main__":
    # Пример random_string
    print("Random string без seed:", random_string(10))
    print("Random string с seed=42:", random_string(10, seed=42))
    print("Random string из input_str='hello world':", random_string(10, input_str="hello world"))

    print("\n\n\n")

    # Пример remove_emojis
    text_with_emojis = "Привет 🌍! Как дела 😄?"
    cleaned_text = remove_emojis(text_with_emojis)
    # print("Оригинальный текст:", text_with_emojis)
    print("Текст без эмодзи:", cleaned_text)

    print("\n\n\n")

    # Пример try_remove
    # Создадим временный файл для демонстрации
    temp_file = "temp_test_file.txt"
    with open(temp_file, "w") as f:
        f.write("Hello world!")
    print(f"Создан файл: {temp_file}")
    try_remove(temp_file)  # Удаляем файл
    print(f"Файл существует после удаления? {os.path.exists(temp_file)}")

    print("\n\n\n")

    # Пример normalize_name
    raw_name = "Пример файла #1 *test*"
    normalized = normalize_name(raw_name)
    print("Исходное имя:", raw_name)
    print("Нормализованное имя:", normalized)

    print("\n\n\n")

    # Пример remove_markdown_stars_and_content

    # -- Иногда нужно для озвучки текста для ИИ-агентов, чтобы они не озвучивали *действие* --
    markdown_text = "Это **важный** текст и *необязательный* комментарий"
    no_markdown = remove_markdown_stars_and_content(markdown_text)
    print("Оригинальный текст:", markdown_text)
    print("Текст без markdown:", no_markdown)
