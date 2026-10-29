# Runbook: the GPU worker (Framework 16)

WiePlay's internal GPU worker (PLAN §8.2, decision O5): the Framework 16 laptop (RTX 5070
Laptop GPU, 8 GB) runs `wieplay.worker` natively as a systemd **user** service and takes the
GPU work from the queue, today the `players` job (PLAN §10.5). It shares the laptop with its
owner (development, training, the meeting scribe): a busy guard keeps it out of the way.
On the laptop itself, `~/wieplay-gpu/README.md` (from `deploy/fw16/README.md`) says what runs
and how to stop it.

## How it is wired

```text
Framework 16 (user units)                                VPS (club's Hetzner)
  wieplay-gpu-worker@prod ──http 127.0.0.1:18091──┐
  wieplay-gpu-worker@dev  ──http 127.0.0.1:18090──┤
  wieplay-gpu-tunnel ───ssh, forward-only key─────┴──▶ user wieplay-tunnel ─▶ 127.0.0.1:8091 / 8090 (API)
       │
       └── signed URLs (per job, from the API) ─▶ Hetzner Object Storage: the 1080p rendition in,
                                                  index, contact sheets, boxes out
```

- **No NAS on the laptop** (its share is root-mounted, and the worker needs nothing from the
  home network): the players job reads the video's 1080p rendition from the bucket through
  signed URLs, a stretch at a time (the next one fetched while the current one is decoded),
  and writes its files to the job's prefix in the bucket (`tracks.npz` too; the artifact
  rows say `s3:<key>`). The NAS gets them with the bucket mirror.
- **Its own path to the API**: an SSH tunnel with its own key to a VPS user that can only
  forward to the two API ports (`restrict,port-forwarding,permitopen=…`, shell
  `/usr/sbin/nologin`). Nothing listens anywhere but on the laptop's loopback; the worker
  routes still refuse anything that came through Cloudflare. Works from any network.
- **Caps `cuda,ml`** (nothing the Lenovo has): the API sends the players job there with
  `WIEPLAY_PLAYERS_CAPS=cuda,ml` in its environment. With the laptop off for long, players
  jobs wait in the queue; to hand them back to the Lenovo's CPU, set
  `WIEPLAY_PLAYERS_CAPS=analyse,nas` and requeue (below).

## The busy guard (`backend/wieplay/worker/gpu_guard.py`)

Before every claim, in this order: `~/wieplay-gpu/PAUSE` exists → wait; on battery → wait;
other processes hold more than `GPU_MAX_OTHER_MIB` (1 GB) of GPU memory → wait (the meeting
scribe holds ~4 GB while it records or transcribes); the card is more than 40 % busy → wait.
Each change of reason is logged once (`journalctl --user -u wieplay-gpu-worker@prod`). The
admin page's pause works as for every worker.

During a job, between frames (checked every 10 s): the same checks, but the card's own load
no longer counts. When another process takes the GPU, the job stops feeding it and waits;
after 20 min it gives the job back (`given_back`: queued again after 30 min, the attempt not
counted) and frees the card. ONNX Runtime's memory is capped (2 GB per model), so the
worker never holds the card full. A lock file (`~/wieplay-gpu/gpu.lock`) keeps the dev and
prod workers from running a job at the same time. Stopping the service gives the running job
back at once.

The GPU-hour ledger: every players run records `timing.gpu_s` (the job's time on the card,
pauses excluded), `providers` and `gpu_paused_s` in its `model_run`.

## Set up (once per laptop)

1. **Tunnel key and VPS user** (done 18.10.2026):

   ```bash
   # on the laptop
   ssh-keygen -t ed25519 -N "" -C wieplay-gpu-tunnel@<host> -f ~/.ssh/wieplay_tunnel_ed25519
   mkdir -p ~/wieplay-gpu && chmod 700 ~/wieplay-gpu
   ssh-keyscan -t ed25519 <vps> > ~/wieplay-gpu/vps_known_hosts   # compare with a known copy
   echo 'WIEPLAY_VPS=wieplay-tunnel@<vps>' > ~/wieplay-gpu/tunnel.env
   # on the VPS, as root
   useradd --system --create-home --shell /usr/sbin/nologin wieplay-tunnel && usermod -p '*' wieplay-tunnel
   install -d -m 700 -o wieplay-tunnel -g wieplay-tunnel /home/wieplay-tunnel/.ssh
   echo 'restrict,port-forwarding,permitopen="127.0.0.1:8090",permitopen="127.0.0.1:8091" <public key>' \
     > /home/wieplay-tunnel/.ssh/authorized_keys   # chown wieplay-tunnel, chmod 600
   ```

2. **Workers in the API** (one per environment) and their tokens on the laptop:

   ```bash
   ssh kscw-server "sudo docker exec wieplay-prod-api-1 python -m wieplay.cli create-worker fw16-prod cuda,ml" \
     | ssh fw16-remote 'umask 077; cat > ~/wieplay-gpu/prod.token'
   ```

3. **The API sends the players job to the GPU**: in the API's environment
   (`deploy/hetzner/env.example`) `WIEPLAY_PLAYERS_CAPS=cuda,ml` and
   `WIEPLAY_PLAYERS_MINUTES=20` (what a coach is told it takes), then `scripts/deploy.sh <env>`.

4. **Deploy**: `scripts/deploy-gpu-worker.sh prod` (and `dev`) from the Lenovo server.
   The user lingers (`loginctl show-user $USER -p Linger`: yes), so the units run without a
   login session.

## Update

`git -C ~/repos/wieplay pull --ff-only origin main && ~/repos/wieplay/scripts/deploy-gpu-worker.sh prod`.
The script ships the committed tree, builds the venv for a new commit (a minute; uv caches
the wheels), copies the weights if the Lenovo image's differ, restarts the worker (a running
job goes back to the queue) and keeps the last releases. Deploy the API first when the worker
routes change: a newer worker against an older API is refused (its first claim asks for no
job, `max: 0`, which older APIs reject with 422) and keeps retrying until the API catches up. Dev the same with `dev`; stop it
afterwards (`ssh fw16-remote systemctl --user stop wieplay-gpu-worker@dev`).

## When something goes wrong

- **Jobs wait**: `journalctl --user -u wieplay-gpu-worker@prod -n 50` on the laptop says why
  (battery, the scribe, PAUSE, the other worker). The admin page shows the worker's last
  contact. `systemctl --user status wieplay-gpu-tunnel` for the tunnel; `curl
  http://127.0.0.1:18091/api/health` on the laptop.
- **The laptop is away or broken**: hand the players back to the Lenovo's CPU: API env
  `WIEPLAY_PLAYERS_CAPS=analyse,nas`, `WIEPLAY_PLAYERS_MINUTES=120`, deploy, then requeue the
  queued players jobs (`wieplay.cli players --video ID`; queued jobs keep their old caps:
  cancel them first from the admin page).
- **CUDA fails** (driver update): the venv's check at deploy prints the providers; ONNX
  Runtime falls back to the CPU and the job records `providers` without CUDA. Redeploy after
  the driver settles; `WIEPLAY_GPU_ORT` overrides the ONNX Runtime package.
- **Revoke**: the worker in the admin page; the VPS user `wieplay-tunnel`
  (`userdel -r wieplay-tunnel`); the units and folder on the laptop (README there).
