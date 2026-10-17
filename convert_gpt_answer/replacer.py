import argparse
import ast
import atexit
import concurrent.futures
import ctypes
from ctypes import wintypes
from dataclasses import asdict, dataclass, field
import datetime
import difflib
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, Final, FrozenSet, List, Optional, Protocol, Set, Tuple, Union
import zipfile

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

try:
    import tkinter as tk
except ImportError:
    tk = None

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


# ============================================================================
# КОНСТАНТЫ И ЦВЕТА КОНСОЛИ
# ============================================================================

COUNT_TIME = True
DB_PATH = ".deepsearch.db"
HISTORY_DIR = ".deepsearch/history"
MANIFEST_FILENAME = "__deepsearch_manifest__.json"
SIZE_THRESHOLD = 500 * 1024
CLIPBOARD_LOCK_FILE = os.path.join(tempfile.gettempdir(), "deepsearch_replacer_clipboard.lock")
FEEDBACK_MARKER = "[DEEPSEARCH_AGENT_FEEDBACK]"
RG_AVAILABLE = shutil.which("rg") is not None


class Colors:
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    RESET = "\033[0m"


IGNORE_DIRS: Set[str] = {
    ".git", "node_modules", "venv", ".venv", "env",
    "__pycache__", ".idea", ".vscode", "build", "dist",
    "coverage", ".next", ".nuxt", "out", ".deepsearch"
}

TEXT_EXTENSIONS: Set[str] = {
    # Web, разметка, векторная графика и стили
    ".html", ".htm", ".xhtml", ".vue", ".svelte", ".astro",
    ".css", ".scss", ".sass", ".less", ".styl", ".stylus", ".postcss", ".pcss",
    ".svg", ".sgv", ".xml", ".xsd", ".xsl", ".xslt", ".xaml", ".wxml", ".wxss",

    # Научные документы, TeX, типографика
    ".tex", ".ltx", ".sty", ".cls", ".bib", ".dtx", ".ins", ".bbl", ".aux", ".toc",
    ".typ", ".lhs",

    # Документация, Markdown и плоский текст
    ".md", ".markdown", ".mdown", ".mdx", ".rst", ".adoc", ".asciidoc", ".org",
    ".txt", ".text", ".nfo", ".rtf", ".man", ".pod",

    # Языки программирования
    ".py", ".pyi", ".pyw", ".pyx", ".pxd",
    ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts", ".map",
    ".c", ".h", ".cpp", ".hpp", ".cc", ".hh", ".cxx", ".hxx", ".inl", ".ipp",
    ".cs", ".csx", ".fs", ".fsi", ".fsx", ".vb",
    ".go", ".rs", ".zig", ".nim", ".nims", ".v", ".d", ".di",
    ".java", ".kt", ".kts", ".scala", ".sc", ".groovy", ".gvy", ".gradle",
    ".swift", ".dart", ".m", ".mm",
    ".rb", ".rake", ".gemspec", ".podspec",
    ".php", ".phtml", ".php4", ".php5", ".php7",
    ".lua", ".pl", ".pm", ".tcl", ".tk",
    ".r", ".rmd", ".jl", ".sol",
    ".erl", ".hrl", ".ex", ".exs", ".hs", ".clj", ".cljs", ".cljc", ".edn",
    ".lisp", ".lsp", ".cl", ".el", ".rkt", ".scm", ".ss",
    ".pas", ".pp", ".inc", ".ada", ".adb", ".ads",
    ".asm", ".s", ".nasm",

    # Скрипты, командные оболочки
    ".sh", ".bash", ".zsh", ".fish", ".ksh", ".csh", ".tcsh", ".awk", ".sed",
    ".ps1", ".psm1", ".psd1", ".bat", ".cmd", ".vbs",

    # Конфигурации, сериализация, схемы данных
    ".json", ".json5", ".jsonc", ".jsonld", ".ndjson", ".geojson", ".topojson",
    ".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf", ".config", ".env",
    ".properties", ".prefs", ".plist",
    ".csv", ".tsv", ".tab",
    ".sql", ".prisma", ".proto", ".protobuf", ".graphql", ".gql",

    # Шаблонизаторы, сборка и инфраструктура
    ".jinja", ".jinja2", ".j2", ".twig", ".blade.php", ".erb", ".mustache",
    ".hbs", ".handlebars", ".liquid", ".ejs", ".njk", ".pug", ".jade", ".haml",
    ".dockerfile", ".containerfile", ".cmake", ".make", ".mk", ".ninja",
    ".tf", ".tfvars", ".hcl", ".nomad",
    ".diff", ".patch", ".log", ".reg", ".dot", ".gv", ".gcode"
}

CODE_EXTENSIONS: Set[str] = TEXT_EXTENSIONS

KNOWN_TEXT_FILENAMES: Set[str] = {
    "dockerfile", "containerfile", "makefile", "gnumakefile",
    "cmakelists.txt", "gemfile", "vagrantfile", "procfile",
    "rakefile", "jenkinsfile", "brewfile", "license", "copying",
    "readme", ".gitignore", ".gitattributes", ".gitmodules",
    ".editorconfig", ".prettierrc", ".eslintrc", ".babelrc",
    ".npmrc", ".yarnrc", ".env"
}

BINARY_EXTENSIONS: Set[str] = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".tiff", ".tif", ".psd", ".raw", ".heic", ".avif",
    ".mp3", ".wav", ".ogg", ".flac", ".aac", ".m4a", ".wma", ".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm",
    ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".zst", ".iso", ".dmg",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".obj", ".o", ".a", ".lib", ".out", ".node",
    ".pyc", ".pyo", ".pyd", ".class", ".dex", ".elc",
    ".ttf", ".otf", ".woff", ".woff2", ".eot",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".odp",
    ".db", ".sqlite", ".sqlite3", ".db3", ".mdb", ".accdb", ".parquet", ".orc", ".avro", ".dat",
    ".wasm", ".swf", ".lockb"
}


def is_known_text_file(filename_or_path: str) -> bool:
    name = os.path.basename(filename_or_path).lower()
    if name in KNOWN_TEXT_FILENAMES:
        return True
    ext = os.path.splitext(name)[1].lower()
    return ext in TEXT_EXTENSIONS


# ============================================================================
# РАЗДЕЛ БЕЗОПАСНОСТИ: ДОМЕННЫЕ ИСКЛЮЧЕНИЯ И ТИПЫ ДАННЫХ API
# ============================================================================

class PowerShellSecurityException(Exception):
    """Базовое исключение подсистемы безопасности PowerShell."""
    pass


class PathTraversalSecurityError(PowerShellSecurityException):
    """Попытка выхода за пределы доверенной директории проекта."""
    pass


class TranspilerSyntaxError(PowerShellSecurityException):
    """Невозможность безопасного сиксического разбора команды."""
    pass


class PolicyViolationError(PowerShellSecurityException):
    """Нарушение политики безопасности при исполнении."""
    pass


class JobObjectIsolationError(PowerShellSecurityException):
    """Сбой инициализации или связывания Win32 Job Object."""
    pass


class ExecutionStrategy(str, Enum):
    NATIVE_TRANSPILATION = "NATIVE_TRANSPILATION"
    ISOLATED_POWERSHELL = "ISOLATED_POWERSHELL"
    DIRECT_POWERSHELL = "DIRECT_POWERSHELL"
    REJECTED = "REJECTED"


class PowerShellLanguageMode(str, Enum):
    CONSTRAINED = "ConstrainedLanguage"
    FULL = "FullLanguage"


class FileWriteMode(str, Enum):
    OVERWRITE = "OVERWRITE"
    APPEND = "APPEND"
    MKDIR = "MKDIR"


class BlockStatus(str, Enum):
    APPLIED = "APPLIED"
    SKIPPED_CACHED = "SKIPPED_CACHED"
    SKIPPED_ALREADY_APPLIED = "SKIPPED_ALREADY_APPLIED"
    SKIPPED_USER = "SKIPPED_USER"
    FAILED_NOT_FOUND = "FAILED_NOT_FOUND"
    FAILED_LOW_SIMILARITY = "FAILED_LOW_SIMILARITY"
    FAILED_AMBIGUOUS = "FAILED_AMBIGUOUS"


@dataclass(frozen=True)
class TranspiledFileOperation:
    target_path: Path
    content: str
    encoding: str
    mode: FileWriteMode


@dataclass(frozen=True)
class TranspilationAssessment:
    is_transpilable: bool
    operation: Optional[TranspiledFileOperation]
    rejection_reason: Optional[str]
    raw_command: str


@dataclass(frozen=True)
class JobResourceLimits:
    memory_limit_bytes: int = 128 * 1024 * 1024  # 128 MB
    max_active_processes: int = 2
    timeout_seconds: float = 15.0


@dataclass(frozen=True)
class ScriptBlockPayload:
    command: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class CreateFileBlock:
    target_path_raw: str
    content: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class ScriptExecutionSummary:
    success: bool
    strategy: ExecutionStrategy
    exit_code: int
    duration_seconds: float
    stdout: str
    stderr: str
    error_message: Optional[str]
    target_path: Optional[str] = None
    was_new_file: bool = False


@dataclass
class BlockMatchReport:
    """Детальный отчет по одному блоку Search/Replace для API и обратной связи LLM."""
    block_index: int
    status: BlockStatus
    file_hint: Optional[str] = None
    target_files: List[str] = field(default_factory=list)
    similarity_pct: float = 0.0
    closest_file: Optional[str] = None
    closest_start_line: Optional[int] = None
    closest_end_line: Optional[int] = None
    actual_file_snippet: Optional[str] = None
    error_message: Optional[str] = None


@dataclass
class ReplacerExecutionReport:
    """Итоговый контракт выполнения Replacer (возвращается из API и сериализуется в JSON)."""
    success: bool
    action: str = "APPLY"  # "APPLY" | "UNDO" | "NOOP"
    transaction_id: Optional[str] = None
    modified_files: List[str] = field(default_factory=list)
    created_files: List[str] = field(default_factory=list)
    blocks_total: int = 0
    blocks_applied: int = 0
    ps_commands_total: int = 0
    ps_summaries: List[ScriptExecutionSummary] = field(default_factory=list)
    block_reports: List[BlockMatchReport] = field(default_factory=list)
    linter_errors: Dict[str, str] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def to_llm_feedback(self) -> str:
        """Формирует четкий диагностический промпт для самоисправления ИИ-агента."""
        if self.success:
            return (
                f"{FEEDBACK_MARKER}\n"
                f"[SUCCESS] Все операции успешно применены (tx: {self.transaction_id}).\n"
                f"Изменено файлов: {len(self.modified_files)} ({', '.join(self.modified_files) or 'нет'}).\n"
                f"Создано файлов: {len(self.created_files)} ({', '.join(self.created_files) or 'нет'})."
            )

        lines = [
            FEEDBACK_MARKER,
            "[TRANSACTION ABORTED] Правки НЕ были применены из-за следующих ошибок:"
        ]

        for ps_idx, ps_sum in enumerate(self.ps_summaries, 1):
            if not ps_sum.success:
                lines.append(
                    f"\n--- Ошибка команды PowerShell #{ps_idx} ({ps_sum.strategy.value}) ---\n"
                    f"Причина: {ps_sum.error_message or ps_sum.stderr}"
                )

        for br in self.block_reports:
            if br.status in (
                BlockStatus.FAILED_NOT_FOUND,
                BlockStatus.FAILED_LOW_SIMILARITY,
                BlockStatus.FAILED_AMBIGUOUS
            ):
                lines.append(f"\n--- Ошибка в блоке Search/Replace #{br.block_index} ({br.status.value}) ---")
                if br.error_message:
                    lines.append(br.error_message)
                if br.closest_file and br.actual_file_snippet is not None:
                    lines.append(
                        f"Наиболее близкий фрагмент найден в файле '{br.closest_file}' "
                        f"(строки {br.closest_start_line}-{br.closest_end_line}, сходство {br.similarity_pct:.1f}%):\n"
                        f"```\n{br.actual_file_snippet}\n```\n"
                        f"Используй ТОЧНЫЕ строки из актуального файла выше для формирования блока <<<< ==== >>>>."
                    )

        for fpath, l_err in self.linter_errors.items():
            lines.append(
                f"\n--- Синтаксическая ошибка после применения патча в файле '{fpath}' ---\n"
                f"{l_err}\n"
                f"Пожалуйста, проверь отступы, скобки и синтаксис в блоке замены."
            )

        if self.errors and not self.block_reports and not self.linter_errors:
            for err in self.errors:
                lines.append(f"- {err}")

        return "\n".join(lines)


# ============================================================================
# ЭТАП 1: ВАЛИДАТОР ПУТЕЙ И FAST-PATH ZERO-SHELL ТРАНСПИЛЯТОР
# ============================================================================

class SecurePathResolver:
    """Обеспечивает защиту от Path Traversal, NTFS ADS и системных устройств."""

    RESERVED_DEVICE_NAMES: Final[FrozenSet[str]] = frozenset({
        "con", "prn", "aux", "nul",
        "com1", "com2", "com3", "com4", "com5", "com6", "com7", "com8", "com9",
        "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9"
    })

    @staticmethod
    def resolve_project_path(raw_path: str, root_dir: Path) -> Path:
        if "\x00" in raw_path or "::" in raw_path:
            raise PathTraversalSecurityError(f"Обнаружены запрещенные символы в пути: {raw_path!r}")

        clean_path_str = raw_path.strip().strip("'\"")
        if not clean_path_str:
            raise PathTraversalSecurityError("Путь к целевому файлу не может быть пустым.")

        # Защита от NTFS Alternate Data Streams (например, file.txt:stream)
        normalized_slashes = clean_path_str.replace("\\", "/")
        for segment in normalized_slashes.split("/"):
            if ":" in segment and not (len(segment) == 2 and segment[0].isalpha() and segment[1] == ":"):
                raise PathTraversalSecurityError(f"Запрещено использование NTFS ADS в пути: {raw_path!r}")

        candidate_path = Path(clean_path_str)
        resolved_root = root_dir.resolve()

        # Защита от дублирования папки: если replacer запущен внутри проекта, а путь начинается с имени корневой папки
        if not candidate_path.is_absolute() and len(candidate_path.parts) > 1:
            if candidate_path.parts[0].lower() == resolved_root.name.lower():
                candidate_path = Path(*candidate_path.parts[1:])

        if candidate_path.is_absolute():
            resolved_target = candidate_path.resolve()
        else:
            resolved_target = (resolved_root / candidate_path).resolve()

        try:
            resolved_target.relative_to(resolved_root)
        except ValueError:
            raise PathTraversalSecurityError(
                f"Попытка выхода за пределы корня проекта: {raw_path!r} -> {resolved_target}"
            )

        for part in resolved_target.relative_to(resolved_root).parts:
            stem_lower = Path(part).stem.lower()
            if stem_lower in SecurePathResolver.RESERVED_DEVICE_NAMES:
                raise PathTraversalSecurityError(
                    f"Использование зарезервированного имени устройства Windows: {part!r}"
                )

        return resolved_target


