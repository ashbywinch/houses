# Writing Documentation Guide

For anyone writing/updating project documentation.

## Context Efficiency

Every doc contains only what's relevant to its single topic and audience. Before writing, answer:

- Who is this for? (dev running the server, agent implementing a feature, contributor adding enrichment)
- What single question does it answer?
- What does this audience NOT need?

Content for a different audience/topic goes in its own doc — cross-reference by **linking**, never by copying.

Humans AND AI agents must both navigate from AGENTS.md to find what they need.

### Signs of violation

- Two distinct audiences in one doc
- Two unrelated topics
- Temptation to copy-paste content from another doc
- Reader must skip large sections
- A concept explained twice (within a doc or across docs)

## SOLID/DRY for docs

| Principle | Rule |
|---|---|
| **Single source of truth** | Each fact lives in exactly one place; other docs link, never repeat |
| **One topic per file** | Subtopic for a different audience → separate file + link |
| **Avoid redundancy** | Exists elsewhere? Link. Doesn't exist? Most logical home, link from others |
| **Delete, don't archive** | Obsolete = wrong = remove. No "legacy" renames, no deprecation notices |
| **Docs match code** | Rename a function/module/tab → update docs in the same commit |
| **API keys never in docs** | Keys live in the shell environment (`.zshrc`, `.bashrc`, `~/.profile`) — never document values |

## Checklist

- [ ] Single, clearly stated audience
- [ ] Single, clearly stated topic
- [ ] No content belonging to a different doc
- [ ] No duplicated content from other docs (link instead)
- [ ] Every section relevant to the stated audience
- [ ] Title + first paragraph make purpose clear
- [ ] Links to related docs where readers might need them

## How to update

1. Identify audience + topic.
2. Find the existing doc for that audience/topic.
3. No doc exists → create one with a clear single purpose.
4. Add content in the right place.
5. Update cross-references (AGENTS.md decision tree, reference tables).
6. Check nothing duplicated that belongs elsewhere.
7. Verify humans and agents find it starting from AGENTS.md.
