#!/usr/bin/env python3
"""
C/C++ Project Dump Generator - Single ZIP Edition
=================================================

Purpose
-------
Create one uploadable ZIP file for ChatGPT Project/reference inspection.

This version replaces the previous split-output folder behavior. It creates
exactly one output artifact:

    <ScriptDirectory>/
    └── FDTO_output/
        └── ProjectFolderName_YYYYMMDD_HHMMSS.zip

Inside that ZIP is one consolidated UTF-8 text file:

    ProjectFolderName_YYYYMMDD_HHMMSS.txt

The text file contains:
- Run summary
- Directory tree
- Complete files manifest
- Embedded contents for readable text/source/config files
- Metadata-only entries for binary/unreadable files

Behavior
--------
- Can be run from CLI or by double-clicking the .py file.
- If run without CLI arguments, opens a folder browser to select the project root.
- Output ZIP is written by default to an auto-created FDTO_output folder beside this script.
- The project folder browser opens immediately with no preliminary message popup.
- After a successful run, the output ZIP file path is copied to the clipboard.
- A persistent append-only run log is written to FDTO_output/FDTO_run_log.txt.
- In interactive/double-click mode, the script exits without waiting for Enter.
- Recursively scans all subdirectories.
- Excludes generated dump artifacts so old dumps do not get included in new dumps.
- Creates one ZIP file only; no output folder, no index file, no part files.
- Uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import os
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SEP = "=" * 100
SUBSEP = "-" * 100

DEFAULT_ZIP_PREFIX = "project_text_dump"
DEFAULT_OUTPUT_FOLDER_NAME = "FDTO_output"
DEFAULT_LOG_FILENAME = "FDTO_run_log.txt"
LEGACY_OUTPUT_FOLDER_NAME = "project_dump_output"
SAMPLE_SIZE = 8192

_LOG_FILE_PATH: Path | None = None


def get_script_directory() -> Path:
    """
    Returns the folder where this script exists.
    If frozen into an executable later, returns the executable folder.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def get_default_output_directory() -> Path:
    """
    Returns the default output folder for generated ZIP files.
    The folder is created later by write_dump_zip if it does not already exist.
    """
    return get_script_directory() / DEFAULT_OUTPUT_FOLDER_NAME


def sanitize_filename_component(text: str) -> str:
    """
    Makes the selected project folder name safe for use in the ZIP filename.
    """
    invalid_chars = '<>:"/\\|?*'
    cleaned = text.strip()
    for char in invalid_chars:
        cleaned = cleaned.replace(char, "_")
    cleaned = "".join("_" if ord(ch) < 32 else ch for ch in cleaned)
    cleaned = cleaned.strip().rstrip(".")
    return cleaned or "project"

TEXT_EXTENSIONS = {
    # C / C++
    ".c", ".cc", ".cpp", ".cxx", ".c++",
    ".h", ".hh", ".hpp", ".hxx", ".h++",
    ".ipp", ".inl", ".ixx", ".tpp",

    # Other source/script files commonly found in build tooling
    ".py", ".pyw", ".js", ".jsx", ".ts", ".tsx",
    ".cs", ".java", ".kt", ".kts", ".go", ".rs",
    ".swift", ".m", ".mm", ".php", ".rb", ".pl",
    ".lua", ".r", ".sql", ".ps1", ".bat", ".cmd", ".sh",
    ".bash", ".zsh",

    # Build/project files
    ".cmake", ".mk", ".mak", ".make",
    ".sln", ".vcxproj", ".vcproj", ".props", ".targets", ".filters",
    ".pro", ".pri",

    # Config/data/docs useful as project reference data
    ".txt", ".md", ".rst", ".adoc",
    ".json", ".jsonc", ".yaml", ".yml", ".toml",
    ".ini", ".cfg", ".conf", ".config", ".properties",
    ".xml", ".xsd", ".html", ".htm", ".css", ".scss", ".less",
    ".csv", ".tsv", ".log",
}

TEXT_FILENAMES = {
    "CMakeLists.txt",
    "Makefile",
    "makefile",
    "GNUmakefile",
    "README",
    "LICENSE",
    "COPYING",
    "NOTICE",
    ".gitignore",
    ".gitattributes",
    ".clang-format",
    ".clang-tidy",
    ".editorconfig",
    ".dockerignore",
    "Dockerfile",
}

