"""Tkinter desktop interface for the AI Agent chatroom."""

from __future__ import annotations

import base64
import mimetypes
import os
import queue
import time
import tkinter as tk
from collections import defaultdict
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any

from .network import NetworkClient

MAX_IMAGE_SIZE = 2 * 1024 * 1024
POLL_INTERVAL_MS = 80
EMOJIS = ("😀", "😂", "😍", "👍", "🎉", "❤️", "🤔", "😢")


def _as_list(payload: dict[str, Any], *names: str) -> list[Any]:
    for name in names:
        value = payload.get(name)
        if isinstance(value, list):
            return value
    return []


def _item_id(item: Any, *names: str) -> str:
    if isinstance(item, dict):
        for name in names:
            if item.get(name) is not None:
                return str(item[name])
    return str(item)


def _item_name(item: Any, *names: str) -> str:
    if isinstance(item, dict):
        for name in names:
            if item.get(name):
                return str(item[name])
    return str(item)


class ChatApplication:
    """Complete desktop client; all widget access remains on the main thread."""

    def __init__(self, host: str, port: int) -> None:
        self.root = tk.Tk()
        self.root.title("AI Agent 智能聊天室")
        self.root.geometry("1180x760")
        self.root.minsize(940, 620)

        self.network = NetworkClient(host, port)
        self.host = host
        self.port = port
        self.connected = False
        self.authenticated = False
        self.auto_login = False
        self.credentials: tuple[str, str] | None = None
        self.current_user: dict[str, Any] = {}
        self.current_scope: tuple[str, str] | None = None
        self.users: list[Any] = []
        self.groups: list[Any] = []
        self.members: list[Any] = []
        self.user_row_ids: list[str] = []
        self.group_row_ids: list[str] = []
        self.member_rows: dict[str, Any] = {}
        self.messages: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        self.pending: dict[str, str] = {}
        self.tool_rows: dict[str, str] = {}

        self.connection_var = tk.StringVar(value="正在连接…")
        self.agent_var = tk.StringVar(value="Agent 状态未知")
        self.conversation_var = tk.StringVar(value="请选择用户或群组")
        self.username_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.search_var = tk.StringVar()
        self.schedule_var = tk.StringVar(value="60")
        self.login_status_var = tk.StringVar(
            value=f"服务器：{self.host}:{self.port}"
        )

        self._configure_style()
        self._build_login()
        self._build_main()
        self._show_login()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(POLL_INTERVAL_MS, self._poll_events)
        self.network.start()

    def run(self) -> None:
        self.root.mainloop()

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        for theme in ("aqua", "clam", "default"):
            if theme in style.theme_names():
                try:
                    style.theme_use(theme)
                    break
                except tk.TclError:
                    continue
        style.configure("Heading.TLabel", font=("TkDefaultFont", 15, "bold"))
        style.configure("Muted.TLabel", foreground="#666666")

    def _build_login(self) -> None:
        self.login_frame = ttk.Frame(self.root, padding=30)
        card = ttk.Frame(self.login_frame, padding=28)
        card.pack(expand=True)
        ttk.Label(card, text="AI Agent 智能聊天室", style="Heading.TLabel").grid(
            row=0, column=0, columnspan=2, pady=(0, 22)
        )
        ttk.Label(card, text="用户名").grid(row=1, column=0, sticky="e", padx=8, pady=8)
        self.username_entry = ttk.Entry(card, textvariable=self.username_var, width=28)
        self.username_entry.grid(row=1, column=1, pady=8)
        ttk.Label(card, text="密码").grid(row=2, column=0, sticky="e", padx=8, pady=8)
        password_entry = ttk.Entry(
            card, textvariable=self.password_var, show="•", width=28
        )
        password_entry.grid(row=2, column=1, pady=8)
        buttons = ttk.Frame(card)
        buttons.grid(row=3, column=0, columnspan=2, pady=(14, 8))
        self.login_button = ttk.Button(buttons, text="登录", command=self._login)
        self.login_button.pack(side="left", padx=5)
        self.register_button = ttk.Button(buttons, text="注册", command=self._register)
        self.register_button.pack(side="left", padx=5)
        ttk.Label(card, textvariable=self.login_status_var, style="Muted.TLabel").grid(
            row=4, column=0, columnspan=2, pady=(10, 0)
        )
        self.username_entry.bind("<Return>", lambda _event: self._login())
        password_entry.bind("<Return>", lambda _event: self._login())

    def _build_main(self) -> None:
        self.main_frame = ttk.Frame(self.root, padding=8)
        top = ttk.Frame(self.main_frame)
        top.pack(fill="x", pady=(0, 7))
        ttk.Label(top, text="AI Agent 智能聊天室", style="Heading.TLabel").pack(
            side="left"
        )
        ttk.Label(top, textvariable=self.connection_var).pack(side="left", padx=18)
        ttk.Label(top, textvariable=self.agent_var).pack(side="left", padx=4)
        ttk.Button(top, text="退出登录", command=self._logout).pack(side="right")

        body = ttk.Panedwindow(self.main_frame, orient="horizontal")
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body, padding=(0, 0, 6, 0), width=220)
        center = ttk.Frame(body, padding=(6, 0))
        right = ttk.Frame(body, padding=(6, 0, 0, 0), width=270)
        body.add(left, weight=1)
        body.add(center, weight=4)
        body.add(right, weight=2)
        self._build_sidebar(left)
        self._build_conversation(center)
        self._build_details(right)

    def _build_sidebar(self, parent: ttk.Frame) -> None:
        notebook = ttk.Notebook(parent)
        notebook.pack(fill="both", expand=True)
        user_tab = ttk.Frame(notebook, padding=5)
        group_tab = ttk.Frame(notebook, padding=5)
        notebook.add(user_tab, text="用户")
        notebook.add(group_tab, text="群组")

        self.user_list = tk.Listbox(user_tab, exportselection=False)
        self.user_list.pack(fill="both", expand=True)
        self.user_list.bind("<<ListboxSelect>>", self._select_user)
        ttk.Button(user_tab, text="刷新在线用户", command=self._request_presence).pack(
            fill="x", pady=(5, 0)
        )

        self.group_list = tk.Listbox(group_tab, exportselection=False)
        self.group_list.pack(fill="both", expand=True)
        self.group_list.bind("<<ListboxSelect>>", self._select_group)
        group_buttons = ttk.Frame(group_tab)
        group_buttons.pack(fill="x", pady=(5, 0))
        ttk.Button(group_buttons, text="建群", command=self._create_group).pack(
            side="left", fill="x", expand=True, padx=(0, 2)
        )
        ttk.Button(group_buttons, text="加入", command=self._join_group).pack(
            side="left", fill="x", expand=True, padx=(2, 0)
        )
        ttk.Button(group_tab, text="刷新群组", command=self._request_groups).pack(
            fill="x", pady=(4, 0)
        )

    def _build_conversation(self, parent: ttk.Frame) -> None:
        header = ttk.Frame(parent)
        header.pack(fill="x")
        ttk.Label(header, textvariable=self.conversation_var, style="Heading.TLabel").pack(
            side="left"
        )
        self.search_entry = ttk.Entry(header, textvariable=self.search_var, width=20)
        self.search_entry.pack(side="right", padx=(4, 0))
        ttk.Button(header, text="搜索", command=self._search_history).pack(side="right")
        self.search_entry.bind("<Return>", lambda _event: self._search_history())

        message_frame = ttk.Frame(parent)
        message_frame.pack(fill="both", expand=True, pady=7)
        scrollbar = ttk.Scrollbar(message_frame)
        scrollbar.pack(side="right", fill="y")
        self.message_text = tk.Text(
            message_frame,
            wrap="word",
            state="disabled",
            yscrollcommand=scrollbar.set,
            padx=8,
            pady=8,
        )
        self.message_text.pack(fill="both", expand=True)
        scrollbar.configure(command=self.message_text.yview)
        self.message_text.tag_configure("system", foreground="#777777")
        self.message_text.tag_configure("agent", foreground="#5e35b1")
        self.message_text.tag_configure("recalled", foreground="#999999")

        agent_bar = ttk.Frame(parent)
        agent_bar.pack(fill="x", pady=(0, 5))
        ttk.Button(
            agent_bar, text="Agent 总结", command=lambda: self._invoke_agent("总结最近 50 条消息")
        ).pack(side="left")
        ttk.Button(
            agent_bar, text="提取待办", command=lambda: self._invoke_agent("提取最近 50 条消息中的待办")
        ).pack(side="left", padx=4)
        ttk.Button(agent_bar, text="撤回选中/最后消息", command=self._recall_message).pack(
            side="left"
        )

        compose_bar = ttk.Frame(parent)
        compose_bar.pack(fill="x")
        self.compose = tk.Text(compose_bar, height=4, wrap="word")
        self.compose.pack(side="left", fill="x", expand=True)
        self.compose.bind("<Control-Return>", lambda _event: self._send_text())
        controls = ttk.Frame(compose_bar)
        controls.pack(side="right", fill="y", padx=(6, 0))
        ttk.Button(controls, text="发送 Ctrl+Enter", command=self._send_text).pack(
            fill="x"
        )
        ttk.Button(controls, text="发送图片", command=self._send_image).pack(
            fill="x", pady=3
        )
        ttk.Button(controls, text="保存最近图片", command=self._save_latest_image).pack(
            fill="x", pady=(0, 3)
        )
        emoji_button = ttk.Menubutton(controls, text="表情")
        emoji_menu = tk.Menu(emoji_button, tearoff=False)
        for emoji in EMOJIS:
            emoji_menu.add_command(
                label=emoji, command=lambda value=emoji: self.compose.insert("insert", value)
            )
        emoji_button.configure(menu=emoji_menu)
        emoji_button.pack(fill="x")

    def _build_details(self, parent: ttk.Frame) -> None:
        notebook = ttk.Notebook(parent)
        notebook.pack(fill="both", expand=True)
        members_tab = ttk.Frame(notebook, padding=5)
        tools_tab = ttk.Frame(notebook, padding=5)
        notebook.add(members_tab, text="成员与管理")
        notebook.add(tools_tab, text="Agent 工具日志")

        self.member_tree = ttk.Treeview(
            members_tab, columns=("name", "status"), show="headings", height=14
        )
        self.member_tree.heading("name", text="成员")
        self.member_tree.heading("status", text="状态")
        self.member_tree.column("name", width=115)
        self.member_tree.column("status", width=70)
        self.member_tree.pack(fill="both", expand=True)
        manage = ttk.Frame(members_tab)
        manage.pack(fill="x", pady=5)
        ttk.Button(manage, text="禁言", command=lambda: self._moderate("mute")).pack(
            side="left", expand=True, fill="x", padx=(0, 2)
        )
        ttk.Button(
            manage, text="解禁", command=lambda: self._moderate("unmute")
        ).pack(side="left", expand=True, fill="x", padx=(2, 0))

        schedule = ttk.LabelFrame(members_tab, text="定时总结（分钟）", padding=5)
        schedule.pack(fill="x", pady=(8, 0))
        ttk.Entry(schedule, textvariable=self.schedule_var, width=8).pack(side="left")
        ttk.Button(schedule, text="启用", command=lambda: self._set_schedule(True)).pack(
            side="left", padx=3
        )
        ttk.Button(schedule, text="停用", command=lambda: self._set_schedule(False)).pack(
            side="left"
        )

        self.tool_tree = ttk.Treeview(
            tools_tab,
            columns=("time", "tool", "requester", "scope", "status", "count", "elapsed"),
            show="headings",
        )
        tool_scroll = ttk.Scrollbar(tools_tab, orient="horizontal", command=self.tool_tree.xview)
        self.tool_tree.configure(xscrollcommand=tool_scroll.set)
        for key, title, width in (
            ("time", "时间", 70),
            ("tool", "工具", 125),
            ("requester", "调用者", 70),
            ("scope", "授权会话", 90),
            ("status", "状态", 70),
            ("count", "条数", 45),
            ("elapsed", "耗时", 60),
        ):
            self.tool_tree.heading(key, text=title)
            self.tool_tree.column(key, width=width, stretch=key == "tool")
        self.tool_tree.pack(fill="both", expand=True)
        tool_scroll.pack(fill="x")

    def _show_login(self) -> None:
        self.main_frame.pack_forget()
        self.login_frame.pack(fill="both", expand=True)
        self.username_entry.focus_set()

    def _show_main(self) -> None:
        self.login_frame.pack_forget()
        self.main_frame.pack(fill="both", expand=True)

    def _request(self, event_type: str, payload: dict[str, Any] | None = None) -> str | None:
        try:
            request_id = self.network.send(event_type, payload or {})
        except (ConnectionError, ValueError) as exc:
            self._status(f"请求未发送：{exc}")
            return None
        self.pending[request_id] = event_type
        return request_id

    def _login(self) -> None:
        username = self.username_var.get().strip()
        password = self.password_var.get()
        if not username or not password:
            messagebox.showwarning("登录", "请输入用户名和密码")
            return
        self.credentials = (username, password)
        self.login_status_var.set("正在登录…")
        self._request("auth.login", {"username": username, "password": password})

    def _register(self) -> None:
        username = self.username_var.get().strip()
        password = self.password_var.get()
        if len(username) < 3 or len(username) > 20:
            messagebox.showwarning("注册", "用户名长度应为 3–20 个字符")
            return
        if len(password) < 8:
            messagebox.showwarning("注册", "密码至少需要 8 个字符")
            return
        self.credentials = (username, password)
        self.login_status_var.set("正在注册…")
        self._request("auth.register", {"username": username, "password": password})

    def _logout(self) -> None:
        if self.authenticated:
            self._request("auth.logout")
        self.auto_login = False
        self.authenticated = False
        self.credentials = None
        self.current_user = {}
        self.current_scope = None
        self.password_var.set("")
        self._show_login()

    def _request_presence(self) -> None:
        self._request("presence.list")

    def _request_groups(self) -> None:
        self._request("group.list")

    def _select_user(self, _event: Any = None) -> None:
        selection = self.user_list.curselection()
        if not selection:
            return
        index = selection[0]
        if index >= len(self.user_row_ids):
            return
        user_id = self.user_row_ids[index]
        item = self.users[index]
        self.current_scope = ("private", user_id)
        self.conversation_var.set(f"私聊：{_item_name(item, 'username', 'name')}")
        self.members = []
        self._render_members()
        self._render_messages()
        self._sync_history()

    def _select_group(self, _event: Any = None) -> None:
        selection = self.group_list.curselection()
        if not selection:
            return
        index = selection[0]
        if index >= len(self.group_row_ids):
            return
        group_id = self.group_row_ids[index]
        item = self.groups[index]
        self.current_scope = ("group", group_id)
        self.conversation_var.set(f"群聊：{_item_name(item, 'name', 'group_name')}")
        self._render_messages()
        self._sync_history()
        self._request("group.members", {"group_id": group_id})

    def _scope_payload(self) -> dict[str, Any] | None:
        if not self.current_scope:
            messagebox.showinfo("会话", "请先选择用户或群组")
            return None
        kind, identifier = self.current_scope
        return {
            "scope": {"type": kind, "id": identifier},
            "conversation_type": kind,
            "conversation_id": identifier,
        }

    def _sync_history(self) -> None:
        payload = self._scope_payload()
        if payload is not None:
            payload.update({"limit": 100})
            self._request("history.sync", payload)

    def _send_text(self) -> None:
        text = self.compose.get("1.0", "end-1c").strip()
        if not text:
            return
        payload = self._scope_payload()
        if payload is None:
            return
        if text.lower().startswith("@agent"):
            prompt = text[len("@agent") :].strip() or "请总结最近 50 条消息"
            payload.update({"message_type": "text", "content": text})
            if self._request("message.send", payload):
                self._invoke_agent(prompt)
                self.compose.delete("1.0", "end")
        else:
            payload.update({"message_type": "text", "content": text})
            if self._request("message.send", payload):
                self.compose.delete("1.0", "end")

    def _send_image(self) -> None:
        payload = self._scope_payload()
        if payload is None:
            return
        path = filedialog.askopenfilename(
            title="选择图片",
            filetypes=(
                ("图片", "*.png *.jpg *.jpeg *.gif *.webp"),
                ("所有文件", "*.*"),
            ),
        )
        if not path:
            return
        try:
            size = os.path.getsize(path)
            if size > MAX_IMAGE_SIZE:
                messagebox.showerror("图片过大", "图片不能超过 2 MiB")
                return
            with open(path, "rb") as image_file:
                raw = image_file.read(MAX_IMAGE_SIZE + 1)
        except OSError as exc:
            messagebox.showerror("读取失败", str(exc))
            return
        if len(raw) > MAX_IMAGE_SIZE:
            messagebox.showerror("图片过大", "图片不能超过 2 MiB")
            return
        mime_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if not mime_type.startswith("image/"):
            messagebox.showerror("格式错误", "请选择有效的图片文件")
            return
        payload.update(
            {
                "message_type": "image",
                "image_name": os.path.basename(path),
                "image_mime": mime_type,
                "size": len(raw),
                "image_data": base64.b64encode(raw).decode("ascii"),
            }
        )
        self._request("message.send", payload)

    def _invoke_agent(self, prompt: str) -> None:
        payload = self._scope_payload()
        if payload is None:
            return
        payload.update({"prompt": prompt, "task_type": "query", "limit": 200})
        self._request("agent.invoke", payload)

    def _save_latest_image(self) -> None:
        if not self.current_scope:
            messagebox.showinfo("保存图片", "请先选择会话")
            return
        images = [
            item
            for item in self.messages.get(self.current_scope, [])
            if item.get("message_type") == "image" and item.get("image_data") and not item.get("recalled")
        ]
        if not images:
            messagebox.showinfo("保存图片", "当前会话没有可保存的图片")
            return
        image = images[-1]
        destination = filedialog.asksaveasfilename(
            title="保存最近图片",
            initialfile=str(image.get("image_name") or "chat-image"),
        )
        if not destination:
            return
        try:
            raw = base64.b64decode(str(image["image_data"]), validate=True)
            with open(destination, "wb") as output:
                output.write(raw)
        except (OSError, ValueError) as exc:
            messagebox.showerror("保存失败", str(exc))
            return
        self._status("图片已保存")

    def _search_history(self) -> None:
        keyword = self.search_var.get().strip()
        if not keyword:
            messagebox.showinfo("搜索", "请输入关键词")
            return
        payload = self._scope_payload()
        if payload is None:
            return
        payload.update({"keyword": keyword, "limit": 50})
        self._request("history.search", payload)

    def _create_group(self) -> None:
        name = simpledialog.askstring("创建群组", "群名称：", parent=self.root)
        if name and name.strip():
            self._request("group.create", {"name": name.strip()})

    def _join_group(self) -> None:
        selection = self.group_list.curselection()
        initial = self.group_row_ids[selection[0]] if selection else ""
        group_id = simpledialog.askstring(
            "加入群组", "群组 ID：", initialvalue=initial, parent=self.root
        )
        if group_id and group_id.strip():
            self._request("group.join", {"group_id": group_id.strip()})

    def _moderate(self, action: str) -> None:
        if not self.current_scope or self.current_scope[0] != "group":
            messagebox.showinfo("群管理", "请先选择群聊")
            return
        selected = self.member_tree.selection()
        if not selected:
            messagebox.showinfo("群管理", "请选择成员")
            return
        member_id = selected[0]
        self._request(
            f"moderation.{action}",
            {"group_id": self.current_scope[1], "user_id": member_id},
        )

    def _recall_message(self) -> None:
        if not self.current_scope:
            return
        records = self.messages.get(self.current_scope, [])
        my_id = str(self.current_user.get("id", self.current_user.get("user_id", "")))
        candidates = [
            item
            for item in records
            if item.get("id", item.get("message_id"))
            and not item.get("recalled")
            and (
                str(item.get("sender_id", "")) == my_id
                or item.get("sender") == self.current_user.get("username")
            )
        ]
        if not candidates:
            candidates = [
                item
                for item in records
                if item.get("id", item.get("message_id")) and not item.get("recalled")
            ]
        if not candidates:
            messagebox.showinfo("撤回", "当前会话没有可撤回的消息")
            return
        message = candidates[-1]
        message_id = message.get("id", message.get("message_id"))
        self._request("message.recall", {"message_id": message_id})

    def _set_schedule(self, enabled: bool) -> None:
        if not self.current_scope or self.current_scope[0] != "group":
            messagebox.showinfo("定时总结", "请先选择群聊")
            return
        try:
            interval = int(self.schedule_var.get())
        except ValueError:
            messagebox.showwarning("定时总结", "周期必须是整数")
            return
        if not 1 <= interval <= 1440:
            messagebox.showwarning("定时总结", "周期范围为 1–1440 分钟")
            return
        self._request(
            "agent.schedule.set",
            {
                "group_id": self.current_scope[1],
                "interval_minutes": interval,
                "enabled": enabled,
            },
        )

    def _poll_events(self) -> None:
        try:
            while True:
                event = self.network.events.get_nowait()
                self._handle_event(event)
        except queue.Empty:
            pass
        if self.root.winfo_exists():
            self.root.after(POLL_INTERVAL_MS, self._poll_events)

    def _handle_event(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type", ""))
        payload = event.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        handlers = {
            "network.connected": self._on_connected,
            "network.disconnected": self._on_disconnected,
            "network.reconnecting": self._on_reconnecting,
            "network.error": self._on_network_error,
            "network.send_failed": self._on_send_failed,
            "auth.result": self._on_auth_result,
            "presence.list": self._on_presence_list,
            "presence.changed": self._on_presence_changed,
            "group.list": self._on_group_list,
            "group.created": self._on_group_changed,
            "group.joined": self._on_group_changed,
            "group.members": self._on_group_members,
            "message.event": self._on_message,
            "message.recall": self._on_recall,
            "message.recalled": self._on_recall,
            "history.result": self._on_history,
            "agent.result": self._on_agent_result,
            "agent.status": self._on_agent_status,
            "tool.call.event": self._on_tool_call,
            "tool.calls.list": self._on_tool_calls_list,
            "moderation.mute": self._on_moderation,
            "moderation.unmute": self._on_moderation,
            "moderation.changed": self._on_moderation,
            "agent.schedule.updated": self._on_schedule_updated,
            "ack": self._on_ack,
            "error": self._on_server_error,
            "heartbeat.ping": self._on_ping,
            "heartbeat.pong": lambda _payload, _event: None,
        }
        handler = handlers.get(event_type)
        if handler:
            handler(payload, event)

    def _on_connected(self, _payload: dict[str, Any], _event: dict[str, Any]) -> None:
        self.connected = True
        self.connection_var.set(f"已连接 {self.host}:{self.port}")
        self.login_status_var.set(f"已连接 {self.host}:{self.port}")
        if self.auto_login and self.credentials:
            username, password = self.credentials
            self._request("auth.login", {"username": username, "password": password})

    def _on_disconnected(self, _payload: dict[str, Any], _event: dict[str, Any]) -> None:
        self.connected = False
        self.authenticated = False
        self.connection_var.set("连接已断开")
        self.login_status_var.set("连接已断开，正在自动重连…")

    def _on_reconnecting(self, payload: dict[str, Any], _event: dict[str, Any]) -> None:
        delay = payload.get("delay", "?")
        self.connection_var.set(f"{delay} 秒后重连")
        self.login_status_var.set(f"{delay} 秒后重连…")

    def _on_network_error(self, payload: dict[str, Any], _event: dict[str, Any]) -> None:
        self.login_status_var.set(f"连接错误：{payload.get('message', '未知错误')}")

    def _on_send_failed(self, payload: dict[str, Any], _event: dict[str, Any]) -> None:
        self._status(f"发送失败：{payload.get('type', '请求')}")

    def _on_auth_result(self, payload: dict[str, Any], event: dict[str, Any]) -> None:
        success = bool(payload.get("success", payload.get("ok", False)))
        self.pending.pop(str(event.get("request_id", "")), None)
        if not success:
            self.login_status_var.set(str(payload.get("message", "认证失败")))
            if self.authenticated:
                self._show_login()
            return
        user = payload.get("user")
        self.current_user = user if isinstance(user, dict) else {
            "username": self.credentials[0] if self.credentials else ""
        }
        self.authenticated = True
        self.auto_login = True
        self.password_var.set("")
        self._show_main()
        bootstrap_users = _as_list(payload, "users", "online_users")
        bootstrap_groups = _as_list(payload, "groups")
        if bootstrap_users:
            self.users = bootstrap_users
            self._render_users()
        else:
            self._request_presence()
        if bootstrap_groups:
            self.groups = bootstrap_groups
            self._render_groups()
        else:
            self._request_groups()
        for message in _as_list(payload, "offline_messages"):
            if isinstance(message, dict):
                self._merge_messages(self._extract_scope(message), [message])
        if payload.get("agent_online") is not None:
            self.agent_var.set("Agent 在线" if payload["agent_online"] else "Agent 离线")
        self._request("agent.status")
        self._request("tool.calls.list", {"limit": 100})

    def _on_presence_list(self, payload: dict[str, Any], _event: dict[str, Any]) -> None:
        self.users = _as_list(payload, "users", "items", "online_users")
        self._render_users()

    def _on_presence_changed(self, _payload: dict[str, Any], _event: dict[str, Any]) -> None:
        self._request_presence()

    def _on_group_list(self, payload: dict[str, Any], _event: dict[str, Any]) -> None:
        self.groups = _as_list(payload, "groups", "items")
        self._render_groups()

    def _on_group_changed(self, payload: dict[str, Any], _event: dict[str, Any]) -> None:
        self._status(str(payload.get("message", "群组信息已更新")))
        self._request_groups()

    def _on_group_members(self, payload: dict[str, Any], _event: dict[str, Any]) -> None:
        self.members = _as_list(payload, "members", "users", "items")
        self._render_members()

    def _on_message(self, payload: dict[str, Any], _event: dict[str, Any]) -> None:
        message = payload.get("message") if isinstance(payload.get("message"), dict) else payload
        scope = self._extract_scope(message)
        self._merge_messages(scope, [message])
        if scope == self.current_scope:
            self._render_messages()
            message_id = message.get("id", message.get("message_id"))
            if message_id is not None:
                self._mark_read(scope, int(message_id))

    def _on_recall(self, payload: dict[str, Any], _event: dict[str, Any]) -> None:
        recalled = payload.get("message") if isinstance(payload.get("message"), dict) else payload
        message_id = str(recalled.get("message_id", recalled.get("id", "")))
        for records in self.messages.values():
            for message in records:
                if str(message.get("id", message.get("message_id", ""))) == message_id:
                    message["recalled"] = True
                    message["content"] = ""
        self._render_messages()

    def _on_history(self, payload: dict[str, Any], event: dict[str, Any]) -> None:
        records = _as_list(payload, "messages", "items", "results")
        request_type = self.pending.pop(str(event.get("request_id", "")), "")
        scope = self._extract_scope(payload) or self.current_scope
        if not scope:
            return
        self._merge_messages(scope, [item for item in records if isinstance(item, dict)])
        if scope == self.current_scope:
            self._render_messages()
            ids = [int(item.get("id", item.get("message_id", 0))) for item in records if item.get("id", item.get("message_id")) is not None]
            if ids:
                self._mark_read(scope, max(ids))
                if request_type == "history.sync" and len(records) == 100:
                    self._request(
                        "history.sync",
                        {
                            "conversation_type": scope[0],
                            "conversation_id": scope[1],
                            "after_id": max(ids),
                            "limit": 100,
                        },
                    )
        if payload.get("keyword") is not None or payload.get("search"):
            self._status(f"搜索到 {len(records)} 条记录")

    def _on_agent_result(self, payload: dict[str, Any], _event: dict[str, Any]) -> None:
        if payload.get("online") is not None:
            self.agent_var.set("Agent 在线" if payload["online"] else "Agent 离线")
        message = payload.get("message")
        if isinstance(message, dict):
            self._on_message({"message": message}, {})
        elif payload.get("content") or payload.get("result"):
            content = str(payload.get("content", payload.get("result", "")))
            record = {
                "id": payload.get("message_id", f"agent-{time.time_ns()}"),
                "sender": "Agent",
                "sender_name": "Agent",
                "content": content,
                "message_type": "agent",
                "timestamp": payload.get("timestamp", int(time.time() * 1000)),
            }
            scope = self._extract_scope(payload) or self.current_scope
            if scope:
                self._merge_messages(scope, [record])
                if scope == self.current_scope:
                    self._render_messages()
        if payload.get("success") is False:
            self._status(str(payload.get("error", payload.get("message", "Agent 调用失败"))))
        elif payload.get("status") == "failed":
            self._status(str(payload.get("error", "Agent 调用失败")))
            messagebox.showerror("Agent 调用失败", str(payload.get("error", "未知错误")))

    def _on_agent_status(self, payload: dict[str, Any], _event: dict[str, Any]) -> None:
        online = bool(payload.get("online", payload.get("available", False)))
        model = payload.get("model")
        suffix = f" · {model}" if model else ""
        self.agent_var.set(("Agent 在线" if online else "Agent 离线") + suffix)

    def _on_tool_call(self, payload: dict[str, Any], _event: dict[str, Any]) -> None:
        tool_call = payload.get("tool_call")
        if isinstance(tool_call, dict):
            payload = tool_call
        timestamp = payload.get("timestamp", payload.get("started_at", time.time()))
        try:
            numeric = float(timestamp)
            if numeric > 10_000_000_000:
                numeric /= 1000
            clock = time.strftime("%H:%M:%S", time.localtime(numeric))
        except (TypeError, ValueError, OverflowError):
            clock = str(timestamp)
        elapsed = payload.get("elapsed_ms", payload.get("duration_ms", ""))
        if elapsed == "" and payload.get("started_at") and payload.get("finished_at"):
            try:
                elapsed = round(
                    (float(payload["finished_at"]) - float(payload["started_at"])) * 1000
                )
            except (TypeError, ValueError):
                elapsed = ""
        elapsed_text = f"{elapsed} ms" if elapsed != "" else ""
        values = (
                clock,
                payload.get("tool", payload.get("tool_name", "")),
                payload.get("requested_by_name", payload.get("requested_by", "")),
                f"{payload.get('conversation_type', '')}:{payload.get('conversation_id', '')}".strip(":"),
                payload.get("status", ""),
                payload.get("count", payload.get("result_count", "")),
                elapsed_text,
            )
        record_id = str(payload.get("id", ""))
        iid = self.tool_rows.get(record_id) if record_id else None
        if iid and self.tool_tree.exists(iid):
            self.tool_tree.item(iid, values=values)
        else:
            iid = self.tool_tree.insert("", 0, values=values)
            if record_id:
                self.tool_rows[record_id] = iid

    def _on_tool_calls_list(self, payload: dict[str, Any], event: dict[str, Any]) -> None:
        for row in self.tool_tree.get_children():
            self.tool_tree.delete(row)
        self.tool_rows.clear()
        records = _as_list(payload, "tool_calls", "items")
        for record in reversed(records):
            if isinstance(record, dict):
                self._on_tool_call(record, event)

    def _on_moderation(self, payload: dict[str, Any], _event: dict[str, Any]) -> None:
        self._status(str(payload.get("message", "群成员状态已更新")))
        if self.current_scope and self.current_scope[0] == "group":
            self._request("group.members", {"group_id": self.current_scope[1]})

    def _on_schedule_updated(self, payload: dict[str, Any], _event: dict[str, Any]) -> None:
        schedule = payload.get("schedule")
        if isinstance(schedule, dict):
            state = "已启用" if schedule.get("enabled") else "已停用"
            self._status(f"定时总结{state}")
        else:
            self._status("定时总结设置已更新")

    def _on_ack(self, payload: dict[str, Any], event: dict[str, Any]) -> None:
        self.pending.pop(str(event.get("request_id", "")), None)
        if payload.get("message"):
            self._status(str(payload["message"]))

    def _on_server_error(self, payload: dict[str, Any], event: dict[str, Any]) -> None:
        self.pending.pop(str(event.get("request_id", "")), None)
        message = str(payload.get("message", payload.get("error", "服务端错误")))
        self._status(message)
        messagebox.showerror("服务端错误", message)

    def _on_ping(self, _payload: dict[str, Any], _event: dict[str, Any]) -> None:
        self._request("heartbeat.pong")

    def _extract_scope(self, value: dict[str, Any]) -> tuple[str, str] | None:
        scope = value.get("scope")
        if isinstance(scope, dict):
            kind = scope.get("type", scope.get("conversation_type"))
            identifier = scope.get("id", scope.get("conversation_id"))
            if kind is not None and identifier is not None:
                return str(kind), self._display_scope_id(str(kind), str(identifier))
        kind = value.get("conversation_type", value.get("scope_type"))
        identifier = value.get("conversation_id", value.get("scope_id"))
        if kind is not None and identifier is not None:
            return str(kind), self._display_scope_id(str(kind), str(identifier))
        if value.get("group_id") is not None:
            return "group", str(value["group_id"])
        sender_id = value.get("sender_id")
        recipient_id = value.get("recipient_id")
        my_id = self.current_user.get("id", self.current_user.get("user_id"))
        if sender_id is not None and recipient_id is not None:
            peer = recipient_id if str(sender_id) == str(my_id) else sender_id
            return "private", str(peer)
        return None

    def _display_scope_id(self, kind: str, identifier: str) -> str:
        """Map a canonical private conversation ID back to the peer user ID."""
        if kind != "private" or not identifier.startswith("dm:"):
            return identifier
        parts = identifier.split(":")
        if len(parts) != 3:
            return identifier
        my_id = str(self.current_user.get("id", self.current_user.get("user_id", "")))
        if my_id == parts[1]:
            return parts[2]
        if my_id == parts[2]:
            return parts[1]
        return identifier

    def _mark_read(self, scope: tuple[str, str], message_id: int) -> None:
        kind, identifier = scope
        self._request(
            "history.read",
            {"conversation_type": kind, "conversation_id": identifier, "message_id": message_id},
        )

    def _merge_messages(
        self, scope: tuple[str, str] | None, new_messages: list[dict[str, Any]]
    ) -> None:
        if not scope:
            return
        records = self.messages[scope]
        positions = {
            str(item.get("id", item.get("message_id", ""))): index
            for index, item in enumerate(records)
            if item.get("id", item.get("message_id")) is not None
        }
        for message in new_messages:
            message_id = str(message.get("id", message.get("message_id", "")))
            if message_id and message_id in positions:
                records[positions[message_id]].update(message)
            else:
                records.append(dict(message))
        records.sort(key=lambda item: (item.get("timestamp", item.get("created_at", 0)), str(item.get("id", ""))))

    def _render_users(self) -> None:
        self.user_list.delete(0, "end")
        self.user_row_ids = []
        my_id = str(self.current_user.get("id", self.current_user.get("user_id", "")))
        filtered: list[Any] = []
        for item in self.users:
            identifier = _item_id(item, "id", "user_id", "username")
            if my_id and identifier == my_id:
                continue
            filtered.append(item)
            self.user_row_ids.append(identifier)
            online = not isinstance(item, dict) or item.get("online", True)
            marker = "●" if online else "○"
            self.user_list.insert("end", f"{marker} {_item_name(item, 'username', 'name')}")
        self.users = filtered

    def _render_groups(self) -> None:
        self.group_list.delete(0, "end")
        self.group_row_ids = []
        for item in self.groups:
            identifier = _item_id(item, "id", "group_id", "name")
            self.group_row_ids.append(identifier)
            joined = not isinstance(item, dict) or item.get("joined", item.get("is_member", True))
            marker = "#" if joined else "+"
            self.group_list.insert("end", f"{marker} {_item_name(item, 'name', 'group_name')}")

    def _render_members(self) -> None:
        for row in self.member_tree.get_children():
            self.member_tree.delete(row)
        self.member_rows.clear()
        for item in self.members:
            identifier = _item_id(item, "id", "user_id", "username")
            name = _item_name(item, "username", "name")
            muted = isinstance(item, dict) and bool(item.get("muted", item.get("is_muted", False)))
            role = item.get("group_role", item.get("role", "")) if isinstance(item, dict) else ""
            status = "禁言" if muted else str(role or "正常")
            self.member_tree.insert("", "end", iid=identifier, values=(name, status))
            self.member_rows[identifier] = item

    def _render_messages(self) -> None:
        self.message_text.configure(state="normal")
        self.message_text.delete("1.0", "end")
        if self.current_scope:
            for message in self.messages.get(self.current_scope, []):
                timestamp = message.get("timestamp", message.get("created_at", ""))
                clock = self._format_time(timestamp)
                sender = message.get(
                    "sender_name", message.get("sender", message.get("username", "未知用户"))
                )
                if isinstance(sender, dict):
                    sender = sender.get("username", sender.get("name", "未知用户"))
                if message.get("recalled", message.get("is_recalled", False)):
                    self.message_text.insert(
                        "end", f"[{clock}] {sender}：消息已撤回\n", "recalled"
                    )
                    continue
                message_type = message.get("message_type", message.get("type", "text"))
                if message_type == "image":
                    content = f"[图片] {message.get('image_name', '')}".strip()
                else:
                    content = str(message.get("content", message.get("text", "")))
                tag = "agent" if message_type == "agent" or str(sender).lower() == "agent" else None
                self.message_text.insert("end", f"[{clock}] {sender}：{content}\n", tag)
        self.message_text.configure(state="disabled")
        self.message_text.see("end")

    @staticmethod
    def _format_time(value: Any) -> str:
        try:
            numeric = float(value)
            if numeric > 10_000_000_000:
                numeric /= 1000
            return time.strftime("%H:%M:%S", time.localtime(numeric))
        except (TypeError, ValueError, OverflowError):
            return str(value or "--:--:--")

    def _status(self, message: str) -> None:
        self.connection_var.set(message)
        self.root.after(
            3500,
            lambda: self.connection_var.set(
                f"已连接 {self.host}:{self.port}" if self.connected else "连接已断开"
            ),
        )

    def _on_close(self) -> None:
        self.network.close()
        self.root.destroy()
