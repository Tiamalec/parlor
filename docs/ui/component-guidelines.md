# Component Guidelines

## 1. 保留、重构与新增

### 保留业务边界

- `NetworkClient -> ChatService -> AppStore -> UI` 数据流。
- `ConversationKey`、现有协议名、Signal 和 Store topic。
- Avatar/UserAvatar、MentionTextEdit、MessageBubble、UserProfileCard、ProfilePopover、ConversationDetailsPanel 的业务能力。
- 详情面板现有宽屏停靠/窄屏覆盖行为。

### 当前实现（2026-11-25 基线）

- `client/v2/components.py` 提供 `PageHeader`、`SectionHeader`、`IconButton`、`StatusBadge`、`SegmentedTabs`、`FilterBar`、`InlineNotice`、`StatTile`。
- `Workspace` 使用 236/76/64px 三档侧栏；导航顺序为消息、通讯、AI 任务、待办、管理、设置。
- `AuthPage` 使用左侧品牌面板 + 右侧表单面板；窄屏隐藏品牌区。
- `ConversationPane`、`ChatSurface`、`ContactsDirectory`、`ContactDetails`、`SettingsSidebar`、`TodoWorkspace` 使用连续表面和分隔线。
- `ConversationCard` 行高 72px；`DirectoryList` 行高 56px；任务/待办行 64px。

### 渐进重构

- `ConversationCard`：加入明确选中态、状态与 token；保持 `activated(ConversationKey)`。
- `MessageBubble`：主题化展示，不改变 recall/media signals 和消息格式。
- `AgentTaskCard`：转为紧凑行，保持 `result_requested`。
- `UserProfileCard`：统一用户与群组资料表现，不改变动作 signal。
- `EmptyState`：默认符号改为 `◇`，不依赖 Emoji；空详情落在画布上，不再包成巨大卡片。

### 允许新增的轻量组件

- `PageHeader`、`SectionHeader`、`StatTile`：页面与分组标题。
- `IconButton`：统一图标按钮的尺寸、tooltip、accessibleName 和焦点。
- `SegmentedTabs`：联系人/群聊、主题或密度等少量互斥选项。
- `StatusBadge`：连接、任务、Agent 等状态文本。
- `FilterBar`：AI 任务与列表筛选行。
- `InlineNotice`：表单反馈、加载和错误提示。
- `ConfirmActionDialog`：仅在确有危险动作时使用。

只有在两个以上页面出现相同结构或交互时才提取；不建立通用组件框架、容器 DSL 或新的状态管理层。

## 2. 组件规则

- 业务数据通过构造参数、setter 或现有 Store 投影进入组件；组件不直接发网络请求，既有例外逐步收敛但不在主题修复阶段改写。
- 样式来自 QSS、ThemeTokens 或 ThemeManager snapshot；禁止新增局部颜色字符串。
- 图标按钮提供 tooltip、accessibleName、hover、pressed、focus 和 disabled。
- 列表优先使用行与分隔线，不为每行添加独立大卡片。
- Surface 不嵌套 Surface；浮层和对话框除外。
- 所有空状态必须说明原因；只有已有业务动作时才显示 CTA。
- 危险动作与普通操作分离，必要时确认；不把退出登录做成全宽主操作。

## 3. `application.py` 渐进拆分

主题基础设施阶段只允许增加必要导入、主题装配和背景角色，不移动页面类。

视觉稳定后按以下顺序拆分，每次只移动一组类并保持公开名字与 Signal：

1. `pages/settings_page.py`：`SettingsPage`。
2. `pages/ai_center_page.py`：`AgentTaskCard`、`NewAgentTaskDialog`、`AICenterPage`。
3. `pages/contacts_page.py`：`ContactsPage`、`TextPrompt`。
4. `pages/chat_page.py`：`ConversationPane`、`ChatPane`、`DetailsScrim`。
5. `pages/admin_page.py`：`AdminPage`。
6. `workspace.py`：`Workspace`。
7. `main_window.py`：`AuthPage`、`MainWindow`、`run()` 或按最终依赖再分离 `AuthPage`。

迁移策略：

- 先从新模块导入并在旧模块 re-export，保证现有测试导入路径继续有效。
- 不在同一提交中移动类并重写布局。
- 每次移动后运行对应 UI 测试和完整测试。
- 不改变 Signal 名、参数、Service 调用、Store topic 或协议 payload。

## 4. 页面语言

- 导航：统一 Lucide 图标，选中项使用背景、图标/文字色和稳定指示；设置固定底部。
- 导航：宽屏显示文字与品牌字标，中窄屏退化为图标栏；未读徽标不遮挡会话预览。
- 聊天：会话栏和正文为连续工作区；输入区与正文用分隔线，不再用独立大卡片。
- 通讯录：目录是紧凑列表，详情区域采用统一 Profile 展示。
- AI 任务：任务类型为主标题，范围/用户/时间为 metadata，状态与结果操作右对齐。
- 设置：以 section + separator 组织，不用一个撑满页面的空白卡片。
- 登录：深绿品牌面板承担气质，表单区保持单一主操作；不再使用旧品牌名或默认 Qt 卡片。

## 5. 评审清单

- 是否复用了已存在的组件或语义 token？
- 是否新增了局部颜色、Emoji 图标或无必要固定宽高？
- 是否在列表中制造了多层卡片？
- 是否保持键盘焦点、accessibleName 和状态文本？
- 是否保持 Signal、Store、Service 和协议行为？
- 是否在系统暗色 palette 下显式声明了 QWidget/viewport/content 背景？
