# US-Iran Conflict Tracker (Free MVP)

Small personal app that:
- pulls free RSS headlines
- converts them into simple rule-based events
- computes a scoreboard JSON
- renders a static dashboard

## Local run

```bash
python scripts/fetch_news.py
python scripts/score_events.py
python -m http.server 8000
```

Open `http://localhost:8000` to view the dashboard.

## Auto updates (GitHub Actions)

Workflow file: `.github/workflows/update-score.yml`

- runs every 3 hours
- updates `data/events.json` and `data/score.json`
- commits changes back to the repo

## Deploy free

Use GitHub Pages:
1. Push this repo to GitHub.
2. In repo settings, enable Pages from branch root (`/`).
3. Share the Pages URL with friends.

## Notes

- Scoring is heuristic and noisy by design.
- Treat this as a signal board, not objective truth.
- If you want better quality later, add optional OpenAI event classification only for ambiguous headlines.
