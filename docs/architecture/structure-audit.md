# 结构审计与渐进拆分计划

状态：阶段 1 完成，服务端媒体模块已抽取
日期：2026-11-25
范围：`client/`、`server/`、`shared/`、`tests/`

## 0. 结论

当前分层方向是正确的：客户端保持 `NetworkClient -> ChatService -> AppStore -> UI`，服务端保持 `asyncio -> ChatServer -> 领域服务 -> SQLite`，协议集中在 `shared/protocol.py`。结构混乱不是因为选错了架构，而是因为模块粒度过粗：

- 少数文件承担了整层的职责，单文件达到数千行。
- 目录按技术类型平铺，导入关系无法表达“谁能依赖谁”。
- 测试按文件而不是按行为或模块组织，移动代码时缺少稳定边界。

因此不建议更换框架或重写。应保持现有数据流、Signal、Store topic 和协议，按“先拆文件、后理目录”的顺序渐进收敛。UI 侧已在 `docs/ui/component-guidelines.md` 第 3 节给出 `application.py` 的拆分顺序，本文件是对全仓库的补充，不覆盖该规范。

## 0.1 执行记录（2026-11-25）

已完成：

- `client/v2/application.py` 从 5,042 行收缩为 66 行兼容导出层。
- 页面拆到 `client/v2/pages/`，应用外壳拆到 `client/v2/shell/`。
- 公共函数与常量拆到 `client/v2/ui_helpers.py` 和 `client/v2/ui_constants.py`。
- 图片签名与尺寸校验从 `server/app.py` 抽到 `server/media.py`。
- 删除 `server/stickers.py` 中重复的图片校验实现，改为复用 `server/media.py`。
- `server/app.py` 从 1,697 行收缩为 296 行，保留生命周期、分发和广播。
- 协议处理器按域拆到 `server/handlers/`：认证、资料、待办、群组、消息、Agent。
- `ClientSession` 拆到 `server/session.py`，共享限制拆到 `server/constants.py`。
- `server/database.py` 从 2,577 行收缩为 264 行组合根；数据域拆到 `server/storage/`，schema 独立到 `server/storage/schema.py`。
- `server/agent.py` 从 2,644 行收缩为 674 行组合根；运行时拆到 `server/agent_runtime/`，时间解析独立到 `server/agent_time.py`。
- 旧 Tkinter 客户端 `client/app.py` 与 `--legacy` 入口已删除，客户端只保留 PySide6 V2。

验证：

- `python -m compileall -q client server shared` 通过。
- 完整测试 235 项通过，1 项实时 Ollama 测试跳过。
- 旧导入路径 `client.v2.application.*` 保持可用。
- `ChatServer` 的公开入口和协议处理器名称保持不变。

## 1. 量化基线（拆分前）

全仓库 Python 文件 66 个，约 37,000 行。

| 文件 | 行数 | 主要结构 | 问题 |
| --- | ---: | --- | --- |
| `client/v2/application.py` | 5,042 | 17 个类 | 7 个页面、对话框、窗口和入口混在一起 |
| `server/agent.py` | 2,643 | `AgentService` 66 个方法 | 提示词、调度、工具、时间解析、模型调用混在一起 |
| `server/database.py` | 2,577 | `Database` 107 个方法 | 用户、会话、消息、群组、待办、Agent 配置共用一个仓储 |
| `tests/test_v2_ui.py` | 2,096 | 单个测试类 | 页面级测试无法单独运行 |
| `client/v2/agent_cards.py` | 1,718 | 5 个类、31 个函数 | 卡片、时间与数据投影混合 |
| `server/app.py` | 1,697 | `ChatServer` 93 个方法 | 传输、协议分发、校验、媒体处理混在一起 |
| `server/agent_cards.py` | 1,542 | 48 个函数 | 结构化卡片与日期解析混合 |
| `client/v2/widgets/` | 1,272 | 9 个类、7 个模块 | 已按表面、头像、文本、消息、会话和开关拆分 |
| `client/v2/personal_todo.py` | 1,144 | 3 个类 | 页面、模型投影和请求编排混合 |
| `client/store.py` | 647 | `handle_event` 约 494 行 | 所有事件类型集中在一个分发方法 |

集中度：前 4 个业务文件合计约 11,300 行，占全部业务代码的三分之一以上；前 9 个文件超过 19,000 行。

拆分后的主要变化：

