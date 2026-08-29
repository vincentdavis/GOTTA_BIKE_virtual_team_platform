# Accessibility

Working plan for bringing the platform to **WCAG 2.2 Level AA**.

This is a live document. Phase 0 and Phase 1 are done once; Phase 2 is worked one page at a
time, and each page gets ticked off in the table below.

## Why WCAG 2.2 AA

AA is the level every regulation actually references — EN 301 549 (EU), ADA Title II (US, now
with a fixed compliance deadline for public entities), and AODA (Ontario) all point at WCAG AA.
AAA is not a realistic target for an app like this and nobody asks for it.

Version 2.2 rather than 2.1 matters here specifically, because three of the criteria it added
land directly on features this site already has:

| 2.2 criterion | Where it bites |
|---|---|
| 2.5.7 Dragging Movements (AA) | The availability grid is painted by click-and-drag |
| 2.5.8 Target Size (Minimum) (AA) | `btn-xs` icon buttons in dense tables |
| 2.4.11 Focus Not Obscured (AA) | Sticky header at `z-50` over a scrolled page |

## What the audit found

A seven-dimension audit of the whole template surface (159 templates, plus the shared CSS and
JS) produced **123 findings**, each re-checked against the file by a second pass:

| | blocker | serious | moderate | minor |
|---|---|---|---|---|
| **count** | 9 | 45 | 45 | 24 |

"Blocker" means a keyboard or screen-reader user cannot complete the task at all.

**Method and its limits.** This was static analysis of templates, CSS and JS — no browser, no
screen reader, no axe run. Contrast ratios were computed from the daisyUI theme tokens where the
hex values could be derived and estimated otherwise; the doc says which. Nothing was verified
against the rendered DOM, so a handful of findings will turn out to be wrong once Phase 1's
tooling is in place. The second pass downgraded 37 findings and refuted none, which is itself a
mild warning that it was lenient — treat severities as a starting order, not gospel.

### The nine blockers reduce to four root causes

1. **The availability grid cannot be operated from a keyboard at all.** Cells are bare `<td>`
   elements with pointer-only handlers, in all three grid templates
   (`availability_respond.html`, `availability_builder.html`, `availability_results.html`).
   A rider who cannot use a mouse cannot answer an availability sheet — and answering one is a
   core member task. Painting is also drag-only, which fails 2.5.7 on its own.
2. **The shared searchable select (`templates/events/_filter_select_script.html`) is keyboard-dead.**
   It hides the native `<select>` with `display:none`, which removes it from both the tab order
   and the accessibility tree, then replaces it with an unlabelled `<input>` and a list of
   `<div>` rows that respond only to `mousedown`. There is no `keydown` handler anywhere in the
   file. A keyboard user gets an unnamed text box that opens a list they cannot select from.
   It is used on the squad form, event form, TTT planner, ladder planner, and the Compliance
   page — every one of which had a perfectly accessible native `<select>` before enhancement.
3. **`.zsl-chart`'s "fixed dark palette" is not actually fixed.** `chart.css` paints only two
   boxes, so the remaining chart chrome renders `#e6edf3` on a white card in the light theme.
   The claim in CLAUDE.md that it "stays legible in any DaisyUI theme" is wrong and should be
   corrected when this is fixed.
4. **Availability selection is conveyed by fill colour alone**, at 1.35:1 in the light theme —
   below even the 3:1 non-text minimum, so it is close to invisible to anyone, not just to
   colour-blind users.

### Three findings worth acting on immediately

These are cheap, and two of them are ordinary bugs that happen to have been caught by an
accessibility audit.

- **The sidebar's current-page highlight has not worked since the daisyUI 5 upgrade.**
  `sidebar.html` sets `class="active"` in 40 places. daisyUI 5 renamed the menu modifier to
  `menu-active`, and no project CSS re-adds `.active`. Nothing marks the current page today —
  visually or programmatically. One find-and-replace restores a feature you already think you
  have, and adding `aria-current="page"` in the same edit closes the 1.3.1 gap.
- **No form field on the public registration form is labelled.** `application_public.html` has
  56 `<label>` tags and zero `for=` attributes; the daisyUI idiom used throughout the codebase
  puts the label text in a `<span>` inside a `<label class="label">` that is a *sibling* of the
  input, not a parent — so there is no association, explicit or implicit. This is the front door
  for someone who is not a member yet and has nobody to ask for help.
- **Race Verified status renders as an empty coloured badge with no text**, in six places in
  `base.html`. Verified and not-verified are announced identically: as nothing.

### What is already right

Worth knowing, so it does not get broken:

- **Page titles.** 82 templates set a distinct `{% block title %}`. Better than most Django
  codebases; keep it up.
- **`<main>` is present, unique, and correct** (`base.html:389`), and `<footer>` is a real
  `<footer>` outside it, with `aria-label` on every social icon link.
- **The Admin ▸ Configuration submenu is a native `<details>`/`<summary>`** (`sidebar.html:244`) —
  a real disclosure with free keyboard support and implicit state. **This is the pattern the
  rest of the nav should copy.**
- **Server-rendered messages already carry `role="alert"`** and a labelled close button.
- **When the drawer is closed, daisyUI correctly removes the sidebar links from the tab order**
  rather than leaving them focusable off-screen.

## The approach: shell first, then a ratchet, then pages

Working page-by-page from the start would mean fixing the same defect dozens of times. The
orphan-label pattern alone spans 16 templates and 178 error blocks; `text-base-content/50`
appears 217 times across 78 files. Those are not page problems, they are component problems.

So: **Phase 0** fixes the shell and the shared components, **Phase 1** installs a guard so the
fixed things stay fixed, and only then does **Phase 2** go page by page — by which point each
page is a much smaller job.

32 of the 123 findings — including 4 of the 9 blockers — live in eight shared files. The
systemic ones (labels, headings, colour tokens) are counted once but land on dozens of pages,
so Phase 0's real reach is considerably wider than that number suggests.

