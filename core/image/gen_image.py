#!/usr/bin/env python3
"""Generate one image for the styles marked ✦ (needs: image-generation).

Supports three backends via `--provider` (or `LEMO_IMAGE_PROVIDER`, default `auto`):
- `codex`: the user's Codex CLI (`codex exec` + built-in `image_gen`), checked and preferred first in `auto` mode.
- `openai`: OpenAI Images API (`/v1/images/generations` and `/v1/images/edits`) using `OPENAI_API_KEY` (stdlib `urllib`).
- `gemini`: Google Gemini image generation (`generateContent`) using `GEMINI_API_KEY` or `GOOGLE_API_KEY` (stdlib `urllib`).

A user with another image tool (another API, a web app, their own drawings) makes the images there from the same
prompts and saves them under the names the style's scripts expect; see core/README.md, "Image generation".

  gen_image.py --check [--provider auto|codex|openai|gemini]
      Is an image generator ready? Exit 0 if so, 4 if not. A film that needs images starts only after this passes
      or the user has said which other tool they will use. The first real image confirms that the account can
      generate images.
  gen_image.py "<prompt>" [--provider auto|codex|openai|gemini] [--aspect 16:9] [--ref <image> ...] [--edit]
               [--out <dir>] [--name <base>] [--reasoning low] [--retries 1] [--timeout 480]
      Saves <out>/<name>_<date-time>.png and prints its path.

What it does:
- in `auto` mode (default), picks signed-in `codex` first, else `OPENAI_API_KEY`, else `GEMINI_API_KEY` /
  `GOOGLE_API_KEY`, or prints setup instructions and exits 4;
- for `codex`, sends the prompt to `codex exec` on stdin (read-only sandbox), with every --ref image attached (-i),
  and takes the image from Codex's own records under $CODEX_HOME;
- for `openai` / `gemini`, calls the provider's REST image endpoint over HTTPS using Python's standard library,
  attaching any --ref images and mapping --aspect to the provider's size or aspectRatio parameter;
- refuses a result that is a byte-for-byte copy of a reference, or smaller than 8 KB;
- with --aspect, puts the width:height at the top of the prompt and checks the result (5 % tolerance); a wrong ratio is
  retried, and if it is still wrong the image is kept as <name>_<date-time>_wrongaspect.png.

References:
- --ref adds a reference image (repeatable): a character or product sheet the new drawing must match. Composition is
  described in the prompt, never taken from a reference.
- --edit changes an existing image: the first --ref is the image to edit, the others are references.

Exit codes: 0 ok · 2 no image · 3 wrong aspect after the retries · 4 setup problem (no provider ready, bad argument).
"""
import argparse
import base64
import glob
import hashlib
import json
import math
import mimetypes
import os
import re
import signal
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from shutil import which

PROVIDERS = ("auto", "codex", "openai", "gemini")
DEFAULT_OPENAI_MODEL = "gpt-image-2.5-flare"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-image"
DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

ONCE = ("Call image_gen exactly once. When it succeeds, reply DONE. When it fails, print the tool's error verbatim "
        "and stop. Do not run shell commands, do not look for or save files, do not print the image, do not retry.\n")
PREAMBLE = ("Use your built-in image generation tool (image_gen) to create one image.\n" + ONCE +
            "Any image attached with -i is a reference: match what the prompt says it is for (a character, a product, a "
            "drawing style); never copy its composition.\n\n")
EDIT_PREAMBLE = ("Use your built-in image generation tool (image_gen) to create one image.\n" + ONCE +
                 "The first attached image is the image to edit: change it as described below and keep everything "
                 "not mentioned exactly as it is. Any further attached images are references for the change (a "
                 "character or product sheet); never copy their layout.\n\n")
API_REF_PREAMBLE = ("Create one image.\n"
                    "Any attached image is a reference: match what the prompt says it is for (a character, a product, a "
                    "drawing style); never copy its composition.\n\n")
API_EDIT_PREAMBLE = ("Create one image.\n"
                     "The first attached image is the image to edit: change it as described below and keep everything "
                     "not mentioned exactly as it is. Any further attached images are references for the change (a "
                     "character or product sheet); never copy their layout.\n\n")
