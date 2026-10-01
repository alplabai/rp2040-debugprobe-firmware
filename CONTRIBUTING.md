# Contributing

This is the RP2040 debug probe firmware for Alp Lab E1M EVKs, derived from
[raspberrypi/debugprobe](https://github.com/raspberrypi/debugprobe). The USB
interface contract (see [README.md](README.md)) is consumed by host tooling in
[alplabai/alp-sdk](https://github.com/alplabai/alp-sdk); changing interface
numbers, endpoints or strings needs a matching change there.

## Branches

`dev` is the default branch and integration target. Branch from `dev`
(`fix/`, `feat/`, `docs/`, `chore/`), open the pull request against `dev`.
Squash-merge is the default. Release tags are `vX.Y.Z`.

`upstream-master` mirrors `raspberrypi/debugprobe` `master`; never commit to it.
Take upstream changes by merging an upstream tag into `dev`.

## Before you open a PR

Both targets must build with no warnings (commands in the README):

```sh
cmake -G Ninja -S . -B build && ninja -C build
cmake -G Ninja -S . -B build-pico -DDEBUG_ON_PICO=ON -DPICO_BOARD=pico && ninja -C build-pico
```

Say in the PR what you did and did not verify on hardware. A descriptor or
UART change is not verified by a successful build; "built, not enumerated on a
board" is a useful statement.

## Rules

- Keep upstream licence headers intact; new files are MIT, Copyright (c) Alp Lab AB.
- Do not add pin assignments for boards whose schematic is not fixed.
- Keep the substring `CMSIS-DAP` in the product string.
- No AI-assistant footers or co-author trailers in commits or PR bodies.
