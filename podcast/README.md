# AI Engineer → podcast

Turns the [AI Engineer YouTube channel](https://www.youtube.com/@aiDotEngineer) into a
podcast RSS feed that podcast apps (Spotify, Apple Podcasts, Pocket Casts, Overcast, …)
can subscribe to.

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

1. **Default branch.** Scheduled workflows only run on the default branch, so merge this
   into `main`, or make this branch the default.
2. **Pages.** Go to *Settings → Pages → Build and deployment → Source* and choose
   **GitHub Actions**.
3. **Secrets** (*Settings → Secrets and variables → Actions*):
   - `PODCAST_OWNER_EMAIL`: the email Spotify sends its ownership verification code to.
     It's published in the feed.
   - `YT_COOKIES` (optional, but usually needed): YouTube often blocks GitHub's servers with
     "Sign in to confirm you're not a bot". Export cookies for youtube.com from a
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

## Getting it into Spotify

Spotify has no way to subscribe to a private RSS URL. The only way in is for the show's
owner to submit the feed publicly through
[Spotify for Creators](https://creators.spotify.com) (*Add an existing podcast → Find
your podcast by RSS feed*). After you verify the code sent to `PODCAST_OWNER_EMAIL`, the
show appears in Spotify, usually within a few hours, and new episodes follow on their own.

**Rights:** that makes the show public on Spotify, and the talks are AI Engineer's content.
Get their permission before submitting, or Spotify may take the show down.
If this is only for you, add the feed URL to an app that accepts private RSS feeds instead:
Apple Podcasts (*Library → … → Follow a Show by URL*), Pocket Casts, Overcast, AntennaPod
or Podcast Addict all do.

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
