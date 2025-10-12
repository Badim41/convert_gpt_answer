import base64

import requests
from PIL import Image


def resize_image(image_path: str, x: int, y: int) -> bool:
    try:
        with Image.open(image_path) as img:
            resized_img = img.resize((x, y))
            resized_img.save(image_path)
        return True
    except Exception as e:
        print(f"Ошибка при изменении размера: {e}")
        return False


def image_to_base64(image_path):
    if image_path.startswith("http"):
        response = requests.get(image_path)
        response.raise_for_status()  # Проверка на ошибки HTTP
        return base64.b64encode(response.content).decode('utf-8')
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')
