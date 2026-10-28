from __future__ import annotations

import json
import math
import os
import shutil
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRect, QRectF, Qt, QTimer, QObject, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QFont, QGuiApplication, QImage, QImageReader, QLinearGradient, QMouseEvent, QPainter, QPen, QPixmap, QRegion
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QLabel,
    QLineEdit,
    QListView,
    QMenu,
    QMessageBox,
    QPushButton,
    QSlider,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from kimi_quota import QuotaError, QuotaSnapshot, fetch_quota

WIDTH = 320
HEIGHT = 300
REFRESH_MS = 5 * 60 * 1000


def config_dir() -> Path:
    override = os.environ.get("KIMI_WHALE_DATA_DIR")
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parent / "userdata"


def legacy_config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "KimiWhaleWidget"
    base = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser()
    return base / "kimi-whale-widget"


def migrate_legacy_config() -> None:
    source = legacy_config_dir()
    target = config_dir()
    if source == target or not source.is_dir():
        return
    try:
        target.mkdir(parents=True, exist_ok=True)
        for name in ("settings.json", "custom-role.png"):
            origin = source / name
            destination = target / name
            if origin.is_file() and not destination.exists():
                shutil.copy2(origin, destination)
        source_roles = source / "roles"
        target_roles = target / "roles"
        if source_roles.is_dir():
            target_roles.mkdir(parents=True, exist_ok=True)
            for origin in source_roles.glob("*.png"):
                destination = target_roles / origin.name
                if origin.is_file() and not destination.exists():
                    shutil.copy2(origin, destination)
    except OSError:
        pass


def percent_text(value: float | None) -> str:
    if value is None:
        return "--"
    if value.is_integer():
        return f"{value:.0f}%"
    return f"{value:.1f}%"


def countdown_text(value: datetime | None) -> str:
    if value is None:
        return "--:--:--"
    seconds = max(0, int((value - datetime.now().astimezone()).total_seconds()))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"


def usage_alert_threshold(previous_used: float, current_used: float) -> int | None:
    previous_level = max(0, math.floor((previous_used + 0.000001) / 10) * 10)
    current_level = min(100, max(0, math.floor((current_used + 0.000001) / 10) * 10))
    return current_level if current_level > previous_level else None


class RefreshBridge(QObject):
    finished = Signal(object, str)


