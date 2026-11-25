# 测试记录与提交清单

## 1. 自动化测试

执行命令：

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
```

最近一次完整执行（2026-11-25，macOS）：

```text
Ran 235 tests in 9.533s

OK (skipped=1)
```

- 运行解释器：Python 3.13.12
- 客户端依赖：PySide6 6.12.0
- 跳过项：需要显式启用的实时 Ollama 集成测试
- Qt offscreen 环境会输出 `propagateSizeHints()` 提示，这是测试插件噪声，不影响测试结果

结构检查：

```bash
python -m compileall -q client server shared
python -c "import client.v2.application, server.app; print('import ok')"
```

## 2. 需求覆盖矩阵

| 课程要求 | 主要测试 |
| --- | --- |
| TCP 粘连、拆包、非法长度、超大帧、非法 JSON | `tests/test_protocol.py` |
| 注册、登录、错误密码、未认证请求 | `tests/test_server_integration.py` |
| 三客户端私聊、群广播、在线状态、异常断线 | `tests/test_server_integration.py` |
| 离线游标分页与重连同步 | `tests/test_server_integration.py` |
| 群成员、禁言、撤回、越权拒绝 | `tests/test_server_integration.py` |
| 图片大小、签名、文件名与撤回后访问 | `tests/test_server_integration.py`、`tests/test_m4_stickers.py` |
| Agent 会话授权与撤回消息排除 | `tests/test_agent.py` |
| 摘要、待办提取与结构化卡片 | `tests/test_agent_cards.py`、`tests/test_agent_cards_ui.py` |
| 真实工具调用与审计 | `tests/test_agent.py`、`tests/test_personal_todos.py` |
| 个人待办隔离、版本校验、幂等导入 | `tests/test_personal_todos.py` |
| 模型提供商、离线与超时 | `tests/test_model_provider.py` |
| 管理台登录、配置、搜索与表情包 | `tests/test_admin_http.py`、`tests/test_admin_service.py`、`tests/test_admin_ui.py` |
| Web 搜索工具与权限 | `tests/test_web_search.py` |
| V2 页面、主题、导航、响应式 | `tests/test_v2_ui.py`、`tests/test_v2_theme.py` |

## 3. 人工验收记录

已在 macOS 完成：

- 三客户端注册、登录和在线状态。
- 私聊与群聊消息广播。
- 客户端退出、重连和离线消息同步。
- 群聊 @Agent 的摘要与待办任务。
- 工具调用记录展示。
- 图片、表情包、历史搜索、禁言和撤回。

尚未实机验收：

- Windows 和 Linux 的字体度量、原生标题栏、系统文件对话框。
- 真实 Ollama 实时链路的完整回归（自动化测试中默认跳过）。
- 跨公网部署的 TLS、限流和审计留存。

提交前必须在演示机器上补一次实时 Ollama 验收，并记录模型名与结果。

## 4. 提交清单

### 4.1 必须提交

- [ ] 完整源代码（不含 `.env`、`data/`、日志、数据库、上传内容）
- [ ] `README.md` 运行说明
- [ ] 本目录课程文档：`README.md`、`design.md`、`run-and-demo.md`、`test-and-submission.md`
- [ ] 15 分钟系统运行录屏
- [ ] 课堂展示 PPT，列出全部成员和具体分工
- [ ] 说明文档：设计思路、开发环境、关键代码、运行结果截图、测试过程、心得体会

### 4.2 提交前检查

- [ ] 在一台干净机器上按 `run-and-demo.md` 从零跑通
- [ ] 三个不同用户名同时在线
- [ ] 私聊与群聊各完成一次
- [ ] 断线重连和离线消息各完成一次
- [ ] @Agent 完整链路和工具调用记录可见
- [ ] 至少两个自定义功能可演示
- [ ] 完整测试通过，跳过项有说明
- [ ] 录屏和截图中没有 API Key、密码、真实聊天内容

## 5. 分工模板

| 成员 | 角色 | 具体工作 | 对应材料 |
| --- | --- | --- | --- |
| 待填写 | 组长 / 服务端 | 协议、连接管理、权限、数据持久化 | `server/`、`shared/` |
| 待填写 | 客户端 | 页面、状态投影、主题与交互 | `client/` |
| 待填写 | Agent | 提示词、工具、模型适配、审计 | `server/agent.py`、`server/tools.py` |
| 待填写 | 测试与文档 | 测试、录屏、说明文档、PPT | `tests/`、`docs/course/` |
| 待填写 | 展示 | 演示流程、现场操作、答辩 | `run-and-demo.md` |

## 6. 关键代码索引

| 主题 | 文件 |
| --- | --- |
| TCP 帧协议 | `shared/protocol.py` |
| 服务端连接与分发 | `server/app.py` |
| 协议处理器 | `server/handlers/` |
| 客户端会话 | `server/session.py` |
| 服务端共享常量 | `server/constants.py` |
| Agent 运行时 | `server/agent.py`、`server/agent_runtime/` |
| 时间窗口解析 | `server/agent_time.py` |
| 工具目录 | `server/tools.py` |
| 数据访问 | `server/database.py`、`server/storage/` |
| 图片校验 | `server/media.py` |
| 客户端网络 | `client/network.py` |
| 状态投影 | `client/store.py` |
| 页面 | `client/v2/pages/` |
| 应用外壳 | `client/v2/shell/` |
