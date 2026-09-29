# Prompt: build the Manufacturing Analytics Pilot presentation film

Paste everything below the line into a fresh Claude Code session opened at the repository root.

---

You are producing a presentation film for the **Manufacturing Analytics Pilot** in this repository. It will be shown to an auditorium: an ERP director, a manufacturing director, a CTO and a hiring panel for a freelance data-engineering role on an 8-week analysis pilot at a sheet-metal and assembly plant. The film has to convince them, in about 11–13 minutes, that this model is useful for their decisions: where WIP buffers belong, how large they should be, which planning policy fits, and how forecast changes hit lead time and cost.

The film must feel cinematic and deliberate. It must also be 100% truthful. Its credibility with this audience comes from traceable numbers, not effects. Every effect in the film serves the evidence.

## 0. Read first (do not skip)

1. `docs/demo_film/SCRIPT.md` has the scene-by-scene voiceover, on-screen directions and timings. It is your base script.
2. `docs/demo_film/project/slides/*.html` are the slides. `deck.json` gives their order. The published deck is https://claude.ai/artifact/QeYmKRA5JzX8fqxyp3LC4N.
3. `PLAN.md`, `tasks/todo.md` and `tasks/lessons.md` describe what exists, what doesn't, and why.
4. `git log --oneline` and `git status` show the checkpoints. Also check for uncommitted work that isn't yours. Never overwrite it; ask first.

## 1. Non-negotiable truth rules

- **Show only what runs.** Before scripting any scene, verify the feature exists by running it. If the React dashboard, Evidence Drawer, scenario API or AI copilot are not built and working, those scenes are cut, not mocked. No fake UI, no AI-generated footage of the product, no stock footage passed off as the plant.
- **Every number on screen or in the voiceover comes from a command you ran in this session.** Keep a `docs/demo_film/NUMBERS.md` log: each number, the command that produced it, and the date. If a number in `SCRIPT.md` differs from today's run, update the script, not the output.
- **Label what isn't real.** The data is synthetic (seeded generator). The M3, IBM i, Qlik Cloud, Bright Analytics and MES connections are simulated adapters. Say so on screen within the first 90 seconds, as scene 3 already does. Keep the MEASURED / DERIVED / ASSUMED tags visible where a value is shown.
- **Scene 15 (lead time) is blocked** until the lead-time correction is merged and its tests pass. Lead time must differ between SHOCK and CAPACITY_ONLY, with processing time unchanged and the response lagged, not instant. If it isn't merged, cut scene 15 and say so in your final report. Never show the old table where both read 6.40 days.
- **No claims of production-readiness, certification, DDMRP compliance or real client results.**
- **Any intervention shown in operational terms** (shifts, cells, overtime) must be backed by calendar arithmetic and an engine run at that exact multiplier. See scene 14.

## 2. Prerequisites (check each one, and stop and report any that fail)

- Docker stack up: `docker compose up -d`. Rebuild `api` so the container runs current code: `docker compose build api`.
- The full test suite passes inside the container. Record the exact count for scene 16.
- `ffmpeg` and `ffprobe` are installed and on PATH.
- A text-to-speech provider key is in an environment variable (ask which one: `ELEVENLABS_API_KEY` or `OPENAI_API_KEY`). Never write keys into files or logs. **Or** the user chooses to record their own voice. In that case, generate a teleprompter file and a per-scene recording checklist instead of TTS audio.
- A music bed: a royalty-free track the user supplies at `docs/demo_film/assets/music.*`. Never download music yourself.
- Placeholders filled: presenter name and contact on the cover and closing slides, and every `[__]` in `SCRIPT.md`.

## 3. Creative direction ("breathtaking", but honest)

**Arc:** tension, then evidence, then resolution.
1. **Cold open (0:00–0:20):** black screen. One line of kinetic type: *"A customer raises its forecast by 40%. Which work centre breaks first, and what actually fixes it?"* Then a hard cut to the welding backlog curve drawing itself: grey baseline, then the orange shock line climbing to 340.8 h. Only then the title card.
2. **Act I, trust the data (scenes 2–7):** fast, confident, lots of real terminal output. Row counts roll up as counters. The 641 findings split into blocking, assumption-based and tolerable. The planted-to-found cross-check lands with a tick sound.
3. **Act II, the model (scenes 8–10):** calmer and more explanatory. The BOM netting animates level by level (100 → minus 80 on hand → build 20). WAPE bars grow by horizon. The capacity waterfall builds tier by tier.
4. **Act III, the decision (scenes 11–15):** the climax. The shock curve again, then the four interventions race as bars: shock 340.8 h, buffer 239.3 h, capacity 14.8 h, combined 0.0 h. Hold on silence for one beat when "combined" hits zero. Then the conservation proof: the numbers on each side of the equation slide together and read 0.000000.
5. **Resolution (scenes 16–19):** the test suite scrolling green at 4× speed, the integration diagram, the 8-week plan, and the closing line: *"Every number traces to a rule, an input and a source record."*

