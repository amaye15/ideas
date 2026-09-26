# AI Engineer → podcast

Turns the [AI Engineer YouTube channel](https://www.youtube.com/@aiDotEngineer) into a
private podcast feed you can subscribe to in Pocket Casts, Apple Podcasts, Overcast
or any other app that accepts an RSS feed URL.

How it works, all on free GitHub infrastructure:

1. A scheduled GitHub Action (`.github/workflows/podcast.yml`, every 6 hours) runs
   `podcast.py sync`. It lists the newest uploads with
   [yt-dlp](https://github.com/yt-dlp/yt-dlp), skips Shorts, and converts new
   videos to 64 kbps mono MP3 (about 30 MB per hour of talk).
2. Each MP3 is attached to a GitHub Release (`episodes-YYYYMM`), which serves the audio.
3. The episode list is committed back to `podcast/episodes.json`.
4. `podcast.py feed` writes `feed.xml` and the cover art, which get deployed to GitHub Pages at
   **`https://<owner>.github.io/<repo>/feed.xml`**.

## Setup

1. **Default branch.** Scheduled workflows only run on the default branch, so make sure
   `main` is the default (*Settings → General*) and is allowed to deploy under
   *Settings → Environments → github-pages*.
2. **Pages.** Go to *Settings → Pages → Build and deployment → Source* and choose
   **GitHub Actions**.
3. **Secrets** (*Settings → Secrets and variables → Actions*):
   - `PODCAST_OWNER_EMAIL` (optional): only needed if you ever submit the feed to a
     directory. It's published in the feed.
   - YouTube often blocks GitHub's servers with "Sign in to confirm you're not a bot".
     The workflow first tries without a login, using a proof-of-origin token server
     ([bgutil](https://github.com/Brainicism/bgutil-ytdlp-pot-provider)). If runs still
     stop with that message, add one of these:
   - `YT_PROXY` (optional): a residential proxy URL, e.g. `http://user:pass@host:port`.
   - `YT_COOKIES` (optional): Export cookies for youtube.com from a
     logged-in browser in Netscape `cookies.txt` format (for example with the "Get
     cookies.txt LOCALLY" extension) and paste the whole file in as the secret. Using a
     throwaway Google account is a good idea.
4. **First run.** Under *Actions → AI Engineer podcast → Run workflow*, you can leave the
   inputs blank. Each run adds up to 10 episodes (`max_new_per_run`), so the most recent
   100 uploads (`scan_limit`) fill in over a day or two. To go further back, run the
   workflow with a larger `scan_limit`, or set `oldest_upload_date` in `config.toml` to
   limit how far back it goes.
5. **Subscribe.** Check that `https://<owner>.github.io/<repo>/feed.xml` loads, then add it
   to your podcast app (see below).

## Listening

The feed is private: it tells podcast directories not to list it (`private = true` in
`config.toml`), so the talks aren't republished anywhere. Subscribe to the feed URL
directly in an app that accepts one. **Pocket Casts** is the recommended option: it works
on iOS, Android, the web and desktop, and downloads new episodes automatically.

1. Open Pocket Casts → **Search** → paste `https://<owner>.github.io/<repo>/feed.xml`.
2. Subscribe, then in the podcast's settings turn on **Auto download** (and optionally
   **Add to Up Next**) so new episodes arrive with no further effort.

Other apps that take a feed URL: Apple Podcasts (*Library → … → Follow a Show by URL*),
Overcast (*+ → Add URL*) and AntennaPod (*+ → Add podcast by RSS address*).

Spotify can't subscribe to a feed URL; a show only gets there by being publicly
submitted through Spotify for Creators, which needs the rights to the content.

## Local use

```sh
pip install -r podcast/requirements.txt   # also needs ffmpeg (and deno for YouTube)
GITHUB_REPOSITORY=owner/repo python podcast/podcast.py sync --dry-run --max-new 1
GITHUB_REPOSITORY=owner/repo python podcast/podcast.py feed --out /tmp/feed
```

## Configuration

See `config.toml`: channel URL, number of episodes per run, minimum length, audio bitrate,
and feed title, description and category. Point `channel_url` at any other channel to mirror it
instead.
