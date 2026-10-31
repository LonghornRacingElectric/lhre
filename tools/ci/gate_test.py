import unittest

from tools.ci import gate

ALL_AREAS = ("bazel", "sim", "car", "autonomy", "bevo", "docs")


def needs(changed=(), results=None, changes_result="success"):
    outputs = {a: "true" if a in changed else "false" for a in ALL_AREAS}
    n = {"changes": {"result": changes_result, "outputs": outputs}}
    for job, (_, area) in gate.JOBS.items():
        ran = area is None or area in changed
        n[job] = {"result": "success" if ran else "skipped", "outputs": {}}
    for job, result in (results or {}).items():
        n[job] = {"result": result, "outputs": {}}
    return n


class GateTest(unittest.TestCase):
    def assertPasses(self, n):
        self.assertEqual(gate.check(n)[1], [])

    def assertFails(self, n, text):
        errors = gate.check(n)[1]
        self.assertTrue(any(text in e for e in errors), errors)

    def test_untouched_areas_may_skip(self):
        self.assertPasses(needs(changed=("bazel",)))

    def test_failure_fails(self):
        self.assertFails(needs(changed=("bazel",), results={"bazel": "failure"}),
                         "Firmware · Bazel (Linux): failure")

    def test_cancelled_fails(self):
        self.assertFails(needs(changed=("sim",), results={"sim-fast": "cancelled"}),
                         "cancelled")

    def test_skip_of_a_changed_area_fails(self):
        self.assertFails(needs(changed=("docs",), results={"docs": "skipped"}),
                         "Docs · build: skipped, but it had to run")

    def test_format_always_has_to_run(self):
        self.assertFails(needs(results={"format": "skipped"}), "Format")

    def test_job_that_ran_anyway_counts(self):
        self.assertFails(needs(results={"bevo": "failure"}), "BEVO")

    def test_failed_detection_fails_even_if_jobs_pass(self):
        n = needs(changed=ALL_AREAS, changes_result="failure")
        self.assertFails(n, "Changes: failure")

    def test_failed_detection_forbids_skips(self):
        n = needs(changes_result="failure")
        n["changes"]["outputs"] = {a: "false" for a in ALL_AREAS}
        self.assertFails(n, "Docs · build: skipped, but it had to run")

    def test_missing_job_fails(self):
        n = needs(changed=("bazel",))
        del n["bazel"]
        self.assertFails(n, "missing")

    def test_unknown_job_must_pass(self):
        n = needs()
        n["telemetry"] = {"result": "skipped", "outputs": {}}
        self.assertFails(n, "telemetry")


if __name__ == "__main__":
    unittest.main()
