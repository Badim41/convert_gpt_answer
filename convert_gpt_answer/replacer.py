import ast
import atexit
import concurrent.futures
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import datetime
import difflib
from enum import Enum
import hashlib
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
import threading
import time
from typing import Dict, Final, FrozenSet, List, Optional, Protocol, Set, Tuple, Union
import zipfile

try:
    import tkinter as tk
except ImportError:
    tk = None


# ============================================================================
# РАЗДЕЛ БЕЗОПАСНОСТИ: ДОМЕННЫЕ ИСКЛЮЧЕНИЯ И ТИПЫ (ЭТАПЫ 1 И 2)
# ============================================================================

class PowerShellSecurityException(Exception):
    """Базовое исключение подсистемы безопасности PowerShell."""
    pass


class PathTraversalSecurityError(PowerShellSecurityException):
    """Попытка выхода за пределы доверенной директории проекта."""
    pass


class TranspilerSyntaxError(PowerShellSecurityException):
    """Невозможность безопасного синтаксического разбора команды."""
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
    REJECTED = "REJECTED"


class PowerShellLanguageMode(str, Enum):
    CONSTRAINED = "ConstrainedLanguage"
    FULL = "FullLanguage"


class FileWriteMode(str, Enum):
    OVERWRITE = "OVERWRITE"
    APPEND = "APPEND"
    MKDIR = "MKDIR"


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

        candidate_path = Path(clean_path_str)
        resolved_root = root_dir.resolve()

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

        for part in resolved_target.parts:
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

    # Регулярные выражения для heredoc синтаксиса PowerShell
    HEREDOC_PATTERN: Final[re.Pattern[str]] = re.compile(
        r"^\s*\$content\s*=\s*@(['\"])\r?\n(?P<content>[\s\S]*?)\r?\n\1@\s*$",
        re.MULTILINE
    )

    IO_FILE_WRITE_PATTERN: Final[re.Pattern[str]] = re.compile(
        r"\[System\.IO\.File\]::WriteAllText\(\s*[\"'](?P<path>[^\"']+)[\"']\s*,\s*\$content(?:\s*,\s*(?P<enc>[^\)]+))?\s*\)",
        re.IGNORECASE
    )

    IO_FILE_APPEND_PATTERN: Final[re.Pattern[str]] = re.compile(
        r"\[System\.IO\.File\]::AppendAllText\(\s*[\"'](?P<path>[^\"']+)[\"']\s*,\s*\$content(?:\s*,\s*(?P<enc>[^\)]+))?\s*\)",
        re.IGNORECASE
    )

    OUT_FILE_PIPELINE_PATTERN: Final[re.Pattern[str]] = re.compile(
        r"\$content\s*\|\s*(?:Out-File|Set-Content)\s+(?:-(?:FilePath|Path)\s+)?[\"']?(?P<path>[^\"'\s]+)[\"']?(?:\s+-Encoding\s+(?P<enc>\w+))?(?:\s+(?P<append>-Append))?",
        re.IGNORECASE
    )

    OUT_FILE_DIRECT_PATTERN: Final[re.Pattern[str]] = re.compile(
        r"(?:Out-File|Set-Content)\s+(?:-(?:FilePath|Path)\s+)?[\"']?(?P<path>[^\"'\s]+)[\"']?\s+-InputObject\s+\$content(?:\s+-Encoding\s+(?P<enc>\w+))?(?:\s+(?P<append>-Append))?",
        re.IGNORECASE
    )

    # Паттерны создания директорий в пределах проекта
    MKDIR_COMMAND_PATTERN: Final[re.Pattern[str]] = re.compile(
        r"^\s*(?:mkdir|md)\s+[\"']?(?P<path>[^\"'\r\n;]+)[\"']?\s*(?:-Force)?\s*;?\s*$",
        re.IGNORECASE
    )

    NEW_ITEM_DIR_PATTERN: Final[re.Pattern[str]] = re.compile(
        r"^\s*New-Item\s+(?:-(?:ItemType|Type)\s+(?:Directory|dir)\s+-(?:Path|Name)\s+[\"']?(?P<path1>[^\"'\s;]+)[\"']?|-(?:Path|Name)\s+[\"']?(?P<path2>[^\"'\s;]+)[\"']?\s+-(?:ItemType|Type)\s+(?:Directory|dir))\s*(?:-Force)?\s*;?\s*$",
        re.IGNORECASE
    )

    IO_DIR_CREATE_PATTERN: Final[re.Pattern[str]] = re.compile(
        r"^\s*\[System\.IO\.Directory\]::CreateDirectory\(\s*[\"'](?P<path>[^\"']+)[\"']\s*\)\s*;?\s*$",
        re.IGNORECASE
    )

    @classmethod
    def assess(cls, raw_script: str, root_dir: Path) -> TranspilationAssessment:
        normalized_script = raw_script.strip()

        # 1. Проверка на создание папок (mkdir / New-Item -ItemType Directory)
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
        normalized_script = raw_script.strip()
        heredoc_match = cls.HEREDOC_PATTERN.search(normalized_script)

        if not heredoc_match:
            return TranspilationAssessment(
                is_transpilable=False,
                operation=None,
                rejection_reason="Отсутствует стандартная декларация $content = @'...' heredoc.",
                raw_command=raw_script
            )

        extracted_content = heredoc_match.group("content")
        preamble_and_trailer = cls.HEREDOC_PATTERN.sub("", normalized_script).strip()

        # Разделяем оставшиеся команды и удаляем пустые строки
        remaining_statements = [
            stmt.strip() for stmt in preamble_and_trailer.splitlines() if stmt.strip()
        ]

        # Удаление безопасной инициализации UTF8Encoding без BOM: $utf8NoBom = New-Object ...
        clean_statements: List[str] = []
        has_utf8_no_bom_helper = False
        for stmt in remaining_statements:
            if re.match(r"^\$utf8nobom\s*=\s*new-object\s+system\.text\.utf8encoding\s+\$false\s*;?$", stmt,
                        re.IGNORECASE):
                has_utf8_no_bom_helper = True
                continue
            clean_statements.append(stmt)

        if len(clean_statements) != 1:
            return TranspilationAssessment(
                is_transpilable=False,
                operation=None,
                rejection_reason=f"Обнаружено недопустимое количество инструкций вне heredoc ({len(clean_statements)} шт.).",
                raw_command=raw_script
            )

        file_statement = clean_statements[0]
        mode = FileWriteMode.OVERWRITE
        detected_path: Optional[str] = None
        detected_encoding = "utf-8"

        io_write_match = cls.IO_FILE_WRITE_PATTERN.search(file_statement)
        io_append_match = cls.IO_FILE_APPEND_PATTERN.search(file_statement)
        pipe_match = cls.OUT_FILE_PIPELINE_PATTERN.search(file_statement)
        direct_match = cls.OUT_FILE_DIRECT_PATTERN.search(file_statement)

        if io_write_match:
            detected_path = io_write_match.group("path")
            enc_raw = io_write_match.group("enc")
            if enc_raw and "$utf8nobom" in enc_raw.lower() and has_utf8_no_bom_helper:
                detected_encoding = "utf-8"
            elif enc_raw:
                detected_encoding = cls._normalize_encoding(enc_raw)
        elif io_append_match:
            detected_path = io_append_match.group("path")
            mode = FileWriteMode.APPEND
            enc_raw = io_append_match.group("enc")
            if enc_raw and "$utf8nobom" in enc_raw.lower() and has_utf8_no_bom_helper:
                detected_encoding = "utf-8"
            elif enc_raw:
                detected_encoding = cls._normalize_encoding(enc_raw)
        elif pipe_match:
            detected_path = pipe_match.group("path")
            if pipe_match.group("append"):
                mode = FileWriteMode.APPEND
            if pipe_match.group("enc"):
                detected_encoding = cls._normalize_encoding(pipe_match.group("enc"))
        elif direct_match:
            detected_path = direct_match.group("path")
            if direct_match.group("append"):
                mode = FileWriteMode.APPEND
            if direct_match.group("enc"):
                detected_encoding = cls._normalize_encoding(direct_match.group("enc"))
        else:
            return TranspilationAssessment(
                is_transpilable=False,
                operation=None,
                rejection_reason=f"Инструкция {file_statement!r} не соответствует безопасным паттернам записи файлов.",
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

        if operation.mode == FileWriteMode.APPEND and target.exists():
            with open(target, "a", encoding=operation.encoding, newline="") as f:
                f.write(operation.content)
            return

        tmp_file = target.with_name(f"{target.name}.{secrets.token_hex(4)}.tmp_write")
        try:
            with open(tmp_file, "w", encoding=operation.encoding, newline="") as f:
                f.write(operation.content)
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
    """POSIX-реализация изоляции через группы процессов (Process Groups)."""

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

        # 1. Приоритет: Современный PowerShell 7+ (pwsh.exe)
        program_files_list = [
            os.environ.get("ProgramFiles", "C:\\Program Files"),
            os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")
        ]
        for pf in program_files_list:
            candidate = Path(pf) / "PowerShell" / "7" / "pwsh.exe"
            if candidate.is_file():
                return candidate

        # 2. Системный Windows PowerShell 5.1 в System32
        system32_ps = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        if system32_ps.is_file():
            return system32_ps

        raise PowerShellSecurityException(
            "Доверенный исполняемый файл PowerShell не найден в системных каталогах Windows. Запуск через PATH запрещен."
        )


# ============================================================================
# ЭТАП 3: ЛЕКСИЧЕСКИЙ АУДИТ, ZERO-TRUST ШЛЮЗ И ИЗОЛИРОВАННЫЙ ИСПОЛНИТЕЛЬ (SLOW-PATH)
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
        "add-type"
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
    ]

    @classmethod
    def audit(cls, raw_script: str) -> SecurityAuditReport:
        script_sha256 = hashlib.sha256(raw_script.encode("utf-8", errors="replace")).hexdigest()

        # 1. Удаление однострочных и многострочных комментариев
        clean_script = re.sub(r"<#[\s\S]*?#>", "", raw_script)
        clean_script = re.sub(r"#[^\r\n]*", "", clean_script)

        # 2. Деобфускация обратных апострофов (backticks), применяемых для сокрытия имен команд
        deobfuscated = clean_script.replace("`", "")

        violations: List[str] = []
        detected_tokens: List[str] = []

        # 3. Поиск запрещенных команд и псевдонимов по границам слов
        tokens = re.findall(r"\b[a-zA-Z][a-zA-Z0-9_-]*\b", deobfuscated)
        for token in tokens:
            token_lower = token.lower()
            if token_lower in cls.FORBIDDEN_CMDLETS:
                msg = f"Запрещенная команда или алиас: {token!r}"
                violations.append(msg)
                detected_tokens.append(token)

        # 4. Поиск запрещенных вызовов .NET API, рефлексии и системных операций
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
    """Шлюз авторизации выполнения императивных скриптов с блокировкой авто-режима."""

    @staticmethod
    def verify_execution_permission(
            script: str,
            audit_report: SecurityAuditReport,
            is_auto: bool
    ) -> bool:
        if not audit_report.is_safe:
            print(f"\n{Colors.RED}{'=' * 60}")
            print("БЛОКИРОВКА БЕЗОПАСНОСТИ: В скрипте обнаружены запрещенные операции:")
            for v in audit_report.detected_violations:
                print(f"  - {v}")
            print(f"{'=' * 60}{Colors.RESET}")
            raise PolicyViolationError(
                f"Скрипт отклонен из-за обнаружения запрещенных инструкций: {audit_report.detected_violations}"
            )

        # Блокировка автоматического исполнения императивного кода
        if is_auto:
            print(f"\n{Colors.RED}{'=' * 60}")
            print("ОТКАЗ В АВТО-ИСПОЛНЕНИИ:")
            print(
                "Выполнение императивного скрипта PowerShell в авто-режиме (--auto / -y) запрещено политикой безопасности.")
            print(f"SHA-256: {audit_report.script_sha256}")
            print(f"{'=' * 60}{Colors.RESET}")
            raise PolicyViolationError(
                "Автоматическое выполнение скриптов PowerShell заблокировано политикой безопасности Zero-Trust."
            )

        # Интерактивное подтверждение человеком (Human-in-the-Loop)
        print(f"\n{Colors.YELLOW}{'=' * 60}")
        print("ТРЕБУЕТСЯ РУЧНОЕ ПОДТВЕРЖДЕНИЕ ВЫПОЛНЕНИЯ POWERSHELL:")
        print(f"Хэш SHA-256: {audit_report.script_sha256}")
        print(f"Статус аудита: Пройден (опасных токенов не обнаружено)")
        print(f"Режим изоляции: ConstrainedLanguage + Win32 Job Object")
        print(f"{'-' * 60}{Colors.RESET}")
        print(script.strip()[:1000] + ("\n... [обрезано]" if len(script.strip()) > 1000 else ""))
        print(f"{Colors.YELLOW}{'=' * 60}{Colors.RESET}")

        try:
            answer = input(
                "Вы подтверждаете выполнение данного скрипта в изолированной песочнице? [y/N]: ").strip().lower()
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

        # Внедрение принудительного Constrained Language Mode в начало скрипта
        hardened_script = (
                "$ExecutionContext.SessionState.LanguageMode = 'ConstrainedLanguage'\r\n"
                + script
        )

        with open(ephemeral_file, "w", encoding="utf-8-sig", newline="") as f:
            f.write(hardened_script)

        start_time = time.time()
        isolation_manager = create_process_isolation_manager(limits)

        creation_flags = 0
        if sys.platform == "win32":
            # Изоляция группы процессов и скрытие консольного окна
            creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP | 0x08000000

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
                creationflags=creation_flags
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
                raw_stderr += f"\n[SECURITY] Процесс превысил лимит времени ({limits.timeout_seconds}с) и был принудительно завершен.".encode(
                    "utf-8")
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
        """Перезапись содержимого нулями (shredding) перед удалением файла с диска."""
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


