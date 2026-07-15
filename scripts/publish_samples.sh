#!/usr/bin/env bash
# Publish on-box sample videos into MediaMTX as looping RTSP streams, standing in
# for physical cameras so the whole pipeline runs on real streams. Uses a static
# ffmpeg container on the host network (reaches mediamtx on localhost:8554).
set -u

FF_IMAGE="mwader/static-ffmpeg:latest"
RTSP_BASE="rtsp://localhost:8554"
PATHS=(cam_lobby cam_dock cam_perimeter cam_parking)

# gather up to 4 real sample videos
mapfile -t VIDS < <(ls /home/jo/Documents/dashcam-videos/*.MP4 2>/dev/null | head -4)
if [ "${#VIDS[@]}" -eq 0 ]; then
  mapfile -t VIDS < <(ls /home/jo/Videos/*.mp4 2>/dev/null | head -4)
fi
if [ "${#VIDS[@]}" -eq 0 ]; then
  echo "no sample videos found under /home/jo/Documents/dashcam-videos or /home/jo/Videos" >&2
  exit 1
fi

echo "pulling $FF_IMAGE (first run only)..."
docker pull -q "$FF_IMAGE" >/dev/null

# stop any prior publishers
docker ps -aq --filter "name=sentigon-pub-" | xargs -r docker rm -f >/dev/null 2>&1

i=0
for p in "${PATHS[@]}"; do
  v="${VIDS[$(( i % ${#VIDS[@]} ))]}"
  dir=$(dirname "$v"); base=$(basename "$v")
  docker run -d --rm --name "sentigon-pub-$p" --network host \
    -v "$dir":/videos:ro "$FF_IMAGE" \
    -hide_banner -loglevel warning -stream_loop -1 -re -i "/videos/$base" \
    -an -vf "scale=1280:720,fps=15" \
    -c:v libx264 -preset veryfast -tune zerolatency -g 30 -pix_fmt yuv420p \
    -f rtsp -rtsp_transport tcp "$RTSP_BASE/$p" >/dev/null
  echo "  publishing $(basename "$v") -> $RTSP_BASE/$p"
  i=$(( i + 1 ))
done

echo "waiting for streams to become ready..."
sleep 6
for p in "${PATHS[@]}"; do
  ready=$(curl -s "http://localhost:9997/v3/paths/get/$p" | grep -o '"ready":[a-z]*' | head -1)
  echo "  $p: ${ready:-not-found}"
done