class ZeroShellTranspiler:
    """Детерминированный транслятор типовых файловых операций PowerShell в нативный Python."""

    ENCODING_MAP: Final[Dict[str, str]] = {
        "utf8": "utf-8",
        "utf-8": "utf-8",
        "utf8nobom": "utf-8",
        "utf8bom": "utf-8-sig",
        "ascii": "ascii",
        "unicode": "utf-16le",
        "utf16": "utf-16le",
        "default": "utf-8"
    }

    HEREDOC_PATTERN: Final[re.Pattern[str]] = re.compile(
        r"^\s*\$(?:content|filecontent|data|text|body|code)\s*=\s*@(['\"])[ \t]*\r?\n(?P<content>[\s\S]*?)\r?\n[ \t]*\1@\s*$",
        re.MULTILINE | re.IGNORECASE
    )

    MKDIR_COMMAND_PATTERN: Final[re.Pattern[str]] = re.compile(
        r"^\s*(?:mkdir|md)\s+[\"']?(?P<path>[^\"'\r\n;]+)[\"']?\s*(?:-Force)?\s*;?\s*$",
        re.IGNORECASE
    )

    NEW_ITEM_DIR_PATTERN: Final[re.Pattern[str]] = re.compile(
        r"^\s*New-Item\s+(?:-(?:ItemType|Type)\s+(?:Directory|dir)\s+-(?:Path|Name)\s+[\"']?(?P<path1>[^\"'\s;]+)[\"']?|-(?:Path|Name)\s+[\"']?(?P<path2>[^\"'\s;]+)[\"']?\s+-(?:ItemType|Type)\s+(?:Directory|dir))\s*(?:-Force)?\s*(?:\|\s*Out-Null)?\s*;?\s*$",
        re.IGNORECASE
    )

    IO_DIR_CREATE_PATTERN: Final[re.Pattern[str]] = re.compile(
        r"^\s*\[System\.IO\.Directory\]::CreateDirectory\(\s*[\"'](?P<path>[^\"']+)[\"']\s*\)\s*(?:\|\s*Out-Null)?\s*;?\s*$",
        re.IGNORECASE
    )

    @classmethod
    def assess(cls, raw_script: str, root_dir: Path) -> TranspilationAssessment:
        normalized_script = raw_script.strip()

        # 1. Проверка на изолированные команды создания директорий
        mkdir_match = cls.MKDIR_COMMAND_PATTERN.match(normalized_script)
        new_item_match = cls.NEW_ITEM_DIR_PATTERN.match(normalized_script)
        io_dir_match = cls.IO_DIR_CREATE_PATTERN.match(normalized_script)

        raw_dir_path: Optional[str] = None
        if mkdir_match:
            raw_dir_path = mkdir_match.group("path")
        elif new_item_match:
            raw_dir_path = new_item_match.group("path1") or new_item_match.group("path2")
        elif io_dir_match:
            raw_dir_path = io_dir_match.group("path")

        if raw_dir_path is not None:
            try:
                resolved_dir = SecurePathResolver.resolve_project_path(raw_dir_path, root_dir)
                return TranspilationAssessment(
                    is_transpilable=True,
                    operation=TranspiledFileOperation(
                        target_path=resolved_dir,
                        content="",
                        encoding="utf-8",
                        mode=FileWriteMode.MKDIR
                    ),
                    rejection_reason=None,
                    raw_command=raw_script
                )
            except PathTraversalSecurityError as err:
                return TranspilationAssessment(
                    is_transpilable=False,
                    operation=None,
                    rejection_reason=str(err),
                    raw_command=raw_script
                )

        # 2. Проверка на запись файла с Heredoc
        heredoc_match = cls.HEREDOC_PATTERN.search(normalized_script)
        if not heredoc_match:
            return TranspilationAssessment(
                is_transpilable=False,
                operation=None,
                rejection_reason="Отсутствует стандартная декларация $content = @'...'@ heredoc.",
                raw_command=raw_script
            )

        extracted_content = heredoc_match.group("content")
        script_without_heredoc = cls.HEREDOC_PATTERN.sub("", normalized_script).strip()

        # Сбор объявлений строковых переменных: $filePath = "..." или $path = "..."
        var_map: Dict[str, str] = {}
        for var_m in re.finditer(
            r"\$(?P<var>[a-zA-Z0-9_]+)\s*=\s*[\"'](?P<val>[^\"'\r\n]+)[\"']",
            script_without_heredoc
        ):
            var_map[var_m.group("var").lower()] = var_m.group("val")

        mode = FileWriteMode.OVERWRITE
        detected_path: Optional[str] = None
        detected_encoding = "utf-8"

        var_names_group = r"(?:content|filecontent|data|text|body|code)"
        io_write_re = re.compile(
            rf"\[System\.IO\.File\]::WriteAllText\(\s*(?:[\"'](?P<lit_path>[^\"']+)[\"']|\$(?P<var_path>[a-zA-Z0-9_]+))\s*,\s*\${var_names_group}(?:\s*,\s*(?P<enc>[^\)]+))?\s*\)",
            re.IGNORECASE
        )
        io_append_re = re.compile(
            rf"\[System\.IO\.File\]::AppendAllText\(\s*(?:[\"'](?P<lit_path>[^\"']+)[\"']|\$(?P<var_path>[a-zA-Z0-9_]+))\s*,\s*\${var_names_group}(?:\s*,\s*(?P<enc>[^\)]+))?\s*\)",
            re.IGNORECASE
        )
        pipe_re = re.compile(
            rf"^\s*\${var_names_group}\s*\|\s*(?:Out-File|Set-Content)\s+(?P<args>[^\r\n;]+)",
            re.MULTILINE | re.IGNORECASE
        )
        direct_re = re.compile(
            rf"^\s*(?:Out-File|Set-Content)\s+(?P<args>[^\r\n;]+)",
            re.MULTILINE | re.IGNORECASE
        )

        io_write_match = io_write_re.search(script_without_heredoc)
        io_append_match = io_append_re.search(script_without_heredoc)
        pipe_match = pipe_re.search(script_without_heredoc)
        direct_match = direct_re.search(script_without_heredoc)

        has_utf8_no_bom_helper = bool(
            re.search(r"utf8encoding(?:\s*\(\s*\$false\s*\)|\s+\$false|::new)", script_without_heredoc, re.IGNORECASE)
        )

        write_match = None
        matched_cmd_line = None
        enc_raw = None

        if io_write_match:
            write_match = io_write_match
            matched_cmd_line = io_write_match.group(0)
            if io_write_match.group("lit_path"):
                detected_path = io_write_match.group("lit_path")
            elif io_write_match.group("var_path"):
                vname = io_write_match.group("var_path").lower()
                detected_path = var_map.get(vname)
            enc_raw = io_write_match.groupdict().get("enc")
        elif io_append_match:
            write_match = io_append_match
            matched_cmd_line = io_append_match.group(0)
            mode = FileWriteMode.APPEND
            if io_append_match.group("lit_path"):
                detected_path = io_append_match.group("lit_path")
            elif io_append_match.group("var_path"):
                vname = io_append_match.group("var_path").lower()
                detected_path = var_map.get(vname)
            enc_raw = io_append_match.groupdict().get("enc")
        elif pipe_match or direct_match:
            chosen = pipe_match or direct_match
            write_match = chosen
            matched_cmd_line = chosen.group(0)
            args_str = chosen.group("args")

            if re.search(r"(?:^|\s)-Append\b", args_str, re.IGNORECASE):
                mode = FileWriteMode.APPEND

            enc_m = re.search(r"-Encoding\s+([\"']?[a-zA-Z0-9_-]+[\"']?)", args_str, re.IGNORECASE)
            if enc_m:
                enc_raw = enc_m.group(1)

            p_m = re.search(
                r"-(?:FilePath|Path|LiteralPath)\s+(?:[\"'](?P<lit_path>[^\"'\r\n;]+)[\"']|\$(?P<var_path>[a-zA-Z0-9_]+))",
                args_str,
                re.IGNORECASE
            )
            if not p_m:
                p_m = re.search(
                    r"(?:^|\s)(?:[\"'](?P<lit_path>[^\"'\r\n;]+)[\"']|\$(?P<var_path>(?!content|filecontent|data|text|body|code)[a-zA-Z0-9_]+))",
                    args_str,
                    re.IGNORECASE
                )

            if p_m:
                if p_m.group("lit_path"):
                    detected_path = p_m.group("lit_path")
                elif p_m.group("var_path"):
                    vname = p_m.group("var_path").lower()
                    detected_path = var_map.get(vname)

        if not write_match:
            return TranspilationAssessment(
                is_transpilable=False,
                operation=None,
                rejection_reason="Не найдена безопасная инструкция записи файла (WriteAllText / AppendAllText / Out-File / Set-Content).",
                raw_command=raw_script
            )

        if not detected_path:
            return TranspilationAssessment(
                is_transpilable=False,
                operation=None,
                rejection_reason="Не удалось определить целевой путь файла или переменная пути не была определена.",
                raw_command=raw_script
            )

        if enc_raw:
            if "$utf8" in enc_raw.lower() or has_utf8_no_bom_helper:
                detected_encoding = "utf-8"
            else:
                detected_encoding = cls._normalize_encoding(enc_raw)

        # Валидация остаточных инструкций: удаление только безопасного файлового бойлерплейта
        remainder = script_without_heredoc.replace(matched_cmd_line, "") if matched_cmd_line else script_without_heredoc
        remainder = re.sub(
            r"\$[a-zA-Z0-9_]+\s*=\s*(?:[\"'][^\"'\r\n]*[\"']|(?:New-Object\s+System\.Text\.UTF8Encoding|\[System\.Text\.UTF8Encoding\]::new)(?:\s*\(\s*\$false\s*\)|\s+\$false)?|\[System\.IO\.Path\]::GetDirectoryName\([^\)]+\))\s*;?",
            "",
            remainder,
            flags=re.IGNORECASE
        )
        remainder = re.sub(r"if\s*\([^)]+\)\s*\{[^}]*\}", "", remainder, flags=re.DOTALL | re.IGNORECASE)
        remainder = re.sub(r"\[System\.IO\.Directory\]::CreateDirectory\([^)]+\)\s*(?:\|\s*Out-Null)?\s*;?", "", remainder, flags=re.IGNORECASE)
        remainder = re.sub(r"New-Item\s+-(?:ItemType|Type)\s+(?:Directory|dir)[^;\r\n]*;?", "", remainder, flags=re.IGNORECASE)
        remainder = re.sub(r"\bOut-Null\b;?", "", remainder, flags=re.IGNORECASE)

        residual_tokens = [
            line.strip() for line in remainder.splitlines()
            if line.strip() and not line.strip().startswith("#") and line.strip() not in ("{", "}")
        ]

        if residual_tokens:
            return TranspilationAssessment(
                is_transpilable=False,
                operation=None,
                rejection_reason=f"Обнаружены сторонние инструкции вне операции записи файла: {residual_tokens}",
                raw_command=raw_script
            )

        if not detected_path:
            return TranspilationAssessment(
                is_transpilable=False,
                operation=None,
                rejection_reason="Не удалось определить целевой путь файла.",
                raw_command=raw_script
            )

        try:
            resolved_path = SecurePathResolver.resolve_project_path(detected_path, root_dir)
        except PathTraversalSecurityError as e:
            return TranspilationAssessment(
                is_transpilable=False,
                operation=None,
                rejection_reason=str(e),
                raw_command=raw_script
            )

        op = TranspiledFileOperation(
            target_path=resolved_path,
            content=extracted_content,
            encoding=detected_encoding,
            mode=mode
        )

        return TranspilationAssessment(
            is_transpilable=True,
            operation=op,
            rejection_reason=None,
            raw_command=raw_script
        )

    @classmethod
    def _normalize_encoding(cls, raw_enc: str) -> str:
        clean = raw_enc.strip().strip("'\"").lower()
        if "utf8" in clean or "utf-8" in clean:
            return "utf-8"
        return cls.ENCODING_MAP.get(clean, "utf-8")

    @classmethod
    def execute_operation(cls, operation: TranspiledFileOperation) -> None:
        """Атомарная запись данных или создание папок на диске."""
        target = operation.target_path

        if operation.mode == FileWriteMode.MKDIR:
            target.mkdir(parents=True, exist_ok=True)
            return

        target.parent.mkdir(parents=True, exist_ok=True)

        content_to_write = operation.content
        if content_to_write and not content_to_write.endswith("\n"):
            content_to_write += "\n"

        if operation.mode == FileWriteMode.APPEND and target.exists():
            with open(target, "a", encoding=operation.encoding, newline="") as f:
                f.write(content_to_write)
            return

        tmp_file = target.with_name(f"{target.name}.{secrets.token_hex(4)}.tmp_write")
        try:
            with open(tmp_file, "w", encoding=operation.encoding, newline="") as f:
                f.write(content_to_write)
            os.replace(tmp_file, target)
        except Exception:
            if tmp_file.exists():
                try:
                    os.remove(tmp_file)
                except OSError:
                    pass
            raise


# ============================================================================
# ЭТАП 2: WIN32 C-STRUCTS, KERNEL JOB OBJECTS И TRUSTED RUNTIME RESOLVER
# ============================================================================

if sys.platform == "win32":
    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryLimit", ctypes.c_size_t),
            ("PeakJobMemoryLimit", ctypes.c_size_t),
        ]

    _kernel32 = ctypes.windll.kernel32

    _kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE

    _kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD
    ]
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL

    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL

    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL

    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE

    _kernel32.GetLastError.argtypes = []
    _kernel32.GetLastError.restype = wintypes.DWORD

    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: Final[int] = 0x2000
    JOB_OBJECT_LIMIT_ACTIVE_PROCESS: Final[int] = 0x0008
    JOB_OBJECT_LIMIT_PROCESS_MEMORY: Final[int] = 0x0100
    JobObjectExtendedLimitInformation: Final[int] = 9
    PROCESS_SET_QUOTA: Final[int] = 0x0100
    PROCESS_TERMINATE: Final[int] = 0x0001


class IProcessIsolationManager(Protocol):
    def assign_process(self, process_id: int) -> None: ...
    def close(self) -> None: ...


class Win32JobObjectManager:
    """Обеспечивает изоляцию процесса через Win32 Job Object на уровне ядра Windows."""

    def __init__(self, limits: JobResourceLimits) -> None:
        self._limits: Final[JobResourceLimits] = limits
        self._job_handle: Optional[wintypes.HANDLE] = None
        self._is_closed: bool = False
        self._init_job()

    def _init_job(self) -> None:
        if sys.platform != "win32":
            return

        h_job = _kernel32.CreateJobObjectW(None, None)
        if not h_job:
            err = _kernel32.GetLastError()
            raise JobObjectIsolationError(f"Не удалось создать Win32 Job Object (код ошибки: {err}).")

        self._job_handle = h_job

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limit_flags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

        if self._limits.max_active_processes > 0:
            limit_flags |= JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            info.BasicLimitInformation.ActiveProcessLimit = self._limits.max_active_processes

        if self._limits.memory_limit_bytes > 0:
            limit_flags |= JOB_OBJECT_LIMIT_PROCESS_MEMORY
            info.ProcessMemoryLimit = self._limits.memory_limit_bytes

        info.BasicLimitInformation.LimitFlags = limit_flags

        success = _kernel32.SetInformationJobObject(
            self._job_handle,
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info)
        )

        if not success:
            err = _kernel32.GetLastError()
            self.close()
            raise JobObjectIsolationError(f"Сбой конфигурации лимитов Job Object (код ошибки: {err}).")

    def assign_process(self, process_id: int) -> None:
        if sys.platform != "win32" or not self._job_handle:
            return

        h_proc = _kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, process_id)
        if not h_proc:
            err = _kernel32.GetLastError()
            raise JobObjectIsolationError(f"Не удалось открыть дескриптор процесса PID {process_id} (код: {err}).")

        try:
            success = _kernel32.AssignProcessToJobObject(self._job_handle, h_proc)
            if not success:
                err = _kernel32.GetLastError()
                raise JobObjectIsolationError(f"Сбой привязки процесса PID {process_id} к Job Object (код: {err}).")
        finally:
            _kernel32.CloseHandle(h_proc)

    def close(self) -> None:
        if self._job_handle and not self._is_closed:
            _kernel32.CloseHandle(self._job_handle)
            self._job_handle = None
            self._is_closed = True

    def __enter__(self) -> "Win32JobObjectManager":
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.close()


class PosixProcessGroupManager:
    """POSIX-реализация изоляции через группы процессов."""

    def __init__(self, limits: JobResourceLimits) -> None:
        self._limits: Final[JobResourceLimits] = limits
        self._target_pid: Optional[int] = None

    def assign_process(self, process_id: int) -> None:
        self._target_pid = process_id

    def close(self) -> None:
        if self._target_pid and self._target_pid > 0:
            try:
                pgid = os.getpgid(self._target_pid)
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
            self._target_pid = None

    def __enter__(self) -> "PosixProcessGroupManager":
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.close()


def create_process_isolation_manager(limits: JobResourceLimits) -> IProcessIsolationManager:
    if sys.platform == "win32":
        return Win32JobObjectManager(limits)
    return PosixProcessGroupManager(limits)


class TrustedRuntimeResolver:
    """Безопасный поиск доверенного исполняемого файла PowerShell с защитой от Binary Hijacking."""

    @staticmethod
    def resolve_powershell_executable() -> Path:
        if sys.platform != "win32":
            trusted_posix_paths = [
                Path("/usr/bin/pwsh"),
                Path("/usr/local/bin/pwsh"),
                Path("/opt/microsoft/powershell/7/pwsh"),
                Path("/bin/pwsh")
            ]
            for candidate in trusted_posix_paths:
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    return candidate
            raise PowerShellSecurityException("PowerShell (pwsh) не обнаружен в доверенных системных каталогах POSIX.")

        system_root = os.environ.get("SystemRoot")
        if not system_root:
            raise PowerShellSecurityException("Критическая переменная окружения SystemRoot не определена.")

        program_files_list = [
            os.environ.get("ProgramFiles", "C:\\Program Files"),
            os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")
        ]
        for pf in program_files_list:
            candidate = Path(pf) / "PowerShell" / "7" / "pwsh.exe"
            if candidate.is_file():
                return candidate

        system32_ps = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        if system32_ps.is_file():
            return system32_ps

        raise PowerShellSecurityException(
            "Доверенный исполняемый файл PowerShell не найден в системных каталогах Windows. Запуск через PATH запрещен."
        )


# ============================================================================
# ЭТАП 3: ЛЕКСИЧЕСКИЙ АУДИТ И ZERO-TRUST ШЛЮЗ
# ============================================================================

@dataclass(frozen=True)
class SecurityAuditReport:
    is_safe: bool
    detected_violations: List[str]
    script_sha256: str
    detected_tokens: List[str]


@dataclass(frozen=True)
class SlowPathExecutionResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float


class StaticScriptAuditor:
    """Статический анализатор токенов и синтаксических конструкций PowerShell."""

    FORBIDDEN_CMDLETS: Final[FrozenSet[str]] = frozenset({
        "invoke-expression", "iex",
        "invoke-webrequest", "iwr", "wget", "curl",
        "invoke-restmethod", "irm",
        "start-process", "saps",
        "start-job",
        "new-service",
        "set-itemproperty", "remove-itemproperty", "clear-itemproperty",
        "add-type", "invoke-wmimethod", "invoke-cimmethod"
    })

    FORBIDDEN_PATTERNS: Final[List[Tuple[re.Pattern[str], str]]] = [
        (re.compile(r"\[\s*system\.net\.webclient\s*\]", re.IGNORECASE), "[System.Net.WebClient]"),
        (re.compile(r"\[\s*system\.reflection\.", re.IGNORECASE), "[System.Reflection]"),
        (re.compile(r"\[\s*system\.runtime\.interopservices\.", re.IGNORECASE), "[System.Runtime.InteropServices]"),
        (re.compile(r"\[\s*system\.diagnostics\.process\s*\]", re.IGNORECASE), "[System.Diagnostics.Process]"),
        (re.compile(r"\[\s*system\.io\.driveinfo\s*\]", re.IGNORECASE), "[System.IO.DriveInfo]"),
        (re.compile(r"\bdownloadstring\s*\(", re.IGNORECASE), ".DownloadString()"),
        (re.compile(r"\bdownloadfile\s*\(", re.IGNORECASE), ".DownloadFile()"),
        (re.compile(r"\bdownloaddata\s*\(", re.IGNORECASE), ".DownloadData()"),
        (re.compile(r"\$executioncontext\b", re.IGNORECASE), "$ExecutionContext manipulation"),
        (re.compile(r"\$env:comspec\b", re.IGNORECASE), "$env:comspec invocation"),
        (re.compile(r"\bencodedcommand\b", re.IGNORECASE), "-EncodedCommand parameter"),
        (re.compile(r"&\s*\(\s*[\"']", re.IGNORECASE), "Dynamic call operator obfuscation &(...)"),
    ]

    @classmethod
    def audit(cls, raw_script: str) -> SecurityAuditReport:
        script_sha256 = hashlib.sha256(raw_script.encode("utf-8", errors="replace")).hexdigest()

        clean_script = re.sub(r"<#[\s\S]*?#>", "", raw_script)
        clean_script = re.sub(r"#[^\r\n]*", "", clean_script)
        deobfuscated = clean_script.replace("`", "")

        violations: List[str] = []
        detected_tokens: List[str] = []

        tokens = re.findall(r"\b[a-zA-Z][a-zA-Z0-9_-]*\b", deobfuscated)
        for token in tokens:
            token_lower = token.lower()
            if token_lower in cls.FORBIDDEN_CMDLETS:
                msg = f"Запрещенная команда или алиас: {token!r}"
                violations.append(msg)
                detected_tokens.append(token)

        for pattern, description in cls.FORBIDDEN_PATTERNS:
            if pattern.search(deobfuscated):
                violations.append(f"Обнаружена опасная операция: {description}")
                detected_tokens.append(description)

        is_safe = len(violations) == 0
        return SecurityAuditReport(
            is_safe=is_safe,
            detected_violations=violations,
            script_sha256=script_sha256,
            detected_tokens=detected_tokens
        )


class ZeroTrustGatekeeper:
    """Шлюз авторизации выполнения императивных скриптов."""

    @staticmethod
    def verify_execution_permission(
        script: str,
        audit_report: SecurityAuditReport,
        is_auto: bool,
        allow_auto: bool = False,
        quiet: bool = False
    ) -> bool:
        if not audit_report.is_safe:
            if not quiet:
                print(f"\n{Colors.RED}{'=' * 60}")
                print("БЛОКИРОВКА БЕЗОПАСНОСТИ: В скрипте обнаружены запрещенные операции:")
                for v in audit_report.detected_violations:
                    print(f"  - {v}")
                print(f"{'=' * 60}{Colors.RESET}")
            raise PolicyViolationError(
                f"Скрипт отклонен из-за обнаружения запрещенных инструкций: {audit_report.detected_violations}"
            )

        if is_auto:
            if allow_auto:
                return True
            if not quiet:
                print(f"\n{Colors.RED}{'=' * 60}")
                print("ОТКАЗ В АВТО-ИСПОЛНЕНИИ:")
                print("Выполнение императивного скрипта PowerShell в авто-режиме запрещено политикой безопасности.")
                print(f"SHA-256: {audit_report.script_sha256}")
                print(f"{'=' * 60}{Colors.RESET}")
            raise PolicyViolationError(
                "Автоматическое выполнение скриптов PowerShell заблокировано политикой безопасности Zero-Trust."
            )

        if not quiet:
            print(f"\n{Colors.YELLOW}{'=' * 60}")
            print("ТРЕБУЕТСЯ РУЧНОЕ ПОДТВЕРЖДЕНИЕ ВЫПОЛНЕНИЯ POWERSHELL:")
            print(f"Хэш SHA-256: {audit_report.script_sha256}")
            print("Статус аудита: Пройден (опасных токенов не обнаружено)")
            print("Режим изоляции: ConstrainedLanguage + Win32 Job Object")
            print(f"{'-' * 60}{Colors.RESET}")
            print(script.strip()[:1000] + ("\n... [обрезано]" if len(script.strip()) > 1000 else ""))
            print(f"{Colors.YELLOW}{'=' * 60}{Colors.RESET}")

        try:
            answer = input("Вы подтверждаете выполнение данного скрипта в изолированной песочнице? [y/N]: ").strip().lower()
            return answer in ["y", "yes", "да", "1"]
        except EOFError:
            return False