ASPECT_LINE = ("Required aspect ratio: {aspect} (width:height). Pick the landscape, portrait or square size that "
               "matches it, and fill the whole frame: no black or white borders, no frame, no letterboxing.\n\n")

MIN_BYTES = 8 * 1024
POLL_SEC = 2
ASPECT_TOL = 0.05
OTHER_TOOL = ("Or the user makes the images with another image tool they have (an image API or app, or their own "
              "drawings), from the same prompts and under the same file names (core/README.md, \"Image generation\"). "
              "Without any image generator, a film in a style marked ✦ cannot start.")
NO_CODEX = ("✗ codex not found: the Codex CLI is not installed here (or not on PATH). The styles marked ✦ draw with an "
            "image generator; to use Codex, the user installs the Codex CLI, signs in to an account that can generate "
            "images (`codex login`) and runs `gen_image.py --check` again. " + OTHER_TOOL)
NOT_SIGNED_IN = ("✗ {ver} is installed but not signed in. The user runs `codex login` (an account that can generate "
                 "images), then `gen_image.py --check` again. " + OTHER_TOOL)
NO_OPENAI = ("✗ OPENAI_API_KEY is not set. Set OPENAI_API_KEY in the environment, then run "
             "`gen_image.py --provider openai --check`. " + OTHER_TOOL)
NO_GEMINI = ("✗ GEMINI_API_KEY (or GOOGLE_API_KEY) is not set. Set GEMINI_API_KEY or GOOGLE_API_KEY in the "
             "environment, then run `gen_image.py --provider gemini --check`. " + OTHER_TOOL)

GEMINI_ASPECTS = (
    ("1:1", 1.0),
    ("4:3", 4.0 / 3.0),
    ("3:4", 3.0 / 4.0),
    ("3:2", 3.0 / 2.0),
    ("2:3", 2.0 / 3.0),
    ("16:9", 16.0 / 9.0),
    ("9:16", 9.0 / 16.0),
    ("21:9", 21.0 / 9.0),
    ("5:4", 5.0 / 4.0),
    ("4:5", 4.0 / 5.0),
    ("4:1", 4.0),
    ("1:4", 0.25),
    ("8:1", 8.0),
    ("1:8", 0.125),
)


class Parser(argparse.ArgumentParser):
    """A bad argument is a setup problem: exit 4, as documented (argparse's own 2 means "no image" here)."""

    def error(self, message):
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        sys.exit(4)


def codex_home():
    return os.environ.get("CODEX_HOME", os.path.expanduser("~/.codex"))


def codex_status():
    """Return (installed, signed_in, version) for the local Codex CLI."""
    if which("codex") is None:
        return False, False, ""
    ver = subprocess.run(["codex", "--version"], capture_output=True, text=True).stdout.strip()
    st = subprocess.run(["codex", "login", "status"], capture_output=True, text=True)
    return True, st.returncode == 0, ver or "codex"


def openai_api_key():
    return (os.environ.get("OPENAI_API_KEY") or "").strip()


def openai_model():
    return (os.environ.get("LEMO_OPENAI_IMAGE_MODEL") or DEFAULT_OPENAI_MODEL).strip() or DEFAULT_OPENAI_MODEL


def openai_base_url():
    return (os.environ.get("OPENAI_BASE_URL") or DEFAULT_OPENAI_BASE_URL).strip().rstrip("/")


def gemini_api_key():
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        val = (os.environ.get(name) or "").strip()
        if val:
            return val, name
    return "", ""


