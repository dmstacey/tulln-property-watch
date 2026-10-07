# Tulln Property Watch

Interactive dashboard of Lower Austria / Tulln-area family-home listings under ~€450k.

Production: https://tulln-property-watch.vercel.app

Static site: `index.html` is the full dashboard. Vercel deploys from the `main` branch.

## Family-fit score
`score_listings.py` (run by the pre-commit hook after `stamp_first_seen.py`) rescores every active listing 0-100 and sets `fit_label` (Highly recommended / Worth a look / Not recommended). Weights and renovation rules of thumb are in the script docstring and the methodology note on the page. Renovation and operating numbers are estimates.
