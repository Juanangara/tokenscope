#!/usr/bin/env sh
# Download the three models the benchmark compares into ./models (~4.1 GB in total; the first
# one is the same model download_model.sh fetches).
set -e

mkdir -p models

fetch() {
  repo="$1"
  file="$2"
  echo "Downloading $file"
  curl -L -C - --fail -o "models/$file" "https://huggingface.co/$repo/resolve/main/$file"
}

fetch bartowski/Llama-3.2-1B-Instruct-GGUF Llama-3.2-1B-Instruct-Q4_K_M.gguf   # ~0.8 GB
fetch bartowski/Llama-3.2-1B-Instruct-GGUF Llama-3.2-1B-Instruct-Q8_0.gguf     # ~1.3 GB
fetch bartowski/Llama-3.2-3B-Instruct-GGUF Llama-3.2-3B-Instruct-Q4_K_M.gguf   # ~2.0 GB
