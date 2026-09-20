---
name: autodl-instance
description: Manage ordinary AutoDL container instances and private images through direct HTTP requests, including listing, power, image storage, creation, and same-region cloning. Not for Container Instance Pro.
---

# AutoDL through HTTP

Use [scripts/autodl_instance.py](scripts/autodl_instance.py) for all supported operations. Do not open or operate the browser for this skill, including as a fallback. The helper signs in with sub-account credentials; it does not extract browser cookies or tokens.

## Authentication and queries

Set `AUTODL_SUB_ACCOUNT` to `<username>@<main-account-id>` and `AUTODL_SUB_PASSWORD` in the process environment. Never put credentials in command arguments, chat, receipts, or skill files. If absent, explain that authenticated HTTP operations need these environment variables; continue local implementation or public-document work when applicable. Do not switch accounts or broaden permissions to overcome a denial.

Use Python with `curl_cffi`; install it in the intended environment if necessary (`python -m pip install curl_cffi`). Resolve script paths relative to this skill, not the research repository. Commands below use `python scripts/autodl_instance.py` from the skill directory:

| Task | Command |
| --- | --- |
| Instances and SSH commands, all pages | `list` |
| Exact source configuration and image | `detail <instance-uuid>` |
| Start / stop | `start <instance-uuid>` / `stop <instance-uuid>` |
| Private/shared images visible to this sub-account | `images` |
| Image storage usage and today's estimated charge | `image-usage` |
| Current official image storage rules, no credentials needed | `image-billing` |
| Region identifiers | `regions` |
| Available hosts | `machines --region <sign> --gpu <model>` |
| Exact host | `machines --machine-id <id>` |
| Base image metadata | `base-image <image-uuid>` |
| Create / clone / save / pay for a prepared order | Read the workflow below |

`list` suppresses SSH passwords. Use `list --show-ssh-password` only when explicitly needed by the user, and keep that output out of shared logs. Never print full raw instance, login, or snapshot records. An empty image list describes the sub-account's visible scope; nonzero storage usage alongside it does not establish that the main account has no images.

## Creation, cloning, and images

Before these operations, read [references/create-and-migrate.md](references/create-and-migrate.md). It covers configuration JSON, preview fingerprints, submission receipts, costs, and completion verification. The commands are `create`, `clone`, `save-image`, `order`, and `pay-order`. New resource mutations default to read-only previews; `--execute` submits the exact reviewed configuration once. This technical preview step does not require repeatedly asking the user when their existing authorization already covers the concrete action and spending.

An instruction to extend this skill authorizes local edits and read-only HTTP discovery, not creating a billed instance or image. Before a paid submission, report the concrete source/target, disks, billing mode, quote, and recurring storage charges. Ask only for a genuinely missing spending or scope decision. Preserve the source unless separately authorized to retire it. A saved image includes the system disk only; cloning must explicitly choose whether to copy the data disk.

Power changes require the user's instruction for the exact instance. After an accepted `start` or `stop`, query `list` to verify the eventual state. Starting can resume charges; stopping may release GPU allocation. Do not start a stopped source merely to test reported GPU shortage.

## Interface evidence and failures

Read [references/http-api.md](references/http-api.md) when maintaining requests or diagnosing authentication/schema errors. Endpoint methods, payload fields, and sub-account routing were derived from the official public web client on 2026-09-20. Authenticated resource operations added in this revision have offline contract tests but have not been tested against a logged-in account or submitted for billing. Do not claim successful live cloning based on these tests.

On interface changes, inspect official public documentation and JavaScript using HTTP. Do not guess mutation endpoints or probe them with dummy requests. Reject incomplete schemas and permission failures without falling back to the browser. After an uncertain mutation, reconcile the instance/image/order list and the local receipt before any retry; an unresolved request is not evidence of failure. An existing receipt prevents reuse of that receipt, not every possible duplicate made with a new filename.

Before resuming experiments, follow the project's own process, checkpoint, lock, and code-hash protocol. Infrastructure provisioning alone does not authorize launching training or clearing unresolved locks. Other storage mounts require separately scoped transfer work; the helper does not implement cross-instance file transfer, sharing, release, or Container Instance Pro.

## Verification after changes

Run `python -m unittest discover -s scripts -p test_autodl_http.py` and the skill-creator `quick_validate.py` against this folder. Test new paid workflows with offline fixtures unless a specific live action is authorized. Keep fixtures synthetic and distinguish static contract evidence from live server validation.
