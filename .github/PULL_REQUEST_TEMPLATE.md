<!-- One topic per pull request. A security fix goes through a private advisory first (SECURITY.md). -->

## Why

<!-- The problem this solves, not only what changed. Link the issue if there is one. -->

## What changed

<!-- The approach, and anything a reviewer should look at first. -->

## What I tested, and on what

<!--
CI cannot see the appliance: audio, sources, snapcast, CamillaDSP and hardware run only on a unit.
For a change there, say which part of docs/manual/verification-checklist.md you ran (the smoke set
at least), and say so if you cut it short. Write "nothing on a unit" when that is the case.
-->

- Raspberry Pi model:
- Audio card:
- Unit: main unit / multiroom speaker / both
- Build: production build (`npm run build`, served by nginx) / development (`npm run dev`)
- What I ran and what I saw:

## Checklist

- [ ] I have read the [Contributor License Agreement](https://github.com/leodurandfr/Milo/blob/main/CLA.md). Opening this pull request accepts it, for this contribution and every one I make afterwards.
- [ ] The lint floor passes: `ruff check backend/ milo-client/`, `pytest backend/`, `pytest milo-client/`, and in `frontend/` `npm run lint:js`, `npm run lint:css`, `npm run test:run`.
- [ ] If this changes the API or user-facing behavior, the relevant file in `docs/` is updated.