def gemini_model():
    model = (os.environ.get("LEMO_GEMINI_IMAGE_MODEL") or DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL
    return model.removeprefix("models/")


def gemini_base_url():
    return (os.environ.get("GEMINI_BASE_URL") or DEFAULT_GEMINI_BASE_URL).strip().rstrip("/")


def requested_provider(cli_provider=None):
    """Resolve requested provider mode from CLI flag, LEMO_IMAGE_PROVIDER, or default 'auto'."""
    raw = cli_provider if cli_provider is not None else os.environ.get("LEMO_IMAGE_PROVIDER")
    val = (raw or "").strip().lower() or "auto"
    if val not in PROVIDERS:
        raise ValueError(f"unknown provider {val!r}; expected {', '.join(PROVIDERS)}")
    return val


def format_no_provider(codex_installed, codex_ver):
    codex_hint = (
        f"{codex_ver} is installed but not signed in (`codex login`)"
        if codex_installed
        else "`codex` is not installed or not on PATH (`codex login`)"
    )
    return (
        f"✗ no image generator is configured ({codex_hint}; neither OPENAI_API_KEY nor "
        "GEMINI_API_KEY/GOOGLE_API_KEY is set). Set up any one of:\n"
        "  - Codex CLI: install Codex CLI and run `codex login`\n"
        "  - OpenAI Images API: set OPENAI_API_KEY (or pass `--provider openai`)\n"
        "  - Google Gemini API: set GEMINI_API_KEY or GOOGLE_API_KEY (or pass `--provider gemini`)\n"
        "Then run `python3 core/image/gen_image.py --check` again. " + OTHER_TOOL
    )


def resolve_backend(mode, for_check=False):
    """Pick the active backend ('codex'|'openai'|'gemini') or return (None, err_msg, meta)."""
    if mode == "codex":
        if for_check:
            installed, signed_in, ver = codex_status()
            if not installed:
                return None, NO_CODEX, {}
            if not signed_in:
                return None, NOT_SIGNED_IN.format(ver=ver), {}
            return "codex", "", {"ver": ver}
        if which("codex") is None:
            return None, NO_CODEX, {}
        return "codex", "", {}

    if mode == "openai":
        if not openai_api_key():
            return None, NO_OPENAI, {}
        return "openai", "", {"model": openai_model()}

    if mode == "gemini":
        key, env_name = gemini_api_key()
        if not key:
            return None, NO_GEMINI, {}
        return "gemini", "", {"model": gemini_model(), "env": env_name}

    installed, signed_in, ver = codex_status()
    if installed and signed_in:
        return "codex", "", {"ver": ver}
    if openai_api_key():
        return "openai", "", {"model": openai_model()}
    key, env_name = gemini_api_key()
    if key:
        return "gemini", "", {"model": gemini_model(), "env": env_name}
    return None, format_no_provider(installed, ver), {}


def check(mode="auto"):
    backend, err, meta = resolve_backend(mode, for_check=True)
    if backend is None:
        print(err, file=sys.stderr)
        return 4
    if backend == "codex":
        print(f"✓ {meta['ver']}, signed in. The first image confirms the account can generate images.")
        return 0
    prefix = " (auto)" if mode == "auto" else ""
    if backend == "openai":
        print(f"✓ openai{prefix}: OPENAI_API_KEY is set (model {meta['model']}). The first image confirms the key can generate images.")
        return 0
    print(f"✓ gemini{prefix}: {meta['env']} is set (model {meta['model']}). The first image confirms the key can generate images.")
    return 0


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_aspect(s):
    try:
        w, h = s.split(":")
        wf, hf = float(w), float(h)
        v = wf / hf
        if wf > 0 and hf > 0 and math.isfinite(v):
            return v
    except (ValueError, ZeroDivisionError):
        pass
    print(f"--aspect is width:height, e.g. 16:9, 2.35:1, 1:1; got {s}", file=sys.stderr)
    sys.exit(4)


def image_size(path):
    """(width, height) from the file header (PNG, JPEG, WebP), no Pillow needed; None if unreadable."""
    with open(path, "rb") as f:
        head = f.read(64)
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", head[16:24])
        if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            kind = head[12:16]
            if kind == b"VP8 ":
                w, h = struct.unpack("<HH", head[26:30])
                return w & 0x3FFF, h & 0x3FFF
            if kind == b"VP8L":
                b = head[21:25]
                return 1 + (((b[1] & 0x3F) << 8) | b[0]), 1 + (((b[3] & 0xF) << 10) | (b[2] << 2) | ((b[1] & 0xC0) >> 6))
            if kind == b"VP8X":
                return 1 + int.from_bytes(head[24:27], "little"), 1 + int.from_bytes(head[27:30], "little")
        if head[:2] == b"\xff\xd8":
            f.seek(2)
            while True:
                m = f.read(4)
                if len(m) < 4 or m[0] != 0xFF:
                    return None
                seg = struct.unpack(">H", m[2:4])[0]
                if m[1] in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    h, w = struct.unpack(">HH", f.read(5)[1:5])
                    return w, h
                f.seek(seg - 2, 1)
    return None


def aspect_of(path):
    size = image_size(path)
    return size[0] / size[1] if size and size[1] else None


def image_mime_type(path, data=b""):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    guess, _ = mimetypes.guess_type(path)
    return guess or "image/png"


def openai_size_for_aspect(want_ratio, model):
    """Map numeric aspect ratio to a valid OpenAI `size` string for `model`."""
    if want_ratio is None:
        return "1024x1024" if model.startswith("dall-e-") else None
    if model.startswith("dall-e-2"):
        return "1024x1024"
    if model.startswith("dall-e-3"):
        presets = (("1024x1024", 1.0), ("1792x1024", 1792.0 / 1024.0), ("1024x1792", 1024.0 / 1792.0))
        return min(presets, key=lambda item: abs(item[1] / want_ratio - 1.0))[0]
    if model in ("gpt-image-1", "gpt-image-1-mini", "gpt-image-1.5", "chatgpt-image-latest"):
        presets = (("1024x1024", 1.0), ("1536x1024", 1.5), ("1024x1536", 1024.0 / 1536.0))
        return min(presets, key=lambda item: abs(item[1] / want_ratio - 1.0))[0]

    # GPT Image 2+ supports custom WIDTHxHEIGHT divisible by 16, ratio in [1:3, 3:1], area >= 655,360 px.
    clamped = max(1.0 / 3.0, min(3.0, want_ratio))
    target_area = 1024 * 1024
    best = None
    best_score = None
    w_min = max(256, int(math.floor(math.sqrt(655360 * clamped) / 16.0)) * 16)
    w_max = min(3840, int(math.ceil(math.sqrt(1600000 * clamped) / 16.0)) * 16)
    for w in range(w_min, w_max + 16, 16):
        h_mid = int(round((w / clamped) / 16.0)) * 16
        for h in (h_mid - 16, h_mid, h_mid + 16):
            if h < 256 or h > 3840 or w * h < 655360:
                continue
            ratio = w / h
            if ratio < 1.0 / 3.0 or ratio > 3.0:
                continue
            score = (round(abs(ratio / want_ratio - 1.0), 6), abs(w * h - target_area))
            if best_score is None or score < best_score:
                best_score = score
                best = f"{w}x{h}"
    return best or "1024x1024"


def gemini_aspect_for(aspect_str, want_ratio):
    """Map --aspect string/ratio to the closest Gemini imageConfig.aspectRatio value."""
    cleaned = aspect_str.strip()
    for label, _ in GEMINI_ASPECTS:
        if cleaned == label:
            return label
    return min(GEMINI_ASPECTS, key=lambda item: abs(item[1] / want_ratio - 1.0))[0]


def encode_multipart(fields, files):
    """Build a multipart/form-data payload using stdlib only."""
    boundary = f"----LemoOpuscar{uuid.uuid4().hex}"
    chunks = []
    for name, value in fields:
        chunks.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode("utf-8")
        )
    for field_name, filename, content_type, data in files:
        safe_name = re.sub(r'[^A-Za-z0-9._-]', "_", os.path.basename(filename)) or "image.png"
        chunks.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{field_name}"; filename="{safe_name}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode("utf-8")
            + data
            + b"\r\n"
        )
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def extract_http_error(err):
    """Extract a concise diagnostic message from urllib.error.HTTPError."""
    try:
        raw = err.read().decode("utf-8", errors="replace").strip()
    except OSError:
        raw = ""
    if raw:
        try:
            parsed = json.loads(raw)
            msg = parsed.get("error", {}).get("message") if isinstance(parsed, dict) else None
            if msg:
                return f"HTTP {err.code}: {msg}"[:800]
        except json.JSONDecodeError:
            pass
        return f"HTTP {err.code}: {raw[-700:]}"
    return f"HTTP {err.code}: {err.reason}"


