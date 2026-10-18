#!/usr/bin/env bash
# Deploy the WiePlay GPU worker to the GPU host (the Framework 16, PLAN §8.2) and restart it.
#
#   scripts/deploy-gpu-worker.sh dev|prod
#
# Runs on the Lenovo server (or any machine with an ssh login to the GPU host under the alias
# in WIEPLAY_GPU_HOST, default fw16-remote, and the repo). The GPU host has no Docker and no
# access to GitHub: the committed tree goes over as `git archive` of HEAD into
# ~/wieplay-gpu/releases/<sha>/, gets a venv there (uv, the locked dependencies, ONNX Runtime
# for CUDA instead of the CPU one) and ~/wieplay-gpu/<env> points at it. The weights come
# from the newest Lenovo worker image (the same files and SHA-256 as the CPU runs).
# Installs the user units from deploy/fw16/ and the README, restarts
# wieplay-gpu-worker@<env> (prod also enabled at boot; dev only started, off otherwise),
# keeps the last three releases. Once per host: ~/wieplay-gpu/<env>.token (0600) from
# `python -m wieplay.cli create-worker <name> cuda,ml`, ~/wieplay-gpu/tunnel.env
# (WIEPLAY_VPS=wieplay-tunnel@<the VPS>), ~/wieplay-gpu/vps_known_hosts and the tunnel key:
# docs/runbooks/gpu-worker.md.
set -euo pipefail

env="${1:-}"
case "$env" in
  dev|prod) ;;
  *) echo "usage: scripts/deploy-gpu-worker.sh dev|prod" >&2; exit 2 ;;
esac
host="${WIEPLAY_GPU_HOST:-fw16-remote}"
ort="${WIEPLAY_GPU_ORT:-onnxruntime-gpu[cuda,cudnn]==1.22.0}"
cd "$(dirname "$0")/.."

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Working tree has uncommitted changes; commit first so the release means something." >&2
  exit 1
fi
tag="$(git rev-parse --short=12 HEAD)"
remote() { ssh -o BatchMode=yes "$host" "$@"; }

echo "→ $host: release $tag"
remote "mkdir -p ~/wieplay-gpu/releases ~/.config/systemd/user && chmod 700 ~/wieplay-gpu"
remote "test -f ~/wieplay-gpu/$env.token && test -f ~/wieplay-gpu/tunnel.env && test -f ~/wieplay-gpu/vps_known_hosts" || {
  echo "!! $host needs ~/wieplay-gpu/$env.token, tunnel.env and vps_known_hosts (docs/runbooks/gpu-worker.md)" >&2
  exit 1
}
if ! remote "test -x ~/wieplay-gpu/releases/$tag/venv/bin/python"; then
  remote "rm -rf ~/wieplay-gpu/releases/$tag && mkdir -p ~/wieplay-gpu/releases/$tag"
  git archive HEAD backend | remote "tar -x -C ~/wieplay-gpu/releases/$tag"
  echo "→ venv (uv, $ort)"
  remote "set -e; export PATH=\$HOME/.local/bin:\$PATH; cd ~/wieplay-gpu/releases/$tag/backend
    UV_PROJECT_ENVIRONMENT=\$HOME/wieplay-gpu/releases/$tag/venv uv sync --frozen --no-dev --extra analysis --no-editable -q
    uv pip uninstall -q --python ../venv/bin/python onnxruntime
    uv pip install -q --python ../venv/bin/python '$ort'
    ../venv/bin/python -c 'import onnxruntime as o; o.preload_dlls(); assert \"CUDAExecutionProvider\" in o.get_available_providers(), o.get_available_providers()'"
fi
echo "WIEPLAY_GIT_SHA=$tag" | remote "cat > ~/wieplay-gpu/releases/$tag/release.env"

# The weights: the Lenovo worker image's, copied when they differ.
image="$(docker images --format '{{.Repository}}:{{.Tag}}' wieplay-worker | head -n 1)"
if [ -n "$image" ]; then
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  cid="$(docker create "$image")"
  docker cp "$cid:/opt/wieplay-weights/." "$tmp/" >/dev/null
  docker rm "$cid" >/dev/null
  if ! remote "cat ~/wieplay-gpu/weights/MANIFEST.json 2>/dev/null" | cmp -s - "$tmp/MANIFEST.json"; then
    echo "→ weights from $image"
    remote "mkdir -p ~/wieplay-gpu/weights"
    scp -q -o BatchMode=yes "$tmp"/* "$host:wieplay-gpu/weights/"
  fi
else
  remote "test -f ~/wieplay-gpu/weights/MANIFEST.json" || {
    echo "!! no weights on $host and no wieplay-worker image here to take them from" >&2; exit 1; }
fi

# Units, README, the environment's settings (written once from the template).
scp -q -o BatchMode=yes deploy/fw16/wieplay-gpu-tunnel.service deploy/fw16/wieplay-gpu-worker@.service \
  "$host:.config/systemd/user/"
scp -q -o BatchMode=yes deploy/fw16/README.md "$host:wieplay-gpu/README.md"
if ! remote "test -f ~/wieplay-gpu/$env.env"; then
  port=18091; [ "$env" = dev ] && port=18090
  sed -e "s#127.0.0.1:18091#127.0.0.1:$port#" -e "s#work-prod#work-$env#" deploy/fw16/env.example \
    | remote "cat > ~/wieplay-gpu/$env.env && chmod 600 ~/wieplay-gpu/$env.env"
fi
remote "mkdir -p ~/wieplay-gpu/work-$env && ln -sfn releases/$tag ~/wieplay-gpu/$env"

echo "→ restart wieplay-gpu-worker@$env"
remote "systemctl --user daemon-reload && systemctl --user enable --now wieplay-gpu-tunnel.service"
if [ "$env" = prod ]; then
  remote "systemctl --user enable wieplay-gpu-worker@prod.service"
fi
remote "systemctl --user restart wieplay-gpu-worker@$env.service"

# Keep the releases in use and the two newest.
remote "cd ~/wieplay-gpu/releases && keep=\"\$(readlink ../dev 2>/dev/null; readlink ../prod 2>/dev/null; ls -1t | head -n 2)\" && for r in *; do echo \"\$keep\" | grep -q \"\$r\" || rm -rf \"\$r\"; done"
sleep 8
remote "systemctl --user --no-pager --lines=8 status wieplay-gpu-worker@$env.service" || true
echo "✓ wieplay-gpu-worker@$env running $tag on $host"
