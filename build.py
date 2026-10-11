"""Fetch Birkie results and build docs/index.html.

Run from the repo root:  python build.py
Set OUTPUT_DIR to write somewhere else. GitHub Pages serves docs/.

Fetched results are cached in cache/ (kept out of git), so later builds don't
refetch them; run with --refresh to fetch everything again.
"""

import html
import json
import math
import os
import re
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import plotly.graph_objects as go
import requests

# Where each year's results come from. Most years are on raceresult.com: open
# the year's results page and copy base_url and key from the data/list request
# in the browser dev tools. Older years are on other timing sites; their
# "races" map each race's raceresult contest ID (in EVENTS) to that site's IDs.
# A "note" is shown on every tab for that year.
YEARS = {
    2020: {
        "source": "pttiming",
        "url": "http://birkie.pttiming.com/results/2020/index.php",
        "races": {"11": 2, "10": 1, "8": 4, "7": 3},  # division IDs
    },
    2021: {
        "source": "runsignup",
        "race_id": 107357,
        # (event ID, result set ID) for each start day and the elite starts;
        # the "Combined" result sets aren't public
        "races": {
            "11": [(473212, 239908), (473218, 239914), (473219, 239915)],
            "10": [(473204, 239900), (473205, 239901), (473209, 239905), (473215, 239911)],
            "8": [(473213, 239909), (473220, 239916), (473221, 239917)],
            "7": [(473206, 239902), (473207, 239903), (473210, 239906), (473216, 239912)],
        },
        "note": (
            "2021 was a pandemic-format race: 45 km Birkie and 26 km Korte courses "
            "(normally about 50 and 29 km), with skiers starting on five different days. "
            "Times use each skier's own start, and aren't comparable with other years."
        ),
    },
    2022: {
        "base_url": "https://my-us-1.raceresult.com/189471/RRPublish/data/list",
        "key": "480a2de1e7213f3c4acd95e073d0028a",
    },
    2023: {
        "base_url": "https://my-us-1.raceresult.com/225100/RRPublish/data/list",
        "key": "101f5d19227d60172fb1415b9dda0769",
    },
    2024: {
        "base_url": "https://my-us-1.raceresult.com/273908/RRPublish/data/list",
        "key": "31bbbb41e818e3f37443449d116f13bc",
        # Each race's main field and elite start were separate contests, some
        # only published as "Open Track" lists: (contest, list name). The Birkie
        # Skate elite race was 50 km, so it's left out.
        "races": {
            "11": [("11", "Result Lists|Overall Results"), ("18", "Result Lists|Overall Open Track")],
            "10": [("10", "Result Lists|Overall Open Track")],
            "8": [("8", "Result Lists|Overall Open Track"), ("20", "Result Lists|Overall Results")],
            "7": [("7", "Result Lists|Overall Open Track"), ("19", "Result Lists|Overall Results")],
        },
        "note": (
            "2024 was a low-snow year raced on a 10 km lapped course: 30 km Birkie and 20 km Korte "
            "(normally about 50 and 29 km). The Birkie Skate elite race was 50 km, so it's left out "
            "of that plot. Times aren't comparable with other years."
        ),
    },
    2025: {
        "base_url": "https://my3.raceresult.com/327002/RRPublish/data/list",
        "key": "641c68158d8e2b87c2f92e0f9eaa8f2d",
    },
    2026: {
        "base_url": "https://my4.raceresult.com/380971/RRPublish/data/list",
        "key": "165f512b9755baa4f95b188a873c4739",
    },
}

REPO_URL = "https://github.com/CorbyLewis/birkie-data"

# Bin widths to choose from, in minutes; all divide an hour evenly, so bin
# edges land on round times
BIN_WIDTHS = [2, 3, 4, 5, 6, 10, 12, 15, 20, 30]

# Finish times over this are timing errors (one 2026 Birkie Classic result is 62:34:05)
MAX_FINISH_MIN = 12 * 60


def numbered_waves(first_bib, count):
    """Waves 1..count, each a block of 1000 bibs starting at first_bib + 1000."""
    return [(first_bib + n * 1000, first_bib + n * 1000 + 999, f"Wave {n}") for n in range(1, count + 1)]


