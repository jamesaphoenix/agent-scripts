# Ralph Loop — Example Prompt

This is an example prompt.md for use with ralph.sh. Replace it with your
project-specific task prompt. Ralph.sh reads this file each iteration and
passes its contents to the agent.

## Step 1: Read context

1. CLAUDE.md — project summary, architecture, conventions
2. Your spec index — list of specs and their status
3. Your implementation plan — find the first unchecked task

## Step 2: Read the code you need

Read source files relevant to the current task phase.

## Step 3: Pick one task and implement it

Complete tasks sequentially. Do NOT skip ahead to later phases.

## Workflow

1. Write code
2. Build / compile
3. Lint
4. Write tests
5. Run tests — fix any failures
6. Update the spec (check off the completed task)
7. Commit and push

## Rules

- One task per iteration
- All tests must pass before committing
- Do not commit code that doesn't compile
