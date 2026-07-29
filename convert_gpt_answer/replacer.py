import re
import sys
import os
import subprocess
import tempfile
import time

COUNT_TIME = True
AUTO_MODE = '-y' in sys.argv or '--auto' in sys.argv

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

CODE_EXTENSIONS = {
    '.py', '.js', '.jsx', '.ts', '.tsx', '.vue', '.html', '.css', '.scss',
    '.json', '.yml', '.yaml', '.md', '.sh', '.bash', '.ps1', '.bat', '.cmd',
    '.php', '.java', '.c', '.cpp', '.h', '.hpp', '.cs', '.go', '.rs', '.sql'
}


def extract_filenames_from_prompt(lines):
    filenames = set()
    pattern = re.compile(r'([@a-zA-Z0-9_./\\-]+\.[a-zA-Z0-9]{2,10})')
    for line in lines:
        if line.startswith('<<<<'):
            continue
        for match in pattern.findall(line):
            if "/" in match or "\\" in match or "." in match:
                filenames.add(os.path.basename(match))
    return filenames


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

    for line_idx, line in enumerate(lines):
        stripped = line.strip()

        # Поддержка маркеров любой длины от 4 символов с возможностью комментариев или имен файлов в конце
        is_start = stripped.startswith('<<<<')
        is_mid = stripped.startswith('====')
        is_end = stripped.startswith('>>>>')

        if state == 0:
            if is_start:
                state = 1
                current_search = []
                current_replace = []
        elif state == 1:
            if is_mid:
                state = 2
            elif is_end:
                state = 0
            elif not is_start:
                current_search.append(line.rstrip('\r\n'))
        elif state == 2:
            if is_end:
                def trim_lines_search(l):
                    s, e = 0, len(l)
                    while s < e and not l[s].strip(): s += 1
                    while e > s and not l[e - 1].strip(): e -= 1
                    return l[s:e]

                blocks.append({
                    'search': trim_lines_search(current_search),
                    'replace': current_replace
                })
                state = 0
            elif not is_start and not is_mid:
                current_replace.append(line.rstrip('\r\n'))

    # Решает проблему потери последнего блока при EOF
    if state == 2:
        def trim_lines_search(l):
            s, e = 0, len(l)
            while s < e and not l[s].strip(): s += 1
            while e > s and not l[e - 1].strip(): e -= 1
            return l[s:e]
        blocks.append({'search': trim_lines_search(current_search), 'replace': current_replace})

    return blocks, lines


import difflib

def is_binary_file(filepath):
    try:
        with open(filepath, 'rb') as f:
            chunk = f.read(1024)
            # Проверка стандартных BOM для текста
            if chunk.startswith(b'\xff\xfe') or chunk.startswith(b'\xfe\xff') or chunk.startswith(b'\xef\xbb\xbf'):
                return False
            # Проверка сигнатуры SQLite
            if chunk.startswith(b'SQLite format 3\x00'):
                return True
            # Общая проверка на нулевые байты (бинарники)
            if b'\x00' in chunk:
                return True
            return False
    except IOError:
        return True


def get_all_text_files(root_dir='.', ignore_folders=None, ignore_files=None, prompt_filenames=None):
    folders_to_ignore = set(IGNORE_DIRS)
    if ignore_folders:
        folders_to_ignore.update(ignore_folders)

    files_to_ignore = set(ignore_files) if ignore_files else set()
    prompt_filenames = prompt_filenames or set()

    file_stats = []

    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames[:] = [d for d in dirnames if d not in folders_to_ignore]

        for file in filenames:
            if file in files_to_ignore:
                continue

            file_path = os.path.join(dirpath, file)

            try:
                size = os.path.getsize(file_path)
                if size > 1 * 1024 * 1024:  # Пропускаем файлы больше 1 МБ (защита от нехватки памяти)
                    continue
                mtime = os.path.getmtime(file_path)
            except OSError:
                continue

            priority = 100 if file in prompt_filenames else 0
            file_stats.append({
                'path': file_path,
                'size': size,
                'mtime': mtime,
                'priority': priority
            })

    # Сортировка: 1. Из промпта 2. Недавно измененные (mtime убывает) 3. Меньшего размера (size возрастает)
    file_stats.sort(key=lambda x: (-x['priority'], -x['mtime'], x['size']))

    text_files = []
    for item in file_stats:
        if not is_binary_file(item['path']):
            text_files.append(item['path'])
    return text_files