# One tab per race, with a tab for each year under it. Waves are (first bib,
# last bib, name), in legend order; the bib ranges are the same every year
# except where waves_by_year overrides them.
# Wave rules: birkie.com/ski/events/kortelopet and birkie.com/ski/events/birkie
#
# cutoffs map a result year to the next year it seeded and that year's wave
# cutoffs: the slowest time that still qualifies for Wave 1, Wave 2, ...
# Anything slower goes in the wave after the last one listed. 2023 results
# seeded 2025, since 2024 results didn't count. From
# birkie.com/ski/birkie-week/wave-placement: the live page for 2027, and Wayback
# Machine copies from 2025-01-19 (2025 waves) and 2025-12-07 (2026 waves)
EVENTS = [
    {
        "name": "Birkebeiner Classic",
        "tab": "Birkie Classic",
        "contest": "11",
        "cutoffs": {
            2023: (2025, ["4:08:45", "4:56:58", "5:45:10", "6:54:01"]),
            2025: (2026, ["3:53:35", "4:21:31", "4:57:12", "5:49:59"]),
            2026: (2027, ["3:27:45", "4:00:29", "4:40:34", "5:34:00"]),
        },
        # Classic elite men and women start together, so one Elite wave
        "waves": [
            (300, 499, "Elite"),
            (600, 699, "Elite"),
            *numbered_waves(10000, 6),
            (35000, 35999, "Spirit of 35"),
            (70000, 70999, "Wave 70"),
        ],
    },
    {
        "name": "Birkebeiner Skate",
        "tab": "Birkie Skate",
        "contest": "10",
        "cutoffs": {
            2023: (2025, ["3:00:18", "3:12:52", "3:26:42", "3:41:46", "3:58:06", "4:20:43", "4:49:37", "5:39:53"]),
            2025: (2026, ["2:49:53", "3:04:02", "3:15:44", "3:28:39", "3:44:39", "4:04:21", "4:31:26", "5:42:50"]),
            2026: (2027, ["2:28:23", "2:39:27", "2:50:32", "3:03:49", "3:17:40", "3:31:30", "3:53:39", "5:10:03"]),
        },
        "waves": [
            (1, 499, "Elite Men"),
            (500, 999, "Elite Women"),
            *numbered_waves(0, 10),
            (35000, 35999, "Spirit of 35"),
            (70000, 70999, "Wave 70"),
        ],
    },
    {
        "name": "Kortelopet Classic",
        "tab": "Korte Classic",
        "contest": "8",
        "cutoffs": {
            2023: (2025, ["2:30:56", "3:12:44"]),
            2025: (2026, ["2:44:35", "3:24:26"]),
            2026: (2027, ["2:37:12", "3:14:56"]),
        },
        "waves": [
            (900, 999, "Elite"),
            (50000, 50999, "U20"),
            *numbered_waves(50000, 4),
            (70000, 70999, "Wave 70"),
        ],
        # 2020 Korte bibs were numbered differently; groups matched by age and gender
        "waves_by_year": {
            2020: [
                (200, 399, "Elite"),
                (800, 999, "U20"),
                *numbered_waves(10000, 4),
                (70000, 70999, "Wave 70"),
            ],
        },
    },
    {
        "name": "Kortelopet Skate",
        "tab": "Korte Skate",
        "contest": "7",
        "cutoffs": {
            2023: (2025, ["2:04:34", "2:23:23", "2:42:12"]),
            2025: (2026, ["2:00:51", "2:18:16", "2:43:17"]),
            2026: (2027, ["1:48:43", "2:03:40", "2:25:25"]),
        },
        "waves": [
            (800, 899, "Elite Men"),
            (700, 799, "Elite Women"),
            (40000, 40999, "U20"),
            *numbered_waves(40000, 5),
            (70000, 70999, "Wave 70"),
        ],
        "waves_by_year": {
            2020: [
                (50, 199, "Elite Men"),
                (1, 49, "Elite Women"),
                (400, 799, "U20"),
                *numbered_waves(0, 4),
                (70000, 70999, "Wave 70"),
            ],
        },
    },
]


# Dark theme. Colors are from the data-viz reference palette's dark steps and
# were checked with its palette validator against SURFACE.
SURFACE = "#1a1a19"
INK = "#f0efec"  # primary text
SPECIFIC_COLOR = "#e66767"  # the specific-time line; no wave uses red
INK_2 = "#c3c2b7"  # secondary text
MUTED = "#898781"  # axis text; All finishers; Other
GRID = "#2c2c2a"
# Special waves get categorical hues; adjacent legend neighbors pass the checks
SPECIAL_COLORS = {
    "Elite Men": "#d95926",
    "Elite": "#d95926",
    "Elite Women": "#9085e9",
    "U20": "#199e70",
    "Spirit of 35": "#d55181",
    "Wave 70": "#008300",
    "Other": MUTED,
}
# Numbered waves are ordered, so they share one blue ramp, Wave 1 lightest. A
# ramp has visibly distinct steps only up to 9 waves; any after that (the
# Birkie Skate's Wave 10, new skiers) get this instead.
RAMP_MAX = 9
EXTRA_WAVE_COLOR = "#c98500"


def oklch_to_hex(lightness, chroma, hue):
    a, b = chroma * math.cos(math.radians(hue)), chroma * math.sin(math.radians(hue))
    l_ = lightness + 0.3963377774 * a + 0.2158037573 * b
    m_ = lightness - 0.1055613458 * a - 0.0638541728 * b
    s_ = lightness - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_**3, m_**3, s_**3
    rgb = (
        4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
        -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
        -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
    )

    def encode(x):
        x = min(1, max(0, x))
        return round(255 * (12.92 * x if x <= 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055))

    return "#" + "".join(f"{encode(x):02x}" for x in rgb)


def wave_ramp(n):
    """n blues in OKLCH from near-white to deep blue, evenly stepped in lightness."""
    light, dark = (0.97, 0.025, 255), (0.43, 0.12, 258)
    return [
        oklch_to_hex(*(lo + (hi - lo) * (i / (n - 1) if n > 1 else 0) for lo, hi in zip(light, dark)))
        for i in range(n)
    ]


