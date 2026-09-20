# Create instances, save images, and recover from GPU shortage

Use this reference for ordinary AutoDL container instances. UI routes and labels were inspected on 2026-09-20 with an authenticated sub-account. Forms and navigation were verified without submitting creation, cloning, or image saving; successful paid execution has not been tested. Re-read the live form when labels or restrictions change.

## Select a recovery path

| Need | Path |
| --- | --- |
| Fresh environment | `租用新实例` with a suitable base image |
| Existing environment and data on another host in the same region | Source `更多` → `克隆实例`, explicitly include `数据盘` when needed |
| Reusable system environment or a new instance outside the clone region | Save a private image, then `租用新实例` → `我的镜像`; plan data-disk transfer separately |
| Recover files when a GPU allocation is unavailable | Consider source `更多` → `无卡模式开机` within the authorized scope; this is not GPU experiment execution |

First inspect the source UUID, state, region, GPU/VRAM/count, disk usage, and any known startup error. Do not turn on a stopped instance merely to test a reported shortage. For replacement selection, use the experiment's GPU memory, framework/CUDA, CPU architecture, RAM, disk, region, and cost requirements. If they are unspecified, use the source as the starting point and resolve material differences before committing.

Inspect eligible available hosts within the authorized requirements. Once a suitable candidate is found, prepare one creation. After an explicit inventory rejection, refresh availability and select another eligible host. After a timeout or ambiguous submit result, reconcile the instance/image list first; do not resubmit until the prior outcome is resolved. If no eligible host remains, report the alternatives or missing decision instead of retrying indefinitely. Do not create multiple replacements for a single experiment.

## Save a private image

1. Identify the source by UUID. It must be shut down: the official instructions require shutdown before saving. If it is running, account for active work and use the existing stop/migration authorization; do not interrupt unrelated work. An already stopped source does not need GPU boot to save its image.
2. Check `镜像` → `我的镜像` → `查看计费规则` at https://www.autodl.com/console/image. The inspected rule charges storage above a free allowance using the day's peak total image capacity. Read the current rate and allowance; do not hardcode prices or promise zero cost. The save dialog states that saving agrees to image billing rules; apply the browser tool's agreement/confirmation requirements at submission.
3. In the source row, choose `更多` → `保存镜像`. Use a descriptive unique name, for example project/environment plus date and a short source ID. Inspect the name and source before the final `确定`.
4. **An image contains the entire system disk only. It excludes the data disk and other mounted storage.** Check where the environment, code, checkpoints, and datasets actually live. A system-disk symlink into the data disk does not back up its target. Keep images private; do not use `共享` as part of saving or migration.
5. Submit once within authorization, then use `镜像` to locate the new name and record its actual UUID and state. Wait for a successful/usable state before selecting it for a new instance. Do not boot or modify the source while saving is in progress: AutoDL warns that this can make the image inconsistent or unusable.
6. Report saving as pending if still in progress, or failed if the page shows failure. A closed dialog or accepted submission alone is not completion. If time runs out, retain the identifiers for later verification; do not promise an unscheduled background check.

Official disk-scope and load instructions: https://www.autodl.com/docs/image/ .

## Rent a new instance

1. Open `租用新实例` from the instance list (observed route https://www.autodl.com/create). Record the existing UUIDs before submitting so a newly created instance can be distinguished afterwards.
2. Select the authorized billing mode, region, GPU model and count. For temporary experiments, propose pay-as-you-go unless the user has specified another mode. Wait for inventory to finish loading; an initial empty table is not evidence that the region is sold out.
3. Select an eligible host with enough **free** GPUs, not merely a nonzero total. Inspect its GPU VRAM, per-GPU CPU/RAM, free and expandable disk capacity, and driver/CUDA. The table price is labeled per card; use the final configuration total when assessing the selected GPU count.
4. Set data-disk capacity sufficient for the planned data. Check the final `配置费用` and `日常费用`: expanded storage may be charged even while shut down. Report any time or total-spend limit as a limit to enforce, not as an automatic stop that has already been configured.
5. Under `镜像`, choose `基础镜像` for a fresh environment or `我的镜像` for the verified private image. Confirm the actual image selection; selecting the radio tab alone is insufficient. Confirm its availability for the selected region and architecture. Do not assume images are instantly available in every region; inspect any caching/copy or compatibility prompt.
6. Before `创建并开机`, the concrete proposal should identify region/host, GPU/VRAM/count, CPU/RAM, disks, image, billing mode, hourly total and recurring storage costs. Continue under the existing authorization, or obtain the missing spending decision at this final step. Do not submit merely to test this skill.
7. Submit once and inspect the resulting instance list/detail. Record the new UUID, region/host, selected image, configuration, current state, and SSH command. Wait for provisioning to finish and confirm `运行中` before calling the machine ready; an allocated UUID alone is insufficient.

## Clone an existing instance in the same region

1. In the exact source row, select `更多` → `克隆实例`. Inspect the live source-state requirements; if shutdown is needed, use the same stop safeguards as image saving.
2. The observed dialog has `系统盘` selected and fixed. **`数据盘` is unchecked by default**: select it explicitly when experiment code/data/checkpoints require it. Inspect `优化稀疏文件拷贝` if relevant and the current remaining clone quota; do not hardcode the observed quota.
3. `继续` opens the host-selection page. The page states that cloning is **same-region only**, with other regions disabled. Do not modify the URL to bypass that restriction. The source is preserved: the UI explicitly says cloning does not release it or clear its data.
4. Choose a compatible available host, sufficient disks, and billing mode using the creation checks above. The image description should identify the exact source instance. Recheck any paid disk expansion; source settings may carry over into the clone form.
5. Submit `创建并开机` once within authorization, then verify the destination state and copied data. Do not assume source and destination SSH credentials are identical or launch a second copy of the experiment while source processes are still running.

Cloning need not create a reusable entry under `我的镜像`. If the user also wants a reusable image, perform and verify the separate save-image workflow.

## Data transfer and experiment handoff

Saving/loading a system image is not a complete migration when required files are elsewhere. If cloning is unsuitable, inspect the source menu's `跨实例拷贝数据` form and its live source/destination, region, state, path, overwrite, and cost restrictions. This entry exists, but cross-region support and transfer semantics have not been verified here. Do not promise that it handles cross-region data. When unsuitable, use an authorized SSH/file-transfer route with explicit source and destination paths, enough space, and no destructive synchronization flags.

After creation or transfer, check the following within the authorized access and task scope:

- New UUID and SSH command belong to the destination; keep passwords/tokens out of reports and logs.
- The selected GPU count and memory are visible through `nvidia-smi`; the intended Python/framework environment can detect CUDA. A different GPU generation may require framework compatibility work even when a system image boots successfully.
- Required environment files, code revision, datasets, outputs, and checkpoints exist on their expected disks. Compare file sizes or checksums for critical transferred artifacts.
- The project's process/lock/resume checks permit a single continuation from the intended checkpoint. Do not clear unresolved locks, overwrite completed evidence, change experiment settings, or start training solely because infrastructure is ready.

Report source → destination UUID, actual GPU/configuration and cost, image UUID when applicable, whether the data disk was copied, verification results, and the experiment's actual resume state. Keep the source preserved unless separately authorized to retire it. Check its displayed retention/release deadline if it must remain as a fallback; stopped instances are not guaranteed permanent storage.
