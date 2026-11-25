# 项目结构地图

这份地图按“目录表达层次、文件表达单一职责”的方式标注当前实现。注释描述文件负责什么，不替代代码本身；标有“待拆”的文件仍是后续工作。

```text
chatroom/
├── README.md                         # 运行说明、功能、协议与课程交付索引
├── requirements.txt                  # 唯一运行时依赖：PySide6
├── shared/
│   └── protocol.py                   # 4 字节长度前缀 + JSON 信封；粘包/拆包
│
├── server/                           # 【服务端】asyncio + SQLite，仅标准库
│   ├── __main__.py                   # python -m server 启动入口
│   ├── app.py                        # ChatServer：连接、分发、广播、调度（296 行）
│   ├── session.py                    # ClientSession：单连接发送与关闭
│   ├── constants.py                  # 文本长度、Agent 成员 ID
│   ├── config.py                     # .env 配置与默认值
│   │
│   ├── handlers/                     # 【协议层】按业务域分发请求
│   │   ├── auth.py                   # 注册、登录、在线状态、资料身份
│   │   ├── profile.py                # 用户头像读写
│   │   ├── todos.py                  # 个人待办协议
│   │   ├── groups.py                 # 群组、私聊、禁言
│   │   ├── messages.py               # 消息、历史、媒体、提及
│   │   └── agents.py                 # Agent、记忆、工具权限
│   │
│   ├── storage/                      # 【数据层】SQLite 按数据域拆分
│   │   ├── base.py                   # DatabaseError、连接包装、私聊 ID
│   │   ├── schema.py                 # 建表与迁移 SQL
│   │   ├── users.py                  # 用户、身份、头像
│   │   ├── groups.py                 # 群成员、权限、禁言
│   │   ├── messages.py               # 消息、历史、搜索、已读游标
│   │   ├── agents.py                 # Agent 卡、头像、工具绑定
│   │   ├── stickers.py               # 表情包目录与发送状态
│   │   ├── memory.py                 # 长期记忆与上下文摘要
│   │   └── activity.py               # 调度、任务、工具调用、审计
│   │
│   ├── agent_runtime/                # 【Agent 运行时】
│   │   ├── runtime.py                # 运行时状态与回调类型
│   │   ├── providers.py              # 模型/供应商选择与健康检查
│   │   ├── context.py                # 上下文加载、记忆、压缩
│   │   ├── generation.py             # 生成、分段、联网搜索
│   │   ├── tools.py                  # 工具校验、执行、待办循环（957 行，待拆）
│   │   └── errors.py                 # 需要用户确认的写入异常
│   │
│   ├── agent.py                      # AgentService 组合根（674 行）
│   ├── agent_time.py                 # 时间窗口解析
│   ├── agent_cards.py                # 摘要/待办卡片解析（1,542 行，待拆）
│   ├── agent_config.py               # 角色卡、记忆配置校验
│   ├── database.py                   # Database 组合根（264 行）
│   ├── media.py                      # 图片签名、尺寸、头像校验
│   ├── model_provider.py             # OpenAI / Ollama / Gemini 适配
│   ├── provider_presets.py           # 供应商预设
│   ├── search_provider.py            # Web 搜索适配
│   ├── stickers.py                   # 表情包匹配
│   ├── todos.py                      # 待办服务
│   ├── tools.py                      # 工具目录与参数 schema
│   └── admin/                        # 管理台（只监听 loopback）
│       ├── auth.py                   # 管理员登录与 Cookie
│       ├── config_store.py           # 模型/搜索配置快照
│       ├── http.py                   # HTTP 路由与 CSRF
│       └── service.py                # 管理业务逻辑
│
├── client/                           # 【客户端】PySide6 V2
│   ├── __main__.py                   # python -m client
│   ├── models.py                     # 数据类型与 AppState
│   ├── network.py                    # 后台线程 TCP 与自动重连
│   ├── service.py                    # 协议命令门面
│   ├── store.py                      # 事件投影与 topic 通知
│   ├── drafts.py                     # 会话草稿持久化
│   ├── profile.py                    # 用户资料投影
│   └── v2/
│       ├── application.py            # 兼容导出层（66 行）
│       ├── shell/
│       │   ├── workspace.py          # 导航栏与页面切换
│       │   └── main_window.py        # 主窗口、托盘、入口
│       ├── pages/
│       │   ├── auth_page.py          # 登录/注册
│       │   ├── chat_page.py          # 会话列表、消息区、输入区（2,161 行）
│       │   ├── contacts_page.py      # 通讯录
│       │   ├── ai_center_page.py     # AI 任务中心
│       │   ├── settings_page.py      # 设置
│       │   ├── admin_page.py         # 客户端管理页
│       │   └── agent_manager.py      # Agent 管理与记忆
│       ├── widgets/                  # 通用控件包（9 个类，7 个模块）
│       ├── agent_cards.py            # 摘要/待办卡片（1,718 行，待拆）
│       ├── personal_todo.py          # 待办页与控件（1,144 行，待拆）
│       ├── details.py                # 会话详情面板
│       ├── profile_widgets.py        # 用户资料弹层
│       ├── stickers.py               # 表情包面板
│       ├── theme.py                  # 语义 token、QSS、主题切换
│       ├── icons.py                  # Lucide 图标封装
│       ├── time_text.py              # 时间与日期格式化
│       ├── ui_helpers.py             # 消息预览与分组纯函数
│       ├── ui_constants.py           # 布局与上传限制
│       └── resources/                # 图标与品牌资源
│
├── tests/                            # 235 项 unittest（平铺，待按域分组）
├── docs/
│   ├── architecture/                 # 本文与结构审计
│   ├── course/                       # 课程交付说明、设计、演示、测试
│   ├── ui/                           # 设计系统、主题、组件规范、roadmap
│   └── brand/                        # 品牌素材
├── ollama/                           # Modelfile 与本地模型配置
├── packaging/                        # Windows PyInstaller 入口
└── data/                             # 运行时数据库与上传（不提交）
```

## 为什么这张图看起来清晰

1. **每个文件只写一句职责。** 读者不需要打开文件就知道它负责什么。
2. **目录名表达层次。** `handlers`、`storage`、`agent_runtime` 分别对应协议、数据和运行时。
3. **文件足够小。** 大多数文件能在一屏到两屏内读完，修改时知道该去哪里。
4. **地图和代码同步。** 结构变化后更新地图，读者看到的是当前状态。

## 当前仍需收敛的热区

| 文件 | 行数 | 建议拆分 |
| --- | ---: | --- |
| `client/v2/pages/chat_page.py` | 2,161 | 会话列表、消息区、输入区、媒体预览 |
| `client/v2/agent_cards.py` | 1,718 | 格式化、卡片基类、SummaryCard、TodoCard |
| `server/agent_cards.py` | 1,542 | 摘要解析、待办解析、Markdown 渲染 |
| `client/v2/widgets/` | 1,272 | 表面、头像、文本编辑、消息、会话卡片 |
| `client/v2/personal_todo.py` | 1,144 | 待办页面、选择控件、状态投影 |
| `server/agent_runtime/tools.py` | 957 | 工具校验、工具执行、待办循环 |

这些是当前“看起来还不够整齐”的主要来源。目录已经分层，剩下的是文件内部的职责继续收敛。