---

## Phase 0 — the shell and shared components

Eight files. Do them in this order; each is independently shippable.

### 0.1 `theme/templates/base.html`

- [ ] **Skip link.** First child of `<body>`, before the announcement banner:
      `<a href="#main-content" class="sr-only focus:not-sr-only focus:absolute focus:z-[100] focus:top-2 focus:left-2 btn btn-primary btn-sm">Skip to content</a>`.
      Give `<main>` `id="main-content" tabindex="-1"`. Tailwind ships `sr-only`/`not-sr-only`
      natively — this is the first use anywhere in the repo, which is why the utility is
      currently absent from the build.
- [ ] **Drawer triggers.** The hamburger (line ~164) and the mobile "More" button (~450) are
      `<label for="main-drawer">` elements; the only focusable control is a 0×0, `opacity:0`
      checkbox with no name, role or state. Replace both with real
      `<button type="button" aria-controls="main-drawer-side" aria-expanded="false" aria-label="Open navigation menu">`,
      driven by a small script that toggles the checkbox, mirrors `aria-expanded`, moves focus
      into the drawer on open, returns it to the trigger on close, and closes on Escape. Keep
      the checkbox for daisyUI's CSS but give it `tabindex="-1" aria-hidden="true"`.
      **Below 1024px this is the only route to the entire sidebar**, so today a keyboard user on
      a phone can reach nothing beyond the four bottom-bar links.
- [ ] **Race Verified badge** — give the six empty-badge fallbacks real text (visually hidden is
      fine) and the emoji `<img>` variants a meaningful `alt`.
- [ ] **Icon-only controls** — 25 of them across 14 files have no accessible name; the header's
      are here.
- [ ] **Toast region.** It is created and filled in the same tick, so HTMX-triggered messages are
      not reliably announced. Ship an empty `aria-live="polite"` container in the base document
      and inject text into it. Separately, the 5-second auto-dismiss has no pause/extend
      (2.2.1) — either lengthen it substantially or pause on hover/focus.
- [ ] **Mobile bottom nav** marks the current tab with `text-primary` alone (colour-only, and
      3.40:1 in dark). Add `aria-current="page"` and a non-colour indicator.

### 0.2 `theme/templates/sidebar.html`

- [ ] Wrap in `<nav id="main-drawer-side" aria-label="Main">` — it is a bare `<aside>` today, so
      40 nav links are announced as a *complementary* region. Rename the mobile bar's label from
      "Primary" so the two navigation landmarks are distinguishable.
- [ ] Replace the 40 dead `active` classes with `menu-active` **and** `aria-current="page"`.

### 0.3 A shared form-field partial — the biggest single win

Create one partial (`templates/shared/_field.html`) that renders label + control + help + error
with the wiring done once, and migrate forms to it. It fixes, in one place:

- 749 labels with no `for=` (the sibling-`<span>` idiom)
- 178 error blocks across 16 files that are orphan `<label>` elements, unconnected to their input
  → needs `aria-describedby` + `aria-invalid`
- 95 help texts across 14 files, likewise floating free
- required state signalled only by a red asterisk
- `text-error` at 2.87:1 in the light theme (177 uses across 18 files)

Then, separately: **no `<fieldset>`/`<legend>` exists anywhere in the codebase** (0 files), so
every radio and checkbox group — gender, equipment, the agreement checkboxes, the Role Setup
lists — is ungrouped. And `autocomplete` appears 5 times total; name, email and country fields
need it (1.3.5).

### 0.4 `templates/events/_filter_select_script.html`

Blocker. Options, cheapest first:

1. **Delete it.** The native `<select>` it replaces is fully accessible and already searchable by
   typing in every browser. The enhancement buys filtering on long lists and costs keyboard users
   the control entirely.
2. Keep the native `<select>` visible and focusable and add filtering *around* it rather than
   replacing it.
3. Build a real combobox: `role="combobox"`, `aria-expanded`, `aria-controls`, `aria-activedescendant`,
   `role="listbox"`/`role="option"` rows, arrow-key navigation, Enter to select, Escape to close,
   a label on the input, and a live region for the result count. This is the APG combobox pattern
   and it is a genuine piece of work.

Recommendation: **option 1 or 2.** Option 3 is only worth it if the long-list filtering is
load-bearing on the planner pages.

### 0.5 Colour tokens — `theme/static_src/src/styles.css`

- [ ] `text-base-content/50` — 217 uses across 78 files — is 3.38:1 in light. Fails.
- [ ] `/30` (132 uses, 29 files) and `/40` (38 uses, 23 files) fail in **both** themes. These are
      the site's standard empty-cell placeholder.
      Fix at the token level: pick the lowest opacity step that clears 4.5:1 in both themes and
      sweep the codebase onto it. Do not fix these per page.
- [ ] **Focus indicator.** daisyUI's default outline is `currentColor`, which is invisible on
      about half the button variants in each theme, and nothing in the repo overrides it. There
      are zero uses of `focus-visible` site-wide. Add one global `:focus-visible` rule with a
      3:1 outline against both themes — this is a single rule that fixes focus visibility
      everywhere, and it is a prerequisite for every keyboard fix below being testable.

### 0.6 The shared table pattern

Applies to `roster.html`, `event_detail.html`, `discord_roles.html`, the availability grids, and
three more — 7 templates with 97 click-sortable `<th>` elements:

- [ ] Sortable headers are bare `<th onclick>` — not focusable, no role, no key handler. Put a
      real `<button>` inside the `<th>`.
- [ ] `aria-sort` appears **0 times** in the repo; direction is conveyed by a ▲/▼ glyph alone.
- [ ] `scope="col"`/`scope="row"` is absent; several matrices use a `<td>` for the row name, so
      cells have no row header at all.
- [ ] Live-filtered tables (signup search, answer facets) change row counts silently — needs a
      live region reporting "N of M shown".

