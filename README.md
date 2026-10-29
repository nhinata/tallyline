# tallyline

A fast Claude Code status line that **tallies your token usage and API-equivalent cost by day, month and year** across every session, and tells you **why your usage is high**.

![tallyline status line on a Pro plan](https://raw.githubusercontent.com/nhinata/tallyline/main/assets/screenshot.png)

```
Pro/Max plan:  [Opus 5.5] │ ctx 120k (12%) │ 5h 23%  7d 41% │ Oct 22.1M tok (≈$14.92)
API billing:   [Opus 5.5] │ ctx 120k (12%)  6.6M tok (≈$3.85) │ Oct 22.1M tok (≈$14.92)
```

What to watch while working comes first and totals come last: this session (cyan), your whole account (magenta), all sessions on this machine (yellow).

```bash
pipx install tallyline && tallyline init
```

Beyond the status line:

- `tallyline diagnose` shows what drove your usage (cache rewrites after idle breaks, tool outputs kept in context, long context) and what to change.
- `tallyline sessions` lists your open sessions and what resuming each one costs once its cache has expired.

[日本語版 README](README.ja.md)

## Why

Claude Code's status line only knows about the current session. tallyline keeps a running ledger of every session on your machine, so you can answer "how much have I used this month?" at a glance. On a Pro/Max plan, the API-equivalent cost tells you what your usage would have cost on the pay-as-you-go API.

- **Fast**: after the first scan it parses only newly appended transcript bytes. A render takes about 25 ms.
- **Local only**: apart from a once-a-day update check that asks PyPI for the latest version number, tallyline makes no network requests. It never sends your conversations or usage data anywhere. Turn the check off with `tallyline config update-check off` or `NO_UPDATE_NOTIFIER=1`.
- **No dependencies**: it uses only the Python standard library (Python 3.9+).
- **Accurate**: it removes duplicate transcript lines written while a response streams. Without that, a naive sum roughly doubles your usage.

## Install

```bash
pipx install tallyline      # or: uv tool install tallyline
tallyline init              # adds the statusLine entry to ~/.claude/settings.json
```

Claude Code reloads `settings.json` when it changes, so the status line appears without a restart. `init` backs up `settings.json` to `settings.json.bak`. It refuses to replace an existing status line unless you pass `--force`.

## What it shows

| Segment | Scope | Meaning |
|---|---|---|
| `[Opus 5.5]` | — | Current model |
| `ctx 120k (12%)` | this session | Size of the context right now, and how full the context window is. It drops after `/clear` or `/compact`. Every turn re-reads the whole context, and resuming after the cache expires (5 minutes or 1 hour idle) writes all of it again, so the token count is the better gauge of cost |
| `6.6M tok (≈$3.85)` | this session | API billing only: tokens used so far in this session, subagents included |
| `5h 23%  7d 41%` | your account | Pro/Max only: usage of your 5-hour and 7-day rate limits, counted across all devices and apps. A window that has reset shows `0%` |
| `Oct 22.1M tok (≈$14.92)` | all sessions on this machine | Total for the period (`today`, the month, or the year) |

A `*` after the period, as in `2026*`, means tallyline's records start partway through that period, so the total is incomplete. This is normal right after installing: Claude Code keeps only recent transcripts (see Caveats).

tallyline picks between rate limits and session usage automatically. Claude Code sends rate limits only to Pro/Max subscribers, and for them the per-session cost has little meaning.

Claude Code sends rate limits only after a session's first response, so tallyline remembers the latest values seen in any session. A new session shows them right away, and every session shows the newest value seen on this machine. Usage on claude.ai or other devices appears after your next response, so treat the figures as "at least".

Every turn re-reads the whole context, so cumulative tokens grow much faster than `ctx`. Most of them are cache reads.

Token totals include input, output, cache writes and cache reads. Cache reads are cheap, so the `≈$` cost is the better gauge of how heavily you are using Claude. The cost is an API-equivalent estimate at list prices.

## Finding out why usage is high

The status line shows how much you use. `tallyline diagnose` shows why, and what to change:

```
$ tallyline diagnose
Last 30 days · 9 sessions · 2,087 requests · ≈$331.83 API-equivalent
  cache reads 52%   cache writes 43%   output 6%

Top causes
 1. Resumed after the cache expired                    ≈$122.81   37%
    72 requests after an idle gap wrote the context again (avg 384k tokens).
    → Before a long break, /compact or start a new session.
 2. Tool outputs kept in context                       ≈$102.28   31%
    claude-in-chrome 74% · Read 17% · Bash 6% · Edit 1%
    Costliest: Read notes.md (25.1k tokens, carried 482 turns, ≈$2.52)
    → Delegate broad reading or browsing to a subagent so only its summary stays.
 ...
```

| Cause | What it counts |
|---|---|
| Resumed after the cache expired | Cache writes on a request sent after the cache lifetime (5 minutes or 1 hour) had passed, so the whole context was written again |
| Tool outputs kept in context | Each tool output's share of cache writes and reads, from the request that first sent it until the session ends or is compacted. Its size comes from how much the context grew |
| Long context | The part of each cache read above 200k tokens. This overlaps the causes above |
| Same file read repeatedly | Re-reads of a file within one session, as part of tool outputs |

Causes under 1% of the total are left out.

```bash
tallyline diagnose --days 7          # look back 7 days (default 30)
tallyline diagnose --session 1fc6    # one session, by id prefix
tallyline diagnose --json            # for scripts, or to hand to Claude
tallyline diagnose --no-titles       # hide session titles, projects and file names
```

It reads the transcripts directly and only when you run it, so the status line stays as fast as before. Claude Code deletes old transcripts (see Caveats), so the look-back is limited to what is still on disk.

## Finding sessions that are costly to resume

With several sessions open, it is easy to lose track of which one holds what, and of how much picking one up again will cost. `tallyline sessions` lists every running Claude Code session:

```
$ tallyline sessions
6 open Claude Code sessions · 4 with an expired cache
Resuming all of them writes 612k tokens again (≈$4.90 API-equivalent).

 #  Last message  Where             State  Context  Cache    Resume  Title
 1  now           tmux work:1 api   busy      142k  warm             Add rate limiting to the API
 2  25m ago       tmux work:2 web   idle       64k  warm             Fix login redirect loop
 3  5h ago        ttys004           idle      231k  expired  ≈$1.85  Migrate billing to the new schema
 4  20h ago       tmux work:3 docs  idle      121k  expired  ≈$0.97  Write the release notes
 5  2.4d ago      ttys002           idle      174k  expired  ≈$1.39  Investigate flaky checkout test
 6  6.1d ago      background        idle       86k  expired  ≈$0.69  Draft the onboarding guide

Resume: an expired session writes its whole context to the cache again on the next message.
Closing a session keeps the conversation; claude --resume picks it up later.
```

| Column | Meaning |
|---|---|
| Last message | Time since the last message in the session |
| Where | The tmux session, window and name, the terminal (tty), or `background` |
| Context | Size of the context now. For an expired session, this is what the next message writes again |
| Cache | `warm` while the cache lifetime (5 minutes or 1 hour, whichever the session last used) has not passed |
| Resume | API-equivalent cost of writing the context again on the next message |

It only reports; whether to close a session is up to you. A session moved to the background shows `(continued as #n)` on its original entry.

```bash
tallyline sessions --sort resume   # most expensive to resume first
tallyline sessions --json          # for scripts
tallyline sessions --no-titles     # hide titles, directories and tmux window names
```

It reads the per-session files Claude Code keeps in `~/.claude/sessions/`. They are undocumented and may change between Claude Code versions.

## Configuration

```bash
tallyline config range month   # today | month | year | all
tallyline config cost off      # hide cost figures
tallyline config color off     # plain text (NO_COLOR is honored too)
tallyline config update-check off  # no daily version check (or set NO_UPDATE_NOTIFIER=1)
tallyline config               # show current settings
```

Settings live in `~/.config/tallyline/config.json`. The cache lives in `~/.cache/tallyline/usage.db`. `tallyline rebuild` re-scans every transcript. It keeps the history of transcripts that Claude Code has already deleted.

### Prices

Prices come from a table bundled with each release, generated from [Anthropic's pricing page](https://platform.claude.com/docs/en/about-claude/pricing). A scheduled GitHub Actions job checks that page daily and opens a pull request when prices change. Costs are computed when the status line renders, so a price update also applies to past usage.

If a model is missing from the table, its cost shows as `+?`. To fill in a price without waiting for a release, add it to `config.json`. Prices are USD per million tokens:

```json
{
  "prices": {
    "claude-new-model": {
      "input": 3, "cache_write_5m": 3.75, "cache_write_1h": 6,
      "cache_read": 0.3, "output": 15
    }
  }
}
```

Fast mode requests are multiplied by each model's `fast_multiplier`.

## Caveats

- The cost is an estimate at list prices, not a bill. On Pro/Max plans you pay your plan price.
- tallyline only sees what Claude Code writes to transcripts. Some background requests are never recorded there, so totals can come out lower than `/cost`. In one auto mode session, tallyline counted about 15% less than `/cost` (likely the permission checks auto mode runs). Tokens and cost come from the same transcripts, so the two always match each other.
- Claude Code deletes old transcripts after `cleanupPeriodDays` (30 by default). tallyline keeps whatever it has already ingested, but it can't count transcripts that were deleted before you installed it.
- Transcript files are an internal Claude Code format and may change.
- If you switch between several Claude accounts on one machine, their rate limit records can mix.
- Tested on macOS and Linux. Windows is untested; on Windows, `tallyline sessions` cannot tell whether a listed session is still running, so a crashed session may stay in the list.
- Not affiliated with or endorsed by Anthropic.

## Updating

When a newer release is out, the status line ends with `↑ 0.2.0`. Run:

```bash
pipx upgrade tallyline      # or: uv tool upgrade tallyline
```

New releases also carry the latest model prices.

## Uninstall

```bash
tallyline uninstall && pipx uninstall tallyline
```

## Related projects

- [ccusage](https://github.com/ryoppippi/ccusage) builds detailed daily, weekly, monthly and session reports for Claude Code and many other coding agents.
- [ccstatusline](https://github.com/sirmalloc/ccstatusline) offers a highly customizable status line with many widgets and themes.

tallyline keeps to a small, fixed status line plus two questions those tools don't focus on: why usage was high, and which open sessions are costly to resume.

## License

MIT
