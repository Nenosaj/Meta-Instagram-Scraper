"""
Viral Real-Estate Content Scraper
Apify (Instagram + Facebook) -> Google Sheets

- Posts with >= min_views go to the "Viral Content" tab
- Everything else goes to the "Not Qualified" tab (optional)
- Keeps scraping in rounds until every (source, keyword) is exhausted

Recover modes (no new scrape, reads data already in Apify):
    python main.py --recover-all                      # ALL past IG/FB runs
    python main.py --dataset <ID> --platform Facebook # one dataset
"""
import argparse
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import gspread
import yaml
from apify_client import ApifyClient
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("scraper")

HEADERS = [
    "Platform", "Keyword", "Views", "Likes", "Comments", "Shares",
    "Author", "Caption", "URL", "Posted At", "Scraped At", "Raw Fields",
]

# Apify actors return different field names; we try each in order.
VIEW_KEYS = ["videoPlayCount", "videoViewCount", "playsCount", "playCount",
             "viewsCount", "viewCount", "views", "video_view_count", "igPlayCount"]
LIKE_KEYS = ["likesCount", "likes", "likeCount", "reactionsCount", "reactionCount", "topReactionsCount"]
COMMENT_KEYS = ["commentsCount", "comments", "commentCount"]
SHARE_KEYS = ["sharesCount", "shares", "shareCount"]
URL_KEYS = ["url", "postUrl", "permalink", "link", "topLevelUrl", "facebookUrl",
            "postLink", "shareUrl", "videoUrl", "inputUrl"]
CAPTION_KEYS = ["caption", "text", "message", "description", "title"]
DATE_KEYS = ["timestamp", "time", "date", "createdAt", "takenAtTimestamp", "publishedAt"]
AUTHOR_KEYS = ["ownerUsername", "ownerFullName", "pageName", "authorName", "username"]


# ----------------------------------------------------------------- helpers
def first(d: dict, keys: list, default=None):
    for k in keys:
        v = d.get(k)
        if v not in (None, "", []):
            return v
    return default


def to_int(v) -> int:
    """Handles 12345, '12,345', '1.2M', '850K'."""
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip().upper().replace(",", "")
    m = re.match(r"^([\d.]+)\s*([KMB])?", s)
    if not m:
        return 0
    n = float(m.group(1))
    mult = {"K": 1e3, "M": 1e6, "B": 1e9}.get(m.group(2), 1)
    return int(n * mult)


def get_author(item: dict) -> str:
    a = first(item, AUTHOR_KEYS)
    if a:
        return str(a)
    for nested in ("user", "author", "owner", "page"):
        obj = item.get(nested)
        if isinstance(obj, dict):
            a = first(obj, ["name", "username", "fullName", "title"])
            if a:
                return str(a)
    return ""


def normalize(item: dict, platform: str, keyword: str) -> dict | None:
    url = first(item, URL_KEYS)
    if not url:
        return None
    return {
        "platform": platform,
        "keyword": keyword,
        "views": to_int(first(item, VIEW_KEYS)),
        "likes": to_int(first(item, LIKE_KEYS)),
        "comments": to_int(first(item, COMMENT_KEYS)),
        "shares": to_int(first(item, SHARE_KEYS)),
        "author": get_author(item),
        "caption": str(first(item, CAPTION_KEYS, ""))[:500].replace("\n", " "),
        "url": str(url),
        "posted_at": str(first(item, DATE_KEYS, "")),
        "raw_fields": ", ".join(item.keys())[:300],   # helps debug field mapping
    }


# ------------------------------------------------------------ google sheet
class Sheet:
    def __init__(self, book, worksheet: str):
        try:
            self.ws = book.worksheet(worksheet)
        except gspread.WorksheetNotFound:
            self.ws = book.add_worksheet(worksheet, rows=1000, cols=len(HEADERS))
        if self.ws.row_values(1) != HEADERS:
            self.ws.update(range_name="A1", values=[HEADERS])
            self.ws.freeze(rows=1)
        url_col = HEADERS.index("URL") + 1
        self.known_urls = set(self.ws.col_values(url_col)[1:])
        log.info("Tab '%s' ready. %d existing rows.", worksheet, len(self.known_urls))

    def append(self, posts: list[dict]) -> int:
        fresh = [p for p in posts if p["url"] not in self.known_urls]
        if not fresh:
            return 0
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        rows = [[p["platform"], p["keyword"], p["views"], p["likes"], p["comments"],
                 p["shares"], p["author"], p["caption"], p["url"], p["posted_at"],
                 now, p["raw_fields"]] for p in fresh]
        for i in range(0, len(rows), 500):
            self._append_rows(rows[i:i + 500])
        self.known_urls.update(p["url"] for p in fresh)
        return len(fresh)

    def _append_rows(self, rows):
        for attempt in range(6):
            try:
                self.ws.append_rows(rows, value_input_option="USER_ENTERED")
                return
            except gspread.exceptions.APIError as e:
                if "429" in str(e) or "quota" in str(e).lower():
                    wait = 20 * (attempt + 1)
                    log.warning("Google Sheets rate limit, waiting %ds...", wait)
                    time.sleep(wait)
                else:
                    raise
        raise RuntimeError("Google Sheets kept rate-limiting; try again in a few minutes.")


