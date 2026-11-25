# Project UI Development Constraints

UI 工作开始前必须阅读：

- `docs/ui/design-system.md`
- `docs/ui/theme-architecture.md`
- `docs/ui/component-guidelines.md`
- `docs/ui/roadmap.md`

视觉规范中的 token 和尺寸记录当前实现，不代表现有截图是审美模板。用户提供的视觉参考与明确偏好优先；开始页面改造前先读 `docs/ui/design-system.md` 第 0 节，并按其截图标准验收。

约束：

- V2 保持 PySide6 / Qt Widgets；不得迁移 React 或引入整体 Qt 主题库。
- 保持 `NetworkClient -> ChatService -> AppStore -> UI`、现有 Signal、Store topic 和协议接口。
- 视觉值使用语义 tokens；不得新增散落颜色、Emoji 操作图标或无必要局部 `setStyleSheet()`。
- `QScrollArea`、viewport 与内容 QWidget 必须声明一致的表面角色。
- 主题基础设施与页面大规模拆分不得在同一阶段实施。
- `application.py` 只能按组件规范渐进拆分，每次保持旧导入路径和业务行为。
- 服务端管理台不接入 V2 ThemeManager。
- 修改后运行目标测试和完整测试；未实际验证的平台不得宣称通过。
- `.env`、`data/`、`.omx/`、日志、数据库、上传内容和密钥不得提交。
- Git 提交遵循仓库 Lore Commit Protocol：意图优先，并记录 Tested / Not-tested 等有价值 trailers。
