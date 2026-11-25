# 系统设计说明

## 1. 目标与范围

Parlor 是一个面向课程验收的多人聊天室：客户端通过 TCP 连接服务端，完成注册登录、私聊、群聊和断线重连；服务端在授权范围内为 Agent 提供会话上下文，Agent 通过真实工具调用完成任务并把结果回写聊天室。

设计约束：

- 服务端只依赖 Python 标准库，便于在课程机器上复现。
- V2 客户端固定使用 PySide6 / Qt Widgets。
- 模型是可降级依赖：模型不可用时，注册、登录、私聊和群聊仍可正常工作。
- 面向可信单机或可信局域网演示，不作为公网生产系统。

## 2. 总体架构

```text
┌──────────────────────┐        TCP 长度前缀 JSON        ┌────────────────────────┐
│ PySide6 V2 Client    │ ──────────────────────────────▶ │ asyncio Chat Server    │
│  pages / shell       │                                 │  ChatServer            │
│  service / store     │ ◀────────────────────────────── │  AgentService          │
└──────────────────────┘                                 └───────┬────────┬───────┘
                                                                 │        │
                                                        SQLite ◀─┘        └─▶ media/
                                                                 │
                                                                 ▼
                                                     OpenAI / Ollama / Gemini
```

客户端只和服务端通信；模型调用、工具执行、权限判断都在服务端完成。客户端展示的是服务端投影后的状态，不直接访问数据库或模型。

## 3. 协议设计

每个协议帧由「4 字节无符号大端长度 + UTF-8 JSON 消息体」组成，单帧上限 4 MiB。

公共信封：

```json
{
  "version": 1,
  "request_id": "uuid",
  "type": "message.send",
  "timestamp": 0,
  "payload": {}
}
```

约定：

- 客户端生成 `request_id`，服务端在响应中回传，用于关联请求和界面状态。
- 服务端生成最终消息 ID 和时间戳，避免客户端时间不可信。
- 大字段（图片）使用 Base64 传输，服务端校验 MIME、大小和文件签名后落盘，数据库只保存元数据和路径。
- 协议错误使用统一错误响应，不把 Python 异常直接暴露给客户端。

实现位置：`shared/protocol.py`。

## 4. 客户端结构

数据流固定为：

```text
NetworkClient -> ChatService -> AppStore -> UI
```

- `client/network.py`：后台线程维护 Socket、收发帧、自动重连和事件接收。
- `client/service.py`：协议命令门面，页面通过它发请求，不直接拼 JSON。
- `client/store.py`：把协议事件投影为会话、消息、成员、Agent、待办状态，并按 topic 通知监听者。
- `client/v2/pages/`：按页面拆分的 UI，只消费 Store 快照和 Service 命令。
- `client/v2/shell/`：工作区、导航和主窗口。
- `client/v2/widgets/` 等：可复用视觉组件。

当前 V2 目录：

```text
client/v2/
  application.py          # 兼容导出层，保留旧导入路径
  pages/                  # auth, chat, contacts, ai_center, settings, admin, agent_manager
  shell/                  # workspace, main_window
  ui_helpers.py           # 消息预览、分组等纯函数
  ui_constants.py         # 布局与上传限制
  widgets/  agent_cards.py  details.py  personal_todo.py
  profile_widgets.py  stickers.py  theme.py  icons.py  time_text.py
```

页面之间不互相导入；共享逻辑下沉到组件、`ui_helpers` 或 `ChatService`。`application.py` 只做重新导出，保证旧测试和旧导入继续工作。

## 5. 服务端结构

```text
server/
  app.py                # asyncio 连接管理、协议分发、广播与调度
  session.py            # 单个客户端会话与帧发送
  constants.py          # 文本长度、Agent 成员标识
  handlers/
    auth.py             # 注册、登录、在线状态、资料身份
    profile.py          # 资料头像
    todos.py            # 个人待办
    groups.py           # 群组、私聊、禁言
    messages.py         # 消息、历史、媒体、提及
    agents.py           # Agent、记忆、工具
  agent.py              # AgentService 组合根：调用流程与结果写入
  agent_time.py         # 时间窗口解析
  agent_runtime/
    runtime.py          # 运行时状态与回调类型
    providers.py        # 模型与供应商选择
    context.py          # 上下文、记忆与压缩
    generation.py       # 生成、分段与联网搜索
    tools.py            # 工具校验、执行与待办循环
  agent_cards.py        # 摘要与待办等结构化卡片
  agent_config.py       # Agent 角色卡与记忆配置校验
  database.py           # SQLite 组合根，保持旧方法入口
  storage/
    base.py             # 异常、连接与会话 ID 工具
    schema.py           # 建表与迁移 SQL
    users.py  agents.py  stickers.py  memory.py
    groups.py  messages.py  activity.py
  media.py              # 图片签名、尺寸与头像校验
  stickers.py           # 表情包目录与匹配
  todos.py              # 账户隔离的个人待办服务
  tools.py              # 工具目录与参数 schema
  model_provider.py     # OpenAI / Ollama / Gemini 兼容适配
  search_provider.py    # 服务端 Web 搜索适配
  admin/                # 仅监听 loopback 的管理台
```

