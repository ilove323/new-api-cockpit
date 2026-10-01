"""Explicit catalog synchronization; --full rediscovers deleted log channel IDs."""

import argparse
from new_api_statistics import balance, scopes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--full",
        action="store_true",
        help="Scan historical logs once; never rewrite charges.",
    )
    args = parser.parse_args()
    balance.initialize()
    rows = scopes.refresh_scopes(full_discovery=args.full)
    print(f"Catalog synchronization complete: {len(rows)} channel entries.")


if __name__ == "__main__":
    main()
