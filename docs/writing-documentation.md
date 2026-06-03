# Writing Documentation Guide

This guide is for anyone writing or updating documentation in this project. It covers the principles and conventions that keep our docs focused, maintainable, and useful.

## Context Efficiency Principle

Every documentation file must only contain information that is relevant to its topic and audience. For each document, be clear about exactly what the (single) topic and intended audience is.

**Before writing a doc, answer these questions:**
- Who is this for? (developer running the server, agent implementing a feature, contributor adding enrichment)
- What single question does it answer?
- What information does this audience NOT need?

If a piece of information belongs to a different audience or topic, put it there instead. Don't cross-reference by copying content. Cross-reference by linking.

All documentation must be usable by humans or by AI agents. Humans and agents must both be easily able to navigate the documentation to find what they need, starting from the entry point of AGENTS.md.

### Signs You're Violating Context Efficiency

- A doc has two distinct audiences (e.g., "this section is for contributors, that section is for administrators")
- A doc covers two unrelated topics
- You're tempted to copy-paste content from another doc
- A reader has to skip large sections to find what they need
- Content is duplicated or concepts are explained twice in broadly the same way, whether within one doc or across several.

## SOLID/DRY Principles for Documentation

### Single Source of Truth

Each piece of information lives in exactly one place. Other docs link to it. They don't repeat it.

**Good:** The development guide says "See the column reference for details" and links to column-reference.md.

**Bad:** The development guide repeats the column layout inline.

### One Topic Per File

Each doc file covers one topic for one audience. If you need to cover a subtopic for a different audience, create a separate file and link to it.

### Avoid Redundancy

Before adding content to a doc, check if it already exists elsewhere. If it does, link to it instead of repeating it. If it doesn't, put it in the most logical place and link from other docs.

### Delete, Don't Archive

Obsolete content is a liability. When something is no longer accurate, delete it. Don't rename it "legacy", don't add a deprecation notice. If it's wrong, remove it.

### Docs Must Match the Code

When you rename a function, module, or tab, update the docs in the same commit. When you add a feature, document it before moving on. Outdated docs are noise.

### API Keys Never Go in Docs

API keys, passwords, and secrets never appear in documentation or `.env` files. They live in the shell environment (`.zshrc`, `.bashrc`, `~/.profile`). Never document actual key values.

## Documentation Checklist

Use this to evaluate whether a doc follows Context Efficiency:

- [ ] Single, clearly stated audience
- [ ] Single, clearly stated topic
- [ ] No content that belongs to a different doc
- [ ] No duplicated content from other docs (link instead)
- [ ] Every section is relevant to the stated audience
- [ ] Title and first paragraph make the purpose clear
- [ ] Links to related docs where readers might need them

## How to Update Documentation

1. Identify the audience for your content
2. Find the existing doc for that audience and topic
3. If no doc exists, create one with a clear single purpose
4. Add your content to the right place
5. Update cross-references in other docs (AGENTS.md decision tree, reference tables)
6. Check that you haven't duplicated information that belongs elsewhere
7. Make sure that humans and agents will find your document if they start by reading AGENTS.md.