**Visual grammar:** use the deck's palette and typefaces: navy `#16202E`, warm light `#F3F1EB`, weld orange `#D9622B`/`#B04A18`, steel blue `#2F6690`, Oswald / IBM Plex Sans / JetBrains Mono. Orange means stress, blue means intervention, grey means baseline, everywhere. Motion is purposeful: slow push-ins on slides (≤5% zoom over the scene), line charts that draw left to right, numbers that count up to their true value. No spinning 3D, no lens flares, no stock people.

**Sound:** the music bed ducks under the voice at −18 dB and swells only at the cold open, the Act III climax and the close. Loudness is normalized to −16 LUFS integrated, true peak −1 dBTP.

**Pacing:** narration at 140–150 words per minute. Hold every screen-recording beat long enough to read (at least 2.5 s per number called out). Terminal text must be legible at 1080p: 20pt+ font, a dark theme matching navy.

## 4. Production pipeline (build it as re-runnable scripts under `docs/demo_film/pipeline/`)

1. **Numbers pass:** run `scripts/smoke_test.py`, `app.dq.run_dq_engine`, `app.analytics.run_cp3_report` and `app.analytics.run_cp3_2_report` (plus the lead-time report once it exists) inside the container. Write their outputs to `docs/demo_film/runs/` and reconcile them into `NUMBERS.md`. Update `SCRIPT.md` and the slides wherever a value changed.
2. **Slides to frames:** export the deck to PDF (ask the user to download it from the artifact's Share › Export if you can't), then rasterize to 1920×1080 PNGs (`pdftoppm -r` sized to 1920 px wide). One PNG per scene.
3. **Animated data beats:** render the cold-open curve, the counters, the WAPE bars, the capacity waterfall and the intervention race as short clips. Use matplotlib animation or HTML + Playwright video capture, drawn from the numbers in `runs/`, never hardcoded. 1920×1080, 30 fps, same palette.
4. **Screen recordings (scenes 5, 7, 16, and the dashboard if it exists):** script each one so it is repeatable. For the terminal, use a scripted typing replay (for example `asciinema` + `agg`, or a PowerShell script driving a fixed-size Windows Terminal captured with `ffmpeg -f gdigrab`). For SQL, show SSMS or Azure Data Studio running the query from scene 7. For a browser UI, use Playwright's video recording with scripted clicks. Trim dead time; speed up long runs at 4× with an on-screen "4×" tag.
5. **Voice:** split `SCRIPT.md` into one audio file per scene (TTS, or the user's own recordings). Measure each clip's duration with `ffprobe` and let the audio drive scene lengths, not the other way round.
6. **Captions:** generate `film.srt` from the per-scene voiceover text and measured durations. Captions are required for accessibility and for rooms with bad acoustics.
7. **Assembly:** use one `ffmpeg` filter graph (or a Python driver that builds it). Push-ins on stills with `zoompan`, 0.5 s crossfades with `xfade`, music ducked with `sidechaincompress`, then `loudnorm`. Output `docs/demo_film/out/film_1080p.mp4` (H.264, yuv420p, 30 fps, AAC 192k), plus `film_720p.mp4` for email.
8. **Review pass:** extract a contact sheet (one frame every 10 s) and check it yourself for typos, overflow, unreadable text and any number not in `NUMBERS.md`. Fix, re-render, and report.

## 5. Deliverables

- `docs/demo_film/out/film_1080p.mp4`, `film_720p.mp4`, `film.srt`
- `docs/demo_film/NUMBERS.md`: the provenance log for every figure in the film
- `docs/demo_film/pipeline/`: scripts that rebuild the film end to end from a running stack
- An updated `SCRIPT.md` that matches the final cut exactly
- A final report: film length, scenes cut and why, every number that changed from the original script, and anything the user must do by hand (voice recording, music file, deck export)

## 6. Working rules

- Plan first (`tasks/todo.md`), then build. Don't commit or push unless the user asks.
- Don't touch the analytics engine to make the film look better. If you find a genuine correctness issue, stop and report it.
- Keep large binaries (audio, video, frames) out of git; add them to `.gitignore`.
- When a choice is really the user's to make (voice, music, cutting a scene, the presenter's name), ask. Otherwise decide and note the decision in the report.
