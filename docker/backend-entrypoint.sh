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

exec "$@"

