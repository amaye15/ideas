#!/usr/bin/env python3
"""Mirror a YouTube channel as a podcast.

  sync   find new uploads, convert them to MP3 and attach them to GitHub
         releases (needs the `gh` CLI and GH_TOKEN), recording each one in
         episodes.json
  feed   write feed.xml (+ cover art) for the recorded episodes into a folder
         that gets published with GitHub Pages
"""

from __future__ import annotations

import argparse
import email.utils
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.toml"
STATE_PATH = HERE / "episodes.json"
COVER_PATH = HERE / "cover.jpg"

# Give up on a video after this many failed download attempts (members-only,
# region-locked, removed, ...). Premieres that aren't out yet also fail, so
# this is more than one.
MAX_ATTEMPTS = 3

# Errors that mean YouTube is blocking this machine rather than one video
# being unavailable. There's no point trying the rest of the list.
# YouTube player clients to try, in order, when one gets the bot check.
# Different clients face different checks, so another may still get through.
PLAYER_CLIENTS = (
    ["default", "mweb"],
    ["tv_simply"],
    ["tv"],
    ["web_embedded"],
    ["android_vr"],
    ["web_safari"],
)
_client_index = 0

BLOCKED_MARKERS = ("confirm you’re not a bot", "confirm you're not a bot", "HTTP Error 429")

ITUNES = "http://www.itunes.com/dtds/podcast-1.0.dtd"
ATOM = "http://www.w3.org/2005/Atom"
ET.register_namespace("itunes", ITUNES)
ET.register_namespace("atom", ATOM)


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def load_config() -> dict:
    with CONFIG_PATH.open("rb") as f:
        return tomllib.load(f)


def load_state() -> dict:
    if STATE_PATH.exists():
        state = json.loads(STATE_PATH.read_text())
    else:
        state = {}
    state.setdefault("channel", {})
    state.setdefault("episodes", [])
    state.setdefault("skipped", {})
    state.setdefault("failures", {})
    return state


def save_state(state: dict) -> None:
    state["episodes"].sort(key=lambda e: e["published"], reverse=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------
# YouTube


def ydl_options(**extra) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "extractor_args": {"youtube": {"player_client": PLAYER_CLIENTS[_client_index]}},
    }
    proxy = os.environ.get("YT_PROXY", "").strip()
    if proxy:
        opts["proxy"] = proxy
    # GitHub-hosted runners are often challenged by YouTube's bot check;
    # exported browser cookies (Netscape cookies.txt format) get past it.
    cookies = os.environ.get("YT_COOKIES", "").strip()
    if cookies:
        path = Path(tempfile.gettempdir()) / "yt-cookies.txt"
        path.write_text(cookies + "\n")
        opts["cookiefile"] = str(path)
    opts.update(extra)
    return opts


def log_antibot_setup() -> None:
    try:
        urllib.request.urlopen("http://127.0.0.1:4416/ping", timeout=5)
        pot = "PO token server up"
    except OSError:
        pot = "no PO token server"
    log(f"Anti-bot: {pot}; cookies {'set' if os.environ.get('YT_COOKIES', '').strip() else 'not set'}; "
        f"proxy {'set' if os.environ.get('YT_PROXY', '').strip() else 'not set'}")


def list_channel(channel_url: str, limit: int) -> dict:
    from yt_dlp import YoutubeDL

    opts = ydl_options(extract_flat="in_playlist", playlistend=limit)
    with YoutubeDL(opts) as ydl:
        return ydl.extract_info(channel_url, download=False)