### 0.7 `apps/zwift_data/static/zwift_data/chart.css` and the SVG charts

- [ ] Fix the light-theme contrast (blocker) and correct the CLAUDE.md claim.
- [ ] The generated elevation profile and route map are inline SVG with no name and no text
      alternative. Minimum: `role="img"` + an `aria-label` summarising the route. Better: an
      adjacent, visually-hidden data table of the key figures.

### 0.8 `prefers-reduced-motion`

Zero uses site-wide. One global media query that disables transitions and animations.

---

## Phase 1 — the ratchet

There is **no CI in this repo** (`.github/workflows/` does not exist), so pytest is the only
enforcement point that will actually run. `gotta_bike_platform/test_templates.py` already
establishes the pattern: a repo-wide template scan with a clear failure message.

Add accessibility guards there, each backed by a **baseline file of known offenders that may only
shrink**. New violations fail; existing ones are grandfathered until their page comes up in
Phase 2. `beautifulsoup4` is already a dependency, so this needs nothing new.

Guards worth having, roughly in order of value:

- [ ] no `<label>` without `for=` (or a nested control)
- [ ] no `<th>` without `scope=` in a data table
- [ ] no `onclick` on a non-interactive element
- [ ] no `<img>` without `alt`
- [ ] no `<svg>` inside an interactive element without an accessible name on that element
- [ ] no positive `tabindex`
- [ ] no `focus:outline-none` without a replacement indicator
- [ ] every template extending `base.html` defines exactly one `<h1>` — **55 of 72 have none
      today**, so this one starts with a large baseline
- [ ] the banned-opacity check: no `text-base-content/30|40|50`

For real browser checks, add **playwright + axe-core** as dev dependencies and run axe against a
handful of representative rendered pages. Static guards catch regressions cheaply; axe catches
what only exists in the DOM. Neither replaces a manual keyboard pass.

---

## Phase 2 — page by page

Order is by **who is forced through the page**, not by traffic. (The local analytics DB has 5,112
visits but only one distinct user — it is dev browsing, not member traffic, and is not a useful
priority signal.)

| # | Page | Why here | Known findings |
|---|---|---|---|
| 1 | `/team/apply/<uuid>/` — public registration | Unauthenticated front door; a prospective member with a disability has nobody to ask for help. Zero labelled fields today | 4 |
| 2 | `/accounts/login/` + MFA screens | Second gate; everyone passes through, allauth templates | — |
| 3 | `/user/profile/` + profile edit | Every member must complete a profile; the app nags them until they do | 3 |
| 4 | `/team/roster/` | The most-used read-only page; flagship data table | table pattern |
| 5 | `/events/<id>/` | The busiest member page; 8 sortable tables, signup modals, silent live search | 10 |
| 6 | `/user/verification/` + submission | Gates racing; file upload + status conveyed by image | 2 |
| 7 | Availability grids (respond → builder → results) | The blockers. A core member task that is currently impossible from a keyboard. Needs a rebuild, so it gets its own slice of time | 12 |
| 8 | `/routes/` + route detail | SVG charts, contrast blocker | 4 |
| 9 | TTT + ladder planners | filter-select, HTMX autocomplete | 4 |
| 10 | Squad management, Role Setup, Discord roles | Admin-facing but heavily used by captains | 8 |
| 11 | `/site/config/`, `/analytics/`, `/team/discord-review/`, tickets | Admin-only, lowest reach | — |

### Definition of done for a page

Use this checklist each time; a page is not done until every line passes.

1. **Keyboard only.** Unplug the mouse. Every action reachable and operable; focus visible at all
   times; focus order matches the visual order; nothing traps focus except a modal that should.
2. **Headings.** Exactly one `<h1>`, no skipped levels, headings describe sections.
3. **Forms.** Every control has a programmatic label; errors are associated and announced;
   required state is programmatic; groups are in a `<fieldset>` with a `<legend>`.
4. **Images and icons.** Every meaningful image has a text alternative; decorative ones are
   `aria-hidden` or `alt=""`; every icon-only button has a name.
5. **Colour.** No information carried by colour alone; text ≥ 4.5:1 and UI boundaries ≥ 3:1 in
   **both** the light and the dark theme.
6. **Dynamic content.** Every HTMX swap that reports an outcome announces it; focus survives the
   swap or is deliberately placed.
7. **Zoom and reflow.** Usable at 200% text zoom and at 320px width without horizontal scrolling
   of the page body.
8. **axe.** Clean, or every remaining item is a known and documented false positive.
9. **Screen reader.** One pass with VoiceOver (built into macOS: ⌘F5). The page's purpose and
   every control's purpose are clear from audio alone.

Then remove that page's entries from the Phase 1 baseline file, so it can never regress.

## Notes

- The `tickets` app is internal-only with its sidebar link disabled, and `templates/admin/**` is
  Django admin. Both are lower priority than anything member-facing.
- An accessibility statement page (what the site supports, known gaps, how to report a problem)
  is expected under EN 301 549 and is a natural CMS page once Phase 2 is under way.

---

## Appendix — full finding list

All 123 findings, severity as adjusted by the verification pass. `file:line` is where the pattern
was read, not necessarily its only home — **spread** says how far it actually reaches, measured
with grep during verification.

### Structure, landmarks, headings — 18 findings (6 serious, 7 moderate, 5 minor)

