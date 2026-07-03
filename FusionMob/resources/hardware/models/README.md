# Hardware CAD models

Drop bundled drawer-slide (and future hardware) CAD models here. Each slide entry
in `../hardware.json` points to a file in this folder via its `model_file` key.

**Recommended format:** a **closed-state** STEP (`.step`/`.stp`) — clean, lightweight
solid geometry that positions well and stays performant across multiple drawers.
Mesh formats (`.obj`, `.stl`, `.3mf`) also import (as mesh bodies) but are heavier.

The Matrix Invisa entry points to the bundled **closed-state STEP** `433_03_132_4.stp` —
a clean 801 KB solid assembly (15 bodies: rails, ball cages, latches, brackets) in
millimetres — so enabling *Inserir modelo 3D da corredica* imports the real slide.
The model is imported **once per build and instanced** for the other slides.

**Placement almost certainly needs tuning.** The assembly's bounding box is
~576 × 312 × 61 mm (longer and much wider than a bare rail, because it includes the
lateral drawer-bottom locking brackets), and its origin is the CAD origin, not the
slide's mounting point. `model_transform.rot_deg` is a **starting guess** of `90`
(about Z) to run the longest axis front-to-back along the drawer.

To dial it in, open a cabinet with a drawer + hardware on, note where the model lands,
and edit the entry's `model_transform` in `../hardware.json`:
`rot_deg` + `rot_axis` (rotation), `tx/ty/tz` (mm offset added after rotation), `scale`
(models are imported assuming **millimetres**). If `model_file` is `""`/missing or the
import fails, FusionMob falls back to the lightweight parametric **proxy** box (no error).