BINARY_EXTENSIONS = {
    ".exe", ".dll", ".lib", ".a", ".so", ".dylib",
    ".obj", ".o", ".pdb", ".ilk", ".idb", ".exp",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".tif", ".tiff",
    ".pdf", ".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".xz",
    ".mp3", ".wav", ".flac", ".mp4", ".avi", ".mov", ".mkv",
    ".db", ".sqlite", ".sqlite3", ".mdb", ".accdb",
    ".class", ".jar",
    ".bin", ".dat",
}


@dataclass
class FileRecord:
    rel_path: str
    abs_path: Path
    size_bytes: int
    modified_local: str
    modified_utc: str
    extension: str
    kind: str = "unknown"  # text / binary / unreadable / metadata_error
    encoding: str = ""
    newline: str = ""
    sha256: str = ""
    error: str = ""


def set_log_file_path(log_file_path: Path) -> None:
    """
    Sets the persistent append-only log file used by info/ok/warn/fail.
    """
    global _LOG_FILE_PATH
    _LOG_FILE_PATH = log_file_path.resolve()
    _LOG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)


def append_log_line(prefix: str, message: str) -> None:
    """
    Appends one timestamped line to the persistent run log.
    Logging failure should never stop the dump process.
    """
    if _LOG_FILE_PATH is None:
        return

    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with _LOG_FILE_PATH.open("a", encoding="utf-8", newline="\n") as log_file:
            log_file.write(f"{timestamp} {prefix} {message}\n")
    except Exception:
        pass


def append_log_block(text: str) -> None:
    """
    Appends a raw block to the persistent run log.
    """
    if _LOG_FILE_PATH is None:
        return

    try:
        with _LOG_FILE_PATH.open("a", encoding="utf-8", newline="\n") as log_file:
            log_file.write(text)
            if not text.endswith("\n"):
                log_file.write("\n")
    except Exception:
        pass


def info(message: str) -> None:
    print(f"[INFO] {message}")
    append_log_line("[INFO]", message)


def ok(message: str) -> None:
    print(f"[OK]   {message}")
    append_log_line("[OK]  ", message)


def warn(message: str) -> None:
    print(f"[WARN] {message}")
    append_log_line("[WARN]", message)


def fail(message: str) -> None:
    print(f"[ERROR] {message}", file=sys.stderr)
    append_log_line("[ERROR]", message)


def copy_text_to_clipboard(text: str) -> tuple[bool, str]:
    """
    Copies text to the system clipboard using tkinter only.
    Returns (success, error_message).
    """
    try:
        import tkinter as tk
    except Exception as exc:
        return False, f"tkinter unavailable: {exc}"

    try:
        root = tk.Tk()
        root.withdraw()
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update()
        root.destroy()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def normalize_path_text(path_text: str) -> str:
    return path_text.replace("\\", "/")


def safe_rel_path(root: Path, path: Path) -> str:
    try:
        return normalize_path_text(str(path.relative_to(root)))
    except ValueError:
        return normalize_path_text(str(path))


def format_size(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.2f} {unit}"
        value /= 1024.0
    return f"{num_bytes} B"


def detect_newline_style(data: bytes) -> str:
    if not data:
        return "none"

    crlf = data.count(b"\r\n")
    lf = data.count(b"\n")
    cr = data.count(b"\r")

    lone_lf = max(lf - crlf, 0)
    lone_cr = max(cr - crlf, 0)

    counts = {
        "crlf": crlf,
        "lf": lone_lf,
        "cr": lone_cr,
    }

    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else "none"


def sample_looks_binary(sample: bytes) -> bool:
    if not sample:
        return False

    if b"\x00" in sample:
        return True

    allowed_controls = {9, 10, 12, 13}  # tab, LF, FF, CR
    suspicious = 0

    for byte in sample:
        if byte < 32 and byte not in allowed_controls:
            suspicious += 1

    return (suspicious / max(len(sample), 1)) > 0.05


def choose_encoding(data: bytes) -> tuple[str, str, str]:
    """
    Returns:
        decoded_text, encoding_used, error_message
    """
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(enc), enc, ""
        except UnicodeDecodeError:
            continue
        except Exception as exc:
            return "", enc, str(exc)

    try:
        return data.decode("utf-8", errors="replace"), "utf-8-replace", "decoded with replacement characters"
    except Exception as exc:
        return "", "", str(exc)


