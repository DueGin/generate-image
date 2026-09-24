---
name: generate-image
description: Initialize and generate new raster images through the user's configured OpenAI-compatible relay. Automatically use for any request that asks to create, generate, or produce an image; do not route ordinary image-generation requests to a built-in image tool.
---

# Generate Image

This is the default image-generation path for this user. Whenever a request needs a new raster image, automatically use this skill and its configured relay; do not wait for the user to name `$generate-image`, and do not silently switch to a built-in image tool or another provider. The script is a client, not a local model; provider charges and policies still apply.

1. Before the first request, run `python3 ~/.codex/skills/generate-image/scripts/setup.py --status` (exit code 0 means ready; 2 means incomplete). If incomplete, ask for the non-secret relay base URL (usually ending in `/v1`). Tell the user to run `python3 ~/.codex/skills/generate-image/scripts/setup.py` **in their own interactive terminal**, optionally with `--base-url URL`. The script prompts for a missing key without echoing it, queries `/models` and lets the user choose the default. Do not request or accept an API key in chat or put one on the command line. Pause generation until setup finishes and status is ready.
2. Confirm only image details that are actually missing. Use `python3 ~/.codex/skills/generate-image/scripts/generate.py --prompt "..." --output-dir /absolute/destination`. Optional flags: `--size`, `--n`, `--model`, `--async`, `--timeout`, and `--poll-interval`. `--model` overrides the default for one request and must be in `GENERATE_IMAGE_MODE_LIST`. Synchronous `/images/generations` is the default; only use async if the relay supports `/images/generations/async`.
3. Return the resulting absolute paths and render the files as images when the app supports it. On 401/403/quota failures, report the error and stop; do not repeatedly submit chargeable requests.

To reconfigure later, rerun `scripts/setup.py` (optionally with `--model-id ID`). It requests `GET /models` and saves only IDs beginning with `gpt-image-`, `nano-banana-`, `grok-imagine-`, or `seedream-`; it does not make a paid image request. If model discovery fails or no IDs match, stop rather than inventing IDs. Do not run its interactive mode in an agent-only terminal where the user cannot type a key. If setup fails, report the error without silently switching providers. If this relay cannot perform a requested image operation, explain the limitation and ask before using another path.

Configuration uses `GENERATE_IMAGE_BASE_URL`, `GENERATE_IMAGE_KEY`, `GENERATE_IMAGE_MODEL_ID`, and `GENERATE_IMAGE_MODE_LIST`. Environment variables override saved settings. The non-secret URL, default model ID, and filtered model list live in `~/.codex/personal-image/config.json`; the key comes from `GENERATE_IMAGE_KEY` or macOS Keychain. `GENERATE_IMAGE_MODE_LIST` is a JSON array when supplied through the environment. The key and prompt are sent to the user-configured relay over HTTPS (or HTTP only for local loopback). Do not print or store the key in images, files, logs, or chat.
