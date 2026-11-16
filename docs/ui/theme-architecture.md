# Theme Architecture

状态：架构已确认；当前阶段只实现浅色主题的背景边界修复，不提前实现完整主题切换。

## 1. Qt 版本事实与兼容边界

- 项目声明 `PySide6>=6.7,<7`，当前开发环境为 PySide6/Qt 6.12.0。
- `Qt.ColorScheme`、`QStyleHints.colorScheme()` 和 `colorSchemeChanged` 从 Qt 6.5 可用，因此声明的最低版本能够读取和监听系统主题。
- `QStyleHints.setColorScheme()` / `unsetColorScheme()` 从 Qt 6.8 才可用，且只是一项平台提示。主题实现不得依赖它作为唯一真相。
- 在 Qt 6.7 上，强制 Light/Dark 只保证应用自有 QWidget；原生标题栏和原生文件对话框仍可能跟随系统。使用前通过 `hasattr` 特性检测，不按当前开发机版本假定最低版本能力。

## 2. 模块职责

### `ThemeTokens`

不可变数据对象，只包含语义颜色、尺寸和状态值。不得引用 QWidget、Store 或 Service。

### `ThemeManager(QObject)`

唯一主题协调者：

- 持有用户模式 `LIGHT | DARK | SYSTEM` 与有效变体 `LIGHT | DARK`；
- 使用 `QSettings` 读写 `ui/theme_mode`；
- 在 System 模式监听 `QGuiApplication.styleHints().colorSchemeChanged`；
- 生成并应用 QPalette、QSS 和图标主题 revision；
- 发出 `themeAboutToChange(old, new)` 与 `themeChanged(snapshot)`；
- 不持有或修改 `AppStore`、ChatService、NetworkClient 和会话状态。

### `QssBuilder`

纯函数：`ThemeTokens -> str`。负责应用自有 QWidget 的确定性外观。禁止读取系统状态或业务状态。

### `IconProvider`

从 Qt Resource 读取精选 Lucide SVG，按名称、语义色、逻辑尺寸、DPR、控件状态和 theme revision 缓存图标。主题变化清空旧 revision 缓存。

### `RichTextRenderer`

根据 tokens 生成安全的 Markdown/Mention HTML 和 QTextDocument 样式。原始消息文本与 mention 元数据始终保留在业务模型中，主题切换只重新投影显示。

## 3. 启动流程与防闪烁

1. 创建 `QApplication`。
2. 设置 organization/application 名称，立即构造 `QSettings`。
3. 在创建 `MainWindow` 前同步读取主题偏好并解析有效主题。
4. 先应用最小 QPalette fallback，再应用完整 QSS。
5. 初始化 IconProvider revision。
6. 最后创建并显示窗口。

主题偏好读取失败时使用 `SYSTEM`；系统返回 `Unknown` 时回退为 Light。不得先显示浅色窗口再异步切换。

## 4. Light / Dark / System

- Light/Dark：ThemeManager 直接选取对应 tokens；Qt 6.8+ 可选调用 `setColorScheme()` 改善原生控件匹配，但必须特性检测并允许平台忽略。
- System：读取 `styleHints().colorScheme()`，连接 `colorSchemeChanged`。由于该信号发出时旧系统 palette 可能仍生效，使用零延迟单次调度合并更新，随后重新解析 tokens 和 fallback palette。
- 模式变化仅写 QSettings、更新视觉和发信号，不重新创建 MainWindow，不清空 Store，不重连网络。

## 5. QPalette 与 QSS 边界

QSS 是应用自有组件的权威视觉层；QPalette 只作为未覆盖控件、QTextDocument 默认颜色和系统控件的 fallback。

Palette 至少映射 Window、WindowText、Base、AlternateBase、Text、Button、ButtonText、Highlight、HighlightedText、ToolTipBase、ToolTipText、PlaceholderText 和 Link。若 QSS 已声明某组件颜色，不再依赖 palette 决定它。

