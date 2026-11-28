"""Pins which text the STE check fails and which it leaves alone."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.ste import ste

LONG = " ".join(["word"] * 26) + "."


def messages(text):
    return [m for _, m in ste.check_text(text)]


class SemicolonTest(unittest.TestCase):
    def test_a_semicolon_in_prose_fails(self):
        self.assertEqual(messages("Run it; then stop.\n"), ["semicolon: write two sentences"])

    def test_semicolons_in_code_fences_and_spans_pass(self):
        self.assertEqual(messages("Run `a; b`.\n\n```bash\na; b\n```\n"), [])

    def test_a_semicolon_in_a_quote_passes(self):
        self.assertEqual(messages('Do not write "a; b".\n'), [])


class LengthTest(unittest.TestCase):
    def test_twenty_five_words_pass(self):
        self.assertEqual(messages(" ".join(["word"] * 25) + ".\n"), [])

    def test_twenty_six_words_fail(self):
        self.assertEqual(messages(LONG + "\n"), ['26 words, max 25: "word word word word word word word word ..."'])

    def test_a_sentence_wrapped_over_lines_counts_as_one(self):
        words = ["word"] * 26
        text = " ".join(words[:13]) + "\n" + " ".join(words[13:]) + ".\n"
        self.assertEqual(len(messages(text)), 1)

    def test_short_sentences_in_one_paragraph_pass(self):
        self.assertEqual(messages(("Run the test. " * 10) + "\n"), [])

    def test_a_sentence_that_starts_with_code_is_split(self):
        text = " ".join(["word"] * 20) + ". `make test` " + " ".join(["word"] * 20) + ".\n"
        self.assertEqual(messages(text), [])

    def test_code_counts_as_one_word_and_a_link_as_its_text(self):
        text = " ".join(["word"] * 22) + " `a b c d e` [f g](https://x.org/a/b/c).\n"
        self.assertEqual(messages(text), [])
        self.assertEqual(len(messages(text.replace("[f g]", "[f g h]"))), 1)

    def test_list_items_and_table_cells_are_separate_sentences(self):
        items = "\n".join(f"- {' '.join(['word'] * 20)}" for _ in range(3)) + "\n"
        cells = "| " + " | ".join(" ".join(["word"] * 20) for _ in range(2)) + " |\n| - | - |\n"
        self.assertEqual(messages(items + "\n" + cells), [])

    def test_frontmatter_and_headings_are_not_checked(self):
        text = f"---\nname: x\ndescription: {LONG}\n---\n\n# {LONG}\n"
        self.assertEqual(messages(text), [])

    def test_the_line_number_is_the_first_line_of_the_paragraph(self):
        self.assertEqual(ste.check_text(f"Short.\n\n{LONG}\n")[0][0], 3)


class BannedTest(unittest.TestCase):
    def test_each_banned_kind_fails(self):
        cases = {
            "We spin up a container.": 'phrasal verb: "spin up"',
            "Set up the board.": 'phrasal verb: "Set up"',
            "A robust check.": 'marketing word: "robust"',
            "It may possibly fail.": 'stacked hedge: "may possibly"',
            "Perform an analysis of the log.": 'noun form of an action: "Perform an analysis"',
        }
        for text, message in cases.items():
            with self.subTest(text=text):
                self.assertEqual(messages(text + "\n"), [message])

    def test_one_word_forms_pass(self):
        self.assertEqual(messages("The setup is a robustness test. Check the setups.\n"), [])

    def test_banned_words_in_quotes_and_code_pass(self):
        self.assertEqual(messages('Do not write "spin up". Run `set up`.\n'), [])


class IgnoreTest(unittest.TestCase):
    def test_an_ignore_comment_with_a_reason_skips_the_next_paragraph(self):
        text = f"<!-- ste-ignore: quotes a datasheet -->\n{LONG}\n\n{LONG}\n"
        self.assertEqual(len(messages(text)), 1)

    def test_an_ignore_comment_without_a_reason_skips_nothing(self):
        self.assertEqual(len(messages(f"<!-- ste-ignore: -->\n{LONG}\n")), 1)


class MainTest(unittest.TestCase):
    def test_main_prints_github_errors_and_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "AGENTS.md").write_text("Run it; then stop.\n", encoding="utf-8")
            with mock.patch("sys.stdout") as out:
                status = ste.main(["--root", tmp, "AGENTS.md"])
        self.assertEqual(status, 1)
        printed = "".join(c.args[0] for c in out.write.call_args_list)
        self.assertIn("::error file=AGENTS.md,line=1::semicolon: write two sentences", printed)

    def test_main_passes_on_clean_docs(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "AGENTS.md").write_text("Run the test.\n", encoding="utf-8")
            self.assertEqual(ste.main(["--root", tmp, "AGENTS.md"]), 0)


if __name__ == "__main__":
    unittest.main()
