---
name: simple-technical-english
description: Write or rewrite lhre docs, skills, AGENTS.md files, comments, error messages, PR descriptions and study results in Simplified Technical English (STE). Use when you write any text that a person or an agent in this repo will read, when text is long, dense, hedged or easy to misread, or when someone asks for STE, plain English or a rewrite.
---

# Write in Simplified Technical English

Simplified Technical English (STE) is a controlled form of English. The
aerospace industry made it (ASD-STE100), so that a technician cannot
misread a procedure. lhre uses its sentence rules for the same reason. A
new member, a non-native speaker or an agent must read a doc one way only.

This skill uses the STE rules for sentences. It does not use the STE
dictionary of approved words. ASD does not let us copy that dictionary.
Use plain, common words instead.

## Pick the mode

| Mode | Use it for | Apply |
| ---- | ---------- | ----- |
| Strict | Skill steps, `AGENTS.md` rules, error messages, CI messages, instructions to an agent | Every rule below |
| Flavored | READMEs, ADRs, PR descriptions, study results, explanations | The sentence rules. Prefer plain words, but you can vary them |

## Sentence rules

1. Write one idea in each sentence.
2. Keep a sentence to 20 words or fewer for an instruction. Keep it to
   25 words or fewer for a description.
3. Use active voice. Say who or what does the action: "`make -C simulation car`
   restores `bobsim/`", not "`bobsim/` is restored".
4. Write an instruction as a command: "Run `make -C simulation test`."
5. Use a verb, not a noun form of the action: "check the value", not
   "perform a check of the value".
6. Do not use a semicolon. Write two sentences.
7. Do not use a phrasal verb such as "set up", "spin up" or "kick off".
   Use one verb: "configure", "start", "begin".
8. Keep a noun stack to three words or fewer: "rear damper curve" is
   correct, but "rear damper curve lookup table file" is too long.
9. Use simple tenses: present, past, future and the command form. Keep
   "has" or "have" only when it carries meaning, for example "may have
   failed".
10. Write a list for three or more steps or conditions. Number the steps.
11. Give each paragraph one topic, with six sentences or fewer.

## Word rules

1. Use one name for one thing in a document. Do not rotate "vehicle file",
   "car file" and "YAML" for the same file.
2. Define a term or an abbreviation the first time you use it.
3. Keep real uncertainty. "May", "about" and "we did not check" stay. Do
   not make a hedge into a fact to make the sentence shorter.
4. Do not stack hedges: "it may possibly help" becomes "it may help".
5. Do not use marketing words such as "robust", "seamless", "powerful"
   or "blazing fast". Give the number that shows the claim.
6. Give every number with its unit. For a simulation result, say which
   vehicle `bundle_id` and BobSim SHA gave it.
7. Use symbols and arrows only in tables and code. In a sentence, use
   words.

## Keep the meaning

A rewrite must say the same thing as the original.

- Keep every condition, limit, unit and exception.
- Do not add a cause, a fact or a number that the source does not give.
- If a short sentence loses precision, keep the longer sentence.
- If the text says nothing useful, delete it. A rewrite does not fix
  empty content.

## Procedure

1. Pick the mode.
2. Read the whole text once for its meaning.
3. For each sentence, find the rules that it breaks.
4. Rewrite the sentence. Then check that the meaning did not change.
5. If the text already obeys the rules, change nothing.

## Checks before you finish

- No sentence in an instruction has more than 20 words.
- The text has no semicolons and no phrasal verbs.
- Each thing has one name in the document.
- Every hedge in the source is still there, with the same strength.
- Every number has a unit.
- Code, commands, paths and quoted output did not change.