def run_openai_once(prompt, refs, want_ratio, timeout):
    """One OpenAI Images API request; returns (image bytes or None, diagnostic tail)."""
    key = openai_api_key()
    model = openai_model()
    base = openai_base_url()
    size = openai_size_for_aspect(want_ratio, model)

    if refs:
        url = f"{base}/images/edits"
        fields = [("model", model), ("prompt", prompt), ("n", "1")]
        if size:
            fields.append(("size", size))
        if model.startswith("gpt-image"):
            fields.append(("output_format", "png"))
        field_name = "image" if len(refs) == 1 and not model.startswith("gpt-image") else "image[]"
        files = []
        for r in refs:
            with open(r, "rb") as f:
                raw_ref = f.read()
            files.append((field_name, os.path.basename(r), image_mime_type(r, raw_ref), raw_ref))
        body, content_type = encode_multipart(fields, files)
    else:
        url = f"{base}/images/generations"
        payload = {"model": model, "prompt": prompt, "n": 1}
        if size:
            payload["size"] = size
        if model.startswith("gpt-image"):
            payload["output_format"] = "png"
        body = json.dumps(payload).encode("utf-8")
        content_type = "application/json"

    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": content_type,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        return None, f"OpenAI API {extract_http_error(e)}"
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return None, f"OpenAI API request failed: {e}"

    try:
        res = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as e:
        return None, f"OpenAI API returned invalid JSON: {e}"

    items = res.get("data") or []
    if not items:
        return None, f"OpenAI API returned no image data: {json.dumps(res)[:700]}"
    item = items[0]
    if item.get("b64_json"):
        try:
            return base64.b64decode(item["b64_json"]), ""
        except (ValueError, TypeError) as e:
            return None, f"OpenAI API returned invalid base64 image: {e}"
    if item.get("url"):
        try:
            with urllib.request.urlopen(item["url"], timeout=timeout) as img_resp:
                return img_resp.read(), ""
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return None, f"OpenAI API failed to download image URL: {e}"
    return None, f"OpenAI API response lacked b64_json or url: {json.dumps(item)[:700]}"


