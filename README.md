# generate-image

A single Codex skill to configure an OpenAI-compatible image relay and generate images. The skill calls your relay; it does not contain an image model or provide API credentials.

## Install

Clone this repository into `${CODEX_HOME:-$HOME/.codex}/skills/generate-image`. Reload Codex if the skill does not appear immediately.

## Configure

On macOS, run in **your own interactive terminal**:

```bash
python3 ~/.codex/skills/generate-image/scripts/setup.py
```

Enter your relay base URL (typically ending in `/v1`). The key is entered at a hidden macOS Keychain prompt, not in chat. Setup queries `GET /models`, keeps IDs prefixed with `gpt-image-`, `nano-banana-`, `grok-imagine-`, or `seedream-`, and lets you pick a default. It does not make an image generation request. To check readiness without disclosing the key, run `python3 ~/.codex/skills/generate-image/scripts/setup.py --status`.

Alternatively, set `GENERATE_IMAGE_BASE_URL`, `GENERATE_IMAGE_KEY`, `GENERATE_IMAGE_MODEL_ID`, and `GENERATE_IMAGE_MODE_LIST` in the environment. `GENERATE_IMAGE_MODE_LIST` is a JSON array of allowed model IDs. Environment variables override local configuration. The default model ID is only used when no model override is supplied; both it and overrides must appear in the filtered list.

The non-secret URL, default ID and filtered list are stored outside this repository in `~/.codex/personal-image/config.json`; the key stays in macOS Keychain unless supplied through an environment variable. Do not add credentials to the repo or send them in chat.

## Generate

Ask Codex to use `$generate-image`, or run:

```bash
python3 ~/.codex/skills/generate-image/scripts/generate.py --prompt "A cinematic landscape" --output-dir ./images
```

Optional: `--model` chooses another discovered model for one call; `--size`, `--n`, and `--async` customize the request. Synchronous `/images/generations` is the default. The relay may charge for image generation.

## Tests

Run `python3 -m unittest discover -s tests -v` from the repository root. Tests use a local mock relay and never make paid image requests.
