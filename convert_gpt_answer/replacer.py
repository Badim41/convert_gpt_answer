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


def get_all_text_files(root_dir='.'):
    text_files = []
    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]

        for file in filenames:
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


def find_best_matches(search_lines, file_lines, threshold=0.4):
    s_norm = [normalize_str(l) for l in search_lines]
    f_norm = [normalize_str(l) for l in file_lines]

    n_s = len(s_norm)
    n_f = len(f_norm)

    if n_s == 0 or n_f == 0:
        return []

    exact = find_matches(search_lines, file_lines)
    if exact:
        return [{'start': m[0], 'end': m[1], 'ratio': 1.0} for m in exact]

    matcher = difflib.SequenceMatcher(None, s_norm, f_norm)
    matching_blocks = matcher.get_matching_blocks()

    potential_starts = set()
    for s_idx, f_idx, length in matching_blocks:
        if length == 0:
            continue
        est_start = max(0, min(n_f - 1, f_idx - s_idx))
        potential_starts.add(est_start)

    refined_starts = set()
    for ps in potential_starts:
        for offset in range(-5, 6):
            ns = ps + offset
            if 0 <= ns < n_f:
                refined_starts.add(ns)

    best_candidate_for_start = {}
    for start in refined_starts:
        min_len = max(1, int(n_s * 0.5))
        max_len = min(n_f - start, int(n_s * 1.5))
        for length in range(min_len, max_len + 1):
            end = start + length
            cand_sub = f_norm[start:end]

            line_ratio = difflib.SequenceMatcher(None, s_norm, cand_sub).ratio()
            if line_ratio >= threshold:
                if start not in best_candidate_for_start or line_ratio > best_candidate_for_start[start]['ratio']:
                    best_candidate_for_start[start] = {
                        'start': start,
                        'end': end,
                        'ratio': line_ratio
                    }

    sorted_candidates = sorted(best_candidate_for_start.values(), key=lambda x: x['ratio'], reverse=True)
    non_overlapping = []
    for cand in sorted_candidates:
        overlap = False
        for existing in non_overlapping:
            if not (cand['end'] <= existing['start'] or cand['start'] >= existing['end']):
                overlap = True
                break
        if not overlap:
            non_overlapping.append(cand)

    return non_overlapping


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

    # 1. Чтение всех файлов с автоопределением кодировки
    files = get_all_text_files()
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
    for idx, block in enumerate(blocks):
        search_lines = block['search']
        matches_for_block = []

        # Поиск потенциальных кандидатов во всех файлах
        candidates = []
        for path, lines in file_contents.items():
            file_candidates = find_best_matches(search_lines, lines, threshold=0.4)
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

        block_matches.append(matches_for_block)

    # 3. Валидация
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
        return False

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


def run_replacer():
    while True:
        try:
            main()
        except KeyboardInterrupt:
            print(f"\n{Colors.YELLOW}Операция прервана пользователем.{Colors.RESET}")
            break


if __name__ == '__main__':
    run_replacer()