def run_gemini_once(prompt, refs, aspect_str, want_ratio, timeout):
    """One Google Gemini image generation request; returns (image bytes or None, diagnostic tail)."""
    key, _ = gemini_api_key()
    model = gemini_model()
    base = gemini_base_url()

    if model.startswith("imagen-"):
        if refs:
            return None, f"Gemini model {model} does not support --ref/--edit; use a gemini-*-image model"
        url = f"{base}/models/{model}:predict"
        params = {"sampleCount": 1}
        if aspect_str and want_ratio is not None:
            params["aspectRatio"] = gemini_aspect_for(aspect_str, want_ratio)
        payload = {"instances": [{"prompt": prompt}], "parameters": params}
    else:
        url = f"{base}/models/{model}:generateContent"
        parts = []
        for r in refs:
            with open(r, "rb") as f:
                raw_ref = f.read()
            parts.append({
                "inlineData": {
                    "mimeType": image_mime_type(r, raw_ref),
                    "data": base64.b64encode(raw_ref).decode("ascii"),
                }
            })
        parts.append({"text": prompt})
        gen_cfg = {"responseModalities": ["TEXT", "IMAGE"]}
        if aspect_str and want_ratio is not None:
            gen_cfg["imageConfig"] = {"aspectRatio": gemini_aspect_for(aspect_str, want_ratio)}
        payload = {"contents": [{"role": "user", "parts": parts}], "generationConfig": gen_cfg}

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "x-goog-api-key": key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        return None, f"Gemini API {extract_http_error(e)}"
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return None, f"Gemini API request failed: {e}"

    try:
        res = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as e:
        return None, f"Gemini API returned invalid JSON: {e}"

    if model.startswith("imagen-"):
        preds = res.get("predictions") or []
        for pred in preds:
            b64 = pred.get("bytesBase64Encoded")
            if b64:
                try:
                    return base64.b64decode(b64), ""
                except (ValueError, TypeError) as e:
                    return None, f"Gemini Imagen returned invalid base64 image: {e}"
        return None, f"Gemini Imagen returned no image: {json.dumps(res)[:700]}"

    texts = []
    reasons = []
    block = (res.get("promptFeedback") or {}).get("blockReason")
    if block:
        reasons.append(f"blockReason={block}")
    for cand in res.get("candidates") or []:
        if cand.get("finishReason"):
            reasons.append(f"finishReason={cand['finishReason']}")
        content = cand.get("content") or {}
        for part in content.get("parts") or []:
            inline = part.get("inlineData") or part.get("inline_data")
            if isinstance(inline, dict) and inline.get("data"):
                try:
                    return base64.b64decode(inline["data"]), ""
                except (ValueError, TypeError) as e:
                    return None, f"Gemini API returned invalid base64 image: {e}"
            if part.get("text"):
                texts.append(part["text"].strip())
    detail = "; ".join(part for part in ("; ".join(reasons), " ".join(texts)) if part)
    return None, f"Gemini API returned no image ({detail or json.dumps(res)[:600]})"


