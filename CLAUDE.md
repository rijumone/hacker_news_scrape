# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A Flask REST API that scrapes tech news aggregators (Hacker News, Lobsters, High Scalability), stores each scrape as a time-stamped snapshot in PostgreSQL, and exposes aggregate statistics (top posts, word frequencies, comment-tree depth, most-active users, etc.) over configurable time windows.

## Commands

Docker Compose is the supported way to run the stack — it wires up Postgres, sets every required env var, and runs migrations + `init_db` on startup. Running the app/scrapers directly on the host isn't currently supported (`hacker_news/models.py` reads `os.environ['DB_CONNECTION']` at import time, so anything importing it needs the env fully configured — Compose does this for you).

```bash
# Full stack (Postgres + API + Adminer) — runs alembic + init_db automatically
ENV=DEV docker-compose up      # DEV → flask dev server; else gunicorn. API on :8001, Adminer on :8080

# Management actions (run inside the web container)
docker-compose exec web python management.py init_db     # one-time FTS config setup (see below)
docker-compose exec web python management.py scrape_hn   # also: scrape_lobsters, scrape_high_scalability
docker-compose exec web python management.py sched_scrape  # hourly HN scrape via crontab
docker-compose exec web python management.py backup_db     # pg_dump → S3 (only actually runs on Sundays)

# Migrations (also run automatically on `docker-compose up`)
docker-compose exec web alembic upgrade head
```

Tests spin up their own ephemeral Postgres via `testing.postgresql` (overriding `DB_CONNECTION` at runtime in `utils/tests.py`), so they don't need the Compose DB. But `models.py` reads `DB_CONNECTION` at import — before that override — so the var must still *exist* in the environment (any value; it gets replaced). CI sets `DB_CONNECTION` and `DB_NAME` globally (see `.travis.yml`).

```bash
coverage run -m unittest discover
python -m unittest tests.test_hacker_news.TestComment.test_comment_get   # single test
```

## Architecture

Three layers, each in its own package:

- **`server.py`** — thin Flask routing layer. Every route is `/api/<source>/...`; `<source>` is a URL variable (`hacker_news`, `lobsters`, `high_scalability`, or `all`) threaded into every handler. Handlers do no logic — they call `hacker_news.*` functions and return the result directly.
- **`hacker_news/hacker_news.py`** — all query/stats business logic. Functions return Flask `jsonify(...)` responses directly (not plain data). Stats endpoints follow the pattern `hacker_news.get_X(hacker_news.get_feeds(time_period, source))` — `get_feeds` resolves a `time_period` (`hour`/`day`/`week`/`all`) + `source` into a list of feed IDs, then the stat function aggregates over those feeds.
- **`scrapers/`** — one module per source, each a `BaseScraper` (ABC in `base.py`) subclass implementing `scrape_loop` / `scrape_page` / `scrape_post`. Scraping is async (asyncio + `requests` + BeautifulSoup), fetching the first three feed pages concurrently. `management.py` maps CLI actions to each scraper's module-level `scrape_*_loop()` function.

### Data model (`hacker_news/models.py`)

The key idea is **snapshots over time**. Each scrape run creates one `Feed` row (tagged with `source` + timestamp). `Post` and `Comment` hold the canonical content (deduplicated by id/uid across scrapes), while the join tables **`FeedPost`** and **`FeedComment`** capture the per-snapshot mutable data — feed rank, comment count, point count. This is what makes time-series stats possible: the same post appears in many feeds with different ranks/counts. "Latest" data for a post is found via `MAX(feed_id)` per post_id.

`source` and `uid` columns (added in migration `4bb07f14e7f4`) make the schema multi-source. Query functions filter by `source` unless it's `'all'`.

### PostgreSQL full-text search

Comment word-frequency stats depend on a custom `simple_english` text-search dictionary/config created by `management.py init_db` — it strips English stopwords **without** stemming. `Comment.word_counts` is a `TSVECTOR` populated with `func.to_tsvector('simple_english', ...)`. Running word-related stats before `init_db` will fail.

## Conventions & gotchas

- **`update_server.py`, `update_server2.py`, `fix_entities.py`** (untracked) are one-off codemod scripts (regex rewrites of `server.py` routes and `hacker_news.py` queries) used to retrofit multi-source support. They are throwaway migration tooling, not part of the runtime.
- When `DB_CONNECTION` is empty, several endpoints fall back to returning static JSON from `sample_data/` (front-end demo mode).
- Environment is `DEV` vs `PROD` via `ENV` / `ENV_TYPE` (default `PROD`); `DEV` enables Flask debug and the dev server.
- Tests subclass `HackerNewsTestCase` in `utils/tests.py`, which mocks `requests.get` against HTML/JSON in `fixtures/` and runs migrations against a throwaway Postgres.
- Adding a new source: create a `scrapers/<name>.py` `BaseScraper` subclass with a `scrape_<name>_loop()`, register it in `management.py`, and set `source='<name>'` on the `Feed`. No `server.py` change needed — routing is already source-generic.
