# birkie-data

Interactive finish-time histograms for the American Birkebeiner and Kortelopet ski races, built from their public results.

The site is published with GitHub Pages at [corbylewis.github.io/birkie-data](https://corbylewis.github.io/birkie-data/).

For race information, registration and official results, see the [American Birkebeiner website](https://www.birkie.com/ski/).

## Plots

2020–2026 finish times for the Birkebeiner Classic, Birkebeiner Skate, Kortelopet Classic and Kortelopet Skate: one tab per race, with a year tab under it (link to one with e.g. `#birkie-skate-2025`).

- Each plot shows all finishers, with a histogram for each start wave (Elite, U20, numbered waves, Spirit of 35, Wave 70) that can be added from the legend. Waves are identified by bib number ranges.
- **Show medians** adds each group's median.
- **Cutoffs (next year)**, on by default, shows the wave cutoffs that year's results set for the next year's race, from birkie.com's wave placement page and its Wayback Machine copies (hard-coded in `EVENTS`). It's only offered for 2023, 2025 and 2026; earlier years' tables weren't archived.
- **Specific finish time** places a time on the plot, with its overall place and percentile. Pick it with the percentile slider (fastest first), or type an actual time to override it. It starts at the 25th percentile, with the line hidden until its box is ticked or the time is changed.

## Build

From the repo root:

```sh
pip install -r requirements.txt
python build.py
```

This writes `docs/index.html`, a single self-contained page. Set `OUTPUT_DIR` to write somewhere else.

Fetched results are cached in `cache/`, one JSON file per race and year, so later builds take a couple of seconds instead of fetching everything again (about a minute, mostly paging through the 2020 results). Run `python build.py --refresh` to fetch everything again, or delete a single file in `cache/` to refetch just that race.

## Publishing

The site is served by GitHub Pages from the `docs/` folder. To publish an update, run the build and commit `docs/`. The page goes live once the commit is pushed to `main`.

For a new repo, turn this on once under **Settings → Pages**: set **Source** to *Deploy from a branch*, the branch to `main` and the folder to `/docs`.

## Adding a year

Add the year's `base_url` and `key` to `YEARS` in `build.py`. To find them, open the year's results page on raceresult.com and look at the `data/list` request in the browser dev tools. (2020 and 2021 come from other timing sites; see their `YEARS` entries.) Then check that the contest IDs and bib ranges in `EVENTS` still match that year's races and waves, and add the year's `cutoffs` once birkie.com publishes the next year's wave placement tables.

Two years carry a note on their tabs, since their times aren't comparable with other years:

- **2021** was a pandemic-format race on 45 km and 26 km courses, with skiers starting on five different days.
- **2024** was a low-snow year on a 10 km lapped course: 30 km Birkie and 20 km Korte. The Birkie Skate elite race was 50 km, so it's left out of that plot.

## Credits

- Race results, via the links on the [American Birkebeiner results page](https://www.birkie.com/ski/results/): [raceresult.com](https://my.raceresult.com/) (2022 on), [RunSignup](https://runsignup.com/Race/Results/107357) (2021) and [PT Timing](http://birkie.pttiming.com/results/2020/) (2020).
- Wave rules and cutoffs: the American Birkebeiner's [wave placement page](https://birkie.com/ski/birkie-week/wave-placement).
- Inspired by [birkieguide.com](https://birkieguide.com/).

This project is not affiliated with the American Birkebeiner Ski Foundation.
