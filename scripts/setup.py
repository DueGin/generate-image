#!/usr/bin/env python3
"""Configure an image relay without putting its API key in chat or argv."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
import urllib.error
import urllib.request
from urllib.parse import urlsplit


CONFIG_DIR = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "personal-image"
CONFIG_PATH = CONFIG_DIR / "config.json"
KEYCHAIN_SERVICE = "codex-personal-image"
KEYCHAIN_ACCOUNT = "default"
MODEL_PREFIXES = ("gpt-image-", "nano-banana-", "grok-imagine-", "seedream-")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def saved_config():
    try:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return config if isinstance(config, dict) else {}
    except (FileNotFoundError, OSError, ValueError):
        return {}


def key_exists():
    if os.environ.get("GENERATE_IMAGE_KEY", "").strip():
        return True
    result = subprocess.run(
        ["security", "find-generic-password", "-a", KEYCHAIN_ACCOUNT, "-s", KEYCHAIN_SERVICE],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def api_key():
    if os.environ.get("GENERATE_IMAGE_KEY", "").strip():
        return os.environ["GENERATE_IMAGE_KEY"].strip()
    result = subprocess.run(
        ["security", "find-generic-password", "-a", KEYCHAIN_ACCOUNT,
         "-s", KEYCHAIN_SERVICE, "-w"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode or not result.stdout.strip():
        raise ValueError("API key is unavailable; set GENERATE_IMAGE_KEY or store it in Keychain")
    return result.stdout.strip()


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


def effective_config():
    current = saved_config()
    mode_list = os.environ.get("GENERATE_IMAGE_MODE_LIST")
    if mode_list is None:
        mode_list = current.get("GENERATE_IMAGE_MODE_LIST", [])
    return {
        "GENERATE_IMAGE_BASE_URL": os.environ.get("GENERATE_IMAGE_BASE_URL")
        or current.get("GENERATE_IMAGE_BASE_URL") or current.get("base_url"),
        "GENERATE_IMAGE_MODEL_ID": os.environ.get("GENERATE_IMAGE_MODEL_ID")
        or current.get("GENERATE_IMAGE_MODEL_ID") or current.get("model"),
        "GENERATE_IMAGE_MODE_LIST": filtered_models(mode_list),
    }


def status():
    config = effective_config()
    url = config["GENERATE_IMAGE_BASE_URL"]
    model = config["GENERATE_IMAGE_MODEL_ID"]
    models = config["GENERATE_IMAGE_MODE_LIST"]
    try:
        url = validate_url(url) if isinstance(url, str) else None
    except ValueError:
        url = None
    model = model if isinstance(model, str) and model in models else None
    has_key = key_exists()
    print(f"GENERATE_IMAGE_BASE_URL: {url or 'missing'}")
    print(f"GENERATE_IMAGE_KEY: {'present (environment or Keychain)' if has_key else 'missing'}")
    print(f"GENERATE_IMAGE_MODEL_ID: {model or 'missing or not in model list'}")
    print(f"GENERATE_IMAGE_MODE_LIST: {json.dumps(models, ensure_ascii=False)}")
    return 0 if url and model and models and has_key else 2


def validate_url(value):
    value = value.strip().rstrip("/")
    parts = urlsplit(value)
    if parts.scheme not in {"https", "http"} or not parts.netloc or parts.username or parts.password:
        raise ValueError("Provide an HTTP(S) base URL without credentials")
    if parts.scheme == "http" and parts.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Remote relays must use HTTPS")
    if parts.query or parts.fragment or "/images/generations" in parts.path or parts.path.endswith("/models"):
        raise ValueError("Provide only the base URL, without query or an API endpoint")
    return value


def fetch_models(base_url, key):
    request = urllib.request.Request(
        base_url + "/models",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=20) as response:
            body = response.read()
    except urllib.error.HTTPError as error:
        with error:
            detail = error.read().decode("utf-8", errors="replace").replace(key, "[REDACTED]")
        raise ValueError(f"GET /models returned HTTP {error.code}: {detail[:500]}") from error
    except urllib.error.URLError as error:
        raise ValueError(f"GET /models failed: {error.reason}") from error
    try:
        catalog = json.loads(body)
    except ValueError as error:
        raise ValueError("GET /models did not return JSON") from error
    entries = catalog.get("data") if isinstance(catalog, dict) else None
    if not isinstance(entries, list):
        raise ValueError("GET /models response has no data array")
    identifiers = [entry.get("id") for entry in entries if isinstance(entry, dict)]
    models = filtered_models(identifiers)
    if not models:
        raise ValueError("GET /models returned no supported image model IDs")
    return models


def save_config(base_url, model_id, models):
    CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(CONFIG_DIR, 0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".config-", dir=CONFIG_DIR)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump({
                "GENERATE_IMAGE_BASE_URL": base_url,
                "GENERATE_IMAGE_MODEL_ID": model_id,
                "GENERATE_IMAGE_MODE_LIST": models,
            }, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, CONFIG_PATH)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description="Configure generate-image on macOS")
    parser.add_argument("--status", action="store_true", help="Check configuration without revealing the key")
    parser.add_argument("--base-url", help="OpenAI-compatible relay base URL")
    parser.add_argument("--model-id", "--model", dest="model_id", help="Default image model ID")
    args = parser.parse_args()

    if sys.platform != "darwin":
        parser.error("This setup uses the macOS Keychain and requires macOS")
    try:
        if args.status:
            return status()
        current = effective_config()
        if args.base_url and os.environ.get("GENERATE_IMAGE_BASE_URL") and args.base_url != os.environ["GENERATE_IMAGE_BASE_URL"]:
            raise ValueError("--base-url conflicts with GENERATE_IMAGE_BASE_URL; unset the environment override first")
        base_url = args.base_url or current["GENERATE_IMAGE_BASE_URL"]
        if not base_url:
            if not sys.stdin.isatty():
                raise ValueError("Provide GENERATE_IMAGE_BASE_URL or run setup in your own terminal")
            base_url = input("Relay base URL: ").strip()
        base_url = validate_url(base_url)

        if not os.environ.get("GENERATE_IMAGE_KEY", "").strip():
            stored_key = key_exists()
            if not sys.stdin.isatty() and not stored_key:
                raise ValueError("Set GENERATE_IMAGE_KEY or run setup in your own terminal for the hidden key prompt")
            replace = stored_key and sys.stdin.isatty() and input(
                "Replace existing Keychain API key? [y/N]: "
            ).strip().lower() == "y"
            if not stored_key or replace:
                print("Enter the relay API key at the macOS Keychain prompt (input is hidden).")
                subprocess.run(
                    ["security", "add-generic-password", "-a", KEYCHAIN_ACCOUNT,
                     "-s", KEYCHAIN_SERVICE, "-U", "-w"],
                    check=True,
                )

        models = fetch_models(base_url, api_key())
        if os.environ.get("GENERATE_IMAGE_MODE_LIST") is not None and current["GENERATE_IMAGE_MODE_LIST"] != models:
            raise ValueError("GENERATE_IMAGE_MODE_LIST conflicts with /models; unset the override and rerun setup")
        print("Supported image model IDs:")
        for index, identifier in enumerate(models, 1):
            print(f"  {index}. {identifier}")
        preferred = args.model_id or current["GENERATE_IMAGE_MODEL_ID"]
        if args.model_id and args.model_id not in models:
            raise ValueError(f"Default model {args.model_id!r} is not in the filtered upstream list")
        if os.environ.get("GENERATE_IMAGE_MODEL_ID") and preferred not in models:
            raise ValueError("GENERATE_IMAGE_MODEL_ID is not in the filtered upstream list")
        if args.model_id and os.environ.get("GENERATE_IMAGE_MODEL_ID") and args.model_id != os.environ["GENERATE_IMAGE_MODEL_ID"]:
            raise ValueError("--model-id conflicts with GENERATE_IMAGE_MODEL_ID; unset the override first")
        if preferred not in models:
            preferred = "gpt-image-2" if "gpt-image-2" in models else models[0]
        model_id = preferred
        if sys.stdin.isatty() and not args.model_id and not os.environ.get("GENERATE_IMAGE_MODEL_ID"):
            selected = input(f"Default model ID or number [{preferred}]: ").strip()
            if selected.isdecimal() and 1 <= int(selected) <= len(models):
                model_id = models[int(selected) - 1]
            elif selected:
                model_id = selected
        if model_id not in models:
            raise ValueError(f"Default model {model_id!r} is not in the filtered upstream list")
        save_config(base_url, model_id, models)
        print("Configuration saved; no image request was made.")
        return status()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"Setup failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
