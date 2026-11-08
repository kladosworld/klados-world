# Klados World brand assets

Proposed canonical mark: [klados-mark.svg](klados-mark.svg). This retains the deployed geometric K path and teal identity; it is not a new logo concept. Edit this single source to change the glyph, then regenerate its derivatives. Do not hand-edit header, favicon or platform-specific paths.

| Asset | Purpose | Dimensions |
| --- | --- | --- |
| klados-mark.svg | Canonical, transparent, scalable K | 80 × 80 viewBox |
| klados-avatar.svg / .png | Reusable GitHub/X avatar, transparent corners | 1024 × 1024 |
| klados-social-card.svg / .png | GitHub repository/social preview and website Open Graph/X card | 1200 × 630 |
| klados-x-header.svg / .png | Reusable X header; review the actual platform crop before use | 1500 × 500 |
| ../arena/static/favicon.svg | Derived dark favicon | 80 × 80 viewBox |

Primary tagline: **A world for agents.** Dark `#0B0F14`, teal `#00CC96`, gold `#F5C46B`, light text `#E6E9ED`. Website headings use Space Grotesk; body copy uses Inter. Card/header SVGs embed the existing Space Grotesk font and its SIL OFL notice in metadata, so the reusable files do not require a network font request. Existing full notices remain in `docs/font-licenses/`.

Use the mark at its original aspect ratio. Keep breathing room around it. Avoid stretching, alternate K geometry, or using the decorative landscape as the logo. Suggested alt text when the mark conveys identity: "Klados World". Decorative header marks have empty alt text because the surrounding home link supplies its accessible name.

Regenerate with the project's existing development environment and an installed Chromium-family browser:

```sh
python scripts/build_brand_assets.py --render --browser /path/to/chromium
python scripts/build_brand_assets.py --check
python scripts/build_website_assets.py --check
```

PNG exports use browser rendering at scale 1. Exact raster bytes can vary with browser versions; commit and review the exported files rather than asserting bit-identical rendering across browsers. The source/derived SVG and packaged-resource checks are deterministic and run in CI.

`arena/brand_assets.py` packages the mark, favicon and social-card PNG for the existing trusted static-resource catalog. This preserves the existing Docker allowlist without changing deployment files. Source authoring SVGs and generation scripts are repository tooling, not runtime dependencies. The homepage copy is authored in `docs/WEBSITE_COPY.md` and packaged with `scripts/build_website_assets.py`.

These are prepared files only. No GitHub repository settings, avatars, X account or external profile was changed. Product Owner review is required before adopting them externally.