def normalize_str(s):
    return s.strip()


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

    # 2. Если строк меньше 3, якорный поиск не применим
    if len(s_clean) < 3:
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

    leading = 0
    while search_lines and leading < len(search_lines) and not search_lines[leading].strip():
        leading += 1
    trailing = 0
    while search_lines and trailing < len(search_lines) and not search_lines[-(trailing + 1)].strip():
        trailing += 1

    # Динамическое расширение якорей 
    max_anchor_len = max(3, len(s_clean) // 2)

    # Поиск Top Anchor (Верхний якорь)
    top_anchor_len = max(2, min(3, len(s_clean)))
    start_idx_matches = []
    while top_anchor_len <= max_anchor_len:
        top_anchor = s_norm[:top_anchor_len]
        start_idx_matches = get_anchor_indices(top_anchor, f_norm)
        if len(start_idx_matches) >= 1:
            break
        top_anchor_len += 1

    if not start_idx_matches:
        return []

    candidates = []
    for start_idx in start_idx_matches:
        # Поиск Bottom Anchor (Нижний якорь)
        bottom_anchor_len = 2
        end_idx_matches = []
        while bottom_anchor_len <= max_anchor_len:
            bottom_anchor = s_norm[-bottom_anchor_len:]
            end_idx_matches = get_anchor_indices(bottom_anchor, f_norm, start_idx)
            if end_idx_matches:
                break
            bottom_anchor_len += 1

        if end_idx_matches:
            # Берем ближайший подходящий якорь, чтобы избежать захвата лишнего кода при добавлении/удалении строк
            end_anchor_start_idx = min(end_idx_matches, key=lambda x: abs(x - (start_idx + len(s_clean))))
            end_idx = end_anchor_start_idx + bottom_anchor_len

            if end_idx - start_idx <= len(s_clean) * 2:
                actual_start = max(0, start_idx - leading)
                actual_end = min(len(file_lines), end_idx + trailing)
                candidates.append({'start': actual_start, 'end': actual_end, 'ratio': 0.95})

    return candidates


def find_fuzzy_block(search_lines, file_lines, threshold=0.85):
    s_clean = search_lines[:]
    leading = 0
    while s_clean and not s_clean[0].strip():
        s_clean.pop(0)
        leading += 1
    trailing = 0
    while s_clean and not s_clean[-1].strip():
        s_clean.pop()
        trailing += 1

    if not s_clean:
        return []

    s_norm = [normalize_str(l) for l in s_clean]
    f_norm = [normalize_str(l) for l in file_lines]

    n_s = len(s_norm)
    n_f = len(f_norm)

    if n_s == 0:
        return []

    candidates = []
    s_text = "\n".join(s_norm)

    # Если файл меньше искомого блока, просто сравниваем их целиком
    if n_f < n_s:
        f_text = "\n".join(f_norm)
        ratio = difflib.SequenceMatcher(None, s_text, f_text).ratio()
        if ratio >= max(threshold, 0.95):  # Строгий порог для коротких файлов
            candidates.append({'start': 0, 'end': len(file_lines), 'ratio': ratio * 0.98})
        return candidates

    dynamic_threshold = threshold if n_s >= 4 else 0.70

    for i in range(n_f - n_s + 1):
        window = f_norm[i:i+n_s]
        w_text = "\n".join(window)

        # O(N) фильтр SequenceMatcher (в 10 раз быстрее полного ratio)
        matcher = difflib.SequenceMatcher(None, s_text, w_text)
        if matcher.quick_ratio() < dynamic_threshold * 0.8:
            continue

        ratio = matcher.ratio()
        if ratio >= dynamic_threshold:
            actual_start = max(0, i - leading)
            actual_end = min(len(file_lines), i + n_s + trailing)
            candidates.append({'start': actual_start, 'end': actual_end, 'ratio': ratio * 0.98})

    return candidates


def find_matches(search_lines, file_lines):
    matches = []
    n_s = len(search_lines)
    n_f = len(file_lines)

    if n_s == 0:
        return matches

    i = 0
    while i <= n_f - n_s:
        match = True
        for j in range(n_s):
            if file_lines[i + j].rstrip('\r\n') != search_lines[j].rstrip('\r\n'):
                match = False
                break
        if match:
            matches.append((i, i + n_s))
            i += n_s  # Избегаем перекрытия совпадений
        else:
            i += 1

    return matches


def extract_powershell_commands(lines):
    commands = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()

        # 1. Поиск блоков powershell в markdown
        if stripped.lower() in ["```powershell", "```ps1", "```ps"]:
            start = i + 1
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                i += 1
            if i < len(lines):
                cmd = "".join(lines[start:i])
                if cmd.strip():
                    commands.append(cmd)

        # 2. Поиск блоков $content = @'
        elif stripped.startswith("$content = @'") or stripped.startswith('$content=@\''):
            start = i
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("'@"):
                i += 1
            # Захватываем команду Out-File если она идет сразу после
            while i < len(lines) and "Out-File" not in lines[i]:
                i += 1
            if i < len(lines):
                cmd = "".join(lines[start:i+1])
                commands.append(cmd)
        i += 1

    return commands


def execute_powershell(script):
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.ps1', delete=False, encoding='utf-8-sig') as f:
            f.write(script)
            temp_path = f.name

        process = subprocess.Popen(["powershell", "-ExecutionPolicy", "Bypass", "-NoProfile", "-NonInteractive", "-File", temp_path],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
        stdout, stderr = process.communicate()
        if stdout:
            print(stdout)
        if stderr:
            print(f"{Colors.YELLOW}Вывод PowerShell:\n{stderr}{Colors.RESET}")

        try:
            # Даем процессу время отпустить файл
            import time; time.sleep(0.5)
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError as e:
            print(f"{Colors.YELLOW}Не удалось удалить временный файл {temp_path}: {e}{Colors.RESET}")

        if process.returncode == 0:
            print(f"{Colors.GREEN}Скрипт успешно выполнен.{Colors.RESET}")
            return True
        else:
            print(f"{Colors.RED}Скрипт завершился с кодом {process.returncode}.{Colors.RESET}")
            return False
    except Exception as e:
        print(f"{Colors.RED}Ошибка запуска PowerShell: {e}{Colors.RESET}")
        return False


def main(ignore_folders=None, ignore_files=None):
    blocks, input_lines = parse_input()
    prompt_filenames = extract_filenames_from_prompt(input_lines)

    ps_commands = extract_powershell_commands(input_lines)
    if ps_commands:
        print(f"\n{Colors.YELLOW}Обнаружены команды PowerShell ({len(ps_commands)} шт.):{Colors.RESET}")
        for idx, cmd in enumerate(ps_commands, 1):
            print(f"{Colors.YELLOW}--- Скрипт {idx} ---{Colors.RESET}")
            print(cmd.strip()[:500] + ("..." if len(cmd.strip()) > 500 else ""))
            print(f"{Colors.YELLOW}-------------------{Colors.RESET}")

        if AUTO_MODE:
            ans = 'y'
            print("Auto-mode: PowerShell скрипт выполняется автоматически.")
        else:
            try:
                ans = input("Выполнить команды PowerShell? (y/n): ").strip().lower()
            except EOFError:
                ans = 'n'

        if ans in ['y', 'yes', 'да', '1']:
            for idx, cmd in enumerate(ps_commands, 1):
                print(f"\n{Colors.YELLOW}Выполнение скрипта {idx}...{Colors.RESET}")
                success = execute_powershell(cmd)
                if not success:
                    print(f"{Colors.RED}Прерывание работы из-за ошибки в скрипте PowerShell.{Colors.RESET}")
                    return False

    if not blocks:
        if not ps_commands:
            print(f"{Colors.RED}Не найдено ни одного корректного блока с маркерами <<<< ==== >>>>!{Colors.RESET}")
        return

    start_time = time.time()

    print(f"\nРаспознано блоков правок: {len(blocks)}. Идет сканирование файлов...")

    # 1. Чтение всех файлов с автоопределением кодировки
    files = get_all_text_files(ignore_folders=ignore_folders, ignore_files=ignore_files, prompt_filenames=prompt_filenames)
    file_contents = {}
    file_encodings = {}
    non_utf8_files = []

    for f in files:
        is_read = False
        try:
            with open(f, 'rb') as fp:
                has_bom = fp.read(3) == b'\xef\xbb\xbf'
        except Exception:
            has_bom = False

        # Если есть BOM, используем utf-8-sig, иначе обычный utf-8
        encodings = ['utf-8-sig'] if has_bom else ['utf-8']
        for enc in encodings:
            try:
                with open(f, 'r', encoding=enc, newline='') as fp:
                    content = fp.read()
                    file_newlines = '\r\n' if '\r\n' in content else '\n'
                    file_contents[f] = content.splitlines()
                file_encodings[f] = (enc, file_newlines)
                is_read = True
                break
            except (UnicodeError, LookupError, ValueError):
                pass

        if not is_read:
            non_utf8_files.append(f)

    # 2. Поиск совпадений
    block_matches = []
    already_applied_blocks = {}

    code_file_contents = {p: l for p, l in file_contents.items() if os.path.splitext(p)[1].lower() in CODE_EXTENSIONS or not os.path.splitext(p)[1]}
    other_file_contents = {p: l for p, l in file_contents.items() if p not in code_file_contents}

    def get_candidates(search_lines, target_files):
        cands = []

        for path in target_files:
            lines = target_files[path]

            exact_matches = find_matches(search_lines, lines)
            if exact_matches:
                for m in exact_matches:
                    cands.append({'path': path, 'start': m[0], 'end': m[1], 'ratio': 1.0})
                continue

            file_candidates = find_anchors_match(search_lines, lines)
            if file_candidates:
                for cand in file_candidates:
                    cands.append({'path': path, 'start': cand['start'], 'end': cand['end'], 'ratio': cand['ratio']})
            else:
                s_clean_len = len([l for l in search_lines if l.strip()])
                if s_clean_len > 0:
                    for cand in find_fuzzy_block(search_lines, lines):
                        cands.append({'path': path, 'start': cand['start'], 'end': cand['end'], 'ratio': cand['ratio']})
        return cands

    for idx, block in enumerate(blocks):
        search_lines = block['search']
        matches_for_block = []

        # Поиск потенциальных кандидатов сначала в файлах кода
        candidates = get_candidates(search_lines, code_file_contents)

        if not candidates and other_file_contents:
            print(f"\n{Colors.YELLOW}Блок {idx + 1} не найден в файлах кода.{Colors.RESET}")
            ans = 'y' if AUTO_MODE else 'n'
            if not AUTO_MODE:
                try:
                    ans = input("Искать в остальных файлах (базы данных, логи и т.д.)? (y/n для отмены поиска блока): ").strip().lower()
                except EOFError:
                    ans = 'n'

            if ans in ['y', 'yes', 'да', '1']:
                candidates = get_candidates(search_lines, other_file_contents)

        if not candidates and non_utf8_files:
            print(f"\n{Colors.YELLOW}Блок {idx + 1} не найден в текущих файлах. Подгрузка файлов с другими кодировками...{Colors.RESET}")
            newly_read = {}
            for f in list(non_utf8_files):
                for enc in ['utf-16', 'utf-16le', 'utf-16be', 'cp1251', 'latin-1']:
                    try:
                        with open(f, 'r', encoding=enc, newline='') as fp:
                            content = fp.read()
                            file_newlines = '\r\n' if '\r\n' in content else '\n'
                            lines = content.splitlines()
                        file_contents[f] = lines
                        file_encodings[f] = (enc, file_newlines)
                        newly_read[f] = lines
                        non_utf8_files.remove(f)

                        # Распределяем файлы по категориям для будущих блоков
                        if os.path.splitext(f)[1].lower() in CODE_EXTENSIONS or not os.path.splitext(f)[1]:
                            code_file_contents[f] = lines
                        else:
                            other_file_contents[f] = lines
                        break
                    except (UnicodeError, LookupError, ValueError):
                        pass

            if newly_read:
                candidates = get_candidates(search_lines, newly_read)

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

                if AUTO_MODE and stats['char_similarity_pct'] > 90:
                    ans = 'y'
                    print("Auto-mode: Сходство высокое, блок подтвержден.")
                else:
                    try:
                        ans = input("Подтвердить? y/n: ").strip().lower()
                    except EOFError:
                        ans = 'n'

                if ans in ['y', 'yes', 'да', '1']:
                    matches_for_block.append((path, start, end))
                    break

        if not matches_for_block:
            replace_lines = block['replace']
            if len(replace_lines) >= 3:
                applied_locs = []
                for path, lines in file_contents.items():
                    # Проверяем, есть ли уже блок замены в каком-либо файле
                    # Игнорируем пустые строки для более надежной проверки
                    non_empty_replace = [l for l in replace_lines if l.strip()]
                    found_replacements = find_matches(non_empty_replace, lines) if non_empty_replace else []
                    for m in found_replacements:
                        applied_locs.append((path, m[0], m[1]))
                if applied_locs:
                    already_applied_blocks[idx] = applied_locs
            else:
                # Если блок замены пустой, а блок поиска не найден, возможно он уже удален
                already_applied_blocks[idx] = []

        block_matches.append(matches_for_block)

    # 3. Валидация
    errors = []
    missing_blocks = 0
    duplicate_blocks = 0

    for idx, matches in enumerate(block_matches):
        if len(matches) == 0:
            if idx in already_applied_blocks:
                locs = already_applied_blocks[idx]
                if locs:
                    locs_str = "\n  - ".join([f"{p} (строки {s + 1}-{e})" for p, s, e in locs])
                    print(f"\n{Colors.YELLOW}ВНИМАНИЕ: Для блока {idx + 1} оригинал не найден, но точная копия текста ЗАМЕНЫ уже присутствует в коде:{Colors.RESET}")
                    print(f"  - {locs_str}")
                    try:
                        ans = input("похоже, правки уже внесены. Пропустить этот блок? (y/n): ").strip().lower()
                    except EOFError:
                        ans = 'n'
                    if ans in ['y', 'yes', 'да', '1']:
                        continue
                else:
                    print(f"{Colors.GREEN}Блок {idx + 1} пропущен: блок замены пуст, оригинал не найден (вероятно, уже удален).{Colors.RESET}")
                    continue

            err_msg = f"Блок {idx + 1} НЕ НАЙДЕН НИ В ОДНОМ ФАЙЛЕ.\nОригинальный текст, который мы искали:\n" + "\n".join(
                blocks[idx]['search'])
            errors.append(err_msg)
            missing_blocks += 1
        elif len(matches) > 1:
            locs = "\n  - ".join([f"{p} (строки {s + 1}-{e})" for p, s, e in matches])
            print(f"\n{Colors.YELLOW}Блок {idx + 1} найден {len(matches)} раз (Неоднозначность!).\nГде найдено:\n  - {locs}{Colors.RESET}")
            if AUTO_MODE:
                ans = '1'
                print("Auto-mode: применяем только к первому совпадению.")
            else:
                try:
                    ans = input("Применить ко всем? (y - ко всем, 1 - только к первому, s - пропустить): ").strip().lower()
                except EOFError:
                    ans = 's'

            if ans in ['s', 'skip', 'пропустить']:
                print(f"{Colors.YELLOW}Блок {idx + 1} пропущен пользователем.{Colors.RESET}")
                block_matches[idx] = []
            elif ans == '1':
                block_matches[idx] = [matches[0]]
            elif ans not in ['y', 'yes', 'да']:
                err_msg = f"Блок {idx + 1} найден {len(matches)} раз.\nГде найдено:\n  - {locs}\nОригинальный текст:\n" + "\n".join(
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

        elapsed = time.time() - start_time
        if COUNT_TIME and elapsed > 5:
            print(f"\n{Colors.YELLOW}Время выполнения: {elapsed:.2f} сек.{Colors.RESET}")
        return False

    # 4. Применение правок
    file_modifications = {f: [] for f in file_contents}
    for idx, matches in enumerate(block_matches):
        if not matches:
            # Пропускаем блоки, которые не были найдены (или уже были применены)
            continue

        for match in matches:
            path, start_idx, end_idx = match
            file_modifications[path].append({
                'start': start_idx,
                'end': end_idx,
                'replace': blocks[idx]['replace'],
                'search': blocks[idx]['search']
            })

    files_changed = 0
    pending_replacements = []

    # Первый проход: формирование всех файлов в памяти и запись во временные файлы (.tmp)
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

            f_base_str = ""
            s_base_str = ""
            for j in range(min(len(search_lines), len(lines) - start)):
                if search_lines[j].strip() and lines[start + j].strip():
                    s_line = search_lines[j]
                    s_base_str = s_line[:len(s_line) - len(s_line.lstrip())]
                    f_line = lines[start + j]
                    f_base_str = f_line[:len(f_line) - len(f_line.lstrip())]
                    break

            new_lines = []
            for r_line in replace_lines:
                if not r_line.strip():
                    new_lines.append("")
                    continue

                r_indent_str = r_line[:len(r_line) - len(r_line.lstrip())]

                if r_indent_str.startswith(s_base_str) and len(s_base_str) > 0:
                    relative_indent_str = r_indent_str[len(s_base_str):]
                    new_line = f_base_str + relative_indent_str + r_line.lstrip()
                else:
                    new_line = r_line  # Прямое использование отступов, если база не совпадает

                new_lines.append(new_line.rstrip('\n\r'))

            lines = lines[:start] + new_lines + lines[end:]

        # Запись во временный словарь (в памяти)
        try:
            enc_info = file_encodings.get(path, ('utf-8', '\n'))
            if isinstance(enc_info, tuple):
                enc, newline_char = enc_info
            else:
                enc, newline_char = enc_info, '\n'

            if 'utf-16' in enc.lower():
                print(f"\n{Colors.YELLOW}Файл {path} обнаружен в кодировке {enc}.{Colors.RESET}")
                ans = 'y' if AUTO_MODE else 'n'
                if not AUTO_MODE:
                    try:
                        ans = input("Разрешить внесение правок и перезаписать в UTF-8? (y/n): ").strip().lower()
                    except EOFError:
                        ans = 'n'
                if ans in ['y', 'yes', 'да', '1']:
                    enc = 'utf-8'
                    print(f"{Colors.GREEN}Кодировка файла {path} изменена на UTF-8 при сохранении.{Colors.RESET}")
                else:
                    print(f"{Colors.RED}Правки для файла {path} отменены.{Colors.RESET}")
                    continue

            pending_replacements.append((path, lines, enc, newline_char))
        except Exception as e:
            print(f"{Colors.RED}Ошибка при подготовке файла {path}: {e}{Colors.RESET}")
            return False

    # Второй проход: Создание бэкапов и атомарная перезапись (Транзакционность)
    backups = []
    import shutil
    try:
        for path, lines, enc, newline_char in pending_replacements:
            if os.path.exists(path):
                backup_path = path + ".bak.tmp"
                shutil.copy2(path, backup_path)
                backups.append((path, backup_path))
            
            with open(path, 'w', encoding=enc, newline='') as f:
                f.write(newline_char.join(lines) + newline_char)
            print(f"Обновлен файл: {Colors.YELLOW}{path}{Colors.RESET}")
            files_changed += 1

        # Очистка бэкапов при успехе
        for _, backup_path in backups:
            try: os.remove(backup_path)
            except: pass
    except Exception as e:
        print(f"{Colors.RED}КРИТИЧЕСКАЯ ОШИБКА ПРИ ЗАПИСИ: {e}{Colors.RESET}")
        print(f"{Colors.YELLOW}Выполняется откат изменений...{Colors.RESET}")
        for path, backup_path in backups:
            try:
                os.replace(backup_path, path)
                print(f"{Colors.GREEN}Откат файла {path} успешен.{Colors.RESET}")
            except Exception as rb_e:
                print(f"{Colors.RED}Ошибка отката {path}: {rb_e}{Colors.RESET}")
        return False

    print(f"\n{Colors.GREEN}{'=' * 60}")
    print(f"Правки применены.")
    print(f"Всего обработано блоков: {len(blocks)}")
    print(f"Изменено файлов: {files_changed}")
    print(f"{'=' * 60}{Colors.RESET}")

    elapsed = time.time() - start_time
    if COUNT_TIME and elapsed > 5:
        print(f"{Colors.YELLOW}Время выполнения: {elapsed:.2f} сек.{Colors.RESET}")

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
