#!/usr/bin/env sh
# Download the default model (Llama 3.2 1B Instruct, Q4_K_M, ~0.8 GB) into ./models
set -e

FILE="Llama-3.2-1B-Instruct-Q4_K_M.gguf"
URL="https://huggingface.co/bartowski/Llama-3.2-1B-Instruct-GGUF/resolve/main/$FILE"

mkdir -p models
curl -L -C - --fail -o "models/$FILE" "$URL"
echo "Saved models/$FILE"
