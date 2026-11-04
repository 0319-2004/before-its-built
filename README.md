# Before It's Built

**Snap the construction notice board on a Japanese building site, and an AI agent reads it, checks it against the city's open filing data, and raises the building that's *about to be built* in 3D on top of Japan's PLATEAU city model — then shows you its winter shadow, including how long your own home would sit in it.**

> 🔗 **Live:** https://0319-2004.github.io/before-its-built/
>
> You can't photograph a Japanese sign from the judging room, so **try the pre-computed sample**:
> - **Sample mode** (watch the agent's decisions, then a building rises): https://0319-2004.github.io/before-its-built/?mode=sample
> - **Planned buildings + winter shadows** (tap the map for "your house"): https://0319-2004.github.io/before-its-built/?stage=1&shadow=1
> - **+ buildings about to be demolished** (orange): https://0319-2004.github.io/before-its-built/?stage=2&shadow=1
>
> Sample mode calls **no model** — the agent runs were computed ahead of time and saved, so it's safe and instant to open.

Built from scratch during the **Meta Global AI Developer Hackathon** (Oct 27 – Nov 4, 2026). Developed with **Claude Code**.

---

## The pain

The earliest, most accurate signal that a city is about to change is a sign bolted to a fence — and it's just numbers. "Height 25 m, 9 storeys" means nothing until it's already casting a shadow on your window.

| Who | Today | With this |
|---|---|---|
| Neighbours | Learn about sunlight/view loss at the briefing or after work starts | See the size and shadow from their own home, beforehand |
| Home buyers/renters | A building appears across the street after they sign | See what's coming around a property |
| City / schools | Population spikes show up late | Add up the planned dwellings in advance |
| 3D city models | Refresh every few years; the changing places go stale fastest | Build a "future PLATEAU" from pre-construction figures |

## How it works

```
 photo of the board
        │
        ▼
   SAM 3.1 ───────────▶ crop the board out of the photo (full-res; whole photo if none found)
        │
        ▼
  Muse Spark 1.3  ◀───▶ an AGENT loop: the model picks the next tool
        │               read → cross-check numbers → re-read weak fields →
        │               look up the city filing → geocode → extrude → finish
        ▼
   PLATEAU 3D Tiles ──▶ the planned building (blue) rises on the real city model,
                        with a winter-solstice shadow study
```

The agent (`pipeline/agent.py`) is the centre of the project: **the model decides what to do next**, calling tools that wrap the pipeline. The read is the model's; **the checking is code's**. The model never compares numbers — `lookup_filing` normalises both sides and returns the per-field agreement itself, so the screen and the evaluation can't drift apart.

### Guardrails that live in code, not in the prompt

The more we trust the model to decide, the more we pin down in code the things it must *not* decide:

- **It can't invent digits.** If a value is blurred, the field is `unreadable`; back-filling a plausible number from the floor count is forbidden. Numeric cross-checks can't catch a *consistent* lie, so `extrude()` also refuses to raise a building when the height or footprint confidence is below `0.9`.
- **It can't pass the wrong height to 3D.** `extrude()` rejects the eave height (a mistake we made once) and anything that isn't the display height.
- **It can't "eyeball" a match.** `lookup_filing` computes field-by-field agreement with the same rules as the evaluator.

### Decisions worth watching

- **IMG_3928 — self-correcting a misread.** The board's address was read as "3-chōme **1**-ban", but it's "3-chōme **17**-ban" (the number was crammed into the land-number field). The agent matched the filing by **land number**, not the address, so the misread never mattered — and the filing confirms 17-ban, consistent with the building's own name.
- **IMG_3966 — refusing to build.** The height was crushed in the photo; the model read "11.985 m" against 9 storeys with low confidence, and a re-read didn't help. Rather than invent a tidier figure (an earlier run wrote "31.985 m because it's 9 storeys" — which *passes* the numeric check), it left the value unreadable and **did not raise a building**.
- **IMG_3997 / No.55 — catching a stale city model.** From the far site photo the agent's `check_site` tool read the lot as *under construction*, while PLATEAU (2021) still carries buildings there — so it flagged **"PLATEAU not reflected"**. This round, all **3 near/far photo pairs** came back the same way: the real site is already being built while the 3D model shows the old block.
- **No.60 — the board correcting the filing.** Three independent photos (IMG_3989/3990/3993) all read the street block as "**1**-ban"; the filing reading says "19-ban". With three boards agreeing, the discrepancy is counted as a **likely filing-side misread** (not a sign misread, not an undecided disagreement), pending visual check of the filing scan.

## Evaluation (provisional)

`pipeline/evaluate.py` scores the agent's output (`data/agent/*.json`) against Meguro City's filings. Summary: [`docs/eval-summary.md`](docs/eval-summary.md).

**The filings themselves are read by the model and have not yet been visually verified, so the "match rate" below is a _model-vs-model agreement rate_, and every number is provisional.** Mismatches are split so the headline isn't punished by things that aren't reading errors:

| of 26 photos | |
|---|---|
| matched a filing | 9 |
| raised in 3D | 11 |
| not a construction notice (labour-insurance / permit boards) | 11 |
| not built — unreadable value, or not in the mid-rise list | 6 |

On the 9 matched photos: **146 / 146 field match (100 %), 0 misreads** — again, this is model-vs-model agreement until the filings are human-verified. Discrepancies are kept out of that rate and counted separately: **1 real sign-vs-filing disagreement** (a 71.87 vs 70.32 m² site area the agent surfaced rather than silently picking one) and **3 likely filing-side misreads** (the No.60 block number above). Separately, **`check_site` flagged "PLATEAU not reflected" on 3 near/far pairs** — real sites already under construction that the 2021 model still shows as standing.

### Known limits (stated honestly)

- **Mid-rise only** — the input is the standard mid/high-rise notice board; buildings over 10,000 m² total floor area aren't in the city's mid-rise list.
- **Shape is approximate** — an area-equal square facing north at the block's reference point; not yet aligned to the nearest road, and the exact spot within the block is unknown.
- **Filings unverified** — the "ground truth" is itself a model reading until a human confirms it; numbers are provisional.
- **Small sample so far** — 26 photos evaluated.
- **Demolition is a ranked guess** — the notice gives no coordinate, so the demolished building is the top candidate in the block, shown faintly when it can't be pinned to one.
- **PLATEAU is from 2021** — a cleared lot still shows its old building. We show this rather than hide it: it *is* the "city models go stale" problem.
- **The shadow is a guide, not a legal ruling** — winter solstice, cuboid approximation, measured 1.5 m above ground; the UI says so.

## Shadow study

Planned buildings cast a **winter-solstice shadow** computed on a plane 1.5 m above ground (NAVARA's real-time shadows only land on 3D geometry, so a cleared lot would show nothing). A time slider (08:00–16:00 apparent solar time) sweeps the shadow and drives the scene's lighting to match. **Tap the map** and it reports how long that point would sit in a planned building's shadow — "your house". Details: [`docs/shadow.md`](docs/shadow.md).

## Privacy

No personal names are extracted, stored, or shown. The names, addresses, and phone numbers of owners, architects, and builders printed on the boards are never read or written. Photos and filing PDFs stay out of this repository; building names (which can contain personal names) are withheld from everything published.

## Meta models used

- **SAM 3.1** — detect and crop the board from the photo.
- **Muse Spark 1.3** — read the board and drive the agent loop (`store: false`; the `-contributor`-free model IDs, because the photos contain personal names).

## Third-party libraries

- **Frontend 3D:** [NAVARA](https://www.npmjs.com/package/@navaramap/three) (`@navaramap/three` 0.2.1) + `@navaramap/three-default-plugin`, [three.js](https://threejs.org/), `postprocessing`. Basemap raster: © OpenStreetMap contributors.
- **Pipeline (Python):** OpenAI SDK (pointed at `https://api.meta.ai/v1`), `python-dotenv`, `pillow`, `pypdfium2`.
- **Build/test:** Vite, Playwright.

## Data sources and licenses

- **3D city model — Project PLATEAU** (MLIT, Japan). Official 3D Tiles distribution, Meguro 2025 series, used for display; CityGML converted to GeoJSON for server-side calculation. Attribution: "3D都市モデル（Project PLATEAU）を加工して作成".
- **Meguro City open data** — mid/high-rise construction notice filings, and the demolition-notice register. **CC BY 4.0**.
- **Location reference information** (block-level and chō-level, Meguro 2025), MLIT/GSI — free to use with attribution; maps addresses to block reference points.

## Repository

```
pipeline/   Python. SAM 3.1 crop → Muse Spark read + code cross-checks → agent loop → GeoJSON
web/        NAVARA + PLATEAU official 3D Tiles; planned (blue), demolition (orange), shadow study
docs/       dev-policy.md (the single source of decisions), stage/shadow/eval notes
```

Development policy and every design decision live in [`docs/dev-policy.md`](docs/dev-policy.md). `data/`, `samples/`, and `.env` are never committed.
