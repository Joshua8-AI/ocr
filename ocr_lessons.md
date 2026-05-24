# OCR Engineering Lessons

Distilled from benchmarking and tuning a self-hosted document-OCR pipeline (VLMs +
traditional OCR + layout models) against **olmOCR-bench**. Numbers below are from the
150-document stratified sample (≈790 tests, 7 categories, ±~3% CI), but the lessons are
meant to transfer to any OCR project.

---

## 1. The biggest lever is the prompt, not the model

- A **generic "preserve the original layout"** instruction makes capable VLMs reproduce
  tables as **space-aligned text**, which most scorers (and downstream parsers) can't read.
  Replacing it with an **explicit, unambiguous table rule** — *"output every table as a
  GitHub-flavored Markdown pipe table with a header separator row; never align columns with
  spaces"* — was the single largest win we found: one 35B model went **table accuracy 37% →
  86%** and **overall 56% → 73%** from prompt changes alone.
- **Contradictory instructions quietly tank quality.** The original prompt said *both*
  "preserve layout" *and* "extract as a markdown table." Small models resolved it toward
  markdown; large models toward faithful spatial layout. Make each rule unambiguous.
- **Prompts do not transfer across models.** We hill-climbed prompts per model:
  - A small model **over-omits** content without an explicit *completeness* safeguard
    ("transcribe the complete body including dense/small/faint text and every equation").
  - One large model's **tables collapse at temperature 0** unless the markdown-table
    directive is present.
  - "Omit page furniture" wording *helped* some models (they gained header handling) but
    *hurt* another (it dropped old-scan math). **Broad** furniture lists (journal/DOI/date)
    made models over-editorial and drop real body text — net negative.
  - Budget time to tune per model; don't ship one global prompt.
- **Use temperature 0 for OCR.** Deterministic, reproducible, and it scored slightly higher
  than 0.1 while eliminating run-to-run variance that faked regressions during tuning.

## 2. Counterintuitive: isolating a region makes a VLM *worse* at it

We tested the appealing idea of *"detect the table, crop it, OCR just that region."* It
**hurt** table accuracy:

```
full-page VLM 86   >   crop with a layout model's precise bbox 78   >   crop with the VLM's own bbox 65
```

- A VLM reads tables **better with full-page context** — the crop clips edge rows/columns
  and severs the header↔data alignment the model relies on.
- A purpose-built layout model localizes regions more precisely than the VLM does, but even
  perfect crops lost to full-page.
- **Lesson:** don't fragment the page to "help" a strong VLM. Feed it the whole page.

## 3. The winning pattern: augment the strong model, don't replace it

The best general VLM was excellent at body text, tables, and math but had **one floored
category: running headers/footers** (~18% — a hard ceiling that prompting could not lift).

Fix: keep the VLM's full-page output and **post-process only the weak part** — strip the
lines a layout model flags as `page_header`/`page_footer`/`page_number`. That single,
targeted post-process lifted every model with **tables/math untouched**:

| base model | overall before | after furniture-strip | headers/footers |
|---|---|---|---|
| large VLM | 72 | **80** | 18 → 84 |
| small VLM | 67 | **74** | 29 → 81 |
| XL VLM | 70 | **74** | 48 → 86 |
| Tesseract | 36 | **41** | 51 → 86 |

- **Structural furniture removal beats prompt-based omission.** "Don't transcribe headers"
  caps out; dropping the regions a layout model classifies as furniture works (every model
  converged to ~85 on the "absent" tests).
- **Gain scales with how much furniture the base leaks** — biggest lift on the leakiest
  model, smallest on the one whose prompt already removed some.
- It generalizes across engines (it even helped Tesseract) because it's a pure post-process
  keyed off the layout model, independent of who produced the text.
- **Guard against over-stripping:** on a sparse cover/title page the *whole* body can be
  layout-classified as a header. Never let the strip reduce a page to empty — fall back to
  the original.

## 4. Know your coordinate space before you crop

- A VLM asked for bounding boxes returned coordinates **normalized to 0–1000**, *not pixels*
  — and it **ignored** an explicit "coordinates in pixels" instruction. Verify the
  convention empirically (cross-check one box against a known region) before trusting any
  crop pipeline; an unnoticed 0–1000-vs-pixels mismatch silently crops the wrong area.
