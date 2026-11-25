# 课程设计交付说明（2026Fall）

对应课程文档：`docs/计算机网络实验课程设计_2026Fall.docx`

评分构成与材料位置：

| 评分项 | 占比 | 对应材料 |
| --- | ---: | --- |
| 聊天室服务端和客户端基本功能、说明文档及录屏 | 50% | 本目录 `design.md`、`run-and-demo.md`、`test-and-submission.md`，以及团队录屏 |
| Agent 功能与自定义扩展 | 30% | `design.md` 第 5 节、根目录 `README.md` 第 7 节 |
| 课上展示 | 20% | `run-and-demo.md` 的 15 分钟演示脚本 |

## 1. 实验要求对照

| 课程要求 | 实现位置 | 验证方式 |
| --- | --- | --- |
| TCP Socket 多客户端、断线处理、粘连与拆分 | `shared/protocol.py`、`server/app.py`、`client/network.py` | `tests/test_protocol.py`、`tests/test_server_integration.py`、`tests/test_client_network.py` |
| 注册登录、在线列表、私聊、群聊、可操作界面 | `server/app.py`、`client/service.py`、`client/store.py`、`client/v2/pages/` | `tests/test_server_integration.py`、`tests/test_client_store.py`、`tests/test_v2_ui.py` |
| @Agent 问答、群聊总结、待办提取，结果回写会话 | `server/agent.py`、`server/agent_cards.py`、`client/v2/pages/ai_center_page.py` | `tests/test_agent.py`、`tests/test_agent_cards.py`、`tests/test_agent_cards_ui.py` |
| 至少一种真实工具调用并展示调用记录 | `server/tools.py`、`AgentService` 工具执行与审计、`AICenterPage` 审计面板 | `tests/test_agent.py` 的工具调用断言、UI 工具审计测试 |
| 至少两个自定义功能 | 消息持久化与搜索、图片与表情包、离线同步、禁言与撤回、定时总结、个人待办、Web 搜索、模型降级 | 对应测试模块与 `README.md` 第 1、7 节 |
| 语言与平台不限，模型不可用时基本通信正常 | Python 3.10+；服务端标准库；V2 使用 PySide6；Ollama 可选 | `tests/test_model_provider.py`；实时 Ollama 测试默认跳过 |

验收主链路：三个客户端在线 → 私聊与群聊 → 客户端异常退出与重连同步 → 群内 @Agent → 服务端读取授权会话 → 模型生成结果 → 回写原会话 → 展示工具调用记录。

## 2. 当前完成状态

已完成并有自动化测试覆盖：

- TCP 帧协议、认证、私聊、群聊、在线状态、断线清理与离线同步。
- PySide6 V2 客户端主要页面、主题、个人待办、AI 任务中心、Agent 管理与工具审计。
- Agent 会话授权、摘要、待办提取、真实工具调用、定时总结和模型降级。
- 图片、表情包、撤回、禁言、历史搜索、Web 搜索扩展。

需要团队在提交前补充：

- PPT（列出全部成员和具体分工）。
- 15 分钟系统运行录屏。
- 小组成员信息与分工确认。

尚未实机验收：

- Windows、Linux 的桌面字体、原生标题栏和文件对话框。
- 真实 Ollama 实时测试默认不运行，需在演示机器上显式启用。

## 3. 文档索引

| 文档 | 内容 |
| --- | --- |
| `design.md` | 系统架构、模块边界、协议、数据流、关键设计决策 |
| `run-and-demo.md` | 环境准备、启动步骤、15 分钟演示脚本、录屏清单 |
| `test-and-submission.md` | 测试记录、需求覆盖矩阵、提交清单、分工模板 |
| `../architecture/structure-audit.md` | 代码结构审计与渐进拆分计划 |
