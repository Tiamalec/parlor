from __future__ import annotations

import unittest
from datetime import datetime, timedelta

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

    def test_first_complete_json_object_survives_trailing_output(self) -> None:
        card, _ = build_agent_card(
            "summary",
            '{"overview": "结论已确认", "conclusions": [], "pending": []}\n'
            '补充说明：这是一个多余的第二个对象 {"unused": true}',
            self.messages,
        )
        self.assertFalse(card["parse_error"])
        self.assertEqual(card["overview"], "结论已确认")

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
                {"title": "更新部署文档", "owner": "Charlie", "due": "未明确", "group": "confirmed", "sources": [2]},
                {"title": "整理告警阈值", "group": "suggested", "sources": [3]}
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

    def test_personal_todo_recovers_explicit_first_person_commitment(self) -> None:
        messages = [{
            "id": 10, "sender_id": 7, "sender_kind": "user", "sender_name": "owner",
            "content": "我明天要去参加一个原神的七神会议，然后 @user2 你去给我准备一下资料",
        }]
        card, _ = build_agent_card(
            "todo", '{"tasks":[],"unresolved":[]}', messages,
            requested_by=7, requester_name="owner",
        )
        self.assertEqual(len(card["tasks"]), 1)
        self.assertEqual(card["tasks"][0]["title"], "参加一个原神的七神会议")
        self.assertEqual(card["tasks"][0]["owner"], "owner")
        self.assertEqual(card["tasks"][0]["due"], "明天")
        self.assertEqual(card["tasks"][0]["sources"], [10])

        group_card, _ = build_agent_card(
            "todo",
            '{"tasks":[{"title":"准备七神会议资料","owner":"user2","source_message_id":10}]}',
            [{**messages[0], "mentions": [{"member_id": "user:8", "display": "user2"}]}],
        )
        self.assertEqual(
            {task["owner"] for task in group_card["tasks"]}, {"owner", "user2"}
        )

    def test_first_person_scheduled_meeting_without_action_verb_is_extracted(self) -> None:
        sent_at = datetime(2026, 11, 24, 10, 58).timestamp()
        messages = [
            {
                "id": 40,
                "sender_id": 7,
                "sender_name": "摸底鳄鱼",
                "sender_kind": "user",
                "content": "我后天早上八点还有一场七神会议",
                "created_at": sent_at,
            }
        ]
        card, _ = build_agent_card(
            "todo",
            '{"tasks":[],"unresolved":[]}',
            messages,
            requested_by=7,
            requester_name="摸底鳄鱼",
        )
        self.assertEqual(len(card["tasks"]), 1)
        task = card["tasks"][0]
        self.assertEqual(task["title"], "七神会议")
        self.assertEqual(task["due"], "后天早上八点")
        self.assertEqual(
            datetime.fromtimestamp(task["due_at"]),
            datetime(2026, 11, 26, 8, 0),
        )
        self.assertEqual(task["sources"], [40])

    def test_same_title_on_different_dates_is_not_merged(self) -> None:
        first = datetime(2026, 11, 24, 10, 0).timestamp()
        second = datetime(2026, 11, 25, 10, 0).timestamp()
        messages = [
            {
                "id": 51,
                "sender_id": 7,
                "sender_name": "摸底鳄鱼",
                "sender_kind": "user",
                "content": "我明天要参加七神会议",
                "created_at": first,
            },
            {
                "id": 52,
                "sender_id": 7,
                "sender_name": "摸底鳄鱼",
                "sender_kind": "user",
                "content": "我后天要参加七神会议",
                "created_at": second,
            },
        ]
        card, _ = build_agent_card(
            "todo",
            '{"tasks":[],"unresolved":[]}',
            messages,
            requested_by=7,
            requester_name="摸底鳄鱼",
        )
        self.assertEqual(len(card["tasks"]), 2)
        self.assertEqual(
            [datetime.fromtimestamp(task["due_at"]).date() for task in card["tasks"]],
            [datetime(2026, 11, 25).date(), datetime(2026, 11, 27).date()],
        )

    def test_action_prefix_same_title_and_time_is_merged(self) -> None:
        due = "2026-11-26T08:00:00+08:00"
        card, _ = build_agent_card(
            "todo",
            f'''{{"tasks":[
              {{"title":"参加七神会议","owner":"摸底鳄鱼","due_at":"{due}","sources":[71]}},
              {{"title":"七神会议","owner":"摸底鳄鱼","due_at":"{due}","sources":[72]}}
            ]}}''',
            [
                {"id": 71, "sender_id": 7, "sender_name": "摸底鳄鱼", "sender_kind": "user"},
                {"id": 72, "sender_id": 7, "sender_name": "摸底鳄鱼", "sender_kind": "user"},
            ],
            requested_by=7,
            requester_name="摸底鳄鱼",
        )
        self.assertEqual(len(card["tasks"]), 1)
        self.assertEqual(set(card["tasks"][0]["sources"]), {71, 72})

    def test_similar_title_same_day_merges_date_only_and_explicit_clock(self) -> None:
        card, _ = build_agent_card(
            "todo",
            """{"tasks":[
              {"title":"参加原神七神会议","owner":"摸底鳄鱼","due":"2026-11-25","sources":[81]},
              {"title":"参加七神会议","owner":"摸底鳄鱼","due":"2026-11-25 08:00","sources":[82]}
            ]}""",
            [
                {"id": 81, "sender_id": 7, "sender_name": "摸底鳄鱼", "sender_kind": "user"},
                {"id": 82, "sender_id": 7, "sender_name": "摸底鳄鱼", "sender_kind": "user"},
            ],
            requested_by=7,
            requester_name="摸底鳄鱼",
        )
        self.assertEqual(len(card["tasks"]), 1)
        self.assertEqual(card["tasks"][0]["due"], "2026-11-25 08:00")

    def test_same_title_with_different_explicit_clocks_is_not_merged(self) -> None:
        card, _ = build_agent_card(
            "todo",
            """{"tasks":[
              {"title":"七神会议","owner":"摸底鳄鱼","due":"2026-11-25 08:00","sources":[91]},
              {"title":"七神会议","owner":"摸底鳄鱼","due":"2026-11-25 14:00","sources":[92]}
            ]}""",
            [
                {"id": 91, "sender_id": 7, "sender_name": "摸底鳄鱼", "sender_kind": "user"},
                {"id": 92, "sender_id": 7, "sender_name": "摸底鳄鱼", "sender_kind": "user"},
            ],
            requested_by=7,
            requester_name="摸底鳄鱼",
        )
        self.assertEqual(len(card["tasks"]), 2)

    def test_todo_resolves_current_other_and_unassigned_owners(self) -> None:
        messages = [
            {
                "id": 21, "sender_id": 7, "sender_name": "Alice",
                "sender_kind": "user", "conversation_type": "group",
                "conversation_id": "17",
            },
            {
                "id": 22, "sender_id": 8, "sender_name": "Bob",
                "sender_kind": "user", "conversation_type": "group",
                "conversation_id": "17",
            },
            {
                "id": 23, "sender_id": 7, "sender_name": "Alice",
                "sender_kind": "user", "conversation_type": "group",
                "conversation_id": "17",
                "mentions": [{"member_id": "user:9", "display": "Carol"}],
            },
        ]
        payload = """{
          "tasks": [
            {"title": "更新客户端", "owner": "Alice", "sources": [21], "todo_id": 42, "sync_status": "synced"},
            {"title": "补充接口文档", "owner": "Bob", "sources": [22]},
            {"title": "整理发布清单", "sources": [23]},
            {"title": "准备演示环境", "owner": "Carol", "sources": [23]}
          ]
        }"""

        group_card, _ = build_agent_card(
            "todo", payload, messages,
            requested_by=7, requester_name="Alice", filter_owner=False,
        )
        tasks = group_card["tasks"]
        self.assertEqual(
            [task["owner_kind"] for task in tasks],
        ["current", "unassigned", "other", "other"],
        )
        self.assertEqual(
            [task["assignee_user_id"] for task in tasks],
            [7, None, 8, 9],
        )
        self.assertEqual(group_card["source_conversation_type"], "group")
        self.assertEqual(group_card["source_conversation_id"], "17")
        self.assertEqual(tasks[0]["source_conversation_type"], "group")
        self.assertEqual(tasks[0]["source_conversation_id"], "17")
        self.assertEqual(tasks[0]["todo_id"], 42)
        self.assertEqual(tasks[0]["sync_status"], "synced")

        personal_card, _ = build_agent_card(
            "todo", payload, messages,
            requested_by=7, requester_name="Alice",
        )
        self.assertEqual([task["owner"] for task in personal_card["tasks"]], ["Alice"])

    def test_todo_priority_can_be_inferred_explicit_or_default(self) -> None:
        card, _ = build_agent_card(
            "todo",
            """{
              "tasks": [
                {"title": "紧急修复支付回调", "sources": [1]},
                {"title": "有空时整理旧文档", "sources": [1]},
                {"title": "发布版本公告", "priority": "high", "priority_basis": "explicit", "sources": [2]},
                {"title": "更新普通配置", "sources": [3]}
              ]
            }""",
            self.messages,
        )
        self.assertEqual(
            [(task["priority"], task["priority_basis"]) for task in card["tasks"]],
            [
                ("high", "inferred"),
                ("low", "inferred"),
                ("high", "explicit"),
                ("normal", "default"),
            ],
        )

    def test_todo_due_at_uses_source_context_and_explicit_iso_values(self) -> None:
        sent_at = "2026-11-24T09:00:00+08:00"
        messages = [{"id": 31, "created_at": sent_at}, {"id": 32}]
        explicit_due = "2026-11-28T18:30:00+08:00"
        card, _ = build_agent_card(
            "todo",
            f"""{{
              "tasks": [
                {{"title": "提交上线检查", "description": "检查回滚脚本",
                  "due": "明天", "status": "open", "source_message_id": 31}},
                {{"title": "确认发布窗口", "due": "11月28日 18:30",
                  "due_at": "{explicit_due}", "sources": [31, 32]}},
                {{"title": "整理会议纪要", "due": "待定", "due_at": "周四", "sources": [32]}}
              ]
            }}""",
            messages,
        )
        tomorrow_task, explicit_task, pending_task = card["tasks"]
        base_date = datetime.fromtimestamp(
            int(datetime.fromisoformat(sent_at).timestamp())
        ).astimezone().date()
        self.assertEqual(
            datetime.fromtimestamp(tomorrow_task["due_at"]).astimezone().date(),
            base_date + timedelta(days=1),
        )
        self.assertEqual(tomorrow_task["due"], "明天")
        self.assertEqual(tomorrow_task["description"], "检查回滚脚本")
        self.assertEqual(tomorrow_task["status"], "open")
        self.assertEqual(
            explicit_task["due_at"],
            int(datetime.fromisoformat(explicit_due).timestamp()),
        )
        self.assertIsNone(pending_task["due_at"])
        self.assertEqual(pending_task["due"], "")

    def test_relative_due_text_overrides_incorrect_model_timestamp(self) -> None:
        sent_at = datetime(2026, 11, 24, 11, 0).timestamp()
        card, _ = build_agent_card(
            "todo",
            """{"tasks":[{
              "title":"参加学术会议",
              "due":"2026-11-25 08:00:00",
              "due_at":"2026-11-25T08:00:00+08:00",
              "sources":[61]
            }]}""",
            [
                {
                    "id": 61,
                    "sender_id": 7,
                    "sender_name": "摸底鳄鱼",
                    "sender_kind": "user",
                    "content": "我后天早上八点要去参加一个学术会议",
                    "created_at": sent_at,
                }
            ],
            requested_by=7,
            requester_name="摸底鳄鱼",
        )
        self.assertEqual(
            datetime.fromtimestamp(card["tasks"][0]["due_at"]),
            datetime(2026, 11, 26, 8, 0),
        )
        self.assertEqual(card["tasks"][0]["due"], "后天早上八点")

    def test_todo_does_not_fabricate_missing_metadata(self) -> None:
        card, content = build_agent_card(
            "todo",
            """{
              "tasks": [{
                "title": "整理材料", "owner": "Charlie", "description": "未明确",
                "due": "待定", "due_at": "周四", "priority": "P9",
                "status": "maybe", "sources": [999], "todo_id": 0
              }]
            }""",
            self.messages,
        )
        self.assertEqual(card["tasks"], [])
        with_source = build_agent_card(
            "todo",
            """{"tasks":[{"title":"整理材料","owner":"Charlie",
            "description":"未明确","due":"待定","due_at":"周四",
            "priority":"P9","status":"maybe","sources":[1],"todo_id":0}]}""",
            self.messages,
        )[0]
        task = with_source["tasks"][0]
        self.assertEqual(task["owner"], "")
        self.assertIsNone(task["assignee_user_id"])
        self.assertEqual(task["owner_kind"], "unassigned")
        self.assertEqual(task["due"], "")
        self.assertIsNone(task["due_at"])
        self.assertNotIn("description", task)
        self.assertEqual((task["priority"], task["priority_basis"]), ("normal", "default"))
        self.assertEqual(task["status"], "open")
        self.assertEqual(task["sources"], [1])
        self.assertNotIn("todo_id", task)
        self.assertNotIn("source_conversation_type", with_source)
        self.assertIn("## 待办提取", content)

    def test_completed_extracted_tasks_are_retained_for_client_grouping(self) -> None:
        card, _ = build_agent_card(
            "todo",
            """{"tasks":[
              {"title":"已经完成的事情","status":"completed","sources":[1]},
              {"title":"仍需处理的事情","status":"open","sources":[2]}
            ]}""",
            self.messages,
        )
        self.assertEqual(
            [task["title"] for task in card["tasks"]],
            ["已经完成的事情", "仍需处理的事情"],
        )
        self.assertEqual(card["tasks"][0]["status"], "completed")

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
