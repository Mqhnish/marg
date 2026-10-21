from marg.mermaid import fix_mermaid

# From a real guide to Express that failed to render on GitHub.
BROKEN = '''Intro mentions next() and a|b(c)| outside the diagram.

```mermaid
flowchart TD
    AppFn["app (callable function)\\nlib/express.js:36-39"]
    Handle[app.handle\\nlib/application.js:152-178]
    DB[(database)] --> Sub[[subroutine]]
    Fn[res.send(object)] --> Done
    H2 -->|next()| Done["done -> finalhandler"]
    RT -.|no match|.-> FH
```

```js
app.get('/', (req, res) => res.send('a|b(c)|'))
```
'''


def test_fix_mermaid_quotes_labels_and_converts_line_breaks():
    fixed, repairs = fix_mermaid(BROKEN)
    assert repairs == 4
    assert 'AppFn["app (callable function)<br/>lib/express.js:36-39"]' in fixed
    assert "Handle[app.handle<br/>lib/application.js:152-178]" in fixed
    assert 'Fn["res.send(object)"]' in fixed
    assert 'H2 -->|"next()"| Done["done -> finalhandler"]' in fixed
    assert "DB[(database)] --> Sub[[subroutine]]" in fixed  # node shapes untouched
    assert "RT -.|no match|.-> FH" in fixed
    assert fixed.startswith("Intro mentions next() and a|b(c)| outside")  # prose untouched
    assert "res.send('a|b(c)|')" in fixed  # other code blocks untouched
    assert fix_mermaid(fixed) == (fixed, 0)
