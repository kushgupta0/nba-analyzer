"""
Analysis pipeline: clean → normalize → score → rank → verdict → output.
Mirrors the build guide exactly, Steps 4-9.
"""

import pandas as pd
import numpy as np
import json
from datetime import datetime
from typing import List, Dict, Any


# ── Step 4: Clean ────────────────────────────────────────────────────────────

def assign_salary_tier(salary, composite_score):
    if pd.isna(salary) or salary is None:
        return None

    # Performance-aware overrides so elite/strong players are grouped with peers
    # closer to their impact, not just their contract value.
    if composite_score is not None and not pd.isna(composite_score):
        if composite_score >= 70:
            return "Star" if salary < 25_000_000 else "Max"
        if composite_score >= 50:
            if salary < 15_000_000:
                return "Starter"
            elif salary < 25_000_000:
                return "Star"
            else:
                return "Max"

    # Original salary-only fallback logic
    if salary < 5_000_000:
        return "Minimum"
    elif salary < 15_000_000:
        return "Role Player"
    elif salary < 25_000_000:
        return "Starter"
    elif salary < 35_000_000:
        return "Star"
    else:
        return "Max"


def assign_salary_tiers(df: pd.DataFrame) -> pd.DataFrame:
    print("\nAssigning salary tiers (salary + performance)...")
    df["salary_tier"] = df.apply(
        lambda row: assign_salary_tier(row.get("salary"), row.get("composite_score")),
        axis=1,
    )
    before = len(df)
    df = df[df["salary_tier"].notna()]
    print(f"  → Removed {before - len(df)} players with no salary tier")
    print("  ✓ Salary tiers assigned")
    return df

def clean_data(df: pd.DataFrame) -> pd.DataFrame:
    print("\nCleaning data...")

    # Salary: already numeric from fetcher, but parse strings just in case
    def parse_salary(sal):
        if pd.isna(sal):
            return None
        if isinstance(sal, (int, float)):
            return float(sal)
        try:
            return float(str(sal).replace("$", "").replace(",", ""))
        except ValueError:
            return None

    df["salary"] = df["salary"].apply(parse_salary)

    # Height group from inches
    def assign_height_group(inches):
        if pd.isna(inches):
            return "Unknown"
        if inches < 75:     # under 6'3"
            return "Guard"
        elif inches < 79:   # 6'3"–6'6"
            return "Wing"
        elif inches < 82:   # 6'7"–6'9"
            return "Forward"
        else:               # 6'10"+
            return "Big"

    df["height_group"] = df["height_inches"].apply(assign_height_group)

    before = len(df)
    df = df[df["games_played"] >= 20]
    print(f"  → Removed {before - len(df)} players with <20 games")

    before = len(df)
    df = df[df["salary"].notna()]
    print(f"  → Removed {before - len(df)} players with no salary data")

    before = len(df)
    df = df[df["height_group"] != "Unknown"]
    print(f"  → Removed {before - len(df)} players with no height data")

    print(f"  ✓ {len(df)} players remain after cleaning")
    return df.reset_index(drop=True)


# ── Step 5: Normalize ────────────────────────────────────────────────────────

def _minmax(series: pd.Series) -> pd.Series:
    lo, hi = series.min(), series.max()
    if hi == lo:
        return pd.Series(50.0, index=series.index)
    return ((series - lo) / (hi - lo)) * 100


def normalize_stats(df: pd.DataFrame) -> pd.DataFrame:
    print("\nNormalizing stats to 0-100 scale...")

    for stat in ["vorp", "obpm", "dbpm", "ts_pct", "off_win_shares", "def_win_shares"]:
        if stat in df.columns:
            df[f"{stat}_norm"] = _minmax(df[stat].fillna(0))
        else:
            df[f"{stat}_norm"] = 50.0

    df["availability_norm"] = (df["games_played"] / 82) * 100

    # TOV% inverted — lower is better
    if "tov_pct" in df.columns:
        df["tov_inv_norm"] = 100 - _minmax(df["tov_pct"].fillna(df["tov_pct"].mean()))
    else:
        df["tov_inv_norm"] = 50.0

    print("  ✓ All stats normalized")
    return df


# ── Step 6: Composite score ──────────────────────────────────────────────────

WEIGHTS = {
    "vorp_norm":         0.30,
    "dbpm_norm":         0.20,
    "obpm_norm":         0.15,
    "ts_pct_norm":       0.15,
    "availability_norm": 0.12,
    "tov_inv_norm":      0.08,
}


