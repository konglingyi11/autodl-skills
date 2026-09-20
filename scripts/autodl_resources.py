"""HTTP resource queries and preview-first creation. See references/http-api.md."""

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

from autodl_instance import AutoDLError, BASE, MAX_PAGES, PAGE_SIZE, UUID_RE, public_row, requests

GIB = 1024 ** 3
BILLING = ("payg", "daily", "weekly", "monthly")
IMAGE_FIELDS = (
    "image_uuid", "uuid", "name", "image_size", "status", "created_at",
    "instance_uuid", "image_source", "storage_oss_sign", "framework", "python_v",
    "cuda_v", "cpu_arch", "chip_corp", "share_image_type", "framework_name",
    "framework_version", "python_version", "cuda_version", "image_type",
)
MACHINE_FIELDS = (
    "machine_id", "machine_alias", "region_sign", "region_name", "gpu_name",
    "gpu_memory", "gpu_idle_num", "gpu_number", "cpu_per_gpu", "mem_per_gpu",
    "cpu_arch", "chip_corp", "highest_cuda_version", "driver_version",
    "max_instance_disk_size", "max_data_disk_expand_size", "on_line",
    "max_instance_num", "binding_instance_num",
)
SOURCE_FIELDS = (
    "region_sign", "machine_id", "expand_data_disk_size", "private_image_uuid",
    "reproduction_uuid", "reproduction_id",
)


def select(row, fields):
    if not isinstance(row, dict):
        raise AutoDLError("Expected an object; response schema changed")
    return {key: row[key] for key in fields if key in row}


def object_data(data):
    if not isinstance(data, dict):
        raise AutoDLError("Expected an object; response schema changed")
    return data


def list_data(data):
    if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
        raise AutoDLError("Expected a list of objects; response schema changed")
    return data


def integer(value, label, minimum=0):
    if type(value) is not int or value < minimum:
        raise AutoDLError(f"{label} must be an integer >= {minimum}")
    return value


def paginated(client, resource, filters=None):
    rows = []
    for page in range(1, MAX_PAGES + 1):
        data = object_data(client.api("POST", resource, {
            **(filters or {}), "page_index": page, "page_size": PAGE_SIZE,
        }))
        batch = list_data(data.get("list"))
        rows.extend(batch)
        if "max_page" in data:
            if page >= integer(data["max_page"], "max_page"):
                return rows
        elif "result_total" in data or "total" in data:
            total = integer(data.get("result_total", data.get("total")), "total")
            if len(rows) >= total:
                return rows
        else:
            raise AutoDLError("Pagination metadata missing; refusing a partial list")
        if not batch:
            raise AutoDLError("Empty page before advertised end of list")
    raise AutoDLError("List exceeds page limit")


def images(client):
    return paginated(client, "image/private/list", {
        "order_uuid": "", "date_from": "", "date_to": "",
    })


def usage(client):
    data = object_data(client.api("POST", "image/private/usage"))
    for field in ("current_usage", "free_size", "max_usage", "to_cost"):
        if field not in data:
            raise AutoDLError("Image usage response changed")
    return select(data, ("current_usage", "free_size", "max_usage", "to_cost"))


