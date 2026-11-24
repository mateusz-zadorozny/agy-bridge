# Configuration

agy-bridge is set up through an optional config file, a few environment variables, the
files `install` puts in place, and the agent files themselves.

## The config file

`~/.agy-bridge/config.json` (or `$AGY_BRIDGE_HOME/config.json`). Every key is optional;
a missing file means the defaults. A file that isn't valid is an error, not ignored, so a
typo can't silently drop a sandbox exception or a deny rule.

```json
{
  "default_model": "gemini-4-neon-high",
  "sandbox": true,
  "sandbox_off_paths": ["~/LocalSites/shop", "~/Projects/api-with-docker"],
  "deny_commands": [".*make deploy.*"],
  "env_keep": [],
  "quota_warn_below": 0.2
}
```

| Key | Default | Effect |
|---|---|---|
| `default_model` | none | Model used when Claude names none. A slug with a level (`…-high`) needs no `effort`. |
| `sandbox` | `true` | Run AGY with `--sandbox`: the project is read-only to its shell commands. |
| `sandbox_off_paths` | `[]` | Projects (and everything under them) reviewed without the sandbox, because AGY must reach a running environment there. |
| `deny_commands` | `[]` | Extra command rules, added to the built-in list. AGY matches them against the whole command, and its parser handles literal text, spaces and `.*` but not backslash escapes (`\s`, `\b`) or `\|` (AGY 1.5.16). Write `.*make deploy.*`. |
| `env_keep` | `[]` | Environment variables to pass to AGY even though they look like credentials. |
| `quota_warn_below` | `0.2` | Warn when a quota bucket of the model's group has less than this fraction left. |

The file lives in the state folder, never in the reviewed project, on purpose: a project
must not be able to switch off the sandbox it is reviewed under. The server reads it at
each `agy_start`, so changes need no restart.

## Environment variables

| Variable | Default | Effect |
|---|---|---|
| `AGY_BIN` | `~/.gemini/bin/agy`, then `agy` on `PATH` | The AGY executable to run. |
| `AGY_BRIDGE_HOME` | `~/.agy-bridge` | Where sessions, scratch folders, backups, caches and logs live. |
| `AGY_BRIDGE_LOG` | off | Set to `1` to keep a full transcript of every session in `logs/`. |

The server reads these when Claude Code starts it, so set them in the server's MCP entry;
your shell's environment doesn't reach it. Add an `env` block to the `agy` entry in `~/.claude.json` (user scope)
or in the project's `.mcp.json` (project scope):

```json
"agy": {
  "command": "/Users/you/.local/bin/agy-bridge",
  "args": [],
  "env": {"AGY_BRIDGE_LOG": "1"}
}
```

Or register it with the variable from the start:

```bash
claude mcp remove agy -s user
claude mcp add --scope user agy -e AGY_BRIDGE_LOG=1 -- /path/to/agy-bridge
```

Then restart Claude Code.

The `agy-bridge` commands (`install`, `uninstall`, `doctor`) run in your shell and read
your shell's environment, not the MCP entry. If you set `AGY_BIN` or `AGY_BRIDGE_HOME`
for the server, export them in your shell too, or `doctor` checks a different `agy` or
state folder. Likewise, `doctor`'s "transcript logging" line reflects your shell.

If `install --force` replaces a registration that ran a different command, the new
registration has no `env` block, so add it back afterwards.

### Transcript logging

Logging is off by default because a transcript includes everything AGY read and wrote,
which means your code. When it is on, `logs/<session>.ndjson` records:

- the command line each AGY process was started with;
- every message sent to AGY;
- every event AGY returned;
- AGY's stderr.

Turn it on to debug a problem or to report a bug. Read a log before sharing it.
`agy_end` with `forget` deletes that session's log.

## The state folder

`~/.agy-bridge/`, or `$AGY_BRIDGE_HOME`:

| Path | Contents | Cleaned up |
|---|---|---|
| `sessions.json` | One record per session: agent, project, model, effort, AGY conversation id, turn count, last verdict. | `agy_end` with `forget` |
| `sessions.lock` | Lock file, so several bridges can share the registry. | — |
| `scratch/<session>/` | AGY's own folder for experiments, such as copies or a `git worktree`. | `forget`; at server start, folders with no session, and those of sessions unused for 30 days |
| `backup/<session>/turn-NNN/` | Your uncommitted work, saved before each turn. | Last 5 turns kept; `forget` removes all; at server start, folders with no session, and those of sessions unused for 30 days |
| `backup/blobs/` | One copy of each distinct file in the backups, by content hash. The turn folders hardlink into it. | Whenever a backup is removed, blobs nothing links to go with it |
| `cache.json` | AGY's tool list (per AGY version) and model list (refreshed daily). | Delete it any time |
| `install.json` | What `install` wrote, with checksums. | `uninstall` |
| `logs/<session>.ndjson` | Transcripts, only when `AGY_BRIDGE_LOG` is on. | `forget` |

If `sessions.json` ever gets corrupted, it is renamed to `sessions.corrupt.json` and a
fresh one is started.

## What AGY is allowed to do

Each AGY process is started with settings for that session only. Your own AGY settings
are not changed, so interactive AGY use is unaffected. The settings:

- **Agent mode**: AGY's Default mode (`"agentMode": ""`). Since AGY made Auto the default,
  Auto would approve file edits in the project whatever the rules below say.
- **Shell commands**: any, except the deny list. A reviewer needs to run tests, linters
  and builds.
- **Deny list**: remote access and production tools (`ssh`, `scp`, `rsync`, `gp`, `gh`,
  `git push`, `mysql`, writing `wp db`/`wp search-replace`, `docker … down/stop/rm`),
  plus, without the sandbox, git commands that rewrite the project (`checkout`, `reset`,
  `stash`, `commit`…). A denied command returns an error to AGY; the turn goes on.