def find_result(start_ts, token):
    """The image this run produced (its session log carries the token), or None."""
    root = os.path.join(codex_home(), "sessions")
    found = None
    for f in sorted(glob.glob(os.path.join(root, "*", "*", "*", "rollout-*.jsonl")), key=os.path.getmtime):
        if os.path.getmtime(f) < start_ts - 5:
            continue
        try:
            with open(f, encoding="utf-8", errors="ignore") as fh:
                text = fh.read()
        except OSError:
            continue
        if token not in text:
            continue
        for line in text.splitlines():
            if "image_gen" not in line and "image_generation_call" not in line:
                continue
            try:
                ev = json.loads(line).get("payload", {})
            except json.JSONDecodeError:
                continue
            item = ev.get("item", {})
            if item.get("status") == "completed":          # written to generated_images/<thread>/<item>.png
                png = os.path.join(codex_home(), "generated_images", ev.get("thread_id", ""), item.get("id", "") + ".png")
                if os.path.isfile(png):
                    with open(png, "rb") as src:
                        found = src.read()
            if ev.get("type") == "image_generation_call" and ev.get("status") == "completed" and ev.get("result"):
                try:
                    found = base64.b64decode(ev["result"])  # embedded in the session log
                except (ValueError, TypeError):
                    pass
    return found