def download_audio(video_url: str, workdir: Path, audio: dict, cfg: dict) -> tuple[dict | None, Path | None, str]:
    """Return (info, mp3_path, skip_reason). skip_reason is set for videos
    that should never be retried."""
    from yt_dlp import YoutubeDL

    pp_args = ["-ac", "1"] if audio.get("mono", True) else []
    opts = ydl_options(
        format="bestaudio/best",
        outtmpl=str(workdir / "%(id)s.%(ext)s"),
        postprocessors=[{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": str(audio.get("bitrate_kbps", 64)),
        }],
        postprocessor_args={"extractaudio": pp_args},
    )
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(video_url, download=False)
        if info.get("live_status") in ("is_live", "is_upcoming", "post_live"):
            return info, None, ""  # try again once it's a normal upload
        if (info.get("duration") or 0) < cfg["min_duration_seconds"]:
            return info, None, "too short"
        cutoff = cfg.get("oldest_upload_date") or ""
        if cutoff and (info.get("upload_date") or "") < cutoff:
            return info, None, "older than oldest_upload_date"
        if info.get("availability") in ("subscriber_only", "premium_only", "needs_auth"):
            return info, None, info["availability"]
        ydl.process_ie_result(info, download=True)
    mp3 = workdir / f"{info['id']}.mp3"
    if not mp3.exists():
        raise RuntimeError(f"expected {mp3} after download")
    return info, mp3, ""


def download_with_fallback(url: str, workdir: Path, audio: dict, cfg: dict):
    """download_audio, moving on to the next player client whenever YouTube
    answers with its bot check. The client that works is kept for the rest
    of the run."""
    global _client_index
    while True:
        try:
            return download_audio(url, workdir, audio, cfg)
        except Exception as exc:  # noqa: BLE001
            blocked = any(m in str(exc) for m in BLOCKED_MARKERS)
            if not blocked or _client_index + 1 >= len(PLAYER_CLIENTS):
                raise
            _client_index += 1
            log(f"   bot check; retrying with player client {PLAYER_CLIENTS[_client_index]}")


def published_at(info: dict) -> datetime:
    ts = info.get("release_timestamp") or info.get("timestamp")
    if ts:
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    return datetime.strptime(info["upload_date"], "%Y%m%d").replace(tzinfo=timezone.utc)


def best_thumbnail(info: dict, prefer: tuple[str, ...] = ()) -> str | None:
    thumbs = info.get("thumbnails") or []
    for want in prefer:
        for t in thumbs:
            if t.get("id") == want and t.get("url"):
                return t["url"]
    sized = [t for t in thumbs if t.get("url") and t.get("width")]
    if sized:
        return max(sized, key=lambda t: t["width"] * (t.get("height") or 1))["url"]
    return info.get("thumbnail")


# --------------------------------------------------------------------------
# GitHub releases (audio hosting)


def gh(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], check=check, capture_output=True, text=True)


def upload_to_release(repo: str, tag: str, path: Path) -> str:
    if gh("release", "view", tag, "--repo", repo, check=False).returncode != 0:
        gh("release", "create", tag, "--repo", repo, "--title", f"Podcast audio {tag}",
           "--notes", "Audio files for the podcast feed. Generated automatically.",
           "--latest=false")
    gh("release", "upload", tag, str(path), "--repo", repo, "--clobber")
    return f"https://github.com/{repo}/releases/download/{tag}/{path.name}"


# --------------------------------------------------------------------------
# Commands


