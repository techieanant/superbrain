#!/usr/bin/env python3
"""
YouTube Video Analyzer for SuperBrain
=======================================
Uses Gemini's native YouTube URL understanding — no download, no audio
transcription, no frame extraction. One API call → full structured analysis.

The google.genai SDK passes the YouTube URL directly to Gemini which can
watch, listen, and read the video natively at Google's data centre.
"""

import os
import re
import httpx
from pathlib import Path
from urllib.parse import urlparse, parse_qs

API_KEYS_FILE = Path(__file__).resolve().parent.parent / "config" / ".api_keys"

# ── Prompt ────────────────────────────────────────────────────────────────────

YOUTUBE_PROMPT = """Watch this YouTube video carefully and write a structured analysis report.

Generate the report in this EXACT format (use these exact emoji headers):

📌 TITLE:
[The actual video title, or a clear descriptive title if you can't read it]

� CHANNEL:
[The YouTube channel name or creator/uploader name]

📅 DATE:
[Upload date in YYYY-MM-DD format if visible or known. Otherwise write "Unknown"]

�📝 SUMMARY:
[Comprehensive 3-5 sentence summary covering: main topic/theme, key points and
information shared, any products/places/tools/tips mentioned, who this content
is for, and the overall value or takeaway]

🏷️ TAGS:
[Generate 8-12 relevant hashtags/keywords separated by spaces, e.g. #dji #drone #aerial]

🎵 MUSIC:
[Name specific background music or songs heard in the video. If there is no
identifiable background music, write "No background music". If it's voiceover
only, write "Voiceover only".]

📂 CATEGORY:
[Choose exactly ONE from: product, places, recipe, software, book, tv shows, workout, film, event, other]

Be specific, accurate, and extractive — pull out real names, numbers, and facts from the video."""


# ── Thumbnail helper ─────────────────────────────────────────────────────

def _extract_video_id(youtube_url: str) -> str:
    """Extract the 11-char video ID from any YouTube URL format."""
    parsed = urlparse(youtube_url)
    qs = parse_qs(parsed.query)
    if "youtu.be" in parsed.netloc:
        return parsed.path.lstrip("/").split("/")[0]
    if "v" in qs:
        return qs["v"][0]
    m = re.match(r"^/(?:shorts|embed|v|live|e)/([A-Za-z0-9_-]{11})", parsed.path)
    return m.group(1) if m else ""


def _parse_yt_field(raw: str, label: str) -> str:
    """Extract a single-line field value from YouTube Gemini output.
    Handles emoji/no-emoji and markdown bold variants.
    """
    pattern = re.compile(
        rf'(?:^|\n)\s*\S*\s*\*{{0,2}}{re.escape(label)}\*{{0,2}}:?\s*([^\n]+)',
        re.IGNORECASE,
    )
    m = pattern.search(raw)
    return m.group(1).strip().strip("*").strip() if m else ""


def get_youtube_channel_name(url: str, ai_raw: str = "") -> str:
    """
    Multi-stage robust YouTube channel name extractor.

    Stages (tried in order, returns first non-empty result):
      1. oEmbed API   — fast, no auth, reliable for public videos
      2. HTML scrape  — parses itemprop/JSON-LD metadata from the watch page
      3. yt-dlp       — subprocess call (if yt-dlp is installed)
      4. AI output    — value parsed from Gemini's CHANNEL field in *ai_raw*
    """
    import requests, subprocess, json as _json, shutil

    # ── Stage 1: oEmbed (fastest, no auth) ───────────────────────────────
    try:
        r = requests.get(
            "https://www.youtube.com/oembed",
            params={"url": url, "format": "json"},
            timeout=8,
        )
        if r.ok:
            name = r.json().get("author_name", "").strip()
            if name:
                return name
    except Exception:
        pass

    # ── Stage 2: HTML meta scrape ─────────────────────────────────────────
    try:
        r = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=10,
        )
        text = r.text
        # JSON-LD: "author":{"@type":"Person","name":"Channel Name"}
        m = re.search(r'"author"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"', text)
        if m:
            return m.group(1).strip()
        # itemprop channel
        m = re.search(r'itemprop="author"[^>]*>\s*<[^>]*itemprop="name"[^>]*content="([^"]+)"', text)
        if m:
            return m.group(1).strip()
        # ytInitialData ownerText
        m = re.search(r'"ownerText"\s*:\s*\{"runs"\s*:\s*\[\{"text"\s*:\s*"([^"]+)"', text)
        if m:
            return m.group(1).strip()
    except Exception:
        pass

    # ── Stage 3: yt-dlp subprocess ────────────────────────────────────────
    if shutil.which("yt-dlp"):
        try:
            result = subprocess.run(
                ["yt-dlp", "--print", "channel", "--no-download", "--quiet", url],
                capture_output=True, text=True, timeout=20,
            )
            name = result.stdout.strip()
            if name:
                return name
        except Exception:
            pass

    # ── Stage 4: AI-parsed fallback ───────────────────────────────────────
    if ai_raw:
        name = _parse_yt_field(ai_raw, "CHANNEL")
        if name:
            return name

    return ""