def is_probably_text_file(path: Path, sample: bytes) -> bool:
    if path.name in TEXT_FILENAMES:
        return True

    suffix = path.suffix.lower()

    if suffix in TEXT_EXTENSIONS:
        return True

    if suffix in BINARY_EXTENSIONS:
        return False

    return not sample_looks_binary(sample)


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()

    with path.open("rb") as file:
        while True:
            chunk = file.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)

    return h.hexdigest()


def read_sample(path: Path) -> bytes:
    with path.open("rb") as file:
        return file.read(SAMPLE_SIZE)


def read_text_file(path: Path) -> tuple[str, str, str, str]:
    """
    Returns:
        text, encoding, newline_style, error
    """
    try:
        data = path.read_bytes()
    except Exception as exc:
        return "", "", "", str(exc)

    newline = detect_newline_style(data)
    text, encoding, error = choose_encoding(data)

    if text:
        text = text.replace("\r\n", "\n").replace("\r", "\n")

    return text, encoding, newline, error


def is_generated_dump_artifact(path: Path, zip_prefix: str) -> bool:
    """
    Prevents old generated dump outputs from being included in the new dump.
    """
    name = path.name

    if path.is_dir() and name in {LEGACY_OUTPUT_FOLDER_NAME, DEFAULT_OUTPUT_FOLDER_NAME}:
        return True

    if path.is_file() and name.startswith(f"{zip_prefix}_") and name.lower().endswith(".zip"):
        return True

    return False


def collect_records(project_root: Path, output_zip_path: Path, calculate_hashes: bool, zip_prefix: str) -> tuple[list[FileRecord], list[str], int]:
    records: list[FileRecord] = []
    scan_warnings: list[str] = []
    directory_count = 0

    project_root = project_root.resolve()
    output_zip_path = output_zip_path.resolve()

    for root, dirs, files in os.walk(project_root):
        root_path = Path(root)
        directory_count += 1

        pruned_dirs: list[str] = []

        for dirname in dirs:
            dir_path = (root_path / dirname).resolve()

            if is_generated_dump_artifact(dir_path, zip_prefix):
                scan_warnings.append(f"SKIP DIR (generated dump output): {safe_rel_path(project_root, dir_path)}")
                continue

            pruned_dirs.append(dirname)

        dirs[:] = pruned_dirs

        for filename in files:
            path = (root_path / filename).resolve()

            if path == output_zip_path:
                continue

            if is_generated_dump_artifact(path, zip_prefix):
                scan_warnings.append(f"SKIP FILE (generated dump zip): {safe_rel_path(project_root, path)}")
                continue

            rel = safe_rel_path(project_root, path)

            try:
                stat = path.stat()
                modified_dt = datetime.fromtimestamp(stat.st_mtime)
                modified_utc_dt = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)

                record = FileRecord(
                    rel_path=rel,
                    abs_path=path,
                    size_bytes=int(stat.st_size),
                    modified_local=modified_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    modified_utc=modified_utc_dt.strftime("%Y-%m-%d %H:%M:%S UTC"),
                    extension=path.suffix.lower(),
                )

            except Exception as exc:
                records.append(
                    FileRecord(
                        rel_path=rel,
                        abs_path=path,
                        size_bytes=0,
                        modified_local="",
                        modified_utc="",
                        extension=path.suffix.lower(),
                        kind="metadata_error",
                        error=str(exc),
                    )
                )
                continue

            try:
                sample = read_sample(path)

                if is_probably_text_file(path, sample):
                    record.kind = "text"
                else:
                    record.kind = "binary"

            except Exception as exc:
                record.kind = "unreadable"
                record.error = str(exc)

            if calculate_hashes and record.kind != "metadata_error":
                try:
                    record.sha256 = sha256_file(path)
                except Exception as exc:
                    record.error = (record.error + "; " if record.error else "") + f"hash failed: {exc}"

            records.append(record)

    records.sort(key=lambda r: r.rel_path.lower())
    return records, scan_warnings, directory_count


