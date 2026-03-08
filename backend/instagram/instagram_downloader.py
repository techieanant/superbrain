"""
Instagram post downloader — instaloader engine.

Uses an authenticated instaloader session if one exists
(created by instagram_login.py), falls back to anonymous otherwise.

Session file : backend/.instaloader_session  (gitignored)
"""

import os
import re
import sys
import pathlib
import contextlib

# Ensure backend root is in sys.path (needed when run as a subprocess)
import sys as _sys_
_sys_.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
del _sys_
from urllib.request import urlretrieve as _urlretrieve_il


class RetryableDownloadError(Exception):
    """Raised when Instagram download fails due to a transient issue
    (rate limit / login required) that should be retried later."""
    pass

# ── instaloader ───────────────────────────────────────────────────────────────
try:
    import instaloader
    INSTALOADER_AVAILABLE = True
except ImportError:
    INSTALOADER_AVAILABLE = False

# ── Audio extraction ──────────────────────────────────────────────────────────
try:
    from moviepy.editor import VideoFileClip
    MOVIEPY_AVAILABLE = True
except ImportError:
    MOVIEPY_AVAILABLE = False

# ── Paths ─────────────────────────────────────────────────────────────────────
BACKEND_DIR     = pathlib.Path(__file__).parent.parent
TEMP_DIR        = BACKEND_DIR / "temp"
CONFIG_DIR      = BACKEND_DIR / "config"
IL_SESSION_FILE = CONFIG_DIR / ".instaloader_session"
API_KEYS_FILE   = CONFIG_DIR / ".api_keys"


# ── Credential loader ─────────────────────────────────────────────────────────
def _load_credentials() -> tuple[str, str]:
    """Read INSTAGRAM_USERNAME / INSTAGRAM_PASSWORD from .api_keys or env."""
    creds: dict[str, str] = {}
    if API_KEYS_FILE.exists():
        for line in API_KEYS_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                creds[k.strip()] = v.strip()

    username = creds.get("INSTAGRAM_USERNAME") or os.getenv("INSTAGRAM_USERNAME", "")
    password = creds.get("INSTAGRAM_PASSWORD") or os.getenv("INSTAGRAM_PASSWORD", "")
    return username, password


# ── Helpers ───────────────────────────────────────────────────────────────────
def sanitize_folder_name(text: str, max_length: int = 50) -> str:
    """Strip non-ASCII and filesystem-unsafe characters; trim to max_length."""
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r'[<>:"/\\|?*\n\r]', "", text)
    text = re.sub(r"\s+", " ", text).strip()[:max_length]
    return text or "instagram_post"


def _unique_folder(base: pathlib.Path, name: str) -> pathlib.Path:
    """Return a non-existing path under base, appending _N suffix if needed."""
    folder = base / name
    counter = 1
    while folder.exists():
        folder = base / f"{name}_{counter}"
        counter += 1
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def extract_audio_from_video(video_path: str, audio_path: str) -> bool:
    """Extract audio track from a video file and save as MP3."""
    if not MOVIEPY_AVAILABLE:
        print("  ⚠ moviepy not installed — skipping audio extraction")
        return False
    try:
        print("  Extracting audio...")
        video = VideoFileClip(video_path)
        if video.audio is not None:
            video.audio.write_audiofile(audio_path, verbose=False, logger=None)
            video.close()
            print(f"  ✓ Audio saved: {os.path.basename(audio_path)}")
            return True
        video.close()
        print("  ⚠ No audio track in video")
        return False
    except Exception as e:
        print(f"  ⚠ Audio extraction failed: {e}")
        return False


# ── Main download function ────────────────────────────────────────────────────
def download_instagram_content(url: str) -> str | None:
    """
    Download an Instagram post (photo / video / carousel) to temp/<folder>/.

    Uses authenticated instaloader session if available, anonymous otherwise.
    Returns the folder path string on success, or None on failure.
    """
    TEMP_DIR.mkdir(exist_ok=True)

    # Note: Link validator sets Facebook reels to "instagram" pipeline
    if "facebook.com" in url or "fb.watch" in url:
        return _download_via_ytdlp(url)

    if INSTALOADER_AVAILABLE:
        try:
            return _download_via_instaloader(url)
        except Exception as e:
            print(f"  ⚠ instaloader failed ({str(e)}). Falling back to yt-dlp...")
            # Fall back to yt-dlp on non-instaloader recoverable errors
            # (or if instaloader throws a non-RetryableDownloadError generic exception)

    # Ultimate fallback for Instagram if instaloader is missing or failed
    import shutil
    if shutil.which("yt-dlp"):
        return _download_via_ytdlp(url)

    print("✗ No viable download method available.")
    return None