def cmd_sync(args: argparse.Namespace) -> None:
    config = load_config()
    src, audio = config["source"], config.get("audio", {})
    state = load_state()
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo and not args.dry_run:
        sys.exit("GITHUB_REPOSITORY must be set (owner/repo) to upload audio")

    scan_limit = args.scan_limit or src["scan_limit"]
    max_new = args.max_new if args.max_new is not None else src["max_new_per_run"]

    log_antibot_setup()
    log(f"Listing {src['channel_url']} (newest {scan_limit})")
    channel = list_channel(src["channel_url"], scan_limit)
    state["channel"] = {
        "title": channel.get("channel") or channel.get("uploader") or channel.get("title"),
        "description": channel.get("description") or "",
        "url": channel.get("channel_url") or src["channel_url"],
        "avatar": best_thumbnail(channel, ("avatar_uncropped",)),
    }
    if not COVER_PATH.exists() and state["channel"]["avatar"]:
        make_cover(state["channel"]["avatar"])

    known = {e["id"] for e in state["episodes"]} | set(state["skipped"])
    candidates = []
    for entry in channel.get("entries") or []:
        vid = entry.get("id")
        if not vid or vid in known:
            continue
        # The flat listing already knows durations, so skip Shorts cheaply.
        if entry.get("duration") and entry["duration"] < src["min_duration_seconds"]:
            state["skipped"][vid] = "too short"
            continue
        candidates.append(entry)
    log(f"{len(candidates)} new video(s); processing up to {max_new}")

    added = tried = 0
    with tempfile.TemporaryDirectory() as tmp:
        for entry in candidates:
            # Also cap attempts, so a run of broken videos can't turn into
            # hammering YouTube with the whole list.
            if added >= max_new or tried >= max_new * 2:
                break
            tried += 1
            vid = entry["id"]
            url = entry.get("url") or f"https://www.youtube.com/watch?v={vid}"
            log(f"-> {vid} {entry.get('title', '')}")
            try:
                info, mp3, reason = download_with_fallback(url, Path(tmp), audio, src)
            except Exception as exc:  # noqa: BLE001 - yt-dlp raises many types
                if any(m in str(exc) for m in BLOCKED_MARKERS):
                    save_state(state)
                    sys.exit(
                        "YouTube is blocking this runner (\"Sign in to confirm you're not a bot\").\n"
                        "Add youtube.com cookies (YT_COOKIES) or a residential proxy (YT_PROXY) as a repository secret; see podcast/README.md."
                    )
                attempts = state["failures"].get(vid, 0) + 1
                state["failures"][vid] = attempts
                log(f"   failed ({attempts}/{MAX_ATTEMPTS}): {exc}")
                if attempts >= MAX_ATTEMPTS:
                    state["skipped"][vid] = f"failed {attempts} times: {str(exc)[:200]}"
                    state["failures"].pop(vid)
                save_state(state)
                continue
            if reason:
                log(f"   skipped: {reason}")
                state["skipped"][vid] = reason
                save_state(state)
                continue
            if mp3 is None:
                log("   not available yet, will retry next run")
                continue

            pub = published_at(info)
            size = mp3.stat().st_size
            if args.dry_run:
                media_url = f"file://{mp3}"
            else:
                media_url = upload_to_release(repo, f"episodes-{pub:%Y%m}", mp3)
            state["episodes"].append({
                "id": vid,
                "title": info.get("title") or vid,
                "description": info.get("description") or "",
                "youtube_url": info.get("webpage_url") or url,
                "published": pub.isoformat(),
                "duration": int(info.get("duration") or 0),
                "image": best_thumbnail(info, ("maxresdefault",)),
                "audio_url": media_url,
                "audio_bytes": size,
            })
            state["failures"].pop(vid, None)
            save_state(state)  # after every episode, so a crash loses nothing
            added += 1
            log(f"   published ({size / 1e6:.1f} MB)")
    save_state(state)
    log(f"Done: {added} new episode(s), {len(state['episodes'])} total")


def make_cover(avatar_url: str) -> None:
    """Podcast directories want square art of at least 1400x1400; YouTube
    avatars are usually smaller, so scale up with ffmpeg."""
    log("Creating cover art from channel avatar")
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "avatar"
        urllib.request.urlretrieve(avatar_url, src)
        subprocess.run([
            "ffmpeg", "-loglevel", "error", "-y", "-i", str(src),
            "-vf", "scale=1400:1400:force_original_aspect_ratio=decrease,"
                   "pad=1400:1400:(ow-iw)/2:(oh-ih)/2:color=black",
            "-q:v", "2", str(COVER_PATH),
        ], check=True)


def base_url(feed_cfg: dict) -> str:
    if feed_cfg.get("base_url"):
        return feed_cfg["base_url"].rstrip("/")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" not in repo:
        sys.exit("Set feed.base_url in config.toml or GITHUB_REPOSITORY=owner/repo")
    owner, name = repo.split("/", 1)
    if name.lower() == f"{owner.lower()}.github.io":
        return f"https://{owner.lower()}.github.io"
    return f"https://{owner.lower()}.github.io/{name}"


def format_duration(seconds: int) -> str:
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


