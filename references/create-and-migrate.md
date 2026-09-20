# HTTP creation, cloning, and private images

All commands below use `python scripts/autodl_instance.py` from the skill directory. Authenticate through `AUTODL_SUB_ACCOUNT` and `AUTODL_SUB_PASSWORD`. No browser session is used. See [http-api.md](http-api.md) for interface provenance and unverified boundaries.

## Prepare a configuration

Run `list`, then `detail <source-uuid>` for a migration. Use its exact UUID, region, image, configuration, and state. `machines --region <sign>` supplies candidate hosts; `machines --machine-id <id>` checks a chosen host. Do not confuse physical host IDs with instance UUIDs.

Match GPU memory/count, CPU architecture, RAM, disk capacity, and framework/CUDA requirements. Inspect free GPU count and instance slots, not just total GPUs. The helper enforces free GPUs, compatible image architecture/CUDA, and disk limits. Also compare the user's workload requirements, system-disk space and image cache constraints against available evidence; missing fields are unresolved.

Write a local configuration file containing resource choices, never authentication data. Keep operational files in a task output or temporary directory. For creation, replace these example IDs with queried identifiers:

```json
{
  "machine_id": 12345,
  "gpu_count": 1,
  "charge_type": "payg",
  "duration": 1,
  "expand_data_disk_gib": 0,
  "base_image_uuid": "<verified-base-image-uuid>",
  "instance_name": "experiment"
}
```

Use `private_image_uuid` instead of `base_image_uuid` to reuse a saved private image; exactly one is required. Run `images` to obtain it. The helper rechecks readiness and the architecture-filtered private-image catalog. Community/application images are preserved when cloning their source; creating a new community application directly is outside the current configuration interface.

Clone configuration:

```json
{
  "machine_id": 12345,
  "gpu_count": 1,
  "charge_type": "payg",
  "duration": 1,
  "expand_data_disk_gib": 50,
  "copy_data_disk": true,
  "ft_sparse": false
}
```

`copy_data_disk` must explicitly be `true` or `false`. The system disk is always cloned. Include the data disk when code, data, or checkpoints reside there. A symlink into another disk does not copy its target. Cloning is same-region only. Copying the data disk requires at least the source allocated capacity. Expanded storage is an additional allocation in GiB, not the total disk size; do not copy the sample capacity blindly.

The helper requires a shut-down source for consistent cloning and saving. Check active work before any authorized shutdown. Clone image fields come from the source snapshot. Shared-address takeover and cloning to another account are not enabled. Daily clone quotas remain server-enforced; report a quota rejection instead of repeatedly submitting.

## Preview and submit once

```text
python scripts/autodl_instance.py create --config create.json
python scripts/autodl_instance.py clone <source-uuid> --config clone.json
python scripts/autodl_instance.py save-image <source-uuid> --name <unique-name>
```

Without `--execute`, these commands only read state and prepare a request. Output includes configuration, current quote or image rules, and `request_sha256`. Image saving uses `try_again: false` and refuses an existing name, including a pending image.

Review source/target, GPU/VRAM/count, CPU/RAM, region, disks, image, billing period, instance cost, and recurring storage cost. Price API amounts are thousandths of yuan. `price.display_cny` follows the frontend's per-product calculation; `data_disk_info.data_disk_deal_daily_price / 1000` is the expanded disk's daily recurring price. Do not sum amounts with different billing periods. Missing values remain unknown. In pay-as-you-go mode, `duration: 1` is the pricing request value, not an enforced one-hour stop. The quote may change before submission or a later power-on.

Image previews retrieve current official rules using `image-billing` and current usage using `image-usage`. The new image's final size and resulting storage charge are unknown until saving completes; resolve spending scope if outside existing authorization. The storage rule uses the natural day's peak total capacity. Account-specific values take precedence over historical examples; do not promise zero cost.

When the user has authorized the concrete action and spending, rerun the same command with:

```text
--execute --request-sha256 <preview-fingerprint> --receipt <new-absolute-receipt-path.json>
```

The helper reads fresh state and regenerates the request. Changes to the quote, image/source identity, host configuration, or request invalidate the fingerprint. Decide from existing authorization whether a changed proposal needs a new user decision. Before submission, an exclusive local receipt records the source and existing resource UUIDs. Each invocation makes at most one mutation request and never retries after transport failure.

The receipt must be in an existing directory. Reusing its filename is refused. Never bypass an unresolved receipt by supplying a new filename. `SUBMITTING` means outcome unresolved, including a crash or timeout; compare remote instance/image/order lists with the prior UUIDs before proceeding. `ACCEPTED` means only that AutoDL accepted the request. Preserve both successful and unresolved receipts.

## Prepaid orders

`charge_type` can be `daily`, `weekly`, or `monthly`; `duration` is the positive number of those periods. Creating/cloning in these modes submits a prepaid order and does not pay automatically. Read its actual returned UUID:

```text
python scripts/autodl_instance.py order <order-uuid>
python scripts/autodl_instance.py pay-order <order-uuid>
```

`pay-order` previews the exact create/clone order and `deal_price / 1000` yuan. Existing authorization must cover the amount. To pay, add the three submission options with the payment preview's fingerprint and a separate receipt. It uses official `PUT order/pay` and rejects already-paid or unrelated order types. Server expiry/status checks remain authoritative; a stale or uncertain response requires reconciliation.

## Verify the outcome

- After pay-as-you-go creation/cloning or prepaid payment, use the actual instance UUID and `list`/`detail` to verify configuration and running state. An order UUID or accepted response is not a ready machine.
- After saving, use `images` to match name, source UUID, and actual image UUID. Report `saving`, `finished`, or `failed` as returned. Do not restart or modify the source while saving is pending. The image contains only the system disk.
- Poll with bounded calls and report meaningful changes. Timeout does not establish failure; do not promise an unscheduled background check.
- A ready destination still requires separately authorized SSH/workload verification: GPU visibility, CUDA detection, expected files and critical hashes, process/lock state, and a single permitted checkpoint continuation. HTTP provisioning does not prove disk content integrity.

For GPU shortage, refresh hosts after an explicit inventory rejection and select another compatible host within budget. Do not repeatedly power on the unavailable source, silently downgrade memory, expand budget, release the source, or start duplicate training. Cross-region moves require a private system image plus separately authorized data transfer; this helper does not provide cross-instance file transfer. Preserve the source and inspect its actual retention deadline when using it as a fallback.
