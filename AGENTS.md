# Repository workflow

- This project uses the root `.envrc` to select the correct GitHub account.
- Run commits, pushes, GitHub CLI commands, and other GitHub-authenticated operations through `direnv exec . <command>`.
- Do not rely on the globally active GitHub CLI account for this repository.
