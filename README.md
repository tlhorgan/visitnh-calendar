# Visit NH → Proton Calendar

This repository builds an iCalendar (`visitnh.ics`) feed from the Visit NH events calendar:

https://www.visitnh.gov/things-to-do/events-calendar

A GitHub Action runs daily and commits the refreshed ICS file.

## Proton Calendar subscription URL

After pushing this repository to GitHub, use:

https://raw.githubusercontent.com/YOUR-GITHUB-USERNAME/YOUR-REPOSITORY/main/visitnh.ics

Replace `YOUR-GITHUB-USERNAME` and `YOUR-REPOSITORY`.

In Proton Calendar, add a calendar from URL and paste the raw GitHub URL.

## Run manually

Open the repository in GitHub:

Actions → Update Visit NH calendar → Run workflow

## Files

- `generate_calendar.py` — renders Visit NH with Chromium, discovers event pages, extracts dates/times/locations, and builds the ICS file.
- `.github/workflows/update-calendar.yml` — runs the generator daily.
- `visitnh.ics` — generated automatically after the first successful workflow run.
