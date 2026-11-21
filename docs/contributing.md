# Contributing

How to test the module.

## Testing

`tools/test-server/podman-test.sh` builds the module in a throwaway container from the
prebuilt test server image (`ghcr.io/dudemcbro/acore-test-server`, from
`apps/test-server-image` in azerothcore-wotlk-playerbots-custom), starts the servers and
runs `tools/test-server/testclient/sqo_smoke.py` with a headless 3.3.5a client: the sync
against the table, the quest id before an NPC dialog, and the GM commands. `--verbose`
prints every chat line.

To regenerate the quest data, see [Classification](classification.md).
