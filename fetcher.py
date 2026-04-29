import time
from pathlib import Path
import requests
from bs4 import BeautifulSoup
import pandas as pd
from typing import Optional, Dict
from nba_api.stats.endpoints import (
    leaguedashplayerstats,
    leaguedashplayerbiostats,
    playerestimatedmetrics,
)


# ── Salary sources ───────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent
SALARY_CSV = BASE_DIR / "salaries_2026.csv"


def load_salary_data_from_csv(csv_path: Path = SALARY_CSV) -> Dict[str, float]:
    """
    Load salary data from a local CSV.
    Expected columns: player_name,salary
    Returns a dict keyed by lowercase player name -> annual salary (USD).
    """
    salary_map: Dict[str, float] = {}

    if not csv_path.exists():
        return salary_map

    try:
        salary_df = pd.read_csv(csv_path)
        required = {"player_name", "salary"}
        if not required.issubset(salary_df.columns):
            print(f"    ! Salary CSV missing required columns: {sorted(required)}")
            return salary_map

        salary_df = salary_df.dropna(subset=["player_name", "salary"]).copy()
        salary_df["player_name"] = salary_df["player_name"].astype(str).str.strip()
        salary_df["salary"] = pd.to_numeric(salary_df["salary"], errors="coerce")
        salary_df = salary_df.dropna(subset=["salary"])

        salary_map = {
            row["player_name"].lower(): float(row["salary"])
            for _, row in salary_df.iterrows()
            if row["player_name"]
        }
        print(f"    ✓ Loaded {len(salary_map)} salaries from {csv_path.name}")
    except Exception as e:
        print(f"    ✗ Salary CSV load failed: {e}")

    return salary_map


def fetch_salary_data_from_hoopshype(season: int = 2024) -> Dict[str, float]:
    """
    Scrape current-season salary data from HoopsHype.
    Returns a dict keyed by lowercase player name → annual salary (USD).
    """
    url = "https://hoopshype.com/salaries/players/"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; nba-analyzer/1.0)"}
    salary_map: Dict[str, float] = {}

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # HoopsHype is now a Next.js app: the visible HTML table may not contain
        # salary data. Prefer the embedded `__NEXT_DATA__` payload when present.
        next_data = soup.select_one('script#__NEXT_DATA__[type="application/json"]')
        if next_data and next_data.string:
            import json

            payload = json.loads(next_data.string)
            queries = (
                payload.get("props", {})
                .get("pageProps", {})
                .get("dehydratedState", {})
                .get("queries", [])
            )
            contracts = None
            for q in queries:
                pages = q.get("state", {}).get("data", {}).get("pages")
                if not pages:
                    continue
                maybe = pages[0]
                c = maybe.get("contracts", {}).get("contracts")
                if isinstance(c, list):
                    contracts = c
                    break

            if contracts:
                for c in contracts:
                    name = c.get("playerName")
                    seasons = c.get("seasons") or []
                    match = next((s for s in seasons if s.get("season") == season), None)
                    if name and match and isinstance(match.get("salary"), (int, float)):
                        salary_map[name.lower()] = float(match["salary"])
                return salary_map

        # Fallback for older HTML structure (may be empty if table is JS-rendered)
        for row in soup.select("table tbody tr"):
            cols = row.select("td")
            if len(cols) < 3:
                continue
            name = cols[1].get_text(strip=True)
            raw = cols[2].get_text(strip=True).replace("$", "").replace(",", "")
            if not name or not raw:
                continue
            try:
                salary_map[name.lower()] = float(raw)
            except ValueError:
                continue
    except Exception as e:
        print(f"    ✗ Salary scrape failed: {e}")

    return salary_map


def _match_salary(name: str, salary_map: Dict[str, float]) -> Optional[float]:
    key = name.lower().strip()
    if key in salary_map:
        return salary_map[key]
    # try "Firstname Lastname" → "Lastname Firstname"
    parts = key.split()
    if len(parts) == 2:
        alt = f"{parts[1]} {parts[0]}"
        if alt in salary_map:
            return salary_map[alt]
    return None


# ── NBA.com stat pulls ────────────────────────────────────────────────────────

def _pull(endpoint_cls, season_str: str, delay: float = 0.7, **kwargs) -> Optional[pd.DataFrame]:
    time.sleep(delay)
    try:
        return endpoint_cls(season=season_str, **kwargs).get_data_frames()[0]
    except Exception as e:
        print(f"    ✗ {endpoint_cls.__name__}: {e}")
        return None


