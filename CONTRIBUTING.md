# Contributing to Milō

Thanks for wanting to help.

## Before your first pull request

All contributions require agreeing to the [Contributor License Agreement](CLA.md). It is
a one-time step — a bot comments on your first pull request with a link, and signing
takes a few seconds. It then covers every contribution you make afterwards.

The CLA exists so Milō can be offered under more than one licence in the future (for
example, a commercial licence to a hardware manufacturer) while the project itself stays
GPL-3.0 for everyone. You keep full ownership of what you write.

## Getting set up

See the [Developer Guide](docs/development.md) for setup, architecture and testing, and
the [Architecture overview](docs/architecture.md) for how the pieces fit together.

## Pull requests

- One topic per pull request.
- Explain **why** in the description, not only what changed.
- Match the conventions of the surrounding code rather than introducing new ones.
- If your change affects the API or user-facing behaviour, update the relevant file
  in `docs/`.

## Reporting a bug

Open an issue with your Raspberry Pi model, your Milō version, and the steps to
reproduce. Service logs from `journalctl` are usually what turns a report into something
actionable.
