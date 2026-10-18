#!/bin/bash
# Natural anchor voices for Alex's channel (jb-anchor): free Piper voices that run on the Mac itself, with no
# internet once downloaded. Safe to run again. If anything fails, the channel keeps the Mac voice (Samantha).
# Run:   cd ~/josephs-board && bash bin/jb-voices-setup.sh
cd ~/josephs-board || exit 1
echo "Natural voices: installing the free voice engine (sherpa-onnx)..."
if ! .venv/bin/python -m pip install -q "sherpa-onnx==1.13.8" >/tmp/jb-voices.log 2>&1; then
  tail -2 /tmp/jb-voices.log
  echo "Natural voices: the voice engine would not install on this Mac (it needs macOS 10.15 or newer)."
  echo "The channel keeps the Mac voice. Everything else is installed."
  exit 0
fi
mkdir -p voices
for v in en_US-lessac-medium en_US-ryan-medium en_US-amy-medium; do
  d="voices/vits-piper-$v"
  if [ -f "$d/tokens.txt" ]; then echo "  $v: already here"; continue; fi
  echo "  downloading voice $v (about 65 MB)..."
  if curl -sSfL --retry 3 -o "/tmp/jb-$v.tar.bz2" "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-piper-$v.tar.bz2" \
     && tar xjf "/tmp/jb-$v.tar.bz2" -C voices; then echo "  $v: OK"; else echo "  $v: download failed (skipped)"; rm -rf "$d"; fi
  rm -f "/tmp/jb-$v.tar.bz2"
done
echo "Natural voices: testing each one on this Mac..."
.venv/bin/python bin/jb-piper.py --test