def open_sheets(cfg: dict, sheet_id: str, creds_file: str):
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(creds_file, scopes=scopes)
    book = gspread.authorize(creds).open_by_key(sheet_id)
    good = Sheet(book, cfg.get("worksheet_name", "Viral Content"))
    bad = None
    if cfg.get("save_not_qualified", True):
        bad = Sheet(book, cfg.get("not_qualified_worksheet_name", "Not Qualified"))
    return good, bad


# ------------------------------------------------------------- processing
def process(items, platform, keyword, min_views, seen_urls, good, bad):
    """Split items into qualified / not qualified, save both."""
    qualified, rest = [], []
    no_url = dupes = 0
    sample_keys = None
    for it in items:
        p = normalize(it, platform, keyword)
        if not p:
            no_url += 1
            sample_keys = sample_keys or list(it.keys())
            continue
        if p["url"] in seen_urls:
            dupes += 1
            continue
        seen_urls.add(p["url"])
        (qualified if p["views"] >= min_views else rest).append(p)

    saved_good = good.append(qualified)
    saved_bad = bad.append(rest) if bad else 0
    log.info("   -> %d items | %d qualified (%d new) | %d not qualified (%d new) | "
             "%d duplicates | %d no URL",
             len(items), len(qualified), saved_good, len(rest), saved_bad, dupes, no_url)
    if no_url:
        log.warning("   Items without a URL. Fields seen: %s", sample_keys)
    return len(qualified), saved_good


# ------------------------------------------------------------------ apify
def g(obj, *names, default=None):
    """Read a field from a dict (old apify-client) or an object (new one)."""
    for n in names:
        v = obj.get(n) if isinstance(obj, dict) else getattr(obj, n, None)
        if v not in (None, ""):
            return v
    return default


def dataset_id_of(run) -> str:
    return g(run, "default_dataset_id", "defaultDatasetId")


def run_actor(client: ApifyClient, source: dict, keyword: str, limit: int, timeout: int) -> list[dict]:
    run_input = dict(source.get("input", {}))
    run_input[source["keyword_field"]] = [keyword]
    run_input[source["limit_field"]] = limit
    actor = client.actor(source["actor"])
    try:  # apify-client >= 2.0 takes a timedelta
        run = actor.call(run_input=run_input, timeout=timedelta(seconds=timeout))
    except TypeError:  # apify-client 1.x takes seconds
        run = actor.call(run_input=run_input, timeout_secs=timeout)
    if not run:
        return []
    return list(client.dataset(dataset_id_of(run)).iterate_items())


def run_keyword(client, run) -> str:
    """Best-effort: read the hashtag/keyword this run was started with."""
    try:
        kv = g(run, "default_key_value_store_id", "defaultKeyValueStoreId")
        val = g(client.key_value_store(kv).get_record("INPUT"), "value")
        if isinstance(val, dict):
            for k in ("hashtags", "keywordList", "keywords"):
                if val.get(k):
                    return ", ".join(map(str, val[k]))
    except Exception:
        pass
    return "recovered"


def recover_all(client, min_views, seen_urls, good, bad, max_runs=1000) -> None:
    """Import datasets from ALL past Instagram/Facebook runs in the account."""
    runs = client.runs().list(limit=max_runs, desc=True).items
    log.info("Found %d runs in your Apify account.", len(runs))
    actor_names: dict = {}
    done: set = set()
    for run in runs:
        if "SUCCEEDED" not in str(g(run, "status", default="")).upper():
            continue
        act_id = g(run, "act_id", "actId")
        ds_id = dataset_id_of(run)
        if not act_id or not ds_id or ds_id in done:
            continue
        done.add(ds_id)

        if act_id not in actor_names:
            try:
                a = client.actor(act_id).get()
                actor_names[act_id] = f"{g(a, 'username')}/{g(a, 'name')}".lower()
            except Exception:
                actor_names[act_id] = ""
        name = actor_names[act_id]
        if "instagram" in name:
            platform = "Instagram"
        elif "facebook" in name:
            platform = "Facebook"
        else:
            continue  # ignore unrelated actors

        keyword = run_keyword(client, run)
        log.info("Importing %s run (%s) dataset %s ...", platform, keyword, ds_id)
        try:
            items = list(client.dataset(ds_id).iterate_items())
        except Exception as e:
            log.error("   could not read dataset %s: %s", ds_id, e)
            continue
        process(items, platform, keyword, min_views, seen_urls, good, bad)
    log.info("Recovery finished: checked %d datasets.", len(done))


