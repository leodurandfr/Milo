# Contributing to Milō

Thanks for wanting to help.

## Before your first pull request

All contributions require agreeing to the [Contributor License Agreement](CLA.md).
Opening a pull request means you accept it — there is nothing to sign and nothing to
install. It covers every contribution you make afterwards.

The CLA exists so Milō can be offered under more than one licence in the future (for
example, a commercial licence to a hardware manufacturer) while the project itself stays
source-available under PolyForm Shield 1.0.0 for everyone. You keep full ownership of
what you write.

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

Open an issue with the bug report form: it asks for your Milō version, your Raspberry Pi
model, your audio card and the steps to reproduce. Attach the diagnostic report (Settings ›
System › Diagnostic › Generate a report, then Copy), generated right after the problem
happened: it carries the version, the audio path, the speakers and the recent errors, with
passwords and keys left out, and is usually what turns a report into something actionable.
Read it before you paste it.

A security vulnerability does not go in an issue: report it privately, as
[SECURITY.md](SECURITY.md) explains.