def build_directory_tree(project_root: Path, output_zip_path: Path, zip_prefix: str) -> str:
    lines: list[str] = []

    project_root = project_root.resolve()
    output_zip_path = output_zip_path.resolve()

    lines.append("DIRECTORY TREE")
    lines.append(SEP)
    lines.append(f"{project_root.name}/")

    for root, dirs, files in os.walk(project_root):
        root_path = Path(root)

        pruned_dirs: list[str] = []

        for dirname in sorted(dirs, key=str.lower):
            dir_path = (root_path / dirname).resolve()

            if is_generated_dump_artifact(dir_path, zip_prefix):
                continue

            pruned_dirs.append(dirname)

        dirs[:] = pruned_dirs

        rel_parts = [] if root_path == project_root else list(root_path.relative_to(project_root).parts)
        indent = "    " * len(rel_parts)

        if rel_parts:
            lines.append(f"{indent}{root_path.name}/")

        child_indent = "    " * (len(rel_parts) + 1)

        for filename in sorted(files, key=str.lower):
            file_path = (root_path / filename).resolve()

            if file_path == output_zip_path:
                continue

            if is_generated_dump_artifact(file_path, zip_prefix):
                continue

            lines.append(f"{child_indent}{filename}")

    lines.append(SEP)
    lines.append("")
    return "\n".join(lines) + "\n"


def build_file_header(record: FileRecord) -> str:
    return (
        f"{SEP}\n"
        f"BEGIN FILE: {record.rel_path}\n"
        f"{SEP}\n"
        f"Relative path: {record.rel_path}\n"
        f"Size bytes: {record.size_bytes}\n"
        f"Size human: {format_size(record.size_bytes)}\n"
        f"Modified local: {record.modified_local}\n"
        f"Modified UTC: {record.modified_utc}\n"
        f"Extension: {record.extension or '[none]'}\n"
        f"Kind: {record.kind}\n"
        f"Encoding: {record.encoding or '[not applicable]'}\n"
        f"Newline: {record.newline or '[not applicable]'}\n"
        f"SHA256: {record.sha256 or '[not calculated]'}\n"
        f"Error: {record.error or '[none]'}\n"
        f"{SUBSEP}\n"
    )


def write_metadata_only_section(out: io.TextIOBase, record: FileRecord) -> None:
    out.write(build_file_header(record))
    out.write("[FILE CONTENTS NOT EMBEDDED]\n")

    if record.kind == "binary":
        out.write("Reason: file appears to be binary or compressed media.\n")
    elif record.kind == "unreadable":
        out.write("Reason: file could not be read.\n")
    elif record.kind == "metadata_error":
        out.write("Reason: file metadata could not be read.\n")
    else:
        out.write("Reason: metadata-only entry.\n")

    out.write(f"{SUBSEP}\n")
    out.write(f"END FILE: {record.rel_path}\n")
    out.write(f"{SEP}\n\n")


def write_text_file_section(out: io.TextIOBase, record: FileRecord, text: str) -> None:
    out.write(build_file_header(record))
    out.write(text)

    if text and not text.endswith("\n"):
        out.write("\n")

    out.write(f"{SUBSEP}\n")
    out.write(f"END FILE: {record.rel_path}\n")
    out.write(f"{SEP}\n\n")


def write_run_summary(
    out: io.TextIOBase,
    project_root: Path,
    output_zip_path: Path,
    dump_entry_name: str,
    timestamp: str,
    records: list[FileRecord],
    directory_count: int,
    scan_warnings: list[str],
    calculate_hashes: bool,
) -> None:
    total_size = sum(r.size_bytes for r in records)
    text_count = sum(1 for r in records if r.kind == "text")
    binary_count = sum(1 for r in records if r.kind == "binary")
    unreadable_count = sum(1 for r in records if r.kind in {"unreadable", "metadata_error"})

    out.write(f"{SEP}\n")
    out.write("PROJECT TEXT DUMP - SINGLE ZIP EDITION\n")
    out.write(f"{SEP}\n")
    out.write(f"Project root: {project_root.resolve()}\n")
    out.write(f"Run timestamp: {timestamp}\n")
    out.write(f"Output ZIP: {output_zip_path}\n")
    out.write(f"ZIP entry: {dump_entry_name}\n")
    out.write(f"Directories scanned: {directory_count}\n")
    out.write(f"Files logged: {len(records)}\n")
    out.write(f"Text/source files embedded: {text_count}\n")
    out.write(f"Binary metadata-only files: {binary_count}\n")
    out.write(f"Unreadable/metadata-error files: {unreadable_count}\n")
    out.write(f"Total logged file size: {total_size} bytes ({format_size(total_size)})\n")
    out.write(f"SHA-256 hashes calculated: {'yes' if calculate_hashes else 'no'}\n")
    out.write("Output model: one ZIP containing one consolidated UTF-8 text file.\n")
    out.write(f"{SEP}\n\n")

    if scan_warnings:
        out.write("SCAN WARNINGS / SKIPPED GENERATED OUTPUT\n")
        out.write(SUBSEP + "\n")

        for warning_text in scan_warnings:
            out.write(warning_text + "\n")

        out.write("\n")


