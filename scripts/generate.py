#!/usr/bin/env python3
"""Call a configured image relay and save its output as local image files."""

import argparse
import base64
import binascii
import datetime
import json
import mimetypes
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


CONFIG_PATH = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "personal-image" / "config.json"
KEYCHAIN_SERVICE = "codex-personal-image"
KEYCHAIN_ACCOUNT = "default"
MODEL_PREFIXES = ("gpt-image-", "nano-banana-", "grok-imagine-", "seedream-")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def fail(message):
    raise SystemExit(message)


def filtered_models(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    if not isinstance(value, list):
        return []
    return sorted({
        item for item in value if isinstance(item, str)
        and any(item.startswith(prefix) and len(item) > len(prefix) for prefix in MODEL_PREFIXES)
    })


def load_config():
    try:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        config = {}
    if not isinstance(config, dict):
        config = {}
    base_url = os.environ.get("GENERATE_IMAGE_BASE_URL") or config.get("GENERATE_IMAGE_BASE_URL") or config.get("base_url")
    model_id = os.environ.get("GENERATE_IMAGE_MODEL_ID") or config.get("GENERATE_IMAGE_MODEL_ID") or config.get("model")
    raw_models = os.environ.get("GENERATE_IMAGE_MODE_LIST")
    if raw_models is None:
        raw_models = config.get("GENERATE_IMAGE_MODE_LIST", [])
    models = filtered_models(raw_models)
    if not isinstance(base_url, str) or not base_url or not models:
        fail("Image relay URL or model list is missing. Run scripts/setup.py to discover models.")
    parts = urllib.parse.urlsplit(base_url)
    if (parts.scheme not in {"https", "http"} or not parts.netloc or
            parts.username or parts.password or parts.query or parts.fragment):
        fail("GENERATE_IMAGE_BASE_URL must be an HTTP(S) base URL without credentials or query")
    if parts.scheme == "http" and parts.hostname not in {"localhost", "127.0.0.1", "::1"}:
        fail("Remote relays must use HTTPS")
    api_key = os.environ.get("GENERATE_IMAGE_KEY", "").strip()
    if not api_key:
        result = subprocess.run(
            ["security", "find-generic-password", "-a", KEYCHAIN_ACCOUNT,
             "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode or not result.stdout.strip():
            fail("GENERATE_IMAGE_KEY is missing or unavailable. Run scripts/setup.py first.")
        api_key = result.stdout.strip()
    return {"base_url": base_url, "model_id": model_id, "models": models}, api_key


def request_json(url, api_key, payload, timeout):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None,
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json",
                 "Content-Type": "application/json"},
        method="POST" if payload is not None else "GET",
    )
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            status, headers = response.status, response.headers
            body = response.read()
    except urllib.error.HTTPError as error:
        status, headers, body = error.code, error.headers, error.read()
    except urllib.error.URLError as error:
        fail(f"Cannot reach image relay: {error.reason}")
    text = body.decode("utf-8", errors="replace").replace(api_key, "[REDACTED]")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        fail(f"Image relay returned HTTP {status} with non-JSON body: {text[:1000]}")
    if not 200 <= status < 300:
        fail(f"Image relay returned HTTP {status}: {json.dumps(data, ensure_ascii=False)[:2000]}")
    return data, headers


def image_items(value):
    if isinstance(value, dict):
        yield value
        for field in ("data", "output", "images", "result"):
            nested = value.get(field)
            if isinstance(nested, (dict, list)):
                yield from image_items(nested)
    elif isinstance(value, list):
        for item in value:
            yield from image_items(item)


def has_image(response):
    for item in image_items(response):
        for field in ("b64_json", "image", "result", "base64", "url", "image_url"):
            if isinstance(item.get(field), str) and item[field]:
                return True
    return False


def image_extension(content_type):
    extension = mimetypes.guess_extension((content_type or "image/png").split(";")[0])
    return ".jpg" if extension == ".jpe" else extension or ".png"


def extract_images(response, timeout):
    images = []
    seen_urls = set()
    for item in image_items(response):
        for field in ("b64_json", "image", "result", "base64"):
            encoded = item.get(field)
            if not isinstance(encoded, str) or not encoded:
                continue
            media_type = "image/png"
            if encoded.startswith("data:image/"):
                metadata, _, encoded = encoded.partition(",")
                media_type = metadata[5:].split(";")[0]
            try:
                images.append((base64.b64decode(encoded, validate=True), image_extension(media_type)))
                break
            except binascii.Error:
                continue
        else:
            reference = item.get("url") or item.get("image_url")
            if isinstance(reference, dict):
                reference = reference.get("url")
            if not isinstance(reference, str) or reference in seen_urls:
                continue
            if urllib.parse.urlsplit(reference).scheme not in {"http", "https"}:
                fail("Image relay returned an unsupported image URL")
            seen_urls.add(reference)
            try:
                with urllib.request.urlopen(reference, timeout=timeout) as downloaded:
                    images.append((downloaded.read(), image_extension(downloaded.headers.get("Content-Type"))))
            except urllib.error.URLError as error:
                fail(f"Could not download the generated image: {error.reason}")
    return images


def async_request(base_url, api_key, payload, timeout, interval):
    response, headers = request_json(base_url + "/images/generations/async", api_key, payload, timeout)
    if has_image(response):
        return response
    task_id = response.get("task_id") or response.get("id") if isinstance(response, dict) else None
    if not task_id:
        fail("Async relay response has no task ID or image")
    reference = response.get("poll_url") or headers.get("Location")
    poll_url = urllib.parse.urljoin(base_url + "/", reference) if reference else (
        base_url + "/images/tasks/" + urllib.parse.quote(str(task_id), safe="")
    )
    base_origin = urllib.parse.urlsplit(base_url)
    poll_origin = urllib.parse.urlsplit(poll_url)
    if (base_origin.scheme, base_origin.netloc) != (poll_origin.scheme, poll_origin.netloc):
        fail("Refusing to send the API key to a different polling origin")
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            fail("Image task timed out")
        time.sleep(min(interval, remaining))
        response, _ = request_json(poll_url, api_key, None, max(1, deadline - time.monotonic()))
        if has_image(response):
            return response
        if isinstance(response, dict) and str(response.get("status", "")).lower() in {
            "failed", "error", "cancelled", "canceled", "expired"
        }:
            fail(f"Image task failed: {json.dumps(response, ensure_ascii=False)[:2000]}")


def main():
    parser = argparse.ArgumentParser(description="Generate images with a personal relay")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--model")
    parser.add_argument("--size", default="1024x1024")
    parser.add_argument("--n", type=int, default=1)
    parser.add_argument("--output-dir", default=".")
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--poll-interval", type=float, default=3)
    parser.add_argument("--async", dest="async_mode", action="store_true")
    args = parser.parse_args()
    if args.n < 1 or args.timeout <= 0 or args.poll_interval <= 0:
        parser.error("--n, --timeout, and --poll-interval must be positive")

    config, api_key = load_config()
    base_url = config["base_url"].rstrip("/")
    selected_model = args.model or config["model_id"]
    if selected_model not in config["models"]:
        fail(f"Model {selected_model!r} is not in GENERATE_IMAGE_MODE_LIST; run scripts/setup.py")
    payload = {"model": selected_model, "prompt": args.prompt,
               "size": args.size, "n": args.n}
    if args.async_mode:
        response = async_request(base_url, api_key, payload, args.timeout, args.poll_interval)
    else:
        response, _ = request_json(base_url + "/images/generations", api_key, payload, args.timeout)
    images = extract_images(response, args.timeout)
    if not images:
        fail("Relay response contained no supported image data")

    directory = Path(args.output_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    paths = []
    for index, (data, extension) in enumerate(images, 1):
        path = directory / f"image-{timestamp}-{index}{extension}"
        path.write_bytes(data)
        paths.append(str(path))
    print(json.dumps({"files": paths, "count": len(paths)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