def calculate_scores(df: pd.DataFrame) -> pd.DataFrame:
    print("\nCalculating composite scores...")

    if df.empty:
        print("  ! No players to score (empty dataset)")
        return df

    df["composite_score"] = sum(
        df[stat] * weight for stat, weight in WEIGHTS.items()
    ).round(1)

    df["salary_millions"] = pd.to_numeric(df["salary"], errors="coerce") / 1_000_000
    df["value_per_dollar"] = (df["composite_score"] / df["salary_millions"]).round(2)

    print(f"  ✓ Scores calculated")
    print(f"    - Highest value: {df['value_per_dollar'].max():.2f}")
    print(f"    - Lowest value:  {df['value_per_dollar'].min():.2f}")
    return df


# ── Step 7: Peer rankings ────────────────────────────────────────────────────

def calculate_peer_rankings(df: pd.DataFrame) -> pd.DataFrame:
    print("\nCalculating peer rankings...")
    df["peer_rank"] = (
        df.groupby(["height_group", "salary_tier"])["value_per_dollar"]
        .rank(ascending=False, method="min")
        .astype(int)
    )
    print("  ✓ Peer rankings calculated")
    return df


# ── Step 8: Verdicts ─────────────────────────────────────────────────────────

def assign_verdicts(df: pd.DataFrame) -> pd.DataFrame:
    print("\nAssigning verdicts...")

    def get_verdict_and_explanation(row):
        peers = df[
            (df["height_group"] == row["height_group"]) &
            (df["salary_tier"]  == row["salary_tier"])
        ]
        peer_count = len(peers)
        percentile = row["peer_rank"] / peer_count

        base = (
            f"Rank {int(row['peer_rank'])} of {peer_count} in "
            f"{row['height_group']} / {row['salary_tier']} tier peers."
        )

        if percentile <= 0.30:
            return pd.Series([
                "UNDERPAID",
                f"{base} Top value in this peer group, so contract projects as UNDERPAID.",
            ])
        elif percentile >= 0.70:
            return pd.Series([
                "OVERPAID",
                f"{base} Lower value-per-dollar versus comparable peers, so marked OVERPAID.",
            ])
        return pd.Series([
            "FAIR",
            f"{base} Mid-pack value-per-dollar versus comparable peers, so marked FAIR.",
        ])

    df[["verdict", "verdict_explanation"]] = df.apply(get_verdict_and_explanation, axis=1)

    counts = df["verdict"].value_counts()
    print(f"  ✓ Verdicts assigned")
    print(f"    - UNDERPAID: {counts.get('UNDERPAID', 0)}")
    print(f"    - FAIR:      {counts.get('FAIR', 0)}")
    print(f"    - OVERPAID:  {counts.get('OVERPAID', 0)}")
    return df


# ── Step 9: Output ───────────────────────────────────────────────────────────

COL_MAP = {
    "name":            "name",
    "team":            "team",
    "height":          "height",
    "height_group":    "hg",
    "salary_millions": "salary",
    "salary_tier":     "tier",
    "games_played":    "games",
    "vorp":            "vorp",
    "obpm":            "obpm",
    "dbpm":            "dbpm",
    "off_win_shares":  "ows",
    "def_win_shares":  "dws",
    "ts_pct":          "ts",
    "tov_pct":         "tov",
    "composite_score": "score",
    "value_per_dollar":"vpd",
    "peer_rank":       "peer_rank",
    "verdict":         "verdict",
    "verdict_explanation": "verdict_explanation",
}


def generate_output(df: pd.DataFrame, out_dir: str = ".") -> List[Dict[str, Any]]:
    print("\nGenerating output...")

    out_df = df[[c for c in COL_MAP if c in df.columns]].copy()
    out_df.rename(columns=COL_MAP, inplace=True)
    out_df.sort_values("vpd", ascending=False, inplace=True)

    records = out_df.to_dict("records")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{out_dir}/nba_contracts_{timestamp}.json"
    with open(filename, "w") as f:
        json.dump(records, f, indent=2)

    print(f"  ✓ Output saved to {filename}")
    print(f"\n✓ Analysis complete!")
    print(f"  - {len(out_df)} players analyzed")
    if len(out_df):
        print(f"  - Best value:  {out_df.iloc[0]['name']} ({out_df.iloc[0]['vpd']} vpd)")
        print(f"  - Worst value: {out_df.iloc[-1]['name']} ({out_df.iloc[-1]['vpd']} vpd)")

    return records


# ── Full pipeline ────────────────────────────────────────────────────────────

def run_pipeline(df) -> List[Dict[str, Any]]:
    df = clean_data(df)
    if df.empty:
        print("\n✗ No players left after cleaning. This usually means salary data failed to load.")
        print("  - If HoopsHype is blocked, provide a paid API key + salary endpoint,")
        print("    or swap in another salary source.")
        return []
    df = normalize_stats(df)
    df = calculate_scores(df)
    df = assign_salary_tiers(df)
    if df.empty:
        print("\n✗ No players left after salary tier assignment.")
        return []
    df = calculate_peer_rankings(df)
    df = assign_verdicts(df)
    return generate_output(df)
