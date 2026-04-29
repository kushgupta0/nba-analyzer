"""
NBA Contract Value Analyzer — standalone script.
Run:  python nba_analyzer.py
      python nba_analyzer.py --season 2024
"""

import argparse
from fetcher import fetch_player_data
from analyzer import run_pipeline

print("NBA Contract Value Analyzer")
print("=" * 50)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", type=int, default=2024,
                        help="Season end year (e.g. 2024 = 2023-24)")
    args = parser.parse_args()

    df = fetch_player_data(season=args.season)
    if df is None:
        print("\n✗ Failed to fetch data. Check your internet connection and try again.")
        raise SystemExit(1)

    run_pipeline(df)