def wave_colors(race):
    """Color for each wave in a race: the same in every year of that race."""
    names = list(dict.fromkeys(name for *_, name in race["waves"]))
    numbered = [n for n in names if n not in SPECIAL_COLORS]
    ramp = wave_ramp(min(len(numbered), RAMP_MAX))
    colors = {n: ramp[i] if i < RAMP_MAX else EXTRA_WAVE_COLOR for i, n in enumerate(numbered)}
    return {**SPECIAL_COLORS, **colors}


def raceresult_records(source, contest):
    lists = source.get("races", {}).get(contest, [(contest, "Result Lists|Overall Results")])
    records = []
    for list_contest, list_name in lists:
        response = requests.get(
            source["base_url"],
            params={
                "key": source["key"],
                "listname": list_name,
                "page": "results",
                "contest": list_contest,
                "l": 10000,
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        fields = data["DataFields"]
        # Rows come grouped by race name, e.g. {"#1_Birkie Skate": [...]}
        groups = data["data"].values() if isinstance(data["data"], dict) else [data["data"]]
        # Full rows only; each group ends with a one-value row-count row
        records += [
            {"_contest": list_contest, "_list": list_name, **dict(zip(fields, row))}
            for group in groups
            for row in group
            if len(row) == len(fields)
        ]
    return records


def runsignup_records(source, contest):
    records = []
    for event_id, result_set_id in source["races"][contest]:
        response = requests.get(
            f"https://runsignup.com/Rest/race/{source['race_id']}/results/get-results",
            params={
                "format": "json",
                "event_id": event_id,
                "individual_result_set_id": result_set_id,
                "results_per_page": 2500,
            },
            timeout=30,
        )
        response.raise_for_status()
        for result_set in response.json()["individual_results_sets"]:
            records += [{"_event_id": event_id, **r} for r in result_set["results"]]
    return records


def pttiming_records(source, contest):
    """Scrape the HTML results table, 100 finishers a page. It has no gender or
    age columns; those are only on each skier's own detail page."""

    def text(cell):
        return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", cell)).split())

    def get_page(page_num, total):
        params = {"page": 1150, "r_page": "division", "divID": source["races"][contest]}
        if page_num:
            params |= {"pageNum_rsOverall": page_num, "totalRows_rsOverall": total}
        response = requests.get(source["url"], params=params, timeout=30)
        response.raise_for_status()
        # The results table is the one with the most rows
        tables = re.findall(r"<table.*?</table>", response.text, flags=re.S | re.I)
        table = max(tables, key=lambda t: t.lower().count("<tr"))
        cells = [
            [text(c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, flags=re.S | re.I)]
            for tr in re.findall(r"<tr.*?</tr>", table, flags=re.S | re.I)
        ]
        # cells[0] is e.g. "50k Birkebeiner Skate Records 1 to 100 of 3862"; cells[1] the headers
        return int(re.search(r"of (\d+)", cells[0][0]).group(1)), cells[1], cells[2:]

    total, header, rows = get_page(0, None)
    for page_num in range(1, math.ceil(total / 100)):
        time.sleep(0.2)  # go easy on the site
        rows += get_page(page_num, total)[2]
    return [dict(zip(header, row)) for row in rows if len(row) == len(header)]


RECORD_FETCHERS = {"raceresult": raceresult_records, "runsignup": runsignup_records, "pttiming": pttiming_records}

# Field names for the bib and finish time in each site's records. raceresult
# Open Track lists only have a chip time.
BIB_FIELDS = ("BIB", "bib", "Bib #")
TIME_FIELDS = ("Finish", "Finish.CHIP", "chip_time", "Finish Time")


# Each race's full results as fetched, every field included (names, ages,
# genders, ...), so builds don't refetch. Personal data: kept out of git by
# .gitignore, and only bibs and times are used.
CACHE_DIR = Path("cache")


def cached_records(year, contest, refresh):
    path = CACHE_DIR / f"{year}-{contest}.json"
    if path.exists() and not refresh:
        records = json.loads(path.read_text(encoding="utf-8"))
        # Older caches held bare (bib, time) pairs; fetch those again
        if all(isinstance(r, dict) for r in records):
            return records
    source = YEARS[year]
    records = RECORD_FETCHERS[source.get("source", "raceresult")](source, contest)
    CACHE_DIR.mkdir(exist_ok=True)
    path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    return records


def finishers(year, contest, refresh=False):
    """(bib, finish time in minutes) for one race, skipping DNF/DNS."""
    results = []
    for record in cached_records(year, contest, refresh):
        bib = str(next(record[f] for f in BIB_FIELDS if f in record))
        text = next((record[f] for f in TIME_FIELDS if record.get(f)), "")
        if (m := to_minutes(text)) is not None and m <= MAX_FINISH_MIN:
            results.append((bib, m))
    return results


def wave_of(bib, waves):
    if bib.isdigit():
        for first, last, name in waves:
            if first <= int(bib) <= last:
                return name
    return "Other"


def hms(minutes):
    seconds = round(minutes * 60)
    return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def to_minutes(text):
    """Minutes from "H:MM:SS", with optional fractional seconds (2:31:46.83) or
    no hours (58:12.5); None for anything else, e.g. a blank or "DNF"."""
    try:
        values = [float(p) for p in text.strip().split(":")]
    except ValueError:
        return None
    if len(values) == 2:
        values.insert(0, 0)
    if len(values) != 3:
        return None
    h, m, s = values
    return h * 60 + m + s / 60


def bin_width(times):
    """Freedman-Diaconis width (2 * IQR / n^(1/3)), rounded up to the next
    width in BIN_WIDTHS; rounding up favors smoother plots."""
    q1, _, q3 = statistics.quantiles(times, n=4)
    ideal = 2 * (q3 - q1) / len(times) ** (1 / 3)
    return next((w for w in BIN_WIDTHS if w >= ideal), BIN_WIDTHS[-1])


def bin_edges(times, width):
    lo = math.floor(min(times) / width) * width
    hi = math.ceil(max(times) / width) * width
    return list(range(lo, hi + 1, width))


def x_ticks(lo, hi):
    """Round tick values across [lo, hi], plus lo itself, where the axes meet.
    A round tick too close to lo is dropped so the labels don't collide."""
    step = next((s for s in (10, 20, 25, 50, 100) if (hi - lo) / s <= 9), 200)
    first = (math.floor(lo / step) + 1) * step
    if first - lo < 0.4 * step:
        first += step
    return [lo, *range(first, hi + 1, step)]


def bin_counts(times, edges):
    # Right-closed bins (a, b], first bin includes its left edge
    counts = [0] * (len(edges) - 1)
    for t in times:
        i = max(math.ceil((t - edges[0]) / (edges[1] - edges[0])) - 1, 0)
        counts[i] += 1
    return counts


def add_group(fig, name, times, edges, color, y_max, visible=True, bar_opacity=0.3):
    """Bars, outline and median for one group, toggled together from the legend."""
    counts = bin_counts(times, edges)
    width = edges[1] - edges[0]
    mids = [a + width / 2 for a in edges[:-1]]
    fig.add_bar(
        x=mids,
        y=counts,
        width=width,
        name=name,
        legendgroup=name,
        visible=visible,
        opacity=bar_opacity,
        marker=dict(color=color, line_width=0),
        customdata=list(zip(edges[:-1], edges[1:])),
        hovertemplate=f"{name}<br>Time Range: %{{customdata[0]}} - %{{customdata[1]}} minutes"
        + "<br>Count: %{y}<extra></extra>",
    )
    # Trim the outline to this group's own range, plus a zero bin each side
    nonzero = [i for i, c in enumerate(counts) if c]
    lo, hi = max(nonzero[0] - 1, 0), nonzero[-1] + 2
    fig.add_scatter(
        x=mids[lo:hi],
        y=counts[lo:hi],
        mode="lines",
        line=dict(color=color, width=2, shape="spline", smoothing=0.6),
        legendgroup=name,
        visible=visible,
        showlegend=False,
        hoverinfo="skip",
    )
    median = statistics.median(times)
    fig.add_scatter(
        x=[median, median],
        y=[0, y_max],
        mode="lines",
        line=dict(color=color, width=1.5, dash="dash"),
        legendgroup=name,
        showlegend=False,
        visible="legendonly",  # shown by the "Show medians" box
        meta="median",
        hovertemplate=f"{name} Median<br>{hms(median)}<extra></extra>",
    )


def add_cutoffs(fig, seeded_year, cutoffs, x_min, x_max, y_max):
    """Next year's wave cutoffs, shown by the "Cutoffs (next year)" box: a
    dotted line at each cutoff, every other wave's time band lightly shaded,
    and each band labelled with its wave number."""
    lines = [to_minutes(text) for text in cutoffs]
    # The last wave has no upper cutoff, so its band runs to the end of the axis
    bounds = [x_min, *lines, max(x_max, lines[-1] + 20)]
    top = y_max * 1.1
    # Shown by default; the "Cutoffs (next year)" box starts ticked
    common = dict(showlegend=False, visible=True, meta="cutoff", hoverinfo="skip")

    shade_x, shade_y = [], []
    for a, b in list(zip(bounds, bounds[1:]))[1::2]:
        shade_x += [a, b, b, a, a, None]
        shade_y += [0, 0, top, top, 0, None]
    fig.add_scatter(
        x=shade_x, y=shade_y, mode="none", fill="toself", fillcolor="rgba(255, 255, 255, 0.04)", **common,
    )

    xs, ys, hover = [], [], []
    for n, (x, text) in enumerate(zip(lines, cutoffs), 1):
        # A midpoint too, so the line can be hovered along its length
        xs += [x, x, x, None]
        ys += [0, top / 2, top, None]
        hover += [f"{seeded_year} Wave {n} cutoff<br>Up to {text}"] * 3 + [""]
    fig.add_scatter(
        x=xs, y=ys, mode="lines", line=dict(color="rgba(240, 239, 236, 0.45)", width=1, dash="dot"),
        **{**common, "hoverinfo": "text"}, hovertext=hover,
    )

    fig.add_scatter(
        x=[(a + b) / 2 for a, b in zip(bounds, bounds[1:])],
        y=[y_max * 1.05] * (len(bounds) - 1),
        mode="text",
        text=[f"W{n}" for n in range(1, len(bounds))],
        textfont=dict(size=11, color=MUTED),
        cliponaxis=False,
        **common,
    )


def wave_groups(race, year, results):
    """Each wave's finish times, in legend order, leaving out empty waves."""
    race_waves = race.get("waves_by_year", {}).get(year, race["waves"])
    waves = {}
    for bib, t in results:
        waves.setdefault(wave_of(bib, race_waves), []).append(t)
    order = dict.fromkeys([*(name for *_, name in race_waves), "Other"])
    return {w: waves[w] for w in order if w in waves}


def wave_plot(race, year, results):
    times = [t for _, t in results]
    waves = wave_groups(race, year, results)

    width = bin_width(times)
    edges = bin_edges(times, width)
    y_max = max(bin_counts(times, edges))

    fig = go.Figure()
    # Lighter bars, so waves added on top stay clear
    add_group(fig, "All finishers", times, edges, MUTED, y_max, bar_opacity=0.18)
    colors = wave_colors(race)
    # Waves start hidden; click one in the legend to add it
    for wave in waves:
        add_group(fig, wave, waves[wave], edges, colors[wave], y_max, visible="legendonly")

    if cutoffs := race.get("cutoffs", {}).get(year):
        seeded_year, cutoff_times = cutoffs
        add_cutoffs(fig, seeded_year, cutoff_times, edges[0], edges[-1], y_max)

    # Placed by the "Specific finish time" box
    fig.add_scatter(
        x=[None, None],
        y=[0, y_max],
        mode="lines",
        line=dict(color=SPECIFIC_COLOR, width=2.5),
        name="Specific time",
        legendgroup="Specific time",
        meta="specific",
        visible="legendonly",  # shown by the "Specific finish time" box
    )
    axis = dict(gridcolor=GRID, zeroline=False, showline=False, ticks="", title_font_color=MUTED)
    fig.update_layout(
        template="plotly_dark",
        barmode="overlay",
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        font=dict(family='system-ui, -apple-system, "Segoe UI", sans-serif', size=12, color=INK_2),
        # No vertical grid: it would compete with the cutoff and marker lines.
        # The range is fixed to the bins, so the scale stays put as waves are
        # shown and hidden, and the first tick sits where the axes meet.
        xaxis=dict(
            axis,
            title="Finish time (minutes)",
            showgrid=False,
            range=[edges[0], edges[-1]],
            tickvals=x_ticks(edges[0], edges[-1]),
        ),
        # rangemode hides where a smoothed outline dips below zero
        yaxis=dict(axis, title=f"Skiers per {width} min", rangemode="tozero"),
        # Tight enough for the Birkie Skate's 16 entries in a 400px plot
        legend=dict(tracegroupgap=0, font=dict(color=INK_2, size=11)),
        hoverlabel=dict(bgcolor="#262624", bordercolor=GRID, font_color=INK),
        # Room above the plot for the finisher count
        margin=dict(l=56, r=8, t=28, b=48),
        annotations=[
            dict(
                text=f"{len(times):,} finishers",
                xref="paper", yref="paper", x=1, y=1, xanchor="right", yanchor="bottom", yshift=6,
                showarrow=False, font=dict(size=12, color=INK_2),
            )
        ],
        height=520,
    )
    return fig


# Runs after the plot is drawn; {plot_id} and FINISH_TIMES are filled in by
# build_section. FINISH_TIMES maps "All finishers" and each wave to its sorted
# times, and the "Rank among" menu picks which one the slider and place use.
PLOT_SCRIPT = """
// The controls sit below the plot, so wait until they exist
function initControls() {
  const gd = document.getElementById('{plot_id}');
  const section = gd.closest('section');
  const groups = FINISH_TIMES;
  const group = section.querySelector('.specific-group');
  let times = groups[group.value];
  const showMedians = section.querySelector('.show-medians');
  const showCutoffs = section.querySelector('.show-cutoffs');
  const input = section.querySelector('.specific-time');
  const showSpecific = section.querySelector('.show-specific');
  const slider = section.querySelector('.specific-pct');
  const place = section.querySelector('.place');
  const specific = gd.data.findIndex(t => t.meta === 'specific');

  // A median shows only when its box is ticked and its group is on in the
  // legend; cutoffs follow their box alone, whatever the legend does
  function syncToggles() {
    const idx = [], vis = [];
    gd.data.forEach((t, i) => {
      let want;
      if (t.meta === 'median') {
        const bar = gd.data.find(b => b.type === 'bar' && b.legendgroup === t.legendgroup);
        want = showMedians.checked && bar.visible !== 'legendonly';
      } else if (t.meta === 'cutoff') {
        want = showCutoffs.checked;
      } else {
        return;
      }
      if ((t.visible === true) !== want) {
        idx.push(i);
        vis.push(want ? true : t.meta === 'median' ? 'legendonly' : false);
      }
    });
    if (idx.length) Plotly.restyle(gd, {visible: vis}, idx);
  }

  // Accepts H:MM:SS or H:MM
  function parseTime(s) {
    const p = s.trim().split(':').map(Number);
    if (p.length < 2 || p.length > 3 || p.some(x => !Number.isFinite(x) || x < 0)) return null;
    return p[0] * 60 + p[1] + (p[2] || 0) / 60;
  }

  function formatTime(minutes) {
    const s = Math.round(minutes * 60);
    return `${Math.floor(s / 3600)}:${String(Math.floor(s / 60) % 60).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
  }

  // The finish time at a percentile: the time of the finisher p% of the way
  // through the field, fastest first
  function timeAtPercentile(p) {
    return times[Math.min(times.length - 1, Math.max(0, Math.ceil(p / 100 * times.length) - 1))];
  }

  // Draws the time box's time and describes it; returns its percentile
  function updateSpecific() {
    const m = parseTime(input.value);
    if (m === null) {
      Plotly.restyle(gd, {x: [[null, null]]}, [specific]);
      place.textContent = 'Enter a finish time (H:MM:SS) to see where it falls.';
      return null;
    }
    const label = input.value.trim();
    Plotly.restyle(gd, {x: [[m, m]], hovertemplate: `Specific time<br>${label}<extra></extra>`}, [specific]);
    const ahead = times.filter(t => t < m).length;
    const behind = times.filter(t => t > m).length;
    // Percentile = share of finishers at or faster than this time (lower is faster)
    const pct = Math.round(100 * (times.length - behind) / times.length);
    const suffix = pct % 100 >= 11 && pct % 100 <= 13 ? 'th' : {1: 'st', 2: 'nd', 3: 'rd'}[pct % 10] || 'th';
    const among = group.value === 'All finishers' ? 'finishers' : `${group.value} finishers`;
    place.textContent = `${label} places ${Math.min(ahead + 1, times.length).toLocaleString('en-US')} of ${times.length.toLocaleString('en-US')} ${among}, in the ${pct}${suffix} percentile.`;
    return pct;
  }

  // Wide screens: the plot takes the window height left after the header and
  // the controls below it, within limits, so it's fully visible without
  // scrolling. Narrow screens: the legend moves below the plot, and the page
  // scrolls instead.
  function fitLayout() {
    if (!gd.clientWidth) return;  // hidden tab
    const narrow = gd.clientWidth < 640;
    let height = 720;
    if (!narrow) {
      const top = gd.getBoundingClientRect().top + scrollY;
      const below = section.getBoundingClientRect().bottom - gd.getBoundingClientRect().bottom;
      // 400px still fits the Birkie Skate's 16-entry legend
      height = Math.round(Math.min(560, Math.max(400, innerHeight - top - below - 12)));
    }
    const legendMoves = narrow !== (gd.layout.legend.orientation === 'h');
    if (!legendMoves && Math.abs(height - gd.layout.height) < 4) return;
    // plotly's wrapper div has a fixed height too
    gd.style.height = gd.parentElement.style.height = height + 'px';
    // Narrow: the legend's top sits 60px into the bottom margin, under the axis title
    Plotly.relayout(gd, narrow
      ? {height, 'margin.b': 200, 'legend.orientation': 'h', 'legend.x': 0, 'legend.xanchor': 'left',
         'legend.yref': 'container', 'legend.y': (200 - 60) / height, 'legend.yanchor': 'top'}
      : {height, 'margin.b': 48, 'legend.orientation': 'v', 'legend.x': 1.02, 'legend.xanchor': 'left',
         'legend.yref': 'paper', 'legend.y': 1, 'legend.yanchor': 'auto'});
  }
  // Deferred a frame, since fitLayout itself resizes the plot. The observer
  // also fires when a hidden tab is shown.
  new ResizeObserver(() => requestAnimationFrame(fitLayout)).observe(gd);
  addEventListener('resize', () => requestAnimationFrame(fitLayout));

  // No "0" on the y axis, where it would sit beside the first x label.
  // Plotly redraws ticks on every change, so hide it after each draw.
  function hideZeroTick() {
    for (const t of gd.querySelectorAll('.ytick text')) t.style.display = t.textContent === '0' ? 'none' : '';
  }
  gd.on('plotly_afterplot', hideZeroTick);
  hideZeroTick();

  gd.on('plotly_restyle', syncToggles);
  showMedians.addEventListener('change', syncToggles);
  if (showCutoffs) showCutoffs.addEventListener('change', syncToggles);
  // The specific-time line shows when its box is ticked; moving the slider or
  // typing a time ticks it, and clicking the line's legend entry keeps the
  // box in step
  function setSpecificVisible(on) {
    showSpecific.checked = on;
    if ((gd.data[specific].visible === true) !== on) Plotly.restyle(gd, {visible: on ? true : 'legendonly'}, [specific]);
  }
  showSpecific.addEventListener('change', () => setSpecificVisible(showSpecific.checked));
  gd.on('plotly_restyle', () => { showSpecific.checked = gd.data[specific].visible === true; });
  // The slider picks a time by percentile; a typed time overrides it, and the
  // slider then moves to that time's percentile
  slider.addEventListener('input', () => {
    input.value = formatTime(timeAtPercentile(+slider.value));
    updateSpecific();
    setSpecificVisible(true);
  });
  input.addEventListener('input', () => {
    const pct = updateSpecific();
    if (pct !== null) {
      slider.value = pct;
      setSpecificVisible(true);
    }
  });
  // Changing the group keeps the time and ranks it within the new group
  group.addEventListener('change', () => {
    times = groups[group.value];
    const pct = updateSpecific();
    if (pct !== null) slider.value = pct;
  });
  input.value = formatTime(timeAtPercentile(+slider.value));
  updateSpecific();
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initControls);
else initControls();
"""


def tab_id(race):
    return race["tab"].lower().replace(" ", "-")


def build_section(race, year, results, first):
    groups = {"All finishers": [t for _, t in results], **wave_groups(race, year, results)}
    groups = {name: sorted(round(t, 3) for t in ts) for name, ts in groups.items()}
    group_options = "".join(f"<option>{name}</option>" for name in groups)
    plot = wave_plot(race, year, results).to_html(
        full_html=False,
        # Inline plotly.js once, in the first plot, so the page stands alone
        include_plotlyjs="inline" if first else False,
        config={"displayModeBar": False},
        post_script=PLOT_SCRIPT.replace("FINISH_TIMES", json.dumps(groups)),
    )
    cutoffs_box = ""
    if race.get("cutoffs", {}).get(year):
        cutoffs_box = '<label><input type="checkbox" class="show-cutoffs" checked> Cutoffs (next year)</label>'
    note = YEARS[year].get("note")
    note_html = f'<p class="note">{note}</p>' if note else ""
    return f"""<section id="{tab_id(race)}-{year}" role="tabpanel" aria-label="{race['tab']} {year}">
{note_html}
<div class="controls">
<label><input type="checkbox" class="show-medians"> Show medians</label>
{cutoffs_box}
</div>
{plot}
<div class="controls">
<label><input type="checkbox" class="show-specific"> Specific finish time</label>
<input type="range" class="specific-pct" min="0" max="100" step="1" value="25" aria-label="Percentile, fastest first">
<input class="specific-time" placeholder="H:MM:SS" inputmode="numeric" aria-label="Specific finish time">
<label>Rank among <select class="specific-group">{group_options}</select></label>
<span class="place"></span>
</div>
</section>"""


# Shows the section for the chosen race and year; the URL hash (e.g.
# #birkie-skate-2025) picks both
TABS_SCRIPT = """
const raceTabs = [...document.querySelectorAll('[data-race]')];
const yearTabs = [...document.querySelectorAll('[data-year]')];
let race = raceTabs[0].dataset.race;
let year = yearTabs[0].dataset.year;

function show(updateHash) {
  for (const t of raceTabs) t.setAttribute('aria-selected', t.dataset.race === race);
  for (const t of yearTabs) t.setAttribute('aria-selected', t.dataset.year === year);
  const id = `${race}-${year}`;
  for (const s of document.querySelectorAll('section[role=tabpanel]')) s.hidden = s.id !== id;
  // A plot drawn or resized while hidden has the wrong width
  Plotly.Plots.resize(document.querySelector(`#${id} .plotly-graph-div`));
  if (updateHash) history.replaceState(null, '', '#' + id);
}
for (const t of raceTabs) t.addEventListener('click', () => { race = t.dataset.race; show(true); });
for (const t of yearTabs) t.addEventListener('click', () => { year = t.dataset.year; show(true); });

const fromHash = location.hash.match(/^#(.+)-(\\d{4})$/);
if (fromHash && document.getElementById(fromHash[0].slice(1))) [, race, year] = fromHash;
show(false);
// Opening a tab link would scroll to its section, past the header; the
// browser does that once the page loads
if (fromHash) addEventListener('load', () => requestAnimationFrame(() => scrollTo(0, 0)));
"""


def build_page(sections):
    race_tabs = "\n".join(
        f'<button role="tab" data-race="{tab_id(race)}">{race["tab"]}</button>' for race in EVENTS
    )
    # Most recent year first
    year_tabs = "\n".join(
        f'<button role="tab" data-year="{year}">{year}</button>' for year in sorted(YEARS, reverse=True)
    )
    built = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Birkie Plots</title>
<style>
:root {{
  color-scheme: dark;
  --bg: {SURFACE}; --ink: {INK}; --ink-2: {INK_2}; --muted: {MUTED};
  --line: rgba(255, 255, 255, 0.10); --line-hover: rgba(255, 255, 255, 0.24);
  --field: #232322; --accent: #86b6ef;
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; -webkit-font-smoothing: antialiased; }}
main {{ max-width: 64rem; margin: 0 auto; padding: 1rem 1rem 2rem; }}
/* Title and race tabs share one row, over a single rule */
.top {{ display: flex; flex-wrap: wrap; align-items: flex-end; column-gap: 1.5rem;
  border-bottom: 1px solid var(--line); margin-bottom: 0.75rem; }}
h1 {{ font-size: 1.25rem; font-weight: 650; letter-spacing: -0.01em; margin: 0; padding: 0.55rem 0; }}
[role=tablist] {{ display: flex; flex-wrap: wrap; gap: 0.25rem; }}
[role=tab] {{ font: inherit; color: var(--muted); background: none; border: 0; cursor: pointer;
  transition: color 0.12s, background-color 0.12s, border-color 0.12s; }}
.races {{ margin: 0; }}
.races [role=tab] {{ padding: 0.6rem 0.9rem; margin-bottom: -1px; border-bottom: 2px solid transparent; font-weight: 500; }}
.races [role=tab]:hover {{ color: var(--ink-2); }}
.races [role=tab][aria-selected=true] {{ color: var(--ink); border-bottom-color: var(--ink); }}
.years {{ gap: 0.375rem; margin-bottom: 0.75rem; }}
.years [role=tab] {{ padding: 0.2rem 0.75rem; border: 1px solid var(--line); border-radius: 999px;
  font-size: 0.85rem; font-variant-numeric: tabular-nums; }}
.years [role=tab]:hover {{ color: var(--ink); border-color: var(--line-hover); }}
.years [role=tab][aria-selected=true] {{ background: var(--ink); border-color: var(--ink); color: var(--bg); }}
[role=tab]:focus-visible, input:focus-visible, select:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
section:not([hidden]) {{ animation: enter 0.18s ease-out; }}
@keyframes enter {{ from {{ opacity: 0; transform: translateY(4px); }} }}
@media (prefers-reduced-motion: reduce) {{
  section:not([hidden]) {{ animation: none; }}
  [role=tab] {{ transition: none; }}
}}
.controls {{ display: flex; flex-wrap: wrap; gap: 0.5rem 1.25rem; align-items: center; color: var(--ink-2); font-size: 0.9rem; }}
.controls label {{ display: inline-flex; align-items: center; gap: 0.45rem; cursor: pointer; }}
input[type=checkbox] {{ accent-color: var(--accent); width: 1rem; height: 1rem; margin: 0; }}
.specific-time {{ font: inherit; font-variant-numeric: tabular-nums; width: 6.5em; padding: 0.35rem 0.6rem;
  color: var(--ink); background: var(--field); border: 1px solid var(--line); border-radius: 0.4rem;
  transition: border-color 0.12s; }}
.specific-group {{ font: inherit; padding: 0.3rem 0.4rem; color: var(--ink); background: var(--field);
  border: 1px solid var(--line); border-radius: 0.4rem; cursor: pointer; transition: border-color 0.12s; }}
.specific-group:hover {{ border-color: var(--line-hover); }}
.specific-time:hover {{ border-color: var(--line-hover); }}
.specific-pct {{ accent-color: var(--accent); width: 12rem; max-width: 100%; margin: 0; cursor: pointer; }}
.place {{ color: var(--ink-2); }}
.note {{ margin: 0 0 0.75rem; padding: 0.45rem 0.75rem; color: var(--ink-2); font-size: 0.875rem;
  background: rgba(255, 255, 255, 0.04); border-left: 2px solid var(--muted); border-radius: 0.25rem; }}
footer {{ margin-top: 2rem; color: var(--muted); font-size: 0.8rem; }}
footer a {{ color: var(--accent); text-decoration: none; }}
footer a:hover {{ text-decoration: underline; }}
@media (max-width: 640px) {{
  /* Race tabs stay on one row and scroll sideways */
  .races {{ width: 100%; flex-wrap: nowrap; overflow-x: auto; scrollbar-width: none; }}
  .races [role=tab] {{ white-space: nowrap; padding: 0.6rem 0.7rem; }}
}}
</style>
</head>
<body>
<main>
<header class="top">
<h1>Birkie Finish Times</h1>
<div role="tablist" aria-label="Race" class="races">
{race_tabs}
</div>
</header>
<div role="tablist" aria-label="Year" class="years">
{year_tabs}
</div>
{"".join(sections)}
<footer>Click a legend entry to show or hide it; double-click to show only that one. Built {built} &middot;
<a href="{REPO_URL}">Source on GitHub</a></footer>
</main>
<script>{TABS_SCRIPT}</script>
</body>
</html>
"""


def main():
    refresh = "--refresh" in sys.argv[1:]
    sections = [
        build_section(race, year, finishers(year, race["contest"], refresh), first=not (i or j))
        for i, race in enumerate(EVENTS)
        for j, year in enumerate(YEARS)
    ]
    page = build_page(sections)

    output_dir = Path(os.environ.get("OUTPUT_DIR", "docs"))
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "index.html"
    output_file.write_text(page, encoding="utf-8")
    # Tells GitHub Pages to serve the folder as-is, without a Jekyll build
    (output_dir / ".nojekyll").touch()
    print(f"Saved {output_file}")


if __name__ == "__main__":
    main()
