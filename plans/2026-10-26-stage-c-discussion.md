# Stage C (long-tail diversity): discussion and decisions

Source: `Roadgen AV Readiness Review.pdf` (repo root), part **C · Long-tail
diversity**. Implementation plan:
[2026-10-26-stage-c-implementation.md](2026-10-26-stage-c-implementation.md).

## Prompts

**2026-10-26, prompt 1**

> I have implemented stage A in the Roadgen AV Readiness Review.pdf. You can find the prompts and what is implemented in plans folder. Now, we are going to implement stage C. I want you to give me a comprehensive plan first before the implementation. In style mixing inside one map section of stage C, I have already thought about removing the manhattan generator by integrating it into the lsystems. Having a separate grid-shaped city generator is idle and unnecessarily complex. So, as in the papers in files folder, we can create city patterns using only lsystem generator. Do not only focus on that section in stage C.

The user's answers to the clarifying questions, verbatim:

- Manhattan removal: "Remove fully (Recommended)"
- Highway modelling: "What about cloverleaf interchange? It is more complex than diamond on two-way. However, give me the advantages and disadvantages of both." → after the comparison: "Both as templates (Recommended)"
- Scope: "Style mixing + manhattan fold, Junction variety, Roundabouts, cul-de-sacs, U-turns, Overpasses, tunnels, ramps" (all of C)
- Single-junction generator: "I don't know. What is the advantage of adding it ? I am eager to add it. Discuss it." → after the discussion: "Yes, after junction variety (Recommended)"

**2026-10-26, prompt 2** (start of the implementation)

> Implement C0-C4 at once. While removing manhattan generator, you can remove related references and artifacts to it.

## What part C of the review asks for

Stage A of `Roadgen AV Readiness Review.pdf` (geometry accuracy) is done
(`plans/2026-10-23-*`, `2026-10-24-*`). Stage C of the review is **long-tail
diversity**: today every city is a grid (straight, drifting or contour-following),
junctions are almost all 3–4-leg near-90° crossings, and the road types that
make AV driving hard (roundabouts, merges, lane drops, overpasses, tunnels,
U-turns, cul-de-sacs) do not exist. Review items:

1. **Junction variety:** 3–7 legs with skew and offset (Y, K, five-way), lane
   splits (Muktadir et al. / JunctionArt, file10).
