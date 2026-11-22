"""Local user sticker library and compact picker for the V2 chat composer."""

from __future__ import annotations

import json
import mimetypes
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, QStandardPaths, Qt, Signal
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


MAX_STICKER_SIZE = 2 * 1024 * 1024
ALLOWED_STICKER_MIME = {"image/png", "image/jpeg", "image/gif", "image/webp"}
MIME_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}


def _matches_mime(raw: bytes, mime: str) -> bool:
    if mime == "image/png":
        return raw.startswith(b"\x89PNG\r\n\x1a\n")
    if mime == "image/jpeg":
        return raw.startswith(b"\xff\xd8\xff")
    if mime == "image/gif":
        return raw.startswith((b"GIF87a", b"GIF89a"))
    if mime == "image/webp":
        return len(raw) >= 12 and raw.startswith(b"RIFF") and raw[8:12] == b"WEBP"
    return False


class LocalStickerLibrary:
    """Persist user-imported stickers without changing their original files."""

    def __init__(self, root: Path | None = None) -> None:
        default_root = Path(
            QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
        ) / "stickers"
        self.root = Path(root or default_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.json"
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        try:
            loaded = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded = {}
        entries = loaded.get("entries") if isinstance(loaded, dict) else None
        recent = loaded.get("recent") if isinstance(loaded, dict) else None
        return {
            "entries": entries if isinstance(entries, list) else [],
            "recent": recent if isinstance(recent, list) else [],
        }

    def _save(self) -> None:
        temporary = self.index_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, self.index_path)

    def _public(self, entry: dict[str, Any]) -> dict[str, Any]:
        filename = Path(str(entry["filename"])).name
        return {
            **entry,
            "filename": filename,
            "path": str(self.root / filename),
            "animated": entry.get("mime") == "image/gif",
        }

    def _entry(self, sticker_id: str) -> dict[str, Any]:
        entry = next(
            (item for item in self._data["entries"] if item.get("id") == sticker_id), None
        )
        if entry is None:
            raise KeyError(sticker_id)
        return entry

    def import_file(self, source: Path | str) -> dict[str, Any]:
        path = Path(source)
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise ValueError("无法读取所选表情包") from exc
        if not 0 < size <= MAX_STICKER_SIZE:
            raise ValueError("表情包不能为空且不能超过 2 MiB")
        mime = mimetypes.guess_type(path.name)[0] or ""
        if mime not in ALLOWED_STICKER_MIME:
            raise ValueError("仅支持 PNG、JPEG、GIF 和 WebP 表情包")
        raw = path.read_bytes()
        if not _matches_mime(raw, mime) or QImage.fromData(raw).isNull():
            raise ValueError("图片内容无效或与文件类型不匹配")
        sticker_id = uuid.uuid4().hex
        filename = f"{sticker_id}{MIME_EXTENSIONS[mime]}"
        target = self.root / filename
        shutil.copyfile(path, target)
        entry = {
            "id": sticker_id,
            "name": path.stem[:80] or "表情包",
            "filename": filename,
            "source_name": path.name[:120],
            "mime": mime,
            "size": size,
            "favorite": False,
            "created_at": time.time(),
        }
        self._data["entries"].append(entry)
        self._save()
        return self._public(entry)

    def list(self, section: str = "mine") -> list[dict[str, Any]]:
        entries = [
            item
            for item in self._data["entries"]
            if (self.root / Path(str(item.get("filename", ""))).name).is_file()
        ]
        if section == "recent":
            by_id = {item.get("id"): item for item in entries}
            entries = [by_id[item_id] for item_id in self._data["recent"] if item_id in by_id]
        elif section == "favorites":
            entries = [item for item in entries if item.get("favorite")]
            entries.sort(key=lambda item: float(item.get("created_at", 0)), reverse=True)
        elif section == "mine":
            entries.sort(key=lambda item: float(item.get("created_at", 0)), reverse=True)
        else:
            raise ValueError(f"unknown sticker section: {section}")
        return [self._public(item) for item in entries]

    def set_favorite(self, sticker_id: str, favorite: bool) -> dict[str, Any]:
        entry = self._entry(sticker_id)
        entry["favorite"] = bool(favorite)
        self._save()
        return self._public(entry)

    def mark_used(self, sticker_id: str) -> None:
        self._entry(sticker_id)
        recent = [item for item in self._data["recent"] if item != sticker_id]
        self._data["recent"] = [sticker_id, *recent][:40]
        self._save()

    def remove(self, sticker_id: str) -> None:
        entry = self._entry(sticker_id)
        path = self.root / Path(str(entry["filename"])).name
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        self._data["entries"] = [
            item for item in self._data["entries"] if item.get("id") != sticker_id
        ]
        self._data["recent"] = [item for item in self._data["recent"] if item != sticker_id]
        self._save()