def fetch_player_data(season: int = 2024) -> Optional[pd.DataFrame]:
    season_str = f"{season - 1}-{str(season)[-2:]}"
    print(f"\nFetching data for {season_str} season...")

    # Base per-game stats
    print("  → Base stats from NBA.com...")
    base = _pull(leaguedashplayerstats.LeagueDashPlayerStats, season_str,
                 per_mode_detailed="PerGame")
    if base is None:
        return None
    print(f"    ✓ {len(base)} players (base)")

    # Advanced stats: TS%, USG%, TOV%, on-court ratings
    print("  → Advanced stats from NBA.com...")
    adv = _pull(leaguedashplayerstats.LeagueDashPlayerStats, season_str,
                per_mode_detailed="PerGame",
                measure_type_detailed_defense="Advanced")
    if adv is None:
        return None
    print(f"    ✓ {len(adv)} players (advanced)")

    # Estimated metrics — best available proxy for BPM / VORP on NBA.com
    print("  → Estimated +/- metrics (BPM proxy)...")
    est = _pull(playerestimatedmetrics.PlayerEstimatedMetrics, season_str)
    if est is not None:
        print(f"    ✓ {len(est)} players (estimated metrics)")
    else:
        print("    ! Estimated metrics unavailable — using on-court ratings as fallback")

    # Bio stats — height
    print("  → Bio stats (height)...")
    bio = _pull(leaguedashplayerbiostats.LeagueDashPlayerBioStats, season_str)
    if bio is not None:
        print(f"    ✓ {len(bio)} players (bio)")

    # Salary
    print("  → Salary data from CSV...")
    salary_map = load_salary_data_from_csv()
    if not salary_map:
        print("  → Salary CSV unavailable; falling back to HoopsHype...")
        salary_map = fetch_salary_data_from_hoopshype(season=season)
    print(f"    ✓ {len(salary_map)} salaries fetched")

    # ── Merge ────────────────────────────────────────────────────────────────

    df = base[["PLAYER_ID", "PLAYER_NAME", "GP", "TEAM_ABBREVIATION"]].copy()
    df.rename(columns={"PLAYER_NAME": "name", "GP": "games_played",
                        "TEAM_ABBREVIATION": "team"}, inplace=True)

    # Advanced columns we care about
    adv_want = {
        "PLAYER_ID": "PLAYER_ID",
        "TS_PCT": "ts_pct",
        "USG_PCT": "usage_pct",
        "TM_TOV_PCT": "tov_pct",
        "OFF_RATING": "off_rating",
        "DEF_RATING": "def_rating",
    }
    adv_sub = adv[[c for c in adv_want if c in adv.columns]].rename(columns=adv_want)
    df = df.merge(adv_sub, on="PLAYER_ID", how="left")

    # Estimated metrics (preferred for BPM / VORP proxies)
    if est is not None:
        est_want = {
            "PLAYER_ID": "PLAYER_ID",
            "E_PLUS_MINUS": "vorp",   # closest free proxy to VORP
            "E_OFF_RATING": "obpm",   # proxy for OBPM
            "E_DEF_RATING": "dbpm",   # proxy for DBPM
        }
        est_sub = est[[c for c in est_want if c in est.columns]].rename(columns=est_want)
        df = df.merge(est_sub, on="PLAYER_ID", how="left")
    else:
        # Fall back to on-court ratings from advanced pull
        df["vorp"] = df.get("off_rating", pd.Series(0, index=df.index)).fillna(0) + \
                     df.get("def_rating", pd.Series(0, index=df.index)).fillna(0)
        df["obpm"] = df.get("off_rating", pd.Series(0, index=df.index)).fillna(0)
        df["dbpm"] = df.get("def_rating", pd.Series(0, index=df.index)).fillna(0)

    # Win-shares are BBRef-only — set to 0 until API key provides them
    df["off_win_shares"] = 0.0
    df["def_win_shares"] = 0.0

    # Height
    if bio is not None:
        bio_sub = bio[["PLAYER_ID", "PLAYER_HEIGHT_INCHES"]].rename(
            columns={"PLAYER_HEIGHT_INCHES": "height_inches"})
        df = df.merge(bio_sub, on="PLAYER_ID", how="left")
        # Also grab raw height string if available
        if "PLAYER_HEIGHT" in bio.columns:
            df = df.merge(bio[["PLAYER_ID", "PLAYER_HEIGHT"]].rename(
                columns={"PLAYER_HEIGHT": "height"}), on="PLAYER_ID", how="left")
        else:
            df["height"] = df["height_inches"].apply(
                lambda x: f"{int(x)//12}-{int(x)%12}" if pd.notna(x) else None)
    else:
        df["height_inches"] = None
        df["height"] = None

    # Salary
    df["salary"] = df["name"].apply(lambda n: _match_salary(n, salary_map))

    return df
