"""Make a private research transcript of a video or audio source, with timestamps, using faster-whisper.

For press videos, talk shows and extension broadcasts that hold facts an article needs. The machine
transcript lets an editor find, quote and verify what was said. It is a research copy, not a publication:
see README.md in this folder for how such sources may be used.

    python scripts/video-transcript/transcribe_video.py <media file> [options]

    python scripts/video-transcript/transcribe_video.py talkshow.mp4 \\
        --page "https://example.org/video/page.html" --title "Tên chương trình" --publisher "Báo Ví dụ" \\
        --published "07/10/2026" --prompt "sầu riêng, cadimi, ĐBSCL, phân bón"

Input: any file FFmpeg can decode (mp4, mkv, webm, mp3, m4a, wav ...). faster-whisper reads it through
PyAV, which ships its own FFmpeg, so no separate FFmpeg install is needed. Download the file yourself,
from the page or with the publisher's own download link: this tool never fetches media.

Output, beside the media file (or in --out-dir):
    <name>.tsv              start, end, text per segment. Written as the model runs, so a long run that is
                            stopped keeps what it has done. --from-tsv rebuilds the Markdown from it.
    <name>-transcript.md    the research copy: a "not for publication" header, a details table (page,
                            publisher, date, model), empty tables for speakers and mis-heard words to fill
                            in by hand, then the text with a `mm:ss` stamp on every line. A new
                            `### mm:ss (speaker)` heading starts at every pause of --turn-gap seconds or
                            more, so speaker turns can be named by editing the headings. The file holds hand
                            edits, so it is not overwritten without --force.

Model and speed: the default `large-v3-turbo` is the best trade-off found for Vietnamese. On an NVIDIA
GPU (CUDA 12, installed with `setup_env --gpu`) a 30-minute programme takes a few minutes. On CPU it runs
at roughly real time or slower: try `--model medium` or `small` for a first look. The model is downloaded
from Hugging Face on first use and cached (about 1.6 GB for large-v3-turbo).

Accuracy: Whisper mishears names, technical terms and figures. --prompt with the programme's key words
(crop, disease, chemical, place and institution names) helps noticeably. Every figure taken from a
transcript must be checked against the recording, and regulatory numbers against the regulation itself.
"""
import argparse
import csv
import os
import site
import sys
import time
from datetime import date
from pathlib import Path

MODELS = ["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"]


def add_cuda_dll_dirs():
    """On Windows, let ctranslate2 find the CUDA libraries installed as pip wheels (nvidia-cublas-cu12,
    nvidia-cudnn-cu12). Linux finds them through the wheels' own paths. A no-op when they are absent."""
    if os.name != "nt":
        return
    for sp in site.getsitepackages() + [site.getusersitepackages()]:
        for sub in ("cublas", "cudnn", "cuda_nvrtc"):
            d = os.path.join(sp, "nvidia", sub, "bin")
            if os.path.isdir(d):
                os.add_dll_directory(d)
                os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]


def stamp(seconds, hours):
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if hours else f"{s // 60:02d}:{s % 60:02d}"


