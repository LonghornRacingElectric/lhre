"""Pins what the agent docs check accepts and what it fails."""

import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from tools.lint import agent_docs

MAKEFILE = """\
.PHONY: test car
test: smoke unit
smoke:
car:
records records-undo:
VAR := value
other = a:b
"""

SKILL_HEAD = "---\nname: edit-vehicle\ndescription: Change a vehicle number.\n---\n"


class RepoTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.write("simulation/Makefile", MAKEFILE)
        self.write("tools/spec/BUILD.bazel", 'py_binary(\n    name = "fmt",\n)\n')
        self.write("drivers/lhal/BUILD.bazel", "")

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")
        return Path(rel)

    def errors(self, rel, text):
        return [m for _, m in agent_docs.check_doc(self.root, self.write(rel, text))]


class StepsTest(unittest.TestCase):
    def check(self, text):
        return [m for _, m in agent_docs.check_steps(textwrap.dedent(text))]

    def test_steps_in_order_pass(self):
        self.assertEqual(self.check("1. a\n2. b\n   more\n3. c\n"), [])

    def test_a_bad_merge_that_repeats_steps_fails(self):
        # simulation/docs/skills/edit-vehicle after #142 and #145 merged.
        text = "4. records\n5. stale\n6. autonomy\n4. car\n5. autonomy\n7. PR\n"
        self.assertEqual(self.check(text), ["step 4 follows step 6", "step 7 follows step 5"])

    def test_a_skipped_step_fails(self):
        self.assertEqual(self.check("1. a\n3. c\n"), ["step 3 follows step 1"])

    def test_a_heading_or_paragraph_starts_a_new_list(self):
        self.assertEqual(self.check("1. a\n2. b\n## Next\n4. d\n"), [])
        self.assertEqual(self.check("1. a\n2. b\n\nText.\n\n4. d\n"), [])

    def test_a_list_that_restarts_at_one_passes(self):
        self.assertEqual(self.check("1. a\n2. b\n1. a\n"), [])

    def test_nested_steps_count_on_their_own(self):
        text = "1. a\n   1. x\n   2. y\n2. b\n   1. x\n   3. z\n"
        self.assertEqual(self.check(text), ["step 3 follows step 1"])

    def test_numbers_in_code_fences_are_not_steps(self):
        self.assertEqual(self.check("1. a\n   ```\n   5. not a step\n   ```\n2. b\n"), [])


class MakeTest(RepoTest):
    def test_known_targets_pass(self):
        text = "Run `make test` and `make car T=<target> VEHICLE=x`.\n\n```bash\nmake records\nmake records-undo && make smoke\n```\n"
        self.assertEqual(self.errors("simulation/docs/skills/a/AGENTS.md", text), [])

    def test_a_renamed_target_fails(self):
        self.assertEqual(
            self.errors("simulation/AGENTS.md", "Run `make bobsim T=help`.\n"),
            ["make target `bobsim` is not in simulation/Makefile"],
        )

    def test_every_target_in_one_command_is_checked(self):
        self.assertEqual(
            self.errors("simulation/AGENTS.md", "```\nmake build test\n```\n"),
            ["make target `build` is not in simulation/Makefile"],
        )

    def test_dash_c_reads_that_directory_from_the_repo_root(self):
        self.assertEqual(self.errors("AGENTS.md", "`make -C simulation smoke`\n"), [])
        self.assertEqual(
            self.errors("AGENTS.md", "`make -C simulation nope`\n"),
            ["make target `nope` is not in simulation/Makefile"],
        )

    def test_make_with_no_makefile_fails(self):
        self.assertEqual(self.errors("autonomy/AGENTS.md", "`make test`\n"), ["no Makefile for `make test`"])

    def test_make_in_prose_is_not_a_command(self):
        self.assertEqual(self.errors("AGENTS.md", "This will make things faster.\n"), [])

    def test_variables_and_phony_are_not_targets(self):
        targets = agent_docs.make_targets(self.root / "simulation/Makefile")
        self.assertEqual(targets, {"test", "smoke", "car", "records", "records-undo"})


