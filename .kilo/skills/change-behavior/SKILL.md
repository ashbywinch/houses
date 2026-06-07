---
name: change-behavior
description: Decide how to translate user behavior-change requests into rule edits, then draft or modify the appropriate `.kilo/rules/` file and prepare a PR.
---

When the user asks to change your behavior:

1. Decide whether to use a skill, workflow, or rule.
   - If the request is about a reusable agent capability or helper action, use a skill.
   - If the request is about how you should behave, what preferences you should follow, or what policy you should enforce, use a rule.
   - If the request is about execution, process, or a repeatable standard operation (for example, submitting a PR, running tests, or executing a review sequence), suggest a workflow.
   - Skills and rules often work together: skills provide abilities, and rules define when or why those abilities should be used.
   - If a suitable skill, rule, or workflow already exists, prefer modifying that existing artifact over creating a new one.
     * Update an existing skill when the request refines or extends a reusable capability.
     * Update an existing rule when the request changes agent behavior or policy.
     * Update an existing workflow when the request changes a standard process.

   Examples:
   - "Make the agent prefer short answers and more comments in code." → rule.
   - "Add a reusable helper that summarizes rule changes and lists affected files." → new or updated skill, plus rule if needed.
   - "Change the agent so it only edits markdown docs when writing documentation." → rule.
   - "Create a PR for this rule change and run tests." → workflow.
   - "Use the existing review assistant skill and adjust it to include a checklist." → update existing skill.
   - "Require the agent to ask before modifying tests." → rule.

2. Understand the request clearly.
   - Determine whether the user means a change in how you should act, what tools you may use, whether you should prefer a different style, or whether you should follow a new project convention.
   - If the request is ambiguous, ask a concise clarifying question before editing any rule.

3. Review the existing rules, skills and workflows in `.kilo/`.
   - Look for an existing rule, skill or workflow that already covers the requested behavior.
   - If a matching rule, skill or workflow exists, propose a precise update to it.
   - If no rule, skill or workflow exists, create a new file or files of the appropriate type.

4. Draft the change in the repository.
   - Keep language short and specific.
   - Do not encode broad policy in a skill itself; keep the skill as the process. Policy belongs in rules.
   - Use plain Markdown and name the file clearly, for example `change-behavior-style.md` or `change-behavior-permissions.md`.

6. Prepare a PR description.
   - Explain what behavior changed and why.
   - Reference the exact rule file(s) changed.
   - If the change is subtle, include one or two examples of the new behavior.

7. If the user explicitly requests it, prepare the PR.
   