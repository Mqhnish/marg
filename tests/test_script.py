from marg.script import code_spans, foreign_words

# Slips from a real Tamil guide (see examples/README.md), plus code that must be ignored.
TAMIL = '''# ஆன்போர்டிங் வழிகாட்டி (Onboarding guide)
`build_repo_map` repo-வ을 walk செய்து, responses/chat দুই API styles. Step budget;用完 ஆகினால்.
Code is never checked: `用完` and

```python
x = "দুই"  # ignored
```
'''


def test_foreign_words_flags_other_scripts_in_prose_only():
    assert foreign_words(TAMIL, "ta") == ["repo-வ을", "দুই", "budget;用完"]


def test_latin_and_the_target_script_are_allowed():
    assert foreign_words("यह repository `express` का source code है। Node.js 18+ चाहिए।", "hi") == []
    assert foreign_words("ஒரு pull request அனுப்பு.", "ta") == []
    assert foreign_words("ஒரு pull request அனுப்பு.", "hi") == ["ஒரு", "அனுப்பு"]


def test_code_spans_cover_inline_and_fenced_code():
    assert code_spans(TAMIL) == sorted(["`build_repo_map`", "`用完`", '```python\nx = "দুই"  # ignored\n```'])
