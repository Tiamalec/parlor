# Parlor — Logo Kit

> 品牌已更名为 **Parlor**。符号与图标继续沿用；字标与组合已重制为 Parlor，`ai-agent-*` 文件名保留作为历史兼容。

方向：**B — Agent in the Room**。一间房，一个 Agent 席位。

## 主文件

| 文件 | 用途 |
|---|---|
| `ai-agent-symbol-color.svg` | 彩色符号母版（Ink 房间 + Agent 紫席位） |
| `ai-agent-symbol-reversed.svg` | 深底反白符号（White 房间 + Light Agent 席位） |
| `ai-agent-symbol-small.svg` | 16px favicon 用的简化裁切（内层无小尾巴） |
| `ai-agent-symbol-small-reversed.svg` | 简化裁切的深底版 |
| `ai-agent-horizontal-color.svg` | 横版组合，白底/浅底 |
| `ai-agent-horizontal-reversed.svg` | 横版组合，深底 |
| `ai-agent-stacked-color.svg` | 堆叠组合，白底/浅底 |
| `ai-agent-stacked-reversed.svg` | 堆叠组合，深底 |
| `ai-agent-wordmark.svg` | 字标单独使用 |
| `ai-agent-wordmark-reversed.svg` | 字标反白 |

## 图标与网页

`dist/` 包含：

- `ai-agent-black.svg`、`ai-agent-white.svg`、`ai-agent-mono-172033.svg`、`ai-agent-mono-6b4fd3.svg`
- `ai-agent-square.svg`、`ai-agent-favicon.svg`、`ai-agent-app-icon.svg`、`ai-agent-app-icon-small.svg`
- `favicon.ico`、`favicon.svg`、`favicon-16/32/48.png`
- `apple-touch-icon.png`、`icon-192.png`、`icon-512.png`、`maskable-512.png`
- `ai-agent-app.ico`（Windows 16–256px）、`macos/AI-Agent.icns`（macOS）
- `site.webmanifest`、`head-snippet.html`

## 演示与规范

- `presentation.html`：最终身份演示板（5 页，含 README、App 图标、网站、终端、贴纸、社交资料 mockup）
- `slides/slide-01.png` … `slide-05.png`：逐页 PNG
- `brand-guidelines.md`：一页纸使用规范
- `brand-guidelines.html` / `brand-guidelines.png`：可视化一页纸
- `previews/auth-light.png` / `auth-dark.png`：V2 登录页品牌块实机截图

## 颜色（历史品牌套件）

- Ink `#172033`：房间、字标
- Agent Purple `#6B4FD3`：Agent 席位
- Light Agent `#B59CFF`：深底反白时的席位
- White `#FFFFFF`：深底反白的房间与字标

2026-11-25 起，V2「会客厅」UI 基线在运行时使用主操作森林绿 `#2E5E4E`、Agent 暖紫褐 `#7A4B5A`（深色主题 `#8FBFA9` / `#D3A7B4`）；标志几何不变，运行时资源随主题重新着色。界面色值以 `docs/ui/design-system.md` 为准，本页其余色值保留历史品牌记录。

## 字标

字标由 **Avenir Next Demi Bold** 转曲，已做收紧密排。文件内没有 `<text>`。Avenir Next 是 macOS 系统字体；正式商用分发前请确认许可，或换成 OFL 字体后重新转曲。

## 验证

- `ai-agent-symbol-color.svg` / `ai-agent-symbol-small.svg`：`svg_audit.py` 100/100。
- 横版、堆叠：99/100；仅提示转曲字标的路径锚点较多，属于正常情况。
- 字标单独文件：100/100。
- 16/32/48px favicon 已做像素放大检查；登录页浅色/深色品牌块已实际截图检查。
- 完整测试：149 个通过，1 个跳过（`tests/`，`QT_QPA_PLATFORM=offscreen`）。

## 重新导出

```bash
python3 <skill>/scripts/export_variants.py ai-agent-symbol-color.svg \
  --out-dir dist --name ai-agent --title "Parlor" \
  --mono "#172033" --mono "#6B4FD3" \
  --icon-bg "#6B4FD3" --icon-fg "#FFFFFF" --icon-scale 0.62 \
  --favicon-source ai-agent-symbol-small.svg --web-icons --png 256 512 1024
```

正式商用前仍需做商标与近似标志检索；本套件不提供法律 clearance。
