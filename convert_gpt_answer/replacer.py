import re
import sys
import os


# Константы для оформления вывода в консоль
class Colors:
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RESET = '\033[0m'


# Папки, которые следует игнорировать при поиске
IGNORE_DIRS = {
    '.git', 'node_modules', 'venv', '.venv', 'env',
    '__pycache__', '.idea', '.vscode', 'build', 'dist',
    'coverage', '.next', '.nuxt', 'out'
}


def parse_input():
    print(f"{Colors.YELLOW}Введите текст с блоками правок. {Colors.RESET}")
    print(
        f"{Colors.YELLOW}Для подтверждения отправки введите {Colors.GREEN}.,,,{Colors.YELLOW} на новой пустой строке и нажмите Enter:{Colors.RESET}")

    lines = []
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            # Условие завершения ввода
            if line.strip() == '.,,,':
                break
            lines.append(line)
        except EOFError:
            break

    blocks = []
    state = 0  # 0: поиск <<<<, 1: сбор оригинала (поиск ====), 2: сбор нового текста (поиск >>>>)
    current_search = []
    current_replace = []

    for line in lines:
        stripped = line.strip()

        # Поддержка маркеров любой длины от 4 символов (<<<<, <<<<<<< и т.д.)
        is_start = len(stripped) >= 4 and stripped == '<' * len(stripped)
        is_mid = len(stripped) >= 4 and stripped == '=' * len(stripped)
        is_end = len(stripped) >= 4 and stripped == '>' * len(stripped)

        if state == 0:
            if is_start:
                state = 1
                current_search = []
                current_replace = []
        elif state == 1:
            if is_mid:
                state = 2
            elif is_start:
                current_search = []  # Сброс, если маркер повторился
            elif is_end:
                state = 0  # Некорректная структура, сбрасываем
            else:
                current_search.append(line.rstrip('\r\n'))
        elif state == 2:
            if is_end:
                # Очистка случайных пустых строк при копировании
                def trim_lines(lines):
                    s, e = 0, len(lines)
                    while s < e and not lines[s].strip(): s += 1
                    while e > s and not lines[e - 1].strip(): e -= 1
                    return lines[s:e]

                blocks.append({
                    'search': trim_lines(current_search),
                    'replace': trim_lines(current_replace)
                })
                state = 0
            elif is_start:
                state = 1  # Некорректная структура
                current_search = []
            else:
                current_replace.append(line.rstrip('\r\n'))

    return blocks


def get_all_text_files(root_dir='.'):
    text_files = []
    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]

        for file in filenames:
            file_path = os.path.join(dirpath, file)
            is_text = False
            for enc in ['utf-8', 'utf-8-sig', 'cp1251']:
                try:
                    with open(file_path, 'r', encoding=enc) as f:
                        f.read(1024)
                    is_text = True
                    break
                except UnicodeDecodeError:
                    pass
            if is_text:
                text_files.append(file_path)
    return text_files


def normalize_str(s):
    return re.sub(r'\s+', ' ', s.strip())


def find_matches(search_lines, file_lines):
    matches = []
    n_s = len(search_lines)
    n_f = len(file_lines)

    if n_s == 0:
        return matches

    for i in range(n_f - n_s + 1):
        match = True
        for j in range(n_s):
            if normalize_str(file_lines[i + j]) != normalize_str(search_lines[j]):
                match = False
                break
        if match:
            matches.append((i, i + n_s))

    return matches