# ------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--recover-all", action="store_true",
                    help="Import datasets from all past Apify runs in the account")
    ap.add_argument("--dataset", help="Recover mode: read this one Apify dataset ID")
    ap.add_argument("--platform", default="Facebook", help="Platform label for --dataset")
    ap.add_argument("--keyword", default="recovered", help="Keyword label for --dataset")
    args = ap.parse_args()

    token = os.getenv("APIFY_TOKEN")
    sheet_id = os.getenv("GOOGLE_SHEET_ID")
    creds_file = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials/service_account.json")
    if not token or not sheet_id:
        log.error("APIFY_TOKEN and GOOGLE_SHEET_ID must be set (see .env.example).")
        return 1

    with open(os.getenv("CONFIG_FILE", "config.yaml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    min_views = int(cfg["min_views"])
    client = ApifyClient(token)
    good, bad = open_sheets(cfg, sheet_id, creds_file)
    seen_urls: set[str] = set()
    seen_urls |= good.known_urls
    if bad:
        seen_urls |= bad.known_urls

    # ---------- recover ALL past runs ----------
    if args.recover_all:
        recover_all(client, min_views, seen_urls, good, bad)
        return 0

    # ---------- recover one dataset ----------
    if args.dataset:
        log.info("Recovering dataset %s ...", args.dataset)
        items = list(client.dataset(args.dataset).iterate_items())
        process(items, args.platform, args.keyword, min_views, seen_urls, good, bad)
        log.info("Recovery done.")
        return 0

    # ---------- normal scraping mode ----------
    limits = cfg["result_limit_rounds"]
    patience = int(cfg.get("patience", 2))
    max_items = int(cfg.get("max_items_total", 50000))
    timeout = int(cfg.get("actor_timeout_secs", 1800))
    sources = [s for s in cfg["sources"] if s.get("enabled", True)]

    exhausted: set[tuple] = set()
    total_scraped = total_saved = idle_rounds = 0

    for round_no, limit in enumerate(limits, start=1):
        log.info("=== ROUND %d/%d | limit per keyword: %d ===", round_no, len(limits), limit)
        saved_this_round = ok_runs = failed_runs = 0

        for source in sources:
            for keyword in source["keywords"]:
                key = (source["name"], keyword)
                if key in exhausted:
                    continue
                if total_scraped >= max_items:
                    log.warning("Hit max_items_total (%d). Stopping.", max_items)
                    log.info("DONE. Saved %d qualifying posts.", total_saved)
                    return 0

                log.info("[%s] '%s' (limit %d)...", source["name"], keyword, limit)
                try:
                    items = run_actor(client, source, keyword, limit, timeout)
                except Exception as e:
                    log.error("[%s] '%s' failed: %s", source["name"], keyword, e)
                    if "hard limit" in str(e).lower():
                        log.error("Apify usage limit reached. Stopping. Saved %d posts so far.", total_saved)
                        return 0
                    failed_runs += 1
                    continue
                ok_runs += 1

                total_scraped += len(items)
                if len(items) < limit:
                    exhausted.add(key)

                _, saved = process(items, source["platform"], keyword,
                                   min_views, seen_urls, good, bad)
                saved_this_round += saved
                total_saved += saved

        if failed_runs and not ok_runs:
            log.error("Every Apify run failed this round. Check token, actor, "
                      "input fields, credits. Aborting.")
            return 1

        idle_rounds = idle_rounds + 1 if saved_this_round == 0 else 0
        log.info("Round %d finished: +%d qualifying posts (total %d).", round_no, saved_this_round, total_saved)

        all_pairs = {(s["name"], k) for s in sources for k in s["keywords"]}
        if all_pairs <= exhausted:
            log.info("Every source/keyword is exhausted. Nothing left to scrape.")
            break
        if idle_rounds >= patience:
            log.info("No new qualifying posts for %d rounds. Stopping.", patience)
            break

    log.info("DONE. Scraped %d items, saved %d qualifying posts.", total_scraped, total_saved)
    return 0


if __name__ == "__main__":
    sys.exit(main())