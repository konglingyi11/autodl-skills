#!/usr/bin/env python3
"""复用会话与镜像预检，在预算内抢一台按量 GPU 实例。"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import sys
import time
from typing import Callable

if __name__ == "__main__":
    from autodl_update import bootstrap
    bootstrap()

from autodl_instance import AutoDLError, Client, public_row
from autodl_resources import (
    GIB, integer, list_data, paginated, prepare_order, submit_or_preview,
)


@dataclass(frozen=True)
class Reservation:
    gpu: str
    memory_gib: int
    max_hourly: float
    image: str
    name: str
    regions: tuple[str, ...] = ()
    interval: float = 2.0
    timeout: float = 3600.0


def inventory(client: Client, spec: Reservation) -> list[dict[str, object]]:
    """一次获取最多 1000 台；库存更大时继续完整分页。"""
    return paginated(client, "user/machine/list", {
        "charge_type": "payg", "region_sign": "", "region_sign_list": list(spec.regions),
        "gpu_type_name": [spec.gpu], "machine_tag_name": [], "gpu_idle_num": 1,
        "mount_net_disk": False, "cpu_arch": [], "chip_corp": [],
        "instance_disk_size_order": "", "date_range": "", "date_from": "",
        "date_to": "", "pay_price_order": "", "gpu_idle_type": "", "default_order": True,
    }, page_size=1000)


def candidates(rows: list[dict[str, object]], spec: Reservation) -> list[dict[str, object]]:
    """核对实际 GPU 名称、显存、可租状态和空闲槽位。"""
    available = [row for row in rows
                 if row.get("gpu_name") == spec.gpu
                 and row.get("on_line") is not None and row["on_line"] not in (3, 4, 5, 6)
                 and (not spec.regions or row.get("region_sign") in spec.regions)
                 and integer(row.get("gpu_memory"), "gpu_memory") >= spec.memory_gib * GIB
                 and integer(row.get("gpu_idle_num"), "gpu_idle_num") >= 1
                 and integer(row.get("binding_instance_num"), "binding_instance_num")
                 < integer(row.get("max_instance_num"), "max_instance_num")]
    return sorted(available, key=lambda row: integer(row.get("payg_price"), "payg_price"))


def preflight(client: Client, spec: Reservation) -> dict[str, object]:
    """提前固定基础镜像身份，热路径仍核对实时主机兼容性。"""
    images = list_data(client.api("POST", "image/get", {"image_uuid": spec.image}))
    matches = [image for image in images if image.get("uuid") == spec.image]
    if len(matches) != 1:
        raise AutoDLError("基础镜像不存在或不唯一")
    return matches[0]


def wait_ready(client: Client, accepted: dict[str, object], timeout: float = 300) -> dict[str, object]:
    """平台可能改写实例名称，因此按提交响应的 UUID 核验。"""
    target = accepted.get("result_uuid")
    if not target and isinstance(accepted.get("result"), dict):
        target = accepted["result"].get("instance_uuid") or accepted["result"].get("uuid")
    if not isinstance(target, str):
        raise AutoDLError("请求已接受但缺少实例 UUID；先核对回执和远端状态，不要重复提交")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        matches = [row for row in client.instances() if row.get("uuid") == target]
        if len(matches) == 1 and matches[0].get("status") == "running":
            return public_row(matches[0])
        if matches and matches[0].get("status") in ("failed", "shutdown", "released"):
            raise AutoDLError(f"实例 {target} 当前状态为 {matches[0].get('status')}；核对后再操作")
        time.sleep(2)
    raise AutoDLError(f"实例 {target} 尚未就绪；保留回执，不要重复提交")


def reserve(client: Client, spec: Reservation, receipt: Path | None, execute: bool,
            emit: Callable[[dict[str, object]], None]) -> dict[str, object]:
    """库存观测后只询价一次，复用同一计划预览和提交；最多发出一次创建请求。"""
    if execute and (receipt is None or not receipt.is_absolute() or receipt.exists()):
        raise AutoDLError("提交需要全新的绝对回执路径")
    image = preflight(client, spec)
    deadline = time.monotonic() + spec.timeout
    while time.monotonic() < deadline:
        poll_started = time.monotonic()
        for row in candidates(inventory(client, spec), spec):
            observed_at = time.monotonic()
            plan = prepare_order(client, {
                "machine_id": row["machine_id"], "gpu_count": 1, "charge_type": "payg",
                "duration": 1, "expand_data_disk_gib": 0, "base_image_uuid": spec.image,
                "instance_name": spec.name,
            }, observed_machine=row, observed_base_image=image)
            price = plan["price"]["display_cny"]["instance"]
            if type(price) not in (int, float) or not math.isfinite(price):
                raise AutoDLError("无法核对实际小时费用")
            if price > spec.max_hourly:
                continue
            preview = submit_or_preview(client, plan, argparse.Namespace(execute=False))
            emit({"event": "preview", **preview})
            if not execute:
                return preview
            if time.monotonic() >= deadline:
                break
            # 同一份刚核验的观测和报价直接提交，由服务端原子裁决库存竞争。
            accepted = submit_or_preview(client, plan, argparse.Namespace(
                execute=True, request_sha256=preview["request_sha256"], receipt=str(receipt)))
            emit({"event": "accepted", "cny_per_hour": price,
                  "observation_to_accept_seconds": time.monotonic() - observed_at, **accepted})
            return {"event": "ready", "cny_per_hour": price,
                    "instance": wait_ready(client, accepted), "receipt": str(receipt)}
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(remaining, max(0, spec.interval - (time.monotonic() - poll_started))))
    return {"event": "timeout", "submitted": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--auth", choices=("sub", "wechat", "sms"), default="sub")
    parser.add_argument("--gpu", required=True)
    parser.add_argument("--memory-gib", type=int, default=32)
    parser.add_argument("--max-hourly", type=float, required=True)
    parser.add_argument("--image", required=True, help="已查询的基础镜像 UUID")
    parser.add_argument("--name", default="gpu-reservation")
    parser.add_argument("--region", action="append", default=[])
    parser.add_argument("--interval", type=float, default=2)
    parser.add_argument("--timeout", type=float, default=3600)
    parser.add_argument("--execute", action="store_true", help="在给定预算和范围内自动提交一次")
    parser.add_argument("--receipt", type=Path, help="执行时必须提供全新的绝对回执路径")
    args = parser.parse_args(argv)
    for key in ("interval", "timeout", "max_hourly"):
        if not math.isfinite(getattr(args, key)) or getattr(args, key) <= 0:
            parser.error(f"--{key.replace('_', '-')} 必须为有限正数")
    if args.interval < 1 or args.memory_gib < 1 or len(args.name) > 32:
        parser.error("轮询间隔至少 1 秒，显存必须为正数，名称最长 32 字符")
    if args.execute and (not args.receipt or not args.receipt.is_absolute()):
        parser.error("--execute 需要新的绝对 --receipt 路径")
    client = Client()
    lock: Path | None = None
    try:
        if args.execute:
            if args.receipt.exists():
                raise AutoDLError("回执已存在；先核对远端状态，不要覆盖或重复提交")
            candidate_lock = args.receipt.with_suffix(args.receipt.suffix + ".lock")
            with candidate_lock.open("x", encoding="utf-8") as stream:
                stream.write(str(os.getpid()))
            lock = candidate_lock
        client.login(args.auth)
        spec = Reservation(args.gpu, args.memory_gib, args.max_hourly, args.image,
                           args.name, tuple(args.region), args.interval, args.timeout)
        emit = lambda value: print(json.dumps(value, ensure_ascii=False), flush=True)
        emit({"event": "watching", "gpu": spec.gpu, "memory_gib": spec.memory_gib,
              "max_hourly": spec.max_hourly, "interval": spec.interval, "execute": args.execute})
        result = reserve(client, spec, args.receipt, args.execute, emit)
        emit(result)
        return 2 if result.get("event") == "timeout" else 0
    except KeyboardInterrupt:
        print("AutoDL: 已取消；若已有回执，请先核对提交结果", file=sys.stderr)
        return 130
    except (AutoDLError, OSError, ValueError) as error:
        print(f"AutoDL: {error}", file=sys.stderr)
        return 1
    finally:
        client.close()
        if lock is not None:
            lock.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
