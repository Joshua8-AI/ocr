# OCR App — Pending Work (snapshot 2026-05-24)

## STATUS (updated after commit 429c510)
- [x] **A** — deployed (web+worker). ONLY remaining: **Cloudflare purge of index.html** (no CF creds here).
- [x] **B** — 9B/122B tuned prompts wired + chart re-sorted + deployed.
- [ ] **C** — Gemma4-26B/-31B re-run still going (Gemma4-26B ~8/150, Pascal, hours). Score + chart when done.
- [ ] **D** — Docling-VLM decision still OPEN. NOTE: the md-path fix (docling.py) IS now deployed/committed; §D is whether to upgrade to the html-path (70.0%) or change the Docling image for tables.
- [x] **E** — committed locally (429c510), not pushed.

---

Everything below is **uncommitted** on the working tree. Picking this up later:
deploy = `docker compose up -d --build web worker`, then **purge Cloudflare cache for
`index.html`** (the tunnel caches it; it has no `?v=` buster).

Benchmark = olmOCR-bench, app-faithful, 150-doc sample. Harnesses + logs in
`~/ocr-bench/` (see §F). Winning prompts saved in `~/ocr-bench/winning_prompts/`.

---

## A. Redeploy to pick up post-deploy code changes (NOT yet live)
Last deploy shipped the 3 new models, tuned Qwen3.6-35B prompt, HTML output format,
and Chandra `<math>`. These were added AFTER and are uncommitted + undeployed:
- [ ] `worker/app/ocr/engine.py` — `$...$` LaTeX protection in `_html_to_markdown`
      (Nanonets/Chandra math fix). Verified: Nanonets bench 51.8→57.6%, arxiv_math 27→74.
- [ ] `worker/app/ocr/docling.py` — Docling-VLM tuned prompt + `_unescape_vlm_markdown`
      (may be replaced by the html-path — see §D before deploying).
- [ ] `web/app/static/index.html` — Nanonets chart row updated to 57.6% / math 39.0.
- [ ] Deploy (web+worker) + Cloudflare purge.

## B. Wire 9B / 122B tuned prompts into the app (mechanism already exists from 35B)
App still uses the shared prompt for these, so they run at 63.5 / 61.8, not the tuned
numbers. Prompts saved: `~/ocr-bench/winning_prompts/qwen35-9b.*`, `qwen35-122b.*`.
- [ ] Qwen35-9B → 68.1% (+5.5). md-tables + CONCRETE furniture examples + COMPLETENESS
      safeguard (without the safeguard it over-omits dense text). temp 0.
- [ ] Qwen35-122B → 70.7% (+8.9). Same + page-number phrase. NOTE: 122B tables collapse
      at temp 0 unless the markdown-table directive is present.
- [ ] Add to `MODEL_SYSTEM_PROMPTS` / `MODEL_USER_PROMPTS` / `MODEL_SAMPLING`(temp0) in
      `worker/app/tasks.py` (same pattern as Qwen3.6-35B).
- [ ] Update chart rows: Qwen35-9B 63.5→68.1, Qwen35-122B 61.8→70.7; redeploy.

## C. Finish Gemma4-26B / -31B benchmark + chart
`~/ocr-bench/run_fixups.sh` is re-running them at parallel-1 (llama.cpp single-query;
parallel-6 timed out → 93% blank). Long pole (~hours on Pascal).
- [ ] When done: score each (isolated dir, set `PLAYWRIGHT_BROWSERS_PATH`).
- [ ] Replace chart `pending†` rows (`web/app/static/index.html`) with real numbers; redeploy.

## D. Docling-VLM (qwen3.6 + Docling) — DECISION NEEDED
Best our-code-only config = **html-path 70.0%** (consume `html_content` not `md_content`,
convert with math-protecting `_html_to_markdown` + entity-decode in math). Still < standalone
35B (72.9%). Tables (44%) capped by Docling's table-model round-trip — NOT fixable via our
prompting or post-processing (confirmed: hybrid splice failed 61.2%; prompting can't bypass
Docling's doc-model rebuild). Unique strengths: headers 72%, old-scan-math 82%.
- [ ] DECIDE: (1) ship html-path in `docling.py` (correctness fix + best for header-heavy
      docs; replaces the md-path unescape currently there) — ref `~/ocr-bench/docling_html_bench.py`;
      OR (2) modify the `docling-serve` image to fix the table serializer (the one Docker
      change, version-fragile per memory) to push past standalone; OR (3) drop Docling-VLM,
      keep standalone as default.

## E. Commit
- [ ] Once §A–§D land, commit the working tree (14 files). Currently all uncommitted.

## F. Reference (in ~/ocr-bench/)
- Logs: `results/q9_iter_log.md`, `q122_iter_log.md`, `q35_iter_log.md`, `prompt_iter_log.md`
- Harnesses: `prompt_loop_step.py`, `convert_app.py`, `docling_html_bench.py`, `docling_hybrid_bench.py`
- Final leaderboard lives in `web/app/static/index.html` chart.
- Memory: `project_ocr_prompt_tables.md`, `project_ocr_benchmark.md`.
