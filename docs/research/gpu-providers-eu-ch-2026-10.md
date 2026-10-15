# GPU providers in Switzerland and the EU

Researched 15.10.2026 for `docs/PLAN.md` §8.6, the later move of GPU inference from the club's
own hardware to a provider.

**Workload**:
- One container run per video: ball tracker, YOLO + ByteTrack, a small classifier.
- One GPU with at least 16 GB.
- About 0.5–1.5 GPU-hours per video, about 100 videos per season (September to May), bursty.
- Inputs: pre-signed S3 URLs on Hetzner Object Storage (2–5 GB). Outputs: small Parquet files.

**Requirements**:
- A Swiss or EU company, processing only in Switzerland or the EU/EEA.
- A DPA under revDSG/GDPR.
- No inputs routed through a US control plane.

**Budget**: about CHF 80 per season.

**Notes on the figures**:
- Prices exclude VAT; Swiss VAT of 8.1 % will usually be added.
- They come from official pages on 15.10.2026 unless marked *unverified* (third-party source, or
  the provider's own page was ambiguous).
- Conversions assume 1 EUR ≈ CHF 0.93 and 1 USD ≈ CHF 0.80.
- Re-check everything before signing up.

## Bottom line

- **Infomaniak** (Swiss): L4 listed from €0.29/h. Cheapest compliant option, if the price
  holds.
- **OVHcloud AI Training** (French): a true run-to-completion job product, billed per minute,
  L4 at €0.83/h. Best product fit.
- **Exoscale** (Swiss): the strongest certifications, but CHF 1.23/h. Fits CHF 80 only at about
  0.5 GPU-hours per video.
- **Seeweb, SwissGPU and Verda** are cheap but each has maturity or compliance gaps.

## 1. Candidates

| Provider (HQ / owner) | GPU location | Cheapest ≥ 16 GB option | Price/h | Billing | "Run one container, stop paying" | Extras | DPA / certifications | Self-service |
|---|---|---|---|---|---|---|---|---|
| **Infomaniak** (Geneva CH; says it is employee-owned) | Switzerland | L4 24 GB, flavors `nvl4-*` with 4–64 vCPU and 8–128 GB ([flavors](https://docs.infomaniak.cloud/compute/instances/flavors/)) | **from €0.2924**; T4 €0.243, A2 €0.163, L40S €0.762 ([GPU page](https://www.infomaniak.com/en/hosting/gpu)). Which config the "from" price covers: *unverified* | Hourly rate prorated to usage. **GPU and IPv4 are billed while the instance is stopped**, so delete it ([prices FAQ](https://www.infomaniak.com/en/hosting/public-cloud/prices), [billing](https://docs.infomaniak.cloud/metering/billing/)) | OpenStack API/CLI/Terraform: create server + cloud-init → `docker run` → the server deletes itself with an application credential | Bandwidth free | [DPA](https://www.infomaniak.com/documents/privacy/DPA/Data_Processing_Agreement_(DPA).pdf) under FADP Art. 9 and GDPR Art. 28. ISO 27001 company-wide (Public Cloud scope *unverified*) | Yes, with CHF 300 trial credit. **GPU flavors on request** |
| **Exoscale** (Akenes SA, Lausanne; A1 Digital / A1 Telekom Austria) | A30 in **CH-GVA-2**; RTX Pro 6000 in CH-DK-2; A40 in DE-FRA-1; A5000 in AT-VIE-2 ([GPU](https://www.exoscale.com/gpu/)) | A30 24 GB, 12 vCPU / 56 GB | **CHF 1.2265** (A30); A40 CHF 1.0453, A5000 CHF 1.34, RTX Pro 6000 CHF 2.15 (price API behind [exoscale.com/pricing](https://www.exoscale.com/pricing/)) | Per second | `exo compute instance create` + cloud-init + self-delete; API, Terraform | Egress CHF 0.02/GiB beyond a free tier; volumes | [DPA](https://www.exoscale.com/dpa/) (GDPR, FADP). ISO 27001/27017/27018, SOC 2 Type 2, BSI C5 ([compliance](https://community.exoscale.com/platform/compliance/)) | Yes. GPU use needs **manual account screening** |
| **OVHcloud AI Training** (Roubaix FR; listed on Euronext) | Gravelines (GRA), France. L4 only there ([capabilities](https://help.ovhcloud.com/csm/en-public-cloud-ai-training-capabilities?id=kb_article_view&sysparm_article=KB0048429)) | `l4-1-gpu`: L4 24 GB, 20 vCores, 80 GB | **€0.83** (L40S €1.55) ([prices](https://www.ovhcloud.com/fr/public-cloud/prices/)) | **Per minute**, from the image pull until the job finishes; queued time free ([lifecycle](https://docs.ovhcloud.com/en/guides/public-cloud/ai-machine-learning/ai-training-billing-lifecycle)) | **Managed job**: `ovhai registry add …`, then `ovhai job run --flavor l4-1-gpu <private image> -- cmd`; ends when the process exits ([registries](https://docs.ovhcloud.com/en/guides/public-cloud/ai-machine-learning/ai-manage-registries)) | Registry and storage | DPA ([FAQ](https://www.ovhcloud.com/en-ie/personal-data-protection/faq/)). ISO 27001/27017/27018/27701 ([AI Training](https://www.ovhcloud.com/en/public-cloud/ai-training/)) | Yes |
| **OVHcloud instances** | France | `rtx5000-28` (Quadro RTX 5000 16 GB, 4 vCores, 28 GB) **€0.36**; `l4-90` €0.75; V100 €0.70; A10 €0.76 | as listed | Most services per second with a 1-minute minimum ([prices](https://www.ovhcloud.com/en/public-cloud/prices/)) | OpenStack VM + cloud-init | Floating IP €0.0027/h | As above | Yes |
| **Scaleway** (Paris; Iliad group) | Paris | L4-1-24G, 8 vCPU / 48 GB | **€0.79** ([pricing](https://www.scaleway.com/en/pricing/gpu/)) | **Per minute**, including boot ([FAQ](https://www.scaleway.com/en/docs/instances/faq/)) | `scw instance server create` + cloud-init. Serverless Jobs are CPU-only | Block storage and flexible IP are mandatory extras | ISO 27001:2022, DPA ([security](https://www.scaleway.com/en/security-and-compliance/)); full DPA text not reviewed | Yes |
| **Seeweb** (Frosinone IT; DHH group) | Italy (GPU data centre not stated) | A30 or Quadro RTX 6000 24 GB **€0.29**; L4 **€0.38** ([GPU](https://www.seeweb.it/en/products/cloud-server-gpu)) | Hourly; spot up to 40 % cheaper | VM via panel/API/Terraform; "[Serverless GPU](https://www.seeweb.it/en/products/serverless-gpu)" attaches GPUs to your own Kubernetes | Traffic free | ISO 27001 and DPA **not found** | Probably |
| **Verda** (formerly DataCrunch; Helsinki FI) | Helsinki ([locations](https://docs.verda.com/welcome-to-verda/locations-and-sustainability/)) | V100 16 GB **$0.218**; RTX A6000 $0.728 ([pricing](https://verda.com/pricing)); availability varies | Prepaid 10-minute increments, unused refunded ([billing](https://docs.verda.com/welcome-to-verda/pricing-and-billing/)) | VM via API + startup script; serverless containers from L40S $1.87 ([containers](https://docs.verda.com/containers/overview/)) | Volumes | ISO 27001 reported (*unverified*); DPA not found | Yes, prepaid |
| **SwissGPU** (LongLink SAGL, Ticino CH) | Switzerland | RTX 3090 **CHF 0.25** or RTX 4090 **CHF 0.40**, plus CHF 0.30/kWh electricity ([pricing](https://www.swissgpu.ch/pricing)) | Per minute; stops when powered off | Bare metal, dashboard + SSH; **no API documented** | Electricity; OS reinstall CHF 0.50 | DPA / ISO **not found** | Yes (Stripe, TWINT) |
| **Leafcloud** (Amsterdam NL) | Amsterdam | A30 24 GB **€0.60** ([pricing](https://www.leaf.cloud/pricing)); another page says "from €0.85" | Hourly (*unverified*) | OpenStack VM | ? | ISO 27001 / SOC 2 claimed | *Unverified* |

**Over budget or poor fit:**

- **Koyeb** (Paris; reportedly acquired by Mistral AI in February 2026,
  [report](https://www.gend.co/en-ca/blog/mistral-koyeb-acquisition-ai-cloud-europe)):
  - L4 $0.70/h, billed per second ([instances](https://www.koyeb.com/docs/reference/instances)).
  - Needs the Pro plan at **$29/month** ([pricing](https://www.koyeb.com/pricing)).
  - GPU-per-region mapping undocumented.
  - The 2021 [DPA](https://www.koyeb.com/docs/legal/data-processing-agreement) lists Google Cloud
    as a subprocessor.
  - No run-to-completion job found.
- **Nebius** (Amsterdam):
  - L40S $1.55/h, preemptible from $0.74, per second ([prices](https://nebius.com/prices)); L40S
    only in Finland.
  - **Serverless AI Jobs** would fit well ([overview](https://docs.nebius.com/serverless/overview)).
  - ISO 27001, SOC 2; DPA not found.
- **Lyceum** (Germany, founded 2025): L40S $1.45/h, per second; `lyceum docker run` jobs; DPA
  offered; locations "agreed per contract" ([compute](https://lyceum.technology/compute/)).
- **UpCloud** (Helsinki): L40S from €1.11/h ([GPU](https://upcloud.com/products/gpu-servers/)).
- **STACKIT** (Schwarz Group): L40S €1.743/h, billed hourly
  ([price list](https://stackit.com/en/asset/download/37788/file/STACKIT_price_list.pdf?version=30)).
- **Hetzner GEX45** (RTX PRO 4000 Blackwell 24 GB): €214/month or €0.3429/h, plus a **€209
  setup fee per order**, which rules out per-job use ([GEX45](https://www.hetzner.com/dedicated-rootserver/gex45/)).
- **IONOS**: only H200 at €3.00/h ([price list](https://docs.ionos.com/support/general-information/price-list/ionos-cloud-eur-en)).
- **Open Telekom Cloud / T Cloud Public**: GPUs in Germany and the Netherlands; Swiss-region GPUs
  not confirmed; no prices published.
- **Gcore**: L40S only as an 8-GPU node ([calculator](https://gcore.com/pricing/ai)).
- **Genesis Cloud**: reportedly in liquidation (*unverified*).
- **Evroc**: B200 only.
- **Sesterce**: a marketplace; data location depends on the offer.
- **Swiss, checked**:
  - **cloudscale.ch**: RTX PRO 6000 only, from about CHF 4.10/h ([GPU](https://www.cloudscale.ch/en/gpu)).
  - **Hikube**: L40S about $1.81/h (aggregator).
  - **PHOENIQS**: enterprise, sales-led ([page](https://phoeniqs.com/en/products-and-services/infrastructure-as-a-service/phoeniqs-cloud)).
  - **Swisscom**: enterprise GPU-as-a-Service, no self-service prices.
  - **Nine.ch**: no GPU product found.

**Not candidates (contrast):**

- **Modal** (US): `region="eu"` pins containers to the EU, but logs, images, volumes and larger
  payloads are stored in the US, and routing defaults to us-east
  ([data residency](https://modal.com/docs/guide/data-residency)). A
  [DPA](https://modal.com/legal/dpa) with SCCs exists; DPF certification not confirmed.
- **RunPod** (US): EU data centres and DPAs mentioned
  ([docs](https://docs.runpod.io/references/security-and-compliance)), but a US control plane and
  no named transfer mechanism.

**Start-up time** is rarely documented: Exoscale "within minutes", Infomaniak "in seconds"
(marketing), OVH jobs "in a few seconds", with billing from the image pull.

## 2. Season cost (100 videos, CHF excl. VAT)

**Assumptions**:
- **VMs: 10 min of billed overhead per job** (boot, about 5 GB image pull, 2–5 GB download,
  teardown).
- **OVH AI Training and SwissGPU: 5 min per job.** For SwissGPU this assumes the image stays
  cached on the powered-off server, which needs verifying.
- **Hourly-billed providers** round each job up to whole hours.
- **SwissGPU electricity**: about 0.45 kWh/h with the RTX 4090, 0.40 kWh/h with the RTX 3090.
- **Same runtime on every GPU.** In practice an A30, A40 or L40S should be faster than an L4,
  and an RTX 5000 slower. Benchmark first.

| Option | 0.5 h/video | 0.8 h/video | 1.5 h/video |
|---|---|---|---|
| Verda V100 ($0.218) | 12 | 17 | 29 |
| **Infomaniak L4** (€0.29, if confirmed) | **18** | **26** | **45** |
| OVH RTX 5000 instance (€0.36) | 22 | 32 | 56 |
| SwissGPU RTX 3090 / 4090 incl. electricity | 22 / 31 | 33 / 47 | 59 / 85 |
| Seeweb A30 / L4 (hourly) | 27 / 35 | 27 / 35 | 54 / 71 |
| **OVH AI Training L4** (€0.83) | **45** | **68** | **122** |
| OVH L4 instance (€0.75) | 46 | 67 | 116 |
| Scaleway L4 (€0.79) | 49 | 71 | 122 |
| Leafcloud A30 (hourly?) | 56 | 56 | 112 |
| **Exoscale** A40 DE / A30 CH | 70 / **82** | 101 / **119** | 174 / **204** |
| Nebius L40S on-demand / preemptible | 83 / 39 | 120 / 57 | 207 / 99 |
| Lyceum L40S | 77 | 112 | 193 |
| Koyeb (Pro plan September–May) | ~209 | ~209 | ~209 |
| STACKIT L40S (hourly) | 162 | 162 | 324 |

Storage, IPs and egress add only a few CHF for most of these (outputs are small; downloads are
inbound). Check the Hetzner Object Storage egress allowance for the 200–500 GB of analysis
copies read per season (within the included 1 TB/month).

## 3. Shortlist

1. **Infomaniak, `nvl4` (primary).**
   - **Why:** Swiss company, Swiss data centres, Swiss law. A FADP/GDPR DPA, CHF billing,
     free bandwidth. The only Swiss option clearly under CHF 1/h (about CHF 18–45 per season).
     OpenStack, so the automation is reusable.
   - **Risks:**
     - GPU flavors are on request, and availability varies by region.
     - No job product: needs the VM adapter (create, cloud-init, run, self-delete).
     - Instances must be deleted, not stopped, or the GPU keeps billing.
     - How hourly billing treats short instances is unclear.
     - What the "from €0.29" price includes is unverified.
2. **OVHcloud AI Training, `l4-1-gpu` (EU, best product fit).**
   - **Why:** a real "run a private image to completion" service with CLI and API, billed per
     minute from the image pull, mature certifications, no VM handling.
   - **Risks:**
     - Fits CHF 80 only up to about 0.9 GPU-h per video. The `rtx5000-28` instance (€0.36) is a
       cheaper but slower fallback.
     - Processing in France.
     - Contract with the EU OVH entity, not OVH US.
     - New-project L4 quota.
3. **Exoscale, A30 in Geneva (Swiss compliance fallback).**
   - **Why:** a Swiss processor with the strongest attestations, per-second billing, a good CLI
     and Terraform.
   - **Risks:**
     - About CHF 1.23/h: fits CHF 80 only at about 0.5 GPU-h per video, unless the A30 proves
       clearly faster than an L4.
     - Manual GPU screening.
     - Group ownership (A1 Telekom Austria) to check.

**If one of these falls through**:
- **Scaleway L4**: like OVH, but a VM rather than a job service.
- **Seeweb**: cheap and hourly; GPU location, ISO 27001 and DPA unconfirmed.
- **SwissGPU**: very cheap and Swiss; small, no API, no DPA found.
- **Verda V100**: cheapest; old GPU, so the image must stay on CUDA 12.x; patchy availability.

## 4. Questions to ask before signing up

- **Infomaniak**:
  - Exact all-in hourly price for the small `nvl4` flavors.
  - GPU access for a club or association.
  - Weekend L4 capacity.
  - How billing treats instances that live 40–100 minutes.
  - GPU-ready images.
  - Whether the ISO 27001 scope covers Public Cloud.
  - How to sign the DPA.
- **OVHcloud**:
  - L4 quota in GRA for a new project.
  - Outbound internet access from jobs to Hetzner S3.
  - Registry options and cost.
  - Contracting and DPA entity (OVH SAS, EU) and subprocessors.
  - Typical pull time for a 5–8 GB image.
- **Exoscale**:
  - GPU validation for a non-profit.
  - A30 capacity in CH-GVA-2.
  - GPU templates with NVIDIA drivers and a container toolkit.
- **All**:
  - Can an association without a UID/VAT number sign up and pay by card, TWINT or invoice?
  - Is Swiss VAT charged?
  - Is the DPA explicitly under the revDSG/FADP?
  - Does support or admin staff access data from outside CH/EU/EEA?
  - Where is the current subprocessor list?
  - Is there any US parent or US subprocessor?

*Not found or unverified*: the exact config behind Infomaniak's "from" price, Scaleway's L40S
price, Koyeb's GPU regions, DPAs for Verda, Nebius, Seeweb and SwissGPU, Leafcloud's billing
granularity, Exoscale's ownership details.
