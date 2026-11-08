"""Package reviewed website copy without changing Docker inputs."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT))
    from arena.website import extract_home_copy

    copy = extract_home_copy()
    outputs = {
        "arena/static/home-copy.json": json.dumps(copy, ensure_ascii=False, indent=2) + "\n",
        "arena/website_assets.py": (
            "# Generated from docs/WEBSITE_COPY.md by scripts/build_website_assets.py.\n"
            "from arena.brand_assets import FAVICON as FAVICON\n\n"
            + "HOME_COPY = "
            + repr(copy)
            + "\n"
        ),
    }
    for filename, content in outputs.items():
        path = ROOT / filename
        if args.check:
            if path.read_text(encoding="utf-8") != content:
                raise SystemExit("Stale website copy: " + filename)
        else:
            path.write_text(content, encoding="utf-8", newline="\n")
    print("PASS: website copy " + ("verified" if args.check else "packaged"))


if __name__ == "__main__":
    main()
