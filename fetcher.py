import time
from pathlib import Path
import re
import unicodedata
import requests
from bs4 import BeautifulSoup, Comment
import pandas as pd
from typing import Optional, Dict, List
from nba_api.stats.endpoints import (
    leaguedashplayerstats,
    leaguedashplayerbiostats,
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


def _normalize_name(name: str) -> str:
    if not name:
        return ""
    name = unicodedata.normalize("NFKD", str(name))
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = name.lower().strip()
    name = name.replace("’", "'").replace(".", "")
    name = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", name).strip()
    name = re.sub(r"[^a-z'\s-]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _name_candidates(name: str) -> List[str]:
    base = _normalize_name(name)
    if not base:
        return []
    out = {base}
    parts = base.split()
    if len(parts) == 2:
        out.add(f"{parts[1]} {parts[0]}")
    return [c for c in out if c]


def _match_by_name(name: str, value_map: Dict[str, float]) -> Optional[float]:
    for key in _name_candidates(name):
        if key in value_map:
            return value_map[key]
    return None


def _match_salary(name: str, salary_map: Dict[str, float]) -> Optional[float]:
    return _match_by_name(name, salary_map)


def fetch_win_shares_data(season: int = 2024) -> Dict[str, Dict[str, float]]:
    """
    Scrape Basketball-Reference advanced table for PER, OWS, and DWS.
    Returns: {normalized_player_name: {"per": x, "ows": y, "dws": z}}
    """
    url = f"https://www.basketball-reference.com/leagues/NBA_{season}_advanced.html"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; nba-analyzer/1.0)"}
    ws_map: Dict[str, Dict[str, float]] = {}

    try:
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        table = soup.select_one("table#advanced, table#advanced_stats")
        if table is None:
            # Basketball-Reference often nests full tables inside HTML comments.
            for node in soup.find_all(string=lambda t: isinstance(t, Comment)):
                if "advanced_stats" not in node and 'id="advanced"' not in node:
                    continue
                comment_soup = BeautifulSoup(str(node), "html.parser")
                table = comment_soup.select_one("table#advanced, table#advanced_stats")
                if table is not None:
                    break
        if table is None:
            print("    ✗ Basketball-Reference advanced table not found")
            return ws_map

        for row in table.select("tbody tr"):
            if "thead" in (row.get("class") or []):
                continue
            player_tag = row.select_one('td[data-stat="player"], td[data-stat="name_display"]')
            if not player_tag:
                continue
            player = player_tag.get_text(strip=True)
            key = _normalize_name(player)
            if not key:
                continue

            team = row.select_one('td[data-stat="team_id"], td[data-stat="team_name_abbr"]')
            team_id = team.get_text(strip=True) if team else ""

            def parse_stat(stat_name: str) -> Optional[float]:
                cell = row.select_one(f'td[data-stat="{stat_name}"]')
                if not cell:
                    return None
                raw = cell.get_text(strip=True)
                if raw == "":
                    return None
                try:
                    return float(raw)
                except ValueError:
                    return None

            per = parse_stat("per")
            ows = parse_stat("ows")
            dws = parse_stat("dws")
            if per is None or ows is None or dws is None:
                continue

            # Prefer the TOTAL row for traded players.
            if key not in ws_map or team_id == "TOT":
                ws_map[key] = {"per": per, "ows": ows, "dws": dws}

    except Exception as e:
        print(f"    ✗ Basketball-Reference scrape failed: {e}")

    return ws_map


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

    # Advanced stats: TS%, PER/PIE, usage/turnovers
    print("  → Advanced stats from NBA.com...")
    adv = _pull(leaguedashplayerstats.LeagueDashPlayerStats, season_str,
                per_mode_detailed="PerGame",
                measure_type_detailed_defense="Advanced")
    if adv is None:
        return None
    print(f"    ✓ {len(adv)} players (advanced)")

    # Bio stats — height
    print("  → Bio stats (height)...")
    bio = _pull(leaguedashplayerbiostats.LeagueDashPlayerBioStats, season_str)
    if bio is not None:
        print(f"    ✓ {len(bio)} players (bio)")

    # Basketball-Reference advanced totals
    print("  → Basketball-Reference advanced metrics (PER, OWS, DWS)...")
    ws_map = fetch_win_shares_data(season=season)
    print(f"    ✓ {len(ws_map)} players with BBRef advanced metrics")

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
    adv_cols = ["PLAYER_ID", "TS_PCT", "USG_PCT", "TM_TOV_PCT"]
    adv_sub = adv[[c for c in adv_cols if c in adv.columns]].copy()
    adv_sub.rename(columns={
        "TS_PCT": "ts_pct",
        "USG_PCT": "usage_pct",
        "TM_TOV_PCT": "tov_pct",
    }, inplace=True)

    # NBA.com does not always expose classic PER directly. Prefer it when present,
    # otherwise use PIE as the closest free proxy and label it as `per`.
    if "PLAYER_EFFICIENCY_RATING" in adv.columns:
        adv_sub["per"] = pd.to_numeric(adv["PLAYER_EFFICIENCY_RATING"], errors="coerce")
    elif "PIE" in adv.columns:
        adv_sub["per"] = pd.to_numeric(adv["PIE"], errors="coerce")
    else:
        adv_sub["per"] = 15.0
    df = df.merge(adv_sub, on="PLAYER_ID", how="left")

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

    # BBRef advanced stats merge by player name
    df["per"] = pd.to_numeric(df["per"], errors="coerce")
    ows_map = {k: v["ows"] for k, v in ws_map.items()}
    dws_map = {k: v["dws"] for k, v in ws_map.items()}
    bbref_per_map = {k: v["per"] for k, v in ws_map.items()}

    df["off_win_shares"] = df["name"].apply(lambda n: _match_by_name(n, ows_map))
    df["def_win_shares"] = df["name"].apply(lambda n: _match_by_name(n, dws_map))

    # If NBA PER is unavailable, backfill from BBRef PER.
    df["per"] = df["per"].fillna(df["name"].apply(lambda n: _match_by_name(n, bbref_per_map)))
    df["per"] = df["per"].fillna(15.0)
    df["off_win_shares"] = pd.to_numeric(df["off_win_shares"], errors="coerce").fillna(0.0)
    df["def_win_shares"] = pd.to_numeric(df["def_win_shares"], errors="coerce").fillna(0.0)

    return df
