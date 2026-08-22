# FusionMob — palette UI (`resources/ui/`)

Two Fusion palettes live here, and they edit **the same cabinet configuration**:

| File | Palette | What it edits |
|---|---|---|
| `preferences.html` | **Preferências** | The **defaults** every new cabinet starts from (per profile), saved to `preferences.json`. |
| `layout_editor.html` | **Editor de Armário** (Cabinet Layout) | The **per-cabinet override** of those same values, saved to that cabinet's `cabinetConfig`. |

The folder is grouped by ownership, so what is shared is obvious at a glance:

```
resources/ui/
  layout_editor.html   the two entry points — Fusion opens these by path,
  preferences.html     so they stay at the root (see FusionMob.py)
  shared/              loaded by BOTH palettes
  layout/              the Cabinet Layout palette's own code
```

Plus the shared pieces both load:

| File | Contents |
|---|---|
| `shared/cabinet_config.js` | **`FMCFG`** — the field spec (`SPEC`), the reference diagrams (`DIAGRAMS`), the form renderer, the cfg↔form reader/writer, the dynamic behaviours and the tooltip engine. |
| `shared/cabinet_config.css` | Everything that styling touches in the rendered form: theme tokens, rows, switches, sections, diagrams, tooltips. |
| `shared/palette_bridge.js` | `send()` — the one wrapper around `adsk.fusionSendData`, plus `byId`/`esc`. Both palettes talk to Python through it. |

And each palette's own page files — chrome and logic that belong to one page
only, so they are deliberately NOT shared:

| Palette | Files |
|---|---|
| Cabinet Layout | `layout/editor.css` (page chrome), `layout/model.js` (the interior region tree), `layout/canvas.js` (the SVG it draws itself), `layout/wizard.js` (the Assistente), `layout/demo.js` (browser fallback), `layout/editor.js` (state + wiring) |
| Preferências | its own inline `<style>` / `<script>` |

---

## THE RULE

> **Preferências holds the default for every cabinet setting; the Cabinet Editor
> lets the user override that default for one specific cabinet. So every setting
> must exist on BOTH pages — and it must be declared in exactly ONE place.**

That one place is **`SPEC` in `shared/cabinet_config.js`**. Both palettes render their
whole configuration form from it, so a field, a label, a unit, a tooltip, an
option list or a schematic is written once and appears identically on both.

**Never hand-write a configuration row into `preferences.html` or
`layout_editor.html`.** If you find yourself typing `<div class="row"><label>…`
for a cabinet setting in either HTML file, stop — it belongs in `SPEC`.

### Adding or changing a setting

1. Add the key to `DEFAULT_CFG` (and its `*_DEFAULTS` dict, if it is nested) in
   `FusionMob.py`, and make `normalize_cfg` backfill it so older stored configs
   keep loading.
2. Add **one entry** to the right section of `SPEC` in `shared/cabinet_config.js`:
   `{ id, p, l, t, u, h }` — DOM id, cfg path, label, type, unit, tooltip. Both
   palettes pick it up with no further edits: rendering, the help tooltip, the
   read/write round-trip and the option list all follow from the entry.
3. Teach the geometry/validation in `FusionMob.py` about it.
4. Add it to the **New/Editar Armário** Fusion command dialog too — see below.
5. Bump the version in `FusionMob.py` **and** `FusionMob.manifest`.

Changing a label, fixing a typo in a tooltip, adding a `data-tip`, redrawing a
schematic: same story — edit `SPEC` / `DIAGRAMS`, and both pages change together.

### The deliberate exceptions

A handful of fields describe a *single-region* cabinet, and the layout editor
represents them per region in the interior tree instead: `n_shelves`,
`shelf_align_front`, `with_doors`, `n_doors`, `door_inset`, `with_drawers`,
`n_drawers`, `drawer_inset`. They carry `only: "prefs"` in `SPEC`, and the
section shows the reader a `notes.cabinet` line pointing at the Interior editor.
**`only:` is the only sanctioned way to have a field on one page and not the
other** — never by omitting it from one HTML file.