def get_youtube_upload_date(youtube_url: str) -> str | None:
    """
    Scrape the actual upload date from YouTube's page HTML.
    Returns 'YYYY-MM-DD' string or None on failure.
    """
    try:
        import requests
        r = requests.get(
            youtube_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=10,
        )
        for pattern in (
            r'"uploadDate":"(\d{4}-\d{2}-\d{2})',
            r'"publishDate":"(\d{4}-\d{2}-\d{2})',
            r'<meta itemprop="datePublished" content="(\d{4}-\d{2}-\d{2})',
        ):
            m = re.search(pattern, r.text)
            if m:
                return m.group(1)
    except Exception:
        pass
    return None


def get_youtube_thumbnail(youtube_url: str) -> str:
    """
    Return the best available thumbnail URL for a YouTube video.
    Tries maxresdefault (1280x720) first, falls back to hqdefault (480x360).
    The returned string is always a direct HTTPS URL — no download needed.
    """
    video_id = _extract_video_id(youtube_url)
    if not video_id:
        return ""

    # Verify maxresdefault exists (some older videos only have hqdefault)
    maxres = f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"
    hq     = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
    try:
        import requests
        r = requests.head(maxres, timeout=5)
        # YouTube returns a tiny 120x90 stub with 200 when maxres is unavailable
        # Real maxres images are much larger; check content-length as a signal
        cl = int(r.headers.get("content-length", 0))
        return maxres if (r.status_code == 200 and cl > 5000) else hq
    except Exception:
        return hq


# ── Key loader ─────────────────────────────────────────────────────────────────

def _load_ai_config() -> dict:
    """Load AI configuration from the new config format."""
    config = {
        "ai_provider_type": "api_key",
        "api_provider": "gemini",
        "api_key": "",
        "ollama_url": "http://localhost:11434",
        "ollama_model": None,
        "custom_base_url": "",
        "custom_api_key": "",
        "custom_model": ""
    }
    if API_KEYS_FILE.exists():
        for line in API_KEYS_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                key_name = k.strip().upper()
                if key_name == "AI_PROVIDER_TYPE":
                    config["ai_provider_type"] = v.strip()
                elif key_name == "API_PROVIDER":
                    config["api_provider"] = v.strip()
                elif key_name == "API_KEY":
                    config["api_key"] = v.strip()
                elif key_name == "OLLAMA_URL":
                    config["ollama_url"] = v.strip()
                elif key_name == "OLLAMA_MODEL":
                    config["ollama_model"] = v.strip() if v.strip() else None
                elif key_name == "CUSTOM_BASE_URL":
                    config["custom_base_url"] = v.strip()
                elif key_name == "CUSTOM_API_KEY":
                    config["custom_api_key"] = v.strip()
                elif key_name == "CUSTOM_MODEL":
                    config["custom_model"] = v.strip()
    return config


# ── Model fallback chain (supports YouTube video natively) ──────────────────────

# Tried left-to-right; on 429 we parse the retry-after delay and honour it once.
# Only Gemini 2.x+ models support YouTube URL as a native video part via v1beta.
_GEMINI_MODELS = [
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
]


