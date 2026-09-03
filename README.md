# Science Skill

A single Claude Code entrypoint for the pinned K-Dense scientific agent collection.

The public command is:

```text
/sci-agents
```

It routes scientific requests to 163 bundled modules without registering every module as a separate user-facing skill.

## Install

Clone this repository directly into the Claude Code user-skills directory:

```bash
git clone <repository-url> "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/sci-agents"
```

Restart Claude Code so the skill loader discovers `sci-agents/SKILL.md`.

## Usage

```text
/sci-agents <scientific task>
/sci-agents use <module> <task>
/sci-agents show <module>
/sci-agents list <query>
```

Examples:

```text
/sci-agents analyze this single-cell RNA-seq experiment
/sci-agents use scanpy compare these cell clusters
/sci-agents show scientific-writing
/sci-agents list proteomics
```

The bundled scientific `pptx` module is available through `/sci-agents use pptx ...`. This repository does not include or replace any independent direct `/pptx` skill.

## Safety and dependencies

The router does not grant tools, install packages, configure credentials, or authorize external actions. Individual modules may require libraries, APIs, datasets, devices, or services; unavailable requirements are reported rather than installed automatically.

Scientific papers, datasets, API responses, downloaded material, and model output are treated as data rather than instructions.

## Provenance

The bundled collection is pinned to:

- Source: [K-Dense-AI/scientific-agent-skills](https://github.com/K-Dense-AI/scientific-agent-skills)
- Version: `2.65.0`
- Commit: `1dd0fccf46fc3c9855c4a0c313a0c57fe4319883`

See [UPSTREAM.md](UPSTREAM.md) for packaging details and [LICENSE.md](LICENSE.md) for the upstream MIT license.