def main():
    blocks = parse_input()

    if not blocks:
        print(f"{Colors.RED}Не найдено ни одного корректного блока с маркерами <<<< ==== >>>>!{Colors.RESET}")
        return

    print(f"\nРаспознано блоков правок: {len(blocks)}. Идет сканирование файлов...")

    # 1. Чтение всех файлов
    files = get_all_text_files()
    file_contents = {}
    for f in files:
        for enc in ['utf-8', 'utf-8-sig', 'cp1251']:
            try:
                with open(f, 'r', encoding=enc) as fp:
                    file_contents[f] = fp.read().splitlines()
                break
            except UnicodeDecodeError:
                pass

    # 2. Поиск совпадений
    block_matches = []
    for idx, block in enumerate(blocks):
        search_lines = block['search']
        matches_for_block = []

        # Сначала пробуем найти точное совпадение
        for path, lines in file_contents.items():
            matches = find_matches(search_lines, lines)
            for m in matches:
                matches_for_block.append((path, m[0], m[1]))

        # Нечеткий поиск, если точных совпадений нет
        if not matches_for_block:
            n_s = len(search_lines)
            if n_s >= 2:  # Нечеткий поиск имеет смысл при длине блока от 2 строк
                tolerance = max(1, int(n_s * 0.05))
                fuzzy_candidates = []

                for path, lines in file_contents.items():
                    n_f = len(lines)
                    for L in range(n_s - tolerance, n_s + tolerance + 1):
                        if L <= 0 or L > n_f:
                            continue

                        for start_idx in range(n_f - L + 1):
                            # Сравниваем первую и последнюю строки
                            if (normalize_str(lines[start_idx]) == normalize_str(search_lines[0]) and
                                    normalize_str(lines[start_idx + L - 1]) == normalize_str(search_lines[-1])):

                                # Собираем расхождения строк
                                mismatches = []
                                max_len = max(n_s, L)
                                for j in range(max_len):
                                    if j < n_s and j < L:
                                        s_norm = normalize_str(search_lines[j])
                                        f_norm = normalize_str(lines[start_idx + j])
                                        if s_norm != f_norm:
                                            mismatches.append((j + 1, search_lines[j], lines[start_idx + j]))
                                    elif j < n_s:
                                        mismatches.append((j + 1, search_lines[j], "<строка отсутствует в файле>"))
                                    else:
                                        mismatches.append(
                                            (j + 1, "<строка отсутствует в поиске>", lines[start_idx + j]))

                                fuzzy_candidates.append({
                                    'path': path,
                                    'start': start_idx,
                                    'end': start_idx + L,
                                    'mismatches': mismatches
                                })

                for cand in fuzzy_candidates:
                    print(f"\n{Colors.YELLOW}Найден похожий блок в файле {cand['path']}:{Colors.RESET}")
                    print(f"Блок {idx + 1}:")
                    print(f"Начальная строка: {cand['start'] + 1}")
                    print(f"Конечная строка: {cand['end']}")

                    m_count = len(cand['mismatches'])
                    if m_count > 0:
                        print("Не совпадают строки:")
                        if m_count < 10:
                            for pos, s_line, f_line in cand['mismatches']:
                                print(f"  Строка {pos}:")
                                print(f"    Ожидалось: {Colors.RED}{s_line.strip()}{Colors.RESET}")
                                print(f"    Найдено  : {Colors.GREEN}{f_line.strip()}{Colors.RESET}")
                        else:
                            print(f"  (Всего не совпадает строк: {m_count})")
                    else:
                        print("Все внутренние строки совпадают.")

                    ans = input("Подтвердить? y/n: ").strip().lower()
                    if ans in ['y', 'yes', 'да', '1']:
                        matches_for_block.append((cand['path'], cand['start'], cand['end']))
                        break  # Нашли подтвержденного кандидата, переходим к следующему блоку

        block_matches.append(matches_for_block)

    # 3. Валидация (DRY RUN)
    errors = []
    missing_blocks = 0
    duplicate_blocks = 0

    for idx, matches in enumerate(block_matches):
        if len(matches) == 0:
            err_msg = f"Блок {idx + 1} НЕ НАЙДЕН НИ В ОДНОМ ФАЙЛЕ.\nОригинальный текст, который мы искали:\n" + "\n".join(
                blocks[idx]['search'])
            errors.append(err_msg)
            missing_blocks += 1
        elif len(matches) > 1:
            locs = "\n  - ".join([f"{p} (строки {s + 1}-{e})" for p, s, e in matches])
            err_msg = f"Блок {idx + 1} найден {len(matches)} раз (Неоднозначность!).\nГде найдено:\n  - {locs}\nОригинальный текст:\n" + "\n".join(
                blocks[idx]['search'])
            errors.append(err_msg)
            duplicate_blocks += 1

    if errors:
        print(f"\n{Colors.RED}{'=' * 60}")
        print(f"КРИТИЧЕСКАЯ ОШИБКА: ОТМЕНА ВСЕХ ПРАВОК.")
        if missing_blocks:
            print(f"Не найдено блоков: {missing_blocks}")
        if duplicate_blocks:
            print(f"Дублирующихся блоков: {duplicate_blocks}")
        print(f"{'=' * 60}{Colors.RESET}")
        for err in errors:
            print(f"{Colors.RED}{err}{Colors.RESET}")
            print(f"{Colors.YELLOW}{'-' * 60}{Colors.RESET}")
        sys.exit(1)

    # 4. Применение правок
    file_modifications = {f: [] for f in file_contents}
    for idx, matches in enumerate(block_matches):
        path, start_idx, end_idx = matches[0]
        file_modifications[path].append({
            'start': start_idx,
            'end': end_idx,
            'replace': blocks[idx]['replace'],
            'search': blocks[idx]['search']
        })

    files_changed = 0
    for path, mods in file_modifications.items():
        if not mods:
            continue

        mods.sort(key=lambda x: x['start'], reverse=True)
        lines = file_contents[path]

        for mod in mods:
            start = mod['start']
            end = mod['end']
            search_lines = mod['search']
            replace_lines = mod['replace']

            s_base_len = 0
            f_base_str = ""
            for j in range(len(search_lines)):
                if search_lines[j].strip():
                    s_line = search_lines[j]
                    s_base_len = len(s_line) - len(s_line.lstrip())
                    f_line = lines[start + j]
                    f_base_str = f_line[:len(f_line) - len(f_line.lstrip())]
                    break

            new_lines = []
            for r_line in replace_lines:
                if not r_line.strip():
                    new_lines.append("")
                    continue

                r_indent = len(r_line) - len(r_line.lstrip())
                extra = r_indent - s_base_len
                if extra < 0:
                    extra = 0

                extra_str = r_line[s_base_len: s_base_len + extra] if r_indent >= s_base_len else ""
                new_line = f_base_str + extra_str + r_line.lstrip()
                new_lines.append(new_line)

            lines = lines[:start] + new_lines + lines[end:]

        with open(path, 'w', encoding='utf-8') as f:
            f.write("\n".join(lines) + "\n")

        print(f"Обновлен файл: {Colors.YELLOW}{path}{Colors.RESET}")
        files_changed += 1

    print(f"\n{Colors.GREEN}{'=' * 60}")
    print(f"УСПЕХ! Правки применены.")
    print(f"Всего обработано блоков: {len(blocks)}")
    print(f"Изменено файлов: {files_changed}")
    print(f"{'=' * 60}{Colors.RESET}")


def run_replacer():
    while True:
        try:
            main()
        except KeyboardInterrupt:
            print(f"\n{Colors.YELLOW}Операция прервана пользователем.{Colors.RESET}")


if __name__ == '__main__':
    run_replacer()