def run_once(cmd, prompt, token, logpath, timeout):
    """One codex run; returns (image bytes or None, the end of Codex's output)."""
    start = time.time()
    with open(logpath, "w", encoding="utf-8", errors="replace") as log:
        p = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=log,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=True,
        )
        try:
            p.stdin.write(prompt)
            p.stdin.close()
        except BrokenPipeError:
            pass
        data = None
        while time.time() - start < timeout:
            data = find_result(start, token)
            if data or p.poll() is not None:
                break
            time.sleep(POLL_SEC)
        if p.poll() is None:
            if hasattr(os, "killpg") and hasattr(signal, "SIGKILL"):
                try:
                    os.killpg(os.getpgid(p.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    p.kill()
            else:
                p.kill()
    data = data or find_result(start, token)
    try:
        with open(logpath, encoding="utf-8", errors="replace") as f:
            tail = f.read().strip()[-800:]
        os.remove(logpath)
    except OSError:
        tail = ""
    return data, tail


def build_parser():
    ap = Parser(description="Generate one image via Codex CLI, OpenAI Images API, or Gemini API (see the docstring).")
    ap.add_argument("prompt", nargs="?")
    ap.add_argument("--check", action="store_true", help="is an image provider ready (Codex CLI signed in, or API key)?")
    ap.add_argument(
        "--provider",
        default=None,
        type=lambda s: s.strip().lower(),
        choices=PROVIDERS,
        help="image backend: auto (default: signed-in codex, else OPENAI_API_KEY, else GEMINI_API_KEY/GOOGLE_API_KEY), "
             "codex, openai, or gemini (env: LEMO_IMAGE_PROVIDER)",
    )
    ap.add_argument("--aspect", help="width:height, e.g. 16:9, 2.35:1, 1:1; stated in the prompt and checked")
    ap.add_argument("--ref", action="append", default=[], help="reference image (repeatable)")
    ap.add_argument("--edit", action="store_true", help="edit the first --ref; the others are references")
    ap.add_argument("--out", default=".")
    ap.add_argument("--name", default="image", help="file name base, e.g. char_ref")
    ap.add_argument("--reasoning", default="low", help="Codex reasoning effort (low is enough; medium for complex layouts)")
    ap.add_argument("--retries", type=int, default=1)
    ap.add_argument("--timeout", type=int, default=480, help="seconds per attempt")
    return ap


def main(argv=None):
    ap = build_parser()
    a = ap.parse_args(argv)
    try:
        mode = requested_provider(a.provider)
    except ValueError as e:
        ap.error(str(e))
    if a.retries < 0:
        ap.error("--retries must be >= 0")
    if a.timeout <= 0:
        ap.error("--timeout must be > 0")

    if a.check:
        return check(mode)
    if not a.prompt:
        ap.error("a prompt is needed (or --check)")

    backend, err, _ = resolve_backend(mode, for_check=False)
    if backend is None:
        print(err, file=sys.stderr)
        return 4

    want = parse_aspect(a.aspect) if a.aspect else None
    refs = [os.path.abspath(r) for r in a.ref]
    for r in refs:
        if not os.path.isfile(r):
            print(f"reference not found: {r}", file=sys.stderr)
            return 4
    if a.edit and not refs:
        print("--edit needs the image to edit as the first --ref", file=sys.stderr)
        return 4

    os.makedirs(a.out, exist_ok=True)
    target = os.path.abspath(os.path.join(a.out, f"{a.name}_{time.strftime('%Y%m%d-%H%M%S')}.png"))
    ref_sums = {md5(r) for r in refs}

    if backend == "codex":
        head = (EDIT_PREAMBLE if a.edit else PREAMBLE) + (ASPECT_LINE.format(aspect=a.aspect) if a.aspect else "")
        cmd = ["codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only", "-c", f"model_reasoning_effort={a.reasoning}"]
        for r in refs:
            cmd += ["-i", r]   # the prompt goes on stdin: -i takes several values and would swallow a prompt after it
    else:
        api_head = (API_EDIT_PREAMBLE if a.edit else (API_REF_PREAMBLE if refs else "")) + (
            ASPECT_LINE.format(aspect=a.aspect) if a.aspect else ""
        )
        api_prompt = api_head + a.prompt

    label = {"codex": "Codex", "openai": "OpenAI", "gemini": "Gemini"}[backend]
    last, wrong = "", None
    for attempt in range(a.retries + 1):
        if backend == "codex":
            token = f"RUN-{os.getpid()}-{int(time.time() * 1000)}"
            prompt = head + a.prompt + f"\n\n(job {token}; ignore this line)"
            data, tail = run_once(cmd, prompt, token, target + ".log", a.timeout)
        elif backend == "openai":
            data, tail = run_openai_once(api_prompt, refs, want, a.timeout)
        else:
            data, tail = run_gemini_once(api_prompt, refs, a.aspect, want, a.timeout)

        if data and len(data) >= MIN_BYTES:
            with open(target, "wb") as f:
                f.write(data)
            if md5(target) in ref_sums:
                tail += "\nthe result is a copy of a reference; retrying"
                os.remove(target)
            elif want is not None and (got := aspect_of(target)) is not None and abs(got / want - 1) > ASPECT_TOL:
                wrong = target.replace(".png", "_wrongaspect.png")
                os.replace(target, wrong)
                tail += f"\naspect {got:.3f} is not {a.aspect} ({want:.3f} ± 5 %); kept as {wrong}"
            else:
                print(target)
                return 0
        elif data:
            tail += f"\nthe result is too small ({len(data)} bytes) to be an image"
        last = tail.strip()[-800:]
        if attempt < a.retries:
            time.sleep(4 * (attempt + 1))

    if wrong and os.path.isfile(wrong):
        print(f"still the wrong aspect after the retries; the image is kept at {wrong} (crop it, or change the prompt).\n"
              f"End of {label}'s output:\n{last}", file=sys.stderr)
        return 3
    print(f"no image from {label}. End of its output:\n{last}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
