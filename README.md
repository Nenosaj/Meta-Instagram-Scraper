# 🏠 Viral Real-Estate Content Scraper

> **Apify (Instagram + Facebook) → Google Sheets**
>
> Automatically scrapes real-estate hashtag content from Instagram and Facebook using Apify actors, filters posts that meet a minimum view threshold, and writes them—de-duplicated by URL—to a Google Sheet in real time.

---

## Table of Contents

1. [Overview](#overview)
2. [How It Works](#how-it-works)
3. [Project Structure](#project-structure)
4. [Prerequisites](#prerequisites)
5. [Installation & Setup](#installation--setup)
   - [1. Apify](#1-apify)
   - [2. Google Sheets Service Account](#2-google-sheets-service-account)
   - [3. Environment Variables](#3-environment-variables)
   - [4. Configuration File](#4-configuration-file)
6. [Running the Scraper](#running-the-scraper)
   - [Docker Compose (recommended)](#docker-compose-recommended)
   - [Plain Docker](#plain-docker)
   - [Local Python](#local-python)
7. [CLI Arguments & Recover Modes](#cli-arguments--recover-modes)
8. [Output — Google Sheet Structure](#output--google-sheet-structure)
9. [Configuration Reference (`config.yaml`)](#configuration-reference-configyaml)
10. [Source Configuration Reference](#source-configuration-reference)
11. [Code Architecture](#code-architecture)
    - [Field Normalization](#field-normalization)
    - [Sheet Class](#sheet-class)
    - [Scraping Loop (Rounds)](#scraping-loop-rounds)
    - [Recovery Mode](#recovery-mode)
12. [Cost & Rate Limits](#cost--rate-limits)
13. [Troubleshooting](#troubleshooting)
14. [Security Notes](#security-notes)

---

## Overview

This tool is built for **real-estate marketing teams** who want to identify viral social media content about home buying and selling. It:

- Scrapes **Instagram Reels/Posts** and **Facebook** posts by hashtag/keyword
- Applies a **minimum views filter** (`min_views` in `config.yaml`) to separate viral posts from non-qualifying ones
- Writes results to two **Google Sheets tabs**:
  - `Viral Content` — posts that meet the threshold
  - `Not Qualified` — posts that don't (optional, configurable)
- **De-duplicates** by post URL across all runs, so you never get the same post twice
- Scrapes in **escalating rounds** with configurable per-keyword limits, stopping when all sources are exhausted or no new qualifying posts appear
- Supports **recovery modes** to re-import data from past Apify runs without running a new scrape

---

## How It Works

```
config.yaml (keywords, limits, sources)
        │
        ▼
  main.py --  for each source × keyword:
        │       1. Call Apify actor with keyword + result limit
        │       2. Normalize raw items (views, likes, URL, author, caption…)
        │       3. Filter: views >= min_views → "Viral Content" tab
        │                  views <  min_views → "Not Qualified" tab
        │       4. Append new rows (skip URLs already in sheet)
        │
        └──▶ Google Sheets (de-duplicated, timestamped)
```

**Rounds:** The scraper loops through `result_limit_rounds` (e.g. `[100, 500, 1000]`). In each round every enabled source/keyword gets a fresh Apify run with a bigger result limit. A source/keyword pair is marked **exhausted** when Apify returns fewer results than the limit. The loop stops when:
- All source/keyword pairs are exhausted, or
- No new qualifying posts were saved for `patience` consecutive rounds, or
- `max_items_total` raw items have been processed.

---

## Project Structure

```
social-scraper/
├── main.py                  # All scraper logic (single-file)
├── config.yaml              # Keywords, sources, thresholds — edit this
├── requirements.txt         # Python dependencies
├── Dockerfile               # Python 3.12-slim, runs as non-root
├── docker-compose.yml       # Mounts credentials & config; reads .env
├── .env                     # Your secrets (git-ignored)
├── .env.example             # Template for .env
├── .gitignore               # Ignores .env, credentials/, __pycache__
├── .dockerignore            # Ignores .env, credentials/, .git, .venv
└── credentials/
    ├── .gitkeep             # Keeps the folder in git without contents
    └── service_account.json # Your Google service account key (git-ignored)
```

---

## Prerequisites

| Tool | Minimum version | Purpose |
|---|---|---|
| Python | 3.10+ | Run locally |
| Docker + Compose | any recent | Run containerized |
| [Apify](https://apify.com) account | — | Runs Instagram / Facebook actors |
| Google Cloud project | — | Sheets API + service account |

---

## Installation & Setup

### 1. Apify

1. Sign up at [apify.com](https://apify.com).
2. Go to **Console → Settings → Integrations** and copy your **API token**.
3. Ensure your plan includes credits for:
   - `apify/instagram-hashtag-scraper`
   - `apify/facebook-hashtag-scraper`

> **Free plan note:** The free Apify plan caps results at ~30 per hashtag regardless of your `resultsLimit`. A paid plan unlocks higher limits (200–5000+).

---

### 2. Google Sheets Service Account

1. Go to [console.cloud.google.com](https://console.cloud.google.com) and create (or select) a project.
2. Navigate to **APIs & Services → Library** and enable **Google Sheets API**.
3. Go to **IAM & Admin → Service Accounts → Create Service Account**.
4. Under the service account, go to **Keys → Add Key → JSON**. Download the file.
5. Save it as `credentials/service_account.json` in this project.
6. Create a Google Sheet. In the sheet, click **Share** and add the service account's `client_email` as **Editor**.
7. Copy the **sheet ID** from the URL:
   ```
   https://docs.google.com/spreadsheets/d/<SHEET_ID>/edit
   ```

---

### 3. Environment Variables

```bash
cp .env.example .env
```

Then edit `.env`:

```env
# Your Apify API token
APIFY_TOKEN=apify_api_xxxxxxxxxxxxxxxxxxxx

# The Google Sheet ID (from the URL)
GOOGLE_SHEET_ID=your_google_sheet_id
```

Optional env vars (with defaults):

| Variable | Default | Description |
|---|---|---|
| `GOOGLE_CREDENTIALS_FILE` | `credentials/service_account.json` | Path to service account JSON |
| `CONFIG_FILE` | `config.yaml` | Path to the YAML config file |

---

### 4. Configuration File

Edit `config.yaml` to control scraping behavior. See the [Configuration Reference](#configuration-reference-configyaml) section for all options.

---

## Running the Scraper

### Docker Compose (recommended)

```bash
docker compose up --build
```

This automatically:
- Reads secrets from `.env`
- Mounts `credentials/` (read-only) into the container
- Mounts `config.yaml` (read-only) so you can change keywords without rebuilding

### Plain Docker

```bash
docker build -t social-scraper .
docker run --rm --env-file .env \
  -v "$(pwd)/credentials:/app/credentials:ro" \
  -v "$(pwd)/config.yaml:/app/config.yaml:ro" \
  -e GOOGLE_CREDENTIALS_FILE=/app/credentials/service_account.json \
  social-scraper
```

### Local Python

```bash
pip install -r requirements.txt
python main.py
```

---

## CLI Arguments & Recover Modes

The scraper supports three modes:

### Normal scraping (default)
```bash
python main.py
```
Runs the full multi-round scraping loop using `config.yaml`.

---

### Recover all past runs
```bash
python main.py --recover-all
```
Scans **all past runs** in your Apify account, finds any that used an Instagram or Facebook actor, reads their stored datasets, and imports the results into the sheet — **without triggering a new Apify run or spending credits**.

Useful when:
- You forgot to run the script after a manual Apify run
- You changed the `min_views` threshold and want to re-classify old data
- You had a crash mid-run and want to salvage the data

---

### Recover a single dataset
```bash
python main.py --dataset <APIFY_DATASET_ID> --platform Facebook --keyword homebuyertips
```

| Argument | Required | Description |
|---|---|---|
| `--dataset` | Yes | The Apify dataset ID to read from |
| `--platform` | No (default: `Facebook`) | Label to write in the Platform column |
| `--keyword` | No (default: `recovered`) | Label to write in the Keyword column |

---

## Output — Google Sheet Structure

Two tabs are created automatically if they don't exist:

### `Viral Content` tab
Posts where **views ≥ `min_views`**.

### `Not Qualified` tab
Posts where **views < `min_views`** (only written if `save_not_qualified: true` in config).

---

### Column Reference

| Column | Description |
|---|---|
| **Platform** | `Instagram` or `Facebook` |
| **Keyword** | The hashtag/keyword that found this post |
| **Views** | Normalized integer view count |
| **Likes** | Normalized integer like/reaction count |
| **Comments** | Normalized integer comment count |
| **Shares** | Normalized integer share count |
| **Author** | Username or page name of the post author |
| **Caption** | First 500 characters of the post caption/text (newlines removed) |
| **URL** | Direct link to the post (also used as the de-duplication key) |
| **Posted At** | Original post timestamp as returned by the actor |
| **Scraped At** | UTC timestamp when this row was written to the sheet |
| **Raw Fields** | Comma-separated list of the raw API field names (first 300 chars) — useful for debugging field mapping |

---

## Configuration Reference (`config.yaml`)

```yaml
# ── Filtering ──────────────────────────────────────────────────────────────
min_views: 50000           # Posts with >= this many views go to "Viral Content"
                           # Posts below go to "Not Qualified"

# ── Google Sheets tabs ─────────────────────────────────────────────────────
worksheet_name: "Viral Content"             # Tab for qualifying posts
save_not_qualified: true                    # Whether to write non-qualifying posts
not_qualified_worksheet_name: "Not Qualified"

# ── Scraping rounds ────────────────────────────────────────────────────────
result_limit_rounds: [100]
# Each number is the per-keyword result limit for that round.
# Example for a paid plan: [200, 500, 1000, 2500, 5000]
# The scraper will run multiple rounds, increasing the limit each time,
# until all source/keyword pairs are exhausted.

patience: 2
# Stop if no new qualifying posts appear for this many consecutive rounds.

max_items_total: 1500
# Hard cap on the total number of raw items to process across all rounds.
# Prevents runaway credit spending. Raise once you're comfortable.

actor_timeout_secs: 1800
# Maximum time (in seconds) to wait for a single Apify actor run to complete.
# Default is 30 minutes.

# ── Sources ────────────────────────────────────────────────────────────────
sources:
  - name: ig_reels
    enabled: false
    # ... (see Source Configuration Reference below)
```

---

## Source Configuration Reference

Each entry under `sources:` in `config.yaml` defines one scraping source:

```yaml
sources:
  - name: ig_reels                           # Internal name (used in logs & exhausted tracking)
    enabled: true                            # Set false to skip this source entirely
    platform: Instagram                      # Label written to the "Platform" column
    actor: apify/instagram-hashtag-scraper   # Apify actor ID
    keyword_field: hashtags                  # Actor input field for the keyword list
    limit_field: resultsLimit                # Actor input field for the result count
    input:                                   # Additional static actor input fields
      resultsType: reels                     #   e.g. "reels" or "posts" for Instagram
    keywords:                                # List of hashtags/keywords to scrape
      - homebuyertips
      - homesellertips
      - firsttimehomebuyer
```

### Pre-configured sources

| Name | Platform | Actor | Status |
|---|---|---|---|
| `ig_reels` | Instagram | `apify/instagram-hashtag-scraper` | Disabled by default |
| `ig_posts` | Instagram | `apify/instagram-hashtag-scraper` | Disabled by default |
| `fb_hashtags` | Facebook | `apify/facebook-hashtag-scraper` | **Enabled** |

> **Why are Instagram sources disabled?**
> Instagram reels have view counts but the free Apify plan returns very few results. Enable them when you have Apify credits. `ig_posts` is also disabled because image posts don't carry view counts.

### YAML Anchors for shared keyword lists

The config uses a YAML anchor (`&ig_tags`) so both `ig_reels` and `ig_posts` share the same keyword list without duplication:

```yaml
keywords: &ig_tags       # define the anchor
  - homebuyertips
  - ...

# In another source:
keywords: *ig_tags       # reference the same list
```

---

## Code Architecture

### Field Normalization

Because different Apify actors return different field names for the same concept (e.g. views might be `videoPlayCount`, `playsCount`, or `viewCount`), the scraper uses priority-ordered key lists and picks the first non-empty value:

```python
VIEW_KEYS    = ["videoPlayCount", "videoViewCount", "playsCount", "playCount", ...]
LIKE_KEYS    = ["likesCount", "likes", "likeCount", "reactionsCount", ...]
COMMENT_KEYS = ["commentsCount", "comments", "commentCount"]
SHARE_KEYS   = ["sharesCount", "shares", "shareCount"]
URL_KEYS     = ["url", "postUrl", "permalink", "link", ...]
CAPTION_KEYS = ["caption", "text", "message", "description", "title"]
DATE_KEYS    = ["timestamp", "time", "date", "createdAt", ...]
AUTHOR_KEYS  = ["ownerUsername", "ownerFullName", "pageName", "authorName", "username"]
```

The `to_int()` helper converts human-readable counts to integers, handling formats like `12,345`, `1.2M`, and `850K`.

Author extraction also checks nested objects (`user`, `author`, `owner`, `page`) if no top-level author field is found.

---

### Sheet Class

`Sheet` wraps a single Google Sheets worksheet tab:

- **On init:** Creates the tab if it doesn't exist, writes the header row, freezes row 1, and loads all existing URLs into a `known_urls` set for O(1) de-duplication.
- **`append(posts)`:** Filters out posts already in `known_urls`, writes new rows in batches of 500 (the Sheets API batch limit), and handles `429 / quota` rate-limit errors with exponential back-off (up to 6 retries, 20s → 120s wait).

---

### Scraping Loop (Rounds)

```
for each round_limit in result_limit_rounds:
    for each enabled source:
        for each keyword:
            skip if (source, keyword) already exhausted
            stop if max_items_total reached
            run Apify actor → get items
            if len(items) < limit → mark (source, keyword) as exhausted
            process items → split into qualified / not qualified → save to sheet

    if all (source, keyword) pairs exhausted → break
    if no new qualifying posts for `patience` rounds → break
```

---

### Recovery Mode

`recover_all()` lists all runs in the Apify account (up to 1000, most recent first), skips non-`SUCCEEDED` runs and unrelated actors, resolves the actor name to determine `Instagram` or `Facebook`, and reads the best-effort keyword from the run's `INPUT` key-value store record.

---

## Cost & Rate Limits

| Service | Notes |
|---|---|
| **Apify** | Billed per result. Free plan: ~30 results/hashtag. Paid plans unlock higher limits. Start with `max_items_total: 1500` and raise gradually. |
| **Google Sheets API** | 60 write requests/minute per user. The scraper handles `429` errors automatically with back-off. |
| **Apify actor timeout** | Each actor run has a 30-minute timeout (`actor_timeout_secs`). Raise this for very large result limits. |

---

## Troubleshooting

| Problem | Likely cause | Fix |
|---|---|---|
| `APIFY_TOKEN and GOOGLE_SHEET_ID must be set` | `.env` not loaded or missing | Copy `.env.example` → `.env` and fill in the values |
| `WorksheetNotFound` | Sheet not shared with service account | Share the Google Sheet with the service account's `client_email` as Editor |
| Actor returns 0 items | Wrong `keyword_field` or `limit_field` | Open the actor on apify.com, check its Input schema, and update `config.yaml` |
| `Google Sheets rate limit` | Too many writes in a short time | The scraper retries automatically; if it keeps failing, wait a few minutes |
| `Apify usage limit reached` | Ran out of Apify credits | Top up credits or reduce `max_items_total` |
| View count is 0 for all posts | Actor field name mismatch | Check the `Raw Fields` column in the sheet to see which fields the actor returned, then add the correct key to `VIEW_KEYS` in `main.py` |
| `Items without a URL` warnings | Actor returned items with no URL field | Check `Raw Fields` column; update `URL_KEYS` if a different field holds the URL |

---

## Security Notes

- `.env` and `credentials/` are listed in both `.gitignore` and `.dockerignore` — they will **never** be committed to git or baked into the Docker image.
- The Docker container runs as a **non-root user** (`scraper`) for least-privilege execution.
- `credentials/` is mounted into the container as **read-only** (`ro`).
- The `credentials/.gitkeep` file keeps the folder tracked in git so the mount path exists, without exposing any secrets.