def build_feed(config: dict, state: dict, site: str) -> bytes:
    fc = config["feed"]
    ch = state["channel"]
    title = fc.get("title") or ch.get("title") or "Podcast"
    description = fc.get("description") or ch.get("description") or title
    author = fc.get("author") or ch.get("title") or title
    owner_email = os.environ.get("PODCAST_OWNER_EMAIL") or fc.get("owner_email") or ""
    explicit = "true" if fc.get("explicit") else "false"

    def sub(parent, tag, text=None, /, **attrs):
        el = ET.SubElement(parent, tag, {k: str(v) for k, v in attrs.items()})
        if text is not None:
            el.text = str(text)
        return el

    it = lambda name: f"{{{ITUNES}}}{name}"  # noqa: E731

    rss = ET.Element("rss", {"version": "2.0"})
    channel = sub(rss, "channel")
    sub(channel, f"{{{ATOM}}}link", href=f"{site}/feed.xml", rel="self", type="application/rss+xml")
    sub(channel, "title", title)
    sub(channel, "link", ch.get("url") or config["source"]["channel_url"])
    sub(channel, "description", description)
    sub(channel, "language", fc.get("language", "en"))
    sub(channel, it("author"), author)
    sub(channel, it("summary"), description)
    sub(channel, it("explicit"), explicit)
    sub(channel, it("type"), "episodic")
    if fc.get("private", True):
        sub(channel, it("block"), "Yes")
    sub(channel, it("category"), None, text=fc.get("category", "Technology"))
    if COVER_PATH.exists() or fc.get("image_url"):
        image = fc.get("image_url") or f"{site}/cover.jpg"
        sub(channel, it("image"), None, href=image)
        img = sub(channel, "image")
        sub(img, "url", image)
        sub(img, "title", title)
        sub(img, "link", ch.get("url") or config["source"]["channel_url"])
    if owner_email:
        owner = sub(channel, it("owner"))
        sub(owner, it("name"), fc.get("owner_name") or author)
        sub(owner, it("email"), owner_email)
    sub(channel, "generator", "podcast.py (yt-dlp)")
    sub(channel, "lastBuildDate", email.utils.format_datetime(datetime.now(timezone.utc)))

    for ep in state["episodes"]:
        item = sub(channel, "item")
        sub(item, "title", ep["title"])
        text = f"Watch on YouTube: {ep['youtube_url']}\n\n{ep['description']}".strip()
        sub(item, "description", text)
        sub(item, it("summary"), text[:4000])
        sub(item, "link", ep["youtube_url"])
        sub(item, "guid", f"youtube:{ep['id']}", isPermaLink="false")
        sub(item, "pubDate", email.utils.format_datetime(datetime.fromisoformat(ep["published"])))
        sub(item, "enclosure", None, url=ep["audio_url"], length=ep["audio_bytes"], type="audio/mpeg")
        sub(item, it("duration"), format_duration(ep["duration"]))
        sub(item, it("explicit"), explicit)
        sub(item, it("episodeType"), "full")
        if ep.get("image"):
            sub(item, it("image"), None, href=ep["image"])

    ET.indent(rss)
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(rss, encoding="utf-8")


def cmd_feed(args: argparse.Namespace) -> None:
    config = load_config()
    state = load_state()
    site = base_url(config["feed"])
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "feed.xml").write_bytes(build_feed(config, state, site))
    if COVER_PATH.exists():
        (out / "cover.jpg").write_bytes(COVER_PATH.read_bytes())
    title = config["feed"].get("title") or state["channel"].get("title") or "Podcast"
    (out / "index.html").write_text(
        f"<!doctype html><meta charset=utf-8><title>{escape(title)}</title>"
        f"<h1>{escape(title)}</h1><p>{len(state['episodes'])} episodes. "
        f"Podcast feed: <a href=feed.xml>{escape(site)}/feed.xml</a></p>\n"
    )
    log(f"Wrote {out / 'feed.xml'} with {len(state['episodes'])} episodes ({site}/feed.xml)")


def escape(text: str) -> str:
    from html import escape as html_escape
    return html_escape(text)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subs = parser.add_subparsers(dest="command", required=True)
    p = subs.add_parser("sync", help="download new videos and publish their audio")
    p.add_argument("--max-new", type=int, help="override source.max_new_per_run")
    p.add_argument("--scan-limit", type=int, help="override source.scan_limit")
    p.add_argument("--dry-run", action="store_true", help="download but don't upload to GitHub")
    p.set_defaults(func=cmd_sync)
    p = subs.add_parser("feed", help="write feed.xml")
    p.add_argument("--out", default=str(HERE / "public"))
    p.set_defaults(func=cmd_feed)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