class StickerPanel(QDialog):
    """Small tabbed picker; imported assets remain local until the user sends one."""

    sticker_selected = Signal(dict)

    def __init__(self, library: LocalStickerLibrary, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.library = library
        self.setWindowTitle("表情包")
        self.setObjectName("StickerPanel")
        self.resize(520, 420)
        layout = QVBoxLayout(self)
        hint = QLabel("双击表情包可直接发送；本地导入由当前用户管理。")
        hint.setObjectName("Muted")
        layout.addWidget(hint)
        self.tabs = QTabWidget()
        self.lists: dict[str, QListWidget] = {}
        for section, title in (
            ("recent", "最近使用"),
            ("favorites", "收藏/默认"),
            ("mine", "我的表情"),
        ):
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(0, 8, 0, 0)
            items = QListWidget()
            items.setObjectName("StickerGrid")
            items.setViewMode(QListWidget.ViewMode.IconMode)
            items.setResizeMode(QListWidget.ResizeMode.Adjust)
            items.setMovement(QListWidget.Movement.Static)
            items.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            items.setIconSize(QSize(88, 88))
            items.setGridSize(QSize(112, 124))
            items.itemDoubleClicked.connect(self._send_item)
            page_layout.addWidget(items)
            self.tabs.addTab(page, title)
            self.lists[section] = items
        layout.addWidget(self.tabs, 1)
        actions = QHBoxLayout()
        self.import_button = QPushButton("导入本地表情")
        self.import_button.setObjectName("Ghost")
        self.favorite_button = QPushButton("收藏/取消收藏")
        self.favorite_button.setObjectName("Ghost")
        self.remove_button = QPushButton("移除")
        self.remove_button.setObjectName("Ghost")
        self.send_button = QPushButton("发送")
        self.send_button.setObjectName("Primary")
        self.close_button = QPushButton("关闭")
        self.close_button.setObjectName("Ghost")
        self.close_button.setAccessibleName("关闭表情包面板")
        actions.addWidget(self.import_button)
        actions.addWidget(self.favorite_button)
        actions.addWidget(self.remove_button)
        actions.addStretch()
        actions.addWidget(self.close_button)
        actions.addWidget(self.send_button)
        layout.addLayout(actions)
        self.import_button.clicked.connect(self._import)
        self.favorite_button.clicked.connect(self._toggle_favorite)
        self.remove_button.clicked.connect(self._remove)
        self.close_button.clicked.connect(self.reject)
        self.send_button.clicked.connect(self._send_current)
        self.refresh()

    def refresh(self) -> None:
        for section, widget in self.lists.items():
            widget.clear()
            for sticker in self.library.list(section):
                item = QListWidgetItem(sticker["name"])
                item.setData(Qt.ItemDataRole.UserRole, sticker)
                pixmap = QPixmap(sticker["path"])
                if not pixmap.isNull():
                    item.setIcon(
                        QIcon(
                            pixmap.scaled(
                                88,
                                88,
                                Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.SmoothTransformation,
                            )
                        )
                    )
                suffix = " · GIF" if sticker.get("animated") else ""
                item.setToolTip(f"{sticker['name']}{suffix}")
                widget.addItem(item)

    def _current_item(self) -> QListWidgetItem | None:
        section = ("recent", "favorites", "mine")[self.tabs.currentIndex()]
        return self.lists[section].currentItem()

    def _current_sticker(self) -> dict[str, Any] | None:
        item = self._current_item()
        value = item.data(Qt.ItemDataRole.UserRole) if item else None
        return value if isinstance(value, dict) else None

    def _import(self) -> None:
        path, _selected = QFileDialog.getOpenFileName(
            self, "导入表情包", "", "图片 (*.png *.jpg *.jpeg *.gif *.webp)"
        )
        if not path:
            return
        try:
            sticker = self.library.import_file(path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "导入失败", str(exc))
            return
        self.refresh()
        self.tabs.setCurrentIndex(2)
        mine = self.lists["mine"]
        for index in range(mine.count()):
            if mine.item(index).data(Qt.ItemDataRole.UserRole).get("id") == sticker["id"]:
                mine.setCurrentRow(index)
                break

    def _toggle_favorite(self) -> None:
        sticker = self._current_sticker()
        if not sticker:
            return
        self.library.set_favorite(sticker["id"], not bool(sticker.get("favorite")))
        self.refresh()

    def _remove(self) -> None:
        sticker = self._current_sticker()
        if not sticker:
            return
        answer = QMessageBox.question(
            self,
            "移除表情包",
            f"从我的表情中移除“{sticker['name']}”？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.library.remove(sticker["id"])
            self.refresh()

    def _send_current(self) -> None:
        sticker = self._current_sticker()
        if sticker:
            self._emit_sticker(sticker)

    def _send_item(self, item: QListWidgetItem) -> None:
        sticker = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(sticker, dict):
            self._emit_sticker(sticker)

    def _emit_sticker(self, sticker: dict[str, Any]) -> None:
        self.sticker_selected.emit(sticker)
        self.accept()