def _parse_retry_after(err_str: str) -> float:
    """Extract retry delay seconds from a Gemini 429 error string."""
    m = re.search(r"retryDelay['\"]?\s*:\s*['\"]?(\d+(?:\.\d+)?)", err_str)
    if m:
        return float(m.group(1))
    m = re.search(r"retry in (\d+(?:\.\d+)?)s", err_str)
    if m:
        return float(m.group(1))
    return 0.0


# ── Core analysis ──────────────────────────────────────────────────────────────

def analyze_youtube(youtube_url: str) -> dict:
    """
    Analyze a YouTube video using the configured AI provider.
    Supports API key providers (gemini, groq, openrouter), Ollama, and custom providers.

    Returns:
        dict with keys: raw_output (str), channel (str), thumbnail (str), error (str|None)
    """
    import time

    ai_config = _load_ai_config()
    provider_type = ai_config.get("ai_provider_type", "api_key")
    
    thumbnail = get_youtube_thumbnail(youtube_url)
    post_date = get_youtube_upload_date(youtube_url)
    
    if provider_type == "ollama":
        return _analyze_youtube_ollama(youtube_url, ai_config, thumbnail, post_date)
    elif provider_type == "custom":
        return _analyze_youtube_custom(youtube_url, ai_config, thumbnail, post_date)
    else:
        return _analyze_youtube_api(youtube_url, ai_config, thumbnail, post_date)


def _analyze_youtube_api(youtube_url: str, ai_config: dict, thumbnail: str, post_date: str) -> dict:
    """Analyze YouTube using API key provider (gemini, groq, openrouter)."""
    provider = ai_config.get("api_provider", "gemini")
    api_key = ai_config.get("api_key", "")
    
    if not api_key:
        return {"raw_output": "", "channel": "", "thumbnail": thumbnail,
                "post_date": post_date, "error": f"No API key configured. Set up your {provider} API key in settings."}
    
    if provider == "gemini":
        return _analyze_youtube_gemini(youtube_url, api_key, thumbnail, post_date)
    elif provider == "groq":
        return _analyze_youtube_groq(youtube_url, api_key, thumbnail, post_date)
    elif provider == "openrouter":
        return _analyze_youtube_openrouter(youtube_url, api_key, thumbnail, post_date)
    else:
        return {"raw_output": "", "channel": "", "thumbnail": thumbnail,
                "post_date": post_date, "error": f"Unknown provider: {provider}"}


def _analyze_youtube_gemini(youtube_url: str, api_key: str, thumbnail: str, post_date: str) -> dict:
    """Analyze YouTube using Gemini API."""
    import time
    
    try:
        from google import genai
        from google.genai import types as gtypes
    except ImportError:
        return {"raw_output": "", "channel": "", "thumbnail": thumbnail,
                "post_date": post_date, "error": "google-genai not installed. Run: pip install google-genai"}

    client = genai.Client(api_key=api_key)
    last_error = ""

    for model in _GEMINI_MODELS:
        print(f"  🎬 Trying {model} for YouTube analysis...")
        try:
            response = client.models.generate_content(
                model=model,
                contents=[
                    gtypes.Part.from_uri(
                        file_uri=youtube_url,
                        mime_type="video/youtube",
                    ),
                    YOUTUBE_PROMPT,
                ],
                config=gtypes.GenerateContentConfig(
                    max_output_tokens=1500,
                    temperature=0.7,
                ),
            )
            raw = response.text.strip()
            channel = get_youtube_channel_name(youtube_url, ai_raw=raw)
            info = f" | channel: {channel}" if channel else ""
            dp = f" | date: {post_date}" if post_date else ""
            print(f"  ✓ Gemini YouTube analysis complete (model: {model}){info}{dp}")
            return {"raw_output": raw, "channel": channel, "thumbnail": thumbnail,
                    "post_date": post_date, "error": None}

        except Exception as e:
            err = str(e)
            last_error = err
            if "429" in err or "RESOURCE_EXHAUSTED" in err:
                wait = min(_parse_retry_after(err), 65.0)
                if wait > 0:
                    print(f"  ⏳ {model} rate-limited — waiting {wait:.0f}s before next model...")
                    time.sleep(wait)
                else:
                    print(f"  ⚠️  {model} quota exhausted — trying next model")
            else:
                print(f"  ✗ {model} failed: {err[:120]}")

    print(f"  ✗ All Gemini models exhausted for YouTube analysis")
    return {"raw_output": "", "channel": "", "thumbnail": thumbnail, "post_date": post_date, "error": last_error}


