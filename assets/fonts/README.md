# Fonts

Place per-script font files here. The default mapping (`core/subtitles.py`
`DEFAULT_SCRIPT_FONTS`) expects these exact filenames — all from the
Google **Noto** family (SIL Open Font License, safe to bundle/redistribute):

| Script bucket | Expected filename           | Covers languages like        | Download |
|---------------|------------------------------|-------------------------------|----------|
| latin / cyrillic / greek | `NotoSans-Bold.ttf` | en, es, fr, de, ru, el, ...    | https://fonts.google.com/noto/specimen/Noto+Sans |
| arabic        | `NotoNaskhArabic-Bold.ttf`   | ar, fa, ur                     | https://fonts.google.com/noto/specimen/Noto+Naskh+Arabic |
| hebrew        | `NotoSansHebrew-Bold.ttf`    | he                              | https://fonts.google.com/noto/specimen/Noto+Sans+Hebrew |
| devanagari    | `NotoSansDevanagari-Bold.ttf`| hi, mr, ne                     | https://fonts.google.com/noto/specimen/Noto+Sans+Devanagari |
| cjk           | `NotoSansSC-Bold.otf`        | zh, ja, ko (SC covers zh; swap for NotoSansJP/NotoSansKR if you need native JP/KR glyph shapes) | https://fonts.google.com/noto/specimen/Noto+Sans+SC |
| thai          | `NotoSansThai-Bold.ttf`      | th                              | https://fonts.google.com/noto/specimen/Noto+Sans+Thai |

You can override any of these in `config.yaml` under `fonts.script_map` to
point at your own brand fonts instead, e.g.:

```yaml
fonts:
  script_map:
    latin: "MyBrandFont-Bold.ttf"
```

If a mapped font file is missing, `subtitles.py` logs a warning and falls
back to `NotoSans-Bold.ttf` rather than crashing — but non-Latin scripts
will render as tofu boxes until the correct font is present.
