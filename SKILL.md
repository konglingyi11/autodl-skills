---
name: autodl-instance
description: Manage AutoDL container instances, create or clone a replacement when GPUs are unavailable, and save or reuse private images. Use the sub-account HTTP helper for listing and power operations, and the browser for creation, cloning, and images. Not for Container Instance Pro.
---

# AutoDL container instances

Manage the ordinary container instance product at https://www.autodl.com/console/instance/list. Use the exact instance UUID to identify the source; a physical host number, row position, or instance name alone is insufficient.

## Choose the operation

| Request | Method |
| --- | --- |
| List instances, SSH command, start or stop | HTTP helper below; browser fallback when environment credentials are unavailable |
| Rent a new instance, save or reuse an image | Browser workflow in [references/create-and-migrate.md](references/create-and-migrate.md) |
| Cannot start because the host has no free GPU; move the experiment | Read the same reference; consider same-region cloning with the data disk first |

Read the reference before creating, cloning, saving images, or migrating. It covers current UI labels, costs, disk scope, and completion checks. These operations are browser-supported; the Python helper implements only `list`, `start`, and `stop`. Do not invent additional CLI commands or mutation endpoints.

Use the available browser/computer-use tool with its documented initialization. Reuse an AutoDL tab in the correct account, or open the console. A browser session can be used without the helper's environment variables; never extract its token or cookies. If a capability is unavailable to the sub-account, report the permission limitation rather than switching accounts or expanding access.

## Listing and power operations

Use [scripts/autodl_instance.py](scripts/autodl_instance.py). It uses `curl_cffi` Chrome impersonation and the site's sub-account login, ticket exchange, and instance endpoints. The script never stores a token or prints the raw instance record. Resolve script paths relative to this skill directory, not the experiment repository.

Set `AUTODL_SUB_ACCOUNT` to `<子账号用户名>@<主账户ID>` and `AUTODL_SUB_PASSWORD` to its password in the process environment. Do not place credentials in a command, skill file, or chat response. If `curl_cffi` is missing, install it in the intended Python environment (`python -m pip install curl_cffi`).

Run `python scripts/autodl_instance.py list` to list all pages with each instance's `ssh_command`. The password is omitted by default; use `list --show-ssh-password` only when the user explicitly needs the SSH password, and keep that output out of logs and shared files. Run `python scripts/autodl_instance.py start <instance-uuid>` or `stop <instance-uuid>` only when the user has asked for that state change on the identified instance. The script checks that the UUID exists and is in the expected state before requesting the change. A successful response means the operation was accepted; query `list` again to verify its eventual state. Start can resume billing, and stopping a pay-as-you-go instance can release its GPU allocation.

The endpoints are inferred from AutoDL's web client as of 2026-09-20 and may change. If a response indicates authentication or schema failure, inspect the current site behavior before updating the script. Do not extract an existing browser token, print full API responses, or retry a state-changing request after an uncertain network outcome.

## Authorization and experiment continuity

An instruction to extend this skill authorizes local edits and read-only inspection, not a billed test instance or a new image. For operational requests, use existing session authorization and constraints; do not ask again for an unchanged authorized action. Before a paid submission, make the selected configuration and current costs concrete. If spending scope is missing, prepare the form and ask only for the missing decision before submitting. Follow the tool's confirmation requirements for actual payment or agreement steps.

GPU shortage is a reason to select another eligible host, not to retry power-on indefinitely. Distinguish host inventory shortage from insufficient GPU memory for a workload, billing errors, account permissions, or service faults. Do not silently downgrade required VRAM, change architecture, or increase the spending limit.

Keep the source instance and its data until migration is verified; creating a replacement does not authorize releasing, resetting, or changing the source image. Provisioning success does not mean the experiment resumed. Follow the project's own resume protocol, locks, and process checks before launching work on the replacement, and preserve existing results and checkpoints.