# ============================================================================
# ЭТАП 4: ФАСАД ОРКЕСТРАЦИИ, СИСТЕМА АУДИТА И DTO РЕЗУЛЬТАТОВ
# ============================================================================

@dataclass(frozen=True)
class ScriptBlockPayload:
    command: str
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
            disable_all_except_write: bool = True
    ) -> ScriptExecutionSummary:
        # Эшелон 1: Детерминированный Fast-Path (нативная запись файлов и создание папок в проекте)
        assessment = ZeroShellTranspiler.assess(raw_script, root_dir)
        if assessment.is_transpilable and assessment.operation is not None:
            start_time = time.time()
            try:
                target_path = assessment.operation.target_path
                if assessment.operation.mode != FileWriteMode.MKDIR and target_path.exists():
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

                op_desc = "Папка успешно создана" if assessment.operation.mode == FileWriteMode.MKDIR else "Файл атомарно записан"
                return ScriptExecutionSummary(
                    success=True,
                    strategy=ExecutionStrategy.NATIVE_TRANSPILATION,
                    exit_code=0,
                    duration_seconds=elapsed,
                    stdout=f"{op_desc} нативным транслятором: {target_path}",
                    stderr="",
                    error_message=None
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
                    error_message=f"Ошибка нативной файловой операции: {e}"
                )

        # Применение правила disable_all_except_write: запрет любых команд вне записи файлов/создания папок
        if disable_all_except_write:
            if is_auto:
                err_msg = (
                    "ОТКАЗ (disable_all_except_write=True): Разрешены только операции записи файлов "
                    "и создания папок в текущей директории проекта. Автоматический запуск любых других команд заблокирован."
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

            print(f"\n{Colors.YELLOW}{'=' * 60}")
            print("ВНИМАНИЕ: Скрипт не является операцией записи файлов или создания папок в текущей директории.")
            print(f"Активно правило безопасности по умолчанию: disable_all_except_write = True.")
            print(
                f"Причина отклонения Fast-Path: {assessment.rejection_reason or 'Обнаружены нетривиальные императивные команды'}")
            print(f"{'-' * 60}{Colors.RESET}")
            print(raw_script.strip()[:600] + ("\n... [обрезано]" if len(raw_script.strip()) > 600 else ""))
            print(f"{Colors.YELLOW}{'=' * 60}{Colors.RESET}")

            try:
                answer = input(
                    "Команда выходит за рамки записи файлов/создания папок. Разрешить выполнение? [y/N]: ").strip().lower()
                if answer not in ["y", "yes", "да", "1"]:
                    return ScriptExecutionSummary(
                        success=False,
                        strategy=ExecutionStrategy.REJECTED,
                        exit_code=1,
                        duration_seconds=0.0,
                        stdout="",
                        stderr="Выполнение скрипта отклонено оператором в соответствии с правилом disable_all_except_write.",
                        error_message="Отклонено оператором (disable_all_except_write)."
                    )
            except EOFError:
                return ScriptExecutionSummary(
                    success=False,
                    strategy=ExecutionStrategy.REJECTED,
                    exit_code=1,
                    duration_seconds=0.0,
                    stdout="",
                    stderr="Прервано: отсутствие интерактивного ввода для подтверждения правила disable_all_except_write.",
                    error_message="Прервано: отсутствие ввода."
                )

        # Эшелон 2: Статический лексический аудит безопасности
        audit_report = StaticScriptAuditor.audit(raw_script)

        # Эшелон 3: Zero-Trust шлюз авторизации
        try:
            is_permitted = ZeroTrustGatekeeper.verify_execution_permission(raw_script, audit_report, is_auto)
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

        # Эшелон 4: Изолированный Slow-Path запуск (ConstrainedLanguage + Win32 Job Object)
        exec_result = SandboxedPowerShellRunner.execute(raw_script, root_dir)
        is_success = exec_result.exit_code == 0

        if indexer is not None:
            indexer.record_powershell_audit(
                script_hash=audit_report.script_sha256,
                strategy=ExecutionStrategy.ISOLATED_POWERSHELL.value,
                exit_code=exec_result.exit_code,
                duration_seconds=exec_result.duration_seconds
            )

        return ScriptExecutionSummary(
            success=is_success,
            strategy=ExecutionStrategy.ISOLATED_POWERSHELL,
            exit_code=exec_result.exit_code,
            duration_seconds=exec_result.duration_seconds,
            stdout=exec_result.stdout,
            stderr=exec_result.stderr,
            error_message=None if is_success else f"Скрипт завершился с кодом ошибки {exec_result.exit_code}"
        )


CLIPBOARD_LOCK_FILE = os.path.join(tempfile.gettempdir(), 'deepsearch_replacer_clipboard.lock')


def is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == 'win32':
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


def check_active_clipboard_process():
    if not os.path.exists(CLIPBOARD_LOCK_FILE):
        return None
    try:
        with open(CLIPBOARD_LOCK_FILE, 'r', encoding='utf-8') as f:
            data = f.read().strip().split('|')
            if len(data) >= 2:
                pid = int(data[0])
                proj_dir = data[1]
                if pid != os.getpid() and is_pid_alive(pid):
                    return {'pid': pid, 'dir': proj_dir}
        try:
            os.remove(CLIPBOARD_LOCK_FILE)
        except OSError:
            pass
        return None
    except Exception:
        return None


def acquire_clipboard_lock():
    try:
        with open(CLIPBOARD_LOCK_FILE, 'w', encoding='utf-8') as f:
            f.write(f"{os.getpid()}|{os.path.abspath('.')}")
        return True
    except Exception:
        return False


def release_clipboard_lock():
    try:
        if os.path.exists(CLIPBOARD_LOCK_FILE):
            with open(CLIPBOARD_LOCK_FILE, 'r', encoding='utf-8') as f:
                data = f.read().strip().split('|')
                if len(data) >= 1 and int(data[0]) == os.getpid():
                    pass
                else:
                    return
            os.remove(CLIPBOARD_LOCK_FILE)
    except Exception:
        pass


def get_clipboard_content() -> str:
    if sys.platform == 'win32':
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
                    break
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


def has_actionable_markers(text):
    if not text:
        return False
    if ',!!!' in text:
        return True
    start_tag = '<' * 4
    mid_tag = '=' * 4
    end_tag = '>' * 4
    if start_tag in text and mid_tag in text and end_tag in text:
        return True
    if "$content = @'" in text or "$content=@'" in text or "```powershell" in text or "```ps1" in text or "```ps" in text:
        return True
    return False


RG_AVAILABLE = shutil.which('rg') is not None


def is_comment_line(line: str) -> bool:
    """Определяет, является ли строка чистым комментарием."""
    s = line.strip()
    if not s:
        return False
    return s.startswith(('//', '#', '/*', '*', '*/', '<!--', '-->', '--', ';', 'REM ', 'rem '))


def generate_whitespace_agnostic_regex(lines):
    clean_lines = [l for l in lines if l.strip()]
    if not clean_lines:
        return ""
    parts = []
    for line in clean_lines:
        tokenized = re.sub(r'\s+', '\x00', line.strip())
        escaped = re.escape(tokenized)
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
        result = subprocess.run(['rg', '-U', '-l', pattern, root_dir], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            paths = set()
            for line in result.stdout.splitlines():
                paths.add(os.path.normpath(line.strip()))
            return paths
    except (subprocess.TimeoutExpired, Exception):
        pass

    # Резервный поиск только по строкам кода (если комментарий в блоке был ошибочным)
    code_lines = [l for l in search_lines if not is_comment_line(l) and l.strip()]
    if len(code_lines) >= 2 and len(code_lines) != len([l for l in search_lines if l.strip()]):
        code_pattern = generate_whitespace_agnostic_regex(code_lines)
        if code_pattern:
            try:
                result = subprocess.run(['rg', '-U', '-l', code_pattern, root_dir], capture_output=True, text=True,
                                        timeout=5)
                if result.returncode == 0:
                    paths = set()
                    for line in result.stdout.splitlines():
                        paths.add(os.path.normpath(line.strip()))
                    return paths
            except (subprocess.TimeoutExpired, Exception):
                pass
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
    pattern = re.compile(r'[@#*`\'"]?([a-zA-Z0-9_./\\-]+\.[a-zA-Z0-9]{2,10})')
    start_tag = '<' * 4
    for line in lines:
        if line.strip().startswith(start_tag):
            continue
        for match in pattern.findall(line):
            clean = match.strip('@#*`\'":;, ')
            if clean and ('.' in clean):
                filenames.add(os.path.normpath(clean))
                filenames.add(os.path.basename(clean))
    return filenames


def parse_patch_blocks(lines):
    for line in lines:
        if line.strip() == ',!!!':
            return 'UNDO'

    blocks = []
    state = 0  # 0: поиск <<<<, 1: сбор оригинала (поиск ====), 2: сбор нового текста (поиск >>>>)
    current_search = []
    current_replace = []
    current_file_hint = None

    start_marker = '<' * 4
    mid_marker = '=' * 4
    end_marker = '>' * 4

    def extract_file_hint_from_recent_lines(line_buffer):
        file_pattern = re.compile(r'[@#*`\'"]?([a-zA-Z0-9_./\\-]+\.[a-zA-Z0-9]{2,10})')
        for l in reversed(line_buffer[-12:]):
            sl = l.strip()
            if sl.startswith(end_marker) or sl.startswith(mid_marker):
                break
            matches = file_pattern.findall(sl)
            for m in reversed(matches):
                clean = m.strip('@#*`\'":;, ')
                if '.' in clean and not clean.endswith('.'):
                    ext = os.path.splitext(clean)[1].lower()
                    if ext in CODE_EXTENSIONS or len(ext) in [3, 4, 5]:
                        return os.path.normpath(clean)
        return None

    recent_lines = []

    for line_idx, line in enumerate(lines):
        stripped = line.strip()

        is_start = stripped.startswith(start_marker)
        is_mid = stripped.startswith(mid_marker)
        is_end = stripped.startswith(end_marker)

        if state == 0:
            if is_start:
                current_file_hint = extract_file_hint_from_recent_lines(recent_lines)
                state = 1
                current_search = []
                current_replace = []
            else:
                recent_lines.append(line)
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
                    'replace': current_replace,
                    'file_hint': current_file_hint
                })
                state = 0
                current_file_hint = None
                recent_lines = []
            elif not is_start and not is_mid:
                current_replace.append(line.rstrip('\r\n'))

    if state == 2:
        def trim_lines_search(l):
            s, e = 0, len(l)
            while s < e and not l[s].strip(): s += 1
            while e > s and not l[e - 1].strip(): e -= 1
            return l[s:e]

        blocks.append({
            'search': trim_lines_search(current_search),
            'replace': current_replace,
            'file_hint': current_file_hint
        })

    return blocks


