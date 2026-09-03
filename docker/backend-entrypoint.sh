#!/bin/sh
set -eu

secret_file="/run/secrets/llm_api_key"
if [ ! -r "$secret_file" ]; then
  echo "ERROR: missing readable LLM API key secret: $secret_file" >&2
  exit 1
fi

DEEPSEEK_API_KEY="$(tr -d '\r\n' < "$secret_file")"
if [ -z "$DEEPSEEK_API_KEY" ]; then
  echo "ERROR: LLM API key secret is empty" >&2
  exit 1
fi
export DEEPSEEK_API_KEY

signing_secret_file="/run/secrets/harness_signing_secret"
if [ ! -r "$signing_secret_file" ]; then
  echo "ERROR: missing readable Harness signing secret: $signing_secret_file" >&2
  exit 1
fi

DWP_HARNESS_TOOL_SIGNING_SECRET="$(tr -d '\r\n' < "$signing_secret_file")"
if [ -z "$DWP_HARNESS_TOOL_SIGNING_SECRET" ]; then
  echo "ERROR: Harness signing secret is empty" >&2
  exit 1
fi
export DWP_HARNESS_TOOL_SIGNING_SECRET

exec "$@"