| 文件 | 行数 |
| --- | ---: |
| `client/v2/application.py` | 66（兼容导出） |
| `server/app.py` | 296（生命周期、分发、广播） |
| `server/handlers/` | 81–499（按协议域拆分） |
| `server/media.py` | 80 |
| `server/session.py` | 30 |
| `server/database.py` | 264（组合根） |
| `server/storage/` | 9–602（按数据域拆分） |
| `server/agent.py` | 674（组合根） |
| `server/agent_runtime/` | 9–957（按运行时阶段拆分） |
| `server/agent_time.py` | 403 |

## 2. 具体问题与影响

### 2.1 `application.py` 是 UI 层的单点

`ChatPane` 一个类约 1,949 行、108 个方法；同一文件还包含 `AuthPage`、`ContactsPage`、`SettingsPage`、`AICenterPage`、`AdminPage`、`Workspace`、`MainWindow` 和 `run()`。

影响：

- 任意页面测试都会加载整棵 UI 导入图，启动慢、与页面无关的失败会互相干扰。
- 多人修改不同页面时冲突集中在一个文件。
- 页面之间的依赖无法被静态约束，容易形成隐式耦合。
- 重命名或移动类会牵动大量测试导入。

### 2.2 `server/app.py` 混合传输与业务策略

同一文件既处理帧读写、连接生命周期，又处理图片签名、大小上限、权限校验、消息路由和群操作。

影响：

- 校验和权限逻辑只能通过 socket 或完整服务端夹具测试。
- 事件循环代码与纯函数策略混在一起，超时和异常路径难以单独推理。

### 2.3 `server/agent.py` 混合 Agent 运行时的多个阶段

`AgentService` 同时负责提示词拼装、时间窗口解析、工具调用、待办导入、模型提供商选择、调度与审计。

影响：

- 调整一个工具行为需要理解整个运行时。
- 纯时间解析和提示词构造无法脱离数据库与服务对象测试。

### 2.4 `server/database.py` 是单一巨型仓储

`Database` 覆盖用户、会话、消息、群成员、待办、Agent 卡、审计等全部表。

影响：

- 任意 schema 变更都落在同一个类中，回归面覆盖全部功能。
- 调用方无法从类型或模块名看出自己依赖哪一块数据。
- 事务边界容易在一个巨型类中被隐式扩大。

### 2.5 `AppStore.handle_event` 是事件分发的单点

单个方法按事件类型分支约 494 行，每个分支同时负责状态投影、缓存更新和 `notify`。

影响：

- 新增事件必须修改同一函数，遗漏 `notify` 或 topic 不会在结构上暴露。
- 事件处理逻辑无法按协议域拆开测试。

### 2.6 测试按文件平铺

23 个测试文件全部在 `tests/` 根目录；`test_v2_ui.py` 单文件 2,096 行。

影响：

- 拆分源码后，测试的归属和最小验证集不明确。
- 页面级失败需要跑整个 UI 测试文件。

### 2.7 目录没有表达边界

`client/` 和 `server/` 都是平铺模块，没有子包。导入语句只说明“导入了哪个文件”，不能说明依赖方向是否允许。

影响：

- UI 可以直接依赖任意模块，服务端入口也可以直接操作任意数据。
- 新代码没有明确的落点，只能继续堆进已有大文件。

## 3. 目标结构

原则：保留所有现有入口、公开类名、Signal、Store topic、协议 payload；新目录只用于承载已经拆出的代码，不追求一次性完成。

```text
client/
  models.py  network.py  store.py  service.py  drafts.py  profile.py
  v2/
    application.py          # 逐步退化为兼容导出层
    pages/
      auth.py  chat.py  contacts.py  ai_center.py  settings.py  admin.py
    components/
      widgets/  details.py  cards.py  personal_todo.py  profile_widgets.py
    shell/
      workspace.py  main_window.py
    theme.py  icons.py  time_text.py  stickers.py

server/
  app.py                    # 组合根与兼容导出，不放业务规则
  transport/
    server.py  frame.py  media.py
  services/
    messaging.py  groups.py  presence.py  todos.py
  storage/
    database.py             # 门面
    users.py  conversations.py  messages.py  agents.py  todos.py
  agent_runtime/
    runtime.py  providers.py  context.py  generation.py  tools.py
  agent_time.py
  storage/
    database.py  schema.py  base.py  users.py  agents.py  messages.py  ...
  admin/

shared/
  protocol.py

tests/
  client/  server/  shared/  ui/
```

边界规则：

- `pages/` 只能依赖 `components/`、`theme`、`icons`、`time_text` 以及 `client.service` 暴露的命令；页面之间不互相导入。
- `components/` 不发起网络请求，不读取全局 Store 单例。
- `transport/` 只负责帧、连接和媒体落盘，不写业务规则。
- `storage/` 的仓储只做数据访问，不调用模型或 UI。
- `agent/` 内部按阶段分层，提示词和调度不直接执行 SQL。
- `shared/` 不依赖 `client/` 或 `server/`。