class SettingsDialog(QDialog):
    def __init__(self, pet: WhalePet) -> None:
        super().__init__(None)
        self.pet = pet
        self.setWindowTitle(f"{pet.display_name()}设置")
        self.setMinimumSize(390, 330)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setAutoFillBackground(True)
        self.setStyleSheet(
            "QDialog { background: #f5f6fa; color: #34415f; }"
            "QLabel { font-size: 13px; }"
            "QLineEdit { background: white; border: 1px solid #c7ccd8; border-radius: 5px; padding: 6px; }"
            "QSlider::groove:horizontal { height: 6px; background: #d9dce6; border-radius: 3px; }"
            "QSlider::sub-page:horizontal { background: #293c73; border-radius: 3px; }"
            "QSlider::handle:horizontal { width: 18px; margin: -6px 0; background: #293c73; border-radius: 9px; }"
            "QCheckBox { font-size: 13px; spacing: 8px; }"
            "QPushButton { background: #293c73; color: white; border: 0; border-radius: 6px; padding: 8px 24px; }"
            "QPushButton:hover { background: #3a5290; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(18)
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(18)
        layout.addLayout(grid)

        role = QComboBox()
        for role_id, saved_role in pet.roles.items():
            role.addItem(saved_role["name"], role_id)
        if pet.active_role is not None:
            role.setCurrentIndex(role.findData(pet.active_role))
        grid.addWidget(QLabel("角色"), 0, 0)
        grid.addWidget(role, 0, 1)

        name_input = QLineEdit(pet.role_name)
        name_input.setMaxLength(20)
        name_input.setPlaceholderText("给角色起个名字")
        name_input.setEnabled(pet.role_mode == "custom")

        def update_title() -> None:
            self.setWindowTitle(f"{pet.display_name()}设置")
            name_input.setEnabled(pet.role_mode == "custom")
            delete_button.setEnabled(len(pet.roles) > 1)

        def select_role(index: int) -> None:
            pet.set_role(role.itemData(index))
            name_input.blockSignals(True)
            name_input.setText(pet.role_name)
            name_input.blockSignals(False)
            update_title()

        def rename_role(text: str) -> None:
            pet.set_role_name(text)
            role.setItemText(role.currentIndex(), pet.role_name)
            update_title()

        role.currentIndexChanged.connect(select_role)
        name_input.textChanged.connect(rename_role)
        grid.addWidget(QLabel("名称"), 1, 0)
        grid.addWidget(name_input, 1, 1)

        def import_image() -> None:
            dialog = QFileDialog(self)
            dialog.setWindowTitle("选择角色图片")
            dialog.setDirectory(str(Path.home()))
            dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
            dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
            dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
            dialog.setNameFilter("图片文件 (*.png *.jpg *.jpeg *.bmp *.webp);;所有文件 (*)")
            dialog.setLabelText(QFileDialog.DialogLabel.LookIn, "位置")
            dialog.setLabelText(QFileDialog.DialogLabel.FileName, "文件名")
            dialog.setLabelText(QFileDialog.DialogLabel.FileType, "文件类型")
            dialog.setLabelText(QFileDialog.DialogLabel.Accept, "打开")
            dialog.setLabelText(QFileDialog.DialogLabel.Reject, "取消")
            for button in dialog.findChildren(QPushButton):
                text = button.text().replace("&", "")
                if text == "Open":
                    button.setText("打开")
                elif text == "Cancel":
                    button.setText("取消")
            place_names = {
                "Computer": "计算机",
                "Home": "主目录",
                "Desktop": "桌面",
                "Recent": "最近使用",
            }
            for view in dialog.findChildren(QListView):
                model = view.model()
                if model is None:
                    continue
                for row in range(model.rowCount()):
                    index = model.index(row, 0)
                    translated = place_names.get(index.data())
                    if translated:
                        model.setData(index, translated)
            headers = {"Name": "名称", "Size": "大小", "Kind": "类型", "Date Modified": "修改日期"}
            for view in dialog.findChildren(QTreeView):
                model = view.model()
                if model is None:
                    continue
                for column in range(model.columnCount()):
                    text = model.headerData(column, Qt.Orientation.Horizontal)
                    translated = headers.get(text)
                    if translated:
                        model.setHeaderData(column, Qt.Orientation.Horizontal, translated)
            if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.selectedFiles():
                return
            path = dialog.selectedFiles()[0]
            error = pet.import_role(path)
            if error:
                QMessageBox.warning(self, "无法导入图片", error)
                return
            role.addItem(pet.role_name, pet.active_role)
            role.setCurrentIndex(role.count() - 1)
            name_input.setText(pet.role_name)
            update_title()

        import_button = QPushButton("导入图片")
        import_button.clicked.connect(import_image)
        grid.addWidget(import_button, 0, 2)

        def delete_image() -> None:
            if len(pet.roles) <= 1:
                return
            confirmation = QMessageBox(self)
            confirmation.setWindowTitle("删除自定义角色")
            confirmation.setText(f"确定删除“{pet.role_name}”的图片吗？")
            confirmation.setInformativeText("只会删除已导入的副本，不会影响你的原始图片。")
            delete_action = confirmation.addButton("删除", QMessageBox.ButtonRole.AcceptRole)
            confirmation.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            confirmation.exec()
            if confirmation.clickedButton() is not delete_action:
                return
            deleted_role = pet.active_role
            error = pet.delete_custom_role()
            if error:
                QMessageBox.warning(self, "删除失败", error)
                return
            role.removeItem(role.findData(deleted_role))
            role.setCurrentIndex(role.findData(pet.active_role))
            name_input.blockSignals(True)
            name_input.setText(pet.role_name)
            name_input.blockSignals(False)
            update_title()

        delete_button = QPushButton("删除图片")
        delete_button.setEnabled(len(pet.roles) > 1)
        delete_button.clicked.connect(delete_image)
        grid.addWidget(delete_button, 1, 2)

        grid.addWidget(QLabel("大小"), 2, 0)
        size_slider = QSlider(Qt.Orientation.Horizontal)
        size_slider.setRange(50, 200)
        size_slider.setValue(round(pet.scale_factor * 100))
        size_label = QLabel(f"{size_slider.value()}%")
        size_label.setMinimumWidth(48)
        size_slider.valueChanged.connect(lambda value: size_label.setText(f"{value}%"))
        size_slider.valueChanged.connect(lambda value: pet.set_scale(value / 100))
        grid.addWidget(size_slider, 2, 1)
        grid.addWidget(size_label, 2, 2)

        grid.addWidget(QLabel("Q弹强度"), 3, 0)
        elasticity_slider = QSlider(Qt.Orientation.Horizontal)
        elasticity_slider.setRange(0, 100)
        elasticity_slider.setValue(round(pet.elasticity * 100))
        elasticity_label = QLabel(f"{elasticity_slider.value()}%")
        elasticity_label.setMinimumWidth(48)
        elasticity_slider.valueChanged.connect(lambda value: elasticity_label.setText(f"{value}%"))
        elasticity_slider.valueChanged.connect(lambda value: pet.set_elasticity(value / 100))
        grid.addWidget(elasticity_slider, 3, 1)
        grid.addWidget(elasticity_label, 3, 2)

        grid.addWidget(QLabel("朝向"), 4, 0)
        direction = QComboBox()
        direction.addItems(["向右", "向左"])
        direction.setCurrentText("向右" if pet.facing == "right" else "向左")
        direction.currentTextChanged.connect(lambda text: pet.set_facing("right" if text == "向右" else "left"))
        grid.addWidget(direction, 4, 1, 1, 2)

        grid.addWidget(QLabel("边框颜色"), 5, 0)
        color_button = QPushButton()

        def show_color(color: QColor) -> None:
            text_color = "#172636" if color.lightness() > 150 else "#ffffff"
            color_button.setText(color.name())
            color_button.setStyleSheet(f"background: {color.name()}; color: {text_color};")

        def choose_color() -> None:
            dialog = QColorDialog(QColor(pet.bubble_color), self)
            dialog.setWindowTitle("选择气泡边框颜色")
            dialog.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)
            button_texts = {
                "OK": "确定",
                "Cancel": "取消",
                "Pick Screen Color": "屏幕取色",
                "Add to Custom Colors": "添加自定义颜色",
            }
            for button in dialog.findChildren(QPushButton):
                translated = button_texts.get(button.text().replace("&", ""))
                if translated:
                    button.setText(translated)
            for label in dialog.findChildren(QLabel):
                if label.text() == "Basic colors":
                    label.setText("基础颜色")
                elif label.text() == "Custom colors":
                    label.setText("自定义颜色")
            if dialog.exec() == QDialog.DialogCode.Accepted:
                color = dialog.currentColor()
                pet.set_bubble_color(color.name())
                show_color(color)

        show_color(QColor(pet.bubble_color))
        color_button.clicked.connect(choose_color)
        grid.addWidget(color_button, 5, 1, 1, 2)

        self._add_toggle(grid, 6, "显示额度泡泡", pet.bubble_visible, pet._toggle_bubble)
        self._add_toggle(grid, 7, "10 秒自动收回", pet.bubble_auto_hide, pet._toggle_bubble_auto_hide)
        self._add_toggle(grid, 8, "边缘吸附", pet.snap_to_edge, pet._toggle_snap)
        self._add_toggle(grid, 9, "窗口置顶", pet.topmost, pet._toggle_topmost)
        self._add_toggle(grid, 10, "上下浮动", pet.floating, pet._toggle_floating)

        grid.addWidget(QLabel("透明度"), 11, 0)
        opacity_slider = QSlider(Qt.Orientation.Horizontal)
        opacity_slider.setRange(50, 100)
        opacity_slider.setValue(round(pet.windowOpacity() * 100))
        opacity_label = QLabel(f"{opacity_slider.value()}%")
        opacity_label.setMinimumWidth(48)
        opacity_slider.valueChanged.connect(lambda value: opacity_label.setText(f"{value}%"))
        opacity_slider.valueChanged.connect(lambda value: pet._set_opacity(value / 100))
        grid.addWidget(opacity_slider, 11, 1)
        grid.addWidget(opacity_label, 11, 2)

        close_button = QPushButton("完成")
        close_button.clicked.connect(self.close)
        layout.addWidget(close_button, 0, Qt.AlignmentFlag.AlignCenter)

    @staticmethod
    def _add_toggle(grid: QGridLayout, row: int, label: str, checked: bool, toggle) -> None:
        box = QCheckBox(label)
        box.setChecked(checked)
        box.toggled.connect(lambda desired: toggle() if desired != checked else None)
        grid.addWidget(box, row, 1, 1, 2)