2. **Missing road types:** roundabouts; on/off ramps, merges, lane drops/adds;
   overpasses (impossible with today's planar graph); tunnels; U-turns;
   cul-de-sacs (Yang file4, Teng & Bidarra file9).
3. **Style mixing inside one map:** grid, radial and organic blended by control
   maps; Paris radial and San Francisco contour roads; rotation fields for skewed
   junctions; one grid-to-organic knob; a connectivity knob from grid to
   cul-de-sac suburbs (Parish & Müller file1 §3.2.2, Chen file2 §5.3, TownSim
   file8 §3.3, Lechner file6 §3).

The user also asked to remove the `manhattan` generator: a separate grid
generator is redundant, and the papers make every city pattern with one
L-system (Parish & Müller's pattern rules).

## Options discussed

### Removing manhattan

- **Remove fully (chosen).** Its features (alternating one-way streets, ring
  road, edge drop that keeps connectivity) move into lsystem; a
  `manhattan_like` lsystem preset replaces the old preset; `bench` sweeps
  lsystem grids sized from counts. The 7 old manhattan run folders in `out/`
  can no longer be replayed.
- **Keep a thin alias** that converts manhattan params to lsystem params.
  Old commands keep running, but outputs differ anyway (node ids, growth
  order), and it is one more thing to maintain.

### Highway interchanges: diamond vs cloverleaf

| | Diamond | Cloverleaf |
|---|---|---|
| Shape | 4 straight one-way ramps, ending at junctions on the crossing street | 4 loop ramps (~270°) + 4 outer ramps, free-flowing |
| Area needed | ~300 × 100 m, fits a city grid | ~500 × 500 m; loop radius 45–60 m at 40 km/h, blocks must be cleared |
| Conflicts | left turns at the ramp ends cross oncoming traffic | no crossings; weaving between consecutive loops |
| AV training value | merges, diverges, unprotected lefts; the most common urban interchange | weaving, long tight loops, free-flow merges: rare, hard cases |
| Where it belongs | highway × avenue or street | highway × highway (both multi-lane) |
| Fit with our code | simple: stage A tapers plus side-restricted movements | loops exceed `max_merge_deflection_deg` (135°) so need several bend nodes; weaving = lane add then drop between two close junctions, which stresses trims and repair |
| Downsides | needs signals or stop signs at the ramp ends (stage B); lower capacity | large; new designs avoid it for weaving safety (many rebuilt as partial cloverleafs) |
| Extra effort | base | ~3–4 days on top of diamond |

Other highway options considered: divided carriageways (two one-way roads
with a median; more realistic but changes highway growth, drape, bridges and
scene, about a week more), and deferring ramps.

**Chosen:** both, as templates on one shared base (overpass, ramps,
merge/diverge nodes), on the existing two-way highways.

### A single-junction generator (JunctionArt-style)

Advantages:

- **Long tail on demand.** In a city almost all junctions are 3–4 legs at
  about 90°; 6–7 legs, 20° skew or offset legs are rare and can't be
  steered. A sampler fills any bin (the review's part D: "generate until
  every junction-type bin is filled").
- **Builder test bed.** Seed sweeps over extreme angles, leg counts and lane
  splits test trims, clothoid turns and drive-check directly, with small,
  reproducible failures.
- **Cheap scenarios.** One junction builds in about a second; suits stage E
  ego manoeuvres (an unprotected left in a K junction).
- **Paper comparison.** JunctionArt's per-junction difficulty metrics can be
  compared directly.

Disadvantages:

- No surroundings: no approach speeds, no upstream junctions, no network
  traffic; the shape distribution is chosen, not natural.
- One more small generator to maintain (registry, defaults, CLI, docs,
  tests; ~200–300 lines, no maps). It shares its sampling with lsystem's
  junction-variety code.

**Chosen:** add it, right after junction variety.

## Paper techniques used

- Parish & Müller (file1) §3.2.2 pattern rules (basic, New York, Paris, San
  Francisco) blended with grey-scale control maps; §3.3 local constraints;
  bridges and tunnel portals for highways crossing illegal areas.
- Chen et al. (file2) §5.1–5.3: grid, radial and height-field tensor fields,
  blended by a weighted sum; rotation fields (R1/R2) for skewed crossings;
  Perlin rotation for organic layouts; §6.3 deletion noise.
- TownSim (file8) §3.3: snap knob `co` from organic to grid; §4.2
  orientation and curvature metrics.
- Lechner et al. (file6) §3: grid-deviation knob and the connectivity knob
  (network distance > k × straight distance).
- Teng & Bidarra (file9) §3.1–3.3: cul-de-sac and terminal patches, snap and
  clip rules (50 ± 10 m, min angle 70°, snap 20 m); §4.2 CNR and density.
- Yang et al. (file4) §7: dead ends and intersections per km², block sizes.
- Muktadir et al. / JunctionArt (file10): 3–7 legs, lanes per side
  distribution [0.15, 0.6, 0.2, 0.05], lane splits, lane split/merge sections.
- StreetGen (file5) §2.3–2.5: corner radius by road class (3, 4.9, 7.6 m).
- No paper covers ramps, interchanges, overpasses or U-turns; these follow
  AASHTO and FHWA practice.

## Decisions

| # | Question | Decision | By |
|---|---|---|---|
| 1 | Manhattan generator | **Remove fully.** Its features move into lsystem (alternating one-way streets, ring road, edge drop that keeps the network connected). A `manhattan_like` lsystem preset replaces the old preset, and `bench` sweeps lsystem grids sized from counts. The 7 old manhattan run folders can't be replayed; replaying one says so and names the replacement preset. | user |
| 2 | Interchanges | **Diamond and cloverleaf as templates on one shared base** (overpass, ramps, merge/diverge nodes). Diamond at highway × avenue/street, cloverleaf at highway × highway. Diamond is built first. | user |
| 3 | Scope | **All of stage C** | user |
| 4 | Single-junction generator | **Yes**, a `junction` generator right after junction variety | user |
| 5 | How patterns blend | **Tensor-field blending** (Chen §5.1–5.3): each pattern gives a direction field at a point, and the fields are summed with control-map weights (P&M §3.2.2 weighting). Tensors are symmetric under 90° and 180° turns, so there are no angle wrap-around artefacts when grid and radial meet, and rotation fields come for free. | Claude |
| 6 | Where the control maps live | **A new map kind `style`** (categorical zones: grid / radial / organic / suburb), in the existing map registry. lsystem turns zones into weights by blurring them over `blend_width`. Without a style map, a constant `pattern_weights`. | Claude |
| 7 | Roundabouts, cul-de-sacs, interchanges | **Graph → graph transforms** (`roadgen/generators/transforms/`), run at the end of generation like `repair.py`. Every feature becomes ordinary nodes and edges (one-way rings, ramps, bend nodes), so the builder needs only small changes, and any generator, including `junction`, can use them. | Claude |
| 8 | Highways for ramps | **Keep two-way highways.** New node kinds `merge` / `diverge` restrict movements to lanes on the same side of the road, so traffic never turns across the centre line onto a ramp. Divided carriageways are left for later. | Claude |
| 9 | Overpasses | **Two edges that cross without a shared node**, with the upper road raised through `Edge.profile` and a `bridge` span over the crossing. No new graph topology. A new clearance validator checks the vertical gap. | Claude |
| 10 | Identical output | Every new feature defaults to off. Existing lsystem presets must give identical graph JSON apart from `metadata.params`, which lists every parameter and so grows with each new one (the CLAUDE.md rule; corrected after C1, see its result). New randomness comes from salted sub-streams `np.random.default_rng([seed, SALT])`, so the existing draw order never changes. | Claude |
| 11 | Manhattan `jitter` (node displacement) | **Not ported**; lsystem's angle and length jitter cover it | Claude |

