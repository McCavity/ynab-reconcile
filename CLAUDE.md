# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A YNAB reconciliation tool available in two modes:
- **CLI** (`ynab_reconcile.py`) — interactive terminal tool, stdlib only
- **Web UI** (`web/`) — Flask + vanilla JS SPA, drag-and-drop matching interface

The UI is in German. Deploys via Docker or runs locally with Flask.

## Directory Structure

```
core/               — shared business logic (no UI dependencies)
  api.py            — YNAB HTTP layer (raises exceptions instead of sys.exit)
  csv_parser.py     — Finanzblick CSV parser (returns tuple, no prints)
  matching.py       — weighted matching algorithm
  parsing.py        — German/English amount parser
  state.py          — aliases.json + config.json persistence (DATA_DIR aware)
  ynab.py           — load payees/categories helpers
web/
  app.py            — Flask backend, in-memory sessions, REST API
  static/
    index.html      — four-view SPA (setup, matching, reconcile, done)
    app.js          — vanilla JS, drag-and-drop state management
    style.css       — dark theme
ynab_reconcile.py   — CLI entry point (imports from core/)
ynab_test.py        — API connection test (run first to verify credentials)
.env                — YNAB Personal Access Token (not committed; copy from .env.example)
requirements.txt    — flask>=3.0,<4.0
Dockerfile          — python:3.12-slim, DATA_DIR=/data
docker-compose.yml  — named volume ynab-data, YNAB_API_TOKEN from env
```

## Setup

```bash
cp .env.example .env
# edit .env and set YNAB_API_TOKEN=your_token_here

# CLI
python3 ynab_test.py        # verify connection
python3 ynab_reconcile.py

# Web (local dev)
pip install flask
python3 -m web.app

# Docker
docker compose up --build
```

## Bank CSV Format

Imports **Finanzblick** `Buchungsliste.csv` exports (semicolon-separated, UTF-8-BOM). In the
web UI, upload via file picker — never written to disk. In the CLI, place the file in the
project root — it is auto-detected. PayPal transactions have their real merchant name extracted
from the `Verwendungszweck` field.

## Key Concepts

- **Matching algorithm**: weighted score on amount (60%), date proximity (25%), payee similarity (15%)
- **Aliases** (`aliases.json`): remembers bank payee → YNAB payee mappings across sessions
- **Deferred payees** (`config.json`): payees billed as monthly lump sums (e.g. RMV) can be
  marked "always skip" so individual YNAB entries are ignored during reconciliation
- Both JSON files are auto-created and excluded from git; delete them to reset learned state
- **DATA_DIR**: env var for Docker volume path; defaults to project root locally
- **Sessions**: in-memory only, keyed by UUID; discarded on page reload (acceptable trade-off)

## CLI Actions During Reconciliation

| Situation | Actions |
|---|---|
| YNAB ↔ Bank match | `[c]`lear, `[e]`dit amount, `[s]`kip |
| Only in YNAB | `[s]`kip, `[c]`lear anyway, `[i]`always skip (deferred) |
| Only in bank | `[a]`dd to YNAB, `[s]`kip |

## Organisation Context

This repository is part of Henning Halfpap's personal GitHub collection, located at
`/Users/hhalfpap/git/projects/own` on the development machine.

- **Org index**: `/Users/hhalfpap/git/projects/own/org-index.json` — machine-readable
  metadata for all repos (last commit, CLAUDE.md presence, file count, etc.)
- **Org instructions**: `/Users/hhalfpap/git/projects/own/CLAUDE.md` — guidance for
  cross-repo maintenance tasks (checking sync status, stale repos, etc.)

For project-specific work, operate within this directory. For questions spanning
multiple repos, consult the org index first.

**Tooling rule**: Skills, plugins, and MCP servers are always installed at project level
(`.claude/settings.json` in this directory), never at user/global level.