- Layout models (e.g. Docling) report **real document coordinates** with an explicit origin
  (often bottom-left) — reliable, but mind the y-flip and points→pixels scale.

## 5. Reasoning ("thinking") models need thinking disabled for OCR

A model that emits a reasoning trace, left in thinking mode, **spends its entire token
budget reasoning and emits empty `content`** (`finish_reason: length`) — or, at higher token
limits, runs long enough to trip the gateway's timeout and surface as an **HTTP 500**.

- Symptom we chased: ~half of one model's pages came back as **0-byte files / 500s**. Root
  cause was *not* a client timeout or a generation loop — it was thinking burning the budget.
- **Diagnose precisely:** a 500 is server-side; check `finish_reason` and whether `content`
  vs `reasoning_content` is populated before assuming a timeout. A trivial prompt returning
  fast confirms the backend is healthy.
- **Fix:** disable thinking (`chat_template_kwargs: {enable_thinking: false}` for vLLM /
  llama.cpp; or the server's `--reasoning-budget 0`). **Verify the exact param is honored** —
  several plausible ones (`reasoning_effort: none`, a `/no_think` prompt) were silently
  ignored by our backend; only one worked.
- A longer timeout and a repetition penalty do **not** fix this (it's not a hang or a loop).

## 6. Document round-trips lose fidelity

Feeding a VLM's markdown back through a document toolkit's serializer corrupted output:

- The markdown serializer **hard-coded escaping** (`\_`, `\*`, HTML-entity-encoded `<>&`)
  that **overrode its own config flags**, mangling LaTeX inside `$...$` and breaking math
  scoring. The HTML serializer was cleaner for math but **merged table cells**.
- **Lesson:** consume the *least-processed* output you can; if you must round-trip, audit the
  serializer (and be ready to patch it). Don't assume config flags are respected — diff the
  actual output.

## 7. Model-selection notes

- **Tiny "doctags"/structure models** (a 258M layout-to-tags model) were great at furniture
  and layout but **could not transcribe dense content** — a dead end for table/text quality.
  Good furniture detector, bad OCR.
- **Render resolution is a real knob:** too low → empty/garbled cells; too high → runaway
  generation. There's a sweet spot (~2000px longest side worked well); tune it.
- **Serving:** doctags/structure tokens get stripped by OpenAI-compatible APIs unless
  `skip_special_tokens=false`. Single-stream backends (llama.cpp) 500 on concurrent requests
  — run them at parallelism 1.
- **Traditional OCR (Tesseract)** is floored on tables (~1%) and math (0%); no prompt or
  post-process changes those. It's a fallback, not a contender, regardless of furniture
  cleanup.

## 8. Benchmarking discipline

- **Empty output = failed tests.** Anything that yields a blank page (timeout, 500, runaway
  thinking, over-aggressive post-process) zeroes those tests — guard every stage against it.
- **Re-run surprising numbers.** One model's table score read 90 once and ~84 on two later
  runs — the 90 was an outlier (that model has table instability even at temp 0). A clean,
  *matched* re-run settled it.
- **Change one variable at a time** and keep base + variant from the *same* run when you A/B
  (don't compare a post-process built on run A against a baseline from run B).
- **Score per category, not just overall.** Overall hides the story: the furniture-strip win
  was invisible in a single number until you saw headers go 18 → 84 with tables flat.

## 9. Delivery / ops

- **Serve HTML with `Cache-Control: no-cache`** so a CDN/edge cache doesn't pin a stale page
  after deploys; version static assets (`?v=`) for long-cache + cache-busting. (A CDN edge
  cache will keep serving the old page until purged even after you fix the origin — a
  connector/tunnel token can't purge; you need an API token or a dashboard purge.)
- **Make each capability an additive, selectable option** rather than swapping defaults —
  it lets you A/B variants in production and on the chart instead of guessing.

---

### TL;DR cheat-sheet
1. Fix the prompt first; make table rules explicit; tune per model; temperature 0.
2. Give the VLM the **whole page** — don't crop regions to "help" it.
3. Augment the strong model with a **targeted post-process** for its weak category (e.g.
   strip layout-detected headers/footers) instead of replacing it.
4. **Disable thinking** on reasoning models for OCR; diagnose empties/500s via `finish_reason`.
5. Avoid document round-trips; consume the least-processed output.
6. Guard every stage against empty pages; re-run outliers; score per category.