class SandboxedPowerShellRunner:
    """Изолированный исполнитель PowerShell (Slow-Path) с контролем ресурсов и очисткой."""

    SANDBOX_DIR_NAME: Final[str] = ".deepsearch/sandbox"

    @classmethod
    def execute(
        cls,
        script: str,
        root_dir: Path,
        limits: Optional[JobResourceLimits] = None
    ) -> SlowPathExecutionResult:
        if limits is None:
            limits = JobResourceLimits()

        executable_path = TrustedRuntimeResolver.resolve_powershell_executable()
        sandbox_dir = root_dir / cls.SANDBOX_DIR_NAME
        sandbox_dir.mkdir(parents=True, exist_ok=True)

        ephemeral_file = sandbox_dir / f"ps_sandbox_{secrets.token_hex(8)}.ps1"

        hardened_script = (
            "$ExecutionContext.SessionState.LanguageMode = 'ConstrainedLanguage'\r\n"
            + script
        )

        with open(ephemeral_file, "w", encoding="utf-8-sig", newline="") as f:
            f.write(hardened_script)

        start_time = time.time()
        isolation_manager = create_process_isolation_manager(limits)

        creation_flags = 0
        preexec_fn = None
        if sys.platform == "win32":
            creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP | 0x08000000
        else:
            preexec_fn = os.setsid

        cmd = [
            str(executable_path),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy", "RemoteSigned",
            "-File", str(ephemeral_file.resolve())
        ]

        raw_stdout = b""
        raw_stderr = b""
        exit_code = -1

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(root_dir.resolve()),
                creationflags=creation_flags,
                preexec_fn=preexec_fn
            )

            try:
                isolation_manager.assign_process(process.pid)
            except JobObjectIsolationError as e:
                process.kill()
                process.communicate()
                raise e

            try:
                raw_stdout, raw_stderr = process.communicate(timeout=limits.timeout_seconds)
                exit_code = process.returncode
            except subprocess.TimeoutExpired:
                isolation_manager.close()
                process.kill()
                raw_stdout, raw_stderr = process.communicate()
                exit_code = -1
                raw_stderr += (
                    f"\n[SECURITY] Процесс превысил лимит времени ({limits.timeout_seconds}с) "
                    f"и был принудительно завершен."
                ).encode("utf-8")
        finally:
            isolation_manager.close()
            cls._secure_shred_file(ephemeral_file)

        duration = time.time() - start_time
        stdout_str = cls._decode_stream(raw_stdout)
        stderr_str = cls._decode_stream(raw_stderr)

        return SlowPathExecutionResult(
            exit_code=exit_code,
            stdout=stdout_str,
            stderr=stderr_str,
            duration_seconds=duration
        )

    @classmethod
    def _decode_stream(cls, raw: bytes) -> str:
        if not raw:
            return ""

        if sys.platform == "win32":
            try:
                oem_cp = f"cp{ctypes.windll.kernel32.GetOEMCP()}"
                return raw.decode(oem_cp)
            except Exception:
                pass

        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("latin-1", errors="replace")

    @classmethod
    def _secure_shred_file(cls, path: Path) -> None:
        if not path.exists():
            return
        try:
            file_size = path.stat().st_size
            if file_size > 0:
                with open(path, "wb") as f:
                    f.write(b"\x00" * file_size)
            path.unlink(missing_ok=True)
        except OSError:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


class DirectPowerShellRunner:
    """Прямой запуск PowerShell в рабочей директории проекта (только при явном флаге unsafe_direct_ps)."""

    @classmethod
    def execute(cls, script: str, root_dir: Path) -> SlowPathExecutionResult:
        executable_path = TrustedRuntimeResolver.resolve_powershell_executable()
        temp_dir = Path(tempfile.gettempdir())
        ephemeral_file = temp_dir / f"ps_direct_{secrets.token_hex(8)}.ps1"

        with open(ephemeral_file, "w", encoding="utf-8-sig", newline="") as f:
            f.write(script)

        start_time = time.time()
        cmd = [
            str(executable_path),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy", "Bypass",
            "-File", str(ephemeral_file.resolve())
        ]

        raw_stdout = b""
        raw_stderr = b""
        exit_code = -1
        creation_flags = 0x08000000 if sys.platform == "win32" else 0

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(root_dir.resolve()),
                creationflags=creation_flags
            )
            raw_stdout, raw_stderr = process.communicate(timeout=30.0)
            exit_code = process.returncode
        except Exception as e:
            raw_stderr = str(e).encode("utf-8")
            exit_code = 1
        finally:
            if ephemeral_file.exists():
                try:
                    ephemeral_file.unlink(missing_ok=True)
                except OSError:
                    pass

        duration = time.time() - start_time
        stdout_str = SandboxedPowerShellRunner._decode_stream(raw_stdout)
        stderr_str = SandboxedPowerShellRunner._decode_stream(raw_stderr)

        return SlowPathExecutionResult(
            exit_code=exit_code,
            stdout=stdout_str,
            stderr=stderr_str,
            duration_seconds=duration
        )


# ============================================================================
# ЭТАП 4: ИСПРАВЛЕННЫЙ ФАСАД ОРКЕСТРАЦИИ POWERSHELL
# ============================================================================

def run_dry_linter(path: str, content: str) -> Tuple[bool, str]:
    """Проверка синтаксиса перед записью или коммитом транзакции."""
    lower_path = path.lower()
    if lower_path.endswith(".py"):
        try:
            ast.parse(content)
        except SyntaxError as e:
            return False, f"Синтаксическая ошибка Python (строка {e.lineno}): {e.msg}"
    elif lower_path.endswith(".json"):
        try:
            json.loads(content)
        except json.JSONDecodeError as e:
            return False, f"Ошибка синтаксиса JSON (строка {e.lineno}, колонка {e.colno}): {e.msg}"
    return True, ""


class PowerShellSecurityOrchestrator:
    """Единая точка входа для безопасной диспетчеризации PowerShell-скриптов."""

    @classmethod
    def dispatch(
        cls,
        raw_script: str,
        root_dir: Path,
        tm: "TransactionManager",
        indexer: Optional["CodeIndexer"] = None,
        is_auto: bool = False,
        disable_all_except_write: bool = True,
        unsafe_direct_ps: bool = False,
        quiet: bool = False
    ) -> ScriptExecutionSummary:
        # Эшелон 1: Детерминированный Fast-Path (нативная запись файлов и создание папок)
        assessment = ZeroShellTranspiler.assess(raw_script, root_dir)
        if assessment.is_transpilable and assessment.operation is not None:
            start_time = time.time()
            target_path = assessment.operation.target_path
            was_new = not target_path.exists()

            # Проверка синтаксиса записываемого файла ДО записи на диск
            if assessment.operation.mode == FileWriteMode.OVERWRITE:
                is_valid, lint_err = run_dry_linter(str(target_path), assessment.operation.content)
                if not is_valid:
                    return ScriptExecutionSummary(
                        success=False,
                        strategy=ExecutionStrategy.NATIVE_TRANSPILATION,
                        exit_code=1,
                        duration_seconds=time.time() - start_time,
                        stdout="",
                        stderr=lint_err,
                        error_message=f"Проверка линтера не пройдена для {target_path.name}: {lint_err}",
                        target_path=str(target_path),
                        was_new_file=was_new
                    )

            try:
                if assessment.operation.mode != FileWriteMode.MKDIR:
                    tm.backup_files([str(target_path)])

                ZeroShellTranspiler.execute_operation(assessment.operation)
                elapsed = time.time() - start_time

                script_hash = hashlib.sha256(raw_script.encode("utf-8", errors="replace")).hexdigest()
                if indexer is not None:
                    indexer.record_powershell_audit(
                        script_hash=script_hash,
                        strategy=ExecutionStrategy.NATIVE_TRANSPILATION.value,
                        exit_code=0,
                        duration_seconds=elapsed
                    )

                op_desc = (
                    "Папка успешно создана"
                    if assessment.operation.mode == FileWriteMode.MKDIR
                    else "Файл атомарно записан"
                )
                return ScriptExecutionSummary(
                    success=True,
                    strategy=ExecutionStrategy.NATIVE_TRANSPILATION,
                    exit_code=0,
                    duration_seconds=elapsed,
                    stdout=f"{op_desc} нативным транслятором: {target_path}",
                    stderr="",
                    error_message=None,
                    target_path=str(target_path),
                    was_new_file=was_new
                )
            except Exception as e:
                elapsed = time.time() - start_time
                return ScriptExecutionSummary(
                    success=False,
                    strategy=ExecutionStrategy.NATIVE_TRANSPILATION,
                    exit_code=1,
                    duration_seconds=elapsed,
                    stdout="",
                    stderr=str(e),
                    error_message=f"Ошибка нативной файловой операции: {e}",
                    target_path=str(target_path),
                    was_new_file=was_new
                )

        # Если скрипт не транспилируется в простую запись файла/создание папки:
        if disable_all_except_write:
            if is_auto:
                err_msg = (
                    f"ОТКАЗ (disable_all_except_write=True): Разрешены только операции записи файлов "
                    f"и создания папок в директории проекта. Причина отклонения Fast-Path: "
                    f"{assessment.rejection_reason or 'Императивный скрипт'}"
                )
                return ScriptExecutionSummary(
                    success=False,
                    strategy=ExecutionStrategy.REJECTED,
                    exit_code=1,
                    duration_seconds=0.0,
                    stdout="",
                    stderr=err_msg,
                    error_message=err_msg
                )

            if not quiet:
                print(f"\n{Colors.YELLOW}{'=' * 60}")
                print("ВНИМАНИЕ: Скрипт не является простой операцией записи файлов или создания папок.")
                print("Активно правило безопасности: disable_all_except_write = True.")
                print(f"Причина отклонения Fast-Path: {assessment.rejection_reason}")
                print(f"{'-' * 60}{Colors.RESET}")
                print(raw_script.strip()[:600] + ("\n... [обрезано]" if len(raw_script.strip()) > 600 else ""))
                print(f"{Colors.YELLOW}{'=' * 60}{Colors.RESET}")

            try:
                answer = input(
                    "Команда выходит за рамки записи файлов/создания папок. Продолжить проверку в песочнице? [y/N]: "
                ).strip().lower()
                if answer not in ["y", "yes", "да", "1"]:
                    return ScriptExecutionSummary(
                        success=False,
                        strategy=ExecutionStrategy.REJECTED,
                        exit_code=1,
                        duration_seconds=0.0,
                        stdout="",
                        stderr="Выполнение скрипта отклонено оператором (disable_all_except_write).",
                        error_message="Отклонено оператором (disable_all_except_write)."
                    )
            except EOFError:
                return ScriptExecutionSummary(
                    success=False,
                    strategy=ExecutionStrategy.REJECTED,
                    exit_code=1,
                    duration_seconds=0.0,
                    stdout="",
                    stderr="Прервано: отсутствие интерактивного ввода.",
                    error_message="Прервано: отсутствие ввода."
                )

        # Эшелон 2: Статический лексический аудит безопасности (ВСЕГДА выполняется!)
        audit_report = StaticScriptAuditor.audit(raw_script)

        # Эшелон 3: Zero-Trust шлюз авторизации
        try:
            is_permitted = ZeroTrustGatekeeper.verify_execution_permission(
                raw_script,
                audit_report,
                is_auto=is_auto,
                allow_auto=(not disable_all_except_write),
                quiet=quiet
            )
        except PolicyViolationError as e:
            return ScriptExecutionSummary(
                success=False,
                strategy=ExecutionStrategy.REJECTED,
                exit_code=1,
                duration_seconds=0.0,
                stdout="",
                stderr=str(e),
                error_message=str(e)
            )

        if not is_permitted:
            return ScriptExecutionSummary(
                success=False,
                strategy=ExecutionStrategy.REJECTED,
                exit_code=1,
                duration_seconds=0.0,
                stdout="",
                stderr="Исполнение PowerShell отклонено пользователем.",
                error_message="Отклонено оператором."
            )

        # Эшелон 4: Изолированный запуск в песочнице (или Direct только при явном unsafe_direct_ps)
        if unsafe_direct_ps:
            exec_result = DirectPowerShellRunner.execute(raw_script, root_dir)
            chosen_strategy = ExecutionStrategy.DIRECT_POWERSHELL
        else:
            exec_result = SandboxedPowerShellRunner.execute(raw_script, root_dir)
            chosen_strategy = ExecutionStrategy.ISOLATED_POWERSHELL

        is_success = exec_result.exit_code == 0

        if indexer is not None:
            indexer.record_powershell_audit(
                script_hash=audit_report.script_sha256,
                strategy=chosen_strategy.value,
                exit_code=exec_result.exit_code,
                duration_seconds=exec_result.duration_seconds
            )

        return ScriptExecutionSummary(
            success=is_success,
            strategy=chosen_strategy,
            exit_code=exec_result.exit_code,
            duration_seconds=exec_result.duration_seconds,
            stdout=exec_result.stdout,
            stderr=exec_result.stderr,
            error_message=None if is_success else f"Скрипт завершился с кодом ошибки {exec_result.exit_code}"
        )


# ============================================================================
# БУФЕР ОБМЕНА И ПРОЦЕССНЫЕ БЛОКИРОВКИ
# ============================================================================

def is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        kernel32 = ctypes.windll.kernel32
        h_proc = kernel32.OpenProcess(0x1000, False, pid)
        if h_proc:
            kernel32.CloseHandle(h_proc)
            return True
        err = kernel32.GetLastError()
        if err == 5:
            return True
        return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def check_active_clipboard_process() -> Optional[Dict[str, Any]]:
    if not os.path.exists(CLIPBOARD_LOCK_FILE):
        return None
    try:
        with open(CLIPBOARD_LOCK_FILE, "r", encoding="utf-8") as f:
            data = f.read().strip().split("|")
            if len(data) >= 2:
                pid = int(data[0])
                proj_dir = data[1]
                if pid != os.getpid() and is_pid_alive(pid):
                    return {"pid": pid, "dir": proj_dir}
        try:
            os.remove(CLIPBOARD_LOCK_FILE)
        except OSError:
            pass
        return None
    except Exception:
        return None


def acquire_clipboard_lock() -> bool:
    try:
        with open(CLIPBOARD_LOCK_FILE, "w", encoding="utf-8") as f:
            f.write(f"{os.getpid()}|{os.path.abspath('.')}")
        return True
    except Exception:
        return False


def release_clipboard_lock() -> None:
    try:
        if os.path.exists(CLIPBOARD_LOCK_FILE):
            with open(CLIPBOARD_LOCK_FILE, "r", encoding="utf-8") as f:
                data = f.read().strip().split("|")
                if len(data) >= 1 and int(data[0]) == os.getpid():
                    pass
                else:
                    return
            os.remove(CLIPBOARD_LOCK_FILE)
    except Exception:
        pass


def get_clipboard_content() -> str:
    if sys.platform == "win32":
        try:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            user32.OpenClipboard.argtypes = [wintypes.HWND]
            user32.OpenClipboard.restype = wintypes.BOOL
            user32.CloseClipboard.argtypes = []
            user32.CloseClipboard.restype = wintypes.BOOL
            user32.GetClipboardData.argtypes = [wintypes.UINT]
            user32.GetClipboardData.restype = wintypes.HANDLE
            user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
            user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
            kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
            kernel32.GlobalLock.restype = ctypes.c_void_p
            kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
            kernel32.GlobalUnlock.restype = wintypes.BOOL

            cf_unicodetext = 13

            for _ in range(5):
                if user32.OpenClipboard(None):
                    try:
                        if not user32.IsClipboardFormatAvailable(cf_unicodetext):
                            return ""
                        h_clip = user32.GetClipboardData(cf_unicodetext)
                        if not h_clip:
                            return ""
                        p_clip = kernel32.GlobalLock(h_clip)
                        if not p_clip:
                            return ""
                        try:
                            return ctypes.wstring_at(p_clip)
                        finally:
                            kernel32.GlobalUnlock(h_clip)
                    finally:
                        user32.CloseClipboard()
                time.sleep(0.02)
            return ""
        except Exception:
            return ""

    if tk is not None:
        try:
            root = tk.Tk()
            root.withdraw()
            text = root.clipboard_get()
            root.destroy()
            return text or ""
        except Exception:
            return ""
    return ""


def set_clipboard_content(text: str) -> bool:
    """Записывает текст в буфер обмена (для авто-возврата ошибки агенту)."""
    if tk is not None:
        try:
            root = tk.Tk()
            root.withdraw()
            root.clipboard_clear()
            root.clipboard_append(text)
            root.update()
            root.destroy()
            return True
        except Exception:
            pass
    if sys.platform == "win32":
        try:
            subprocess.run(["clip"], input=text.encode("utf-16le"), check=True, creationflags=0x08000000)
            return True
        except Exception:
            pass
    return False


def has_actionable_markers(text: str) -> bool:
    if not text or FEEDBACK_MARKER in text:
        return False
    if ",!!!" in text:
        return True
    start_tag = "<" * 4
    mid_tag = "=" * 4
    end_tag = ">" * 4
    if start_tag in text and mid_tag in text and end_tag in text:
        return True
    if start_tag in text and end_tag in text and re.search(r"create\s*:", text, re.IGNORECASE):
        return True
    if (
        "$content = @'" in text
        or "$content=@'" in text
        or '$content = @"' in text
        or "```powershell" in text.lower()
        or "```ps1" in text.lower()
        or "```ps" in text.lower()
        or re.search(r"^\s*(?:mkdir|md)\s+", text, re.MULTILINE | re.IGNORECASE)
        or re.search(r"^\s*New-Item\s+", text, re.MULTILINE | re.IGNORECASE)
        or "[System.IO." in text
    ):
        return True
    return False


# ============================================================================
# ПОИСКОВЫЕ УТИЛИТЫ, RIPGREP, АНАЛИЗ ОТСТУПОВ И ПАРСИНГ БЛОКОВ
# ============================================================================

def is_comment_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    return s.startswith(("//", "#", "/*", "*", "*/", "<!--", "-->", "--", ";", "%", "::", "REM ", "rem "))


def generate_whitespace_agnostic_regex(lines: List[str]) -> str:
    clean_lines = [l for l in lines if l.strip()]
    if not clean_lines:
        return ""
    parts = []
    for line in clean_lines:
        tokenized = re.sub(r"\s+", "\x00", line.strip())
        escaped = re.escape(tokenized)
        regex_line = escaped.replace("\x00", r"\s+")
        parts.append(regex_line)
    return r"\s+".join(parts)