def _analyze_youtube_groq(youtube_url: str, api_key: str, thumbnail: str, post_date: str) -> dict:
    """Analyze YouTube using Groq API - fetches transcript and analyzes with LLM."""
    try:
        from groq import Groq
    except ImportError:
        return {"raw_output": "", "channel": "", "thumbnail": thumbnail,
                "post_date": post_date, "error": "groq not installed. Run: pip install groq"}
    
    client = Groq(api_key=api_key)
    
    transcript = _fetch_youtube_transcript(youtube_url)
    if not transcript:
        return {"raw_output": "", "channel": "", "thumbnail": thumbnail,
                "post_date": post_date, "error": "Could not fetch YouTube transcript"}
    
    prompt = f"""Analyze this YouTube video transcript and generate a structured report in this EXACT format (use these exact emoji headers):

📌 TITLE:
[The actual video title, or a clear descriptive title if you can't read it]

📢 CHANNEL:
[The YouTube channel name or creator/uploader name]

📅 DATE:
[Upload date in YYYY-MM-DD format if visible or known. Otherwise write "Unknown"]

📝 SUMMARY:
[Comprehensive 3-5 sentence summary covering: main topic/theme, key points and information shared, any products/places/tools/tips mentioned, who this content is for, and the overall value or takeaway]

🏷️ TAGS:
[Generate 8-12 relevant hashtags/keywords separated by spaces, e.g. #dji #drone #aerial]

🎵 MUSIC:
[Name specific background music or songs heard in the video. If there is no identifiable background music, write "No background music". If it's voiceover only, write "Voiceover only".]

📂 CATEGORY:
[Choose exactly ONE from: product, places, recipe, software, book, tv shows, workout, film, event, other]

Transcript:
{transcript[:8000]}

Be specific, accurate, and extractive — pull out real names, numbers, and facts from the video."""

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1500,
            temperature=0.7
        )
        raw = response.choices[0].message.content
        channel = get_youtube_channel_name(youtube_url, ai_raw=raw)
        print(f"  ✓ Groq YouTube analysis complete")
        return {"raw_output": raw, "channel": channel, "thumbnail": thumbnail,
                "post_date": post_date, "error": None}
    except Exception as e:
        return {"raw_output": "", "channel": "", "thumbnail": thumbnail,
                "post_date": post_date, "error": f"Groq API error: {str(e)}"}