| Sev | Effort | Finding | Location | WCAG | Spread |
|---|---|---|---|---|---|
| serious | medium | 55 of the 72 templates that extend base.html contain no &lt;h1&gt;; the page heading is a DaisyUI card-title &lt;h2&gt; | `templates/accounts/profile.html:10` | 1.3.1 Info and Relationships (A) | 55 files |
| serious | medium | Actions/columns dropdown menus are triggered by &lt;label tabindex="0"&gt; — no role, and Enter/Space does not open them | `templates/events/squad_manage.html:22` | 4.1.2 Name, Role, Value (A) | 9 instances across 5 files |
| serious | small | No skip-to-content link exists anywhere on the site | `theme/templates/base.html:389` | 2.4.1 Bypass Blocks (A) | 1 file (base.html) needs the edit; the absence is universal |
| serious | medium | The hamburger and "More" drawer triggers are &lt;label&gt; elements; the only focusable control is a 0x0 invisible checkbox with no name or role | `theme/templates/base.html:384` | 4.1.2 Name, Role, Value (A) | 1 file |
| serious | trivial | The primary navigation is inside a bare &lt;aside&gt;, so 40 nav links are announced as a complementary region, not navigation | `theme/templates/sidebar.html:1` | 1.3.1 Info and Relationships (A) | 1 file for the fix |
| serious | small | Sidebar current-page state uses a bare `active` class that daisyUI 5 does not style, and there is no aria-current — so the current page is indicated neither visually nor programmatically | `theme/templates/sidebar.html:7` | 1.3.1 Info and Relationships (A) | — |
| moderate | medium | Radio-input tabs override the radio role with role="tab" but never set aria-selected, so no selected state is exposed at all | `apps/ttt_planner/templates/ttt_planner/route_list.html:40` | 4.1.2 Name, Role, Value (A) | 5 files |
| moderate | trivial | The whole allauth/MFA screen family inherits a card shell whose heading block is an h2, so login and every 2FA screen has no h1 | `templates/account/base.html:7` | 1.3.1 Info and Relationships (A) | 10 files + 1 shared parent |
| moderate | trivial | Navigation links are given role="tab" inside a role="tablist" with no tabpanel and no aria-selected | `templates/analytics/dashboard.html:14` | 4.1.2 Name, Role, Value (A) | 2 files |
| moderate | small | Markdown Write/Preview toggle uses role="tab" buttons with no aria-selected and panels that are not tabpanels | `templates/events/event_form.html:65` | 4.1.2 Name, Role, Value (A) | 1 file, 2 instances |
| moderate | medium | The global user menu — the only route to Profile, Security, API Keys and Logout — is a div-based dropdown with no expanded state and a focusable &lt;ul&gt; | `theme/templates/base.html:216` | 4.1.2 Name, Role, Value (A) | 1 file for this specific trigger |
| moderate | trivial | Mobile bottom bar marks the current tab with colour only and no aria-current | `theme/templates/base.html:435` | 1.4.1 Use of Color (A) | 1 file, 3 anchors |
| moderate | small | Sidebar section labels are plain &lt;li class="menu-title"&gt; text, so the eight nav groups are not programmatically associated with the links under them | `theme/templates/sidebar.html:5` | 1.3.1 Info and Relationships (A) | 1 file, 7 section labels |
| minor | trivial | card-title styling applied to &lt;span&gt; where a section heading belongs | `apps/ladder_planner/templates/ladder_planner/detail.html:25` | 1.3.1 Info and Relationships (A) | 2 files, 2 instances |
| minor | trivial | 403 page's only h1 is the number "403"; the actual page name is demoted to h2 | `templates/403.html:9` | 2.4.6 Headings and Labels (AA) | 1 file |
| minor | trivial | CMS pages skip from h1 straight to h3 in the shared cards partial | `templates/cms/partials/_cards.html:11` | 1.3.1 Info and Relationships (A) | 1 partial, 2 include sites |
| minor | small | HTMX-rendered toasts are appended to &lt;body&gt;, outside every landmark, while server-rendered ones live inside &lt;main&gt; — and both use id="messages-toast" | `theme/templates/base.html:391` | 1.3.1 Info and Relationships (A) | 1 file, 2 code paths |
| minor | trivial | Footer navigation landmark is unlabelled, producing a second unnamed navigation region on every page | `theme/templates/footer.html:21` | 1.3.1 Info and Relationships (A) | 1 file, 1 attribute |

### Forms and labels — 18 findings (1 blocker, 6 serious, 8 moderate, 3 minor)

