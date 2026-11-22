from __future__ import annotations

import unittest

from server.agent_cards import build_agent_card


class AgentCardParsingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.messages = [
            {"id": 1, "sender_name": "Alice"},
            {"id": 2, "sender_name": "Bob"},
            {"id": 3, "sender_name": "Alice"},
        ]

    def test_summary_filters_meta_questions_speculation_and_duplicates(self) -> None:
        card, content = build_agent_card(
            "summary",
            """{
              "overview": "团队完成联调，灰度细节仍未确定。",
              "conclusions": [
                {"text": "TCP 联调已完成", "sources": [1]},
                {"text": "用户问是否周五发布", "sources": [2]},
                {"text": "灰度可能覆盖 10%", "sources": [3]},
                {"text": "TCP 联调已完成。", "sources": [2]}
              ],
              "pending": [
                {"text": "TCP 联调已完成", "sources": [1]},
                {"text": "是否周五发布", "sources": [2]}
              ]
            }""",
            self.messages,
        )
        self.assertIsNotNone(card)
        self.assertEqual([item["text"] for item in card["conclusions"]], ["TCP 联调已完成"])
        pending = [item["text"] for item in card["pending"]]
        self.assertIn("灰度可能覆盖 10%", pending)
        self.assertIn("是否周五发布", pending)
        self.assertNotIn("TCP 联调已完成", pending)
        self.assertFalse(any("用户问" in item for item in pending))
        self.assertTrue(content.startswith("## 群聊总结"))

    def test_invalid_structured_output_never_leaks_raw_json(self) -> None:
        card, content = build_agent_card(
            "summary", '{"overview": "broken", "conclusions": [', self.messages
        )
        self.assertTrue(card["parse_error"])
        self.assertNotIn("{", content)
        self.assertIn("解析失败", content)

        prefixed, prefixed_content = build_agent_card(
            "summary",
            '以下是结果：{"overview": "broken", "conclusions": [',
            self.messages,
        )
        self.assertTrue(prefixed["parse_error"])
        self.assertNotIn('"overview"', prefixed_content)

    def test_near_duplicate_settled_and_open_items_keep_only_the_open_question(self) -> None:
        card, _ = build_agent_card(
            "summary",
            """{
              "overview": "灰度发布时间仍在讨论。",
              "conclusions": [{"text": "灰度窗口定在周四", "sources": [1]}],
              "pending": [{"text": "灰度窗口定在周四还是周五尚未决定", "sources": [2]}]
            }""",
            self.messages,
        )
        self.assertEqual(card["conclusions"], [])
        self.assertEqual(
            [item["text"] for item in card["pending"]],
            ["灰度窗口定在周四还是周五尚未决定"],
        )

    def test_todo_requires_explicit_owner_due_and_action_wording(self) -> None:
        card, content = build_agent_card(
            "todo",
            """{
              "tasks": [
                {"title": "补充演示录屏", "owner": "Alice", "due": "周四", "group": "confirmed", "source_message_id": 1},
                {"title": "要不要周五发布？"},
                {"title": "更新部署文档", "owner": "Charlie", "due": "未明确", "group": "confirmed"},
                {"title": "整理告警阈值", "group": "suggested"}
              ],
              "unresolved": ["回滚负责人尚未确认"]
            }""",
            self.messages,
        )
        self.assertIsNotNone(card)
        self.assertEqual([task["title"] for task in card["tasks"]], ["补充演示录屏", "更新部署文档", "整理告警阈值"])
        self.assertEqual(card["tasks"][0]["owner"], "Alice")
        self.assertEqual(card["tasks"][0]["due"], "周四")
        self.assertEqual(card["tasks"][0]["sources"], [1])
        self.assertEqual(card["tasks"][1]["owner"], "")
        self.assertEqual(card["tasks"][1]["due"], "")
        self.assertEqual(card["tasks"][2]["group"], "suggested")
        self.assertEqual(card["unresolved"], ["回滚负责人尚未确认"])
        self.assertIn("## 待办提取", content)

    def test_todo_can_return_an_empty_valid_result(self) -> None:
        card, content = build_agent_card(
            "todo", '{"tasks": [], "unresolved": []}', self.messages
        )
        self.assertEqual(card["tasks"], [])
        self.assertFalse(card["parse_error"])
        self.assertIn("未发现明确待办", content)

    def test_legacy_markdown_is_still_accepted(self) -> None:
        summary, _ = build_agent_card(
            "summary",
            "主题：TCP 联调\n结论：已完成\n未决问题：未明确",
            self.messages,
        )
        self.assertEqual(summary["overview"], "TCP 联调")
        self.assertEqual([item["text"] for item in summary["conclusions"]], ["已完成"])
        todo, _ = build_agent_card(
            "todo",
            "事项：补充演示录屏\n负责人：Alice\n期限：周四",
            self.messages,
        )
        self.assertEqual(todo["tasks"][0]["owner"], "Alice")
        self.assertEqual(todo["tasks"][0]["due"], "周四")


if __name__ == "__main__":
    unittest.main()
