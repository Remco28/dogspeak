# dogspeak

<!-- agent-office -->
## Agent Office

This repository (`dogspeak`) uses **Agent Office**, the shared working memory for agents on this machine. You arrive with no memory of earlier sessions: the office is how you pick up what the last agent knew, and how the next one picks up from you. Code goes in git; everything else worth keeping goes in the office.

**One agent, one repository.** Work only in this repository. If you are asked to work in another one, suggest that the human start a new agent there instead of switching.

### Start

If another agent started you to do one narrow task (research, a review, a single file), skip all of this: do the task, report back to it, and do not check in.

1. Check in from inside this repository (any git worktree of it counts):
   ```bash
   office begin --by <your-tool>        # for example --by codex
   ```
   If you have been told not to change anything, run `office peek --by <your-tool>` instead.
2. The office gives back a numbered name for this run, like `codex-4821`. Use it on every later call (`--by codex-4821`, or `export OFFICE_AUTHOR=codex-4821`), so what you write is signed.
3. Read the briefing: the tools and skills this machine has, unfinished work, recent activity, and this project's memories. Prefer a listed tool or skill to building your own.
4. Start your first reply to the human with:
   > `Working on dogspeak within Agent Office (<numbered-name>).`

### While you work

**Write while you still know it.** You will not know when your session ends.

- **Work log, the handoff:** `office work open "<title>"`, then `office work note <id> "<where you got to>"` as you go, and `office work close <id> "<outcome>"` once the work is finished, not when one step of it is. A note says where you stopped and what you would check next, not every file you touched. Write one note per stopping point (a piece finished, a decision, a dead end), not one per step: a log of hundreds of notes on one item is never read again.
- **Memories:** `office remember "<fact>"` for decisions and why, conventions, dead ends, and facts the next agent would otherwise rediscover. Add `--global` only for a rule that holds in every project. Never store secrets, `.env` contents, source dumps or progress chatter. If a memory is wrong, correct it and say why (`office forget <id>` removes it).
- **Finding things:** `office context "<task>"` before you start something, `office search "<words>"` for one fact. An empty result is real: nothing relevant is recorded.
- **Tools:** `office tools add <name> --use "<command>" "<what it is>"` for anything installed here that you would want again.
- **Files outside this repository:** before you change one (a tool's settings, a shared config), copy the original next to it, for example `settings.toml.bak`, and say in your work note what you changed and where. Git cannot undo it, and the next agent will not know.
- **Deleting:** never delete or overwrite something you did not create without asking the human first.
- **Committing:** commit, or ask the human to, before a large change, so it can be undone.
- **Temporary files:** put them in your scratch folder (or the system temp folder), never in this repository or the home folder. Link big dependency folders (`node_modules`, `.venv`) rather than copying them. Delete what you made before you finish, and say in your work note if you left anything.
- **Issues:** when the office itself is in your way (not a bug in this repository), check `office issues`, then `office issue open "<what>"`, or add a note to the one already there.

`office help` lists every command.
<!-- /agent-office -->