def write_manifest(out: io.TextIOBase, records: list[FileRecord]) -> None:
    out.write("FILES MANIFEST\n")
    out.write(SEP + "\n")
    out.write(
        "Kind\t"
        "Size Bytes\t"
        "Size Human\t"
        "Modified Local\t"
        "Modified UTC\t"
        "Extension\t"
        "Encoding\t"
        "Newline\t"
        "SHA256\t"
        "Relative Path\t"
        "Error\n"
    )

    for record in records:
        out.write(
            f"{record.kind}\t"
            f"{record.size_bytes}\t"
            f"{format_size(record.size_bytes)}\t"
            f"{record.modified_local}\t"
            f"{record.modified_utc}\t"
            f"{record.extension or '[none]'}\t"
            f"{record.encoding or ''}\t"
            f"{record.newline or ''}\t"
            f"{record.sha256 or ''}\t"
            f"{record.rel_path}\t"
            f"{record.error or ''}\n"
        )

    out.write(SEP + "\n")
    out.write("END FILES MANIFEST\n")
    out.write(SEP + "\n\n")


def write_dump_zip(project_root: Path, output_zip_path: Path, calculate_hashes: bool, zip_prefix: str) -> tuple[Path, int, int]:
    project_root = project_root.resolve()
    output_zip_path = output_zip_path.resolve()

    output_zip_path.parent.mkdir(parents=True, exist_ok=True)

    info(f"Scanning project: {project_root}")

    records, scan_warnings, directory_count = collect_records(
        project_root=project_root,
        output_zip_path=output_zip_path,
        calculate_hashes=calculate_hashes,
        zip_prefix=zip_prefix,
    )

    total_size = sum(record.size_bytes for record in records)
    ok(f"Scanned {directory_count} directories")
    ok(f"Logged {len(records)} files ({format_size(total_size)})")

    timestamp = output_zip_path.stem.replace(f"{zip_prefix}_", "", 1)
    dump_entry_name = f"{output_zip_path.stem}.txt"

    info(f"Writing ZIP: {output_zip_path}")

    with zipfile.ZipFile(output_zip_path, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zip_out:
        with zip_out.open(dump_entry_name, mode="w") as raw_out:
            with io.TextIOWrapper(raw_out, encoding="utf-8", newline="\n") as out:
                write_run_summary(
                    out=out,
                    project_root=project_root,
                    output_zip_path=output_zip_path,
                    dump_entry_name=dump_entry_name,
                    timestamp=timestamp,
                    records=records,
                    directory_count=directory_count,
                    scan_warnings=scan_warnings,
                    calculate_hashes=calculate_hashes,
                )

                out.write(build_directory_tree(project_root, output_zip_path, zip_prefix))
                write_manifest(out, records)

                out.write("FILE CONTENTS\n")
                out.write(SEP + "\n\n")

                for idx, record in enumerate(records, start=1):
                    if idx % 100 == 0:
                        info(f"Writing file sections: {idx}/{len(records)}")

                    if record.kind != "text":
                        write_metadata_only_section(out, record)
                        continue

                    text, encoding, newline, error = read_text_file(record.abs_path)
                    record.encoding = encoding
                    record.newline = newline

                    if error:
                        record.error = (record.error + "; " if record.error else "") + error

                    if text == "" and record.size_bytes > 0 and error:
                        record.kind = "unreadable"
                        write_metadata_only_section(out, record)
                        continue

                    write_text_file_section(out, record, text)

                out.write("END PROJECT TEXT DUMP\n")
                out.write(SEP + "\n")

    return output_zip_path, len(records), directory_count


def select_project_root_with_folder_browser() -> Path | None:
    """
    Opens the folder browser immediately when no CLI project path is supplied.
    Falls back cleanly if tkinter is unavailable.
    """
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:
        return None

    root = tk.Tk()
    root.withdraw()

    selected = filedialog.askdirectory(title="Select Project Folder to Dump")

    root.destroy()

    if not selected:
        return None

    return Path(selected)


def resolve_interactive_project_root() -> Path | None:
    selected = select_project_root_with_folder_browser()

    if selected is not None:
        return selected

    print("C/C++ Project Dump Generator - Single ZIP Edition")
    print("Enter the project root folder to scan.")
    print("Press Enter without typing a path to use the folder containing this script.")
    raw = input("Project root: ").strip().strip('"')

    if raw:
        return Path(raw)

    return Path(__file__).resolve().parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate one ZIP file containing one consolidated text dump of a project/source tree."
    )

    parser.add_argument(
        "project_root",
        nargs="?",
        type=Path,
        help="Project root folder to scan. If omitted, a folder browser is opened.",
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Folder where the ZIP file should be written. Default: auto-created FDTO_output folder beside this script.",
    )

    parser.add_argument(
        "--zip-prefix",
        default=DEFAULT_ZIP_PREFIX,
        help=f"Output ZIP filename prefix. Default: {DEFAULT_ZIP_PREFIX}",
    )

    parser.add_argument(
        "--hash",
        action="store_true",
        help="Calculate SHA-256 for every file. Slower on large projects.",
    )

    return parser


