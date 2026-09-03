#!/bin/bash
# cam-look.sh — one frame from the webcam, for an agent asked to look at
# the room. Prints the JPEG path; then READ that file. Only when the person
# asks you to look, or asks something only the room can answer -- and say
# you are looking before you do. The first use asks macOS for camera
# permission for the app that runs the voice line; allow it once.
#
#   cam-look.sh            -> /tmp/.../cam-look.jpg
#   cam-look.sh out.jpg
set -e
OUT="${1:-${TMPDIR:-/tmp}/cam-look.jpg}"
if command -v imagesnap >/dev/null; then
  imagesnap -w 1 -q "$OUT" >/dev/null 2>&1 || { echo "cam-look.sh: imagesnap failed" >&2; exit 3; }
elif command -v ffmpeg >/dev/null; then
  # avfoundation: device 0 is the built-in camera; uyvy422 is what it speaks.
  ffmpeg -hide_banner -loglevel error -y -f avfoundation -framerate 30 -pixel_format uyvy422 \
    -video_size 1280x720 -i "0" -frames:v 1 -q:v 3 "$OUT" </dev/null \
    || { echo "cam-look.sh: ffmpeg could not read the camera (is it allowed in System Settings > Privacy & Security > Camera for the app running this?)" >&2; exit 3; }
else
  echo "cam-look.sh: needs ffmpeg (brew install ffmpeg) or imagesnap" >&2; exit 2
fi
echo "$OUT"
echo "cam-look.sh: one frame from the camera. Read this file to look at it." >&2
