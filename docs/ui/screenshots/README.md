# UI Screenshot Baselines

## Current baseline

`baseline-2026-11/` is the visual acceptance record for the「会客厅」design baseline. It is generated from deterministic in-memory state; the script never starts the network client.

```bash
QT_QPA_PLATFORM=offscreen python tools/ui_snapshot.py
```

Each page is captured at 720×520, 860×600, 1180×760 and 1440×900, in light and dark themes. The file name format is:

```text
<page>-<theme>-<width>x<height>.png
```

The `messages-details-*` set covers the narrow overlay details panel. The `admin-web-*` set covers the independent Web console at 1440×1000, 1024×768 and 760×900; `admin-web-login-*` covers its sign-in screen. They are rendered from the real `server/admin/static` CSS and DOM with local demo values.

## Historical screenshots

`archive-2026-11/` contains screenshots from before the warm Parlor baseline. They document previous implementation states and must not be used as visual templates.

## Review rules

- Compare the same page and theme across widths.
- Check hierarchy, spacing, alignment, state text, focus visibility and dark-mode surface leakage.
- Functional tests passing does not replace visual review.
- Windows and Linux rendering remain unverified until captured on those platforms.