- **File-writing tools**: only inside the session's scratch folder.
- **Sandbox** (`--sandbox`, unless switched off): shell commands can read the project
  but not write to it.

AGY's environment has credential-like variables removed (`*TOKEN*`, `*SECRET*`,
`SSH_AUTH_SOCK`, `AWS_*`, `CLOUDFLARE_*`…), and `~/.agy-bridge/shims` comes first on its
`PATH`, where `ssh`, `scp`, `sftp`, `rsync`, `gp` and `gh` are scripts that refuse, and
`git` refuses `push` and runs the real git for everything else.

Anything outside these rules that AGY would normally ask you to approve is denied, since
nobody is there to approve it. AGY reports what was denied, and agy-bridge asks it up to
three times to carry on without it. agy-bridge never passes
`--dangerously-skip-permissions`.

The deny list and shims catch mistakes, not attacks: `bash -c`, an absolute path or a
script can get round a regex. The sandbox is the real limit.

### Sandbox on or off

| | Sandbox on (default) | Sandbox off |
|---|---|---|
| Shell writes to the project | blocked by the OS | possible; detected afterwards |
| `git worktree add` | blocked (writes the project's `.git`) | allowed |
| Experiments | `git clone --local` into scratch | worktree or clone |
| Local's MySQL sockets (`~/Library/Application Support`) | blocked | reachable |
| Dev servers on localhost | through AGY's proxy; may fail | reachable |
| Tests that write caches into the project | fail; run them in the clone | work |

Switch it off per project with `sandbox_off_paths`, or for one session when you ask Claude
to (`agy_start(…, sandbox: false)`). A session keeps its choice when resumed. Every
result says `"sandbox": "on"` or `"off"`.

Shell commands can still change files. The agent's instructions forbid changing the
project, and the bridge detects and backs up, as described in the [user guide](user-guide.md#reading-the-result).
See [SECURITY.md](../SECURITY.md) for what that does and does not protect against.

## Models and effort

Set `default_model` in the config file to pick the model used when Claude names none.
Without it, the Claude skill tells Claude to use a medium effort level on a model from
your AGY's list; if no model is passed, AGY uses its own default.

## Quota

At each `agy_start` and `agy_say` the bridge reads AGY's quota (`agy -p /quota`: about
3 seconds, no tokens, run alongside the turn's start). If a bucket of the chosen model's
group (Gemini, or Claude and GPT) is below `quota_warn_below`, the result carries
`notes.quota_warning` with the percentage and reset time, and Claude tells you. It never
blocks a review. `agy_list_agents` shows the full quota. The model list comes from `agy models` and is cached for a day; ask Claude
to "refresh the AGY model list" after AGY adds models.

## Answer formats

AGY answers in one of two formats, chosen per session with `format`, and changeable
mid-session:

- **`review`** (the default): `verdict`, `summary`, `findings` and
  `questions_for_claude`. See
  [`review.schema.json`](../src/agy_bridge/data/review.schema.json).
- **`brainstorm`**, for planning and ideas: `verdict` (`needs_discussion` or `ready`),
  `summary`, `stage`, `techniques_used`, `reframed_problem`, `assumptions` (each with
  its source), `options` (idea, gains, costs), `recommendation`, `next_step` and
  `questions_for_claude`. See
  [`brainstorm.schema.json`](../src/agy_bridge/data/brainstorm.schema.json).

`agy_start` also takes a `schema` argument for a custom format. If AGY
answers in plain text instead, agy-bridge takes the first JSON object from the text and
marks the result `review_parsed_from_text`.

## Agents and skills

Edit the installed agents and skills to suit you. `install` won't overwrite your edits:

- `~/.gemini/config/agents/code-reviewer.md`, `design-reviewer.md` and
  `thinking-partner.md`: each agent's role and ground rules, and the tools it may use.
- `~/.gemini/config/skills/code-review/SKILL.md`: how to review code, the checklist, and
  how to grade severity.
- `~/.gemini/config/skills/architecture-review/`: how to review a design (`SKILL.md`)
  and the ten review lenses (`references/principles.md`).
- `~/.gemini/config/skills/thinking-techniques/`: how to brainstorm and review, the
  assumption guard, and how to choose a technique (`SKILL.md`), plus the techniques
  themselves (`references/techniques.md`). Add the techniques your team likes.
- `~/.claude/skills/agy/SKILL.md`: how Claude runs the review loop: how many rounds,
  when to stop arguing, what to report.

To add more agents, see [Writing your own agents](writing-agents.md).

### Other skills and agents AGY can use

An agent's `skills:` list names the skills it is told to follow, but AGY doesn't stop
there. **It can also read and follow any other skill it can see:**

- your global skills, in `~/.gemini/config/skills/`;
- the reviewed project's own skills, in `.agents/skills/` in the project or any folder
  above it.

We've seen both in testing:
- A shipped agent read an unrelated skill from the user's global folder.
- A project skill saying "end every review summary with these words" was followed,
  although nobody mentioned it.

The same goes for agents. A project's `.agents/agents/code-reviewer.md` replaces the
shipped `code-reviewer` there, because AGY uses the nearest agent with a given name.

What this means for you:

- **Your own skills shape the shipped agents on your machine**, for better or worse. If
  reviews follow instructions you didn't expect, look at what's installed.
- **Project skills are a feature.** A project can teach every reviewer its conventions.
- **They are also a way in for an untrusted project.** See [SECURITY.md](../SECURITY.md).
- The `looked_at` list in each result shows which files AGY read, skill files included.
  When a project's own agent is used instead of the global one, the first result says
  so in `notes.agent_file`.