不得在业务组件调用 `QApplication.setPalette()`。ThemeManager 应在一次批处理中应用 palette 与 QSS，使用重入保护避免 `ApplicationPaletteChange` 造成循环。

## 6. QWidget 与滚动容器

- 页面 canvas、surface、subtle、elevated 使用语义属性或稳定对象名。
- 每个 `QScrollArea`、`viewport()` 和 `setWidget()` 内容 QWidget 必须拥有相同 `surfaceRole`。
- 需要由 QSS 绘制背景的普通 QWidget 设置 `WA_StyledBackground`。
- 禁止用透明滚动内容依赖父背景；列表确需透明时，父级和 viewport 仍必须有确定表面色。
- 原生 `QFileDialog`/`QMessageBox` 在强制主题与系统主题相反时可能不一致；当前保留原生行为，后续只有在产品明确要求完全一致时才评估非原生对话框。

## 7. 局部样式治理

- 颜色、边框、圆角、字体和交互状态不得新增到局部 `setStyleSheet()`。
- 静态业务布局仍可通过 Qt API 设置尺寸和 spacing。
- 组件状态使用 dynamic property + 全局 QSS；属性变化通过统一 `repolish(widget)` 更新。
- 主题切换只全局重建一次 QSS，不逐组件拼接样式字符串。
- CI 增加颜色字面量和局部 stylesheet allowlist；富文本生成器和 QSS builder 是允许边界。

## 8. 自绘、图标与头像

- ToggleSwitch、fallback avatar 等 QPainter 控件订阅 `themeChanged`，更新 token snapshot 后调用 `update()`。
- 用户上传头像不着色；只有 fallback 背景、首字母和 Agent 标识随主题变化。
- Lucide SVG 作为受信任资源嵌入 `.qrc`。动态着色时从资源读取 SVG bytes，将受控的 `currentColor`/stroke 替换为 token 色，再用 `QSvgRenderer` 绘制。
- 生成 pixmap 时使用 `logical_size * devicePixelRatio`，并设置 pixmap DPR；缓存键必须包含 DPR 和 theme revision。
- 不在主题切换时修改 SVG 源文件或写临时资源。

## 9. Markdown、Mention 与消息刷新

- 删除固定 `bgcolor` 和固定浅色 inline style；Markdown、代码块、链接、引用、Mention 都从 RichTextRenderer 获取颜色。
- `MessageBubble` 保留原始 message；主题改变时重建 label 的富文本投影，不修改 Store。
- `MentionTextEdit` 依据稳定 mention 元数据重新应用 QTextCharFormat，并保存/恢复 cursor 与 selection。
- 若阶段性采用消息列表重绘，必须先记录滚动锚点、当前消息 ID、编辑器文本、mention 元数据和焦点，重绘后恢复；不得调用历史同步或业务请求。

## 10. 主题传播流程

```text
QSettings / system colorSchemeChanged
              ↓
         ThemeManager
      ┌───────┼────────┐
      ↓       ↓        ↓
   QPalette  QSS   Icon revision
      └───────┼────────┘
              ↓ themeChanged(snapshot)
     self-painted widgets / rich text / avatars
```

ThemeManager 不向 AppStore 发 topic；Workspace 和 ChatPane 不因主题变化执行 bootstrap、history.sync 或 select_conversation。

## 11. 测试策略

- 单元：token 完整性、颜色对比度、模式解析、QSettings 回退、Qt 6.7 特性分支。
- 边界：暗色系统 palette 下，canvas/surface scroll area、viewport 和 content 仍渲染目标主题色。
- 组件：QPainter 控件和 IconProvider 在 revision 改变后清缓存并更新。
- 富文本：切换后代码块/Mention/AI 正文颜色更新，原始消息和 mention ID 不变。
- 集成：主题切换前后 Store 对象、当前会话、消息数量、未发送编辑内容和网络连接保持不变。
- 视觉：720×520、860×600、1180×760、≥1280 宽度；系统 Light/Dark × 应用 Light/Dark/System。
