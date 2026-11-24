from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QEvent, QSettings, QSize, Qt
    from PySide6.QtWidgets import (
        QAbstractButton,
        QApplication,
        QCheckBox,
        QFrame,
        QLabel,
        QPushButton,
        QWidget,
    )
except ImportError:  # pragma: no cover - optional until V2 dependencies are installed
    QApplication = None  # type: ignore[assignment]

from client.models import ConversationKey


def _summary(**overrides):
    card = {
        "kind": "summary",
        "title": "群聊总结",
        "overview": "团队完成 TCP 联调，灰度窗口仍待确认。",
        "conclusions": [
            {"text": "TCP 联调已完成", "sources": [12]},
            {"text": "服务端使用 asyncio", "sources": [14]},
            {"text": "灰度先覆盖 10% 用户", "sources": [18]},
            {"text": "周四前补齐监控面板", "sources": [20]},
        ],
        "pending": [
            {"text": "灰度窗口定在周四还是周五尚未决定", "sources": [22]},
            {"text": "回滚责任人尚未确认", "sources": [23]},
        ],
        "sources": [12, 23],
        "range": {"start_id": 12, "end_id": 23},
        "generated_at": 0,
        "parse_error": False,
    }
    card.update(overrides)
    return card


def _todo(**overrides):
    card = {
        "kind": "todo",
        "title": "待办事项",
        "current_owner": "Alice",
        "tasks": [
            {
                "title": "补充演示录屏",
                "description": "覆盖登录、群聊和 Agent 卡片。",
                "owner": "Alice",
                "due": "周四",
                "group": "confirmed",
                "priority": "high",
                "priority_basis": "inferred",
                "status": "pending",
                "source_message_id": 55,
            },
            {
                "title": "更新部署文档",
                "owner": "Bob",
                "due": "",
                "group": "confirmed",
                "priority": "normal",
                "source_message_id": 56,
            },
            {
                "title": "整理监控告警阈值",
                "owner": "",
                "due": "",
                "group": "suggested",
                "priority": "low",
                "source_message_id": 57,
            },
            {
                "title": "确认灰度发布窗口",
                "owner": "Alice",
                "due": "周五前",
                "group": "confirmed",
                "priority": "high",
                "priority_basis": "explicit",
                "source_message_id": 58,
            },
        ],
        "unresolved": ["回滚负责人尚未确认"],
        "sources": [12, 22],
        "range": {"start_id": 12, "end_id": 23},
        "generated_at": 0,
        "parse_error": False,
    }
    card.update(overrides)
    return card


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class AgentCardUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        from client.v2.theme import ThemeMode, install_theme_manager

        cls.settings_dir = tempfile.TemporaryDirectory()
        cls.manager = install_theme_manager(
            cls.app,
            QSettings(
                str(Path(cls.settings_dir.name) / "chatroom-card-ui-tests.ini"),
                QSettings.Format.IniFormat,
            ),
        )
        cls.manager.set_mode(ThemeMode.LIGHT)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.settings_dir.cleanup()

    def tearDown(self) -> None:
        self.app.processEvents()
        for widget in self.app.topLevelWidgets():
            widget.close()
            widget.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def test_summary_card_collapses_to_three_points_and_expands(self) -> None:
        from client.v2.agent_cards import SummaryCard

        card = SummaryCard(_summary())
        card.show()
        self.app.processEvents()
        self.assertEqual(card.objectName(), "AgentCard")
        self.assertLessEqual(card.maximumWidth(), 640)
        self.assertLessEqual(card.minimumWidth(), 400)
        self.assertEqual(len(card.findChildren(QLabel, "SummaryPoint")), 3)
        self.assertEqual(len(card.findChildren(QLabel, "CardPendingPoint")), 0)
        self.assertTrue(card._expand_button.isVisible())
        collapsed_height = card.height()
        card.set_expanded(True)
        self.app.processEvents()
        self.assertEqual(len(card.findChildren(QLabel, "SummaryPoint")), 4)
        self.assertEqual(len(card.findChildren(QLabel, "CardPendingPoint")), 2)
        self.assertGreater(card.height(), collapsed_height)
        self.assertEqual(card._expand_button.text(), "收起")
        card.set_expanded(False)
        self.app.processEvents()
        self.assertEqual(len(card.findChildren(QLabel, "SummaryPoint")), 3)

    def test_summary_and_todo_source_actions_emit_valid_ids(self) -> None:
        from client.v2.agent_cards import SummaryCard, TodoCard

        summary = SummaryCard(_summary())
        todo = TodoCard(_todo())
        summary_ids: list[list[int]] = []
        todo_ids: list[list[int]] = []
        summary.sources_requested.connect(summary_ids.append)
        todo.sources_requested.connect(todo_ids.append)
        summary._source_button.click()
        todo._source_button.click()
        self.assertEqual(summary_ids, [[12, 23]])
        self.assertEqual(todo_ids, [[12, 22]])

    def test_todo_selection_uses_custom_buttons_and_owner_permissions(self) -> None:
        from client.v2.agent_cards import TodoCard

        card = TodoCard(_todo())
        card.show()
        self.app.processEvents()
        self.assertEqual(len(card.findChildren(QCheckBox)), 0)
        selectors = {
            button.property("taskIndex"): button
            for button in card.findChildren(QAbstractButton, "CardTaskCheck")
        }
        self.assertEqual(set(selectors), {0, 1, 2})
        self.assertTrue(selectors[0].isChecked())
        self.assertTrue(selectors[0].isEnabled())
        self.assertFalse(selectors[1].isChecked())
        self.assertFalse(selectors[1].isEnabled())
        self.assertFalse(selectors[2].isChecked())
        self.assertTrue(selectors[2].isEnabled())
        self.assertEqual(card._import_button.text(), "加入个人待办 (2)")
        selectors[2].click()
        self.assertEqual(card.selected_task_indices, [0, 2, 3])
        self.assertEqual(card._import_button.text(), "加入个人待办 (3)")

        titles = [label.text() for label in card.findChildren(QLabel, "CardTaskTitle")]
        self.assertEqual(titles, ["补充演示录屏", "更新部署文档", "整理监控告警阈值"])
        meta = " ".join(label.text() for label in card.findChildren(QLabel, "CardTaskMeta"))
        self.assertIn("负责人 Alice", meta)
        self.assertIn("截止 周四", meta)
        self.assertNotIn("未明确", meta)
        card.set_expanded(True)
        self.app.processEvents()
        titles = [label.text() for label in card.findChildren(QLabel, "CardTaskTitle")]
        self.assertEqual(len(titles), 4)
        self.assertIn("确认灰度发布窗口", titles)
        self.assertEqual(len(card.findChildren(QLabel, "CardPendingPoint")), 1)
        selectors = {
            button.property("taskIndex"): button
            for button in card.findChildren(QAbstractButton, "CardTaskCheck")
        }
        self.assertTrue(selectors[3].isChecked())
        self.assertTrue(selectors[3].isEnabled())

        compact = TodoCard(
            _todo(
                tasks=[
                    {"title": "补充演示录屏", "owner": "Alice", "due": "", "group": "confirmed"},
                    {"title": "更新部署文档", "group": "confirmed"},
                ],
                unresolved=[],
            )
        )
        compact.show()
        self.app.processEvents()
        self.assertTrue(compact.footer.isVisible())
        self.assertTrue(compact._copy_button.isVisible())
        self.assertFalse(compact._expand_button.isVisible())

    def test_completed_tasks_form_collapsed_group_and_counts_active_only(self) -> None:
        from client.v2.agent_cards import TodoCard

        card = TodoCard(
            _todo(
                tasks=[
                    {
                        "title": "已经完成",
                        "owner": "Alice",
                        "status": "completed",
                        "source_message_id": 55,
                    },
                    {
                        "title": "仍需处理",
                        "owner": "Alice",
                        "status": "open",
                        "source_message_id": 56,
                    },
                ]
            )
        )
        card.show()
        self.app.processEvents()
        titles = [label.text() for label in card.findChildren(QLabel, "CardTaskTitle")]
        self.assertEqual(titles, ["仍需处理"])
        self.assertTrue(card._meta_label.text().startswith("1 项待办 · 1 项已完成"))
        selectors = card.findChildren(QAbstractButton, "CardTaskCheck")
        self.assertEqual(
            [selector.property("taskIndex") for selector in selectors],
            [1],
        )
        toggle = next(
            button
            for button in card.findChildren(QPushButton)
            if button.property("completedToggle")
        )
        self.assertEqual(toggle.text(), "已完成 (1)")
        self.assertEqual(toggle.accessibleName(), "展开已完成任务，共 1 项")
        self.assertEqual(toggle.iconSize(), QSize(16, 16))
        self.assertFalse(toggle.icon().isNull())
        self.assertEqual(toggle.focusPolicy(), Qt.FocusPolicy.StrongFocus)
        self.assertFalse(toggle.property("completedExpanded"))
        self.assertNotIn(0, card.selected_task_indices)

        toggle.click()
        self.app.processEvents()
        titles = [label.text() for label in card.findChildren(QLabel, "CardTaskTitle")]
        self.assertEqual(titles, ["仍需处理", "已经完成"])
        completed_selector = next(
            selector
            for selector in card.findChildren(QAbstractButton, "CardTaskCheck")
            if selector.property("taskIndex") == 0
        )
        self.assertTrue(completed_selector.isChecked())
        self.assertFalse(completed_selector.isEnabled())
        self.assertEqual(
            [label.text() for label in card.findChildren(QLabel, "CardTaskStatus")],
            ["待处理", "已完成"],
        )
        self.assertNotIn(0, card.selected_task_indices)
        toggle = next(
            button
            for button in card.findChildren(QPushButton)
            if button.property("completedToggle")
        )
        self.assertEqual(toggle.accessibleName(), "收起已完成任务，共 1 项")
        self.assertTrue(toggle.property("completedExpanded"))
        toggle.click()
        self.app.processEvents()
        self.assertEqual(
            [label.text() for label in card.findChildren(QLabel, "CardTaskTitle")],
            ["仍需处理"],
        )

    def test_personal_todo_completion_overrides_status_and_restores_active(self) -> None:
        from client.v2.agent_cards import TodoCard

        card = TodoCard(
            _todo(
                tasks=[
                    {
                        "title": "写发布说明",
                        "owner": "Alice",
                        "status": "open",
                        "source_message_id": 55,
                    },
                    {
                        "title": "跟进回滚演练",
                        "owner": "Alice",
                        "status": "completed",
                        "source_message_id": 56,
                    },
                ],
                unresolved=[],
            )
        )
        card.show()
        self.app.processEvents()
        self.assertTrue(card._meta_label.text().startswith("1 项待办 · 1 项已完成"))

        card.set_runtime_context(
            70,
            [
                {
                    "id": 601,
                    "title": "写发布说明",
                    "source_message_id": 55,
                    "completed": True,
                },
                {
                    "id": 602,
                    "title": "跟进回滚演练",
                    "source_message_id": 56,
                    "completed": False,
                },
            ],
        )
        self.app.processEvents()
        self.assertTrue(card._meta_label.text().startswith("1 项待办 · 1 项已完成"))
        self.assertEqual(
            [label.text() for label in card.findChildren(QLabel, "CardTaskTitle")],
            ["跟进回滚演练"],
        )
        toggle = next(
            button
            for button in card.findChildren(QPushButton)
            if button.property("completedToggle")
        )
        toggle.click()
        self.app.processEvents()
        self.assertEqual(
            [label.text() for label in card.findChildren(QLabel, "CardTaskTitle")],
            ["跟进回滚演练", "写发布说明"],
        )
        self.assertEqual(
            [label.text() for label in card.findChildren(QLabel, "CardTaskStatus")],
            ["待处理", "已完成"],
        )
        self.assertNotIn(0, card.selected_task_indices)

        card.set_runtime_context(
            70,
            [
                {
                    "id": 601,
                    "title": "写发布说明",
                    "source_message_id": 55,
                    "completed": False,
                },
                {
                    "id": 602,
                    "title": "跟进回滚演练",
                    "source_message_id": 56,
                    "completed": False,
                },
            ],
        )
        self.app.processEvents()
        self.assertTrue(card._meta_label.text().startswith("2 项待办"))
        self.assertEqual(
            [label.text() for label in card.findChildren(QLabel, "CardTaskTitle")],
            ["写发布说明", "跟进回滚演练"],
        )
        self.assertEqual(
            [label.text() for label in card.findChildren(QLabel, "CardTaskStatus")],
            ["待处理", "待处理"],
        )
        self.assertFalse(
            any(
                button.property("completedToggle")
                for button in card.findChildren(QPushButton)
            )
        )

    def test_grouping_and_dedupe_keep_source_indices_for_import_and_sync(self) -> None:
        from client.v2.agent_cards import TodoCard

        due_at = 1795651200
        card = TodoCard(
            _todo(
                message_id=41,
                tasks=[
                    {
                        "title": "参加七神会议",
                        "owner": "Alice",
                        "due": "2026-11-26 08:00",
                        "due_at": due_at,
                        "status": "open",
                        "source_message_id": 55,
                    },
                    {
                        "title": "七神会议",
                        "owner": "Alice",
                        "due": "2026-11-26 08:00",
                        "due_at": due_at,
                        "status": "open",
                        "source_message_id": 56,
                    },
                    {
                        "title": "补充演示录屏",
                        "owner": "Alice",
                        "due": "2026-11-27 09:00",
                        "due_at": due_at + 90000,
                        "status": "open",
                        "source_message_id": 57,
                    },
                    {
                        "title": "已经完成",
                        "owner": "Alice",
                        "status": "completed",
                        "source_message_id": 58,
                    },
                ],
                unresolved=[],
            )
        )
        requests: list[tuple[int, list[int]]] = []
        card.import_requested.connect(
            lambda message_id, indices: requests.append((message_id, indices))
        )
        self.assertEqual(card.selected_task_indices, [0, 2])
        card._import_button.click()
        self.assertEqual(requests, [(41, [0, 2])])

        card.set_runtime_context(
            83,
            [],
            {"status": "success", "task_indices": [0], "todo_ids": [501]},
        )
        card.show()
        self.app.processEvents()
        selectors = {
            selector.property("taskIndex"): selector
            for selector in card.findChildren(QAbstractButton, "CardTaskCheck")
        }
        self.assertEqual(set(selectors), {0, 2})
        self.assertTrue(selectors[0].isChecked())
        self.assertFalse(selectors[0].isEnabled())
        self.assertTrue(selectors[2].isChecked())
        self.assertTrue(selectors[2].isEnabled())
        self.assertEqual(card.selected_task_indices, [2])
        self.assertEqual(card._synced, {0: 501})

        card._import_button.click()
        self.assertEqual(requests, [(41, [0, 2]), (83, [2])])

    def test_same_day_similar_titles_merge_and_keep_explicit_due(self) -> None:
        from client.v2.agent_cards import TodoCard

        due_at = 1795651200
        card = TodoCard(
            _todo(
                tasks=[
                    {
                        "title": "准备发布总结",
                        "owner": "Alice",
                        "due": "2026-11-26",
                        "status": "open",
                        "sources": [1],
                        "todo_id": 701,
                    },
                    {
                        "title": "发布总结材料",
                        "owner": "Alice",
                        "due": "2026-11-26 08:00",
                        "due_at": due_at,
                        "status": "open",
                        "sources": [2],
                        "todo_id": 702,
                    },
                ],
                unresolved=[],
            )
        )
        card.show()
        self.app.processEvents()
        self.assertEqual(
            [label.text() for label in card.findChildren(QLabel, "CardTaskTitle")],
            ["准备发布总结"],
        )
        self.assertEqual(len(card._tasks), 1)
        self.assertEqual(card._tasks[0]["source_index"], 0)
        self.assertEqual(card._tasks[0]["due_at"], due_at)
        self.assertEqual(card._tasks[0]["due"], "2026-11-26 08:00")
        self.assertEqual(card._tasks[0]["sources"], [1, 2])
        self.assertEqual(card._tasks[0]["todo_ids"], [701, 702])
        self.assertTrue(card._meta_label.text().startswith("1 项待办"))

    def test_same_day_tasks_with_different_explicit_times_are_not_merged(self) -> None:
        from client.v2.agent_cards import TodoCard

        due_at = 1795651200
        card = TodoCard(
            _todo(
                tasks=[
                    {
                        "title": "准备发布总结",
                        "owner": "Alice",
                        "due": "2026-11-26 08:00",
                        "due_at": due_at,
                        "status": "open",
                    },
                    {
                        "title": "发布总结材料",
                        "owner": "Alice",
                        "due": "2026-11-26 09:00",
                        "due_at": due_at + 3600,
                        "status": "open",
                    },
                ],
                unresolved=[],
            )
        )
        card.show()
        self.app.processEvents()
        self.assertEqual(
            [label.text() for label in card.findChildren(QLabel, "CardTaskTitle")],
            ["准备发布总结", "发布总结材料"],
        )
        self.assertEqual(len(card._tasks), 2)

    def test_same_time_action_prefix_duplicates_are_rendered_once(self) -> None:
        from client.v2.agent_cards import TodoCard

        due_at = 1795651200
        card = TodoCard(
            _todo(
                tasks=[
                    {
                        "title": "参加七神会议",
                        "owner": "Alice",
                        "due": "2026-11-26 08:00",
                        "due_at": due_at,
                        "status": "open",
                        "source_message_id": 55,
                    },
                    {
                        "title": "七神会议",
                        "owner": "Alice",
                        "due": "2026-11-26 08:00",
                        "due_at": due_at,
                        "status": "open",
                        "source_message_id": 56,
                    },
                ],
                unresolved=[],
            )
        )
        card.show()
        self.app.processEvents()
        titles = [label.text() for label in card.findChildren(QLabel, "CardTaskTitle")]
        self.assertEqual(titles, ["参加七神会议"])
        selectors = card.findChildren(QAbstractButton, "CardTaskCheck")
        self.assertEqual(len(selectors), 1)
        self.assertEqual(selectors[0].property("taskIndex"), 0)

    def test_todo_batch_selection_emits_message_and_task_indices(self) -> None:
        from client.v2.agent_cards import TodoCard

        card = TodoCard(_todo(message_id=41))
        requests: list[tuple[int, list[int]]] = []
        card.import_requested.connect(lambda message_id, indices: requests.append((message_id, indices)))
        card.set_runtime_context(83, [])
        card.set_expanded(True)
        selectors = {
            button.property("taskIndex"): button
            for button in card.findChildren(QAbstractButton, "CardTaskCheck")
        }
        selectors[0].click()
        selectors[2].click()
        self.assertEqual(card.selected_task_indices, [2, 3])
        self.assertTrue(card._import_button.isEnabled())
        card._import_button.click()
        self.assertEqual(requests, [(83, [2, 3])])

        selectors[2].click()
        selectors[3].click()
        self.assertEqual(card.selected_task_indices, [])
        self.assertFalse(card._import_button.isEnabled())
        self.assertEqual(card._import_button.text(), "加入个人待办 (0)")

    def test_todo_sync_detection_uses_todo_id_source_and_normalized_title(self) -> None:
        from client.v2.agent_cards import TodoCard

        card = TodoCard(
            _todo(
                tasks=[
                    {
                        "title": "补充演示录屏",
                        "owner": "Alice",
                        "todo_id": 401,
                        "source_message_id": 55,
                    },
                    {
                        "title": "更新部署文档",
                        "owner": "Bob",
                        "source_message_id": 56,
                    },
                    {
                        "title": "整理监控告警阈值",
                        "owner": "",
                        "source_message_id": 57,
                    },
                ],
                unresolved=[],
            )
        )
        viewed: list[int] = []
        card.todo_requested.connect(viewed.append)
        card.set_runtime_context(
            70,
            [
                {"id": 401, "title": "另一条记录", "source_message_id": 99},
                {
                    "id": 402,
                    "title": "  更新部署文档  ",
                    "source_message_id": 56,
                },
            ],
        )
        card.set_expanded(True)
        self.app.processEvents()

        selectors = {
            button.property("taskIndex"): button
            for button in card.findChildren(QAbstractButton, "CardTaskCheck")
        }
        self.assertTrue(selectors[0].isChecked() and selectors[0].isEnabled() is False)
        self.assertTrue(selectors[1].isChecked() and selectors[1].isEnabled() is False)
        self.assertTrue(selectors[2].isEnabled())
        self.assertEqual(
            [label.text() for label in card.findChildren(QLabel, "CardTaskSyncState")],
            ["已同步", "已同步"],
        )
        actions = card.findChildren(QPushButton, "CardTaskTodoAction")
        self.assertEqual([button.text() for button in actions], ["查看待办", "查看待办"])
        actions[0].click()
        actions[1].click()
        self.assertEqual(viewed, [401, 402])
        self.assertTrue(
            any(
                label.text() in {"高优先级", "普通", "低优先级"}
                for label in card.findChildren(QLabel, "CardTaskPriority")
            )
        )

    def test_todo_import_pending_success_and_failure_states(self) -> None:
        from client.v2.agent_cards import TodoCard

        card = TodoCard(_todo())
        card.set_runtime_context(91, [], {"status": "pending"})
        self.assertEqual(card.import_state, "pending")
        self.assertEqual(card._import_button.text(), "正在导入…")
        self.assertFalse(card._import_button.isEnabled())
        selectors = card.findChildren(QAbstractButton, "CardTaskCheck")
        self.assertTrue(all(not button.isEnabled() for button in selectors))

        card.set_runtime_context(
            91,
            [],
            {"status": "success", "task_indices": [0], "todo_ids": [501]},
        )
        card.set_expanded(True)
        card.resize(520, 640)
        card.show()
        self.app.processEvents()
        self.assertEqual(card.import_state, "success")
        self.assertEqual(card.selected_task_indices, [3])
        selectors = {
            button.property("taskIndex"): button
            for button in card.findChildren(QAbstractButton, "CardTaskCheck")
        }
        self.assertFalse(selectors[0].isEnabled())
        self.assertTrue(selectors[0].isChecked())
        self.assertTrue(selectors[3].isEnabled() and selectors[3].isChecked())
        self.assertTrue(card._import_button.isEnabled())
        self.assertEqual(card._import_button.text(), "加入个人待办 (1)")
        feedback = card.findChild(QLabel, "CardImportFeedback")
        self.assertIsNotNone(feedback)
        self.assertIn("已加入个人待办", feedback.text())
        self.assertGreaterEqual(feedback.height(), feedback.heightForWidth(feedback.width()))
        self.assertIsNotNone(card.findChild(QFrame, "CardImportNotice"))

        card.set_runtime_context(
            91,
            [{"id": 501, "title": "补充演示录屏", "source_message_id": 55}],
            {"status": "failed", "error": "网络错误"},
        )
        self.assertEqual(card.import_state, "failure")
        self.assertTrue(card._import_button.isEnabled())
        feedback = card.findChild(QLabel, "CardImportFeedback")
        self.assertIsNotNone(feedback)
        self.assertIn("失败", feedback.text())
        self.assertIn("网络错误", feedback.text())

    def test_todo_priority_basis_description_status_and_plain_text(self) -> None:
        from client.v2.agent_cards import TodoCard
        from PySide6.QtCore import Qt

        card = TodoCard(
            _todo(
                tasks=[
                    {
                        "title": "<b>整理发布说明</b>",
                        "description": "<script>alert(1)</script>",
                        "owner": "Alice",
                        "due": "明天",
                        "priority": "high",
                        "priority_basis": "inferred",
                        "status": "in_progress",
                    },
                    {"title": "普通任务", "priority": "normal"},
                    {"title": "低优先级任务", "priority": "low"},
                ],
                unresolved=[],
            )
        )
        self.assertEqual(
            [label.text() for label in card.findChildren(QLabel, "CardTaskPriority")],
            ["高优先级", "普通", "低优先级"],
        )
        self.assertEqual(
            [label.text() for label in card.findChildren(QLabel, "CardTaskPriorityBasis")],
            ["建议"],
        )
        self.assertEqual(
            [label.text() for label in card.findChildren(QLabel, "CardTaskStatus")],
            ["进行中"],
        )
        self.assertTrue(
            all(
                label.pixmap() is not None and not label.pixmap().isNull()
                for label in card.findChildren(QLabel, "CardTaskPriorityFlag")
            )
        )
        descriptions = card.findChildren(QLabel, "CardTaskDescription")
        self.assertEqual(descriptions[0].text(), "<script>alert(1)</script>")
        for label in card.findChildren(QLabel):
            if label.text():
                self.assertEqual(label.textFormat(), Qt.TextFormat.PlainText)
        copy_text = card._copy_text()
        self.assertIn("描述：<script>alert(1)</script>", copy_text)
        self.assertIn("优先级：高优先级（建议）", copy_text)
        self.assertIn("状态：进行中", copy_text)

    def test_message_bubble_dispatches_by_card_metadata_not_text_keywords(self) -> None:
        from client.v2.agent_cards import SummaryCard, TodoCard
        from client.v2.widgets import MessageBubble

        summary_bubble = MessageBubble(
            {
                "message_type": "agent",
                "content_format": "markdown",
                "content": "## 群聊总结\n\n### 主要话题\n- 不应直接显示",
                "card": _summary(),
            },
            mine=False,
        )
        self.assertIsInstance(summary_bubble.agent_card, SummaryCard)
        self.assertIsNone(summary_bubble.findChild(QLabel, "AgentMarkdownContent"))
        self.assertEqual(summary_bubble.property("messageType"), "summary")
        self.assertEqual(summary_bubble.layout().contentsMargins().left(), 0)

        todo_bubble = MessageBubble(
            {
                "message_type": "agent",
                "content_format": "markdown",
                "content": "## 待办提取\n\n- 不应直接显示",
                "card": _todo(),
            },
            mine=False,
        )
        self.assertIsInstance(todo_bubble.agent_card, TodoCard)
        self.assertIsNone(todo_bubble.findChild(QLabel, "AgentMarkdownContent"))

        legacy = MessageBubble(
            {
                "message_type": "agent",
                "content_format": "markdown",
                "content": "## 群聊总结\n\n### 主要话题\n- 旧消息",
            },
            mine=False,
        )
        self.assertIsNone(getattr(legacy, "agent_card", None))
        self.assertIsNotNone(legacy.findChild(QLabel, "AgentMarkdownContent"))

        keyword_only = MessageBubble(
            {
                "message_type": "agent",
                "content_format": "plain",
                "content": "群聊总结和待办提取只是普通文本。",
            },
            mine=False,
        )
        self.assertIsNone(getattr(keyword_only, "agent_card", None))

    def test_chat_pane_renders_cards_and_keeps_narrow_layout_inside_bounds(self) -> None:
        from client.network import NetworkClient
        from client.service import ChatService
        from client.store import AppStore
        from client.v2.agent_cards import SummaryCard, TodoCard
        from client.v2.application import ChatPane
        from client.v2.widgets import UserAvatar

        store = AppStore()
        store.state.current_user = {"id": 1, "role": "user"}
        key = ConversationKey("group", "7")
        store.state.current_conversation = key
        store.state.messages[key] = [
            {"id": 1, "sender_id": 2, "sender_name": "Alice", "message_type": "text", "content": "开始联调", "created_at": 1000},
            {"id": 2, "sender_id": None, "sender_name": "AI Agent", "sender_kind": "agent", "sender_agent_id": "agent", "message_type": "agent", "content_format": "markdown", "content": "## 群聊总结", "card": _summary(), "created_at": 1100},
            {"id": 3, "sender_id": None, "sender_name": "AI Agent", "sender_kind": "agent", "sender_agent_id": "agent", "message_type": "agent", "content_format": "markdown", "content": "## 待办提取", "card": _todo(), "created_at": 1200},
            {"id": 4, "sender_id": None, "sender_name": "AI Agent", "sender_kind": "agent", "sender_agent_id": "agent", "message_type": "agent", "content_format": "plain", "content": "群聊总结只是普通回复", "created_at": 1300},
        ]
        pane = ChatPane(ChatService(NetworkClient("127.0.0.1", 9), store), store)
        pane.set_conversation(key, "项目组")
        pane.resize(470, 760)
        pane.show()
        self.app.processEvents()
        self.app.processEvents()
        summaries = pane.findChildren(SummaryCard)
        todos = pane.findChildren(TodoCard)
        self.assertEqual(len(summaries), 1)
        self.assertEqual(len(todos), 1)
        self.assertLessEqual(summaries[0].width(), 470)
        self.assertLessEqual(todos[0].width(), 470)
        sender_labels = [label.text() for label in pane.findChildren(QLabel, "AgentMessageSender")]
        self.assertIn("AI Agent · 总结", sender_labels)
        self.assertIn("AI Agent · 待办", sender_labels)

        rows = pane.findChildren(QWidget, "MessageRow")
        plain_avatar = rows[0].findChild(UserAvatar)
        card_avatar = rows[1].findChild(UserAvatar)
        plain_column = rows[0].findChild(QWidget, "MessageColumn")
        card_column = rows[1].findChild(QWidget, "MessageColumn")
        self.assertIsNotNone(plain_avatar)
        self.assertIsNotNone(card_avatar)
        self.assertIsNotNone(plain_column)
        self.assertIsNotNone(card_column)
        self.assertEqual(card_avatar.geometry().x(), plain_avatar.geometry().x())
        self.assertEqual(
            card_column.geometry().x() - card_avatar.geometry().right(),
            plain_column.geometry().x() - plain_avatar.geometry().right(),
        )

    def test_invalid_or_empty_card_payload_does_not_crash(self) -> None:
        from client.v2.agent_cards import SummaryCard, TodoCard
        from client.v2.widgets import MessageBubble
        from PySide6.QtCore import Qt

        empty = MessageBubble({"message_type": "agent", "card": {"kind": "summary"}}, mine=False)
        self.assertIsInstance(empty.agent_card, SummaryCard)
        self.assertTrue(empty.agent_card.findChildren(QLabel, "CardEmpty"))

        malformed = SummaryCard(
            {"kind": "summary", "overview": 42, "conclusions": "bad", "pending": [None, {"text": 3}]}
        )
        self.assertTrue(malformed.findChildren(QLabel, "CardEmpty"))

        todo = TodoCard({"kind": "todo", "tasks": ["bad", {"title": ""}], "unresolved": "bad"})
        self.assertTrue(todo.findChildren(QLabel, "CardEmpty"))

        markup = SummaryCard(
            _summary(
                title="<b>标题</b>",
                overview="<b>不应加粗</b>",
                conclusions=[{"text": "<script>alert(1)</script>", "sources": []}],
                pending=[],
            )
        )
        for label in markup.findChildren(QLabel):
            if label.objectName() in {"CardTitle", "CardBody", "SummaryPoint"}:
                self.assertEqual(label.textFormat(), Qt.TextFormat.PlainText)

    def test_long_text_wraps_within_card_height_and_expands_to_full_copy(self) -> None:
        from client.v2.agent_cards import SummaryCard

        long_overview = "团队围绕发布计划进行了讨论。" * 20
        long_point = "需要确认灰度窗口、回滚责任人和监控面板的完成时间。" * 8
        card = SummaryCard(
            _summary(
                overview=long_overview,
                conclusions=[{"text": long_point, "sources": [12]}],
                pending=[],
            )
        )
        card.resize(420, 700)
        card.show()
        self.app.processEvents()
        self.app.processEvents()
        body = card.findChildren(QLabel, "CardBody")[0]
        point = card.findChildren(QLabel, "SummaryPoint")[0]
        self.assertGreaterEqual(body.height(), body.heightForWidth(body.width()))
        self.assertGreaterEqual(point.height(), point.heightForWidth(point.width()))
        self.assertLessEqual(card.width(), 420)
        self.assertNotEqual(body.text(), long_overview)
        card.set_expanded(True)
        self.app.processEvents()
        self.app.processEvents()
        body = card.findChildren(QLabel, "CardBody")[0]
        point = card.findChildren(QLabel, "SummaryPoint")[0]
        self.assertEqual(body.text(), long_overview)
        self.assertEqual(point.text(), long_point)
        self.assertGreaterEqual(body.height(), body.heightForWidth(body.width()))


if __name__ == "__main__":
    unittest.main()
