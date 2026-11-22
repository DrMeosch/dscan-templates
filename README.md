# DSCAN nuclei templates

A filtered mirror of [projectdiscovery/nuclei-templates](https://github.com/projectdiscovery/nuclei-templates), the [nuclei](https://github.com/projectdiscovery/nuclei) templates the DSCAN scanner runs.

This is not the upstream project. To contribute, report or fix a template, go to [projectdiscovery/nuclei-templates](https://github.com/projectdiscovery/nuclei-templates).

## How the sync works

The Sync workflow (`.github/workflows/sync.yml`) runs daily at 03:00 UTC, and can be started by hand:

1. It copies upstream `main` over this tree, except for the files DSCAN keeps (see below).
2. `.github/download-prs.py` adds the templates of open upstream pull requests labelled `Done` into `local/`.
3. `.github/remove-blacklisted.py` removes the templates DSCAN does not run: by severity, tag, directory and template ID, with a short allowlist.
4. A pinned nuclei version validates the result (`nuclei -validate`).
5. The changes are opened as an `[autosync]` pull request, which is reviewed before it is merged.

## Layout

- `local/`: community templates written by upstream contributors and fetched by Sync from pull requests that were open at the time. Each keeps its `author` field.
- `.github/`, `scripts/`, `AGENTS.md`, `README.md`, `.gitignore`: DSCAN's own files. Sync does not overwrite them.
- Everything else is mirrored from upstream unchanged, apart from the filtered templates.

The translated READMEs and `CONTRIBUTING.md` are upstream documents; use this README for the DSCAN mirror and its contribution guidance.

## License and attribution

The templates are the work of [ProjectDiscovery](https://github.com/projectdiscovery) and the nuclei-templates contributors, and are distributed under the MIT License. `LICENSE.md` is upstream's license notice, kept in sync with upstream. Templates fetched from upstream pull requests keep their authors' `author` field.

DSCAN's own scripts, workflows and documentation (`.github/`, `scripts/`, `AGENTS.md`, `README.md`) are also licensed under the MIT License, on the terms in `LICENSE.md`, Copyright (c) DrMeosch.