| Sev | Effort | Finding | Location | WCAG | Spread |
|---|---|---|---|---|---|
| blocker | large | Shared searchable-select replaces a labelled &lt;select&gt; with an unnamed, keyboard-dead input | `templates/events/_filter_select_script.html:17` | 4.1.2 Name, Role, Value (A) | 5 consuming surfaces + the partial itself = 6 files |
| serious | small | No autocomplete on any name, email, country, city or phone field | `apps/accounts/forms.py:87` | 1.3.5 Identify Input Purpose (AA) | 2 forms files, and the 'only 5 uses' claim is exactly right |
| serious | medium | All 178 field error messages are orphan &lt;label&gt; elements, unconnected to their input | `templates/accounts/partials/profile_form.html:95` | 3.3.1 Error Identification (A) | 178 occurrences of |
| serious | medium | 88 help/hint texts float free as orphan &lt;label&gt; elements, never announced | `templates/accounts/partials/profile_form.html:202` | 1.3.1 Info and Relationships (A) | 95 occurrences across 14 files, not 88… |
| serious | medium | HTMX form re-renders on validation failure with no focus move and no live region | `templates/accounts/partials/profile_form.html:1` | 4.1.3 Status Messages (AA) | 32 templates contain |
| serious | medium | No &lt;fieldset&gt;/&lt;legend&gt; anywhere: every radio and checkbox group is unlabelled as a group | `templates/accounts/partials/race_ready_form.html:78` | 1.3.1 Info and Relationships (A) | — |
| serious | medium | Public registration form: 56 &lt;label&gt; tags, zero with for= — no field on it is labelled | `templates/team/application_public.html:242` | 1.3.1 Info and Relationships (A) | 3 files have a nonzero |
| moderate | trivial | TTT planner manual-rider form: four inputs with no label at all | `apps/ttt_planner/templates/ttt_planner/planner_detail.html:169` | 3.3.2 Labels or Instructions (A) | This exact guest-rider block is 1 file |
| moderate | small | Nine file inputs on Site Images have no id and no associated label | `templates/accounts/partials/config_site_images.html:50` | 4.1.2 Name, Role, Value (A) | 9 file inputs, 1 file |
| moderate | small | Only the first error per field is ever displayed (.errors.0, 178 sites) | `templates/cms/page_form.html:54` | 3.3.1 Error Identification (A) | 178 across 16 files — independently confirmed, same… |
| moderate | small | Sheets export form: every field label is a detached sibling span | `templates/data_connection/connection_form.html:27` | 1.3.1 Info and Relationships (A) | 1 file |
| moderate | trivial | Required state on custom signup questions is conveyed by a red asterisk only | `templates/events/_signup_question_fields.html:27` | 3.3.2 Labels or Instructions (A) | 1 partial, but genuinely shared — it is the signup +… |
| moderate | trivial | Event chip/tag input for timezones is unlabelled and its added chips are not announced | `templates/events/event_form.html:160` | 3.3.2 Labels or Instructions (A) | 1 file, 1 control |
| moderate | trivial | Role Setup role-filter search inputs are labelled only by placeholder | `templates/events/event_role_setup.html:101` | 3.3.2 Labels or Instructions (A) | 3 inputs, 1 file — exactly as claimed |
| moderate | small | Nested &lt;label&gt; inside &lt;label&gt; breaks association on six squad range fields | `templates/events/squad_form.html:134` | 1.3.1 Info and Relationships (A) | 6 occurrences, all in this one file, all verified by… |
| minor | trivial | Emergency contact phone renders as type=text instead of tel | `apps/accounts/forms.py:238` | 1.3.5 Identify Input Purpose (AA) | 1 field, 1 file — NOT 2 |
| minor | trivial | TOTP activation code field is missing the autocomplete the sibling screen has | `templates/mfa/totp/activate_form.html:37` | 1.3.5 Identify Input Purpose (AA) | 1 field, 1 file |
| minor | trivial | Filter and create forms use a sibling label-text span with no for= | `templates/tickets/ticket_list.html:33` | 1.3.1 Info and Relationships (A) | 2 of the 4 cited files actually have this pattern, not 4 |

### Keyboard and focus — 17 findings (4 blocker, 6 serious, 4 moderate, 3 minor)

| Sev | Effort | Finding | Location | WCAG | Spread |
|---|---|---|---|---|---|
| blocker | medium | filter-select replaces a native &lt;select&gt; with a combobox that has no keyboard selection path | `templates/events/_filter_select_script.html:15` | 2.1.1 Keyboard (A) | 5 templates include the script and 14 selects opt in |
| blocker | large | Availability builder: grid cells and the column/row toggle headers are all mouse-only | `templates/events/availability_builder.html:534` | 2.1.1 Keyboard (A) | 1 file |
| blocker | large | Availability grid cells are keyboard-dead: a rider cannot answer an availability sheet at all | `templates/events/availability_respond.html:394` | 2.1.1 Keyboard (A) | 1 file for this grid; the pattern exists in 2 files total |
| blocker | medium | Results heatmap: 'schedule a race' fires from an onclick on a bare &lt;td&gt; | `templates/events/availability_results.html:125` | 2.1.1 Keyboard (A) | 1 file |
| serious | medium | Grid painting is drag-only with no single-pointer alternative | `templates/events/availability_respond.html:405` | 2.5.7 Dragging Movements (AA) | 2 files |
| serious | medium | Sortable table headers are &lt;th onclick&gt; with no tabindex, role or key handler — eight tables | `templates/events/event_detail.html:1295` | 2.1.1 Keyboard (A) | 8 files, not 7 |
| serious | small | Dropdown triggers built as &lt;label tabindex="0"&gt; are not activatable by Enter or Space | `templates/events/event_detail.html:48` | 4.1.2 Name, Role, Value (A) | 12 broken triggers across 8 files (+1 correct one) |
| serious | small | No skip link, and the primary navigation is the last thing in the DOM | `theme/templates/base.html:463` | 2.4.1 Bypass Blocks (A) | 1 file, all pages |
| serious | medium | Mobile drawer opens with no focus move, no trap and no Esc; its overlay close target is a bare &lt;label&gt; | `theme/templates/base.html:384` | 2.4.3 Focus Order (A) | 1 file, all pages |
| serious | medium | No disclosure anywhere reports its state, and no popup can be dismissed with Escape | `theme/templates/base.html:216` | 1.4.13 Content on Hover or Focus (AA) | dropdown-hover: 2 files |
| moderate | trivial | _user_tooltip's trigger becomes a focusable non-control when the rider has no profile link | `templates/accounts/_user_tooltip.html:3` | 2.4.3 Focus Order (A) | 20 templates |
| moderate | small | Checkbox-based collapses are focus stops with no name and no expanded state | `templates/accounts/verification.html:123` | 4.1.2 Name, Role, Value (A) | 10 instances across 6 files |
| moderate | medium | HTMX outerHTML swaps replace the button that was just activated, dumping focus to &lt;body&gt; | `templates/events/_squad_manage_panel.html:46` | 2.4.3 Focus Order (A) | 2 files, 6 self-destroying triggers in the primary |
| moderate | trivial | Quick-select 'All' / 'Clear' buttons are forced to 20px tall and sit 2px apart | `templates/events/availability_respond.html:322` | 2.5.8 Target Size (Minimum) (AA) | 1 file |
| minor | trivial | A modal is rendered visually open without ever being opened as a dialog | `templates/data_connection/connection_list.html:8` | 2.4.3 Focus Order (A) | 1 file, 1 instance |
| minor | trivial | copyAsImage disables the button the user just pressed, dropping focus to &lt;body&gt; | `templates/shared/_copy_image_script.html:53` | 2.4.3 Focus Order (A) | 2 files |
| minor | small | Toast alerts auto-remove after 5 seconds, taking a focusable close button with them | `theme/templates/base.html:409` | 2.4.3 Focus Order (A) | 1 file, 2 timers |

