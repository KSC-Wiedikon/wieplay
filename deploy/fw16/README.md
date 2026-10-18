# WiePlay GPU worker on this machine

This folder (`~/wieplay-gpu`) runs WiePlay's GPU worker (KSC Wiedikon's video analysis,
repo `KSC-Wiedikon/wieplay`, PLAN §8.2). It takes GPU work from the WiePlay queue (today:
finding the players of a match, ~15–20 min of GPU per match) and gives way to everything
else on this laptop. Deployed by `scripts/deploy-gpu-worker.sh dev|prod` from the Lenovo
server; runbook: `docs/runbooks/gpu-worker.md` in the repo.

## What runs

| Unit (systemd `--user`) | Does |
|---|---|
| `wieplay-gpu-tunnel.service` | SSH tunnel to the VPS, forward-only key (`~/.ssh/wieplay_tunnel_ed25519`): the APIs on 127.0.0.1:18090 (dev) and :18091 (prod) of this machine, nowhere else |
| `wieplay-gpu-worker@prod.service` | The prod worker, starts with the machine |
| `wieplay-gpu-worker@dev.service` | The dev worker, started by hand for tests, off otherwise |

The worker never takes a job while the laptop is on battery, while another process holds
more than 1 GB of GPU memory (the meeting scribe holds ~4 GB while it records or
transcribes) or while the card is busy, and it stops feeding the GPU mid-job as soon as
that happens (after 20 min the job goes back to the queue). Its own GPU memory is capped
at 2 GB per model. It runs at low CPU and disk priority.

## Files

| Path | What |
|---|---|
| `prod`, `dev` | Symlinks to the release each environment runs |
| `releases/<sha>/` | A release: `backend/` (the code at that commit), `venv/`, `release.env` |
| `weights/` | Person detector and shirt-number reader (ONNX), `MANIFEST.json` with sources, licences, SHA-256 |
| `prod.env`, `dev.env` | Settings (no secrets) |
| `prod.token`, `dev.token` | Worker tokens (0600) |
| `tunnel.env`, `vps_known_hosts` | Where the tunnel goes, the VPS's host key |
| `work-prod/`, `work-dev/` | Scratch: the video stretches of the running job (deleted after each job) |
| `PAUSE` | If this file exists, the workers take no new job and pause the current one |
| `gpu.lock` | One job on the GPU at a time (dev and prod share it) |

Nothing here holds storage credentials: the worker gets short-lived signed URLs for the
video and its outputs. Crops of players exist only in memory and in the bucket.

## Everyday

```bash
touch ~/wieplay-gpu/PAUSE                         # pause (e.g. before a long GPU task)
rm ~/wieplay-gpu/PAUSE                            # resume
systemctl --user stop wieplay-gpu-worker@prod     # stop now (the running job goes back to the queue)
systemctl --user disable --now wieplay-gpu-worker@prod wieplay-gpu-tunnel  # off for good
journalctl --user -u wieplay-gpu-worker@prod -f   # what it does, and why it waits
systemctl --user start wieplay-gpu-worker@dev     # the dev worker, for a test
```

Removing it entirely: disable the units as above, delete `~/.config/systemd/user/wieplay-gpu-*`,
`~/wieplay-gpu` and `~/.ssh/wieplay_tunnel_ed25519*`, revoke the workers in WiePlay's admin page,
and remove the user `wieplay-tunnel` on the VPS.
