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

        # Поддержка маркеров любой длины от 4 символов с возможностью комментариев или имен файлов в конце
        is_start = stripped.startswith('<<<<')
        is_mid = stripped.startswith('====') and len(stripped.replace('=', '').strip()) == 0
        is_end = stripped.startswith('>>>>')

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


import difflib

def is_binary_file(filepath):
    try:
        with open(filepath, 'rb') as f:
            chunk = f.read(1024)
            if b'\x00' in chunk:
                return True
            return False
    except IOError:
        return True


def get_all_text_files(root_dir='.', ignore_folders=None, ignore_files=None):
    text_files = []
    folders_to_ignore = set(IGNORE_DIRS)
    if ignore_folders:
        folders_to_ignore.update(ignore_folders)

    files_to_ignore = set(ignore_files) if ignore_files else set()

    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames[:] = [d for d in dirnames if d not in folders_to_ignore]

        for file in filenames:
            if file in files_to_ignore:
                continue
            file_path = os.path.join(dirpath, file)
            if not is_binary_file(file_path):
                text_files.append(file_path)
    return text_files


def normalize_str(s):
    return re.sub(r'\s+', ' ', s.strip())


def compute_mismatch_stats(search_lines, candidate_lines):
    search_text = "\n".join(normalize_str(l) for l in search_lines)
    cand_text = "\n".join(normalize_str(l) for l in candidate_lines)

    char_matcher = difflib.SequenceMatcher(None, search_text, cand_text)
    char_ratio = char_matcher.ratio()
    char_similarity_pct = char_ratio * 100
    char_mismatch_pct = 100.0 - char_similarity_pct

    total_chars = max(len(search_text), len(cand_text))
    char_matches = sum(triple.size for triple in char_matcher.get_matching_blocks())
    mismatched_chars = total_chars - char_matches

    s_norm = [normalize_str(l) for l in search_lines]
    c_norm = [normalize_str(l) for l in candidate_lines]
    line_matcher = difflib.SequenceMatcher(None, s_norm, c_norm)
    line_ratio = line_matcher.ratio()
    line_similarity_pct = line_ratio * 100
    line_mismatch_pct = 100.0 - line_similarity_pct

    total_lines = max(len(s_norm), len(c_norm))
    line_matches = sum(triple.size for triple in line_matcher.get_matching_blocks())
    mismatched_lines = total_lines - line_matches

    return {
        'mismatched_chars': mismatched_chars,
        'char_mismatch_pct': char_mismatch_pct,
        'mismatched_lines': mismatched_lines,
        'line_mismatch_pct': line_mismatch_pct,
        'char_similarity_pct': char_similarity_pct,
        'line_similarity_pct': line_similarity_pct
    }