def _analyze_youtube_openrouter(youtube_url: str, api_key: str, thumbnail: str, post_date: str) -> dict:
    """Analyze YouTube using OpenRouter API."""
    transcript = _fetch_youtube_transcript(youtube_url)
    if not transcript:
        return {"raw_output": "", "channel": "", "thumbnail": thumbnail,
                "post_date": post_date, "error": "Could not fetch YouTube transcript"}
    
    prompt = f"""Analyze this YouTube video transcript and generate a structured report in this EXACT format (use these exact emoji headers):

📌 TITLE:
[The actual video title, or a clear descriptive title if you can't read it]

📢 CHANNEL:
[The YouTube channel name or creator/uploader name]

📅 DATE:
[Upload date in YYYY-MM-DD format if visible or known. Otherwise write "Unknown"]

📝 SUMMARY:
[Comprehensive 3-5 sentence summary covering: main topic/theme, key points and information shared, any products/places/tools/tips mentioned, who this content is for, and the overall value or takeaway]

🏷️ TAGS:
[Generate 8-12 relevant hashtags/keywords separated by spaces, e.g. #dji #drone #aerial]

🎵 MUSIC:
[Name specific background music or songs heard in the video. If there is no identifiable background music, write "No background music". If it's voiceover only, write "Voiceover only".]

📂 CATEGORY:
[Choose exactly ONE from: product, places, recipe, software, book, tv shows, workout, film, event, other]

Transcript:
{transcript[:8000]}

Be specific, accurate, and extractive — pull out real names, numbers, and facts from the video."""

    try:
        response = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://superbrain.local",
                "X-Title": "SuperBrain"
            },
            json={
                "model": "google/gemini-2.0-flash-001",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 1500
            },
            timeout=60.0
        )
        response.raise_for_status()
        data = response.json()
        raw = data["choices"][0]["message"]["content"]
        channel = get_youtube_channel_name(youtube_url, ai_raw=raw)
        print(f"  ✓ OpenRouter YouTube analysis complete")
        return {"raw_output": raw, "channel": channel, "thumbnail": thumbnail,
                "post_date": post_date, "error": None}
    except Exception as e:
        return {"raw_output": "", "channel": "", "thumbnail": thumbnail,
                "post_date": post_date, "error": f"OpenRouter API error: {str(e)}"}


def _analyze_youtube_ollama(youtube_url: str, ai_config: dict, thumbnail: str, post_date: str) -> dict:
    """Analyze YouTube using Ollama."""
    ollama_url = ai_config.get("ollama_url", "http://localhost:11434")
    model = ai_config.get("ollama_model", "llama3.2:3b")
    
    transcript = _fetch_youtube_transcript(youtube_url)
    if not transcript:
        return {"raw_output": "", "channel": "", "thumbnail": thumbnail,
                "post_date": post_date, "error": "Could not fetch YouTube transcript"}
    
    prompt = f"""Analyze this YouTube video transcript and generate a structured report in this EXACT format (use these exact emoji headers):

📌 TITLE:
[The actual video title, or a clear descriptive title if you can't read it]

📢 CHANNEL:
[The YouTube channel name or creator/uploader name]

📅 DATE:
[Upload date in YYYY-MM-DD format if visible or known. Otherwise write "Unknown"]

📝 SUMMARY:
[Comprehensive 3-5 sentence summary covering: main topic/theme, key points and information shared, any products/places/tools/tips mentioned, who this content is for, and the overall value or takeaway]

🏷️ TAGS:
[Generate 8-12 relevant hashtags/keywords separated by spaces, e.g. #dji #drone #aerial]

🎵 MUSIC:
[Name specific background music or songs heard in the video. If there is no identifiable background music, write "No background music". If it's voiceover only, write "Voiceover only".]

📂 CATEGORY:
[Choose exactly ONE from: product, places, recipe, software, book, tv shows, workout, film, event, other]

Transcript:
{transcript[:8000]}

Be specific, accurate, and extractive — pull out real names, numbers, and facts from the video."""
    
    try:
        response = httpx.post(
            f"{ollama_url}/api/chat",
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False
            },
            timeout=120.0
        )
        response.raise_for_status()
        data = response.json()
        raw = data.get("message", {}).get("content", "")
        channel = get_youtube_channel_name(youtube_url, ai_raw=raw)
        print(f"  ✓ Ollama YouTube analysis complete")
        return {"raw_output": raw, "channel": channel, "thumbnail": thumbnail,
                "post_date": post_date, "error": None}
    except Exception as e:
        return {"raw_output": "", "channel": "", "thumbnail": thumbnail,
                "post_date": post_date, "error": f"Ollama error: {str(e)}. Make sure Ollama is running at {ollama_url}"}


