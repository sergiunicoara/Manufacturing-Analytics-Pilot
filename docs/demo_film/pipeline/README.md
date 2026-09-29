# Demo film pipeline

`build_film.py` produces a self-contained 1080p MP4 from the current local API,
the slide fragments, animated data beats, OpenAI text-to-speech narration, and
captions. It deliberately does not download music.

Run from the repository root while the local API is available:

```powershell
python docs/demo_film/pipeline/build_film.py
```

Prerequisites: Python with `playwright` and `matplotlib`, FFmpeg at
`C:\ffmpeg\bin\ffmpeg.exe` (or `FFMPEG_BIN`), and `OPENAI_API_KEY`. The key is
read only from the environment and is never written to the workspace. The film
discloses AI-generated narration on scene 3 and in its production report.

Outputs are ignored by Git: `out/manufacturing_analytics_pilot_1080p.mp4`, a
720p companion, captions, and a contact sheet. `runs/NUMBERS.md` captures the
API evidence used in that particular render.