def _download_via_ytdlp(url: str) -> str | None:
    """Download generic social video using yt-dlp directly."""
    import subprocess
    import shutil
    import hashlib

    print(f"  Fetching using yt-dlp: {url}")
    if not shutil.which("yt-dlp"):
        print("  ✗ yt-dlp is not installed!")
        return None

    # Create folder name from URL hash
    clean_url = url.split("?")[0]
    folder_name = hashlib.md5(clean_url.encode()).hexdigest()[:12]
    folder = _unique_folder(TEMP_DIR, folder_name)

    # yt-dlp options: download video + thumbnail + metadata as JSON
    video_path_tmpl = str(folder / f"{folder_name}.%(ext)s")
    
    cmd = [
        "yt-dlp",
        "--format", "best[ext=mp4]/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best",
        "--merge-output-format", "mp4",
        "--write-info-json",
        "--write-thumbnail",
        "--no-warnings",
        "-o", video_path_tmpl,
        url
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            print(f"  ✗ yt-dlp error: {proc.stderr}")
            return None

        # Post process downloaded files
        # Check if mp4 exists
        mp4_files = list(folder.glob("*.mp4"))
        if not mp4_files:
            # yt-dlp might have saved it as mkv or webm despite requesting mp4
            mkv_files = list(folder.glob("*.mkv")) + list(folder.glob("*.webm"))
            if not mkv_files:
                print("  ✗ yt-dlp failed to produce any video file.")
                return None
            video_file = mkv_files[0]
            # Rename or use as is
            dest = folder / f"{folder_name}.mp4"
            shutil.move(str(video_file), str(dest))
            video_file = dest
        else:
            video_file = mp4_files[0]

        # Extract audio for whisper
        extract_audio_from_video(
            str(video_file),
            str(folder / f"{folder_name}_audio.mp3")
        )

        # Convert yt-dlp info.json to info.txt used by main.py
        import json
        info_files = list(folder.glob("*.info.json"))
        if info_files:
            try:
                with open(info_files[0], "r", encoding="utf-8") as f:
                    meta = json.load(f)
                
                info_txt = folder / "info.txt"
                with open(info_txt, "w", encoding="utf-8") as f:
                    # Write in the format main.py expects:
                    # Username: @xx
                    # Likes: 123
                    # Date: 2024-01-01
                    username = meta.get("uploader") or meta.get("channel") or "Unknown"
                    likes = meta.get("like_count", 0)
                    date_str = meta.get("upload_date", "")
                    if date_str and len(date_str) == 8:
                        date_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
                    
                    f.write(f"Username: @{username}\n")
                    f.write(f"Likes: {likes}\n")
                    f.write(f"Date: {date_str}\n")
                    
                    # Also write title and description for text analyzer
                    f.write(f"Title: {meta.get('title', '')}\n")
                    desc = meta.get('description', '')
                    if desc:
                        f.write(f"\nCaption:\n{desc}\n")
            except Exception as e:
                print(f"  ⚠ Failed to parse yt-dlp json: {e}")

        return str(folder)
    except subprocess.TimeoutExpired:
        print("  ✗ yt-dlp timed out.")
        return None
    except Exception as e:
        print(f"  ✗ yt-dlp exception: {e}")
        return None

# ── instaloader engine ───────────────────────────────────────────────────────
def _download_via_instaloader(url: str) -> str | None:
    """Download using instaloader — authenticated session if available, else anonymous."""
    match = re.search(r"/(?:reels?|p|tv)/([^/?#&]+)", url)
    if not match:
        print("  ✗ Invalid Instagram URL.")
        return None
    shortcode = match.group(1)

    L = instaloader.Instaloader(
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        compress_json=False,
        iphone_support=False,          # disable mobile API — use web only, no 403s
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        max_connection_attempts=3,
    )

    # Load saved instaloader session if one exists (set up by instagram_login.py)
    username, _ = _load_credentials()
    if IL_SESSION_FILE.exists() and username:
        try:
            L.load_session_from_file(username, str(IL_SESSION_FILE))
            print(f"  ✓ Using authenticated instaloader session (@{username})")
        except Exception as e:
            print(f"  ⚠ Could not load instaloader session ({e}) — using anonymous")

    try:
        print(f"  Fetching post for shortcode: {shortcode}...")
        with contextlib.redirect_stderr(open(os.devnull, "w")):
            post = instaloader.Post.from_shortcode(L.context, shortcode)

        caption           = post.caption if post.caption else f"post_{shortcode}"
        caption_first_line = caption.split("\n")[0]
        folder_name       = sanitize_folder_name(caption_first_line)
        folder            = _unique_folder(TEMP_DIR, folder_name)

        print(f"  User   : @{post.owner_username}")
        print(f"  Caption: {caption_first_line[:80]}...")

        file_counter = 1
        if post.is_video:
            video_path = str(folder / f"{folder_name}.mp4")
            print("  Downloading video...")
            _urlretrieve_il(post.video_url, video_path)
            extract_audio_from_video(video_path,
                                     str(folder / f"{folder_name}_audio.mp3"))
            _urlretrieve_il(post.url, str(folder / f"{folder_name}_thumbnail.jpg"))

        elif post.typename == "GraphSidecar":
            print(f"  Downloading carousel ({post.mediacount} items)...")
            for node in post.get_sidecar_nodes():
                if node.is_video:
                    fp = str(folder / f"{folder_name}_{file_counter}.mp4")
                    _urlretrieve_il(node.video_url, fp)
                    extract_audio_from_video(
                        fp,
                        str(folder / f"{folder_name}_{file_counter}_audio.mp3")
                    )
                else:
                    _urlretrieve_il(
                        node.display_url,
                        str(folder / f"{folder_name}_{file_counter}.jpg")
                    )
                print(f"  → item {file_counter}/{post.mediacount}")
                file_counter += 1

        else:
            print("  Downloading image...")
            _urlretrieve_il(post.url, str(folder / f"{folder_name}.jpg"))

        # info.txt (instaloader variant — uses post object fields)
        with open(folder / "info.txt", "w", encoding="utf-8") as f:
            f.write("Instagram Post Information\n")
            f.write("=" * 50 + "\n\n")
            f.write(f"URL: {url}\n")
            f.write(f"Username: @{post.owner_username}\n")
            f.write(f"Date: {post.date_utc}\n")
            f.write(f"Likes: {post.likes}\n")
            f.write(f"Type: {'Video' if post.is_video else 'Image'}\n")
            if post.typename == "GraphSidecar":
                f.write(f"Media Count: {post.mediacount} items\n")
            f.write("\n")
            f.write(f"Caption:\n{'-' * 50}\n")
            f.write(post.caption if post.caption else "No caption")
            f.write(f"\n{'-' * 50}\n\n")
            hashtags = re.findall(r"#\w+", post.caption or "")
            if hashtags:
                f.write("Hashtags:\n")
                f.write(", ".join(hashtags) + "\n")

        print(f"\n✓ Download complete (instaloader): {folder}")
        return str(folder)

    except instaloader.exceptions.LoginRequiredException:
        raise RetryableDownloadError(
            "Login required — Instagram blocked anonymous access. Will retry later."
        )
    except instaloader.exceptions.ConnectionException as e:
        _msg = str(e).lower()
        if any(k in _msg for k in ("too many", "rate", "wait", "429", "blocked", "checkpoint")):
            raise RetryableDownloadError(f"Instagram rate-limited: {e}")
        print(f"  ✗ instaloader: Connection error — {e}")
    except Exception as e:
        print(f"  ✗ instaloader error: {e}")
        import traceback; traceback.print_exc()
    return None


# ── CLI entry-point ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) > 1:
        _url = sys.argv[1]
    else:
        _url = input("Enter Instagram Reel / Post / IGTV link: ").strip()

    if _url:
        download_instagram_content(_url)
    else:
        print("No URL provided.")
