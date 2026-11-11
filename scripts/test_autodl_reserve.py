"""抢卡的付费边界、低延迟路径与按 UUID 完成核验。"""

import copy
import json
from pathlib import Path
import tempfile
import unittest

from autodl_instance import AutoDLError
from autodl_reserve import Reservation, candidates, reserve
from autodl_resources import GIB, prepare_order
from test_autodl_http import FakeClient, NEW_UUID


class ReservationClient(FakeClient):
    def __init__(self):
        super().__init__()
        self.created = False
        self.machine.update(gpu_name="RTX 5090", gpu_memory=32 * GIB, payg_price=1500)

    def api(self, method, resource, payload=None):
        result = super().api(method, resource, payload)
        if resource == "order/instance/create/payg":
            self.created = True
        return result

    def instances(self):
        rows = super().instances()
        if self.created:
            rows.append({"uuid": NEW_UUID, "status": "running", "name": "平台改写的名称",
                         "root_password": "MUST_NOT_LEAK"})
        return rows


class ReservationTests(unittest.TestCase):
    def setUp(self):
        self.client = ReservationClient()
        self.spec = Reservation("RTX 5090", 32, 2.88, "base123", "test", timeout=2)

    def submissions(self):
        return [call for call in self.client.calls if call[1] == "order/instance/create/payg"]

    def test_hot_path_checks_image_and_price_once_then_verifies_actual_uuid(self):
        emitted = []
        with tempfile.TemporaryDirectory() as directory:
            result = reserve(self.client, self.spec, Path(directory) / "receipt.json", True, emitted.append)
        self.assertEqual(result["instance"]["uuid"], NEW_UUID)
        self.assertEqual(result["instance"]["name"], "平台改写的名称")
        paths = [call[1] for call in self.client.calls]
        self.assertEqual(paths.count("image/get"), 1)
        self.assertEqual(paths.count("user/machine/list"), 1)
        self.assertEqual(paths.count("order/price/preview"), 1)
        self.assertEqual(len(self.submissions()), 1)
        inventory_call = next(call for call in self.client.calls if call[1] == "user/machine/list")
        self.assertEqual(inventory_call[2]["page_size"], 1000)
        self.assertNotIn("MUST_NOT_LEAK", json.dumps([result, emitted]))

    def test_over_budget_never_creates(self):
        spec = Reservation("RTX 5090", 32, 1.0, "base123", "test", timeout=0.01)
        with tempfile.TemporaryDirectory() as directory:
            receipt = Path(directory) / "receipt.json"
            result = reserve(self.client, spec, receipt, True, lambda value: None)
            self.assertFalse(receipt.exists())
        self.assertEqual(result["event"], "timeout")
        self.assertFalse(self.submissions())

    def test_preview_never_creates(self):
        result = reserve(self.client, self.spec, None, False, lambda value: None)
        self.assertFalse(result["submitted"])
        self.assertFalse(self.submissions())

    def test_unavailable_wrong_gpu_and_insufficient_memory_are_filtered(self):
        bad = [{**self.client.machine, **change} for change in (
            {"on_line": 3}, {"gpu_idle_num": 0}, {"gpu_memory": 24 * GIB},
            {"gpu_name": "RTX 5090 D"}, {"binding_instance_num": 10},
        )]
        self.assertEqual(candidates(bad, self.spec), [])

    def test_observations_do_not_bypass_identity_or_compatibility_checks(self):
        cfg = {"machine_id": 42, "base_image_uuid": "base123"}
        cases = (({**self.client.machine, "machine_id": 43}, self.client.img),
                 ({**self.client.machine, "gpu_idle_num": 0}, self.client.img),
                 (self.client.machine, {**self.client.img, "uuid": "different"}),
                 (self.client.machine, {**self.client.img, "cuda_version": "99.0"}))
        for machine, image in cases:
            with self.subTest(machine=machine, image=image), self.assertRaises(AutoDLError):
                prepare_order(self.client, cfg, observed_machine=copy.deepcopy(machine),
                              observed_base_image=copy.deepcopy(image))
        self.assertFalse(self.submissions())

    def test_uncertain_submission_stops_without_retry_and_receipt_blocks_restart(self):
        self.client.fail_submit = True
        with tempfile.TemporaryDirectory() as directory:
            receipt = Path(directory) / "receipt.json"
            with self.assertRaisesRegex(AutoDLError, "Submission not confirmed"):
                reserve(self.client, self.spec, receipt, True, lambda value: None)
            self.assertEqual(json.loads(receipt.read_text())["state"], "SUBMITTING")
            self.client.fail_submit = False
            with self.assertRaises(AutoDLError):
                reserve(self.client, self.spec, receipt, True, lambda value: None)
        self.assertEqual(len(self.submissions()), 1)


if __name__ == "__main__":
    unittest.main()
