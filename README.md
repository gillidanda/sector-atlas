# Sector Atlas

Static site (index.html) plus a daily price job.

## One-time setup
1. Create a GitHub repo and push this folder (keep the `.github` folder).
2. Settings > Pages > Deploy from branch > `main` / root.
3. Actions tab > "Update prices" > Run workflow (creates `perf.js` the first time).

After that it runs Mon-Fri at 17:00 IST, fetches EOD closes, rewrites `perf.js`, and commits only if it changed.
Add or move tickers by editing `DATA` in `index.html`; the job reads the list from there.
