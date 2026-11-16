"""Filesystem tools — all confined to the sandboxed workspace directory
(spec §15: workspace and filesystem boundaries). Paths that escape the
workspace raise PolicyError before any I/O happens.

Risk tiers: list/read = 1 (read), write/organize = 2 (reversible),
delete = 3 (sensitive — moves to .trash and requires explicit approval).
"""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from .base import ToolError, TIER_READ, TIER_REVERSIBLE, TIER_SENSITIVE, Tool, ToolResult, truncate
from ..core.policy import PolicyError, resolve_within_root, resolve_within_workspace

READ_LIMIT_BYTES = 256 * 1024

CATEGORY_BY_EXT: dict[str, str] = {}
for _category, _extensions in {
    "images": ".jpg .jpeg .png .gif .webp .heic .bmp .svg",
    "documents": ".pdf .doc .docx .txt .md .rtf .odt .xlsx .xls .csv .ppt .pptx",
    "audio": ".mp3 .wav .ogg .flac .m4a",
    "video": ".mp4 .mkv .mov .avi .webm",
    "archives": ".zip .tar .gz .rar .7z",
    "code": ".py .js .ts .html .css .json .sh .java .kt .ipynb",
}.items():
    for ext in _extensions.split():
        CATEGORY_BY_EXT[ext] = _category


def _category_for(name: str) -> str:
    ext = Path(name).suffix.lower()
    return CATEGORY_BY_EXT.get(ext, "other")


def _entry(path: Path) -> dict:
    stat = path.stat()
    return {
        "name": path.name,
        "type": "dir" if path.is_dir() else "file",
        "size": stat.st_size if path.is_file() else None,
        "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(timespec="seconds"),
    }


class FsList(Tool):
    key = "fs.list"
    name = "List directory"
    description = "List entries of a directory inside the sandboxed workspace."
    risk_tier = TIER_READ
    schema = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "relative path, default workspace root"}},
        "required": [],
    }

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        rel = str(args.get("path") or "")
        target = resolve_within_workspace(rel)
        if not target.exists():
            raise ToolError(f"path does not exist: {rel or '.'}")
        if not target.is_dir():
            raise ToolError(f"not a directory: {rel}")
        entries = sorted((_entry(p) for p in target.iterdir()), key=lambda e: (e["type"] != "dir", e["name"]))
        return ToolResult(
            success=True,
            action=self.key,
            summary=f"Listed {len(entries)} entr{'y' if len(entries) == 1 else 'ies'} in {rel or '.'}",
            output={"path": rel or ".", "entries": entries},
            verification={"path_checked": str(target), "exists": True, "entries_counted": len(entries)},
        )


class FsRead(Tool):
    key = "fs.read"
    name = "Read file"
    description = "Read a text file (up to 256 KB) from the sandboxed workspace."
    risk_tier = TIER_READ
    schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        target = resolve_within_workspace(str(args["path"]))
        if not target.is_file():
            raise ToolError(f"not a file: {args['path']}")
        data = target.read_bytes()[:READ_LIMIT_BYTES]
        text = data.decode("utf-8", errors="replace")
        return ToolResult(
            success=True,
            action=self.key,
            summary=f"Read {target.name} ({len(data)} bytes)",
            output={"path": args["path"], "content": truncate(text)},
            verification={"bytes_read": len(data), "path_checked": str(target)},
        )


class FsWrite(Tool):
    key = "fs.write"
    name = "Write file"
    description = "Create or overwrite a text file inside the sandboxed workspace."
    risk_tier = TIER_REVERSIBLE
    schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"],
    }

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        rel = str(args["path"])
        target = resolve_within_workspace(rel)
        if target.is_dir():
            raise ToolError(f"path is a directory: {rel}")
        if ctx.dry_run:
            return ToolResult(True, self.key, f"[dry-run] would write {rel}", output={"path": rel})
        target.parent.mkdir(parents=True, exist_ok=True)
        content = str(args["content"])
        target.write_text(content, encoding="utf-8")
        written = target.stat().st_size
        if written != len(content.encode("utf-8")):
            raise ToolError("size mismatch after write")
        return ToolResult(
            success=True,
            action=self.key,
            summary=f"Wrote {rel} ({written} bytes)",
            output={"path": rel, "bytes": written},
            verification={"path_exists": target.is_file(), "bytes_on_disk": written},
        )


class FsOrganize(Tool):
    key = "fs.organize"
    name = "Organize folder by type"
    description = (
        "Move loose files in a workspace folder into category subfolders "
        "(images, documents, audio, video, archives, code, other)."
    )
    risk_tier = TIER_REVERSIBLE
    schema = {
        "type": "object",
        "properties": {"source": {"type": "string", "description": "folder relative to workspace, default 'downloads'"}},
        "required": [],
    }

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        rel = str(args.get("source") or "downloads")
        source = resolve_within_workspace(rel)
        if not source.is_dir():
            raise ToolError(f"source folder not found: {rel}")

        files = [p for p in source.iterdir() if p.is_file()]
        moved: list[dict] = []
        for path in files:
            category = _category_for(path.name)
            dest_dir = resolve_within_root(source, category)
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / path.name
            if dest.exists():  # never overwrite — keep both, JARVIS is careful
                stem, suffix = path.stem, path.suffix
                dest = dest_dir / f"{stem}-{datetime.now(timezone.utc).strftime('%H%M%S')}{suffix}"
            shutil.move(str(path), str(dest))
            moved.append({"file": path.name, "category": category, "to": str(dest.relative_to(source))})

        remaining = [p for p in source.iterdir() if p.is_file()]
        return ToolResult(
            success=True,
            action=self.key,
            summary=f"Organized {len(moved)} file(s) in {rel} into category folders",
            output={"source": rel, "moved": moved, "remaining_loose_files": len(remaining)},
            verification={
                "files_before": len(files),
                "files_after": len(remaining),
                "moved_count": len(moved),
                "consistent": len(files) == len(moved) + len(remaining),
            },
        )


class FsDelete(Tool):
    key = "fs.delete"
    name = "Delete file (to trash)"
    description = (
        "Move a file to the workspace .trash folder. Sensitive (Tier 3): requires "
        "explicit user approval before execution."
    )
    risk_tier = TIER_SENSITIVE
    schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        rel = str(args["path"])
        target = resolve_within_workspace(rel)
        if not target.is_file():
            raise ToolError(f"not a file: {rel}")
        trash_dir = resolve_within_workspace(".trash")
        trash_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        dest = trash_dir / f"{target.name}.{stamp}"
        if dest.exists():
            raise PolicyError("trash collision — refusing to overwrite")
        shutil.move(str(target), str(dest))
        return ToolResult(
            success=True,
            action=self.key,
            summary=f"Moved {rel} to trash (recoverable)",
            output={"path": rel, "trash_path": str(dest)},
            verification={"original_gone": not target.exists(), "trash_exists": dest.is_file()},
        )


TOOLS: tuple[Tool, ...] = (FsList(), FsRead(), FsWrite(), FsOrganize(), FsDelete())