class LabelTest(RepoTest):
    def test_real_labels_pass(self):
        text = "`bazel run //tools/spec:fmt`, `//drivers/lhal`, `bazel test //drivers/...` and `bazel test //...`\n"
        self.assertEqual(self.errors("AGENTS.md", text), [])

    def test_a_missing_target_fails(self):
        self.assertEqual(
            self.errors("AGENTS.md", "`bazel run //tools/spec:lock`\n"),
            ["`//tools/spec:lock`: no target named lock in tools/spec/BUILD.bazel"],
        )

    def test_a_missing_package_fails(self):
        self.assertCountEqual(
            self.errors("AGENTS.md", "`//tools/gone:x` and `//gone/...`\n"),
            ["`//tools/gone:x`: no BUILD file in tools/gone", "`//gone/...`: no directory gone/"],
        )

    def test_urls_are_not_labels(self):
        self.assertEqual(self.errors("AGENTS.md", "`https://github.com/org/repo/blob/main/x`\n"), [])


class LinkTest(RepoTest):
    def test_relative_links_resolve_from_the_doc(self):
        self.write("simulation/README.md", "")
        text = "[a](README.md#top) [b](../drivers/lhal/) [c](https://x.org/y) [d](#here)\n"
        self.assertEqual(self.errors("simulation/AGENTS.md", text), [])

    def test_a_broken_link_fails(self):
        self.assertEqual(
            self.errors("simulation/AGENTS.md", "[a](docs/gone.md#x)\n"),
            ["link target docs/gone.md does not exist"],
        )


class SkillTest(RepoTest):
    def test_valid_frontmatter_passes(self):
        self.assertEqual(self.errors("simulation/docs/skills/edit-vehicle/SKILL.md", SKILL_HEAD + "# Edit\n"), [])

    def test_missing_frontmatter_fails(self):
        self.assertEqual(
            self.errors("simulation/docs/skills/edit-vehicle/SKILL.md", "# Edit\n"),
            ["a SKILL.md starts with --- frontmatter ---"],
        )

    def test_a_name_that_differs_from_the_folder_fails(self):
        self.assertEqual(
            self.errors("simulation/docs/skills/edit-car/SKILL.md", SKILL_HEAD),
            ["name `edit-vehicle` does not match the folder `edit-car`"],
        )

    def test_a_name_that_is_not_kebab_case_fails(self):
        text = "---\nname: Edit_Vehicle\ndescription: x\n---\n"
        self.assertIn(
            "name `Edit_Vehicle` is not lowercase-kebab-case of 64 characters or fewer",
            self.errors("simulation/docs/skills/Edit_Vehicle/SKILL.md", text),
        )

    def test_an_empty_or_long_description_fails(self):
        message = "description is empty or longer than 1024 characters"
        for description in ("", "x" * 1025):
            with self.subTest(length=len(description)):
                text = f"---\nname: edit-vehicle\ndescription: {description}\n---\n"
                self.assertEqual(self.errors("simulation/docs/skills/edit-vehicle/SKILL.md", text), [message])


class MainTest(RepoTest):
    def test_main_prints_github_errors_and_fails(self):
        self.write("simulation/AGENTS.md", "1. a\n3. b\n")
        with mock.patch("sys.stdout") as out:
            status = agent_docs.main(["--root", str(self.root), "simulation/AGENTS.md"])
        self.assertEqual(status, 1)
        printed = "".join(c.args[0] for c in out.write.call_args_list)
        self.assertIn("::error file=simulation/AGENTS.md,line=2::step 3 follows step 1", printed)

    def test_main_passes_on_clean_docs(self):
        self.write("simulation/AGENTS.md", "1. a\n2. b\n")
        self.assertEqual(agent_docs.main(["--root", str(self.root), "simulation/AGENTS.md"]), 0)


if __name__ == "__main__":
    unittest.main()
