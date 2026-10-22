# Examples

Real output from `marg guide` on BharatCode's `qwen-3.8-27b`. Line numbers in each guide refer to the commit of the repo it was generated from, so later changes can shift them.

| Guide | Repo and commit | Marg commit | Steps | Tokens | Citations |
|---|---|---|---|---|---|
| [express-ONBOARDING.md](express-ONBOARDING.md) | [expressjs/express](https://github.com/expressjs/express) `0dee2c5` | cf984bd | 21 (46 tool calls) | 813,621 | 108 verified, 7 to files not opened, 0 unverified |
| [express-ONBOARDING.hi.md](express-ONBOARDING.hi.md) (Hindi, `--lang hi`) | expressjs/express `0dee2c5` | cf984bd | 31 (53 tool calls) | 1,144,493 | 98 verified, 2 to files not opened, 0 unverified |
| [marg-ONBOARDING.md](marg-ONBOARDING.md) | this repo `d34599d` | d34599d | 9 (20 tool calls) | 184,942 | 88 verified, 1 to a file not opened, 0 unverified |
| [marg-ONBOARDING.ta.md](marg-ONBOARDING.ta.md) (Tamil, `--lang ta`) | this repo `da793dd` | da793dd | 8 (26 tool calls) | 178,817 | 98 verified, 2 to files not opened, 1 unverified |

The figures come from the summary line Marg prints when it finishes. The Express and Tamil runs were on 22 Oct 2026, the English Marg run on 21 Oct.

Each footer reflects the citation checker at the time of its run. Since da793dd the checker also counts bare citations like (README.md:5) and multi-range ones like `agent.py:18,72,93`, so the older footers undercount how many citations their guides contain.

**Edits:** none to the text. The English Express guide was generated before Marg's Mermaid fixer existed, and its diagram did not render on GitHub (an unquoted `next()` label, and `\n` instead of line breaks). It was passed through that fixer, `marg.mermaid.fix_mermaid`, which made 15 label repairs inside the diagram and changed nothing else. Guides generated now get the same repairs automatically.

**Known flaws, left as generated:** in the English Express guide, first task #4 refers to an `examples/mvc/controllers/user-pet/views` folder that does not exist. The Hindi guide has three small typos. The Marg guide's gotcha about `docs/marg-map.svg` comes from Marg's repo map not listing SVG files (fixed in 0e317e8).

**About the Tamil guide:** it shows `--lang` beyond Hindi, but its Tamil is a notch below the Hindi guide's. It has grammar slips, one nonsense word (மட்டிரீ where "matrix" was meant) and an English typo ("cleanning"). One gotcha is wrong: it says `.env` is exempt from the secret-file check, but `.env` is always refused, and it is `.env.example`, `.env.sample` and `.env.template` that are allowed. The unverified citation in its footer is a real catch: the model wrote `ci.yml:10-19` instead of `.github/workflows/ci.yml:10-19`. Marg's script check found no words in the wrong script in this run. An earlier Tamil run had slipped into Korean, Bengali and Chinese, which is why that check exists.
