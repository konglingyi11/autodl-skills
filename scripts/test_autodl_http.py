"""Offline contract tests. No login, network, or billed submissions."""
import argparse
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autodl_instance import AutoDLError, Client, public_row
import autodl_resources as resources

UUID = "0123456789-abcdef01"
NEW_UUID = "abcdef0123-01234567"
GIB = resources.GIB


class FakeClient:
    def __init__(self):
        self.calls = []
        self.fail_submit = False
        self.src = {"uuid": UUID, "id": 1, "status": "shutdown", "region_sign": "r1",
                    "expand_data_disk_size": 10 * GIB, "root_password": "MUST_NOT_LEAK"}
        self.machine = {"machine_id": 42, "region_sign": "r1", "gpu_idle_num": 2,
                        "on_line": 1, "max_instance_num": 10, "binding_instance_num": 2,
                        "max_data_disk_expand_size": 100 * GIB,
                        "max_instance_disk_size": 50 * GIB, "cpu_arch": "x86",
                        "chip_corp": "nvidia", "highest_cuda_version": "12.8"}
        self.img = {"uuid": "base123", "image_name": "base123", "image_type": "public",
                    "cpu_arch": "x86", "chip_corp": "nvidia", "cuda_version": "12.1"}
        self.saved = []
        self.price = {"origin_price": 1500, "discount_list": [
            {"product_type": "instance", "type": "member_discount",
             "origin_price": 1500, "total_reduction": 100},
        ], "data_disk_info": {"data_disk_deal_daily_price": 100}}
        self.order = {"uuid": "order123", "order_type": "clone_instance", "status": "pending",
                      "deal_price": 10000, "price_info": {"num": 1, "duration": 1}}

    def instances(self):
        return [copy.deepcopy(self.src)]

    def api(self, method, resource, payload=None):
        self.calls.append((method, resource, copy.deepcopy(payload)))
        if resource == "user/machine/list":
            return {"list": [copy.deepcopy(self.machine)], "result_total": 1}
        if resource == "instance/snapshot":
            return {"image": copy.deepcopy(self.img), "machine_info_snapshot": {
                "max_instance_disk_size": 50 * GIB}}
        if resource == "image/get":
            return [copy.deepcopy(self.img)]
        if resource == "image/private/get":
            return copy.deepcopy(self.saved)
        if resource == "image/private/list":
            return {"list": copy.deepcopy(self.saved), "max_page": 1}
        if resource == "image/private/check_name":
            return {"existed": False}
        if resource == "image/private/usage":
            return {"current_usage": 80 * GIB, "max_usage": 80 * GIB,
                    "free_size": 30 * GIB, "to_cost": 0.5, "token": "MUST_NOT_LEAK"}
        if resource == "order/price/preview":
            return copy.deepcopy(self.price)
        if resource == "order":
            return {"order": copy.deepcopy(self.order)}
        if self.fail_submit:
            raise AutoDLError("Simulated uncertain timeout")
        return NEW_UUID


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        self.config = {"machine_id": 42, "gpu_count": 1, "base_image_uuid": "base123"}
        self.clone_config = {"machine_id": 42, "gpu_count": 1, "copy_data_disk": True,
                             "expand_data_disk_gib": 10}

    def args(self, plan, receipt=None, execute=False):
        return argparse.Namespace(execute=execute, request_sha256=resources.digest(plan), receipt=receipt)

    def test_create_preview_never_submits_and_converts_prices(self):
        plan = resources.prepare_order(self.client, self.config)
        result = resources.submit_or_preview(self.client, plan, self.args(plan))
        self.assertFalse(result["submitted"])
        self.assertEqual(result["price"]["display_cny"]["instance"], 1.4)
        self.assertFalse(any("create/payg" in path for _, path, _ in self.client.calls))

    def test_clone_copies_explicit_data_and_snapshot_image(self):
        plan = resources.prepare_order(self.client, self.clone_config, UUID)
        self.assertEqual(plan["payload"]["instance_uuid"], UUID)
        info = plan["payload"]["instance_info"]
        self.assertTrue(info["copy_data_disk_after_clone"])
        self.assertEqual(info["base_image_uuid"], "base123")
        self.assertEqual(info["expand_data_disk"], 10 * GIB)
        self.assertNotIn("MUST_NOT_LEAK", json.dumps(plan))

    def test_clone_requires_explicit_data_choice(self):
        del self.clone_config["copy_data_disk"]
        with self.assertRaises(AutoDLError):
            resources.prepare_order(self.client, self.clone_config, UUID)

    def test_system_only_clone_allows_smaller_data_disk(self):
        self.clone_config.update(copy_data_disk=False, expand_data_disk_gib=0)
        plan = resources.prepare_order(self.client, self.clone_config, UUID)
        self.assertFalse(plan["payload"]["instance_info"]["copy_data_disk_after_clone"])

    def test_clone_rejects_other_region(self):
        self.client.machine["region_sign"] = "r2"
        with self.assertRaisesRegex(AutoDLError, "source region"):
            resources.prepare_order(self.client, self.clone_config, UUID)

    def test_clone_rejects_running_source(self):
        self.client.src["status"] = "running"
        with self.assertRaisesRegex(AutoDLError, "shut down"):
            resources.prepare_order(self.client, self.clone_config, UUID)

    def test_clone_rejects_too_small_target_disk(self):
        self.client.machine["max_instance_disk_size"] = 20 * GIB
        with self.assertRaisesRegex(AutoDLError, "smaller"):
            resources.prepare_order(self.client, self.clone_config, UUID)

    def test_no_gpu_capacity_does_not_submit(self):
        self.client.machine["gpu_idle_num"] = 0
        with self.assertRaisesRegex(AutoDLError, "free GPUs"):
            resources.prepare_order(self.client, self.config)

    def test_cuda_and_architecture_compatibility(self):
        for field, value in (("highest_cuda_version", "11.8"), ("cpu_arch", "arm")):
            client = FakeClient()
            client.machine[field] = value
            with self.subTest(field=field), self.assertRaises(AutoDLError):
                resources.prepare_order(client, self.config)

    def test_missing_compatibility_is_not_guessed(self):
        del self.client.img["cpu_arch"]
        with self.assertRaises(AutoDLError):
            resources.prepare_order(self.client, self.config)

    def test_private_image_must_be_ready(self):
        self.config.pop("base_image_uuid")
        self.config["private_image_uuid"] = "saved123"
        self.client.saved = [{**self.client.img, "image_uuid": "saved123", "status": "saving"}]
        with self.assertRaisesRegex(AutoDLError, "not ready"):
            resources.prepare_order(self.client, self.config)
        self.client.saved[0]["status"] = "finished"
        plan = resources.prepare_order(self.client, self.config)
        self.assertEqual(plan["payload"]["instance_info"]["private_image_uuid"], "saved123")

    def test_price_change_invalidates_preview(self):
        plan = resources.prepare_order(self.client, self.config)
        args = self.args(plan, "unused.json", True)
        self.client.price["discount_list"][0]["origin_price"] += 200
        changed = resources.prepare_order(self.client, self.config)
        with self.assertRaisesRegex(AutoDLError, "changed"):
            resources.submit_or_preview(self.client, changed, args)

    def test_unknown_config_and_boolean_gpu_count_rejected(self):
        for cfg in ({**self.config, "gpu_count": True}, {**self.config, "destroy_source": True}):
            with self.subTest(cfg=cfg), self.assertRaises(AutoDLError):
                resources.prepare_order(self.client, cfg)

    def test_string_host_identifier_is_preserved_in_create_preview(self):
        identifier = "fixture-host-abcdef"
        self.client.machine["machine_id"] = identifier
        plan = resources.prepare_order(self.client, {**self.config, "machine_id": identifier})
        self.assertEqual(plan["payload"]["instance_info"]["machine_id"], identifier)
        self.assertEqual(plan["payload"]["price_info"]["machine_id"], identifier)

    def test_invalid_host_identifier_is_rejected(self):
        for identifier in (True, False, 0, "", " ", None):
            with self.subTest(identifier=identifier), self.assertRaises(AutoDLError):
                resources.normalize_config({**self.config, "machine_id": identifier})

    def test_prepaid_creation_routes_to_order_endpoint(self):
        plan = resources.prepare_order(self.client, {**self.config, "charge_type": "monthly"})
        self.assertEqual(plan["resource"], "order/instance/create/prepay")

    def test_receipt_blocks_duplicate_submission(self):
        plan = resources.prepare_order(self.client, self.config)
        with tempfile.TemporaryDirectory() as folder:
            receipt = str(Path(folder) / "receipt.json")
            result = resources.submit_or_preview(self.client, plan, self.args(plan, receipt, True))
            self.assertEqual(result["state"], "ACCEPTED")
            self.assertEqual(result["result_uuid"], NEW_UUID)
            with self.assertRaises(FileExistsError):
                resources.submit_or_preview(self.client, plan, self.args(plan, receipt, True))
            self.assertEqual(sum(path.endswith("create/payg") for _, path, _ in self.client.calls), 1)

    def test_timeout_remains_unresolved_no_retry(self):
        plan = resources.prepare_order(self.client, self.config)
        self.client.fail_submit = True
        with tempfile.TemporaryDirectory() as folder:
            receipt = str(Path(folder) / "receipt.json")
            with self.assertRaisesRegex(AutoDLError, "not confirmed"):
                resources.submit_or_preview(self.client, plan, self.args(plan, receipt, True))
            self.assertEqual(json.loads(Path(receipt).read_text())["state"], "SUBMITTING")
            self.assertEqual(sum(path.endswith("create/payg") for _, path, _ in self.client.calls), 1)

    @patch.object(resources, "image_billing", return_value={"text": "official rules"})
    def test_save_image_preview_contains_billing_and_preserves_privacy(self, _mock):
        plan = resources.prepare_image(self.client, UUID, "environment")
        self.assertFalse(plan["payload"]["try_again"])
        self.assertNotIn("MUST_NOT_LEAK", json.dumps(plan))
        self.assertEqual(plan["billing_rules"]["text"], "official rules")

    def test_existing_image_name_is_not_resubmitted(self):
        self.client.saved = [{"name": "environment", "status": "saving"}]
        with self.assertRaisesRegex(AutoDLError, "already exists"):
            resources.prepare_image(self.client, UUID, "environment")

    def test_pagination_reads_later_pages(self):
        class Pages:
            def api(self, method, resource, payload):
                return {"list": [{"page": payload["page_index"]}], "max_page": 3}
        self.assertEqual(resources.paginated(Pages(), "image/private/list"),
                         [{"page": 1}, {"page": 2}, {"page": 3}])

    def test_missing_pagination_metadata_fails_closed(self):
        class Pages:
            def api(self, *args):
                return {"list": []}
        with self.assertRaisesRegex(AutoDLError, "metadata"):
            resources.paginated(Pages(), "image/private/list")

    def test_payment_uses_put_and_exact_order(self):
        plan = resources.prepare_payment(self.client, "order123")
        self.assertEqual(plan["amount_cny"], 10)
        with tempfile.TemporaryDirectory() as folder:
            result = resources.submit_or_preview(self.client, plan, self.args(plan, str(Path(folder)/"pay.json"), True))
            self.assertEqual(result["order_uuid"], "order123")
        self.assertEqual(self.client.calls[-1], ("PUT", "order/pay", {"order_uuid": "order123"}))

    def test_paid_or_unrelated_order_is_rejected(self):
        for field, value in (("status", "paid"), ("order_type", "renewal_instance")):
            client = FakeClient()
            client.order[field] = value
            with self.subTest(field=field), self.assertRaises(AutoDLError):
                resources.prepare_payment(client, "order123")

    def test_public_instance_password_is_opt_in(self):
        self.assertNotIn("MUST_NOT_LEAK", json.dumps(public_row(self.client.src)))
        self.assertEqual(public_row(self.client.src, True)["ssh_password"], "MUST_NOT_LEAK")


class TransportTests(unittest.TestCase):
    def test_get_uses_query_and_subaccount_prefix_no_redirects(self):
        class Response:
            def raise_for_status(self):
                pass
            def json(self):
                return {"code": "Success", "data": {"ok": True}}
        class Session:
            headers = {}
            def request(self, method, url, **kwargs):
                self.seen = (method, url, kwargs)
                return Response()
        session = Session()
        client = Client(session)
        self.assertEqual(client.api("GET", "instance/snapshot", {"instance_uuid": UUID}), {"ok": True})
        method, url, kwargs = session.seen
        self.assertEqual(method, "GET")
        self.assertTrue(url.endswith("/api/v1/sub_user/instance/snapshot"))
        self.assertEqual(kwargs["params"], {"instance_uuid": UUID})
        self.assertNotIn("json", kwargs)
        self.assertFalse(kwargs["allow_redirects"])
        client.account_type = "main"
        client.api("GET", "instance/snapshot", {"instance_uuid": UUID})
        self.assertTrue(session.seen[1].endswith("/api/v1/instance/snapshot"))


if __name__ == "__main__":
    unittest.main()
