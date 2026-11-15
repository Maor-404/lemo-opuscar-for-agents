---
name: lemo-opuscar
description: Direct and produce a short film made entirely in code, in one of the styles of the Lemo-Opuscar library (e.g. Impasto Oil Painting 油画厚涂, Watercolor Brush 水彩笔刷, Ukiyo-e 浮世绘, Satellite Briefing 卫星简报, Exploded Teardown 产品拆解). Use when the user asks for a video, film, short, promo, explainer or animation (视频、短片、动画、宣传片) in a named style, including explainers built from real material such as satellite imagery, laser scans, CAD files or archive scans (数据解说、科普、产品拆解), and new films cut from real archive footage and documents (剪辑纪实, e.g. 用档案影像剪一支新闻片); when they want a film about their own topic and haven't chosen a style (help them choose); when they want to change a style into their own, make their own style from a blank page, or use a style they saved (改风格、从空白开始、我的风格); when they start the skill without saying what they want; or when they ask which film styles there are. Not for plain editing, trimming or converting the user's existing video files.
---

# Lemo Opuscar for Agents

The styles, guides and tools live in the **Lemo Opuscar for Agents** library (github.com/Maor-404/lemo-opuscar-for-agents, forked from Lemo-Opuscar by Lemomo / LemoLab at github.com/lemomo-ai/lemo-opuscar with multi-agent support and an npm CLI). This skill fetches it and hands you over to it.

In the steps below, `<skill directory>` is the folder containing this `SKILL.md` (for example `.agents/skills/lemo-opuscar` in a repo clone, `plugin/skills/lemo-opuscar` in the plugin, or the base directory reported by your agent).

1. **Get the library.** Run `sh "<skill directory>/scripts/setup.sh"` (or `python3 "<skill directory>/scripts/setup.py"` in Windows PowerShell/CMD when `sh` is not on PATH; it locates Git Bash, MSYS2, or WSL). It clones or updates the library (default `~/lemo-opuscar`; a clone you are already inside is used as it is), lists any missing system tools (Node, ffmpeg, Python) and prints `LIB=<path>` on its last line.
2. **Install the core tools early.** Start `sh "<skill directory>/scripts/setup.sh" deps` (or `python3 "<skill directory>/scripts/setup.py" deps` on Windows) right away (in the background if your environment supports it): it takes a few minutes the first time. Add `deps voice` or `deps music` later, only if the film needs them (`$LIB/TECHNIQUE.md` §1). Report missing system tools (Node, ffmpeg, Python) in your one round of questions; don't install system software yourself. A style marked ✦ also needs an image generator: check readiness from `$LIB` with `python3 core/image/gen_image.py --check` (`--provider auto|codex|openai|gemini`, supporting signed-in Codex CLI, `OPENAI_API_KEY`, or `GEMINI_API_KEY`/`GOOGLE_API_KEY`; see `$LIB/AGENTS.md`, Workflow, Brief). Packages a style needs (its `STYLE.md` lists them) you install yourself, into the project folder.
3. **Follow the library.** Read `$LIB/AGENTS.md` and do what it says, starting with **Start** (the user's own styles in `~/lemo-my-styles/`; when they started the skill without writing anything, the language question first, then the ways in): finding the style or making their own, the one round of questions, the treatment, production and delivery.

| What | Where / how |
|---|---|
| Library root | `$LIB` (what `setup.sh` printed). Tools live there; read `$LIB/AGENTS.md`, `$LIB/DIRECTOR.md`, `$LIB/TECHNIQUE.md`, `$LIB/styles/README.md` |
| Project folder | make `<name>/` where the user asked, or `films/<name>/` inside the library when working in a clone of the repo. Put every file you write (treatment, build scripts, assets, renders) there; never write into `$LIB` outside `films/` or edit the library's files |
| Running tools | from `$LIB`, with the project's absolute path: `cd "$LIB" && node core/render/still.mjs "<project>" 1.5 3` |
| User's own styles | `~/lemo-my-styles/` (list in `~/lemo-my-styles/README.md`, rules in `$LIB/MY-STYLES.md`) |
| Big assets (on demand) | Kokoro voice model installs with `deps voice`; `sampler.py` fetches instrument sample libraries and `sh tools/fetch.sh hdri` fetches HDRIs (only when a style's `STYLE.md` calls for them) |
| Image generation (`✦`) | `python3 core/image/gen_image.py --check` and `gen_image.py "<prompt>"` (`--provider auto\|codex\|openai\|gemini`; `$LIB/core/README.md`, "Image generation") |
| Demo source | not downloaded. `sh "<skill directory>/scripts/setup.sh" demo <slug>` (or `python3 "<skill directory>/scripts/setup.py" demo <slug>` on Windows), only after your `TREATMENT.md` is written, to read its techniques; it is not meant to be rendered. An Edit & Documentary style is the exception: fetch it before the treatment to use the material tools its `STYLE.md` names in `demo/tools/` (`$LIB/AGENTS.md`, "Treatment first") |