def image_billing():
    """Read the image section of official pricing documentation using unauthenticated HTTP."""
    url = BASE + "/docs/price/"
    try:
        response = requests.get(url, impersonate="chrome", timeout=30, allow_redirects=False)
        response.raise_for_status()
    except requests.RequestsError as exc:
        raise AutoDLError("Could not read official image billing rules") from exc
    match = re.search(r'<h2\b[^>]*>镜像计费.*?(?=<h2\b|\Z)', response.text, re.S)
    if not match:
        raise AutoDLError("Official pricing document structure changed")
    class Text(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts = []

        def handle_data(self, data):
            if data.strip() and data.strip() != "¶":
                self.parts.append(data.strip())
    parser = Text()
    parser.feed(match.group())
    return {"source": url, "text": "\n".join(parser.parts)}


def machines(client, region="", gpu="", machine_id=None, charge_type="payg"):
    filters = {
        "charge_type": charge_type, "region_sign": "",
        "region_sign_list": [region] if region else [],
        "gpu_type_name": [gpu] if gpu else [], "machine_tag_name": [],
        "gpu_idle_num": 0, "mount_net_disk": False, "cpu_arch": [], "chip_corp": [],
        "instance_disk_size_order": "", "date_range": "", "date_from": "",
        "date_to": "", "pay_price_order": "", "gpu_idle_type": "", "default_order": True,
    }
    if machine_id is not None:
        filters["machine_id"] = machine_id
    return paginated(client, "user/machine/list", filters)


def source_instance(client, uuid):
    if not UUID_RE.fullmatch(uuid):
        raise AutoDLError("Supply an exact instance UUID")
    rows = [row for row in client.instances() if row.get("uuid") == uuid]
    if len(rows) != 1:
        raise AutoDLError("Source instance is not uniquely visible to this sub-account")
    return rows[0]


def snapshot(client, uuid):
    return object_data(client.api("GET", "instance/snapshot", {"instance_uuid": uuid}))


def compatible(image, machine):
    for field in ("cpu_arch", "chip_corp"):
        if not image.get(field) or not machine.get(field):
            raise AutoDLError(f"Cannot establish image/host {field} compatibility")
        if field == "chip_corp" and machine[field] == "cpu":
            continue
        if image[field] != machine[field]:
            raise AutoDLError(f"Image and host {field} differ")
    if machine["chip_corp"] == "nvidia":
        required = image.get("cuda_version", image.get("cuda_v"))
        available = machine.get("highest_cuda_version")
        def version(value):
            if not isinstance(value, str) or not value or not all(
                part.isdigit() for part in value.split(".")
            ):
                raise AutoDLError("Cannot establish image/host CUDA compatibility")
            parts = tuple(int(part) for part in value.split("."))
            return (parts + (0, 0, 0))[:3]
        if version(required) > version(available):
            raise AutoDLError("Image CUDA requirement exceeds host capability")


def normalize_config(config, clone=False):
    object_data(config)
    allowed = {"machine_id", "gpu_count", "charge_type", "duration", "expand_data_disk_gib",
               "instance_name", "base_image_uuid", "private_image_uuid", "copy_data_disk", "ft_sparse"}
    if set(config) - allowed:
        raise AutoDLError("Unknown configuration fields")
    result = {
        "machine_id": integer(config.get("machine_id"), "machine_id", 1),
        "gpu_count": integer(config.get("gpu_count", 1), "gpu_count", 1),
        "charge_type": config.get("charge_type", "payg"),
        "duration": integer(config.get("duration", 1), "duration", 1),
        "expand_data_disk_gib": integer(config.get("expand_data_disk_gib", 0), "expand_data_disk_gib"),
        "instance_name": config.get("instance_name", ""),
    }
    if result["charge_type"] not in BILLING:
        raise AutoDLError("Unsupported billing mode")
    if result["charge_type"] == "payg" and result["duration"] != 1:
        raise AutoDLError("payg duration must be 1; it is not an automatic shutdown timer")
    if not isinstance(result["instance_name"], str) or len(result["instance_name"]) > 32:
        raise AutoDLError("instance_name must be a string of at most 32 characters")
    if clone:
        if any(config.get(key) for key in ("base_image_uuid", "private_image_uuid")):
            raise AutoDLError("Clone image comes from the source snapshot")
        if type(config.get("copy_data_disk")) is not bool:
            raise AutoDLError("Clone config must explicitly set copy_data_disk true or false")
        if type(config.get("ft_sparse", False)) is not bool:
            raise AutoDLError("ft_sparse must be boolean")
        result.update(copy_data_disk=config["copy_data_disk"], ft_sparse=config.get("ft_sparse", False))
    else:
        if "copy_data_disk" in config or "ft_sparse" in config:
            raise AutoDLError("Clone-only options supplied for create")
        for key in ("base_image_uuid", "private_image_uuid"):
            result[key] = config.get(key, "")
            if not isinstance(result[key], str):
                raise AutoDLError("Image UUID must be a string")
        if sum(bool(result[key]) for key in ("base_image_uuid", "private_image_uuid")) != 1:
            raise AutoDLError("Select exactly one base_image_uuid or private_image_uuid")
    return result


def safe_price(data):
    data = object_data(data)
    result = select(data, ("origin_price", "duration", "charge_type"))
    if not isinstance(data.get("origin_price"), (int, float)):
        raise AutoDLError("Price origin_price missing")
    result["discount_list"] = [select(item, (
        "product_type", "type", "origin_price", "total_reduction", "discount_rate",
    )) for item in list_data(data.get("discount_list"))]
    if "data_disk_info" in data:
        result["data_disk_info"] = select(data["data_disk_info"], ("data_disk_deal_daily_price",))
    # The frontend replaces each product's final price as it traverses discount_list.
    result["display_cny"] = {"instance": None, "expanded_disk": None}
    for item in result["discount_list"]:
        if item.get("product_type") in ("instance", "data_disk"):
            origin, reduction = item.get("origin_price"), item.get("total_reduction")
            if not all(isinstance(value, (int, float)) for value in (origin, reduction)):
                raise AutoDLError("Price discount amounts changed")
            key = "instance" if item["product_type"] == "instance" else "expanded_disk"
            result["display_cny"][key] = (origin - reduction) / 1000
    return result


def prepare_order(client, config, source_uuid=None):
    cfg = normalize_config(config, clone=source_uuid is not None)
    rows = [row for row in machines(client, machine_id=cfg["machine_id"],
                                    charge_type=cfg["charge_type"])
            if row.get("machine_id") == cfg["machine_id"]]
    if len(rows) != 1:
        raise AutoDLError("Target host is not uniquely available")
    machine = rows[0]
    if machine.get("on_line") is None or machine["on_line"] in (3, 4, 5, 6):
        raise AutoDLError("Target host unavailable")
    if integer(machine.get("gpu_idle_num"), "gpu_idle_num") < cfg["gpu_count"]:
        raise AutoDLError("Target host has insufficient free GPUs")
    if integer(machine.get("max_instance_num"), "max_instance_num") <= integer(
        machine.get("binding_instance_num"), "binding_instance_num"
    ):
        raise AutoDLError("Target host has no instance slots")
    expand = cfg["expand_data_disk_gib"] * GIB
    if expand > integer(machine.get("max_data_disk_expand_size"), "max_data_disk_expand_size"):
        raise AutoDLError("Requested expanded disk exceeds host capacity")
    info = {
        "machine_id": cfg["machine_id"], "charge_type": cfg["charge_type"],
        "req_gpu_amount": cfg["gpu_count"], "instance_name": cfg["instance_name"],
        "expand_data_disk": expand,
    }
    context = {"host": select(machine, MACHINE_FIELDS)}
    if source_uuid:
        src = source_instance(client, source_uuid)
        if src.get("status") != "shutdown":
            raise AutoDLError("Quiesce and shut down the source before cloning")
        if not src.get("region_sign") or src["region_sign"] != machine.get("region_sign"):
            raise AutoDLError("Cloning is restricted to the source region")
        snap = snapshot(client, source_uuid)
        img = object_data(snap.get("image"))
        compatible(img, machine)
        source_expand = integer(src.get("expand_data_disk_size"), "source expand_data_disk_size")
        if cfg["copy_data_disk"] and expand < source_expand:
            raise AutoDLError("Keep at least the source expanded disk capacity when copying data")
        if cfg["copy_data_disk"]:
            source_disk = integer(object_data(snap.get("machine_info_snapshot")).get(
                "max_instance_disk_size"), "source max_instance_disk_size")
            target_disk = integer(machine.get("max_instance_disk_size"), "max_instance_disk_size")
            if target_disk + expand < source_disk + source_expand:
                raise AutoDLError("Target data disk is smaller than source allocation")
        info.update({
            "base_image_uuid": img.get("image_name", "") if img.get("image_type") == "public" else "",
            "private_image_uuid": src.get("private_image_uuid", ""),
            "reproduction_uuid": src.get("reproduction_uuid", ""),
            "reproduction_id": src.get("reproduction_id", 0),
            "cg_application_uuid": snap.get("cg_application_uuid", ""),
            "cg_application_info": select(snap.get("cg_application_info") or {},
                ("app_name", "image_name", "current_version_id", "current_version", "image_id")),
            "copy_data_disk_after_clone": cfg["copy_data_disk"],
            "keep_src_user_service_address_after_clone": False,
            "ft_sparse": cfg["ft_sparse"],
        })
        context["source"] = {**public_row(src), **select(src, SOURCE_FIELDS)}
        context["source_disk"] = select(object_data(snap.get("machine_info_snapshot")),
                                        ("max_instance_disk_size",))
        context["image"] = select(img, IMAGE_FIELDS)
    else:
        if cfg["private_image_uuid"]:
            pool = list_data(client.api("POST", "image/private/get", {
                "cpu_arch": [machine["cpu_arch"]],
                "chip_corp": [] if machine["chip_corp"] == "cpu" else [machine["chip_corp"]],
            }))
            matches = [row for row in pool if row.get("image_uuid") == cfg["private_image_uuid"]]
        else:
            pool = list_data(client.api("POST", "image/get", {"image_uuid": cfg["base_image_uuid"]}))
            matches = [row for row in pool if row.get("uuid") == cfg["base_image_uuid"]]
        if len(matches) != 1:
            raise AutoDLError("Selected image is not uniquely available to this sub-account")
        img = matches[0]
        if cfg["private_image_uuid"]:
            saved = [row for row in images(client) if row.get("image_uuid") == cfg["private_image_uuid"]]
            if len(saved) != 1 or saved[0].get("status") != "finished":
                raise AutoDLError("Private image is not ready or not uniquely visible")
        compatible(img, machine)
        info.update({
            "base_image_uuid": cfg["base_image_uuid"], "private_image_uuid": cfg["private_image_uuid"],
            "reproduction_uuid": "", "reproduction_id": 0, "cg_application_uuid": "",
            "cg_application_info": {"image_name": "", "current_version_id": 0,
                                    "current_version": "", "image_id": 0},
        })
        context["image"] = select(img, IMAGE_FIELDS)
    price_info = {"machine_id": cfg["machine_id"], "charge_type": cfg["charge_type"],
                  "duration": cfg["duration"], "num": cfg["gpu_count"],
                  "expand_data_disk": expand, "coupon_id_list": []}
    price = safe_price(client.api("POST", "order/price/preview", price_info))
    payload = {"instance_info": info, "price_info": price_info}
    if source_uuid:
        payload["instance_uuid"] = source_uuid
    action = "clone" if source_uuid else "create"
    endpoint = f"order/instance/{action}/{'payg' if cfg['charge_type'] == 'payg' else 'prepay'}"
    return {"action": action, "resource": endpoint, "payload": payload, "context": context,
            "price": price, "price_unit": "API amounts / 1000 = CNY; payg instance/hour; prepay instance/selected term. data_disk_deal_daily_price / 1000 = recurring expanded disk CNY/day, including shutdown.",
            "note": "Preview only. payg duration is not an auto-stop. Prepay returns an unpaid order; payment is a separate operation."}


def prepare_image(client, uuid, name):
    if not name or len(name) > 32:
        raise AutoDLError("Image name must contain 1 to 32 characters")
    src = source_instance(client, uuid)
    if src.get("status") != "shutdown":
        raise AutoDLError("Source must be shut down before saving its image")
    existing = images(client)
    if any(row.get("name") == name for row in existing):
        raise AutoDLError("Image name already exists; do not retry a pending save")
    check = object_data(client.api("POST", "image/private/check_name", {
        "image_id": src.get("id"), "image_name": name,
    }))
    if type(check.get("existed")) is not bool:
        raise AutoDLError("Image name check response changed")
    if check["existed"]:
        raise AutoDLError("Image name already exists")
    return {"action": "save-image", "resource": "instance/image/save",
            "payload": {"image_name": name, "instance_uuid": uuid, "try_again": False},
            "context": {"source": public_row(src)}, "current_image_usage": usage(client),
            "billing_rules": image_billing(),
            "note": "System disk only. New image size and resulting storage bill are unknown until saved. Read current image billing rules before submitting."}


def order_details(client, uuid):
    data = object_data(client.api("GET", "order", {"order_uuid": uuid}))
    row = object_data(data.get("order"))
    return {**select(row, ("uuid", "order_type", "status", "created_at", "deal_price",
                          "charge_type", "product_uuid", "machine_id")),
            "price_info": select(object_data(row.get("price_info")),
                                 ("duration", "num", "expand_data_disk"))}


def prepare_payment(client, uuid):
    order = order_details(client, uuid)
    if order.get("uuid") != uuid or order.get("order_type") not in ("create_instance", "clone_instance"):
        raise AutoDLError("Only the exact create/clone order may be paid by this command")
    if not order.get("status") or order["status"] in ("paid", "timeout", "cancelled", "closed"):
        raise AutoDLError("Order is already paid, closed, or its status is unavailable")
    if not isinstance(order.get("deal_price"), (int, float)) or order["deal_price"] < 0:
        raise AutoDLError("Order price is unavailable")
    return {"action": "pay-order", "resource": "order/pay", "method": "PUT",
            "payload": {"order_uuid": uuid}, "order": order,
            "amount_cny": order["deal_price"] / 1000,
            "note": "Preview only. Payment creates the prepaid instance. Review this exact order and amount."}


def digest(plan):
    # Host inventory fluctuates; bind the request, image/source identity, and price,
    # while rechecking actual availability every time prepare_order is called.
    stable = {key: value for key, value in plan.items() if key != "context"}
    context = plan.get("context", {})
    stable["image"] = context.get("image")
    stable["source"] = context.get("source")
    stable["source_disk"] = context.get("source_disk")
    stable["host"] = {key: value for key, value in context.get("host", {}).items()
                      if key not in ("gpu_idle_num", "binding_instance_num")}
    return hashlib.sha256(json.dumps(stable, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def submit_or_preview(client, plan, args):
    fingerprint = digest(plan)
    if not args.execute:
        return {**plan, "request_sha256": fingerprint, "submitted": False}
    if args.request_sha256 != fingerprint:
        raise AutoDLError("Preview fingerprint missing or changed; review a fresh preview")
    if not args.receipt:
        raise AutoDLError("--receipt is required for submission")
    before = ([row.get("image_uuid") for row in images(client)] if plan["action"] == "save-image"
              else [row.get("uuid") for row in client.instances()])
    receipt = Path(args.receipt)
    record = {"action": plan["action"], "request_sha256": fingerprint,
              "source_uuid": plan["payload"].get("instance_uuid"),
              "order_uuid": plan["payload"].get("order_uuid"),
              "target": select(plan["payload"].get("instance_info", {}),
                               ("machine_id", "instance_name", "charge_type", "req_gpu_amount",
                                "expand_data_disk", "base_image_uuid", "private_image_uuid",
                                "copy_data_disk_after_clone")),
              "image_name": plan["payload"].get("image_name"), "previous_uuids": before,
              "created_at": datetime.now(timezone.utc).isoformat(), "state": "SUBMITTING"}
    # Exclusive creation prevents reuse of the same receipt, including after a timeout.
    with receipt.open("x", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        result = client.api(plan.get("method", "POST"), plan["resource"], plan["payload"])
    except AutoDLError:
        # SUBMITTING is deliberately unresolved. Reconcile with the list before any new attempt.
        raise AutoDLError(f"Submission not confirmed. Inspect receipt {receipt} and reconcile remote state before retrying") from None
    record["state"] = "ACCEPTED"
    if isinstance(result, str) and UUID_RE.fullmatch(result):
        record["result_uuid"] = result
    elif isinstance(result, dict):
        record["result"] = select(result, ("uuid", "instance_uuid", "image_uuid", "order_uuid", "status"))
    receipt.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**record, "receipt": str(receipt.resolve()),
            "note": "Accepted, not verified ready. Query list/images/order to establish final state."}


def add_submit_options(parser):
    parser.add_argument("--execute", action="store_true", help="Submit an already reviewed preview once")
    parser.add_argument("--request-sha256", help="Fingerprint returned by the preview")
    parser.add_argument("--receipt", help="New local receipt filename; never reuse after uncertain submission")


def add_commands(sub):
    sub.add_parser("images", help="List all visible private/shared images and storage usage")
    sub.add_parser("image-usage", help="Read current image capacity and estimated daily bill")
    sub.add_parser("image-billing", help="Read official image storage rules through public HTTP")
    sub.add_parser("regions", help="List region identifiers")
    machine = sub.add_parser("machines", help="List hosts, available GPUs, and disk limits")
    machine.add_argument("--region", default="")
    machine.add_argument("--gpu", default="")
    machine.add_argument("--machine-id", type=int)
    machine.add_argument("--charge-type", choices=BILLING, default="payg")
    base = sub.add_parser("base-image", help="Read a specified base image")
    base.add_argument("uuid")
    detail = sub.add_parser("detail", help="Read source configuration and image snapshot")
    detail.add_argument("uuid")
    order = sub.add_parser("order", help="Read an order; does not pay it")
    order.add_argument("uuid")
    payment = sub.add_parser("pay-order", help="Preview payment for an exact create/clone order")
    payment.add_argument("uuid")
    add_submit_options(payment)
    image = sub.add_parser("save-image", help="Preview saving a private system-disk image")
    image.add_argument("uuid")
    image.add_argument("--name", required=True)
    add_submit_options(image)
    for action in ("create", "clone"):
        command = sub.add_parser(action, help=f"Preview {action} from a configuration JSON file")
        if action == "clone":
            command.add_argument("uuid")
        command.add_argument("--config", required=True)
        add_submit_options(command)


def dispatch(client, args):
    command = args.command
    if command == "images":
        rows = images(client)
        return {"total": len(rows), "images": [select(row, IMAGE_FIELDS) for row in rows],
                "storage": usage(client), "scope": "Images visible to the authenticated sub-account"}
    if command == "image-usage":
        return usage(client)
    if command == "regions":
        data = client.api("GET", "region/list")
        return {"regions": [select(row, ("region_sign", "region_name", "sign", "name"))
                            for row in list_data(data)]}
    if command == "machines":
        rows = machines(client, args.region, args.gpu, args.machine_id, args.charge_type)
        return {"total": len(rows), "machines": [select(row, MACHINE_FIELDS) for row in rows]}
    if command == "base-image":
        return {"images": [select(row, IMAGE_FIELDS) for row in list_data(
            client.api("POST", "image/get", {"image_uuid": args.uuid}))]}
    if command == "detail":
        src = source_instance(client, args.uuid)
        snap = snapshot(client, args.uuid)
        return {"instance": {**public_row(src), **select(src, SOURCE_FIELDS)},
                "image": select(object_data(snap.get("image")), IMAGE_FIELDS)}
    if command == "order":
        return order_details(client, args.uuid)
    if command == "pay-order":
        return submit_or_preview(client, prepare_payment(client, args.uuid), args)
    if command == "save-image":
        return submit_or_preview(client, prepare_image(client, args.uuid, args.name), args)
    config = json.loads(Path(args.config).read_text(encoding="utf-8-sig"))
    plan = prepare_order(client, config, args.uuid if command == "clone" else None)
    return submit_or_preview(client, plan, args)