## 4. 分阶段执行

### 阶段 0：建立基线

不做代码移动。记录当前测试数量与结果，作为每个阶段的对照。

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
```

若存在依赖真实 Ollama 的测试，单独记录为跳过，不计入通过。

### 阶段 1：按规范拆分 `application.py`（已完成）

严格沿用 `docs/ui/component-guidelines.md` 第 3 节的顺序，每次只移动一组类：

1. `pages/settings_page.py`：`SettingsPage`
2. `pages/ai_center_page.py`：`AgentTaskCard`、`NewAgentTaskDialog`、`AICenterPage`
3. `pages/contacts_page.py`：`ContactsPage`、`TextPrompt`
4. `pages/chat_page.py`：`ConversationPane`、`ChatPane`、`DetailsScrim`
5. `pages/admin_page.py`：`AdminPage`
6. `workspace.py`：`Workspace`
7. `main_window.py`：`AuthPage`、`MainWindow`、`run()`

迁移规则：

- 新模块定义类，旧 `application.py` 导入并重新导出同名对象。
- 不改变 Signal 名、参数、Service 调用、Store topic 和布局。
- 一个提交只移动一组类；移动和布局改写分开。
- 每次移动后运行 `tests.test_v2_ui` 与相关页面测试。

### 阶段 2：拆分 `server/app.py`（已完成入口与处理器拆分）

先抽取无状态函数，再动服务对象：

1. `server/media.py`：图片签名、尺寸与头像校验（已完成）。
2. `server/session.py`：连接会话与帧发送（已完成）。
3. `server/handlers/`：按协议域拆分请求处理器（已完成）。
4. `ChatServer` 保留连接管理和协议分发，作为组合根（已完成）。

验收重点：协议错误码、超时、断线清理行为不变。

### 阶段 3：拆分 `server/database.py`（已完成）

保留 `Database` 作为门面，内部按聚合拆仓储：用户、会话与消息、群成员与权限、待办、Agent 配置与审计。

- 不改变 schema。
- 不改变公开方法签名；门面委托到仓储。
- 每个仓储只接受显式参数，不持有全局连接。
- 事务边界在拆分前先写清楚，再移动代码。

### 阶段 4：拆分 `server/agent.py`（已完成）

按阶段拆分：时间解析、提示词构造、工具执行、模型调用、调度与审计。

- 时间解析与提示词构造先变成纯函数，便于直接测试。
- `AgentService` 保留对外入口，内部委托。
- 工具执行的授权检查留在调用边界，不随纯函数移动。

### 阶段 5：收敛 `AppStore.handle_event`

把分支改为按事件类型的处理器表，保持处理顺序、状态变更和 `notify` topic 完全一致。一次只迁移一组事件，并用现有 Store 测试锁定行为。

### 阶段 6：整理测试目录

按 `client/`、`server/`、`shared/`、`ui/` 分组；拆分 `test_v2_ui.py` 时按页面建立文件。保留旧模块名作为薄导入层，避免一次性修改所有测试入口。

## 5. 每阶段验收

```bash
python -m compileall -q client server shared
python -m unittest tests.test_v2_ui -v
python -m unittest discover -s tests -p 'test_*.py' -v
```

行为不变量：

- `shared/protocol.py` 的帧格式、事件名和 payload 不变。
- 客户端 Signal 名与参数不变。
- Store topic 集合与触发时机不变。
- 现有导入路径可用，至少保留一个版本周期。
- 不新增运行时依赖。

未实际运行的真实平台（Windows、Linux、macOS 原生外观）继续标记为未验证。

## 6. 风险与回滚

| 风险 | 控制 |
| --- | --- |
| 导入路径失效 | 旧模块重新导出；测试保持原导入 |
| 循环导入 | 页面不互相导入；共享逻辑下沉到 components 或 service |
| 移动时夹带行为变更 | 每个提交只做移动或只做改写 |
| 巨型类拆分改变事务边界 | 先写测试锁定，再按聚合移动 |
| 测试无法证明等价 | 以协议、Signal、Store topic 和现有断言作为行为契约 |

每个阶段独立提交，可按提交回滚；文档与阶段记录同步更新 `docs/ui/roadmap.md` 的对应条目。

## 7. 明确不做

- 不迁移 React，不引入整体 Qt 主题库或新状态管理框架。
- 不做一次性大爆炸式目录重写。
- 不在拆分提交中重写页面布局或视觉样式。
- 不为了目录好看新增空抽象层；只有出现第二个使用者时才提取。
