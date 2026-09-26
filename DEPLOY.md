# Deploy

Production deploys from GitHub `main` via Vercel.

Repo: https://github.com/dmstacey/tulln-property-watch  
Live: https://tulln-property-watch.vercel.app

After updating the dashboard from `/workspace/property-research/`:

```bash
cp /workspace/property-research/dashboard.html /workspace/tulln-property-watch/index.html
cp /workspace/property-research/properties.csv /workspace/property-research/properties.json /workspace/tulln-property-watch/
cd /workspace/tulln-property-watch
python3 archive_listings.py          # photos + archive for any listing that lacks them (see below)
git add index.html properties.csv properties.json img archive
git commit -m "Update property watch data"
git push origin main
```

Do not upload files via Vercel MCP for production.

## Photos and listing archive (`archive_listings.py`)

Every listing gets a house photo shown in the map popup (`img/<id>.jpg`, 640 px JPEG,
field `image`) and a static snapshot of its listing page (`archive/<id>.html` plus up to
8 photos in `archive/<id>/`, fields `archive` and `archived_at`). `archive/index.html`
lists all snapshots.

`python3 archive_listings.py` is idempotent: it only touches listings that have a URL
but no `image`/`archive` yet, never overwrites an existing archive (so sold/disappeared
listings keep their snapshot), and writes the new fields into `index.html`, the research
`dashboard.html`, and both copies of `properties.json`/`properties.csv`. Run it in the
twice-daily watch **after** copying the new data into `index.html` and **before**
`git commit` (the pre-commit hook `stamp_first_seen.py` keeps working with it).

Useful flags: `--ids SL1,NEW7` (only these), `--redo --ids X` (rebuild X from scratch),
`--dry-run`, `--no-live` (cached scrape pages only), `--index-only`.
Requires `pip install Pillow beautifulsoup4`. immowelt (DataDome) and remax
(Cloudflare Turnstile) block scripted requests; the script skips them and uses
`url_alt` or cached pages instead. A run log is written to `.archive_last_run.json`
(git-ignored).
