"""Authentication page."""

from __future__ import annotations

from typing import Any
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QVBoxLayout, QWidget
from client.models import AppState
from ..icons import brand_logo_icon, lucide_icon_with_color
from ..theme import active_theme_manager, current_tokens
from ..widgets import configure_surface


class AuthPage(QWidget):
    submitted = Signal(str, str, str)
    HERO_BREAKPOINT = 1000

    def __init__(self, host: str, port: int) -> None:
        super().__init__()
        configure_surface(self, "canvas")
        self._host = host
        self._port = port
        self._password_hidden = True
        self._feature_icons: list[tuple[QLabel, str, str, int]] = []

        self.outer = QHBoxLayout(self)
        self.outer.setContentsMargins(0, 0, 0, 0)
        self.outer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.shell = QWidget()
        self.shell.setObjectName("AuthShell")
        self.shell.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.shell_layout = QHBoxLayout(self.shell)
        self.shell_layout.setContentsMargins(0, 0, 0, 0)
        self.shell_layout.setSpacing(0)
        self.hero = self._build_hero()
        self.card = self._build_card()
        self.shell_layout.addWidget(self.hero, 1)
        self.shell_layout.addWidget(self.card, 0)
        self.outer.addWidget(self.shell, 1)

        self._apply_responsive_layout(self.width())
        manager = active_theme_manager()
        if manager is not None:
            manager.themeChanged.connect(self._refresh_icons)
        self._refresh_icons()

        self.setTabOrder(self.login_mode, self.register_mode)
        self.setTabOrder(self.register_mode, self.username)
        self.setTabOrder(self.username, self.password)
        self.setTabOrder(self.password, self.submit)
        self.login_mode.clicked.connect(self._update_mode)
        self.register_mode.clicked.connect(self._update_mode)
        self.submit.clicked.connect(self._submit)
        self.password.returnPressed.connect(self._submit)
        self.username.returnPressed.connect(self.password.setFocus)
        self.username.textChanged.connect(self._clear_error)
        self.password.textChanged.connect(self._clear_error)
        self.username.setFocus()

    def _build_hero(self) -> QWidget:
        hero = QWidget()
        hero.setObjectName("AuthHeroPanel")
        hero.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(hero)
        layout.setContentsMargins(56, 52, 56, 52)
        layout.setSpacing(0)
        brand = QHBoxLayout()
        brand.setSpacing(12)
        self.brand_tile = QLabel()
        self.brand_tile.setObjectName("AuthBrandTile")
        self.brand_tile.setFixedSize(42, 42)
        self.brand_tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
        names = QVBoxLayout()
        names.setSpacing(1)
        wordmark = QLabel("Parlor")
        wordmark.setObjectName("AuthWordmark")
        wordmark_meta = QLabel("智能聊天室")
        wordmark_meta.setObjectName("AuthWordmarkMeta")
        names.addWidget(wordmark)
        names.addWidget(wordmark_meta)
        brand.addWidget(self.brand_tile, 0, Qt.AlignmentFlag.AlignVCenter)
        brand.addLayout(names)
        brand.addStretch(1)
        layout.addLayout(brand)
        layout.addSpacing(72)
        eyebrow = QLabel("PARLOR · 智能聊天室")
        eyebrow.setObjectName("AuthWordmarkMeta")
        layout.addWidget(eyebrow)
        layout.addSpacing(14)
        headline = QLabel("让人和 Agent\n坐在同一张桌子旁")
        headline.setObjectName("AuthHeroTitle")
        headline.setWordWrap(True)
        layout.addWidget(headline)
        layout.addSpacing(18)
        intro = QLabel("一个安静的会客厅，用于团队协作、任务推进与 AI 助手对话。消息、人物和任务都在同一个房间里。")
        intro.setObjectName("AuthHeroText")
        intro.setWordWrap(True)
        intro.setMaximumWidth(380)
        layout.addWidget(intro)
        layout.addSpacing(44)
        features = (
            ("message-circle", "primary", "群聊、私信与实时协作"),
            ("bot", "agent", "Agent 对话与 AI 任务"),
            ("users", "primary", "团队通讯录与成员状态"),
        )
        for icon_name, color_key, text in features:
            row = QHBoxLayout()
            row.setSpacing(12)
            icon = QLabel()
            icon.setFixedSize(20, 20)
            icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label = QLabel(text)
            label.setObjectName("AuthFeatureText")
            row.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(label, 0, Qt.AlignmentFlag.AlignVCenter)
            row.addStretch(1)
            layout.addLayout(row)
            layout.addSpacing(14)
            self._feature_icons.append((icon, icon_name, color_key, 18))
        layout.addStretch(1)
        return hero

    def _build_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("AuthFormPanel")
        card.setFixedWidth(520)
        card.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(56, 48, 56, 48)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.form_card = QFrame()
        self.form_card.setObjectName("AuthFormCard")
        self.form_card.setFixedWidth(408)
        form_layout = QVBoxLayout(self.form_card)
        form_layout.setContentsMargins(32, 30, 32, 26)
        form_layout.setSpacing(0)
        layout.addWidget(self.form_card)
        layout = form_layout

        self.heading = QLabel("欢迎回来")
        self.heading.setObjectName("AuthHeading")
        self.subtitle = QLabel("登录后继续你的协作与对话")
        self.subtitle.setObjectName("Muted")
        self.subtitle.setWordWrap(True)
        layout.addWidget(self.heading)
        layout.addSpacing(6)
        layout.addWidget(self.subtitle)
        layout.addSpacing(22)

        track = QFrame()
        track.setObjectName("AuthModeTrack")
        track_layout = QHBoxLayout(track)
        track_layout.setContentsMargins(3, 3, 3, 3)
        track_layout.setSpacing(3)
        self.login_mode = QPushButton("登录")
        self.register_mode = QPushButton("注册")
        for button in (self.login_mode, self.register_mode):
            button.setObjectName("AuthModeTab")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            track_layout.addWidget(button, 1)
        self.login_mode.setChecked(True)
        modes = QButtonGroup(self)
        modes.setExclusive(True)
        modes.addButton(self.login_mode)
        modes.addButton(self.register_mode)
        layout.addWidget(track)
        layout.addSpacing(20)

        self.username_label = QLabel("账号")
        self.username_label.setObjectName("AuthFieldLabel")
        self.username = QLineEdit()
        self.username.setObjectName("AuthInput")
        self.username.setMinimumHeight(40)
        self.username.setPlaceholderText("请输入账号")
        self.username.setAccessibleName("账号")
        self.username_action = self.username.addAction(
            lucide_icon_with_color("user-round", current_tokens().text_tertiary, 16),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        layout.addWidget(self.username_label)
        layout.addSpacing(6)
        layout.addWidget(self.username)
        layout.addSpacing(14)

        self.password_label = QLabel("密码")
        self.password_label.setObjectName("AuthFieldLabel")
        self.password = QLineEdit()
        self.password.setObjectName("AuthInput")
        self.password.setMinimumHeight(40)
        self.password.setPlaceholderText("至少 8 位字符")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setAccessibleName("密码")
        self.password_action = self.password.addAction(
            lucide_icon_with_color("lock", current_tokens().text_tertiary, 16),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        self.reveal_action = self.password.addAction(
            lucide_icon_with_color("eye", current_tokens().text_tertiary, 16),
            QLineEdit.ActionPosition.TrailingPosition,
        )
        self.reveal_action.setToolTip("显示密码")
        self.reveal_action.setText("显示密码")
        self.reveal_action.triggered.connect(self._toggle_password)
        layout.addWidget(self.password_label)
        layout.addSpacing(6)
        layout.addWidget(self.password)
        layout.addSpacing(12)

        self.error = QLabel("")
        self.error.setObjectName("InlineNotice")
        self.error.setProperty("noticeKind", "error")
        self.error.setWordWrap(True)
        self.error.hide()
        layout.addWidget(self.error)
        layout.addSpacing(12)

        self.submit = QPushButton("登录")
        self.submit.setObjectName("Primary")
        self.submit.setMinimumHeight(42)
        self.submit.setCursor(Qt.CursorShape.PointingHandCursor)
        self.submit.setAccessibleName("登录")
        layout.addWidget(self.submit)
        layout.addSpacing(18)

        divider = QFrame()
        divider.setObjectName("CardDivider")
        divider.setFixedHeight(1)
        layout.addWidget(divider)
        layout.addSpacing(12)

        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        self.status_dot = QLabel()
        self.status_dot.setObjectName("AuthStatusDot")
        self.status_dot.setProperty("connectionState", "pending")
        self.status_dot.setFixedSize(8, 8)
        self.status = QLabel("正在连接…")
        self.status.setObjectName("AuthStatusText")
        server = QLabel(f"· {self._host}:{self._port}")
        server.setObjectName("AuthServer")
        status_row.addStretch(1)
        status_row.addWidget(self.status_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        status_row.addWidget(self.status)
        status_row.addWidget(server)
        status_row.addStretch(1)
        layout.addLayout(status_row)
        return card

    def _icon_pixmap(self, name: str, color_key: str, size: int) -> Any:
        tokens = current_tokens()
        color = tokens.brand_accent if color_key == "agent" else tokens.on_brand_panel
        return lucide_icon_with_color(name, color, size).pixmap(size, size)

    def _refresh_icons(self, *_args: object) -> None:
        tokens = current_tokens()
        self.brand_tile.setPixmap(brand_logo_icon(32, on_dark=True).pixmap(32, 32))
        for label, name, color_key, size in self._feature_icons:
            label.setPixmap(self._icon_pixmap(name, color_key, size))
        self.username_action.setIcon(lucide_icon_with_color("user-round", tokens.text_tertiary, 16))
        self.password_action.setIcon(lucide_icon_with_color("lock", tokens.text_tertiary, 16))
        self.reveal_action.setIcon(
            lucide_icon_with_color("eye" if self._password_hidden else "eye-off", tokens.text_tertiary, 16)
        )

    def _toggle_password(self) -> None:
        self._password_hidden = not self._password_hidden
        self.password.setEchoMode(
            QLineEdit.EchoMode.Password if self._password_hidden else QLineEdit.EchoMode.Normal
        )
        self.reveal_action.setToolTip("显示密码" if self._password_hidden else "隐藏密码")
        self.reveal_action.setText("显示密码" if self._password_hidden else "隐藏密码")
        self._refresh_icons()
        self.password.setFocus()

    def _show_error(self, message: str) -> None:
        self.error.setText(message)
        self.error.show()

    def _clear_error(self, *_args: object) -> None:
        if self.error.isVisible() or self.error.text():
            self.error.clear()
            self.error.hide()

    def _update_mode(self) -> None:
        registering = self.register_mode.isChecked()
        self.submit.setText("创建账号" if registering else "登录")
        self.submit.setAccessibleName("创建账号" if registering else "登录")
        self.heading.setText("创建账号" if registering else "欢迎回来")
        self.subtitle.setText("加入大厅，与团队和 Agent 开始协作" if registering else "登录后继续你的协作与对话")
        self._clear_error()
        self.username.setFocus()

    def _submit(self) -> None:
        username = self.username.text().strip()
        password = self.password.text()
        if not 3 <= len(username) <= 20:
            self._show_error("账号需要 3–20 个字符")
            self.username.setFocus()
            return
        if len(password) < 8:
            self._show_error("密码至少需要 8 个字符")
            self.password.setFocus()
            return
        self._clear_error()
        self.submit.setEnabled(False)
        mode = "register" if self.register_mode.isChecked() else "login"
        self.submitted.emit(mode, username, password)

    def _apply_responsive_layout(self, width: int) -> None:
        wide = width >= self.HERO_BREAKPOINT
        self.hero.setVisible(wide)
        self.shell_layout.setSpacing(0)
        if wide:
            self.shell.setMinimumWidth(0)
            self.shell.setMaximumWidth(16_777_215)
            self.card.setFixedWidth(520)
            self.form_card.setFixedWidth(408)
            self.outer.setContentsMargins(0, 0, 0, 0)
            self.outer.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        else:
            self.shell.setMinimumWidth(0)
            self.shell.setMaximumWidth(480)
            self.card.setFixedWidth(480)
            self.form_card.setFixedWidth(400)
            self.outer.setContentsMargins(24, 24, 24, 24)
            self.outer.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._apply_responsive_layout(event.size().width())

    def update_state(self, topic: str, state: AppState) -> None:
        self.status.setText(state.connection_text)
        text = state.connection_text
        if state.connected:
            connection_state = "online"
        elif "重连" in text or "正在连接" in text:
            connection_state = "pending"
        else:
            connection_state = "offline"
        if self.status_dot.property("connectionState") != connection_state:
            self.status_dot.setProperty("connectionState", connection_state)
            self.status_dot.style().unpolish(self.status_dot)
            self.status_dot.style().polish(self.status_dot)
        self.submit.setEnabled(state.connected and not any(action.startswith("auth.") for action in state.pending.values()))
        if topic in {"auth", "error"} and state.last_error:
            self._show_error(state.last_error)
