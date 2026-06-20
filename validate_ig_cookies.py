import argparse
import os
import sys

from ig_collection import collection_shortcodes


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate that configured Instagram cookies can read a saved collection."
    )
    parser.add_argument(
        "--collection-url",
        default=os.environ.get("IG_COLLECTION_URL"),
        help="Instagram saved collection URL, or IG_COLLECTION_URL.",
    )
    parser.add_argument("--max-scrolls", type=int, default=5)
    args = parser.parse_args()

    if not args.collection_url:
        print("[ig] missing collection URL. Set IG_COLLECTION_URL or pass --collection-url.")
        return 2

    try:
        codes = collection_shortcodes(args.collection_url, max_scrolls=args.max_scrolls)
    except Exception as exc:
        print(f"[ig] validation failed: {type(exc).__name__}: {exc}")
        return 3

    print(f"[ig] collection yielded {len(codes)} shortcodes")
    for code in sorted(codes):
        print(code)
    return 0 if codes else 4


if __name__ == "__main__":
    sys.exit(main())