def find_anchors_match(search_lines, file_lines):
    # 1. Очистка от пустых строк в начале и в конце
    s_clean = search_lines[:]
    while s_clean and not s_clean[0].strip():
        s_clean.pop(0)
    while s_clean and not s_clean[-1].strip():
        s_clean.pop()

    # 2. Если строк меньше 4, якорный поиск не применим
    if len(s_clean) < 4:
        return []

    s_norm = [normalize_str(l) for l in s_clean]
    f_norm = [normalize_str(l) for l in file_lines]

    def get_anchor_indices(anchor, target_lines, start_index=0):
        matches = []
        a_len = len(anchor)
        for i in range(start_index, len(target_lines) - a_len + 1):
            if target_lines[i:i+a_len] == anchor:
                matches.append(i)
        return matches

    # 6. Динамическое расширение якорей до len(s_clean) // 2
    max_anchor_len = max(3, len(s_clean) // 2)

    # 3-4. Поиск Top Anchor (Верхний якорь)
    top_anchor_len = 3
    start_idx_matches = []
    while top_anchor_len <= max_anchor_len:
        top_anchor = s_norm[:top_anchor_len]
        start_idx_matches = get_anchor_indices(top_anchor, f_norm)
        if len(start_idx_matches) == 1:
            break
        top_anchor_len += 1

    # Если уникальный верхний якорь не найден (либо 0, либо > 1 раз)
    if len(start_idx_matches) != 1:
        return []

    start_idx = start_idx_matches[0]

    # 3-4. Поиск Bottom Anchor (Нижний якорь)
    bottom_anchor_len = 3
    end_idx_matches = []
    while bottom_anchor_len <= max_anchor_len:
        bottom_anchor = s_norm[-bottom_anchor_len:]
        # Ищем только начиная с найденного начала блока
        end_idx_matches = get_anchor_indices(bottom_anchor, f_norm, start_idx)
        if len(end_idx_matches) == 1:
            break
        bottom_anchor_len += 1

    # Если уникальный нижний якорь не найден
    if len(end_idx_matches) != 1:
        return []

    end_anchor_start_idx = end_idx_matches[0]
    end_idx = end_anchor_start_idx + bottom_anchor_len

    # 5. Возврат результата
    return [{'start': start_idx, 'end': end_idx, 'ratio': 0.95}]


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


def main(ignore_folders=None, ignore_files=None):
    blocks = parse_input()

    if not blocks:
        print(f"{Colors.RED}Не найдено ни одного корректного блока с маркерами <<<< ==== >>>>!{Colors.RESET}")
        return

    print(f"\nРаспознано блоков правок: {len(blocks)}. Идет сканирование файлов...")

    # 1. Чтение всех файлов с автоопределением кодировки
    files = get_all_text_files(ignore_folders=ignore_folders, ignore_files=ignore_files)
    file_contents = {}
    file_encodings = {}
    for f in files:
        for enc in ['utf-8', 'utf-8-sig', 'cp1251', 'latin-1']:
            try:
                with open(f, 'r', encoding=enc) as fp:
                    file_contents[f] = fp.read().splitlines()
                file_encodings[f] = enc
                break
            except (UnicodeDecodeError, LookupError):
                pass

    # 2. Поиск совпадений
    block_matches = []
    already_applied_blocks = set()
    for idx, block in enumerate(blocks):
        search_lines = block['search']
        matches_for_block = []

        # Поиск потенциальных кандидатов во всех файлах
        candidates = []
        for path, lines in file_contents.items():
            # Шаг 1: Проверка полного точного совпадения
            exact_matches = find_matches(search_lines, lines)
            if exact_matches:
                for m in exact_matches:
                    candidates.append({
                        'path': path,
                        'start': m[0],
                        'end': m[1],
                        'ratio': 1.0
                    })
                continue

            # Если точного совпадения нет, используем якорный поиск
            file_candidates = find_anchors_match(search_lines, lines)
            for cand in file_candidates:
                candidates.append({
                    'path': path,
                    'start': cand['start'],
                    'end': cand['end'],
                    'ratio': cand['ratio']
                })

        # Сортировка по совпадению (сначала наиболее похожие)
        candidates.sort(key=lambda x: x['ratio'], reverse=True)

        # Если найдено точное совпадение (ratio > 0.999), берем его без лишних вопросов
        exact_candidates = [c for c in candidates if c['ratio'] >= 0.999]
        if exact_candidates:
            for ec in exact_candidates:
                matches_for_block.append((ec['path'], ec['start'], ec['end']))
        else:
            # Иначе запрашиваем подтверждение для лучших нечетких совпадений
            for cand in candidates:
                path = cand['path']
                start = cand['start']
                end = cand['end']

                candidate_lines = file_contents[path][start:end]
                stats = compute_mismatch_stats(search_lines, candidate_lines)

                print(f"\n{Colors.YELLOW}Найден похожий блок в файле {path}:{Colors.RESET}")
                print(f"Блок {idx + 1}:")
                print(f"Начальная строка: {start + 1}")
                print(f"Конечная строка: {end}")
                print(f"Сходство кода: {stats['char_similarity_pct']:.1f}%")
                print(f"несоответствует символов: {stats['mismatched_chars']} ({stats['char_mismatch_pct']:.1f}%)")
                print(f"несоответствует строк: {stats['mismatched_lines']} ({stats['line_mismatch_pct']:.1f}%)")

                # Показ подробностей разницы
                s_norm = [normalize_str(l) for l in search_lines]
                c_norm = [normalize_str(l) for l in candidate_lines]
                line_matcher = difflib.SequenceMatcher(None, s_norm, c_norm)

                print("Детализация расхождений:")
                opcodes = line_matcher.get_opcodes()
                for tag, i1, i2, j1, j2 in opcodes:
                    if tag == 'replace':
                        for idx_s in range(i1, i2):
                            s_line = search_lines[idx_s]
                            idx_c = j1 + (idx_s - i1)
                            if idx_c < j2:
                                c_line = candidate_lines[idx_c]
                                line_diff_ratio = difflib.SequenceMatcher(None, normalize_str(s_line), normalize_str(c_line)).ratio()
                                line_char_mismatch_pct = (1.0 - line_diff_ratio) * 100
                                print(f"  Строка {idx_s + 1} (несоответствует символов: {line_char_mismatch_pct:.1f}%):")
                                print(f"    Ожидалось: {Colors.RED}{s_line.strip()}{Colors.RESET}")
                                print(f"    Найдено  : {Colors.GREEN}{c_line.strip()}{Colors.RESET}")
                            else:
                                print(f"  Строка {idx_s + 1} (удалена):")
                                print(f"    Ожидалось: {Colors.RED}{s_line.strip()}{Colors.RESET}")
                    elif tag == 'delete':
                        for idx_s in range(i1, i2):
                            s_line = search_lines[idx_s]
                            print(f"  Строка {idx_s + 1} (отсутствует в файле):")
                            print(f"    Ожидалось: {Colors.RED}{s_line.strip()}{Colors.RESET}")
                    elif tag == 'insert':
                        for idx_c in range(j1, j2):
                            c_line = candidate_lines[idx_c]
                            print(f"  Лишняя строка в файле:")
                            print(f"    Найдено  : {Colors.GREEN}{c_line.strip()}{Colors.RESET}")

                try:
                    ans = input("Подтвердить? y/n: ").strip().lower()
                except EOFError:
                    ans = 'n'

                if ans in ['y', 'yes', 'да', '1']:
                    matches_for_block.append((path, start, end))
                    break

        if not matches_for_block:
            replace_lines = block['replace']
            if len(replace_lines) > 0:
                is_applied = False
                for path, lines in file_contents.items():
                    # Проверяем, есть ли уже блок замены в каком-либо файле
                    if find_matches(replace_lines, lines):
                        is_applied = True
                        break
                if is_applied:
                    already_applied_blocks.add(idx)
            else:
                # Если блок замены пустой, а блок поиска не найден, возможно он уже удален
                already_applied_blocks.add(idx)

        block_matches.append(matches_for_block)

    # 3. Валидация
    errors = []
    missing_blocks = 0
    duplicate_blocks = 0

    for idx, matches in enumerate(block_matches):
        if len(matches) == 0:
            if idx in already_applied_blocks:
                print(f"{Colors.GREEN}Блок {idx + 1} пропущен: похоже, правки уже внесены.{Colors.RESET}")
                continue
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
        return False

    # 4. Применение правок
    file_modifications = {f: [] for f in file_contents}
    for idx, matches in enumerate(block_matches):
        if not matches:
            # Пропускаем блоки, которые не были найдены (или уже были применены)
            continue

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
            for j in range(min(len(search_lines), len(lines) - start)):
                if search_lines[j].strip() and lines[start + j].strip():
                    s_line = search_lines[j]
                    s_base_len = len(s_line) - len(s_line.lstrip())
                    f_line = lines[start + j]
                    f_base_str = f_line[:len(f_line) - len(f_line.lstrip())]
                    break

            f_base_len = len(f_base_str)
            indent_char = '\t' if '\t' in f_base_str else ' '

            new_lines = []
            for r_line in replace_lines:
                if not r_line.strip():
                    new_lines.append("")
                    continue

                r_indent = len(r_line) - len(r_line.lstrip())
                relative_indent = r_indent - s_base_len
                target_indent_len = max(0, f_base_len + relative_indent)
                new_line = (indent_char * target_indent_len) + r_line.lstrip()
                new_lines.append(new_line)

            lines = lines[:start] + new_lines + lines[end:]

        # Атомарная запись с сохранением оригинальной кодировки
        temp_path = path + ".tmp"
        try:
            enc = file_encodings.get(path, 'utf-8')
            with open(temp_path, 'w', encoding=enc) as f:
                f.write("\n".join(lines) + "\n")
            if os.path.exists(path):
                os.replace(temp_path, path)
            else:
                os.rename(temp_path, path)
        except Exception as e:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            print(f"{Colors.RED}Ошибка при записи файла {path}: {e}{Colors.RESET}")
            return False

        print(f"Обновлен файл: {Colors.YELLOW}{path}{Colors.RESET}")
        files_changed += 1

    print(f"\n{Colors.GREEN}{'=' * 60}")
    print(f"Правки применены.")
    print(f"Всего обработано блоков: {len(blocks)}")
    print(f"Изменено файлов: {files_changed}")
    print(f"{'=' * 60}{Colors.RESET}")
    return True


def run_replacer(ignore_folders=None, ignore_files=None):
    while True:
        try:
            main(ignore_folders=ignore_folders, ignore_files=ignore_files)
        except KeyboardInterrupt:
            print(f"\n{Colors.YELLOW}Операция прервана пользователем.{Colors.RESET}")
            break


if __name__ == '__main__':
    run_replacer()
