# Video transcripts for research

`transcribe_video.py` turns a recorded programme (a press video, a talk show, an extension broadcast)
into a timestamped Markdown transcript, so the facts in it can be found, checked and cited. It runs
entirely on your own machine with [faster-whisper](https://github.com/SYSTRAN/faster-whisper); nothing
is sent to an online service.

## Setup

The speech model is a large optional download, so it has its own requirements file:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_env.ps1 -Video          # CPU
powershell -ExecutionPolicy Bypass -File scripts\setup_env.ps1 -Video -Gpu     # NVIDIA GPU (CUDA 12)
```

```bash
bash scripts/setup_env.sh --video          # add --gpu for an NVIDIA GPU
```

The GPU option installs the CUDA 12 libraries (cuBLAS, cuDNN 9) as Python packages, about 1 GB. It
needs a current NVIDIA driver, not the CUDA toolkit. Without a GPU the tool runs on CPU, more slowly.

## Steps

1. Save the media file from the publisher's page into a folder of its own. The tool never downloads
   media. If the site blocks automated downloads, save it through the browser.
2. Transcribe it. Fill in the page details now, as they go into the transcript's header:

   ```bash
   python scripts/video-transcript/transcribe_video.py "<file.mp4>" --page "<page URL>" \
       --title "<title>" --publisher "<publisher>" --published "dd/mm/yyyy" \
       --prompt "<key words: crops, diseases, chemicals, places, institutions>"
   ```

3. Read the transcript against the recording and edit the Markdown by hand:
   - name the speakers in the `### mm:ss (speaker)` headings and the Speakers table, with where each
     name and title was given (on-screen caption, host introduction),
   - list mis-heard words in the mis-hearings table rather than editing the raw text, so the
     transcript stays a faithful record of the machine output.
4. Write a separate notes file with the usable facts, each with its timestamp, and flag every spoken
   figure until it has been checked against a primary source.

`--from-tsv --force` rebuilds the Markdown from the `.tsv` without transcribing again, for example
with a different `--turn-gap`. It overwrites hand edits to the Markdown.

## Using the transcript

Most press programmes are all rights reserved, and many publishers require written consent for any
reuse. A transcript made for research is not a licence to republish. So:

- **Keep the transcript private.** Do not publish it, upload it to the wiki or the project sheet, or
  share it outside the project. Do not upload, embed or screenshot the media itself.
- **Paraphrase** facts into articles. Quote at most a sentence, with the speaker's name and title.
- **Cite the programme's page** (the publisher's article URL), never the media file's address.
- **Verify spoken figures.** Speakers misspeak years and numbers, and claims about regulations go out
  of date. Check each figure against the recording, and regulatory figures against the regulation
  itself, before it goes into an article.
