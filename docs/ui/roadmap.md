# UI Refactor Roadmap

## 范围分层

- 主题缺陷修复：只消除系统 palette 泄漏和不可读背景。
- 主题基础设施：ThemeManager、双主题 tokens、QSS/Palette、IconProvider、RichTextRenderer。
- 视觉组件迁移：导航、聊天、通讯录、AI 任务、设置逐页迁移。
- 功能扩展：不属于 UI 重构，必须单独立项。

## 阶段记录

| 阶段 | 状态 | 交付 |
| --- | --- | --- |
| 0. UI 架构审计 | 完成 | 技术栈、主题根因、组件与依赖评估 |
| 1. 设计与主题架构冻结 | 完成 | 本目录四份设计规范 |
| 2. 工程基线与背景泄漏修复 | 完成 | Git 基线、文档、语义表面修复、主题边界测试 |
| 3. 主题基础设施 | 未开始 | Light/Dark/System、ThemeManager、QPalette、QSS builder |
| 4. 公共视觉组件 | 未开始 | Lucide、PageHeader、StatusBadge、导航等 |
| 5. 页面迁移 | 未开始 | 聊天、AI、通讯录、设置、管理页 |
| 6. 清理与拆分 | 未开始 | application.py 渐进拆分、残余硬编码清理 |

## 阶段 2：当前阶段

### 完成记录（2026-11-17）

- 在确认工作目录及父目录均无既有仓库后，于项目根建立本地 Git 基线；未配置或推送远程。
- `.env`、`data/`、`.omx/`、日志、构建产物和常见私钥格式均由忽略规则排除。
- 为应用页面根、滚动区、viewport 与内容容器增加显式 `canvas` / `surface` 角色，未改变页面布局、Signal 或业务数据流。
- 13 项 V2 UI 测试通过，其中新增 1 项主题边界测试；全量 56 项测试通过，1 项需显式启用的本地 Ollama 测试跳过。
- Qt offscreen 环境下完成浅色及模拟深色系统 palette 截图检查；未发现目标区域深灰泄漏、不可读文字或输入区割裂。
- 未在真实 macOS 深色桌面、Windows 或 Linux 桌面环境验收，这些平台保持“未验证”。

### 修改范围

- Git 与忽略规则。
- `docs/ui/` 与根 `AGENTS.md`。
- `client/v2/theme.py`、滚动区创建点和主题边界测试。

### 前置依赖

- 当前业务测试基线可通过。
- `.env`、`data/`、`.omx/` 和私钥格式确认不会进入 Git。

### 交付与完成条件

- 有可识别的本地基线提交，无远程推送。
- 设计规范可由后续会话直接引用。
- 会话列表、消息区、AI 任务区与详情滚动内容拥有显式表面角色。
- 暗色系统 palette 下目标区域仍显示预期浅色主题背景。
- 现有 UI 测试、主题边界测试和相关业务测试通过。
- 不移动页面类，不实现主题切换，不改变业务数据流。

### 回滚

- 使用阶段提交的父提交恢复完整阶段。
- 主题修复只涉及 QSS、滚动表面标记和测试，不依赖数据迁移。

## 阶段 3：主题基础设施

### 修改范围

- `ThemeTokens`、`ThemeManager`、QSS builder、QSettings、最小 QPalette fallback。
- 设置页仅增加主题模式选择，不改其他布局。

### 自动化门槛

- token 完整性与对比度测试。
- Light/Dark/System 模式解析与系统信号测试。
- 切换前后 Store、当前会话、消息和输入内容不变。

### 人工门槛

- 系统 Light/Dark × 应用三模式，无启动闪烁和混合底板。
- 原生标题栏/文件对话框若跟随系统而非强制模式，按已知平台限制记录，不宣称完全一致。

## 阶段 4：公共视觉组件

### 修改范围

- Qt Resource 与精选 Lucide SVG。
- 导航、IconButton、PageHeader、StatusBadge、SettingsSection 等。

### 完成条件

- Emoji 不再承担导航/操作图标。
- 所有新增图标在 1x/2x DPR 和两主题下清晰。
- 组件键盘焦点、tooltip、accessibleName 完整。

## 阶段 5：页面迁移

顺序固定为聊天 → AI 任务 → 通讯录 → 设置 → 客户端管理页。每个页面单独提交、单独视觉验收；不得在同一提交迁移多个复杂页面。

## 阶段 6：清理与拆分

- 清理局部 `setStyleSheet()` 与颜色字面量 allowlist。
- 按 `component-guidelines.md` 顺序拆分 `application.py`。
- 格式化管理台代码可另立任务；不接入客户端 ThemeManager。
- Legacy 客户端保持冻结。

## 全局验收矩阵

- 尺寸：720×520、860×600、1180×760、≥1280 宽屏。
- 平台：实际覆盖的平台明确记录；未运行的平台标记未验证。
- 主题：系统 Light/Dark；阶段 3 后增加应用 Light/Dark/System。
- 功能：登录、会话切换、文本/图片发送、Mention、Agent、搜索、详情、任务结果、设置与重连。
- 可访问性：正文 AA、焦点可见、状态不只依赖颜色、图标按钮有名称。