def _analyze_youtube_custom(youtube_url: str, ai_config: dict, thumbnail: str, post_date: str) -> dict:
    """Analyze YouTube using custom OpenAI-compatible API."""
    base_url = ai_config.get("custom_base_url", "")
    api_key = ai_config.get("custom_api_key", "")
    model = ai_config.get("custom_model", "")
    
    if not base_url or not model:
        return {"raw_output": "", "channel": "", "thumbnail": thumbnail,
                "post_date": post_date, "error": "Custom provider not configured. Set Base URL and Model in settings."}
    
    transcript = _fetch_youtube_transcript(youtube_url)
    if not transcript:
        return {"raw_output": "", "channel": "", "thumbnail": thumbnail,
                "post_date": post_date, "error": "Could not fetch YouTube transcript"}
    
    prompt = f"""Analyze this YouTube video transcript and generate a structured report in this EXACT format (use these exact emoji headers):

📌 TITLE:
[The actual video title, or a clear descriptive title if you can't read it]

📢 CHANNEL:
[The YouTube channel name or creator/uploader name]

📅 DATE:
[Upload date in YYYY-MM-DD format if visible or known. Otherwise write "Unknown"]

📝 SUMMARY:
[Comprehensive 3-5 sentence summary covering: main topic/theme, key points and information shared, any products/places/tools/tips mentioned, who this content is for, and the overall value or takeaway]

🏷️ TAGS:
[Generate 8-12 relevant hashtags/keywords separated by spaces, e.g. #dji #drone #aerial]

🎵 MUSIC:
[Name specific background music or songs heard in the video. If there is no identifiable background music, write "No background music". If it's voiceover only, write "Voiceover only".]

📂 CATEGORY:
[Choose exactly ONE from: product, places, recipe, software, book, tv shows, workout, film, event, other]

Transcript:
{transcript[:8000]}

Be specific, accurate, and extractive — pull out real names, numbers, and facts from the video."""

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    
    try:
        response = httpx.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers=headers,
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 1500
            },
            timeout=120.0
        )
        response.raise_for_status()
        data = response.json()
        raw = data["choices"][0]["message"]["content"]
        channel = get_youtube_channel_name(youtube_url, ai_raw=raw)
        print(f"  ✓ Custom provider YouTube analysis complete")
        return {"raw_output": raw, "channel": channel, "thumbnail": thumbnail,
                "post_date": post_date, "error": None}
    except Exception as e:
        return {"raw_output": "", "channel": "", "thumbnail": thumbnail,
                "post_date": post_date, "error": f"Custom provider error: {str(e)}"}


def _fetch_youtube_transcript(youtube_url: str) -> str:
    """Fetch YouTube video transcript or fallback to title/description."""
    transcript = ""
    try:
        import yt_dlp
        import requests
        ydl_opts = {
            'writesubtitles': True,
            'writeautomaticsub': True,
            'subtitleslangs': ['en'],
            'skip_download': True,
            'quiet': True,
            'no_warnings': True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(youtube_url, download=False)
                subtitles = info.get('subtitles') or info.get('automatic_captions')
                if subtitles:
                    for lang in ['en', 'en-US', 'en-GB']:
                        if lang in subtitles:
                            sub = subtitles[lang][0]
                            if 'data' in sub:
                                transcript = sub['data']
                                break
                            elif 'url' in sub:
                                url = sub['url']
                                resp = requests.get(url, timeout=30)
                                if resp.status_code == 200:
                                    transcript = resp.text
                                    break
            except Exception:
                pass
    except Exception:
        pass

    if transcript:
        return f"[TRANSCRIPT]\n{transcript}"

    # Fallback to scraping title and description if transcript fetch fails (e.g. DRM or no subs)
    try:
        import requests
        from bs4 import BeautifulSoup
        resp = requests.get(
            youtube_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=10
        )
        if resp.ok:
            soup = BeautifulSoup(resp.text, "html.parser")
            title = soup.find("meta", property="og:title")
            title_text = title["content"] if title else soup.title.string if soup.title else ""
            
            desc = soup.find("meta", property="og:description")
            desc_text = desc["content"] if desc else ""
            
            if title_text:
                return f"[VIDEO INFO FROM PAGE METADATA]\nTitle: {title_text}\n\nDescription:\n{desc_text}"
    except Exception:
        pass

    return ""


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else input("YouTube URL: ").strip()
    if url:
        result = analyze_youtube(url)
        if result["error"]:
            print(f"\n✗ Error: {result['error']}")
        else:
            print("\n" + "=" * 60)
            print(result["raw_output"])