一次请求的处理路径：

1. `ChatServer._handle_connection` 读取帧并解析信封。
2. `_dispatch` 按事件类型调用 `server/handlers/` 中的处理器。
3. 处理器完成认证、权限、参数校验和数据写入。
4. 服务端向目标用户或群组广播事件。
5. 客户端 Store 投影事件并通知 UI。

`ChatServer` 通过处理器组合获得全部协议方法；处理器共享服务端的数据库、待办、Agent 和广播能力。这样拆分不改变协议名和调用方式，同时让每个文件只承载一个业务域。

数据库操作从事件循环移到后台线程，启用 WAL、外键约束和忙等待超时；模型调用有并发限制和硬超时，不阻塞普通聊天。

## 6. Agent 运行时

Agent 的输入是一条用户任务，输出是写回原会话的结构化结果。

关键步骤：

1. **授权**：服务端根据当前用户、会话类型和群组设置确定可读取的消息范围。
2. **上下文**：读取授权范围内的聊天记录，排除撤回消息和越权会话。
3. **任务分类**：区分问答、总结、待办提取、搜索、定时总结等类型。
4. **工具调用**：模型通过原生 Tool Calling 或严格 JSON 工具桥请求工具；服务端再次校验参数、权限和确认要求。
5. **执行与审计**：工具真实执行，记录调用参数、结果状态和耗时；UI 可查看调用记录。
6. **回写**：结果保存为消息或卡片，广播到原会话。

工具示例：读取会话记录、搜索历史、创建/更新/完成待办、Web 搜索。工具不接受模型指定的待办所有者，所有者始终绑定当前请求用户。

模型不可用时，`AgentService` 返回受控错误，聊天主链路不受影响。

## 7. 数据与安全

- 密码使用 PBKDF2 加盐哈希，不保存明文。
- 消息、群成员、待办、Agent 配置和审计记录保存在 SQLite；图片和头像保存在数据目录。
- 个人待办按账户隔离，更新和删除要求版本匹配，删除需要明确确认。
- 群主/管理员权限、禁言、撤回时限在服务端校验，客户端状态不能作为授权依据。
- 图片校验 MIME、大小、文件签名和头像尺寸。
- 管理台默认只监听 loopback，使用 HttpOnly/SameSite Cookie 和 CSRF 校验。
- 日志不记录密码、完整 API Key、图片 Base64 或无必要的完整聊天正文。

面向课程演示，服务端不提供 TLS；跨公网部署需要额外加 TLS 反向代理或传输层加密。

## 8. 自定义扩展

除课程基本要求外，项目包含：

- 聊天记录持久化与历史搜索。
- 图片消息、表情包目录和 Agent 表情包回复。
- 离线消息同步与断线重连。
- 禁言、撤回和权限校验。
- Agent 定时总结。
- 账户隔离的个人待办与批量导入。
- 服务端 Web 搜索工具。
- 多模型提供商与离线降级。

## 9. 关键设计决策

| 决策 | 原因 |
| --- | --- |
| 服务端保持标准库 asyncio | 课程机器无需安装第三方服务端依赖，便于复现 |
| JSON 长度前缀协议 | 可读、易调试，长度前缀解决 TCP 粘连与拆分 |
| 客户端 Store 投影 | UI 不直接消费原始协议事件，便于多页面共享状态 |
| 页面按功能拆分，`application.py` 保留兼容导出 | 降低单文件复杂度，同时不破坏现有测试和导入 |
| 模型与聊天主链路解耦 | 模型离线或超时不阻塞普通消息 |
| 工具执行与权限校验留在服务端 | 客户端不能伪造工具权限或待办所有者 |

## 10. 已知限制

- 当前验收环境为 macOS；Windows、Linux 的视觉与原生控件表现尚未实机验收。
- 实时 Ollama 测试默认跳过，需要在演示机器上单独启用。
- 管理台和聊天服务面向可信局域网，不包含公网级别的限流、审计留存和 TLS。