def transcribe(media, tsv, args):
    add_cuda_dll_dirs()
    try:
        import ctranslate2
        from faster_whisper import WhisperModel
    except ImportError:
        sys.exit("faster-whisper is not installed. Run the setup script with --video "
                 "(PowerShell: -Video), or: pip install -r scripts/requirements-video.txt")
    device = args.device
    if device == "auto":
        device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    compute = args.compute or ("int8_float16" if device == "cuda" else "int8")
    print(f"Model {args.model} on {device} ({compute}). The first run downloads the model.", file=sys.stderr)
    t0 = time.time()
    model = WhisperModel(args.model, device=device, compute_type=compute)
    segments, info = model.transcribe(
        str(media),
        language=args.language,
        beam_size=5,
        vad_filter=True,                    # skip music and silence, which Whisper otherwise fills with text
        condition_on_previous_text=False,   # stops one mis-hearing from repeating through the rest
        initial_prompt=args.prompt or None,
    )
    hours = info.duration >= 3600
    with open(tsv, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(["start", "end", "text"])
        for seg in segments:
            w.writerow([f"{seg.start:.2f}", f"{seg.end:.2f}", seg.text.strip()])
            fh.flush()
            print(f"\r  {stamp(seg.end, hours)} / {stamp(info.duration, hours)}", end="", file=sys.stderr)
    print(f"\n{info.duration:.0f} s of audio in {time.time() - t0:.0f} s -> {tsv}", file=sys.stderr)
    return {"model": args.model, "device": device}


def read_tsv(tsv):
    with open(tsv, encoding="utf-8", newline="") as fh:
        return [(float(r["start"]), float(r["end"]), r["text"]) for r in csv.DictReader(fh, delimiter="\t")]


def write_markdown(md, segs, args, run):
    hours = bool(segs) and segs[-1][1] >= 3600
    length = stamp(segs[-1][1], hours) if segs else "?"
    title = args.title or Path(args.media).stem
    cell = lambda v: (v or "").replace("|", "\\|")  # noqa: E731
    L = [
        f'# Transcript: "{title}"',
        "",
        "> **Research copy, not for publication.** Machine transcript of a recorded programme, made for",
        "> research only. Do not paste this text, or long runs of it, into a wiki page or the project sheet.",
        "> Paraphrase the facts, quote at most a sentence with the speaker's name and title, and cite the",
        "> programme's page, not the media file. Check every figure against the recording.",
        "",
        "|                  |   |",
        "|------------------|---|",
        f"| Publisher        | {cell(args.publisher)} |",
        f"| Page (cite this) | {cell(args.page)} |",
        f"| Media file       | `{Path(args.media).name}` ({length}) |",
        f"| Published        | {cell(args.published)} |",
        f"| Transcribed      | {date.today():%d/%m/%Y}, faster-whisper `{run['model']}`, language `{args.language}`, VAD on. Not hand-corrected |",
        "",
        "## Speakers",
        "",
        "| Label | Who | Source of name and title |",
        "|-------|-----|--------------------------|",
        "|       |     | (on-screen caption or host introduction, with its time) |",
        "",
        "Turn boundaries below start at pauses and are named by hand.",
        "",
        "## Whisper mis-hearings",
        "",
        "Read these substitutions into the raw text below.",
        "",
        "| Raw text | Means |",
        "|----------|-------|",
        "|          |       |",
        "",
        "## Transcript (raw Whisper output)",
    ]
    prev_end = None
    for start, end, text in segs:
        if prev_end is None or start - prev_end >= args.turn_gap:
            L += ["", f"### {stamp(start, hours)} (speaker)", ""]
        L.append(f"`{stamp(start, hours)}` {text}  ")
        prev_end = end
    md.write_text("\n".join(L).rstrip() + "\n", encoding="utf-8")
    print(f"{len(segs)} segments -> {md}", file=sys.stderr)


def main():
    sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="See the module docstring (or README.md here) for the full description.")
    ap.add_argument("media", help="video or audio file to transcribe")
    ap.add_argument("--out-dir", type=Path, help="folder for the .tsv and .md (default: beside the media file)")
    ap.add_argument("--model", default="large-v3-turbo", choices=MODELS)
    ap.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    ap.add_argument("--compute", help="ctranslate2 compute type (default int8_float16 on GPU, int8 on CPU)")
    ap.add_argument("--language", default="vi", help="spoken language code (default vi)")
    ap.add_argument("--prompt", default="", help="key words of the programme, comma separated, to steer spelling")
    ap.add_argument("--prompt-file", type=Path, help="read --prompt from a UTF-8 text file")
    ap.add_argument("--turn-gap", type=float, default=2.0, help="pause (s) that starts a new heading (default 2)")
    ap.add_argument("--title", help="programme title, as on its page")
    ap.add_argument("--page", help="URL of the programme's page: the one to cite")
    ap.add_argument("--publisher", help="publisher, as the citation will name it")
    ap.add_argument("--published", help="publication date, dd/mm/yyyy")
    ap.add_argument("--from-tsv", action="store_true", help="skip transcription, rebuild the .md from the .tsv")
    ap.add_argument("--force", action="store_true", help="overwrite an existing -transcript.md")
    args = ap.parse_args()
    if args.prompt_file:
        args.prompt = args.prompt_file.read_text(encoding="utf-8").strip()

    media = Path(args.media)
    out = args.out_dir or media.parent
    out.mkdir(parents=True, exist_ok=True)
    tsv, md = out / f"{media.stem}.tsv", out / f"{media.stem}-transcript.md"
    if md.exists() and not args.force:
        sys.exit(f"{md} exists and may hold hand edits. Use --force to overwrite it.")
    if args.from_tsv:
        if not tsv.is_file():
            sys.exit(f"No {tsv} to rebuild from.")
        run = {"model": args.model}
    else:
        if not media.is_file():
            sys.exit(f"No such file: {media}")
        run = transcribe(media, tsv, args)
    write_markdown(md, read_tsv(tsv), args, run)


if __name__ == "__main__":
    main()
