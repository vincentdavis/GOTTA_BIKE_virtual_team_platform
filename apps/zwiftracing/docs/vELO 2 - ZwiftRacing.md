---
title: vELO 2 - ZwiftRacing
source: "https://www.zwiftracing.app/reference/velo2"
saved: 2026-07-01
---

# vELO 2 - ZwiftRacing

## General

### What is vELO 2?

vELO 2 is an enhancement to the vELO rating system that breaks down a rider's performance into specialized factors. Unlike the original vELO system which provides a single overall rating, vELO 2 analyzes different aspects of a rider's power profile to better understand their strengths across various race scenarios.

The system evaluates riders across six factors: Sprint, Punch, Climb, Time Trial Speed, Endurance, and Pursuit. Each factor measures different power characteristics and durations, allowing for a more nuanced assessment of a rider's capabilities. These factors are combined into two main ratings—Race (for drafting events) and Time Trial (for non-drafting events)—giving a comprehensive view of a rider's power profile.

By breaking down performance into these specialized factors, vELO 2 enables more accurate race categorization and better matching of riders to events that suit their strengths. The system recognizes that a rider who excels at sprinting may have different capabilities than one who excels at climbing, and adjusts event ratings accordingly.

### What's the difference between vELO 1 and vELO 2?

**vELO 1** is a single overall rating used for categorization and handicapping. **vELO 2** adds six underlying factors (Sprint, Punch, Climb, Time Trial Speed, Endurance, Pursuit) and two top-level ratings (Race and Time Trial) that are blended per event based on route and distance.

You can switch between vELO 1 and vELO 2 on result pages, rider pages, and favorites. Your choice is remembered for the session. Use vELO 2 when you want to see factor breakdowns and how you perform across different types of efforts.

### How does vELO 2 work?

For each event, vELO 2 uses route characteristics and distance to decide which factors apply and what percentage each contributes. Your Race or Time Trial rating is blended from those factors to produce an event rating; after the race, your delta is spread back across the same factors. See the 'How it works' section below for step-by-step examples.

### Is vELO 2 result based?

Yes. vELO 2 is result based. Each factor updates independently depending on the route and distance of the race, so ratings evolve from actual race performance.

### Where can I see my vELO 2 ratings?

vELO 2 appears wherever rider ratings are shown when you select the vELO 2 option:

-   **Result pages** — After a race, use the vELO 1 / vELO 2 toggle to see Race and Time Trial ratings and factor columns.
    
-   **Rider pages** — On a rider's profile, open the vELO 2 tab to see their Race and Time Trial ratings, factors, and 90-day history charts.
    
-   **Favorites** — From your favorites list, use the vELO / vELO 2 toggle to view ratings in vELO 2 format.
    

### What is the seed?

The **seed** is the rating value used as the starting point for a factor (or for Race/Time Trial) before result-based updates. It is derived from your power data and represents the system's prior estimate of your ability in that dimension.

On rider vELO 2 history charts, you can turn on "Show Seed" to see the seed line alongside your result-based rating over the last 90 days. Seeds update as new power or result data is incorporated.

### What does the 90-day history show?

The 90-day history chart on rider vELO 2 pages shows your Race and Time Trial ratings (and optionally the seed) over your most recent events in the last 90 days. You can switch the x-axis to "By date" (time spacing) or "Per event" (equal spacing per race) for easier reading. The factor history view shows the same timeline for a single factor of your choice.

### What power data is used to calculate the factors?

Power data available from Zwift:

15 second watts/wkg

1 minute watts/wkg

5 minute watts/wkg

20 minute watts/wkg

Power data available from Zwift Power (for opted-in users):

5 second watts/wkg

30 second watts/wkg

2 minute watts/wkg

### What's the current status of vELO 2?

vELO 2 is live. Race and Time Trial ratings, all six factors, event rating blending, and result-based factor updates are in place. You can view vELO 2 on result pages, rider profiles, and favorites.

We continue to refine how route and distance drive factor weights and may incorporate richer route data (e.g. GPX) and historical result patterns in future updates.

## How it works

### How does vELO 2 determine which factors to use for an event?

For each event, vELO 2 uses the route characteristics and distance to determine which factors should be utilized and by what percentage. This allows the system to create a tailored event rating input that reflects the specific demands of each race. The value for each rating factor will update after each race where it is used to calculate the event rating input, ensuring that ratings evolve based on actual race performance.

### How is the event rating calculated?

###### Example (made up for illustration)

Player

Endurance: 500

Sprint: 600

Punch: 700

Climb: 400

---

Event

8 laps of 'Downtown Dolphin' in Crit City that uses the Sprint, Punch and Endurance factors:

Sprint: 25%

Punch: 25%

Endurance: 50%

---

Calculation

vELO2 multiplies each factor by its percentage and sums them:

Event Rating = 150 + 175 + 250 = 575

### How are my ratings updated?

After the race, the vELO delta (change in rating) is spread amongst the input factors based on the same percentages used to calculate the event rating.

###### Continuing the example

---

Delta Distribution

The delta is spread across the factors used in the event:

---

## Default Ratings

### What is the Race rating?

Race is the default rating used for races where drafting is enabled. It is the combined rating used as a starting point for non time trial race events and will be adjusted based on the specific route characteristics and distance of each event.

### What is the Time Trial rating?

Time Trial is the default rating used for non-drafting time trial races. It is a combination of the Endurance and Time Trial Speed factors, providing an assessment of a rider's ability to perform in solo efforts where drafting is not available.

## Individual Factors

### What is the Sprint factor?

What it represents

Sprint measures a rider's peak explosive power over very short durations. It reflects the ability to produce maximal power in a finishing sprint or sharp acceleration.

What influences it

5s watts/wkg, 15s watts/wkg, 30s watts/wkg

Why it matters

Sprint determines who wins once riders arrive at the line together.

### What is the Punch factor?

What it represents

Punch measures a rider's ability to produce short, high-power efforts above threshold. It reflects how well a rider can respond to attacks, accelerate over rollers, and survive aggressive race dynamics.

What influences it

1m watts/wkg, 2m watts/wkg

Why it matters

Punch decides who survives selections and makes the final group in fast, dynamic races.

### What is the Climb factor?

What it represents

Climb measures a rider's ability to produce strong power relative to body weight over sustained uphill efforts. It reflects effectiveness on longer gradients where gravity dominates.

What influences it

5m wkg, 20m wkg

Why it matters

Climb determines performance on sustained hills and mountain stages where drafting offers limited benefit.

### What is the Time Trial Speed factor?

What it represents

Time Trial measures a rider's ability to produce steady, efficient power without drafting. It reflects solo performance, pacing discipline, and aerodynamic efficiency.

What influences it

FTP, height, weight

Why it matters

Time Trial performance defines solo efforts, breakaways, and race formats where drafting is limited or absent.

### What is the Endurance factor?

What it represents

Endurance measures a rider's ability to sustain strong power over the full duration of a race. It reflects how well a rider can stay with the pack, handle repeated efforts, and maintain performance deep into an event.

Why it matters

Endurance determines whether a rider is still competitive when the race actually gets decided.

### What is the Pursuit factor?

What it represents

Pursuit measures a rider's ability to maintain high power over medium durations. It reflects sustained efforts that bridge the gap between sprint and endurance.

What influences it

5m watts/wkg

Why it matters

Pursuit determines performance in breakaways and sustained high-power efforts.