class WhalePet(QWidget):
    def __init__(self) -> None:
        super().__init__()
        migrate_legacy_config()
        self.settings = self._load_settings()
        self.snapshot: QuotaSnapshot | None = None
        self.error = ""
        self.loading = False
        self.closed = False
        saved_usage = self.settings.get("usage_monitor_used")
        self.usage_monitor_used = (
            float(saved_usage)
            if isinstance(saved_usage, (int, float)) and not isinstance(saved_usage, bool) and 0 <= saved_usage <= 100
            else None
        )
        saved_reset = self.settings.get("usage_monitor_reset", "")
        self.usage_monitor_reset = saved_reset if isinstance(saved_reset, str) else ""
        self.usage_alert: int | None = None
        self.usage_alert_until = 0.0
        self.bubble_visible = bool(self.settings.get("bubble_visible", True))
        self.bubble_auto_hide = bool(self.settings.get("bubble_auto_hide", True))
        self.snap_to_edge = bool(self.settings.get("snap_to_edge", True))
        self.floating = bool(self.settings.get("floating", True))
        self.bubble_color = str(self.settings.get("bubble_color", "#55c1ff"))
        if not QColor(self.bubble_color).isValid():
            self.bubble_color = "#55c1ff"
        self.facing = self.settings.get("facing", "right")
        if self.facing not in ("left", "right"):
            self.facing = "right"
        self.elasticity = min(1.0, max(0.0, float(self.settings.get("elasticity", 0.45))))
        self.roles: dict[str, dict] = {}
        roles_directory = config_dir() / "roles"
        saved_roles = self.settings.get("roles", [])
        if isinstance(saved_roles, list):
            for entry in saved_roles:
                if not isinstance(entry, dict):
                    continue
                role_id = entry.get("id")
                if not isinstance(role_id, str) or not role_id.isalnum() or len(role_id) > 64:
                    continue
                image = QImage(str(roles_directory / f"{role_id}.png"))
                if image.isNull():
                    continue
                name = " ".join(str(entry.get("name", "自定义图片")).split())[:20] or "自定义图片"
                self.roles[role_id] = {"name": name, "image": image}
        legacy_path = config_dir() / "custom-role.png"
        if not self.roles and legacy_path.is_file():
            image = QImage(str(legacy_path))
            if not image.isNull():
                name = " ".join(str(self.settings.get("role_name", "自定义图片")).split())[:20] or "自定义图片"
                self.roles["legacy"] = {"name": name, "image": image}
                try:
                    roles_directory.mkdir(parents=True, exist_ok=True)
                    image.save(str(roles_directory / "legacy.png"), "PNG")
                except OSError:
                    pass
        requested_role = self.settings.get("active_role")
        self.active_role = requested_role if requested_role in self.roles else next(iter(self.roles), None)
        self.custom_image = self.roles[self.active_role]["image"] if self.active_role else None
        self.custom_pixmap = None
        self.role_name = self.roles[self.active_role]["name"] if self.active_role else "自定义图片"
        self.role_mode = "custom" if self.active_role else "none"
        self.phase = 0.0
        self.bob = 0.0
        self.blink = False
        self.bubble_animation_started = time.monotonic()
        self.bubble_fade_started: float | None = None
        self.squeeze = 0.0
        self.squeeze_velocity = 0.0
        self.pressing = False
        self.drag_offset = QPoint()
        self.drag_start = QPoint()
        self.dragging = False
        self.moved = False
        self.snap_animation: QPropertyAnimation | None = None
        self.bridge = RefreshBridge()
        self.bridge.finished.connect(self._refresh_done)

        self.setWindowTitle("Kimi 桌宠" if self.role_mode == "none" else f"Kimi {self.display_name()}桌宠")
        self.scale_factor = min(2.0, max(0.5, float(self.settings.get("scale", 1.0))))
        self._refresh_custom_pixmap()
        self.setFixedSize(round(WIDTH * self.scale_factor), round(HEIGHT * self.scale_factor))
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        if sys.platform.startswith("linux"):
            flags |= Qt.WindowType.X11BypassWindowManagerHint
        elif sys.platform == "win32":
            flags |= Qt.WindowType.NoDropShadowWindowHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.setAutoFillBackground(False)
        self.setStyleSheet("background: transparent;")
        self.setMouseTracking(True)
        self.setWindowOpacity(float(self.settings.get("opacity", 0.98)))
        self.topmost = bool(self.settings.get("topmost", True))
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self.topmost)
        self._place_initial()
        self._update_mask()

        self.animation_timer = QTimer(self)
        self.animation_timer.timeout.connect(self._animate)
        self.animation_timer.start(45)
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(REFRESH_MS)
        self.refresh_timer.timeout.connect(self.refresh)
        self.refresh_timer.start()
        QTimer.singleShot(100, self.refresh)

    def _load_settings(self) -> dict:
        path = config_dir() / "settings.json"
        if not path.is_file():
            path = config_dir() / "default_settings.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_settings(self) -> None:
        data = {
            "x": self.x(),
            "y": self.y(),
            "topmost": self.topmost,
            "opacity": self.windowOpacity(),
            "bubble_visible": self.bubble_visible,
            "bubble_auto_hide": self.bubble_auto_hide,
            "usage_monitor_used": self.usage_monitor_used,
            "usage_monitor_reset": self.usage_monitor_reset,
            "snap_to_edge": self.snap_to_edge,
            "floating": self.floating,
            "scale": self.scale_factor,
            "facing": self.facing,
            "elasticity": self.elasticity,
            "bubble_color": self.bubble_color,
            "role": self.role_mode,
            "role_name": self.role_name,
            "active_role": self.active_role,
            "roles": [{"id": role_id, "name": role["name"]} for role_id, role in self.roles.items()],
        }
        try:
            directory = config_dir()
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / "settings.json"
            temporary = directory / "settings.json.tmp"
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(target)
        except OSError:
            pass

    def _place_initial(self) -> None:
        screen = QGuiApplication.primaryScreen().availableGeometry()
        x = int(self.settings.get("x", screen.right() - self.width() - 24))
        y = int(self.settings.get("y", screen.bottom() - self.height() - 40))
        x = min(max(screen.left(), x), max(screen.left(), screen.right() - self.width()))
        y = min(max(screen.top(), y), max(screen.top(), screen.bottom() - self.height()))
        self.move(x, y)

    def _font(self, size: int, bold: bool = False) -> QFont:
        family = "Microsoft YaHei UI" if sys.platform == "win32" else "Noto Sans CJK SC"
        font = QFont(family, size)
        font.setBold(bold)
        return font

    def _animate(self) -> None:
        self.phase += 1
        self.bob = math.sin(self.phase / 8) * 4 if self.floating else 0.0
        self.blink = self.phase % 115 in (108, 109, 110, 111)
        target = 0.24 * self.elasticity if self.pressing else 0.0
        self.squeeze_velocity += (target - self.squeeze) * 380 * 0.045
        self.squeeze_velocity *= math.exp(-11 * 0.045)
        self.squeeze += self.squeeze_velocity * 0.045
        self.squeeze = min(0.28 * self.elasticity, max(-0.12 * self.elasticity, self.squeeze))
        now = time.monotonic()
        if self.usage_alert is not None and now >= self.usage_alert_until:
            self.usage_alert = None
        if self.bubble_auto_hide and self.bubble_visible and self.bubble_fade_started is None and now - self.bubble_animation_started >= 10:
            self.bubble_fade_started = now
        if self.bubble_fade_started is not None and now - self.bubble_fade_started >= 0.22:
            self.bubble_visible = False
            self.bubble_fade_started = None
            self._update_mask()
            self._save_settings()
        self.update()

    def _update_mask(self) -> None:
        if sys.platform.startswith("linux"):
            self.clearMask()
            if QGuiApplication.platformName() == "xcb":
                QTimer.singleShot(0, self._set_x11_input_shape)
            return
        region = QRegion()
        for x, y, width, height in self._input_rectangles():
            region = region.united(QRegion(
                round(x * self.scale_factor),
                round(y * self.scale_factor),
                round(width * self.scale_factor),
                round(height * self.scale_factor),
            ))
        if sys.platform == "win32":
            self.clearMask()
            self.windows_input_region = region
            return
        self.setMask(region)

    @staticmethod
    def _ellipse_rectangles(x: int, y: int, width: int, height: int) -> list[tuple[int, int, int, int]]:
        rectangles = []
        for top in range(0, height, 6):
            middle = top + 3
            half_width = width / 2 * math.sqrt(max(0, 1 - ((middle - height / 2) / (height / 2)) ** 2))
            rectangles.append((round(x + width / 2 - half_width), y + top, round(half_width * 2), min(6, height - top)))
        return rectangles

    def _custom_rect(self) -> QRectF:
        image = self.custom_image
        scale = min(200 / image.width(), 170 / image.height())
        width = image.width() * scale
        height = image.height() * scale
        return QRectF(160 - width / 2, 280 - height, width, height)

    def _refresh_custom_pixmap(self) -> None:
        if self.custom_image is None:
            self.custom_pixmap = None
            return
        area = self._custom_rect()
        ratio = self.scale_factor * self.devicePixelRatioF()
        target_width = max(1, round(area.width() * ratio))
        target_height = max(1, round(area.height() * ratio))
        image = self.custom_image
        while image.width() > target_width * 2 or image.height() > target_height * 2:
            image = image.scaled(
                max(target_width, image.width() // 2),
                max(target_height, image.height() // 2),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        image = image.scaled(
            target_width,
            target_height,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.custom_pixmap = QPixmap.fromImage(image)
        self.custom_pixmap.setDevicePixelRatio(ratio)

    def _custom_input_rectangles(self) -> list[tuple[int, int, int, int]]:
        area = self._custom_rect()
        image = self.custom_image
        rectangles = []
        left = math.floor(area.left())
        top = math.floor(area.top())
        for y in range(top, math.ceil(area.bottom()), 3):
            start = None
            for x in range(left, math.ceil(area.right()) + 1):
                image_x = math.floor((x + 0.5 - area.left()) * image.width() / area.width())
                image_y = math.floor((y + 1 - area.top()) * image.height() / area.height())
                opaque = (
                    x < area.right()
                    and 0 <= image_x < image.width()
                    and 0 <= image_y < image.height()
                    and image.pixelColor(image_x, image_y).alpha() > 32
                )
                if opaque and start is None:
                    start = x
                elif not opaque and start is not None:
                    rectangles.append((start, y, x - start, min(3, math.ceil(area.bottom()) - y)))
                    start = None
        if self.facing == "left":
            rectangles = [(320 - x - width, y, width, height) for x, y, width, height in rectangles]
        return rectangles

    def _input_rectangles(self) -> list[tuple[int, int, int, int]]:
        rectangles = self._custom_input_rectangles() if self.role_mode == "custom" else []
        if self.bubble_visible:
            bubble_x = 5 if self._bubble_on_left() else 115
            rectangles.extend(self._ellipse_rectangles(bubble_x, 10, 200, 130))
            for x, y, width, _delay in self._bubble_dots():
                rectangles.extend(self._ellipse_rectangles(x, y, width, width))
        return rectangles

    def _set_x11_input_shape(self) -> None:
        try:
            import ctypes
            import ctypes.util

            x11 = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
            xext = ctypes.CDLL(ctypes.util.find_library("Xext") or "libXext.so.6")

            class XRectangle(ctypes.Structure):
                _fields_ = [("x", ctypes.c_short), ("y", ctypes.c_short), ("width", ctypes.c_ushort), ("height", ctypes.c_ushort)]

            x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
            x11.XOpenDisplay.restype = ctypes.c_void_p
            x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
            x11.XFlush.argtypes = [ctypes.c_void_p]
            xext.XShapeCombineRectangles.argtypes = [
                ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                ctypes.POINTER(XRectangle), ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ]
            display = x11.XOpenDisplay(None)
            if not display:
                return
            try:
                source = self._input_rectangles()
                rectangles = (XRectangle * len(source))()
                for index, (x, y, width, height) in enumerate(source):
                    rectangles[index] = XRectangle(
                        round(x * self.scale_factor), round(y * self.scale_factor),
                        round(width * self.scale_factor), round(height * self.scale_factor),
                    )
                xext.XShapeCombineRectangles(display, self.winId(), 2, 0, 0, rectangles, len(source), 0, 0)
                x11.XFlush(display)
            finally:
                x11.XCloseDisplay(display)
        except (OSError, AttributeError):
            pass

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._update_mask()

    def nativeEvent(self, eventType, message):
        if sys.platform == "win32" and eventType == b"windows_generic":
            import ctypes

            class WindowsMessage(ctypes.Structure):
                _fields_ = [
                    ("hwnd", ctypes.c_void_p),
                    ("message", ctypes.c_uint),
                    ("wParam", ctypes.c_size_t),
                    ("lParam", ctypes.c_ssize_t),
                    ("time", ctypes.c_ulong),
                    ("x", ctypes.c_long),
                    ("y", ctypes.c_long),
                ]

            native_message = WindowsMessage.from_address(int(message))
            if native_message.message == 0x0084:
                packed = native_message.lParam & 0xFFFFFFFF
                global_x = packed & 0xFFFF
                global_y = (packed >> 16) & 0xFFFF
                if global_x >= 0x8000:
                    global_x -= 0x10000
                if global_y >= 0x8000:
                    global_y -= 0x10000
                local = self.mapFromGlobal(QPoint(global_x, global_y))
                if not self.windows_input_region.contains(local):
                    return True, -1
        return super().nativeEvent(eventType, message)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(self.scale_factor, self.scale_factor)
        if self.role_mode == "custom":
            self._draw_shadow(painter)
            self._draw_custom(painter)
        if self.bubble_visible:
            self._draw_bubble(painter)

    def _draw_shadow(self, painter: QPainter) -> None:
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(4, 15, 25, 45))
        painter.drawEllipse(QRectF(98, 279, 118, 15))
        painter.restore()

    def _draw_custom(self, painter: QPainter) -> None:
        painter.save()
        painter.translate(160, 280)
        painter.scale(1 + self.squeeze * 0.45, 1 - self.squeeze)
        painter.translate(-160, -280)
        if self.facing == "left":
            painter.translate(320, 0)
            painter.scale(-1, 1)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        target = self._custom_rect()
        target.translate(0, self.bob)
        painter.drawPixmap(target, self.custom_pixmap, QRectF(self.custom_pixmap.rect()))
        painter.restore()

    def _bubble_on_left(self) -> bool:
        screen = QGuiApplication.screenAt(self.geometry().center()) or QGuiApplication.primaryScreen()
        return self.geometry().center().x() > screen.availableGeometry().center().x()

    def _bubble_dots(self) -> tuple[tuple[int, int, int, float], ...]:
        dots = ((88, 140, 20, 0.14), (113, 162, 13, 0.07), (137, 179, 9, 0.0))
        if self._bubble_on_left():
            return dots
        return tuple((WIDTH - x - width, y, width, delay) for x, y, width, delay in dots)

    def _shimmer_pen(self, bubble_x: int, base: str, highlight: str) -> QPen:
        progress = (self.phase % 105) / 105
        center = bubble_x + 225 - progress * 250
        gradient = QLinearGradient(center - 32, 0, center + 32, 0)
        gradient.setColorAt(0, QColor(base))
        gradient.setColorAt(0.5, QColor(highlight))
        gradient.setColorAt(1, QColor(base))
        return QPen(QBrush(gradient), 1)

    def _draw_bubble(self, painter: QPainter) -> None:
        elapsed = time.monotonic() - self.bubble_animation_started
        fade_opacity = 1.0
        if self.bubble_fade_started is not None:
            fade_opacity = max(0.0, 1 - (time.monotonic() - self.bubble_fade_started) / 0.22)

        def progress(delay: float) -> float:
            return min(1.0, max(0.0, (elapsed - delay) / 0.18))

        outline = QColor(self.bubble_color)
        fill = QColor("#fffefa")
        for x, y, width, delay in self._bubble_dots():
            value = progress(delay)
            if value <= 0:
                continue
            eased = 1 - (1 - value) ** 3
            scale = 0.35 + 0.65 * eased
            center_x, center_y = x + width / 2, y + width / 2
            painter.save()
            painter.setOpacity(value * fade_opacity)
            painter.translate(center_x, center_y)
            painter.scale(scale, scale)
            painter.translate(-center_x, -center_y)
            painter.setPen(QPen(outline, 3))
            painter.setBrush(fill)
            painter.drawEllipse(QRectF(x, y, width, width))
            painter.restore()

        main_progress = progress(0.20)
        if main_progress <= 0:
            return
        bubble_x = 5 if self._bubble_on_left() else 115
        eased = 1 - (1 - main_progress) ** 3
        scale = 0.55 + 0.45 * eased
        painter.save()
        painter.setOpacity(main_progress * fade_opacity)
        painter.translate(bubble_x + 100, 75)
        painter.scale(scale, scale)
        painter.translate(-bubble_x - 100, -75)
        painter.setPen(QPen(outline, 4))
        painter.setBrush(fill)
        painter.drawEllipse(QRectF(bubble_x, 10, 200, 130))

        painter.setPen(self._shimmer_pen(bubble_x, "#596b84", "#8eacc2"))
        painter.setFont(self._font(11, True))
        title = "5 小时额度提醒" if self.usage_alert is not None else "5 小时剩余"
        painter.drawText(QRect(bubble_x + 10, 21, 180, 24), Qt.AlignmentFlag.AlignCenter, title)
        if self.error and self.snapshot is None:
            painter.setPen(QColor("#b06b45"))
            painter.setFont(self._font(9))
            painter.drawText(
                QRect(bubble_x + 15, 50, 170, 65),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                self.error,
            )
            painter.restore()
            return

        remaining_5h = self.snapshot.limit_5h.remaining_percent if self.snapshot else None
        remaining_7d = self.snapshot.limit_7d.remaining_percent if self.snapshot and self.snapshot.limit_7d else None
        main_text = f"已用 {self.usage_alert}%" if self.usage_alert is not None else percent_text(remaining_5h)
        painter.setPen(self._shimmer_pen(bubble_x, "#536b9b", "#74add2"))
        painter.setFont(self._font(25, True))
        painter.drawText(QRect(bubble_x + 10, 42, 180, 46), Qt.AlignmentFlag.AlignCenter, main_text)

        painter.setPen(QColor("#748197"))
        painter.setFont(self._font(10))
        if self.usage_alert is not None:
            detail = f"当前剩余 {percent_text(remaining_5h)}"
        else:
            detail = "新套餐无每周额度" if self.snapshot is not None and self.snapshot.limit_7d is None else f"每周剩余 {percent_text(remaining_7d)}"
        painter.drawText(QRect(bubble_x + 10, 86, 180, 20), Qt.AlignmentFlag.AlignCenter, detail)

        reset_5h = countdown_text(self.snapshot.limit_5h.reset_at) if self.snapshot else "--:--:--"
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#dcf3df"))
        painter.drawRoundedRect(QRectF(bubble_x + 45, 106, 110, 17), 4, 4)
        painter.setPen(QColor("#47975a"))
        painter.setFont(self._font(7, True))
        painter.drawText(
            QRect(bubble_x + 45, 106, 110, 17),
            Qt.AlignmentFlag.AlignCenter,
            f"5小时 {reset_5h} 后重置",
        )
        painter.restore()

    def refresh(self) -> None:
        if self.loading or self.closed:
            return
        self.loading = True
        self.update()
        threading.Thread(target=self._fetch_in_background, daemon=True).start()

    def _fetch_in_background(self) -> None:
        try:
            snapshot = fetch_quota()
            error = ""
        except QuotaError as exc:
            snapshot = None
            error = str(exc)
        except Exception:
            snapshot = None
            error = "刷新失败，请稍后重试"
        if not self.closed:
            self.bridge.finished.emit(snapshot, error)

    def _refresh_done(self, snapshot: QuotaSnapshot | None, error: str) -> None:
        if self.closed:
            return
        if snapshot is not None:
            self.snapshot = snapshot
            self._monitor_usage(snapshot)
        self.error = error
        self.loading = False
        self.update()

    def _monitor_usage(self, snapshot: QuotaSnapshot) -> None:
        current_used = round(100 - snapshot.limit_5h.remaining_percent, 1)
        reset_at = snapshot.limit_5h.reset_at
        reset_key = reset_at.isoformat() if reset_at is not None else ""
        previous_used = self.usage_monitor_used
        new_cycle = (
            previous_used is None
            or reset_key != self.usage_monitor_reset
            or current_used < previous_used
        )
        threshold = None if new_cycle else usage_alert_threshold(previous_used, current_used)
        self.usage_monitor_used = current_used
        self.usage_monitor_reset = reset_key
        if new_cycle:
            self.usage_alert = None
        if threshold is None:
            self._save_settings()
        else:
            self._show_usage_alert(threshold)

    def _show_usage_alert(self, threshold: int) -> None:
        now = time.monotonic()
        self.usage_alert = threshold
        self.usage_alert_until = now + 6
        self.bubble_visible = True
        self.bubble_animation_started = now
        self.bubble_fade_started = None
        self._update_mask()
        self._save_settings()
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self._show_menu(event.globalPosition().toPoint())
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        point = event.position() / self.scale_factor
        self.pressing = self.role_mode == "custom" and self._custom_rect().translated(0, self.bob).contains(point)
        if self.pressing:
            self.squeeze = max(self.squeeze, 0.12 * self.elasticity)
        self.drag_offset = event.globalPosition().toPoint() - self.pos()
        self.drag_start = event.globalPosition().toPoint()
        self.dragging = True
        self.moved = False
        if self.snap_animation is not None:
            self.snap_animation.stop()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self.dragging:
            return
        point = event.globalPosition().toPoint()
        if (point - self.drag_start).manhattanLength() > 5:
            self.moved = True
        if self.moved:
            bubble_on_left = self._bubble_on_left()
            self.move(point - self.drag_offset)
            if bubble_on_left != self._bubble_on_left():
                self._update_mask()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self.pressing = False
        if not self.dragging:
            return
        self.dragging = False
        if self.moved:
            self._snap_to_edge()
        else:
            opening = not self.bubble_visible or self.bubble_fade_started is not None
            self._toggle_bubble()
            if opening:
                self.refresh()

    def mouseDoubleClickEvent(self, _event: QMouseEvent) -> None:
        self.refresh()

    def _snap_to_edge(self) -> None:
        if not self.snap_to_edge:
            self._save_settings()
            return
        screen = QGuiApplication.screenAt(self.geometry().center()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        target_x = area.left() + 8 if self.x() + self.width() / 2 < area.center().x() else area.right() - self.width() - 8
        target_y = min(max(area.top() + 8, self.y()), area.bottom() - self.height() - 8)
        self.snap_animation = QPropertyAnimation(self, b"pos", self)
        self.snap_animation.setDuration(220)
        self.snap_animation.setStartValue(self.pos())
        self.snap_animation.setEndValue(QPoint(target_x, target_y))
        self.snap_animation.setEasingCurve(QEasingCurve.Type.OutBack)
        self.snap_animation.finished.connect(self._save_settings)
        self.snap_animation.start()

    def _show_menu(self, point: QPoint) -> None:
        menu = QMenu()
        menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        menu.setAutoFillBackground(True)
        menu.setWindowOpacity(1.0)
        menu.setStyleSheet(
            "QMenu { background-color: #f5f8fb; color: #172636;"
            " border: 1px solid #aebdcb; padding: 4px; }"
            "QMenu::item { padding: 7px 26px 7px 12px; border-radius: 4px; }"
            "QMenu::item:selected { background-color: #2589c9; color: white; }"
            "QMenu::separator { height: 1px; background: #c9d4dd; margin: 4px 8px; }"
        )
        refresh_action = QAction("立即刷新", self)
        refresh_action.triggered.connect(self.refresh)
        menu.addAction(refresh_action)
        bubble_action = QAction("显示额度泡泡", self, checkable=True)
        bubble_action.setChecked(self.bubble_visible)
        bubble_action.triggered.connect(self._toggle_bubble)
        menu.addAction(bubble_action)
        top_action = QAction("窗口置顶", self, checkable=True)
        top_action.setChecked(self.topmost)
        top_action.triggered.connect(self._toggle_topmost)
        menu.addAction(top_action)
        opacity_menu = menu.addMenu("透明度")
        for value in (0.75, 0.9, 1.0):
            action = QAction(f"{value:.0%}", self, checkable=True)
            action.setChecked(abs(self.windowOpacity() - value) < 0.05)
            action.triggered.connect(lambda _checked=False, opacity=value: self._set_opacity(opacity))
            opacity_menu.addAction(action)
        settings_action = QAction("设置", self)
        settings_action.triggered.connect(self._show_settings)
        menu.addAction(settings_action)
        menu.addSeparator()
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self.close)
        menu.addAction(quit_action)
        menu.exec(point)

    def _toggle_bubble(self) -> None:
        if self.bubble_visible and self.bubble_fade_started is None:
            self.bubble_fade_started = time.monotonic()
        else:
            self.bubble_visible = True
            self.bubble_animation_started = time.monotonic()
            self.bubble_fade_started = None
            self._update_mask()
            self._save_settings()
        self.update()

    def _toggle_bubble_auto_hide(self) -> None:
        self.bubble_auto_hide = not self.bubble_auto_hide
        if not self.bubble_auto_hide:
            self.bubble_fade_started = None
        self._save_settings()

    def import_role(self, path: str) -> str:
        try:
            if Path(path).stat().st_size > 30 * 1024 * 1024:
                return "图片不能超过 30 MB，请压缩后再导入。"
        except OSError as exc:
            return f"无法读取图片：{exc}"
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        image = reader.read()
        if image.isNull():
            return "无法识别这张图片，请选择有效的 PNG、JPG、BMP 或 WebP 文件。"
        if image.width() > 4096 or image.height() > 4096:
            image = image.scaled(4096, 4096, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        role_id = uuid.uuid4().hex
        try:
            directory = config_dir() / "roles"
            directory.mkdir(parents=True, exist_ok=True)
            temporary = directory / f"{role_id}.tmp.png"
            if not image.save(str(temporary), "PNG"):
                return "图片保存失败，请检查配置目录是否可写。"
            temporary.replace(directory / f"{role_id}.png")
        except OSError as exc:
            return f"图片保存失败：{exc}"
        name = " ".join(Path(path).stem.split())[:20] or "自定义图片"
        self.roles[role_id] = {"name": name, "image": image}
        self.active_role = role_id
        self.custom_image = image
        self._refresh_custom_pixmap()
        if self.custom_pixmap.isNull():
            return "图片保存后无法加载，请换一张图片重试。"
        self.role_mode = "custom"
        self.role_name = name
        self.setWindowTitle(f"Kimi {self.display_name()}桌宠")
        self._update_mask()
        self._save_settings()
        self.update()
        return ""

    def delete_custom_role(self) -> str:
        if self.active_role is None or len(self.roles) <= 1:
            return "最后一张图片不允许删除。"
        role_id = self.active_role
        try:
            (config_dir() / "roles" / f"{role_id}.png").unlink(missing_ok=True)
        except OSError as exc:
            return f"无法删除已导入的图片：{exc}"
        del self.roles[role_id]
        self.active_role = next(iter(self.roles))
        self.custom_image = self.roles[self.active_role]["image"]
        self.role_name = self.roles[self.active_role]["name"]
        self.role_mode = "custom"
        self.pressing = False
        self._refresh_custom_pixmap()
        self.setWindowTitle(f"Kimi {self.display_name()}桌宠")
        self._update_mask()
        self._save_settings()
        self.update()
        return ""

    def display_name(self) -> str:
        return self.role_name if self.role_mode == "custom" else "桌宠"

    def set_role(self, role_id: str) -> None:
        if role_id not in self.roles or role_id == self.active_role:
            return
        self.active_role = role_id
        self.custom_image = self.roles[role_id]["image"]
        self.role_name = self.roles[role_id]["name"]
        self.role_mode = "custom"
        self.pressing = False
        self._refresh_custom_pixmap()
        self.setWindowTitle(f"Kimi {self.display_name()}桌宠")
        self._update_mask()
        self._save_settings()
        self.update()

    def set_role_name(self, name: str) -> None:
        self.role_name = " ".join(name.split())[:20] or "自定义图片"
        if self.active_role is not None:
            self.roles[self.active_role]["name"] = self.role_name
        self.setWindowTitle(f"Kimi {self.display_name()}桌宠")
        self._save_settings()

    def set_facing(self, facing: str) -> None:
        if facing not in ("left", "right") or facing == self.facing:
            return
        self.facing = facing
        self._update_mask()
        self._save_settings()
        self.update()

    def _toggle_facing(self) -> None:
        self.set_facing("left" if self.facing == "right" else "right")

    def set_elasticity(self, value: float) -> None:
        self.elasticity = min(1.0, max(0.0, value))
        if self.elasticity == 0:
            self.squeeze = 0.0
            self.squeeze_velocity = 0.0
        self._save_settings()
        self.update()

    def set_bubble_color(self, value: str) -> None:
        color = QColor(value)
        if not color.isValid():
            return
        self.bubble_color = color.name()
        self._save_settings()
        self.update()

    def _toggle_floating(self) -> None:
        self.floating = not self.floating
        if not self.floating:
            self.bob = 0.0
        self._save_settings()
        self.update()

    def _toggle_snap(self) -> None:
        self.snap_to_edge = not self.snap_to_edge
        self._save_settings()

    def _toggle_topmost(self) -> None:
        self.topmost = not self.topmost
        position = self.pos()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self.topmost)
        self.show()
        self.move(position)
        self._save_settings()

    def _set_opacity(self, value: float) -> None:
        self.setWindowOpacity(value)
        self._save_settings()

    def set_scale(self, value: float) -> None:
        value = min(2.0, max(0.5, value))
        if abs(value - self.scale_factor) < 0.001:
            return
        center = self.geometry().center()
        self.scale_factor = value
        self._refresh_custom_pixmap()
        self.setFixedSize(round(WIDTH * value), round(HEIGHT * value))
        self.move(center.x() - self.width() // 2, center.y() - self.height() // 2)
        self._update_mask()
        self._save_settings()
        self.update()

    def _show_settings(self) -> None:
        dialog = SettingsDialog(self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        self.settings_dialog = dialog

    def closeEvent(self, event) -> None:
        self.closed = True
        self.animation_timer.stop()
        self.refresh_timer.stop()
        self._save_settings()
        event.accept()
        QApplication.quit()


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Kimi 桌宠")
    app.setQuitOnLastWindowClosed(False)
    pet = WhalePet()
    pet.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