### The third surface: the Fusion command dialog

`add_cabinet_inputs` / `read_cabinet_inputs` / `write_cabinet_inputs` in
`FusionMob.py` build the **New/Editar Armário** dialog out of native Fusion
inputs, which cannot share this HTML/JS. It is therefore the one place that must
be updated by hand, in all three functions (create, read, write) — plus
`_CABINET_ADVANCED_IDS` if the new input is not one of the always-visible
essentials. Keep its labels and defaults matching the `SPEC` entry.

---

## Mechanics worth knowing

- **Loading.** Fusion serves the palette from a `file://` URL, so the relative
  `<link href="shared/cabinet_config.css">` / `<script src="layout/model.js">`
  resolve against this folder. No build step, no external deps — keep it that
  way. The two `.html` files must stay at the root of `ui/`: `FusionMob.py`
  hands Fusion their absolute paths, so moving them means editing it too.
- **Page scripts, and why they are not modules.** `layout_editor.html` loads its
  code as plain classic `<script src>` tags in dependency order:

  ```
  shared/palette_bridge → shared/cabinet_config → layout/model
    → layout/canvas → layout/wizard → layout/demo → layout/editor
  ```

  They share plain globals (`state`, `CTX`, and the function declarations); only
  `layout/editor.js` has top-level code that runs on load, which is why it goes
  last. **Never convert these to `type="module"`** — Fusion serves the palette
  from a `file://` URL, where module loading is CORS-blocked (origin `null`) and
  the palette comes up blank; module scoping would also hide `var FMCFG`.
  `layout/editor.css` must likewise load *after* `shared/cabinet_config.css`, because a
  few of its rules override shared classes (`.actionbar .btns`, `.wiz-foot .grow`).
- **Surfaces.** `FMCFG.render(host, {surface, sections, plain, noDiagram})`.
  Preferences renders every section into `#form`; the layout editor renders
  `dims` into the Medidas card (`plain: true, noDiagram: true`, because that card
  draws its own live explainer) and the rest into `#cfgSections`.
- **One listener set.** `FMCFG.wire(CTX)` covers every field of every section;
  `CTX` is a single mutable object (the wiring closes over it), so refresh its
  `materials` / `slides` / `fitaChoices` in place rather than replacing it.
- **Dynamic behaviour is shared too** — the back-panel groove/overlay swap, the
  arremate gap-vs-ceiling switch, the slide read-only readout and the live
  Fixação lateral drawing all live in `syncDynamics`, so both pages behave the
  same. Put new conditional behaviour there, not in a page.
- **Search is derived from `SPEC`.** The property search bar on both palettes
  (`FMCFG.searchMount` / `searchApply`, styled `.fmh-*`) builds its index from
  `SPEC` alone, so a new field is searchable with no extra work — but its `l`
  and `h` ARE the search corpus, so keep them meaningful. That module is also
  the single owner of row/section visibility: it hides with the `.fmh-hide`
  class and never touches inline `style.display` (which `syncDynamics` owns for
  the mode-dependent rows). A page with its own gate declares it through
  `FMCFG.setGatedSections(keys)` instead of hiding sections itself, so a search
  hit can surface a gated section. Any page that re-renders the form must call
  `FMCFG.searchApply()` afterwards, or an active filter is silently lost.
- **Option lists come from Python** (`materials`, `slides`, `fita_choices` in the
  `init` payload of both `_palette_state` and `_prefs_state`). If you add a new
  list-backed field type, send the list from both.
- **Testing without Fusion.** Serve this folder over HTTP and open either page:
  ```bash
  python -m http.server 8791 --directory FusionMob/resources/ui
  ```
  The layout editor falls back to a demo cfg after a few seconds; Preferences
  needs `applyState({...})` from the console. `file://` alone will not work — the
  relative script/stylesheet must be fetchable.
