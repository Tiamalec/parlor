# Parlor — Logo 使用规范（一页纸）

> 品牌已更名为 **Parlor**。符号与图标继续沿用；字标与组合已重制为 Parlor，`ai-agent-*` 文件名保留作为历史兼容。

## 1. 标志

- **概念**：一间房，一个 Agent 席位。外层是共享房间与对话尾巴，内层实心气泡是 Agent 成员。
- **版本**：横版组合、堆叠组合、符号、字标；每个版本都有彩色、反白、单色。
- **母版**：`ai-agent-*.svg`；网页/桌面图标在 `dist/`；演示板在 `presentation.html` 与 `slides/`。

## 2. 留白

以 **Agent 内层气泡的高度** 为 1X（符号坐标约 56/256），标志四周至少留 1X。留白随标志缩放，不使用固定像素。

## 3. 最小尺寸

| 版本 | 屏幕 | 印刷 |
|---|---:|---:|
| 横版组合 | 120px 宽 | 30mm 宽 |
| 堆叠组合 | 80px 宽 | 22mm 宽 |
| 符号 | 24px（16px favicon 用简化裁切） | 6mm |
| 字标 | 96px 宽 | 25mm 宽 |

## 4. 颜色

| 名称 | HEX | RGB | CMYK | Pantone（近似） | 用途 |
|---|---|---|---|---|---|
| Ink | `#172033` | 23 32 51 | 55 38 0 80 | Black 6 C | 房间、字标 |
| Agent Purple | `#6B4FD3` | 107 79 211 | 49 63 0 17 | 2665 C | Agent 席位 |
| Light Agent | `#B59CFF` | 181 156 255 | 29 39 0 0 | 2645 C | 深底反白时的席位 |
| White | `#FFFFFF` | 255 255 255 | 0 0 0 0 | — | 深底反白的房间/字标 |

**允许的组合**：

- 彩色版放在白色或 `#F5F7FA` 上。
- 反白版放在 Ink `#172033` 或深色画布 `#0F141C` 上。
- 单色 Ink 放白底；单色 White 放 Agent Purple 上。
- App 图标：白色符号放在 Agent Purple 圆角方块上。
- 登录页 46px 品牌块：浅色用 `logo-light`（彩色），深色用 `logo-dark`（反白）。

## 5. 字体

- 标志字标：Avenir Next Demi Bold 转曲，做收紧密排；文件内不含 `<text>`。
- 产品界面：继续使用 `docs/ui/design-system.md` 规定的系统 UI 字体，不新增捆绑字体。
- **授权说明**：Avenir Next 是 macOS 系统字体；本课程项目使用其轮廓。若商用分发，请确认字体许可，或换成 OFL 字体（如 Inter / Manrope）后重新转曲。

## 6. 不要

- 不要拉伸、压扁、旋转或倾斜标志。
- 不要改用调色板之外的颜色，不要加渐变、阴影、描边或发光。
- 不要重新排列或单独缩放组合内部的元素。
- 不要把彩色版直接放在深色或复杂照片上；用反白版或加容器。
- 不要用文字工具重新键入字标；使用已转曲的 SVG。字标读作 **Parlor**。

## 7. 交付文件

- 符号：`ai-agent-symbol-color.svg` / `-reversed.svg` / `-small.svg` / `-small-reversed.svg`
- 组合：`ai-agent-horizontal-*.svg`、`ai-agent-stacked-*.svg`、`ai-agent-wordmark*.svg`
- 单色与方形：`dist/ai-agent-black.svg`、`-white.svg`、`-mono-172033.svg`、`-mono-6b4fd3.svg`、`-square.svg`
- Web/桌面：`dist/ai-agent-app-icon.svg`、`dist/ai-agent-app-icon-small.svg`、`dist/ai-agent-app.ico`、`dist/macos/AI-Agent.icns`、`dist/favicon.ico`、`dist/favicon.svg`、`dist/apple-touch-icon.png`、`dist/icon-192.png`、`dist/icon-512.png`、`dist/maskable-512.png`、`dist/site.webmanifest`、`dist/head-snippet.html`
- 演示：`presentation.html`、`slides/slide-01.png` … `slide-05.png`

## 8. 维护

项目维护者负责本目录。修改标志几何时，重新跑 `svg_audit.py`、`preview_sheet.py` 与 `export_variants.py`，并更新本页与演示板。