### Images and text alternatives — 18 findings (4 serious, 7 moderate, 7 minor)

| Sev | Effort | Finding | Location | WCAG | Spread |
|---|---|---|---|---|---|
| serious | small | Signup notes are exposed only through a DaisyUI data-tip, invisible to assistive tech | `templates/events/event_detail.html:479` | 1.1.1 Non-text Content (A) | 2 files |
| serious | trivial | Event-role column is a bare check/cross SVG — the table cell is empty to a screen reader | `templates/events/event_detail.html:463` | 1.1.1 Non-text Content (A) | 2 files |
| serious | trivial | Race Verified status falls back to an empty coloured badge with no text at all | `theme/templates/base.html:198` | 1.1.1 Non-text Content (A) | 2 files, 6 occurrences of the empty-badge fallback |
| serious | medium | 25 icon-only controls have no accessible name whatsoever | `theme/templates/base.html:164` | 1.1.1 Non-text Content (A) | 25 controls across 14 files, verified by script (python3… |
| moderate | small | Character glyphs used as button labels in the ladder planner, with state not exposed | `apps/ladder_planner/templates/ladder_planner/_matchup_body.html:106` | 4.1.2 Name, Role, Value (A) | 1 file, 4 buttons (2 per side) |
| moderate | medium | Chart hover readout is the only access to per-point profile data — no text or table alternative | `apps/zwift_data/static/zwift_data/profile_chart.js:291` | 1.1.1 Non-text Content (A) | 1 file, 1 function; rendered on 2 page types |
| moderate | trivial | Verification status icon on the verification page has no text alternative in the SVG fallback branch | `templates/accounts/verification.html:18` | 1.1.1 Non-text Content (A) | 1 file, 3 occurrences |
| moderate | small | Captain / Vice-Captain conveyed by two similar medal emoji with only a data-tip | `templates/events/_squad_panel.html:50` | 1.1.1 Non-text Content (A) | 5 files, 6 occurrences |
| moderate | small | Help text on form fields lives only in data-tip attached to an unlabelled SVG — including the public registration form | `templates/team/application_public.html:412` | 1.1.1 Non-text Content (A) | 2 files, 3 occurrences of help-text-in-data-tip on form… |
| moderate | large | 371 inline SVGs, none hidden from assistive tech and none labelled | `theme/templates/base.html:165` | 1.1.1 Non-text Content (A) | 371 svg open tags across 80 files |
| moderate | small | title= is the only accessible name on 20 icon-only controls | `theme/templates/base.html:339` | 4.1.2 Name, Role, Value (A) | 20 controls across 10 files, verified by the same script… |
| minor | trivial | Power-up indicator is a lightning emoji with only a title attribute | `apps/ttt_planner/templates/ttt_planner/route_list.html:149` | 1.1.1 Non-text Content (A) | 3 occurrences across 3 files, 2 of them defective |
| minor | small | Route map SVG carries a label that describes the styling, not the route | `apps/zwift_data/static/zwift_data/profile_chart.js:268` | 1.1.1 Non-text Content (A) | 1 occurrence, in JS not templates |
| minor | trivial | Avatar alt duplicates the name printed immediately next to it | `templates/accounts/_user_tooltip.html:9` | 1.1.1 Non-text Content (A) | UNDERSTATED by the finding, which lists five including… |
| minor | small | Constance permission help icons are unnamed focusable graphics with hover-only content | `templates/accounts/partials/config_section.html:42` | 1.1.1 Non-text Content (A) | 1 file, 1 occurrence of this exact construct |
| minor | trivial | Nine image-delete buttons on the Site Images config are identical unnamed red circles | `templates/accounts/partials/config_site_images.html:42` | 4.1.2 Name, Role, Value (A) | 1 file, 9 occurrences |
| minor | trivial | Same-gender-reviewer badge is an icon whose meaning is only in a title attribute | `templates/team/verification_records.html:138` | 1.1.1 Non-text Content (A) | 1 file, 1 occurrence |
| minor | trivial | Zwift connection status shown as a bare check character in a table cell | `templates/team/zwift_connections.html:140` | 1.4.1 Use of Color (A) | 1 file |

### Colour and contrast — 18 findings (2 blocker, 8 serious, 4 moderate, 4 minor)

| Sev | Effort | Finding | Location | WCAG | Spread |
|---|---|---|---|---|---|
| blocker | medium | .zsl-chart's "fixed dark palette" only paints two boxes, so all chart chrome is #e6edf3 on a white card in the light theme | `apps/zwift_data/static/zwift_data/chart.css:14` | 1.4.3 Contrast (Minimum) (AA) | — |
| blocker | small | Availability grid: a rider's own selection is fill colour only, at 1.35:1 in the light theme | `templates/events/availability_respond.html:38` | 1.4.1 Use of Color (A) / 1.4.11 Non-text Contrast (AA) | 2 files |
| serious | trivial | Rider-name links in the shared tooltip partial have no underline and no link colour — they are indistinguishable from plain text | `templates/accounts/_user_tooltip.html:5` | 1.4.1 Use of Color (A) | 25 occurrences of |
| serious | medium | `text-base-content/50` — 217 uses across the site — is 3.38:1 in the light theme | `templates/accounts/public_profile.html:76` | 1.4.3 Contrast (Minimum) (AA) | 217 occurrences across 78 files |
| serious | small | Availability heat-map: the dark-text switch fires too late, leaving a band of mid-density cells with 3.2–4.3:1 text in the dark theme | `templates/events/availability_results.html:127` | 1.4.3 Contrast (Minimum) (AA) | 1 template + 1 view |
| serious | medium | `text-base-content/30` (132 uses) and `/40` (38) fail 4.5:1 in BOTH themes — they are the site's standard empty-cell placeholder | `templates/events/event_detail.html:370` | 1.4.3 Contrast (Minimum) (AA) | /30: 132 occurrences across 29 files |
| serious | small | Every form validation message is `text-error` at 2.87:1 in the light theme — including on the public unauthenticated registration form | `templates/team/application_public.html:153` | 1.4.3 Contrast (Minimum) (AA) | 177 occurrences across 18 files |
| serious | small | Focus outline is daisyUI's `currentColor`, so it is invisible on half the button variants in each theme; nothing in the repo overrides it | `theme/static_src/src/styles.css:3` | 2.4.11 Focus Appearance / Focus Not Obscured (AA, WCAG 2.2) + 1.4.11 Non-text Contrast (AA) | 0 focus overrides site-wide |
| serious | small | Mobile bottom nav marks the current item with `text-primary` alone — colour-only, and 3.40:1 in the dark theme | `theme/templates/base.html:435` | 1.4.1 Use of Color (A) + 1.4.3 Contrast (Minimum) (AA) | 1 file, 3 occurrences |
| serious | small | Sidebar current-page marker uses daisyUI 4's `.active`, renamed to `.menu-active` in v5 — the current page has no indicator at all | `theme/templates/sidebar.html:7` | 1.4.1 Use of Color (A) | 40 occurrences in 1 file |
| moderate | trivial | `badge-secondary` is 3.05:1 in BOTH themes and carries every women's ZP category and every ZR category on the roster | `apps/accounts/templatetags/accounts_tags.py:281` | 1.4.3 Contrast (Minimum) (AA) | 21 |
| moderate | trivial | Verification expiry days use text-success / text-error as the value colour — 1.96:1 and 2.87:1 in the light theme | `templates/team/verification_records.html:149` | 1.4.3 Contrast (Minimum) (AA) | 2 files carry the exact days_remaining pattern; 4 files… |
| moderate | trivial | Mobile card tables render every column heading at 55% opacity (3.95:1 in light) | `theme/static/css/responsive-tables.css:119` | 1.4.3 Contrast (Minimum) (AA) | 1 CSS file governing 12 templates |
| moderate | trivial | `.label{white-space:nowrap}` is only unset below 768px, so form help text still clips at 200% zoom on wide viewports | `theme/static/css/responsive-tables.css:182` | 1.4.4 Resize Text (AA) / 1.4.10 Reflow (AA) | 1 CSS rule affecting 517 |
| minor | small | Men's vs women's ZP category is encoded by badge hue alone — the letter is identical | `apps/accounts/templatetags/accounts_tags.py:281` | 1.4.1 Use of Color (A) | 4 templates call zp_category_badge, one of which (roster,… |
| minor | trivial | vELO factor bars print white percentages on bar fills at 2.15–3.76:1 | `apps/ttt_planner/templates/ttt_planner/route_detail.html:70` | 1.4.3 Contrast (Minimum) (AA) | 1 template + 1 model constant |
| minor | small | daisyUI 5's `.label` renders at 60% currentColor, dropping form labels to 4.42:1 on base-300 cards in the light theme | `templates/team/application_public.html:160` | 1.4.3 Contrast (Minimum) (AA) | 517 |
| minor | trivial | `opacity-50` on whole table rows halves the contrast of live content (3.38:1 in light) | `templates/team/verification_records.html:113` | 1.4.3 Contrast (Minimum) (AA) | 10 total occurrences in 10 files, of which only 3 dim… |

### Tables and grids — 16 findings (1 blocker, 6 serious, 7 moderate, 2 minor)

| Sev | Effort | Finding | Location | WCAG | Spread |
|---|---|---|---|---|---|
| blocker | large | Rider availability grid cells are empty &lt;td&gt;s with pointer-only handlers — no keyboard path to submit availability | `templates/events/availability_respond.html:350` | 2.1.1 Keyboard (A) | 2 templates carry this exact JS-built pointer-only grid |
| serious | small | Every Discord role-matrix cell is a button whose entire accessible name is "✓" or "✗" | `templates/events/_squad_role_cell.html:15` | 4.1.2 Name, Role, Value (A) | 3 partials define the button; 8 include sites render them |
| serious | large | Availability builder grid has the identical pointer-only cell painting — admins cannot block/unblock cells by keyboard | `templates/events/availability_builder.html:553` | 2.1.1 Keyboard (A) | Same 2 templates as finding #1… |
| serious | medium | Availability heatmap slot scheduling is an onclick on a &lt;td&gt; — event admins have no keyboard access | `templates/events/availability_results.html:125` | 2.1.1 Keyboard (A) | 1 template |
| serious | medium | Click-to-sort is bound to bare &lt;th&gt; elements that are not focusable and expose no button role | `templates/events/availability_results.html:232` | 2.1.1 Keyboard (A) | 7 templates, 97 clickable &lt;th&gt; elements |
| serious | trivial | Discord role matrix uses a &lt;td&gt; for the rider name, so cells have no row header at all | `templates/events/discord_roles.html:194` | 1.3.1 Info and Relationships (A) | The &lt;td&gt;-as-row-label pattern is the norm, not the… |
| serious | small | No aria-sort anywhere: sort state is conveyed only by a ▲/▼ glyph | `templates/team/roster.html:271` | 1.3.1 Info and Relationships (A) | aria-sort appears 0 times in the entire repo: |
| moderate | small | Availability cell state (available / unselected / blocked / inactive) is conveyed by background colour alone | `templates/events/availability_respond.html:39` | 1.4.1 Use of Color (A) | Same 3 availability templates |
| moderate | small | Heatmap rider count is encoded as background opacity; data-count is never exposed to assistive tech | `templates/events/availability_results.html:127` | 1.4.1 Use of Color (A) | 1 template |
| moderate | small | No &lt;th&gt; in the codebase carries scope — header/cell association is left entirely to browser inference | `templates/events/discord_roles.html:173` | 1.3.1 Info and Relationships (A) | 0 uses of scope= across 48 table-bearing templates |
| moderate | medium | Two pinned columns make the Discord role matrix unusable at 320px and at 200% zoom | `templates/events/discord_roles.html:170` | 1.4.10 Reflow (AA) | table-pin-col-2 is used on exactly ONE table repo-wide |
| moderate | small | No table on the site has an accessible name, and several pages carry five unnamed tables | `templates/events/event_detail.html:329` | 1.3.1 Info and Relationships (A) | 0 &lt;caption&gt; and 0 table-level aria-label across 48… |
| moderate | trivial | Toggling a column shows or hides table columns with no status announcement | `templates/events/event_detail.html:1181` | 4.1.3 Status Messages (AA) | 1 aria-live region in the entire repo… |
| moderate | medium | .table-cards strips table semantics on mobile; column names survive only as CSS generated content | `theme/static/css/responsive-tables.css:92` | 1.3.1 Info and Relationships (A) | 12 templates, not 11 |
| minor | small | Timezone columns rely on a title attribute on the &lt;td&gt; plus opacity as their only state indication | `templates/events/discord_roles.html:226` | 1.3.1 Info and Relationships (A) | 1 template, 1 column group |
| minor | trivial | Sortable-header hover affordance has no focus-visible counterpart anywhere | `templates/events/squad_add_riders.html:92` | 2.4.7 Focus Visible (AA) | 0 focus-visible declarations and 0 focus-ring… |

### HTMX and dynamic content — 18 findings (1 blocker, 9 serious, 8 moderate)

| Sev | Effort | Finding | Location | WCAG | Spread |
|---|---|---|---|---|---|
| blocker | large | filter-select replaces a labelled &lt;select&gt; with an unlabelled input and mousedown-only &lt;div&gt; rows — no name, no role, no announced results | `templates/events/_filter_select_script.html:17` | 4.1.2 Name, Role, Value (A) | 3 literal |
| serious | medium | HTMX autocomplete search results appear silently — input has no combobox semantics and no result count is announced | `apps/ttt_planner/templates/ttt_planner/planner_detail.html:155` | 4.1.3 Status Messages (AA) | 3 autocomplete inputs across 2 templates… |
| serious | trivial | Signup filter count and zero-match state update on every facet click with no announcement | `templates/events/_answer_facets_script.html:137` | 4.1.3 Status Messages (AA) | 1 file actually carries the elements |
| serious | trivial | Availability save/error toasts are injected into a plain div with no role or aria-live, then hidden on a 4s timer | `templates/events/availability_respond.html:78` | 4.1.3 Status Messages (AA) | 2 templates, both rider/captain-facing |
| serious | small | Signup table live search filters rows silently — no result count reaches assistive tech | `templates/events/event_detail.html:1418` | 4.1.3 Status Messages (AA) | 2 templates with a live name-filter over a signup table:… |
| serious | medium | Click-sortable table headers have no aria-sort and the direction is conveyed only by a ▲/▼ glyph | `templates/events/event_detail.html:1449` | 1.3.1 Info and Relationships (A) | 5 templates with click-sortable headers, 74 |
| serious | medium | Add-members modal destroys the focused control on every selection and never announces results | `templates/events/event_detail.html:1521` | 4.1.3 Status Messages (AA) | 1 file |
| serious | small | hx-indicator spinners are text-free and nothing is ever marked aria-busy, so an in-flight request is invisible to assistive tech | `theme/static_src/src/styles.css:25` | 4.1.3 Status Messages (AA) | 11 htmx-indicator spans across 9 templates:… |
| serious | small | Toast live region is created and filled in the same tick, so HTMX-triggered messages are not reliably announced | `theme/templates/base.html:391` | 4.1.3 Status Messages (AA) | 1 template (theme/templates/base.html) but it is the site… |
| serious | small | Toasts auto-dismiss on a 5-second timer with no way to pause, stop, or extend | `theme/templates/base.html:409` | 2.2.1 Timing Adjustable (A) | 2 timers in base.html (lines 409-420, 495-499) plus 2… |
| moderate | small | Route detail charts lazy-load and fail silently — placeholder swapped, error strings injected, nothing announced | `apps/zwift_data/static/zwift_data/route_detail.js:43` | 4.1.3 Status Messages (AA) | 1 JS file + 1 template |
| moderate | large | SortableJS drag-reorder in the config editor is pointer-only and the new order is never announced | `templates/accounts/config_section_page.html:165` | 2.1.1 Keyboard (A) | 1 live template + 1 dead one |
| moderate | small | Race Ready upload errors are injected as a plain alert div and the progress bar has no accessible name or live region | `templates/accounts/partials/race_ready_form.html:459` | 4.1.3 Status Messages (AA) | 1 file |
| moderate | small | "Re-check" bot-role button lives inside its own swap target — it deletes itself and the new status is silent | `templates/events/_bot_role_status.html:18` | 4.1.3 Status Messages (AA) | 1 partial, 1 host template, 1 render call |
| moderate | medium | Squad panel actions replace the whole panel via outerHTML from a control inside it — focus lost, outcome silent | `templates/events/_squad_manage_panel.html:46` | 4.1.3 Status Messages (AA) | 3 templates, 10 self-destroying controls |
| moderate | trivial | Slot-modal server errors are written into a hidden div with no role=alert | `templates/events/availability_results.html:775` | 4.1.3 Status Messages (AA) | 1 file |
| moderate | small | copyAsImage signals success/failure only by swapping an icon and a colour class, and disables the button mid-operation | `templates/shared/_copy_image_script.html:27` | 4.1.3 Status Messages (AA) | 1 shared partial, 2 call sites |
| moderate | small | Verify/Reject ZWID swaps the row containing the button away to an empty response — focus destroyed, outcome never announced | `templates/team/partials/zwid_pending_section.html:65` | 4.1.3 Status Messages (AA) | 2 templates:… |