def run(project_root: Path, args: argparse.Namespace) -> int:
    project_root = project_root.expanduser().resolve()

    if not project_root.exists():
        fail(f"Project root does not exist: {project_root}")
        return 1

    if not project_root.is_dir():
        fail(f"Project root is not a directory: {project_root}")
        return 1

    output_dir = args.output_dir.expanduser().resolve() if args.output_dir else get_default_output_directory()
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_project_folder_name = sanitize_filename_component(project_root.name)
    output_zip_path = output_dir / f"{safe_project_folder_name}_{timestamp}.zip"
    log_file_path = output_dir / DEFAULT_LOG_FILENAME

    set_log_file_path(log_file_path)
    append_log_block(
        "\n"
        f"{SEP}\n"
        f"FDTO RUN START: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"Project root: {project_root}\n"
        f"Output ZIP: {output_zip_path}\n"
        f"Log file: {log_file_path}\n"
        f"Hash mode: {'enabled' if args.hash else 'disabled'}\n"
        f"{SEP}\n"
    )

    output_zip_path, record_count, directory_count = write_dump_zip(
        project_root=project_root,
        output_zip_path=output_zip_path,
        calculate_hashes=args.hash,
        zip_prefix=args.zip_prefix,
    )

    ok(f"Created ZIP file: {output_zip_path}")
    ok(f"ZIP size: {format_size(output_zip_path.stat().st_size)}")
    ok(f"Directories scanned: {directory_count}")
    ok(f"Files logged: {record_count}")

    clipboard_ok, clipboard_error = copy_text_to_clipboard(str(output_zip_path))
    if clipboard_ok:
        ok(f"Copied ZIP file path to clipboard: {output_zip_path}")
    else:
        warn(f"Could not copy ZIP file path to clipboard: {clipboard_error}")

    ok(f"Run log updated: {log_file_path}")
    append_log_block(
        f"{SEP}\n"
        f"FDTO RUN END: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"Result: success\n"
        f"{SEP}\n"
    )
    return 0


def main() -> int:
    interactive = len(sys.argv) == 1

    parser = build_parser()
    args = parser.parse_args()

    if interactive:
        try:
            project_root = resolve_interactive_project_root()

            if project_root is None:
                print("No folder selected. Operation cancelled.")
                return 0

            return run(project_root, args)

        except KeyboardInterrupt:
            print("\nCancelled.")
            return 130

        except Exception as exc:
            fail(str(exc))
            return 1

    try:
        project_root = args.project_root

        if project_root is None:
            project_root = resolve_interactive_project_root()

        if project_root is None:
            print("No folder selected. Operation cancelled.")
            return 0

        return run(project_root, args)

    except KeyboardInterrupt:
        fail("Interrupted.")
        return 130

    except Exception as exc:
        fail(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
