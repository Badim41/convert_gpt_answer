import re
import sys
import os
import subprocess
import tempfile
import time
import concurrent.futures
import sqlite3
import hashlib
import shutil
import threading
import zipfile
import ast
import datetime

RG_AVAILABLE = shutil.which('rg') is not None

def generate_whitespace_agnostic_regex(lines):
    clean_lines = [l for l in lines if l.strip()]
    if not clean_lines:
        return ""
    parts = []
    for line in clean_lines:
        # Заменяем любые пробелы/табы на нулевой байт (маркер)
        tokenized = re.sub(r'\s+', '\x00', line.strip())
        # Экранируем спецсимволы кода для Regex
        escaped = re.escape(tokenized)
        # Возвращаем маркеры в виде regex-пробелов \s+
        regex_line = escaped.replace('\x00', r'\s+')
        parts.append(regex_line)
    return r'\s+'.join(parts)

def ripgrep_search(search_lines, root_dir='.'):
    if not RG_AVAILABLE:
        return None
    pattern = generate_whitespace_agnostic_regex(search_lines)
    if not pattern:
        return None

    try:
        # -U для многострочного поиска (multiline), -l для возврата только путей файлов
        result = subprocess.run(['rg', '-U', '-l', pattern, root_dir], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            paths = set()
            for line in result.stdout.splitlines():
                paths.add(os.path.normpath(line.strip()))
            return paths
        return None
    except (subprocess.TimeoutExpired, Exception):
        return None

try:
    from rapidfuzz import fuzz
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False

try:
    import charset_normalizer
    CHARSET_NORMALIZER_AVAILABLE = True
except ImportError:
    CHARSET_NORMALIZER_AVAILABLE = False

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
    ext = os.path.splitext(filepath)[1].lower()
    if ext in CODE_EXTENSIONS:
        return False
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


def _scan_directory_tree(root_dir, folders_to_ignore, files_to_ignore):
    entries = []
    try:
        with os.scandir(root_dir) as it:
            for entry in it:
                if entry.name in files_to_ignore:
                    continue
                if entry.is_dir():
                    if entry.name not in folders_to_ignore:
                        entries.extend(_scan_directory_tree(entry.path, folders_to_ignore, files_to_ignore))
                elif entry.is_file():
                    entries.append(entry.path)
    except OSError:
        pass
    return entries

HISTORY_DIR = '.deepsearch/history'

class TransactionManager:
    def __init__(self, root_dir='.'):
        self.history_dir = os.path.join(root_dir, HISTORY_DIR)
        os.makedirs(self.history_dir, exist_ok=True)

    def backup_files(self, file_paths):
        tx_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        zip_path = os.path.join(self.history_dir, f"tx_{tx_id}.zip")
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for path in file_paths:
                if os.path.exists(path):
                    # Сохраняем правильную относительную структуру папок внутри архива
                    rel_path = os.path.relpath(path, start='.')
                    zf.write(path, arcname=rel_path)
        self._cleanup_old()
        return tx_id

    def _cleanup_old(self, keep=10):
        zips = sorted([f for f in os.listdir(self.history_dir) if f.startswith('tx_') and f.endswith('.zip')])
        for old_zip in zips[:-keep]:
            try: os.remove(os.path.join(self.history_dir, old_zip))
            except: pass

    def undo_last(self):
        zips = sorted([f for f in os.listdir(self.history_dir) if f.startswith('tx_') and f.endswith('.zip')])
        if not zips:
            print(f"{Colors.YELLOW}Нет сохраненных транзакций для отмены.{Colors.RESET}")
            return False
        last_zip = zips[-1]
        zip_path = os.path.join(self.history_dir, last_zip)
        print(f"{Colors.YELLOW}Откат последней транзакции из {last_zip}...{Colors.RESET}")
        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                zf.extractall('.')
            os.remove(zip_path)
            print(f"{Colors.GREEN}Откат успешно завершен.{Colors.RESET}")
            return True
        except Exception as e:
            print(f"{Colors.RED}Ошибка при откате: {e}{Colors.RESET}")
            return False


def run_dry_linter(path, content):
    """Компилирует Python код в памяти. Если есть SyntaxError - возвращает False."""
    if path.endswith('.py'):
        try:
            ast.parse(content)
        except SyntaxError as e:
            return False, f"Синтаксическая ошибка (строка {e.lineno}): {e.msg}"
    return True, ""


DB_PATH = '.deepsearch.db'
SIZE_THRESHOLD = 500 * 1024

class CodeIndexer:
    def __init__(self, root_dir='.'):
        self.root_dir = root_dir
        self.db_path = os.path.join(root_dir, DB_PATH)
        self.db = sqlite3.connect(self.db_path, check_same_thread=False)
        self._init_db()

    def _init_db(self):
        self.db.executescript('''
            PRAGMA journal_mode = WAL;
            PRAGMA synchronous = NORMAL;
            PRAGMA mmap_size = 3000000000;

            CREATE TABLE IF NOT EXISTS files_meta (
                path TEXT PRIMARY KEY,
                size INTEGER,
                mtime REAL,
                is_binary INTEGER,
                encoding TEXT,
                newlines TEXT
            );

            CREATE TABLE IF NOT EXISTS file_content (
                path TEXT PRIMARY KEY,
                content TEXT
            );

            CREATE TABLE IF NOT EXISTS applied_patches (
                patch_hash TEXT PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        ''')
        # Пытаемся использовать сверхбыстрый триграммный поиск, если не выйдет - откатываемся на базовый
        try:
            self.db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS fts_code USING fts5(path, content, tokenize='trigram');")
        except sqlite3.OperationalError:
            self.db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS fts_code USING fts5(path, content, tokenize='unicode61');")
        self.db.commit()

    def get_patch_hash(self, search_lines, replace_lines):
        hasher = hashlib.md5()
        hasher.update("\n".join(search_lines).encode('utf-8'))
        hasher.update(b"|||")
        hasher.update("\n".join(replace_lines).encode('utf-8'))
        return hasher.hexdigest()

    def is_patch_applied(self, patch_hash):
        cur = self.db.execute("SELECT 1 FROM applied_patches WHERE patch_hash = ?", (patch_hash,))
        return cur.fetchone() is not None

    def mark_patch_applied(self, patch_hash):
        self.db.execute("INSERT OR IGNORE INTO applied_patches (patch_hash) VALUES (?)", (patch_hash,))
        self.db.commit()

    def fts_search(self, search_lines):
        # Извлекаем слова длиннее 4 символов для формирования FTS-запроса
        words = []
        for line in search_lines:
            words.extend(re.findall(r'[a-zA-Z_]{4,}', line))
        if not words:
            return None

        # Берем 3 самых длинных уникальных слова из блока кода (наивысшая энтропия)
        words = sorted(list(set(words)), key=len, reverse=True)[:3]
        query = " AND ".join(f'"{w}"' for w in words)
        try:
            cur = self.db.execute("SELECT path FROM fts_code WHERE content MATCH ? LIMIT 100", (query,))
            return set(row[0] for row in cur.fetchall())
        except sqlite3.OperationalError:
            return None

    def sync_and_read(self, ignore_folders, ignore_files, prompt_filenames):
        print(f"{Colors.YELLOW}Синхронизация инкрементального кэша SQLite...{Colors.RESET}")
        cur = self.db.execute("SELECT path, size, mtime, is_binary, encoding, newlines FROM files_meta")
        db_files = {row[0]: row for row in cur.fetchall()}

        current_files = _scan_directory_tree(self.root_dir, ignore_folders, ignore_files)

        file_contents = {}
        file_encodings = {}
        non_utf8_files = []
        code_file_contents = {}
        other_file_contents = {}

        # Удаление файлов из БД, которых больше нет на диске
        current_paths_set = set(current_files)
        paths_to_delete = [p for p in db_files if p not in current_paths_set]

        if paths_to_delete:
            for chunk in [paths_to_delete[i:i + 500] for i in range(0, len(paths_to_delete), 500)]:
                qs = ','.join('?' * len(chunk))
                self.db.execute(f"DELETE FROM files_meta WHERE path IN ({qs})", chunk)
                self.db.execute(f"DELETE FROM file_content WHERE path IN ({qs})", chunk)
                self.db.execute(f"DELETE FROM fts_code WHERE path IN ({qs})", chunk)

        updates = []
        fts_updates = []

        def process_file(file_path):
            try:
                stat = os.stat(file_path)
                size = stat.st_size
                mtime = stat.st_mtime
            except OSError:
                return None

            # Пропускаем огромные файлы и сам файл БД
            if size > 1 * 1024 * 1024 or file_path == DB_PATH or DB_PATH in file_path:
                return None

            db_record = db_files.get(file_path)
            is_bin = False
            enc = None
            newlines = '\n'
            content = None
            needs_read = True

            if db_record and db_record[1] == size and db_record[2] == mtime:
                is_bin = bool(db_record[3])
                enc = db_record[4]
                newlines = db_record[5]
                needs_read = False

            if needs_read:
                is_bin = is_binary_file(file_path)
                if not is_bin:
                    try:
                        with open(file_path, 'rb') as fp:
                            raw = fp.read()
                        has_bom = raw.startswith(b'\xef\xbb\xbf')
                        encodings = ['utf-8-sig'] if has_bom else ['utf-8']

                        read_success = False
                        for e in encodings:
                            try:
                                content = raw.decode(e)
                                enc = e
                                read_success = True
                                break
                            except UnicodeDecodeError:
                                pass

                        if not read_success and CHARSET_NORMALIZER_AVAILABLE and not has_bom:
                            res = charset_normalizer.detect(raw)
                            if res['encoding']:
                                try:
                                    content = raw.decode(res['encoding'])
                                    enc = res['encoding']
                                    read_success = True
                                except UnicodeDecodeError:
                                    pass

                        if read_success:
                            newlines = '\r\n' if '\r\n' in content else '\n'
                        else:
                            enc = None
                    except Exception:
                        is_bin = True
                        content = None

            return (file_path, size, mtime, is_bin, enc, newlines, content, needs_read)

        # Распараллеливаем чтение только для измененных файлов
        with concurrent.futures.ThreadPoolExecutor(max_workers=32) as executor:
            results = list(executor.map(process_file, current_files))

        for res in results:
            if not res: continue
            file_path, size, mtime, is_bin, enc, newlines, content, needs_read = res

            # Читаем контент из кэша, если файл не менялся
            if not needs_read:
                if not is_bin:
                    if size < SIZE_THRESHOLD:
                        cur = self.db.execute("SELECT content FROM file_content WHERE path = ?", (file_path,))
                        row = cur.fetchone()
                        if row:
                            content = row[0]
                        else:
                            try:
                                with open(file_path, 'r', encoding=enc) as fp:
                                    content = fp.read()
                            except: pass
                    else:
                        try:
                            with open(file_path, 'r', encoding=enc) as fp:
                                content = fp.read()
                        except: pass

            if needs_read:
                updates.append((file_path, size, mtime, int(is_bin), enc, newlines))
                if not is_bin and content is not None:
                    if size < SIZE_THRESHOLD:
                        self.db.execute("REPLACE INTO file_content (path, content) VALUES (?, ?)", (file_path, content))
                    self.db.execute("DELETE FROM fts_code WHERE path = ?", (file_path,))
                    fts_updates.append((file_path, content))

            if not is_bin:
                if content is not None:
                    lines = content.splitlines()
                    file_contents[file_path] = lines
                    file_encodings[file_path] = (enc, newlines)
                    ext = os.path.splitext(file_path)[1].lower()
                    if ext in CODE_EXTENSIONS or not ext:
                        code_file_contents[file_path] = lines
                    else:
                        other_file_contents[file_path] = lines
                else:
                    non_utf8_files.append(file_path)

        # Пакетная вставка (Batch insert) метаданных для максимальной скорости
        if updates:
            for chunk in [updates[i:i + 500] for i in range(0, len(updates), 500)]:
                self.db.executemany("REPLACE INTO files_meta (path, size, mtime, is_binary, encoding, newlines) VALUES (?, ?, ?, ?, ?, ?)", chunk)
        if fts_updates:
            for chunk in [fts_updates[i:i + 500] for i in range(0, len(fts_updates), 500)]:
                self.db.executemany("INSERT INTO fts_code (path, content) VALUES (?, ?)", chunk)

        self.db.commit()

        priority_files = set(prompt_filenames or set())
        def sort_key(item):
            p = item[0]
            is_prio = 1 if p in priority_files else 0
            return (-is_prio, len(item[1])) 

        code_file_contents = dict(sorted(code_file_contents.items(), key=sort_key))
        other_file_contents = dict(sorted(other_file_contents.items(), key=sort_key))

        return file_contents, file_encodings, non_utf8_files, code_file_contents, other_file_contents



def can_contain_block(search_lines, f_text):
    s_clean = [l.strip() for l in search_lines if len(l.strip()) > 8]
    if not s_clean:
        return True
    longest_lines = sorted(s_clean, key=len, reverse=True)[:3]
    for line in longest_lines:
        if line not in f_text:
            return False
    return True


def get_fuzzy_ratio(s1, s2):
    if RAPIDFUZZ_AVAILABLE:
        return fuzz.ratio(s1, s2) / 100.0
    return difflib.SequenceMatcher(None, s1, s2).ratio()


def detect_indent_style(lines):
    indents = []
    has_tabs = False
    for line in lines:
        if not line.strip():
            continue
        if line.startswith('\t'):
            has_tabs = True
        indents.append(len(line) - len(line.lstrip()))

    if has_tabs:
        return ('tab', 1)

    diffs = {}
    for i in range(1, len(indents)):
        diff = abs(indents[i] - indents[i - 1])
        if diff > 0:
            diffs[diff] = diffs.get(diff, 0) + 1

    if not diffs:
        return ('space', 4)  # Fallback

    most_common_diff = max(diffs.items(), key=lambda x: x[1])[0]
    return ('space', most_common_diff)

def adapt_indentation(replace_lines, source_style, target_style):
    if source_style == target_style:
        return replace_lines

    s_type, s_size = source_style
    t_type, t_size = target_style

    adapted = []
    for line in replace_lines:
        if not line.strip():
            adapted.append(line)
            continue

        indent_len = len(line) - len(line.lstrip())
        if indent_len == 0:
            adapted.append(line)
            continue

        levels = indent_len / s_size if s_type == 'space' else indent_len
        levels = int(round(levels))

        new_indent = ('\t' * levels) if t_type == 'tab' else (' ' * (levels * t_size))
        adapted.append(new_indent + line.lstrip())

    return adapted

def compute_mismatch_stats(search_lines, candidate_lines):
    search_text = "\n".join(l.strip() for l in search_lines)
    cand_text = "\n".join(l.strip() for l in candidate_lines)

    char_matcher = difflib.SequenceMatcher(None, search_text, cand_text)
    char_ratio = char_matcher.ratio()
    char_similarity_pct = char_ratio * 100
    char_mismatch_pct = 100.0 - char_similarity_pct

    total_chars = max(len(search_text), len(cand_text))
    char_matches = sum(triple.size for triple in char_matcher.get_matching_blocks())
    mismatched_chars = total_chars - char_matches

    s_norm = [l.strip() for l in search_lines]
    c_norm = [l.strip() for l in candidate_lines]
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

    s_norm = [l.strip() for l in s_clean]
    f_norm = [l.strip() for l in file_lines]

    line_index = {}
    for idx, line in enumerate(f_norm):
        if line not in line_index:
            line_index[line] = []
        line_index[line].append(idx)

    def get_anchor_indices(anchor, target_lines, start_index=0):
        matches = []
        a_len = len(anchor)
        if a_len == 0:
            return matches
        first_line = anchor[0]
        if first_line not in line_index:
            return matches
        for idx in line_index[first_line]:
            if idx >= start_index and idx <= len(target_lines) - a_len:
                if target_lines[idx:idx + a_len] == anchor:
                    matches.append(idx)
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
        matches = get_anchor_indices(top_anchor, f_norm)
        if not matches:
            break
        start_idx_matches = matches
        if len(start_idx_matches) == 1:
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
            matches = get_anchor_indices(bottom_anchor, f_norm, start_idx)
            if not matches:
                break
            end_idx_matches = matches
            if len(end_idx_matches) == 1:
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

    s_norm = [l.strip() for l in s_clean]
    f_norm = [l.strip() for l in file_lines]

    n_s = len(s_norm)
    n_f = len(f_norm)

    if n_s == 0:
        return []

    candidates = []
    s_text = "\n".join(s_norm)
    f_text_full = "\n".join(f_norm)

    if not can_contain_block(s_norm, f_text_full):
        return candidates

    # Если файл меньше искомого блока, просто сравниваем их целиком
    if n_f < n_s:
        ratio = get_fuzzy_ratio(s_text, f_text_full)
        if ratio >= max(threshold, 0.95):  # Строгий порог для коротких файлов
            candidates.append({'start': 0, 'end': len(file_lines), 'ratio': ratio * 0.98})
        return candidates

    dynamic_threshold = threshold if n_s >= 4 else 0.70

    # Ограничение для огромных файлов
    if n_f > 5000:
        return candidates

    i = 0
    if RAPIDFUZZ_AVAILABLE:
        while i <= n_f - n_s:
            window = f_norm[i:i + n_s]
            w_text = "\n".join(window)
            ratio = fuzz.ratio(s_text, w_text) / 100.0

            if ratio >= dynamic_threshold:
                actual_start = max(0, i - leading)
                actual_end = min(len(file_lines), i + n_s + trailing)
                candidates.append({'start': actual_start, 'end': actual_end, 'ratio': ratio * 0.98})
                i += n_s
            else:
                if ratio < 0.3 and n_s > 4:
                    i += max(1, n_s // 3)
                else:
                    i += 1
    else:
        matcher = difflib.SequenceMatcher()
        matcher.set_seq1(s_text)
        while i <= n_f - n_s:
            window = f_norm[i:i + n_s]
            w_text = "\n".join(window)
            matcher.set_seq2(w_text)
            q_ratio = matcher.quick_ratio()

            if q_ratio < dynamic_threshold * 0.8:
                if q_ratio < 0.3 and n_s > 4:
                    i += max(1, n_s // 3)
                else:
                    i += 1
                continue

            ratio = matcher.ratio()
            if ratio >= dynamic_threshold:
                actual_start = max(0, i - leading)
                actual_end = min(len(file_lines), i + n_s + trailing)
                candidates.append({'start': actual_start, 'end': actual_end, 'ratio': ratio * 0.98})
                i += n_s
            else:
                i += 1

    return candidates


def find_matches(search_lines, file_lines):
    matches = []
    n_s = len(search_lines)
    n_f = len(file_lines)

    if n_s == 0 or n_s > n_f:
        return matches

    s_text = "\n".join(l.rstrip() for l in search_lines)
    f_text = "\n".join(l.rstrip() for l in file_lines)

    idx = f_text.find(s_text)
    while idx != -1:
        start_line = f_text.count('\n', 0, idx)
        matches.append((start_line, start_line + n_s))
        idx = f_text.find(s_text, idx + len(s_text))

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
        try:
            stdout, stderr = process.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            print(f"{Colors.RED}PowerShell скрипт превысил лимит времени (15с) и был принудительно завершен.{Colors.RESET}")
            return False
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
    if not ignore_folders:
        ignore_folders = []
    if not ignore_files:
        ignore_files = []
    
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

    indexer = CodeIndexer('.')
    file_contents, file_encodings, non_utf8_files, code_file_contents, other_file_contents = indexer.sync_and_read(ignore_folders, ignore_files, prompt_filenames)

    # 2. Поиск совпадений
    block_matches = []
    already_applied_blocks = {}
    user_skipped_blocks = set()

    def get_candidates(search_lines, target_files, use_fts=True):
        cands = []

        allowed_paths = None
        if use_fts:
            rg_paths = ripgrep_search(search_lines, indexer.root_dir)
            if rg_paths is not None:
                allowed_paths = rg_paths
            else:
                allowed_paths = indexer.fts_search(search_lines)

        for path in target_files:
            if allowed_paths is not None and path not in allowed_paths:
                continue

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
        replace_lines = block['replace']
        matches_for_block = []

        patch_hash = indexer.get_patch_hash(search_lines, replace_lines)
        if indexer.is_patch_applied(patch_hash):
            print(f"\n{Colors.GREEN}Блок {idx + 1} уже был применен ранее (пропущен благодаря кэшу БД).{Colors.RESET}")
            block_matches.append([])
            continue

        # Поиск потенциальных кандидатов сначала в файлах кода с FTS-фильтрацией
        candidates = get_candidates(search_lines, code_file_contents, use_fts=True)

        if not candidates and other_file_contents:
            print(f"\n{Colors.YELLOW}Блок {idx + 1} не найден в файлах кода.{Colors.RESET}")
            ans = 'y' if AUTO_MODE else 'n'
            if not AUTO_MODE:
                try:
                    ans = input("Искать в остальных файлах (базы данных, логи и т.д.)? (y/n для отмены поиска блока): ").strip().lower()
                except EOFError:
                    ans = 'n'

            if ans in ['y', 'yes', 'да', '1']:
                candidates = get_candidates(search_lines, other_file_contents, use_fts=False)

        # Если FTS пропустил нужный файл, делаем резервный поиск без FTS
        if not candidates and code_file_contents:
            candidates = get_candidates(search_lines, code_file_contents, use_fts=False)

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
                candidates = get_candidates(search_lines, newly_read, use_fts=False)

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
                s_norm = [l.strip() for l in search_lines]
                c_norm = [l.strip() for l in candidate_lines]
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
                                line_diff_ratio = difflib.SequenceMatcher(None, s_line.strip(),
                                                                          c_line.strip()).ratio()
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
                    print(f"{Colors.GREEN}Auto-mode: Сходство высокое, блок подтвержден.{Colors.RESET}")
                elif stats['char_similarity_pct'] == 100.0 and stats['line_mismatch_pct'] == 0.0:
                    ans = 'y'
                    print(f"{Colors.GREEN}Сходство 100% (с учетом игнорирования отступов). Автоматическое подтверждение.{Colors.RESET}")
                else:
                    try:
                        ans = input("Подтвердить? y/n: ").strip().lower()
                    except EOFError:
                        ans = 'n'

                if ans in ['y', 'yes', 'да', '1']:
                    matches_for_block.append((path, start, end))
                    break
                else:
                    user_skipped_blocks.add(idx)

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
            if idx in user_skipped_blocks:
                print(f"{Colors.YELLOW}Блок {idx + 1} пропущен пользователем.{Colors.RESET}")
                continue

            if idx in already_applied_blocks:
                locs = already_applied_blocks[idx]
                if locs:
                    locs_str = "\n  - ".join([f"{p} (строки {s + 1}-{e})" for p, s, e in locs])
                    print(f"\n{Colors.YELLOW}ВНИМАНИЕ: Для блока {idx + 1} оригинал не найден, но точная копия текста ЗАМЕНЫ уже присутствует в коде:{Colors.RESET}")
                    print(f"  - {locs_str}")
                    try:
                        ans = input("Похоже, правки уже внесены. Пропустить этот блок? (y/n): ").strip().lower()
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

            target_style = detect_indent_style(lines)
            source_style = detect_indent_style(replace_lines)

            f_base_str = ""
            for j in range(min(len(search_lines), len(lines) - start)):
                if search_lines[j].strip() and lines[start + j].strip():
                    f_line = lines[start + j]
                    f_base_str = f_line[:len(f_line) - len(f_line.lstrip())]
                    break

            r_base_str = ""
            for r_line in replace_lines:
                if r_line.strip():
                    r_base_str = r_line[:len(r_line) - len(r_line.lstrip())]
                    break

            s_type, s_size = source_style
            t_type, t_size = target_style

            new_lines = []
            for r_line in replace_lines:
                if not r_line.strip():
                    new_lines.append("")
                    continue

                r_indent_str = r_line[:len(r_line) - len(r_line.lstrip())]

                # Вычисляем относительный отступ от базового (r_base_str)
                if r_indent_str.startswith(r_base_str):
                    relative_indent_len = len(r_indent_str) - len(r_base_str)
                else:
                    relative_indent_len = 0 # Fallback, если строка оказалась левее базы

                # Конвертируем относительный отступ под целевой стиль
                if s_type == 'space':
                    levels = relative_indent_len / s_size
                else:
                    levels = relative_indent_len

                levels = max(0, int(round(levels)))

                if t_type == 'tab':
                    scaled_relative_indent = '\t' * levels
                else:
                    scaled_relative_indent = ' ' * (levels * t_size)

                new_line = f_base_str + scaled_relative_indent + r_line.lstrip()
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

    # Второй проход: Машина Времени (бэкап) и Атомарная перезапись (Batch VFS)
    tm = TransactionManager()
    try:
        changed_paths = [d[0] for d in pending_replacements]
        tx_id = tm.backup_files(changed_paths)
        if tx_id:
            print(f"{Colors.GREEN}Создана резервная копия транзакции: tx_{tx_id}{Colors.RESET}")
    except Exception as e:
        print(f"{Colors.RED}Ошибка создания резервной копии: {e}{Colors.RESET}")
        return False

    def safe_atomic_write(file_data):
        path, lines, enc, newline_char = file_data
        content = newline_char.join(lines) + newline_char

        # Dry Run Linter: Проверка синтаксиса перед записью
        is_valid, err_msg = run_dry_linter(path, content)
        if not is_valid:
            raise ValueError(f"Синтаксическая ошибка в файле {path}:\n  {err_msg}")

        # Атомарная запись через .tmp файл
        tmp_path = path + ".tmp_write"
        with open(tmp_path, 'w', encoding=enc, newline='') as f:
            f.write(content)
        os.replace(tmp_path, path)
        return path

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=32) as executor:
            futures = [executor.submit(safe_atomic_write, d) for d in pending_replacements]
            for future in concurrent.futures.as_completed(futures):
                path = future.result()
                print(f"Обновлен файл: {Colors.YELLOW}{path}{Colors.RESET}")
                files_changed += 1

        # Маркируем успешные патчи как примененные в БД SQLite
        for idx, matches in enumerate(block_matches):
            if matches:
                p_hash = indexer.get_patch_hash(blocks[idx]['search'], blocks[idx]['replace'])
                indexer.mark_patch_applied(p_hash)
    except Exception as e:
        print(f"{Colors.RED}КРИТИЧЕСКАЯ ОШИБКА ПРИ ЗАПИСИ: {e}{Colors.RESET}")
        print(f"{Colors.YELLOW}Транзакция прервана! Выполняется откат изменений...{Colors.RESET}")
        tm.undo_last()
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
    if '--undo' in sys.argv:
        TransactionManager().undo_last()
        return

    while True:
        try:
            main(ignore_folders=ignore_folders, ignore_files=ignore_files)
        except KeyboardInterrupt:
            print(f"\n{Colors.YELLOW}Операция прервана пользователем.{Colors.RESET}")
            break


if __name__ == '__main__':
    run_replacer()