def ripgrep_search(search_lines: List[str], root_dir: str = ".") -> Optional[Set[str]]:
    if not RG_AVAILABLE:
        return None
    pattern = generate_whitespace_agnostic_regex(search_lines)
    if not pattern:
        return None

    try:
        result = subprocess.run(["rg", "-U", "-l", pattern, root_dir], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            paths = set()
            for line in result.stdout.splitlines():
                paths.add(os.path.normpath(line.strip()))
            return paths
    except (subprocess.TimeoutExpired, Exception):
        pass

    code_lines = [l for l in search_lines if not is_comment_line(l) and l.strip()]
    if len(code_lines) >= 2 and len(code_lines) != len([l for l in search_lines if l.strip()]):
        code_pattern = generate_whitespace_agnostic_regex(code_lines)
        if code_pattern:
            try:
                result = subprocess.run(
                    ["rg", "-U", "-l", code_pattern, root_dir],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0:
                    paths = set()
                    for line in result.stdout.splitlines():
                        paths.add(os.path.normpath(line.strip()))
                    return paths
            except (subprocess.TimeoutExpired, Exception):
                pass
    return None


def rebase_indentation(
    matched_file_lines: List[str],
    search_lines: List[str],
    replace_lines: List[str]
) -> List[str]:
    """
    Автоматически корректирует базовый отступ блока замены, если LLM прислала блок
    со сбитым уровнем вложенности (например, 0 пробелов вместо 8).
    """
    if not replace_lines or not matched_file_lines or not search_lines:
        return replace_lines

    def get_first_code_indent(lines_list: List[str]) -> Optional[int]:
        for l in lines_list:
            if l.strip() and not is_comment_line(l):
                return len(l) - len(l.lstrip(" "))
        for l in lines_list:
            if l.strip():
                return len(l) - len(l.lstrip(" "))
        return None

    file_indent = get_first_code_indent(matched_file_lines)
    search_indent = get_first_code_indent(search_lines)

    if file_indent is None or search_indent is None:
        return replace_lines

    delta = file_indent - search_indent
    if delta == 0:
        return replace_lines

    rebased: List[str] = []
    for line in replace_lines:
        if not line.strip():
            rebased.append("")
        elif delta > 0:
            rebased.append((" " * delta) + line)
        else:
            remove_count = min(abs(delta), len(line) - len(line.lstrip(" ")))
            rebased.append(line[remove_count:])
    return rebased


def extract_filenames_from_prompt(lines: List[str]) -> Set[str]:
    filenames: Set[str] = set()
    pattern = re.compile(r"[@#*`'\"]?([a-zA-Z0-9_./\\-]+\.[a-zA-Z0-9]{1,10})")
    start_tag = "<" * 4
    end_tag = ">" * 4

    def is_candidate_filename(cand: str) -> bool:
        if not cand or "." not in cand or cand.endswith("."):
            return False
        ext = os.path.splitext(cand)[1].lower()
        if ext in BINARY_EXTENSIONS:
            return False
        if ext in TEXT_EXTENSIONS:
            return True
        ext_clean = ext.lstrip(".")
        return bool(ext_clean.isalnum() and not ext_clean.isdigit())

    for line in lines:
        stripped = line.strip()
        if stripped.startswith(start_tag) or stripped.startswith(end_tag):
            header_rem = stripped.lstrip("<>").strip()
            for match in pattern.findall(header_rem):
                clean = match.strip("@#*`'\":;, ")
                if is_candidate_filename(clean):
                    filenames.add(os.path.normpath(clean))
                    filenames.add(os.path.basename(clean))
            continue

        for match in pattern.findall(line):
            clean = match.strip("@#*`'\":;, ")
            if is_candidate_filename(clean):
                filenames.add(os.path.normpath(clean))
                filenames.add(os.path.basename(clean))

        words = re.findall(r"\b[a-zA-Z0-9_.-]+\b", stripped)
        for w in words:
            if w.lower() in KNOWN_TEXT_FILENAMES:
                filenames.add(os.path.normpath(w))
                filenames.add(os.path.basename(w))

    return filenames


def parse_patch_blocks(lines: List[str]) -> Union[str, List[Dict[str, Any]]]:
    for line in lines:
        if line.strip() == ",!!!":
            return "UNDO"

    blocks: List[Dict[str, Any]] = []
    state = 0
    current_search: List[str] = []
    current_replace: List[str] = []
    current_file_hint: Optional[str] = None

    start_marker = "<" * 4
    mid_marker = "=" * 4
    end_marker = ">" * 4
    file_pattern = re.compile(r"[@#*`'\"]?([a-zA-Z0-9_./\\-]+\.[a-zA-Z0-9]{1,10})")

    def extract_valid_filepath(text_line: str, check_extension: bool = True) -> Optional[str]:
        matches = file_pattern.findall(text_line)
        for m in reversed(matches):
            clean = m.strip("@#*`'\":;, ")
            if "." in clean and not clean.endswith("."):
                ext = os.path.splitext(clean)[1].lower()
                if ext in BINARY_EXTENSIONS:
                    continue
                if not check_extension or ext in TEXT_EXTENSIONS or (ext.lstrip(".").isalnum() and not ext.lstrip(".").isdigit()):
                    return os.path.normpath(clean)

        for w in reversed(re.findall(r"\b[a-zA-Z0-9_.-]+\b", text_line)):
            clean = w.strip("@#*`'\":;, ")
            if clean.lower() in KNOWN_TEXT_FILENAMES:
                return os.path.normpath(clean)

        return None

    def extract_file_hint_from_recent_lines(line_buffer: List[str]) -> Optional[str]:
        for l in reversed(line_buffer[-12:]):
            sl = l.strip()
            if sl.startswith(end_marker) or sl.startswith(mid_marker) or sl.startswith(start_marker):
                break
            found = extract_valid_filepath(sl)
            if found:
                return found
        return None

    def trim_lines_search(l: List[str]) -> List[str]:
        s, e = 0, len(l)
        while s < e and not l[s].strip():
            s += 1
        while e > s and not l[e - 1].strip():
            e -= 1
        return l[s:e]

    recent_lines: List[str] = []
    closing_marker = end_marker

    for line in lines:
        stripped = line.strip()

        is_start_normal = stripped.startswith(start_marker)
        is_start_inverted = stripped.startswith(end_marker)
        is_mid = stripped.startswith(mid_marker)

        if state == 0:
            if is_start_normal:
                if re.match(r"^" + re.escape(start_marker) + r"\s*create:?\s+", stripped, re.IGNORECASE):
                    state = 3
                    continue
                header_tail = stripped.lstrip("<").strip()
                inline_hint = extract_valid_filepath(header_tail) if header_tail else None
                current_file_hint = inline_hint or extract_file_hint_from_recent_lines(recent_lines)
                closing_marker = end_marker
                state = 1
                current_search = []
                current_replace = []
            elif is_start_inverted:
                header_tail = stripped.lstrip(">").strip()
                inline_hint = extract_valid_filepath(header_tail) if header_tail else None
                current_file_hint = inline_hint or extract_file_hint_from_recent_lines(recent_lines)
                closing_marker = start_marker
                state = 1
                current_search = []
                current_replace = []
            else:
                recent_lines.append(line)
        elif state == 1:
            if is_mid:
                state = 2
            elif stripped.startswith(closing_marker):
                state = 0
            elif not stripped.startswith(start_marker) and not stripped.startswith(end_marker):
                current_search.append(line.rstrip("\r\n"))
        elif state == 2:
            if stripped.startswith(closing_marker):
                blocks.append({
                    "search": trim_lines_search(current_search),
                    "replace": current_replace,
                    "file_hint": current_file_hint
                })
                state = 0
                current_file_hint = None
                recent_lines = []
            elif not is_mid and not stripped.startswith(start_marker) and not stripped.startswith(end_marker):
                current_replace.append(line.rstrip("\r\n"))
        elif state == 3:
            if re.match(r"^" + re.escape(start_marker) + r"\s*create:?\s+", stripped, re.IGNORECASE):
                state = 3
            elif stripped.startswith(end_marker):
                state = 0

    if state == 2:
        blocks.append({
            "search": trim_lines_search(current_search),
            "replace": current_replace,
            "file_hint": current_file_hint
        })

    return blocks


def extract_create_blocks(lines: List[str]) -> List[CreateFileBlock]:
    create_pattern = re.compile(
        r"^" + re.escape("<" * 4) + r"+\s*create:?\s*(?P<path>[^\r\n]+)$",
        re.IGNORECASE
    )
    end_marker = ">" * 4
    blocks: List[CreateFileBlock] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        m = create_pattern.match(stripped)
        if m:
            raw_path = m.group("path").strip().strip("'\"`")
            start_idx = i + 1
            i += 1
            content_lines: List[str] = []
            while i < len(lines):
                if lines[i].strip().startswith(end_marker):
                    break
                content_lines.append(lines[i])
                i += 1
            end_idx = i + 1

            ext = os.path.splitext(raw_path)[1].lower()
            clean_lines = content_lines[:]
            if len(clean_lines) >= 2 and ext not in [".md", ".markdown"]:
                if clean_lines[0].strip().startswith("```") and clean_lines[-1].strip() == "```":
                    clean_lines = clean_lines[1:-1]

            content = "".join(clean_lines)
            if content and not content.endswith("\n"):
                content += "\n"

            blocks.append(CreateFileBlock(
                target_path_raw=raw_path,
                content=content,
                start_line=start_idx,
                end_line=end_idx
            ))
        i += 1

    return blocks


def extract_powershell_commands(lines: List[str]) -> List[ScriptBlockPayload]:
    commands: List[ScriptBlockPayload] = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()

        if stripped.lower() in ["```powershell", "```ps1", "```ps"]:
            start_idx = i + 1
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                i += 1
            if i < len(lines):
                cmd = "".join(lines[start_idx:i])
                if cmd.strip():
                    commands.append(ScriptBlockPayload(command=cmd, start_line=start_idx + 1, end_line=i))

        elif (
            ZeroShellTranspiler.MKDIR_COMMAND_PATTERN.match(stripped)
            or ZeroShellTranspiler.NEW_ITEM_DIR_PATTERN.match(stripped)
            or ZeroShellTranspiler.IO_DIR_CREATE_PATTERN.match(stripped)
        ):
            commands.append(ScriptBlockPayload(command=lines[i], start_line=i + 1, end_line=i + 1))

        elif re.match(r"^\$(?:content|filecontent|data|text|body|code)\s*=\s*@(['\"])\s*$", stripped, re.IGNORECASE):
            quote_char = stripped[-1]
            closing_token = f"{quote_char}@"
            start_idx = i
            # Захватываем предшествующие строки с объявлением переменных и путей ($filePath = ...)
            while start_idx > 0:
                prev = lines[start_idx - 1].strip()
                if not prev:
                    if start_idx > 1 and re.match(r"^(\$[a-zA-Z0-9_]+\s*=|#|\[)", lines[start_idx - 2].strip()):
                        start_idx -= 1
                        continue
                    break
                if re.match(r"^(\$[a-zA-Z0-9_]+\s*=|#|\[|New-Item|mkdir\b|md\b)", prev, re.IGNORECASE):
                    start_idx -= 1
                else:
                    break

            i += 1
            # Ищем строго закрывающий токен '@ или "@ на отдельной строке
            while i < len(lines) and lines[i].strip() != closing_token:
                i += 1
            # После закрытия heredoc ищем команду записи в пределах следующих 15 строк
            search_limit = min(len(lines), i + 15)
            while i < search_limit and not any(
                kw in lines[i] for kw in ["Out-File", "Set-Content", "WriteAllText", "AppendAllText"]
            ):
                i += 1
            if i < search_limit:
                cmd = "".join(lines[start_idx:i + 1])
                commands.append(ScriptBlockPayload(command=cmd, start_line=start_idx + 1, end_line=i + 1))
        i += 1

    return commands


def parse_input() -> Tuple[Union[str, List[Dict[str, Any]]], List[str]]:
    print(f"{Colors.YELLOW}Введите текст с блоками правок.{Colors.RESET}")
    print(
        f"{Colors.YELLOW}Для подтверждения отправки введите {Colors.GREEN}.,,,{Colors.YELLOW} на новой пустой строке "
        f"и нажмите Enter (или {Colors.GREEN},!!!{Colors.YELLOW} для отката):{Colors.RESET}"
    )

    lines: List[str] = []
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            stripped = line.strip()
            if stripped == ",!!!":
                return "UNDO", lines
            if stripped == ".,,,":
                break
            lines.append(line)
        except EOFError:
            break

    blocks = parse_patch_blocks(lines)
    return blocks, lines


def is_binary_file(filepath: str) -> bool:
    name = os.path.basename(filepath).lower()
    ext = os.path.splitext(name)[1].lower()

    if ext in BINARY_EXTENSIONS:
        return True

    try:
        with open(filepath, "rb") as f:
            chunk = f.read(8192)
            if not chunk:
                return False

            if chunk.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff", b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
                return False

            if chunk.startswith((b"SQLite format 3\x00", b"\x7fELF", b"MZ", b"PK\x03\x04")):
                return True

            if b"\x00" in chunk:
                if len(chunk) >= 4:
                    if chunk[1::2].count(b"\x00") > len(chunk) // 4 and all(c != 0 for c in chunk[0::2][:20]):
                        return False
                    if chunk[0::2].count(b"\x00") > len(chunk) // 4 and all(c != 0 for c in chunk[1::2][:20]):
                        return False
                return True

            control_chars = sum(1 for byte in chunk if byte < 32 and byte not in (9, 10, 13, 8, 12))
            if control_chars / len(chunk) > 0.10:
                return True

            return False
    except (IOError, OSError):
        return True


def _scan_directory_tree(root_dir: str, folders_to_ignore: Set[str], files_to_ignore: Set[str]) -> List[str]:
    entries: List[str] = []
    lower_files_to_ignore = {f.lower() for f in files_to_ignore}
    try:
        with os.scandir(root_dir) as it:
            for entry in it:
                if entry.name in files_to_ignore or entry.name.lower() in lower_files_to_ignore:
                    continue
                if entry.is_dir():
                    if entry.name not in folders_to_ignore:
                        entries.extend(_scan_directory_tree(entry.path, folders_to_ignore, files_to_ignore))
                elif entry.is_file():
                    entries.append(entry.path)
    except OSError:
        pass
    return entries


# ============================================================================
# ТРАНЗАКЦИОННЫЙ МЕНЕДЖЕР (С МАНИФЕСТОМ, УДАЛЕНИЕМ НОВЫХ ФАЙЛОВ И ОТКАТОМ БД)
# ============================================================================

class TransactionManager:
    """Обеспечивает атомарный бэкап и полный откат (включая удаление созданных файлов и сброс кэша БД)."""

    def __init__(self, root_dir: str = ".") -> None:
        self.root_dir = os.path.abspath(root_dir)
        self.history_dir = os.path.join(self.root_dir, HISTORY_DIR)
        os.makedirs(self.history_dir, exist_ok=True)
        self._active_tx_id: Optional[str] = None
        self._active_zip_path: Optional[str] = None
        self._existing_files: List[str] = []
        self._created_files: List[str] = []
        self._applied_hashes: List[str] = []

    def begin_transaction(self) -> str:
        if self._active_tx_id is None:
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            self._active_tx_id = f"{ts}_{secrets.token_hex(3)}"
            self._active_zip_path = os.path.join(self.history_dir, f"tx_{self._active_tx_id}.zip")
            self._existing_files = []
            self._created_files = []
            self._applied_hashes = []
        return self._active_tx_id

    def backup_files(self, file_paths: List[str], patch_hashes: Optional[List[str]] = None) -> str:
        tx_id = self.begin_transaction()
        assert self._active_zip_path is not None

        if patch_hashes:
            for h in patch_hashes:
                if h not in self._applied_hashes:
                    self._applied_hashes.append(h)

        # Читаем уже сохраненные файлы из активного архива, если дописываем в ту же транзакцию
        existing_archive_blobs: Dict[str, bytes] = {}
        if os.path.exists(self._active_zip_path):
            try:
                with zipfile.ZipFile(self._active_zip_path, "r") as zf:
                    for name in zf.namelist():
                        if name != MANIFEST_FILENAME:
                            existing_archive_blobs[name] = zf.read(name)
            except Exception:
                pass

        for path in file_paths:
            abs_p = os.path.abspath(path)
            rel_path = os.path.relpath(abs_p, start=self.root_dir)
            if rel_path in self._existing_files or rel_path in self._created_files:
                continue

            if os.path.exists(abs_p):
                self._existing_files.append(rel_path)
                with open(abs_p, "rb") as f:
                    existing_archive_blobs[rel_path] = f.read()
            else:
                self._created_files.append(rel_path)

        manifest = {
            "tx_id": tx_id,
            "existing_files": self._existing_files,
            "created_files": self._created_files,
            "applied_hashes": self._applied_hashes,
        }

        with zipfile.ZipFile(self._active_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for arc_name, blob in existing_archive_blobs.items():
                zf.writestr(arc_name, blob)
            zf.writestr(MANIFEST_FILENAME, json.dumps(manifest, ensure_ascii=False, indent=2))

        self._cleanup_old()
        return tx_id

    def record_applied_hashes(self, patch_hashes: List[str]) -> None:
        if not patch_hashes or not self._active_zip_path or not os.path.exists(self._active_zip_path):
            return
        self.backup_files([], patch_hashes=patch_hashes)

    def _cleanup_old(self, keep: int = 15) -> None:
        try:
            zips = sorted([f for f in os.listdir(self.history_dir) if f.startswith("tx_") and f.endswith(".zip")])
            for old_zip in zips[:-keep]:
                try:
                    os.remove(os.path.join(self.history_dir, old_zip))
                except OSError:
                    pass
        except OSError:
            pass

    def undo_last(self, indexer: Optional["CodeIndexer"] = None, quiet: bool = False) -> bool:
        zips = sorted([f for f in os.listdir(self.history_dir) if f.startswith("tx_") and f.endswith(".zip")])
        if not zips:
            if not quiet:
                print(f"{Colors.YELLOW}Нет сохраненных транзакций для отмены.{Colors.RESET}")
            return False

        last_zip = zips[-1]
        zip_path = os.path.join(self.history_dir, last_zip)
        if not quiet:
            print(f"{Colors.YELLOW}Откат последней транзакции из {last_zip}...{Colors.RESET}")

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                namelist = zf.namelist()
                if MANIFEST_FILENAME in namelist:
                    manifest = json.loads(zf.read(MANIFEST_FILENAME).decode("utf-8"))
                    # 1. Восстанавливаем измененные файлы
                    for rel_path in manifest.get("existing_files", []):
                        if rel_path in namelist:
                            target_abs = os.path.join(self.root_dir, rel_path)
                            os.makedirs(os.path.dirname(target_abs), exist_ok=True)
                            with open(target_abs, "wb") as out_f:
                                out_f.write(zf.read(rel_path))
                    target_indexer = indexer or CodeIndexer(self.root_dir)
                    # 2. Удаляем файлы, которые были созданы в рамках транзакции, и очищаем индекс SQLite
                    for rel_path in manifest.get("created_files", []):
                        target_abs = os.path.join(self.root_dir, rel_path)
                        if os.path.exists(target_abs):
                            try:
                                os.remove(target_abs)
                            except OSError:
                                pass
                            try:
                                target_indexer.db.execute("DELETE FROM files_meta WHERE path = ?", (target_abs,))
                                target_indexer.db.execute("DELETE FROM file_content WHERE path = ?", (target_abs,))
                                target_indexer.db.execute("DELETE FROM fts_code WHERE path = ?", (target_abs,))
                                target_indexer.db.commit()
                            except sqlite3.OperationalError:
                                pass

                            parent_dir = os.path.dirname(target_abs)
                            while parent_dir and os.path.abspath(parent_dir) != self.root_dir:
                                try:
                                    os.rmdir(parent_dir)
                                    parent_dir = os.path.dirname(parent_dir)
                                except OSError:
                                    break
                    # 3. Очищаем хэши патчей из SQLite, чтобы их можно было применить снова
                    applied_hashes = manifest.get("applied_hashes", [])
                    if applied_hashes:
                        target_indexer.unmark_patches(applied_hashes)
                else:
                    zf.extractall(self.root_dir)

            os.remove(zip_path)
            if not quiet:
                print(f"{Colors.GREEN}Откат успешно завершен.{Colors.RESET}")
            return True
        except Exception as e:
            if not quiet:
                print(f"{Colors.RED}Ошибка при откате: {e}{Colors.RESET}")
            return False


# ============================================================================
# ИНДЕКСАТОР КОДА SQLITE (FTS5 + КЭШ ПАТЧЕЙ)
# ============================================================================

class CodeIndexer:
    def __init__(self, root_dir: str = ".") -> None:
        self.root_dir = root_dir
        self.db_path = os.path.join(root_dir, DB_PATH)
        self.db = sqlite3.connect(self.db_path, check_same_thread=False)
        self._init_db()

    def _init_db(self) -> None:
        self.db.executescript("""
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

            CREATE TABLE IF NOT EXISTS powershell_audit_log (
                script_hash TEXT PRIMARY KEY,
                strategy TEXT,
                exit_code INTEGER,
                duration_seconds REAL,
                executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        try:
            self.db.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS fts_code USING fts5(path, content, tokenize='trigram');"
            )
        except sqlite3.OperationalError:
            self.db.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS fts_code USING fts5(path, content, tokenize='unicode61');"
            )
        self.db.commit()

    def get_patch_hash(
        self,
        search_lines: List[str],
        replace_lines: List[str],
        file_path: Optional[str] = None
    ) -> str:
        hasher = hashlib.md5()
        if file_path:
            norm_p = os.path.normpath(file_path).replace("\\", "/").lower()
            hasher.update(norm_p.encode("utf-8"))
            hasher.update(b":::")
        hasher.update("\n".join(search_lines).encode("utf-8"))
        hasher.update(b"|||")
        hasher.update("\n".join(replace_lines).encode("utf-8"))
        return hasher.hexdigest()

    def is_patch_applied(self, patch_hash: str) -> bool:
        cur = self.db.execute("SELECT 1 FROM applied_patches WHERE patch_hash = ?", (patch_hash,))
        return cur.fetchone() is not None

    def mark_patch_applied(self, patch_hash: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO applied_patches (patch_hash) VALUES (?)", (patch_hash,))
        self.db.commit()

    def unmark_patches(self, patch_hashes: List[str]) -> None:
        for h in patch_hashes:
            self.db.execute("DELETE FROM applied_patches WHERE patch_hash = ?", (h,))
        self.db.commit()

    def record_powershell_audit(
        self,
        script_hash: str,
        strategy: str,
        exit_code: int,
        duration_seconds: float
    ) -> None:
        self.db.execute(
            """
            INSERT OR REPLACE INTO powershell_audit_log
            (script_hash, strategy, exit_code, duration_seconds)
            VALUES (?, ?, ?, ?)
            """,
            (script_hash, strategy, exit_code, duration_seconds)
        )
        self.db.commit()

    def fts_search(self, search_lines: List[str]) -> Optional[Set[str]]:
        words: List[str] = []
        for line in search_lines:
            if not is_comment_line(line):
                words.extend(re.findall(r"[a-zA-Z_]{4,}", line))
        if not words:
            for line in search_lines:
                words.extend(re.findall(r"[a-zA-Z_]{4,}", line))
        if not words:
            return None

        words = sorted(list(set(words)), key=len, reverse=True)[:3]
        query = " AND ".join(f'"{w}"' for w in words)
        try:
            cur = self.db.execute("SELECT path FROM fts_code WHERE content MATCH ? LIMIT 100", (query,))
            res = set(row[0] for row in cur.fetchall())
            if res:
                return res
            if len(words) > 1:
                or_query = " OR ".join(f'"{w}"' for w in words)
                cur = self.db.execute("SELECT path FROM fts_code WHERE content MATCH ? LIMIT 100", (or_query,))
                res = set(row[0] for row in cur.fetchall())
                if res:
                    return res
            return None
        except sqlite3.OperationalError:
            return None

    def sync_and_read(
        self,
        ignore_folders: List[str],
        ignore_files: List[str],
        prompt_filenames: Set[str],
        quiet: bool = False
    ) -> Tuple[Dict[str, List[str]], Dict[str, Tuple[str, str]], List[str], Dict[str, List[str]], Dict[str, List[str]]]:
        if not quiet:
            print(f"{Colors.YELLOW}Синхронизация инкрементального кэша SQLite...{Colors.RESET}")
        cur = self.db.execute("SELECT path, size, mtime, is_binary, encoding, newlines FROM files_meta")
        db_files = {row[0]: row for row in cur.fetchall()}

        folders_set = set(ignore_folders) | IGNORE_DIRS
        files_set = set(ignore_files) | {DB_PATH, os.path.basename(CLIPBOARD_LOCK_FILE)}
        current_files = _scan_directory_tree(self.root_dir, folders_set, files_set)

        file_contents: Dict[str, List[str]] = {}
        file_encodings: Dict[str, Tuple[str, str]] = {}
        non_utf8_files: List[str] = []
        code_file_contents: Dict[str, List[str]] = {}
        other_file_contents: Dict[str, List[str]] = {}

        current_paths_set = set(current_files)
        paths_to_delete = [p for p in db_files if p not in current_paths_set]

        if paths_to_delete:
            for chunk in [paths_to_delete[i:i + 500] for i in range(0, len(paths_to_delete), 500)]:
                qs = ",".join("?" * len(chunk))
                self.db.execute(f"DELETE FROM files_meta WHERE path IN ({qs})", chunk)
                self.db.execute(f"DELETE FROM file_content WHERE path IN ({qs})", chunk)
                self.db.execute(f"DELETE FROM fts_code WHERE path IN ({qs})", chunk)

        updates = []
        fts_updates = []

        def process_file(file_path: str):
            try:
                stat = os.stat(file_path)
                size = stat.st_size
                mtime = stat.st_mtime
            except OSError:
                return None

            if size > 1 * 1024 * 1024 or file_path == DB_PATH or DB_PATH in file_path:
                return None

            db_record = db_files.get(file_path)
            is_bin = False
            enc = None
            newlines = "\n"
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
                        with open(file_path, "rb") as fp:
                            raw = fp.read()

                        if not raw:
                            content = ""
                            enc = "utf-8"
                            newlines = "\n"
                            read_success = True
                        else:
                            if raw.startswith(b"\xef\xbb\xbf"):
                                encodings = ["utf-8-sig"]
                            elif raw.startswith(b"\xff\xfe"):
                                encodings = ["utf-16le", "utf-16"]
                            elif raw.startswith(b"\xfe\xff"):
                                encodings = ["utf-16be", "utf-16"]
                            else:
                                encodings = ["utf-8"]

                            read_success = False
                            for e in encodings:
                                try:
                                    content = raw.decode(e)
                                    enc = e
                                    read_success = True
                                    break
                                except (UnicodeDecodeError, LookupError):
                                    pass

                            if not read_success and CHARSET_NORMALIZER_AVAILABLE:
                                try:
                                    res = charset_normalizer.detect(raw)
                                    detected_enc = res.get("encoding") if res else None
                                    if detected_enc:
                                        content = raw.decode(detected_enc)
                                        enc = detected_enc
                                        read_success = True
                                except Exception:
                                    pass

                            if not read_success:
                                for fe in ["utf-16le", "utf-16be", "cp1251", "cp1252", "latin-1"]:
                                    try:
                                        content = raw.decode(fe)
                                        enc = fe
                                        read_success = True
                                        break
                                    except (UnicodeDecodeError, LookupError):
                                        pass

                        if read_success and content is not None:
                            newlines = "\r\n" if "\r\n" in content else "\n"
                        else:
                            enc = None
                    except Exception:
                        is_bin = True
                        content = None

            return (file_path, size, mtime, is_bin, enc, newlines, content, needs_read)

        with concurrent.futures.ThreadPoolExecutor(max_workers=32) as executor:
            results = list(executor.map(process_file, current_files))

        for res in results:
            if not res:
                continue
            file_path, size, mtime, is_bin, enc, newlines, content, needs_read = res

            if not needs_read and not is_bin:
                if size < SIZE_THRESHOLD:
                    cur = self.db.execute("SELECT content FROM file_content WHERE path = ?", (file_path,))
                    row = cur.fetchone()
                    if row:
                        content = row[0]
                    elif enc:
                        try:
                            with open(file_path, "r", encoding=enc) as fp:
                                content = fp.read()
                        except Exception:
                            pass
                elif enc:
                    try:
                        with open(file_path, "r", encoding=enc) as fp:
                            content = fp.read()
                    except Exception:
                        pass

            if needs_read:
                updates.append((file_path, size, mtime, int(is_bin), enc, newlines))
                if not is_bin and content is not None:
                    if size < SIZE_THRESHOLD:
                        self.db.execute("REPLACE INTO file_content (path, content) VALUES (?, ?)", (file_path, content))
                    self.db.execute("DELETE FROM fts_code WHERE path = ?", (file_path,))
                    fts_updates.append((file_path, content))

            if not is_bin:
                if content is not None and enc is not None:
                    lines = content.splitlines()
                    file_contents[file_path] = lines
                    file_encodings[file_path] = (enc, newlines)
                    ext = os.path.splitext(file_path)[1].lower()
                    if is_known_text_file(file_path) or ext in TEXT_EXTENSIONS or not ext:
                        code_file_contents[file_path] = lines
                    else:
                        other_file_contents[file_path] = lines
                else:
                    non_utf8_files.append(file_path)

        if updates:
            for chunk in [updates[i:i + 500] for i in range(0, len(updates), 500)]:
                self.db.executemany(
                    "REPLACE INTO files_meta (path, size, mtime, is_binary, encoding, newlines) VALUES (?, ?, ?, ?, ?, ?)",
                    chunk
                )
        if fts_updates:
            for chunk in [fts_updates[i:i + 500] for i in range(0, len(fts_updates), 500)]:
                self.db.executemany("INSERT INTO fts_code (path, content) VALUES (?, ?)", chunk)

        self.db.commit()

        priority_files = set(prompt_filenames or set())

        def sort_key(item):
            p_norm = os.path.normpath(item[0]).lower()
            p_base = os.path.basename(p_norm)
            is_prio = 0
            for pf in priority_files:
                pf_norm = os.path.normpath(pf).lower()
                if p_norm == pf_norm or p_norm.endswith(os.sep + pf_norm) or p_base == pf_norm:
                    is_prio = 1
                    break
            return (-is_prio, len(item[1]))

        code_file_contents = dict(sorted(code_file_contents.items(), key=sort_key))
        other_file_contents = dict(sorted(other_file_contents.items(), key=sort_key))

        return file_contents, file_encodings, non_utf8_files, code_file_contents, other_file_contents


# ============================================================================
# АЛГОРИТМЫ КАСКАДНОГО СОПОСТАВЛЕНИЯ (EXACT -> ANCHOR -> FUZZY)
# ============================================================================

def can_contain_block(search_lines: List[str], f_text: str) -> bool:
    code_lines = [l.strip() for l in search_lines if len(l.strip()) > 6 and not is_comment_line(l)]
    target_lines = code_lines if code_lines else [l.strip() for l in search_lines if len(l.strip()) > 6]
    if not target_lines:
        return True

    longest_lines = sorted(target_lines, key=len, reverse=True)[:5]
    for line in longest_lines:
        if line in f_text:
            return True

    f_text_collapsed = re.sub(r"[ \t]+", " ", f_text)
    for line in longest_lines:
        line_collapsed = re.sub(r"[ \t]+", " ", line)
        if line_collapsed in f_text_collapsed:
            return True

    for line in longest_lines:
        tokens = [
            t for t in re.findall(r"[a-zA-Z0-9_]{5,}", line)
            if t.lower() not in ("return", "const", "function", "import", "export")
        ]
        if len(tokens) >= 2 and all(t in f_text for t in tokens[:2]):
            return True
        tokens_long = [
            t for t in re.findall(r"[a-zA-Z0-9_]{8,}", line)
            if t.lower() not in ("function", "exception", "interface")
        ]
        if any(t in f_text for t in tokens_long):
            return True

    return False


def get_fuzzy_ratio(s1: str, s2: str) -> float:
    if RAPIDFUZZ_AVAILABLE:
        return fuzz.ratio(s1, s2) / 100.0
    return difflib.SequenceMatcher(None, s1, s2).ratio()


def adjust_comment_boundaries(
    search_lines: List[str],
    file_lines: List[str],
    start: int,
    end: int
) -> Tuple[int, int]:
    s_clean = [l for l in search_lines if l.strip()]
    if not s_clean:
        return start, end

    lead_comments = []
    for l in s_clean:
        if is_comment_line(l):
            lead_comments.append(l.strip())
        else:
            break

    adj_start = start
    if lead_comments:
        curr = start - 1
        for comm in reversed(lead_comments):
            if curr >= 0 and is_comment_line(file_lines[curr]):
                if get_fuzzy_ratio(comm, file_lines[curr].strip()) > 0.4:
                    adj_start = curr
                    curr -= 1
                else:
                    break
            else:
                break

    trail_comments = []
    for l in reversed(s_clean):
        if is_comment_line(l):
            trail_comments.append(l.strip())
        else:
            break
    trail_comments.reverse()

    adj_end = end
    if trail_comments:
        curr = end
        for comm in trail_comments:
            if curr < len(file_lines) and is_comment_line(file_lines[curr]):
                if get_fuzzy_ratio(comm, file_lines[curr].strip()) > 0.4:
                    adj_end = curr + 1
                    curr += 1
                else:
                    break
            else:
                break

    return adj_start, adj_end


def compute_mismatch_stats(search_lines: List[str], candidate_lines: List[str]) -> Dict[str, Any]:
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

    s_code = [l.strip() for l in search_lines if not is_comment_line(l) and l.strip()]
    c_code = [l.strip() for l in candidate_lines if not is_comment_line(l) and l.strip()]
    if s_code and c_code:
        code_matcher = difflib.SequenceMatcher(None, "\n".join(s_code), "\n".join(c_code))
        code_similarity_pct = code_matcher.ratio() * 100.0
    else:
        code_similarity_pct = char_similarity_pct

    return {
        "mismatched_chars": mismatched_chars,
        "char_mismatch_pct": char_mismatch_pct,
        "mismatched_lines": mismatched_lines,
        "line_mismatch_pct": line_mismatch_pct,
        "char_similarity_pct": char_similarity_pct,
        "line_similarity_pct": line_similarity_pct,
        "code_similarity_pct": code_similarity_pct
    }


def find_anchors_match(search_lines: List[str], file_lines: List[str]) -> List[Dict[str, Any]]:
    s_clean = search_lines[:]
    while s_clean and not s_clean[0].strip():
        s_clean.pop(0)
    while s_clean and not s_clean[-1].strip():
        s_clean.pop()

    if len(s_clean) < 3:
        return []

    s_norm = [l.strip() for l in s_clean]
    f_norm = [l.strip() for l in file_lines]

    line_index: Dict[str, List[int]] = {}
    for idx, line in enumerate(f_norm):
        if line not in line_index:
            line_index[line] = []
        line_index[line].append(idx)

    def get_anchor_indices(anchor: List[str], target_lines: List[str], start_index: int = 0) -> List[int]:
        matches = []
        a_len = len(anchor)
        if a_len == 0:
            return matches
        first_line = anchor[0]
        if first_line not in line_index:
            return matches
        for idx in line_index[first_line]:
            if start_index <= idx <= len(target_lines) - a_len:
                if target_lines[idx:idx + a_len] == anchor:
                    matches.append(idx)
        return matches

    anchor_offset = 0
    while anchor_offset < len(s_norm) and is_comment_line(s_norm[anchor_offset]):
        anchor_offset += 1
    if anchor_offset >= len(s_norm) or s_norm[0] in line_index:
        anchor_offset = 0

    avail_len = len(s_clean) - anchor_offset
    max_anchor_len = max(3, avail_len // 2)

    top_anchor_len = max(1, min(3, avail_len))
    start_idx_matches: List[int] = []
    while top_anchor_len <= max_anchor_len:
        top_anchor = s_norm[anchor_offset:anchor_offset + top_anchor_len]
        matches = get_anchor_indices(top_anchor, f_norm)
        if not matches:
            if top_anchor_len > 1 and not start_idx_matches:
                matches = get_anchor_indices(s_norm[anchor_offset:anchor_offset + 1], f_norm)
                if matches:
                    start_idx_matches = matches
            break
        start_idx_matches = matches
        if len(start_idx_matches) == 1:
            break
        top_anchor_len += 1

    if not start_idx_matches:
        return []

    candidates = []
    for start_idx in start_idx_matches:
        bottom_anchor_len = 2
        end_idx_matches: List[int] = []
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
            end_anchor_start_idx = min(end_idx_matches, key=lambda x: abs(x - (start_idx + len(s_clean))))
            end_idx = end_anchor_start_idx + bottom_anchor_len

            if end_idx - start_idx <= len(s_clean) * 2:
                adj_s, adj_e = adjust_comment_boundaries(s_clean, file_lines, start_idx, end_idx)
                candidates.append({"start": adj_s, "end": adj_e, "ratio": 0.95})

    return candidates


def _fuzzy_slide_window(
    s_norm: List[str],
    f_norm: List[str],
    file_lines: List[str],
    leading: int,
    trailing: int,
    threshold: float
) -> List[Dict[str, Any]]:
    n_s = len(s_norm)
    n_f = len(f_norm)
    if n_s == 0 or n_f > 5000:
        return []

    candidates = []
    s_text = "\n".join(s_norm)
    dynamic_threshold = threshold if n_s >= 4 else 0.75

    i = 0
    if RAPIDFUZZ_AVAILABLE:
        while i <= n_f - n_s:
            window = f_norm[i:i + n_s]
            w_text = "\n".join(window)
            ratio = fuzz.ratio(s_text, w_text) / 100.0

            if ratio >= dynamic_threshold:
                actual_start = max(0, i - leading)
                actual_end = min(len(file_lines), i + n_s + trailing)
                candidates.append({"start": actual_start, "end": actual_end, "ratio": ratio * 0.98})
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
                candidates.append({"start": actual_start, "end": actual_end, "ratio": ratio * 0.98})
                i += n_s
            else:
                i += 1
    return candidates


def find_fuzzy_block(search_lines: List[str], file_lines: List[str], threshold: float = 0.50) -> List[Dict[str, Any]]:
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

    candidates: List[Dict[str, Any]] = []
    s_text = "\n".join(s_norm)
    f_text_full = "\n".join(f_norm)

    if not can_contain_block(s_norm, f_text_full):
        return candidates

    if n_f < n_s:
        ratio = get_fuzzy_ratio(s_text, f_text_full)
        if ratio >= max(threshold, 0.95):
            candidates.append({"start": 0, "end": len(file_lines), "ratio": ratio * 0.98})
        return candidates

    candidates = _fuzzy_slide_window(s_norm, f_norm, file_lines, leading, trailing, threshold)
    if candidates:
        return candidates

    code_norm = [l for l in s_norm if not is_comment_line(l) and l]
    if len(code_norm) >= 2 and len(code_norm) < len(s_norm):
        cands_code = _fuzzy_slide_window(code_norm, f_norm, file_lines, 0, 0, threshold)
        for c in cands_code:
            adj_s, adj_e = adjust_comment_boundaries(s_clean, file_lines, c["start"], c["end"])
            candidates.append({"start": adj_s, "end": adj_e, "ratio": c["ratio"] * 0.95})

    return candidates


def find_matches(search_lines: List[str], file_lines: List[str]) -> List[Tuple[int, int]]:
    matches: List[Tuple[int, int]] = []
    n_s = len(search_lines)
    n_f = len(file_lines)

    if n_s == 0 or n_s > n_f:
        return matches

    s_text = "\n".join(l.rstrip() for l in search_lines)
    f_text = "\n".join(l.rstrip() for l in file_lines)

    idx = f_text.find(s_text)
    while idx != -1:
        # Проверяем, что совпадение начинается строго с начала строки и заканчивается концом строки
        is_line_start = (idx == 0) or (f_text[idx - 1] == "\n")
        end_pos = idx + len(s_text)
        is_line_end = (end_pos == len(f_text)) or (f_text[end_pos] == "\n")
        if is_line_start and is_line_end:
            start_line = f_text.count("\n", 0, idx)
            matches.append((start_line, start_line + n_s))
        idx = f_text.find(s_text, idx + 1)

    return matches


def find_indent_agnostic_matches(search_lines: List[str], file_lines: List[str]) -> List[Tuple[int, int]]:
    # Срезаем только ведущие и завершающие пустые строки, сохраняя внутренние пустые строки
    start_s = 0
    end_s = len(search_lines)
    while start_s < end_s and not search_lines[start_s].strip():
        start_s += 1
    while end_s > start_s and not search_lines[end_s - 1].strip():
        end_s -= 1

    s_target = search_lines[start_s:end_s]
    if not s_target:
        return []

    n_s = len(s_target)
    n_f = len(file_lines)
    if n_s > n_f:
        return []

    s_stripped = [l.strip() for l in s_target]
    s_non_empty_indices = [idx for idx, l in enumerate(s_stripped) if l]
    if not s_non_empty_indices:
        return []

    s_indents = [len(l) - len(l.lstrip(" ")) for l in s_target]
    first_idx = s_non_empty_indices[0]
    base_s_indent = s_indents[first_idx]

    matches: List[Tuple[int, int]] = []

    for i in range(n_f - n_s + 1):
        window = file_lines[i:i + n_s]
        if [l.strip() for l in window] != s_stripped:
            continue

        w_indents = [len(l) - len(l.lstrip(" ")) for l in window]
        delta = w_indents[first_idx] - base_s_indent
        if all(w_indents[idx] - s_indents[idx] == delta for idx in s_non_empty_indices):
            matches.append((i, i + n_s))
    return matches


def execute_powershell(
    script: str,
    root_dir: Optional[Path] = None,
    indexer: Optional[CodeIndexer] = None,
    is_auto: bool = False,
    disable_all_except_write: bool = True
) -> bool:
    target_dir = root_dir if root_dir is not None else Path(".")
    tm = TransactionManager(root_dir=str(target_dir))
    summary = PowerShellSecurityOrchestrator.dispatch(
        raw_script=script,
        root_dir=target_dir,
        tm=tm,
        indexer=indexer,
        is_auto=is_auto,
        disable_all_except_write=disable_all_except_write
    )
    if summary.stdout:
        print(summary.stdout)
    if summary.stderr:
        print(f"{Colors.YELLOW}Поток stderr:\n{summary.stderr}{Colors.RESET}")
    return summary.success


# ============================================================================
# ЯДРО ИСПОЛНЕНИЯ И ПРОГРАММНЫЙ API (REPLACER ENGINE)
# ============================================================================

class ReplacerAPI:
    """
    Программный интерфейс (API) для вызова Replacer из других Python-модулей и ИИ-агентов.
    Поддерживает передачу сырого текста ответа LLM или пути к файлу с ответом LLM.
    """

    def __init__(
        self,
        root_dir: Union[str, Path] = ".",
        ignore_folders: Optional[List[str]] = None,
        ignore_files: Optional[List[str]] = None,
        auto_mode: bool = True,
        disable_all_except_write: bool = True,
        unsafe_direct_ps: bool = False,
        allow_multi_match_in_auto: bool = False,
        fuzzy_auto_threshold_pct: float = 90.0,
        quiet: bool = False
    ) -> None:
        self.root_dir = Path(root_dir).resolve()
        self.ignore_folders = list(ignore_folders) if ignore_folders else list(IGNORE_DIRS)
        self.ignore_files = list(ignore_files) if ignore_files else [DB_PATH, CLIPBOARD_LOCK_FILE]
        self.auto_mode = auto_mode
        self.disable_all_except_write = disable_all_except_write
        self.unsafe_direct_ps = unsafe_direct_ps
        self.allow_multi_match_in_auto = allow_multi_match_in_auto
        self.fuzzy_auto_threshold_pct = fuzzy_auto_threshold_pct
        self.quiet = quiet

    def _log(self, msg: str) -> None:
        if not self.quiet:
            print(msg)

    def apply_file(self, llm_response_file: Union[str, Path]) -> ReplacerExecutionReport:
        """Читает текстовый файл с ответом LLM и применяет все блоки правок и безопасные команды."""
        fpath = Path(llm_response_file).resolve()
        if not fpath.is_file():
            return ReplacerExecutionReport(
                success=False,
                action="APPLY",
                errors=[f"Файл с ответом LLM не найден: {fpath}"]
            )
        # Исключаем файл промпта из сканирования кодовой базы
        if fpath.name not in self.ignore_files:
            self.ignore_files.append(fpath.name)

        content = fpath.read_text(encoding="utf-8-sig", errors="replace")
        return self.apply_text(content)

    def undo(self) -> ReplacerExecutionReport:
        """Откатывает последнюю транзакцию в root_dir."""
        indexer = CodeIndexer(str(self.root_dir))
        tm = TransactionManager(str(self.root_dir))
        ok = tm.undo_last(indexer=indexer, quiet=self.quiet)
        return ReplacerExecutionReport(
            success=ok,
            action="UNDO",
            errors=[] if ok else ["Нет сохраненных транзакций для отмены или произошла ошибка отката."]
        )

    def apply_text(self, llm_text: str) -> ReplacerExecutionReport:
        """Парсит текст ответа LLM и применяет изменения к проекту."""
        input_lines = [line + "\n" for line in llm_text.splitlines()]
        return self.apply_lines(input_lines)

    def apply_lines(self, input_lines: List[str]) -> ReplacerExecutionReport:
        start_time = time.time()
        report = ReplacerExecutionReport(success=False, action="APPLY")

        parsed = parse_patch_blocks(input_lines)
        if parsed == "UNDO":
            return self.undo()

        blocks: List[Dict[str, Any]] = parsed if isinstance(parsed, list) else []
        report.blocks_total = len(blocks)

        prompt_filenames = extract_filenames_from_prompt(input_lines)
        indexer = CodeIndexer(str(self.root_dir))
        tm = TransactionManager(str(self.root_dir))

        # 1. Обработка команд PowerShell (Fast-Path запись файлов или изолированный запуск)
        ps_commands = extract_powershell_commands(input_lines)
        report.ps_commands_total = len(ps_commands)

        if ps_commands:
            self._log(f"\n{Colors.YELLOW}Обнаружены команды PowerShell ({len(ps_commands)} шт.):{Colors.RESET}")
            for idx, payload in enumerate(ps_commands, 1):
                self._log(f"{Colors.YELLOW}--- Команда {idx} (строки {payload.start_line}-{payload.end_line}) ---{Colors.RESET}")
                summary = PowerShellSecurityOrchestrator.dispatch(
                    raw_script=payload.command,
                    root_dir=self.root_dir,
                    tm=tm,
                    indexer=indexer,
                    is_auto=self.auto_mode,
                    disable_all_except_write=self.disable_all_except_write,
                    unsafe_direct_ps=self.unsafe_direct_ps,
                    quiet=self.quiet
                )
                report.ps_summaries.append(summary)

                if not summary.success:
                    self._log(f"{Colors.RED}Прерывание работы из-за ошибки в PowerShell команде {idx}:{Colors.RESET}")
                    if summary.error_message:
                        self._log(f"{Colors.RED}{summary.error_message}{Colors.RESET}")
                        report.errors.append(summary.error_message)
                    if summary.stderr and summary.stderr != summary.error_message:
                        self._log(f"{Colors.YELLOW}{summary.stderr}{Colors.RESET}")
                    # Если в рамках этой транзакции уже были изменены файлы — откатываем
                    if tm._active_tx_id is not None:
                        tm.undo_last(indexer=indexer, quiet=self.quiet)
                    report.duration_seconds = time.time() - start_time
                    return report
                else:
                    self._log(f"{Colors.GREEN}[УСПЕХ ({summary.strategy.value})] {summary.stdout}{Colors.RESET}")
                    if summary.target_path:
                        rel_t = os.path.relpath(summary.target_path, start=str(self.root_dir))
                        if summary.was_new_file:
                            if rel_t not in report.created_files:
                                report.created_files.append(rel_t)
                        else:
                            if rel_t not in report.modified_files:
                                report.modified_files.append(rel_t)

        # 1.5. Обработка декларативных блоков создания файлов (CREATE)
        create_blocks = extract_create_blocks(input_lines)
        if create_blocks:
            self._log(f"\n{Colors.YELLOW}Обнаружены блоки CREATE ({len(create_blocks)} шт.):{Colors.RESET}")
            for idx, cb in enumerate(create_blocks, 1):
                self._log(f"{Colors.YELLOW}--- Создание файла {idx} (строки {cb.start_line}-{cb.end_line}): {cb.target_path_raw} ---{Colors.RESET}")
                try:
                    resolved_target = SecurePathResolver.resolve_project_path(cb.target_path_raw, self.root_dir)
                except PathTraversalSecurityError as e:
                    err_msg = f"Ошибка безопасности при создании файла {cb.target_path_raw}: {e}"
                    self._log(f"{Colors.RED}{err_msg}{Colors.RESET}")
                    if tm._active_tx_id is not None:
                        tm.undo_last(indexer=indexer, quiet=self.quiet)
                    report.errors.append(err_msg)
                    report.duration_seconds = time.time() - start_time
                    return report

                was_new = not resolved_target.exists()

                is_valid, lint_err = run_dry_linter(str(resolved_target), cb.content)
                if not is_valid:
                    rel_t = os.path.relpath(resolved_target, start=str(self.root_dir))
                    report.linter_errors[rel_t] = lint_err
                    err_msg = f"Проверка линтера не пройдена для {resolved_target.name}: {lint_err}"
                    self._log(f"{Colors.RED}{err_msg}{Colors.RESET}")
                    if tm._active_tx_id is not None:
                        tm.undo_last(indexer=indexer, quiet=self.quiet)
                    report.errors.append(err_msg)
                    report.duration_seconds = time.time() - start_time
                    return report

                try:
                    cb_hash = hashlib.md5(f"CREATE:{cb.target_path_raw}\n{cb.content}".encode("utf-8")).hexdigest()
                    tm.backup_files([str(resolved_target)], patch_hashes=[cb_hash])
                    op = TranspiledFileOperation(
                        target_path=resolved_target,
                        content=cb.content,
                        encoding="utf-8",
                        mode=FileWriteMode.OVERWRITE
                    )
                    ZeroShellTranspiler.execute_operation(op)
                    indexer.mark_patch_applied(cb_hash)

                    rel_t = os.path.relpath(resolved_target, start=str(self.root_dir))
                    if was_new:
                        if rel_t not in report.created_files:
                            report.created_files.append(rel_t)
                    else:
                        if rel_t not in report.modified_files:
                            report.modified_files.append(rel_t)

                    self._log(f"{Colors.GREEN}[УСПЕХ (CREATE)] Файл успешно записан: {rel_t}{Colors.RESET}")
                except Exception as e:
                    err_msg = f"Ошибка при создании файла {cb.target_path_raw}: {e}"
                    self._log(f"{Colors.RED}{err_msg}{Colors.RESET}")
                    if tm._active_tx_id is not None:
                        tm.undo_last(indexer=indexer, quiet=self.quiet)
                    report.errors.append(err_msg)
                    report.duration_seconds = time.time() - start_time
                    return report

        if not blocks:
            if not ps_commands and not create_blocks:
                msg = f"Не найдено ни одного блока с маркерами {'<' * 4} {'=' * 4} {'>' * 4}, блоков CREATE или команд PowerShell!"
                self._log(f"{Colors.RED}{msg}{Colors.RESET}")
                report.action = "NOOP"
                report.errors.append(msg)
                report.duration_seconds = time.time() - start_time
                return report
            # Если были только успешные команды PowerShell или CREATE блоки:
            # синхронизируем индекс SQLite, чтобы новые файлы сразу попали в базу и FTS
            indexer.sync_and_read(self.ignore_folders, self.ignore_files, prompt_filenames, quiet=self.quiet)
            report.success = True
            report.transaction_id = tm._active_tx_id
            report.duration_seconds = time.time() - start_time
            return report

        # 2. Обработка блоков Search / Replace
        self._log(f"\nРаспознано блоков правок: {len(blocks)}. Идет сканирование файлов...")
        file_contents, file_encodings, non_utf8_files, code_file_contents, other_file_contents = indexer.sync_and_read(
            self.ignore_folders, self.ignore_files, prompt_filenames, quiet=self.quiet
        )

        block_matches: List[List[Tuple[str, int, int]]] = []
        already_applied_blocks: Dict[int, List[Tuple[str, int, int]]] = {}
        user_skipped_blocks: Set[int] = set()
        cached_blocks: Set[int] = set()
        closest_candidate_per_block: Dict[int, Dict[str, Any]] = {}

        def get_candidates(search_lines: List[str], target_files: Dict[str, List[str]], use_fts: bool = True) -> List[Dict[str, Any]]:
            cands: List[Dict[str, Any]] = []
            allowed_paths: Optional[Set[str]] = None

            if use_fts:
                rg_paths = ripgrep_search(search_lines, str(self.root_dir))
                if rg_paths is not None:
                    allowed_paths = rg_paths
                else:
                    allowed_paths = indexer.fts_search(search_lines)

            for path, lines in target_files.items():
                if allowed_paths is not None:
                    p_norm = os.path.normpath(path)
                    p_rel = os.path.normpath(os.path.relpath(path, start=str(self.root_dir)))
                    if p_norm not in allowed_paths and path not in allowed_paths and p_rel not in allowed_paths:
                        continue

                exact_matches = find_matches(search_lines, lines)
                if exact_matches:
                    for m in exact_matches:
                        cands.append({"path": path, "start": m[0], "end": m[1], "ratio": 1.0})
                    continue

                indent_matches = find_indent_agnostic_matches(search_lines, lines)
                if indent_matches:
                    for m in indent_matches:
                        cands.append({"path": path, "start": m[0], "end": m[1], "ratio": 0.999})
                    continue

                code_only_lines = [l for l in search_lines if not is_comment_line(l) and l.strip()]
                if len(code_only_lines) >= 2 and len(code_only_lines) < len([l for l in search_lines if l.strip()]):
                    code_matches = find_matches(code_only_lines, lines)
                    if code_matches:
                        for m in code_matches:
                            adj_s, adj_e = adjust_comment_boundaries(search_lines, lines, m[0], m[1])
                            cands.append({"path": path, "start": adj_s, "end": adj_e, "ratio": 0.99})
                        continue

                file_candidates = find_anchors_match(search_lines, lines)
                if file_candidates:
                    for cand in file_candidates:
                        cands.append({"path": path, "start": cand["start"], "end": cand["end"], "ratio": cand["ratio"]})
                else:
                    s_clean_len = len([l for l in search_lines if l.strip()])
                    if s_clean_len > 0:
                        for cand in find_fuzzy_block(search_lines, lines):
                            cands.append({"path": path, "start": cand["start"], "end": cand["end"], "ratio": cand["ratio"]})
            return cands

        for idx, block in enumerate(blocks):
            search_lines = block["search"]
            replace_lines = block["replace"]
            file_hint = block.get("file_hint")
            matches_for_block: List[Tuple[str, int, int]] = []
            candidates: List[Dict[str, Any]] = []

            # 1. Если файл указан прямо в блоке (<<<< file.ext или <<<< folder/file.ext), ищем сначала в нем
            if file_hint:
                hint_norm = os.path.normpath(file_hint).lower()
                hint_base = os.path.basename(hint_norm)

                # Поиск по относительному пути (например, folder/text.txt -> root/folder/text.txt)
                hint_files = {
                    p: lines for p, lines in file_contents.items()
                    if os.path.normpath(p).lower() == hint_norm
                    or os.path.normpath(p).lower().endswith(os.sep + hint_norm)
                    or os.path.normpath(p).lower().endswith("/" + hint_norm)
                }

                # Поиск по конечному имени файла (например, plan.yaml -> any/sub/folder/plan.yaml)
                if not hint_files:
                    hint_files = {
                        p: lines for p, lines in file_contents.items()
                        if os.path.basename(os.path.normpath(p).lower()) == hint_base
                    }

                if hint_files:
                    candidates = get_candidates(search_lines, hint_files, use_fts=False)

            if not candidates:
                candidates = get_candidates(search_lines, code_file_contents, use_fts=True)

            if not candidates and code_file_contents:
                candidates = get_candidates(search_lines, code_file_contents, use_fts=False)

            if not candidates and other_file_contents:
                prio_other = {
                    p: lines for p, lines in other_file_contents.items()
                    if any(
                        os.path.normpath(p).lower() == os.path.normpath(pf).lower()
                        or os.path.basename(p).lower() == os.path.basename(pf).lower()
                        for pf in prompt_filenames
                    )
                }
                if prio_other:
                    candidates = get_candidates(search_lines, prio_other, use_fts=False)

                if not candidates:
                    self._log(f"\n{Colors.YELLOW}Блок {idx + 1} не найден в основных файлах.{Colors.RESET}")
                    ans = "y" if self.auto_mode else "n"
                    if not self.auto_mode:
                        try:
                            ans = input("Искать в остальных текстовых файлах проекта? [y/n] (Enter=y): ").strip().lower()
                            if not ans:
                                ans = "y"
                        except EOFError:
                            ans = "y"

                    if ans in ["y", "yes", "да", "1"]:
                        candidates = get_candidates(search_lines, other_file_contents, use_fts=False)

            if not candidates and non_utf8_files:
                self._log(f"\n{Colors.YELLOW}Блок {idx + 1} не найден. Подгрузка файлов с другими кодировками...{Colors.RESET}")
                newly_read: Dict[str, List[str]] = {}
                for f in list(non_utf8_files):
                    for enc in ["utf-16", "utf-16le", "utf-16be", "cp1251", "latin-1"]:
                        try:
                            with open(f, "r", encoding=enc, newline="") as fp:
                                content = fp.read()
                                file_newlines = "\r\n" if "\r\n" in content else "\n"
                                lines = content.splitlines()
                            file_contents[f] = lines
                            file_encodings[f] = (enc, file_newlines)
                            newly_read[f] = lines
                            non_utf8_files.remove(f)

                            if is_known_text_file(f):
                                code_file_contents[f] = lines
                            else:
                                other_file_contents[f] = lines
                            break
                        except (UnicodeError, LookupError, ValueError):
                            pass

                if newly_read:
                    candidates = get_candidates(search_lines, newly_read, use_fts=False)

            if file_hint and candidates:
                hint_norm = os.path.normpath(file_hint).lower()
                hint_base = os.path.basename(hint_norm)
                hint_matched = []
                for c in candidates:
                    c_norm = os.path.normpath(c["path"]).lower()
                    if c_norm == hint_norm or c_norm.endswith(os.sep + hint_norm) or c_norm.endswith("/" + hint_norm):
                        hint_matched.append(c)
                if not hint_matched:
                    for c in candidates:
                        if os.path.basename(os.path.normpath(c["path"]).lower()) == hint_base:
                            hint_matched.append(c)
                if hint_matched:
                    candidates = hint_matched

            candidates.sort(key=lambda x: x["ratio"], reverse=True)
            if candidates:
                closest_candidate_per_block[idx] = candidates[0]

            exact_candidates = [c for c in candidates if c["ratio"] >= 0.999]
            if exact_candidates:
                if len(exact_candidates) > 1 and prompt_filenames:
                    prio_exact = []
                    for ec in exact_candidates:
                        ec_norm = os.path.normpath(ec["path"]).lower()
                        for pf in prompt_filenames:
                            pf_norm = os.path.normpath(pf).lower()
                            if ec_norm == pf_norm or ec_norm.endswith(os.sep + pf_norm) or ec_norm.endswith("/" + pf_norm):
                                prio_exact.append(ec)
                                break
                    if prio_exact:
                        exact_candidates = prio_exact

                for ec in exact_candidates:
                    matches_for_block.append((ec["path"], ec["start"], ec["end"]))
            else:
                for cand in candidates:
                    path = cand["path"]
                    start = cand["start"]
                    end = cand["end"]

                    candidate_lines = file_contents[path][start:end]
                    stats = compute_mismatch_stats(search_lines, candidate_lines)
                    cand["stats"] = stats

                    self._log(f"\n{Colors.YELLOW}Найден похожий блок в файле {path}:{Colors.RESET}")
                    self._log(f"Блок {idx + 1}: строки {start + 1}..{end}")
                    self._log(f"Сходство символов: {stats['char_similarity_pct']:.1f}% | Сходство кода: {stats['code_similarity_pct']:.1f}%")
                    self._log(f"Несоответствует символов: {stats['mismatched_chars']} ({stats['char_mismatch_pct']:.1f}%)")
                    self._log(f"Несоответствует строк: {stats['mismatched_lines']} ({stats['line_mismatch_pct']:.1f}%)")

                    s_norm = [l.strip() for l in search_lines]
                    c_norm = [l.strip() for l in candidate_lines]
                    line_matcher = difflib.SequenceMatcher(None, s_norm, c_norm)

                    if not self.quiet:
                        print("Детализация расхождений:")
                        for tag, i1, i2, j1, j2 in line_matcher.get_opcodes():
                            if tag == "replace":
                                for idx_s in range(i1, i2):
                                    s_line = search_lines[idx_s]
                                    idx_c = j1 + (idx_s - i1)
                                    if idx_c < j2:
                                        c_line = candidate_lines[idx_c]
                                        line_diff_ratio = difflib.SequenceMatcher(None, s_line.strip(), c_line.strip()).ratio()
                                        line_char_mismatch_pct = (1.0 - line_diff_ratio) * 100
                                        print(f"  Строка {idx_s + 1} (несоответствует: {line_char_mismatch_pct:.1f}%):")
                                        print(f"    Ожидалось: {Colors.RED}{s_line.strip()}{Colors.RESET}")
                                        print(f"    Найдено  : {Colors.GREEN}{c_line.strip()}{Colors.RESET}")
                                    else:
                                        print(f"  Строка {idx_s + 1} (удалена):")
                                        print(f"    Ожидалось: {Colors.RED}{s_line.strip()}{Colors.RESET}")
                            elif tag == "delete":
                                for idx_s in range(i1, i2):
                                    s_line = search_lines[idx_s]
                                    print(f"  Строка {idx_s + 1} (отсутствует в файле):")
                                    print(f"    Ожидалось: {Colors.RED}{s_line.strip()}{Colors.RESET}")
                            elif tag == "insert":
                                for idx_c in range(j1, j2):
                                    c_line = candidate_lines[idx_c]
                                    print(f"  Лишняя строка в файле:")
                                    print(f"    Найдено  : {Colors.GREEN}{c_line.strip()}{Colors.RESET}")

                    if stats["char_similarity_pct"] == 100.0 and stats["line_mismatch_pct"] == 0.0:
                        ans = "y"
                        self._log(f"{Colors.GREEN}Сходство 100% (игнорируя отступы). Автоматическое подтверждение.{Colors.RESET}")
                    elif stats.get("code_similarity_pct", 0) == 100.0:
                        ans = "y"
                        self._log(f"{Colors.GREEN}Код совпадает на 100% (различия только в комментариях/отступах). Автоматическое подтверждение.{Colors.RESET}")
                    elif self.auto_mode:
                        # В Auto-mode принимаем только при высоком пороге сходства!
                        if (
                            stats["char_similarity_pct"] >= self.fuzzy_auto_threshold_pct
                            or stats.get("code_similarity_pct", 0) >= 95.0
                        ):
                            ans = "y"
                            self._log(f"{Colors.GREEN}Auto-mode: Высокое сходство ({stats['char_similarity_pct']:.1f}%), блок подтвержден.{Colors.RESET}")
                        else:
                            ans = "n"
                            self._log(
                                f"{Colors.RED}Auto-mode: Сходство {stats['char_similarity_pct']:.1f}% ниже безопасного порога "
                                f"({self.fuzzy_auto_threshold_pct}%). Блок отклонен во избежание порчи кода.{Colors.RESET}"
                            )
                    else:
                        try:
                            ans = input("Подтвердить? [y/n] (Enter=y): ").strip().lower()
                            if not ans:
                                ans = "y"
                        except EOFError:
                            ans = "n"

                    if ans in ["y", "yes", "да", "1"]:
                        matches_for_block.append((path, start, end))
                        break
                    else:
                        if not self.auto_mode:
                            user_skipped_blocks.add(idx)

            if not matches_for_block:
                applied_locs = []
                target_check_files = file_contents
                if file_hint:
                    hint_norm = os.path.normpath(file_hint).lower()
                    hint_base = os.path.basename(hint_norm)
                    filtered = {
                        p: lines for p, lines in file_contents.items()
                        if os.path.normpath(p).lower() == hint_norm
                        or os.path.normpath(p).lower().endswith(os.sep + hint_norm)
                        or os.path.normpath(p).lower().endswith("/" + hint_norm)
                        or os.path.basename(os.path.normpath(p).lower()) == hint_base
                    }
                    if filtered:
                        target_check_files = filtered

                non_empty_replace = [l for l in replace_lines if l.strip()]
                if non_empty_replace:
                    for path, lines in target_check_files.items():
                        found_replacements = find_matches(non_empty_replace, lines)
                        if not found_replacements:
                            found_replacements = find_indent_agnostic_matches(non_empty_replace, lines)
                        for m in found_replacements:
                            applied_locs.append((path, m[0], m[1]))

                # Проверяем, зафиксирован ли данный патч в кэше БД (SKIPPED_CACHED)
                is_cached_patch = False
                if applied_locs or len(replace_lines) == 0:
                    generic_hash = indexer.get_patch_hash(search_lines, replace_lines)
                    if indexer.is_patch_applied(generic_hash):
                        is_cached_patch = True
                    else:
                        for p, _, _ in applied_locs:
                            rel_p = os.path.relpath(p, start=str(self.root_dir))
                            file_hash = indexer.get_patch_hash(search_lines, replace_lines, file_path=rel_p)
                            if indexer.is_patch_applied(file_hash):
                                is_cached_patch = True
                                break

                if is_cached_patch:
                    self._log(f"\n{Colors.GREEN}Блок {idx + 1} уже был применен ранее (пропущен благодаря кэшу БД).{Colors.RESET}")
                    cached_blocks.add(idx)
                    report.block_reports.append(BlockMatchReport(
                        block_index=idx + 1,
                        status=BlockStatus.SKIPPED_CACHED,
                        file_hint=file_hint,
                        target_files=[os.path.relpath(p, start=str(self.root_dir)) for p, _, _ in applied_locs]
                    ))
                elif applied_locs or len(replace_lines) == 0:
                    already_applied_blocks[idx] = applied_locs

            block_matches.append(matches_for_block)

        # 3. Валидация найденных совпадений и проверка на неоднозначность
        errors: List[str] = []
        missing_blocks = 0
        duplicate_blocks = 0

        for idx, matches in enumerate(block_matches):
            if idx in cached_blocks:
                continue

            file_hint = blocks[idx].get("file_hint")

            if len(matches) == 0:
                if idx in user_skipped_blocks:
                    self._log(f"{Colors.YELLOW}Блок {idx + 1} пропущен пользователем.{Colors.RESET}")
                    report.block_reports.append(BlockMatchReport(
                        block_index=idx + 1,
                        status=BlockStatus.SKIPPED_USER,
                        file_hint=file_hint
                    ))
                    continue

                if idx in already_applied_blocks:
                    locs = already_applied_blocks[idx]
                    if locs:
                        locs_str = "\n  - ".join([f"{p} (строки {s + 1}-{e})" for p, s, e in locs])
                        self._log(
                            f"\n{Colors.YELLOW}ВНИМАНИЕ: Для блока {idx + 1} оригинал не найден, "
                            f"но точная копия текста ЗАМЕНЫ уже присутствует в коде:\n  - {locs_str}{Colors.RESET}"
                        )
                        ans = "y" if self.auto_mode else "n"
                        if not self.auto_mode:
                            try:
                                ans = input("Похоже, правки уже внесены. Пропустить этот блок? [y/n] (Enter=y): ").strip().lower()
                                if not ans:
                                    ans = "y"
                            except EOFError:
                                ans = "y"
                        if ans in ["y", "yes", "да", "1"]:
                            report.block_reports.append(BlockMatchReport(
                                block_index=idx + 1,
                                status=BlockStatus.SKIPPED_ALREADY_APPLIED,
                                file_hint=file_hint
                            ))
                            continue
                    else:
                        self._log(f"{Colors.GREEN}Блок {idx + 1} пропущен: блок замены пуст, оригинал уже отсутствует.{Colors.RESET}")
                        report.block_reports.append(BlockMatchReport(
                            block_index=idx + 1,
                            status=BlockStatus.SKIPPED_ALREADY_APPLIED,
                            file_hint=file_hint
                        ))
                        continue

                # Формируем детальный диагностический репорт с ближайшим кандидатом для ИИ
                best_cand = closest_candidate_per_block.get(idx)
                if best_cand is not None:
                    c_path = best_cand["path"]
                    c_start = best_cand["start"]
                    c_end = best_cand["end"]
                    c_stats = best_cand.get("stats") or compute_mismatch_stats(
                        blocks[idx]["search"], file_contents[c_path][c_start:c_end]
                    )
                    sim_pct = c_stats["char_similarity_pct"]
                    snippet = "\n".join(file_contents[c_path][c_start:c_end])
                    err_msg = (
                        f"Блок {idx + 1} отклонен из-за низкого сходства ({sim_pct:.1f}% < {self.fuzzy_auto_threshold_pct}%) "
                        f"в файле {c_path} (строки {c_start + 1}-{c_end})."
                    )
                    report.block_reports.append(BlockMatchReport(
                        block_index=idx + 1,
                        status=BlockStatus.FAILED_LOW_SIMILARITY,
                        file_hint=file_hint,
                        similarity_pct=sim_pct,
                        closest_file=os.path.relpath(c_path, start=str(self.root_dir)),
                        closest_start_line=c_start + 1,
                        closest_end_line=c_end,
                        actual_file_snippet=snippet,
                        error_message=err_msg
                    ))
                else:
                    err_msg = (
                        f"Блок {idx + 1} НЕ НАЙДЕН НИ В ОДНОМ ФАЙЛЕ.\n"
                        f"Искомый фрагмент:\n" + "\n".join(blocks[idx]["search"])
                    )
                    report.block_reports.append(BlockMatchReport(
                        block_index=idx + 1,
                        status=BlockStatus.FAILED_NOT_FOUND,
                        file_hint=file_hint,
                        error_message=err_msg
                    ))

                errors.append(err_msg)
                missing_blocks += 1

            elif len(matches) > 1:
                if prompt_filenames:
                    filtered_matches = []
                    for p, s, e in matches:
                        p_norm = os.path.normpath(p).lower()
                        for pf in prompt_filenames:
                            pf_norm = os.path.normpath(pf).lower()
                            if p_norm == pf_norm or p_norm.endswith(os.sep + pf_norm) or p_norm.endswith("/" + pf_norm):
                                filtered_matches.append((p, s, e))
                                break
                    if len(filtered_matches) == 1:
                        matches = filtered_matches
                        block_matches[idx] = matches

                if len(matches) == 1:
                    p, s, e = matches[0]
                    report.block_reports.append(BlockMatchReport(
                        block_index=idx + 1,
                        status=BlockStatus.APPLIED,
                        file_hint=file_hint,
                        target_files=[os.path.relpath(p, start=str(self.root_dir))],
                        similarity_pct=100.0
                    ))
                    continue

                locs = "\n  - ".join([f"{p} (строки {s + 1}-{e})" for p, s, e in matches])
                self._log(
                    f"\n{Colors.YELLOW}Блок {idx + 1} найден {len(matches)} раз (Неоднозначность!).\n"
                    f"Где найдено:\n  - {locs}{Colors.RESET}"
                )

                if self.auto_mode:
                    if self.allow_multi_match_in_auto:
                        block_matches[idx] = matches
                        report.block_reports.append(BlockMatchReport(
                            block_index=idx + 1,
                            status=BlockStatus.APPLIED,
                            file_hint=file_hint,
                            target_files=[os.path.relpath(p, start=str(self.root_dir)) for p, _, _ in matches],
                            similarity_pct=100.0
                        ))
                    else:
                        ambig_err = (
                            f"Блок {idx + 1} неоднозначен (найден {len(matches)} раз в:\n  - {locs}\n"
                            f"Укажи точный путь к файлу перед блоком <<<< или добавь больше контекстных строк)."
                        )
                        errors.append(ambig_err)
                        duplicate_blocks += 1
                        report.block_reports.append(BlockMatchReport(
                            block_index=idx + 1,
                            status=BlockStatus.FAILED_AMBIGUOUS,
                            file_hint=file_hint,
                            target_files=[os.path.relpath(p, start=str(self.root_dir)) for p, _, _ in matches],
                            error_message=ambig_err
                        ))
                else:
                    try:
                        ans = input(
                            "Применить ко всем? (y - ко всем, 1 - только к первому, s/n - пропустить) [Enter=1]: "
                        ).strip().lower()
                        if not ans:
                            ans = "1"
                    except EOFError:
                        ans = "n"

                    if ans in ["s", "skip", "n", "no", "нет", "пропустить"]:
                        self._log(f"{Colors.YELLOW}Блок {idx + 1} пропущен пользователем.{Colors.RESET}")
                        user_skipped_blocks.add(idx)
                        block_matches[idx] = []
                        report.block_reports.append(BlockMatchReport(
                            block_index=idx + 1,
                            status=BlockStatus.SKIPPED_USER,
                            file_hint=file_hint
                        ))
                    elif ans in ["1", "первый", "first"]:
                        block_matches[idx] = [matches[0]]
                        report.block_reports.append(BlockMatchReport(
                            block_index=idx + 1,
                            status=BlockStatus.APPLIED,
                            file_hint=file_hint,
                            target_files=[os.path.relpath(matches[0][0], start=str(self.root_dir))],
                            similarity_pct=100.0
                        ))
                    else:
                        block_matches[idx] = matches
                        report.block_reports.append(BlockMatchReport(
                            block_index=idx + 1,
                            status=BlockStatus.APPLIED,
                            file_hint=file_hint,
                            target_files=[os.path.relpath(p, start=str(self.root_dir)) for p, _, _ in matches],
                            similarity_pct=100.0
                        ))
            else:
                p, s, e = matches[0]
                report.block_reports.append(BlockMatchReport(
                    block_index=idx + 1,
                    status=BlockStatus.APPLIED,
                    file_hint=file_hint,
                    target_files=[os.path.relpath(p, start=str(self.root_dir))],
                    similarity_pct=100.0
                ))

        if errors:
            self._log(f"\n{Colors.RED}{'=' * 60}")
            self._log("КРИТИЧЕСКАЯ ОШИБКА: ОТМЕНА ВСЕХ ПРАВОК.")
            if missing_blocks:
                self._log(f"Не найдено / отклонено блоков: {missing_blocks}")
            if duplicate_blocks:
                self._log(f"Неоднозначных блоков: {duplicate_blocks}")
            self._log(f"{'=' * 60}{Colors.RESET}")
            for err in errors:
                self._log(f"{Colors.RED}{err}{Colors.RESET}")
                self._log(f"{Colors.YELLOW}{'-' * 60}{Colors.RESET}")

            if tm._active_tx_id is not None:
                tm.undo_last(indexer=indexer, quiet=self.quiet)

            report.errors.extend(errors)
            report.duration_seconds = time.time() - start_time
            return report

        # 4. Сборка модификаций с авто-коррекцией отступов (rebase_indentation)
        file_modifications: Dict[str, List[Dict[str, Any]]] = {f: [] for f in file_contents}
        applied_patch_hashes: List[str] = []

        for idx, matches in enumerate(block_matches):
            if not matches:
                continue
            p_hash_gen = indexer.get_patch_hash(blocks[idx]["search"], blocks[idx]["replace"])
            if p_hash_gen not in applied_patch_hashes:
                applied_patch_hashes.append(p_hash_gen)

            for match in matches:
                path, start_idx, end_idx = match
                rel_p = os.path.relpath(path, start=str(self.root_dir))
                p_hash_file = indexer.get_patch_hash(blocks[idx]["search"], blocks[idx]["replace"], file_path=rel_p)
                if p_hash_file not in applied_patch_hashes:
                    applied_patch_hashes.append(p_hash_file)

                matched_slice = file_contents[path][start_idx:end_idx]
                rebased_replace = rebase_indentation(
                    matched_file_lines=matched_slice,
                    search_lines=blocks[idx]["search"],
                    replace_lines=blocks[idx]["replace"]
                )
                file_modifications[path].append({
                    "start": start_idx,
                    "end": end_idx,
                    "replace": rebased_replace,
                    "search": blocks[idx]["search"],
                    "block_idx": idx + 1
                })
            report.blocks_applied += 1

        # Проверка на наложение (overlap) блоков в одном файле
        overlap_errors: List[str] = []
        for path, mods in file_modifications.items():
            if len(mods) > 1:
                mods_sorted = sorted(mods, key=lambda x: (x["start"], x["end"]))
                for k in range(len(mods_sorted) - 1):
                    m1 = mods_sorted[k]
                    m2 = mods_sorted[k + 1]
                    if m1["end"] > m2["start"]:
                        rel_p = os.path.relpath(path, start=str(self.root_dir))
                        err_msg = (
                            f"Конфликт наложения блоков в файле '{rel_p}': блок #{m1['block_idx']} "
                            f"(строки {m1['start'] + 1}-{m1['end']}) перекрывается с блоком #{m2['block_idx']} "
                            f"(строки {m2['start'] + 1}-{m2['end']}). Объедините эти изменения в один блок замены."
                        )
                        overlap_errors.append(err_msg)
                        for br in report.block_reports:
                            if br.block_index in (m1["block_idx"], m2["block_idx"]):
                                br.status = BlockStatus.FAILED_AMBIGUOUS
                                br.error_message = err_msg

        if overlap_errors:
            self._log(f"\n{Colors.RED}{'=' * 60}")
            self._log("КРИТИЧЕСКАЯ ОШИБКА: ОБНАРУЖЕНО ПЕРЕСЕЧЕНИЕ БЛОКОВ ЗАМЕНЫ.")
            self._log(f"{'=' * 60}{Colors.RESET}")
            for err in overlap_errors:
                self._log(f"{Colors.RED}{err}{Colors.RESET}")
                self._log(f"{Colors.YELLOW}{'-' * 60}{Colors.RESET}")

            if tm._active_tx_id is not None:
                tm.undo_last(indexer=indexer, quiet=self.quiet)

            report.errors.extend(overlap_errors)
            report.duration_seconds = time.time() - start_time
            return report

        pending_replacements: List[Tuple[str, List[str], str, str]] = []

        for path, mods in file_modifications.items():
            if not mods:
                continue

            mods.sort(key=lambda x: x["start"], reverse=True)
            lines = file_contents[path][:]

            for mod in mods:
                start = mod["start"]
                end = mod["end"]
                replace_lines = mod["replace"]
                lines = lines[:start] + replace_lines + lines[end:]

            enc_info = file_encodings.get(path, ("utf-8", "\n"))
            enc, newline_char = enc_info if isinstance(enc_info, tuple) else (enc_info, "\n")

            if "utf-16" in enc.lower():
                self._log(f"\n{Colors.YELLOW}Файл {path} обнаружен в кодировке {enc}.{Colors.RESET}")
                ans = "y" if self.auto_mode else "n"
                if not self.auto_mode:
                    try:
                        ans = input("Перезаписать в UTF-8? [y/n] (Enter=y): ").strip().lower()
                        if not ans:
                            ans = "y"
                    except EOFError:
                        ans = "y"
                if ans in ["y", "yes", "да", "1"]:
                    enc = "utf-8"
                else:
                    continue

            pending_replacements.append((path, lines, enc, newline_char))

        if not pending_replacements:
            report.success = True
            report.transaction_id = tm._active_tx_id
            report.duration_seconds = time.time() - start_time
            return report

        # 5. Создание бэкапа транзакции
        try:
            changed_paths = [d[0] for d in pending_replacements]
            tx_id = tm.backup_files(changed_paths, patch_hashes=applied_patch_hashes)
            report.transaction_id = tx_id
            self._log(f"{Colors.GREEN}Создана резервная копия транзакции: tx_{tx_id}{Colors.RESET}")
        except Exception as e:
            err_str = f"Ошибка создания резервной копии: {e}"
            self._log(f"{Colors.RED}{err_str}{Colors.RESET}")
            report.errors.append(err_str)
            report.duration_seconds = time.time() - start_time
            return report

        # 6. Строгая проверка синтаксиса (Dry Linter) ДО записи на диск
        for path, lines, enc, newline_char in pending_replacements:
            content = newline_char.join(lines) + newline_char
            is_valid, err_msg = run_dry_linter(path, content)
            if not is_valid:
                rel_p = os.path.relpath(path, start=str(self.root_dir))
                report.linter_errors[rel_p] = err_msg
                self._log(f"\n{Colors.RED}Синтаксическая ошибка в файле {path}:{Colors.RESET}")
                self._log(f"{Colors.YELLOW}{err_msg}{Colors.RESET}")

                if self.auto_mode:
                    self._log(f"{Colors.RED}Auto-mode: Запись заблокирована из-за синтаксической ошибки. Выполняется откат...{Colors.RESET}")
                    tm.undo_last(indexer=indexer, quiet=self.quiet)
                    report.errors.append(f"Синтаксическая ошибка в {rel_p}: {err_msg}")
                    report.duration_seconds = time.time() - start_time
                    return report
                else:
                    try:
                        ans = input("Игнорировать синтаксическую ошибку и все равно сохранить? [y/N]: ").strip().lower()
                    except EOFError:
                        ans = "n"
                    if ans not in ["y", "yes", "да", "1"]:
                        self._log(f"{Colors.RED}Запись отменена пользователем. Выполняется откат...{Colors.RESET}")
                        tm.undo_last(indexer=indexer, quiet=self.quiet)
                        report.errors.append(f"Синтаксическая ошибка в {rel_p}: {err_msg}")
                        report.duration_seconds = time.time() - start_time
                        return report

        # 7. Атомарная запись всех файлов
        def safe_atomic_write(file_data: Tuple[str, List[str], str, str]) -> str:
            path, lines, enc, newline_char = file_data
            content = newline_char.join(lines) + newline_char
            tmp_path = f"{path}.{secrets.token_hex(4)}.tmp_write"
            with open(tmp_path, "w", encoding=enc, newline="") as f:
                f.write(content)
            os.replace(tmp_path, path)
            return path

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=32) as executor:
                futures = [executor.submit(safe_atomic_write, d) for d in pending_replacements]
                for future in concurrent.futures.as_completed(futures):
                    path = future.result()
                    rel_p = os.path.relpath(path, start=str(self.root_dir))
                    if rel_p not in report.modified_files:
                        report.modified_files.append(rel_p)
                    self._log(f"Обновлен файл: {Colors.YELLOW}{path}{Colors.RESET}")

            for p_hash in applied_patch_hashes:
                indexer.mark_patch_applied(p_hash)
        except Exception as e:
            err_str = f"КРИТИЧЕСКАЯ ОШИБКА ПРИ ЗАПИСИ: {e}"
            self._log(f"{Colors.RED}{err_str}{Colors.RESET}")
            self._log(f"{Colors.YELLOW}Транзакция прервана! Выполняется откат изменений...{Colors.RESET}")
            tm.undo_last(indexer=indexer, quiet=self.quiet)
            report.errors.append(err_str)
            report.duration_seconds = time.time() - start_time
            return report

        # Актуализируем базу SQLite для всех измененных файлов
        indexer.sync_and_read(self.ignore_folders, self.ignore_files, prompt_filenames, quiet=self.quiet)

        report.success = True
        report.duration_seconds = time.time() - start_time

        self._log(f"\n{Colors.GREEN}{'=' * 60}")
        self._log("Правки успешно применены.")
        self._log(f"Всего обработано блоков: {len(blocks)} (применено: {report.blocks_applied})")
        self._log(f"Изменено файлов: {len(report.modified_files)}")
        self._log(f"{'=' * 60}{Colors.RESET}")

        if COUNT_TIME and report.duration_seconds > 5:
            self._log(f"{Colors.YELLOW}Время выполнения: {report.duration_seconds:.2f} сек.{Colors.RESET}")

        return report


# ============================================================================
# ПУБЛИЧНЫЕ ФУНКЦИИ МОДУЛЯ (УДОБНЫЙ ФАСАД ДЛЯ ИМПОРТА)
# ============================================================================

def apply_llm_file(
    file_path: Union[str, Path],
    root_dir: Union[str, Path] = ".",
    auto_mode: bool = True,
    disable_all_except_write: bool = True,
    quiet: bool = False
) -> ReplacerExecutionReport:
    """
    Применяет текстовый файл с ответом LLM к указанной директории проекта.
    Возвращает структурированный объект ReplacerExecutionReport.
    """
    api = ReplacerAPI(
        root_dir=root_dir,
        auto_mode=auto_mode,
        disable_all_except_write=disable_all_except_write,
        quiet=quiet
    )
    return api.apply_file(file_path)


def apply_llm_text(
    llm_text: str,
    root_dir: Union[str, Path] = ".",
    auto_mode: bool = True,
    disable_all_except_write: bool = True,
    quiet: bool = False
) -> ReplacerExecutionReport:
    """
    Применяет строку с ответом LLM к указанной директории проекта.
    Возвращает структурированный объект ReplacerExecutionReport.
    """
    api = ReplacerAPI(
        root_dir=root_dir,
        auto_mode=auto_mode,
        disable_all_except_write=disable_all_except_write,
        quiet=quiet
    )
    return api.apply_text(llm_text)


# ============================================================================
# ОБРАТНАЯ СОВМЕСТИМОСТЬ С LEGACY-ФУНКЦИЯМИ (main / run_replacer)
# ============================================================================

def main(
    ignore_folders: Optional[List[str]] = None,
    ignore_files: Optional[List[str]] = None,
    input_lines: Optional[List[str]] = None,
    auto_mode: bool = False,
    disable_all_except_write: bool = True,
    root_dir: str = "."
) -> bool:
    api = ReplacerAPI(
        root_dir=root_dir,
        ignore_folders=ignore_folders,
        ignore_files=ignore_files,
        auto_mode=auto_mode,
        disable_all_except_write=disable_all_except_write,
        quiet=False
    )
    if input_lines is None:
        blocks, input_lines = parse_input()
        if blocks == "UNDO":
            return api.undo().success

    report = api.apply_lines(input_lines)
    return report.success


def run_replacer(
    ignore_folders: Optional[List[str]] = None,
    ignore_files: Optional[List[str]] = None,
    check_clipboard: bool = False,
    skip_clipboard_confirm: bool = False,
    auto_mode: bool = False,
    disable_all_except_write: bool = True,
    root_dir: str = ".",
    copy_feedback_on_error: bool = False
) -> None:
    effective_auto = auto_mode or skip_clipboard_confirm

    if check_clipboard:
        active_proc = check_active_clipboard_process()
        if active_proc:
            print(f"\n{Colors.RED}{'=' * 60}")
            print("КРИТИЧЕСКАЯ ОШИБКА: Другой replacer уже запущен с параметром check_clipboard=True!")
            print(f"PID: {active_proc['pid']}")
            print(f"Директория: {active_proc['dir']}")
            print("Работа завершена во избежание применения правок не в тот проект.")
            print(f"{'=' * 60}{Colors.RESET}")
            sys.exit(1)

        acquire_clipboard_lock()
        atexit.register(release_clipboard_lock)
        print(f"{Colors.GREEN}Режим мониторинга буфера обмена включен (проверка каждую 1 сек).{Colors.RESET}")
        print(f"{Colors.CYAN}Целевая папка проекта: {os.path.abspath(root_dir)}{Colors.RESET}")
        if skip_clipboard_confirm:
            print(f"{Colors.YELLOW}Режим без подтверждения (Auto-mode) активен.{Colors.RESET}")
        if disable_all_except_write:
            print(f"{Colors.GREEN}Политика безопасности: разрешены только патчи и запись файлов (Zero-Shell).{Colors.RESET}")

        last_clip_hash = None
        init_text = get_clipboard_content()
        if init_text and not has_actionable_markers(init_text):
            last_clip_hash = hashlib.md5(init_text.encode("utf-8", errors="ignore")).hexdigest()

        api = ReplacerAPI(
            root_dir=root_dir,
            ignore_folders=ignore_folders,
            ignore_files=ignore_files,
            auto_mode=effective_auto,
            disable_all_except_write=disable_all_except_write,
            quiet=False
        )

        try:
            while True:
                try:
                    clip_text = get_clipboard_content()
                    if clip_text:
                        clip_hash = hashlib.md5(clip_text.encode("utf-8", errors="ignore")).hexdigest()
                        if clip_hash != last_clip_hash:
                            last_clip_hash = clip_hash
                            if has_actionable_markers(clip_text):
                                lines = [l + "\n" for l in clip_text.splitlines()]
                                if not skip_clipboard_confirm:
                                    print(f"\n{Colors.YELLOW}Обнаружены новые правки в буфере обмена.{Colors.RESET}")
                                    try:
                                        ans = input("Применить правки? [y/n] (Enter=y): ").strip().lower()
                                        if not ans:
                                            ans = "y"
                                    except EOFError:
                                        ans = "n"
                                    if ans not in ["y", "yes", "да", "1", ".,,,"]:
                                        print(f"{Colors.YELLOW}Правки из буфера обмена пропущены.{Colors.RESET}")
                                        time.sleep(1)
                                        continue

                                print(f"\n{Colors.GREEN}Обработка правок из буфера обмена...{Colors.RESET}")
                                report = api.apply_lines(lines)
                                if not report.success and copy_feedback_on_error:
                                    feedback_str = report.to_llm_feedback()
                                    if set_clipboard_content(feedback_str):
                                        last_clip_hash = hashlib.md5(
                                            feedback_str.encode("utf-8", errors="ignore")
                                        ).hexdigest()
                                        print(
                                            f"{Colors.CYAN}[АГЕНТ] Диагностический отчет об ошибке скопирован "
                                            f"в буфер обмена для вставки в LLM.{Colors.RESET}"
                                        )
                    time.sleep(1)
                except KeyboardInterrupt:
                    raise
                except Exception as e:
                    print(f"{Colors.RED}Ошибка обработки буфера обмена: {e}{Colors.RESET}")
                    time.sleep(1)
        except KeyboardInterrupt:
            print(f"\n{Colors.YELLOW}Операция прервана пользователем.{Colors.RESET}")
        finally:
            release_clipboard_lock()
    else:
        while True:
            try:
                main(
                    ignore_folders=ignore_folders,
                    ignore_files=ignore_files,
                    auto_mode=effective_auto,
                    disable_all_except_write=disable_all_except_write,
                    root_dir=root_dir
                )
            except KeyboardInterrupt:
                print(f"\n{Colors.YELLOW}Операция прервана пользователем.{Colors.RESET}")
                break


# ============================================================================
# CLI ИНТЕРФЕЙС (ARGPARSE)
# ============================================================================

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="DeepSearch Replacer — безопасный модуль и CLI для применения ответов LLM (Search/Replace + Fast-Path Write)."
    )
    parser.add_argument(
        "input_file",
        nargs="?",
        help="Путь к текстовому файлу с ответом LLM (опционально; используйте '-' для чтения из stdin)."
    )
    parser.add_argument(
        "-f", "--file",
        dest="file_opt",
        help="Путь к текстовому файлу с ответом LLM."
    )
    parser.add_argument(
        "-t", "--text",
        dest="raw_text",
        help="Прямая передача строки с ответом LLM через аргумент."
    )
    parser.add_argument(
        "-d", "--dir", "--root-dir",
        dest="root_dir",
        default=".",
        help="Корневая директория целевого проекта (по умолчанию: текущая папка '.')."
    )
    parser.add_argument(
        "-y", "--auto",
        dest="auto_mode",
        action="store_true",
        help="Автоматический неинтерактивный режим (Auto-mode)."
    )
    parser.add_argument(
        "-c", "--clipboard",
        dest="clipboard",
        action="store_true",
        help="Запустить в режиме фонового мониторинга буфера обмена."
    )
    parser.add_argument(
        "--interactive-clipboard",
        dest="interactive_clipboard",
        action="store_true",
        help="Запрашивать подтверждение перед применением правок из буфера обмена."
    )
    parser.add_argument(
        "--undo",
        action="store_true",
        help="Откатить последнюю транзакцию (восстановить измененные файлы и удалить созданные)."
    )
    parser.add_argument(
        "--allow-ps",
        dest="allow_ps",
        action="store_true",
        help="Разрешить выполнение императивных скриптов PowerShell в изолированной песочнице (по умолчанию разрешена только запись файлов)."
    )
    parser.add_argument(
        "--unsafe-direct-ps",
        dest="unsafe_direct_ps",
        action="store_true",
        help="Разрешить прямой запуск PowerShell без Job Object песочницы (НЕ РЕКОМЕНДУЕТСЯ)."
    )
    parser.add_argument(
        "--allow-multi-match",
        dest="allow_multi_match",
        action="store_true",
        help="В авто-режиме разрешить применение блока ко всем найденным совпадениям при неоднозначности."
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Вывести итоговый отчет выполнения в формате JSON в stdout."
    )
    parser.add_argument(
        "--feedback-file",
        dest="feedback_file",
        help="Путь к файлу, куда будет сохранен диагностический ответ (feedback) для ИИ-агента."
    )
    parser.add_argument(
        "--copy-feedback",
        dest="copy_feedback",
        action="store_true",
        help="При ошибке автоматически копировать диагностический отчет для LLM обратно в буфер обмена."
    )
    return parser


def cli_entry_point() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()

    target_file = args.file_opt or args.input_file
    disable_all_except_write = not (args.allow_ps or args.unsafe_direct_ps)
    quiet_mode = bool(args.json_output)

    # Формируем динамический список исключений: входной файл промпта и файл фидбека
    dynamic_ignore_files = [DB_PATH, CLIPBOARD_LOCK_FILE]
    if target_file and target_file != "-":
        dynamic_ignore_files.append(Path(target_file).name)
    if args.feedback_file:
        dynamic_ignore_files.append(Path(args.feedback_file).name)

    api = ReplacerAPI(
        root_dir=args.root_dir,
        ignore_folders=list(IGNORE_DIRS),
        ignore_files=dynamic_ignore_files,
        auto_mode=args.auto_mode,
        disable_all_except_write=disable_all_except_write,
        unsafe_direct_ps=args.unsafe_direct_ps,
        allow_multi_match_in_auto=args.allow_multi_match,
        quiet=quiet_mode
    )

    # 1. Откат последней транзакции (--undo)
    if args.undo:
        report = api.undo()
        if args.json_output:
            print(report.to_json())
        sys.exit(0 if report.success else 1)

    # 2. Обработка конкретного файла (-f / позиционный аргумент) или текста (-t)
    if target_file or args.raw_text is not None:
        if args.raw_text is not None:
            report = api.apply_text(args.raw_text)
        elif target_file == "-":
            stdin_text = sys.stdin.read()
            report = api.apply_text(stdin_text)
        else:
            report = api.apply_file(target_file)

        if args.feedback_file:
            Path(args.feedback_file).write_text(report.to_llm_feedback(), encoding="utf-8")

        if not report.success and args.copy_feedback:
            set_clipboard_content(report.to_llm_feedback())

        if args.json_output:
            print(report.to_json())

        sys.exit(0 if report.success else 1)

    # 3. Если запущен с флагом --clipboard или вообще без аргументов (поведение по умолчанию)
    use_clipboard = args.clipboard or (len(sys.argv) == 1)
    skip_confirm = (not args.interactive_clipboard) if use_clipboard else args.auto_mode

    run_replacer(
        ignore_folders=list(IGNORE_DIRS),
        ignore_files=[DB_PATH, CLIPBOARD_LOCK_FILE],
        check_clipboard=use_clipboard,
        skip_clipboard_confirm=skip_confirm,
        auto_mode=args.auto_mode,
        disable_all_except_write=disable_all_except_write,
        root_dir=args.root_dir,
        copy_feedback_on_error=args.copy_feedback
    )


if __name__ == "__main__":
    cli_entry_point()


"""
================================================================================
РУКОВОДСТВО ПО БЕЗОПАСНОМУ ИСПОЛЬЗОВАНИЮ (SECURITY & SAFE USAGE GUIDE)
================================================================================

1. ЗАЩИТА ОТ ЗАПИСИ НЕ В ТУ ПАПКУ (DIRECTORY ISOLATION & PATH TRAVERSAL):
   - По умолчанию replacer работает строго внутри текущей рабочей директории (`.`).
   - Встроенный модуль `SecurePathResolver` принудительно блокирует любые попытки
     выхода за пределы корня проекта (`../`, абсолютные пути на другие диски,
     симлинки за пределы проекта, NTFS Alternate Data Streams и системные имена CON/NUL).
   - ПРАВИЛО: При вызове из сторонних скриптов или глобального терминала ВСЕГДА
     явно указывайте целевую папку проекта через флаг `-d` (`--dir`):
     `python replacer.py -d "C:/Projects/MyTargetApp" -f response.txt -y`
   - В режиме мониторинга буфера обмена (`--clipboard`) действует межпроцессная
     блокировка (`deepsearch_replacer_clipboard.lock`). Невозможно случайно запустить
     два фоновых процесса в разных папках, чтобы патч применился не в тот проект.

2. БЕЗОПАСНЫЙ ВЫЗОВ ИЗ CMD / POWERSHELL (ЗАЩИТА ОТ COMMAND INJECTION):
   - КАТЕГОРИЧЕСКИ НЕ РЕКОМЕНДУЕТСЯ передавать сырой многострочный ответ нейросети
     напрямую через аргумент командной строки `-t "..."` в оболочках `cmd.exe` или `bash`!
     Спецсимволы из кода LLM (`&`, `|`, `>`, `<`, `%VAR%`, `$()`, `^`, кавычки)
     будут интерпретированы самой командной оболочкой ОС ДО запуска Python, что
     приведет к выполнению произвольных команд или порче данных.
   - БЕЗОПАСНЫЕ СПОСОБЫ ПЕРЕДАЧИ ДАННЫХ:
     а) Через файл (рекомендуется для агентов и CLI):
        `python replacer.py -d ./my_project -f llm_response.txt -y --json`
     б) Через поток стандартного ввода (stdin pipe) из другого процесса без шелла:
        `python replacer.py -d ./my_project - -y --json` (с передачей `input=` в `subprocess.run`)
     в) Через прямое API внутри Python (без создания подпроцессов ОС):
        `report = apply_llm_text(llm_text, root_dir="./my_project", auto_mode=True)`

3. ПОЛИТИКА ZERO-SHELL (ЗАЩИТА ОТ RCE ПРИ СОЗДАНИИ ФАЙЛОВ):
   - По умолчанию активен режим `disable_all_except_write=True` (флаги `--allow-ps`
     и `--unsafe-direct-ps` ВЫКЛЮЧЕНЫ).
   - В этом режиме replacer НЕ ЗАПУСКАЕТ процесс PowerShell или cmd.exe.
     Даже если LLM пришлет команду создания файла на PowerShell (`$content = @'...'@`),
     модуль `ZeroShellTranspiler` разберет её регулярными выражениями и выполнит
     запись средствами нативного Python. Любые сторонние команды (сеть, реестр,
     удаление файлов, запуск процессов) будут мгновенно заблокированы.
   - Рекомендуется в системном промпте для LLM использовать декларативный блок:
     `<<<< CREATE: path/to/new_file.py` ... `>>>>` вместо генерации PowerShell-скриптов.
   - Никогда не используйте флаг `--unsafe-direct-ps` на ответах от недоверенных LLM.

4. ТРАНЗАКЦИОННОСТЬ, СИНТАКСИЧЕСКИЙ КОНТРОЛЬ И ОТКАТ (--undo):
   - Перед изменением любых файлов создается архив транзакции в `.deepsearch/history/`.
   - Модуль `run_dry_linter` проверяет синтаксис кода ДО записи на диск. В режиме
     `--auto` (`-y`) при любой синтаксической ошибке запись блокируется, а транзакция
     автоматически откатывается.
   - Для полной отмены последней транзакции (восстановление старых версий файлов
     и автоматическое удаление всех новых файлов, созданных агентом) выполните:
     `python replacer.py -d ./my_project --undo` (или отправьте маркер `,!!!`).
================================================================================
"""
