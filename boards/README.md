# Boards

One directory per ECU on the car. [VCU/](VCU/README.md) is the reference
layout. Bring up a new board with the scaffolder — don't copy VCU's
`BUILD.bazel` by hand, it exists so boards can't drift apart:

```bash
bazel run //tools:new_board -- boards/<Name>/<Name>.ioc
```

(full recipe in [CONTRIBUTING.md](../CONTRIBUTING.md#adding-a-new-board)).
`bazel build //boards:all_firmware` builds every ECU's flashable images —
each new board's `:release` gets added to that filegroup.

Every board follows the same ownership split, which is what makes CubeMX
regeneration safe (see
[CONTRIBUTING.md](../CONTRIBUTING.md#regenerating-cubemx-code-safely)):

| Path     | Owned by  | Contents                                                          |
| -------- | --------- | ----------------------------------------------------------------- |
| `Core/`  | CubeMX    | Generated peripheral init. Never hand-edit or format.             |
| `Board/` | us        | `main.cpp`, clock config, LHAL adapter wiring.                    |
| `App/`   | us        | Application logic against LHAL only — runs in host tests and sims too. |

There are no per-year board directories: when hardware is scrapped, its
directory is deleted on `main` (history and season branches keep it — see
[CONTRIBUTING.md](../CONTRIBUTING.md#season-policy)).