def parse_input():
    print(f"{Colors.YELLOW}Введите текст с блоками правок. {Colors.RESET}")
    print(
        f"{Colors.YELLOW}Для подтверждения отправки введите {Colors.GREEN}.,,,{Colors.YELLOW} на новой пустой строке и нажмите Enter (или {Colors.GREEN},!!!{Colors.YELLOW} для отката):{Colors.RESET}")

    lines = []
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            stripped = line.strip()
            if stripped == ',!!!':
                return 'UNDO', lines
            if stripped == '.,,,':
                break
            lines.append(line)
        except EOFError:
            break

    blocks = parse_patch_blocks(lines)
    return blocks, lines


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
            try:
                os.remove(os.path.join(self.history_dir, old_zip))
            except:
                pass

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

            CREATE TABLE IF NOT EXISTS powershell_audit_log (
                script_hash TEXT PRIMARY KEY,
                strategy TEXT,
                exit_code INTEGER,
                duration_seconds REAL,
                executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        ''')
        # Пытаемся использовать сверхбыстрый триграммный поиск, если не выйдет - откатываемся на базовый
        try:
            self.db.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS fts_code USING fts5(path, content, tokenize='trigram');")
        except sqlite3.OperationalError:
            self.db.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS fts_code USING fts5(path, content, tokenize='unicode61');")
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

    def fts_search(self, search_lines):
        # Извлекаем слова длиннее 4 символов только из строк кода (игнорируя комментарии)
        words = []
        for line in search_lines:
            if not is_comment_line(line):
                words.extend(re.findall(r'[a-zA-Z_]{4,}', line))
        if not words:
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
                            except:
                                pass
                    else:
                        try:
                            with open(file_path, 'r', encoding=enc) as fp:
                                content = fp.read()
                        except:
                            pass

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
                self.db.executemany(
                    "REPLACE INTO files_meta (path, size, mtime, is_binary, encoding, newlines) VALUES (?, ?, ?, ?, ?, ?)",
                    chunk)
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


def can_contain_block(search_lines, f_text):
    # Отбираем только значимые строки кода, исключая чистые комментарии
    code_lines = [l.strip() for l in search_lines if len(l.strip()) > 6 and not is_comment_line(l)]
    target_lines = code_lines if code_lines else [l.strip() for l in search_lines if len(l.strip()) > 6]
    if not target_lines:
        return True

    longest_lines = sorted(target_lines, key=len, reverse=True)[:5]
    for line in longest_lines:
        if line in f_text:
            return True

    # Проверка с нормализацией повторяющихся пробелов
    f_text_collapsed = re.sub(r'[ \t]+', ' ', f_text)
    for line in longest_lines:
        line_collapsed = re.sub(r'[ \t]+', ' ', line)
        if line_collapsed in f_text_collapsed:
            return True

    # Проверка по идентификаторам кода
    for line in longest_lines:
        tokens = [t for t in re.findall(r'[a-zA-Z0-9_]{5,}', line) if
                  t.lower() not in ('return', 'const', 'function', 'import', 'export')]
        if len(tokens) >= 2 and all(t in f_text for t in tokens[:2]):
            return True

    return False


def get_fuzzy_ratio(s1, s2):
    if RAPIDFUZZ_AVAILABLE:
        return fuzz.ratio(s1, s2) / 100.0
    return difflib.SequenceMatcher(None, s1, s2).ratio()


def adjust_comment_boundaries(search_lines, file_lines, start, end):
    """
    Корректирует границы start и end для сопоставления внешних комментариев,
    гарантируя, что соседние строки чужого кода никогда не будут затерты.
    """
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

    s_code = [l.strip() for l in search_lines if not is_comment_line(l) and l.strip()]
    c_code = [l.strip() for l in candidate_lines if not is_comment_line(l) and l.strip()]
    if s_code and c_code:
        code_matcher = difflib.SequenceMatcher(None, "\n".join(s_code), "\n".join(c_code))
        code_similarity_pct = code_matcher.ratio() * 100.0
    else:
        code_similarity_pct = char_similarity_pct

    return {
        'mismatched_chars': mismatched_chars,
        'char_mismatch_pct': char_mismatch_pct,
        'mismatched_lines': mismatched_lines,
        'line_mismatch_pct': line_mismatch_pct,
        'char_similarity_pct': char_similarity_pct,
        'line_similarity_pct': line_similarity_pct,
        'code_similarity_pct': code_similarity_pct
    }


def find_anchors_match(search_lines, file_lines):
    s_clean = search_lines[:]
    while s_clean and not s_clean[0].strip():
        s_clean.pop(0)
    while s_clean and not s_clean[-1].strip():
        s_clean.pop()

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

    # Ищем якорь среди строк кода, пропуская внешние комментарии при необходимости
    anchor_offset = 0
    while anchor_offset < len(s_norm) and is_comment_line(s_norm[anchor_offset]):
        anchor_offset += 1
    if anchor_offset >= len(s_norm) or s_norm[0] in line_index:
        anchor_offset = 0

    avail_len = len(s_clean) - anchor_offset
    max_anchor_len = max(3, avail_len // 2)

    top_anchor_len = max(1, min(3, avail_len))
    start_idx_matches = []
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
            end_anchor_start_idx = min(end_idx_matches, key=lambda x: abs(x - (start_idx + len(s_clean))))
            end_idx = end_anchor_start_idx + bottom_anchor_len

            if end_idx - start_idx <= len(s_clean) * 2:
                adj_s, adj_e = adjust_comment_boundaries(s_clean, file_lines, start_idx, end_idx)
                candidates.append({'start': adj_s, 'end': adj_e, 'ratio': 0.95})

    return candidates


def _fuzzy_slide_window(s_norm, f_norm, file_lines, leading, trailing, threshold):
    n_s = len(s_norm)
    n_f = len(f_norm)
    if n_s == 0:
        return []

    candidates = []
    s_text = "\n".join(s_norm)
    dynamic_threshold = threshold if n_s >= 4 else 0.70

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

    if n_f < n_s:
        ratio = get_fuzzy_ratio(s_text, f_text_full)
        if ratio >= max(threshold, 0.95):
            candidates.append({'start': 0, 'end': len(file_lines), 'ratio': ratio * 0.98})
        return candidates

    candidates = _fuzzy_slide_window(s_norm, f_norm, file_lines, leading, trailing, threshold)
    if candidates:
        return candidates

    # Резервный проход: нечеткий поиск только строк кода (если размер окна искажен отсутствующим комментарием)
    code_norm = [l for l in s_norm if not is_comment_line(l) and l]
    if len(code_norm) >= 2 and len(code_norm) < len(s_norm):
        cands_code = _fuzzy_slide_window(code_norm, f_norm, file_lines, 0, 0, threshold)
        for c in cands_code:
            adj_s, adj_e = adjust_comment_boundaries(s_clean, file_lines, c['start'], c['end'])
            candidates.append({'start': adj_s, 'end': adj_e, 'ratio': c['ratio'] * 0.95})

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


def extract_powershell_commands(lines: List[str]) -> List[ScriptBlockPayload]:
    commands: List[ScriptBlockPayload] = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()

        # 1. Поиск блоков powershell в markdown
        if stripped.lower() in ["```powershell", "```ps1", "```ps"]:
            start_idx = i + 1
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                i += 1
            if i < len(lines):
                cmd = "".join(lines[start_idx:i])
                if cmd.strip():
                    commands.append(ScriptBlockPayload(command=cmd, start_line=start_idx + 1, end_line=i))

        # 2. Поиск блоков $content = @'
        elif stripped.startswith("$content = @'") or stripped.startswith("$content=@'"):
            start_idx = i
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("'@"):
                i += 1
            # Захватываем последующие команды файловой записи
            while i < len(lines) and not any(
                    kw in lines[i] for kw in ["Out-File", "Set-Content", "WriteAllText", "AppendAllText"]):
                i += 1
            if i < len(lines):
                cmd = "".join(lines[start_idx:i + 1])
                commands.append(ScriptBlockPayload(command=cmd, start_line=start_idx + 1, end_line=i + 1))
        i += 1

    return commands


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


def main(ignore_folders=None, ignore_files=None, input_lines=None, auto_mode=False, disable_all_except_write=True):
    if not ignore_folders:
        ignore_folders = []
    if not ignore_files:
        ignore_files = []

    is_auto = auto_mode or AUTO_MODE

    if input_lines is None:
        blocks, input_lines = parse_input()
    else:
        blocks = parse_patch_blocks(input_lines)
    if blocks == 'UNDO':
        TransactionManager().undo_last()
        return True

    prompt_filenames = extract_filenames_from_prompt(input_lines)
    indexer = CodeIndexer('.')
    tm = TransactionManager('.')

    ps_commands = extract_powershell_commands(input_lines)
    if ps_commands:
        print(f"\n{Colors.YELLOW}Обнаружены команды PowerShell ({len(ps_commands)} шт.):{Colors.RESET}")
        for idx, payload in enumerate(ps_commands, 1):
            print(
                f"{Colors.YELLOW}--- Команда {idx} (строки {payload.start_line}-{payload.end_line}) ---{Colors.RESET}")
            summary = PowerShellSecurityOrchestrator.dispatch(
                raw_script=payload.command,
                root_dir=Path("."),
                tm=tm,
                indexer=indexer,
                is_auto=is_auto,
                disable_all_except_write=disable_all_except_write
            )
            if not summary.success:
                print(
                    f"{Colors.RED}Прерывание работы из-за отказа в исполнении PowerShell команды {idx}:{Colors.RESET}")
                if summary.error_message:
                    print(f"{Colors.RED}{summary.error_message}{Colors.RESET}")
                if summary.stderr:
                    print(f"{Colors.YELLOW}{summary.stderr}{Colors.RESET}")
                return False
            else:
                print(f"{Colors.GREEN}[УСПЕХ ({summary.strategy.value})] {summary.stdout}{Colors.RESET}")

    if not blocks:
        if not ps_commands:
            start_tag_repr = '<' * 4
            mid_tag_repr = '=' * 4
            end_tag_repr = '>' * 4
            print(
                f"{Colors.RED}Не найдено ни одного корректного блока с маркерами {start_tag_repr} {mid_tag_repr} {end_tag_repr}!{Colors.RESET}")
        return

    start_time = time.time()
    print(f"\nРаспознано блоков правок: {len(blocks)}. Идет сканирование файлов...")
    file_contents, file_encodings, non_utf8_files, code_file_contents, other_file_contents = indexer.sync_and_read(
        ignore_folders, ignore_files, prompt_filenames)

    # 2. Поиск совпадений
    block_matches = []
    already_applied_blocks = {}
    user_skipped_blocks = set()
    cached_blocks = set()

    def get_candidates(search_lines, target_files, use_fts=True):
        cands = []

        allowed_paths = None
        if use_fts:
            rg_paths = ripgrep_search(search_lines, indexer.root_dir)
            if rg_paths is not None:
                allowed_paths = rg_paths
            else:
                allowed_paths = indexer.fts_search(search_lines)

        for path, lines in target_files.items():
            if allowed_paths is not None:
                p_norm = os.path.normpath(path)
                if p_norm not in allowed_paths and path not in allowed_paths:
                    continue

            exact_matches = find_matches(search_lines, lines)
            if exact_matches:
                for m in exact_matches:
                    cands.append({'path': path, 'start': m[0], 'end': m[1], 'ratio': 1.0})
                continue

            # Резервный точный поиск только строк кода (без внешних комментариев)
            code_only_lines = [l for l in search_lines if not is_comment_line(l) and l.strip()]
            if len(code_only_lines) >= 2 and len(code_only_lines) < len([l for l in search_lines if l.strip()]):
                code_matches = find_matches(code_only_lines, lines)
                if code_matches:
                    for m in code_matches:
                        adj_s, adj_e = adjust_comment_boundaries(search_lines, lines, m[0], m[1])
                        cands.append({'path': path, 'start': adj_s, 'end': adj_e, 'ratio': 0.99})
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
        file_hint = block.get('file_hint')
        matches_for_block = []

        patch_hash = indexer.get_patch_hash(search_lines, replace_lines)
        if indexer.is_patch_applied(patch_hash):
            print(f"\n{Colors.GREEN}Блок {idx + 1} уже был применен ранее (пропущен благодаря кэшу БД).{Colors.RESET}")
            cached_blocks.add(idx)
            block_matches.append([])
            continue

        # Поиск потенциальных кандидатов сначала в файлах кода с FTS-фильтрацией
        candidates = get_candidates(search_lines, code_file_contents, use_fts=True)

        if not candidates and other_file_contents:
            print(f"\n{Colors.YELLOW}Блок {idx + 1} не найден в файлах кода.{Colors.RESET}")
            ans = 'y' if is_auto else 'n'
            if not is_auto:
                try:
                    ans = input(
                        "Искать в остальных файлах (базы данных, логи и т.д.)? [y/n] (Enter=y): ").strip().lower()
                    if not ans:
                        ans = 'y'
                except EOFError:
                    ans = 'y'

            if ans in ['y', 'yes', 'да', '1']:
                candidates = get_candidates(search_lines, other_file_contents, use_fts=False)

        # Если FTS пропустил нужный файл, делаем резервный поиск без FTS
        if not candidates and code_file_contents:
            candidates = get_candidates(search_lines, code_file_contents, use_fts=False)

        if not candidates and non_utf8_files:
            print(
                f"\n{Colors.YELLOW}Блок {idx + 1} не найден в текущих файлах. Подгрузка файлов с другими кодировками...{Colors.RESET}")
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

        # Приоритизация/фильтрация кандидатов по подсказке имени файла из промпта
        if file_hint and candidates:
            hint_norm = os.path.normpath(file_hint).lower()
            hint_base = os.path.basename(hint_norm)
            hint_matched = []
            for c in candidates:
                c_norm = os.path.normpath(c['path']).lower()
                if c_norm == hint_norm or c_norm.endswith(os.sep + hint_norm) or c_norm.endswith('/' + hint_norm):
                    hint_matched.append(c)
            if not hint_matched:
                for c in candidates:
                    if os.path.basename(os.path.normpath(c['path']).lower()) == hint_base:
                        hint_matched.append(c)
            if hint_matched:
                candidates = hint_matched

        # Сортировка по совпадению (сначала наиболее похожие)
        candidates.sort(key=lambda x: x['ratio'], reverse=True)

        # Если найдено точное совпадение (ratio > 0.999), берем его без лишних вопросов
        exact_candidates = [c for c in candidates if c['ratio'] >= 0.999]
        if exact_candidates:
            if len(exact_candidates) > 1 and prompt_filenames:
                prio_exact = []
                for ec in exact_candidates:
                    ec_norm = os.path.normpath(ec['path']).lower()
                    for pf in prompt_filenames:
                        pf_norm = os.path.normpath(pf).lower()
                        if ec_norm == pf_norm or ec_norm.endswith(os.sep + pf_norm) or ec_norm.endswith('/' + pf_norm):
                            prio_exact.append(ec)
                            break
                if prio_exact:
                    exact_candidates = prio_exact

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
                                print(
                                    f"  Строка {idx_s + 1} (несоответствует символов: {line_char_mismatch_pct:.1f}%):")
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

                if is_auto and (stats['char_similarity_pct'] > 90 or stats.get('code_similarity_pct', 0) > 90):
                    ans = 'y'
                    print(f"{Colors.GREEN}Auto-mode: Сходство высокое, блок подтвержден.{Colors.RESET}")
                elif stats['char_similarity_pct'] == 100.0 and stats['line_mismatch_pct'] == 0.0:
                    ans = 'y'
                    print(
                        f"{Colors.GREEN}Сходство 100% (с учетом игнорирования отступов). Автоматическое подтверждение.{Colors.RESET}")
                elif stats.get('code_similarity_pct', 0) == 100.0:
                    ans = 'y'
                    print(
                        f"{Colors.GREEN}Код совпадает на 100% (различия только в комментариях/отступах). Автоматическое подтверждение.{Colors.RESET}")
                elif is_auto:
                    ans = 'y'
                    print(f"{Colors.GREEN}Auto-mode: блок автоматически подтвержден.{Colors.RESET}")
                else:
                    try:
                        ans = input("Подтвердить? [y/n] (Enter=y): ").strip().lower()
                        if not ans:
                            ans = 'y'
                    except EOFError:
                        ans = 'y'

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
            if idx in cached_blocks:
                continue

            if idx in user_skipped_blocks:
                print(f"{Colors.YELLOW}Блок {idx + 1} пропущен пользователем.{Colors.RESET}")
                continue

            if idx in already_applied_blocks:
                locs = already_applied_blocks[idx]
                if locs:
                    locs_str = "\n  - ".join([f"{p} (строки {s + 1}-{e})" for p, s, e in locs])
                    print(
                        f"\n{Colors.YELLOW}ВНИМАНИЕ: Для блока {idx + 1} оригинал не найден, но точная копия текста ЗАМЕНЫ уже присутствует в коде:{Colors.RESET}")
                    print(f"  - {locs_str}")
                    if is_auto:
                        ans = 'y'
                    else:
                        try:
                            ans = input(
                                "Похоже, правки уже внесены. Пропустить этот блок? [y/n] (Enter=y): ").strip().lower()
                            if not ans:
                                ans = 'y'
                        except EOFError:
                            ans = 'y'
                    if ans in ['y', 'yes', 'да', '1']:
                        continue
                else:
                    print(
                        f"{Colors.GREEN}Блок {idx + 1} пропущен: блок замены пуст, оригинал не найден (вероятно, уже удален).{Colors.RESET}")
                    continue

            err_msg = f"Блок {idx + 1} НЕ НАЙДЕН НИ В ОДНОМ ФАЙЛЕ.\nОригинальный текст, который мы искали:\n" + "\n".join(
                blocks[idx]['search'])
            errors.append(err_msg)
            missing_blocks += 1
        elif len(matches) > 1:
            # Попытка устранить неоднозначность по подсказкам файлов из промпта
            if prompt_filenames:
                filtered_matches = []
                for p, s, e in matches:
                    p_norm = os.path.normpath(p).lower()
                    for pf in prompt_filenames:
                        pf_norm = os.path.normpath(pf).lower()
                        if p_norm == pf_norm or p_norm.endswith(os.sep + pf_norm) or p_norm.endswith('/' + pf_norm):
                            filtered_matches.append((p, s, e))
                            break
                if len(filtered_matches) == 1:
                    matches = filtered_matches
                    block_matches[idx] = matches

            if len(matches) == 1:
                continue

            locs = "\n  - ".join([f"{p} (строки {s + 1}-{e})" for p, s, e in matches])
            print(
                f"\n{Colors.YELLOW}Блок {idx + 1} найден {len(matches)} раз (Неоднозначность!).\nГде найдено:\n  - {locs}{Colors.RESET}")
            if is_auto:
                ans = 'y'
                print(f"{Colors.GREEN}Auto-mode: применяем ко всем {len(matches)} совпадениям.{Colors.RESET}")
            else:
                try:
                    ans = input(
                        "Применить ко всем? (y - ко всем, 1 - только к первому, s/n - пропустить) [Enter=y]: ").strip().lower()
                    if not ans:
                        ans = 'y'
                except EOFError:
                    ans = 'y'

            if ans in ['s', 'skip', 'n', 'no', 'нет', 'пропустить']:
                print(f"{Colors.YELLOW}Блок {idx + 1} пропущен пользователем.{Colors.RESET}")
                user_skipped_blocks.add(idx)
                block_matches[idx] = []
            elif ans in ['1', 'первый', 'first']:
                block_matches[idx] = [matches[0]]
            elif ans in ['y', 'yes', 'да', 'all', 'все', '.,,,']:
                block_matches[idx] = matches
            else:
                print(f"{Colors.YELLOW}Применяем ко всем найденным файлам.{Colors.RESET}")
                block_matches[idx] = matches

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
            replace_lines = mod['replace']

            lines = lines[:start] + replace_lines + lines[end:]

        # Запись во временный словарь (в памяти)
        try:
            enc_info = file_encodings.get(path, ('utf-8', '\n'))
            if isinstance(enc_info, tuple):
                enc, newline_char = enc_info
            else:
                enc, newline_char = enc_info, '\n'

            if 'utf-16' in enc.lower():
                print(f"\n{Colors.YELLOW}Файл {path} обнаружен в кодировке {enc}.{Colors.RESET}")
                ans = 'y' if is_auto else 'n'
                if not is_auto:
                    try:
                        ans = input(
                            "Разрешить внесение правок и перезаписать в UTF-8? [y/n] (Enter=y): ").strip().lower()
                        if not ans:
                            ans = 'y'
                    except EOFError:
                        ans = 'y'
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

    # Проверка линтером перед записью (последовательно, чтобы можно было взаимодействовать с пользователем)
    for d in pending_replacements:
        path, lines, enc, newline_char = d
        content = newline_char.join(lines) + newline_char
        is_valid, err_msg = run_dry_linter(path, content)
        if not is_valid:
            print(f"\n{Colors.RED}Синтаксическая ошибка в файле {path}:{Colors.RESET}")
            print(f"{Colors.YELLOW}{err_msg}{Colors.RESET}")
            if is_auto:
                ans = 'y'
                print(f"{Colors.YELLOW}Auto-mode: Игнорируем синтаксическую ошибку.{Colors.RESET}")
            else:
                try:
                    ans = input("Игнорировать ошибку и сохранить файл? [y/n] (Enter=y): ").strip().lower()
                    if not ans:
                        ans = 'y'
                except EOFError:
                    ans = 'n'
            if ans not in ['y', 'yes', 'да', '1']:
                print(f"{Colors.RED}Запись отменена пользователем. Выполняется откат...{Colors.RESET}")
                tm.undo_last()
                return False

    def safe_atomic_write(file_data):
        path, lines, enc, newline_char = file_data
        content = newline_char.join(lines) + newline_char

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


def run_replacer(ignore_folders=None, ignore_files=None, check_clipboard=False, skip_clipboard_confirm=False,
                 auto_mode=False, disable_all_except_write=True):
    if '--undo' in sys.argv:
        TransactionManager().undo_last()
        return

    effective_auto = auto_mode or AUTO_MODE or skip_clipboard_confirm

    if check_clipboard:
        # Проверка: запущен ли в системе другой replacer с check_clipboard=True
        active_proc = check_active_clipboard_process()
        if active_proc:
            print(f"\n{Colors.RED}{'=' * 60}")
            print(f"КРИТИЧЕСКАЯ ОШИБКА: Другой replacer уже запущен с параметром check_clipboard=True!")
            print(f"PID: {active_proc['pid']}")
            print(f"Директория: {active_proc['dir']}")
            print(f"Работа завершена во избежание применения правок не в тот проект.")
            print(f"{'=' * 60}{Colors.RESET}")
            sys.exit(1)

        acquire_clipboard_lock()
        atexit.register(release_clipboard_lock)
        print(f"{Colors.GREEN}Режим мониторинга буфера обмена включен (проверка каждую 1 сек).{Colors.RESET}")
        if skip_clipboard_confirm:
            print(
                f"{Colors.YELLOW}Режим без подтверждения (skip_clipboard_confirm=True / Auto-mode) активен.{Colors.RESET}")

        last_clip_hash = None
        init_text = get_clipboard_content()
        if init_text and not has_actionable_markers(init_text):
            last_clip_hash = hashlib.md5(init_text.encode('utf-8', errors='ignore')).hexdigest()

        try:
            while True:
                try:
                    clip_text = get_clipboard_content()
                    if clip_text:
                        clip_hash = hashlib.md5(clip_text.encode('utf-8', errors='ignore')).hexdigest()
                        if clip_hash != last_clip_hash:
                            last_clip_hash = clip_hash
                            if has_actionable_markers(clip_text):
                                lines = [l + '\n' for l in clip_text.splitlines()]
                                if not skip_clipboard_confirm:
                                    print(f"\n{Colors.YELLOW}Обнаружены новые правки в буфере обмена.{Colors.RESET}")
                                    try:
                                        ans = input(f"Применить правки? [y/n] (Enter=y): ").strip().lower()
                                        if not ans:
                                            ans = 'y'
                                    except EOFError:
                                        ans = 'n'
                                    if ans not in ['y', 'yes', 'да', '1', '.,,,']:
                                        print(f"{Colors.YELLOW}Правки из буфера обмена пропущены.{Colors.RESET}")
                                        time.sleep(1)
                                        continue

                                print(f"\n{Colors.GREEN}Обработка правок из буфера обмена...{Colors.RESET}")
                                main(ignore_folders=ignore_folders, ignore_files=ignore_files, input_lines=lines,
                                     auto_mode=effective_auto, disable_all_except_write=disable_all_except_write)
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
                main(ignore_folders=ignore_folders, ignore_files=ignore_files, auto_mode=effective_auto, disable_all_except_write=disable_all_except_write)
            except KeyboardInterrupt:
                print(f"\n{Colors.YELLOW}Операция прервана пользователем.{Colors.RESET}")
                break


if __name__ == '__main__':
    run_replacer()
