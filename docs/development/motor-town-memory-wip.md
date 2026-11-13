# Motor Town memory work in progress

Parked on `fix/native-memory-detection`; not submitted upstream.
Tested on the installed bb05e20 APK using Lab commit 14b5732d3 and native ARM64 guest overrides.

## Saved code

- fb4df65b2: detect Steam/game processes behind the explicit native loader and FEX wrappers; root raw-syscall zRAM control paths with DDNATV_ROOT.
- 14b5732d3: stage opt-in, one-session droiddeck-session and ARM64 libblsession.so overrides for testing.
- 74 shell/C checks passed; eight helper checks passed in the native guest. Android code compiled in the Lab build; new Robolectric tests were not run separately.

The APK Lab build alone does not replace the bundled runtime assets. The fixed script and rebuilt preload must be queued as overrides before each test session, or packaged into a future runtime. No upstream PR has been opened.

## Game settings retained

Steam app 1369670, Motor Town: Behind the Wheel 0.7.19, build 1111, Proton 11-12.6:

- Steam launch options: `-dx11 -nocef` (previously empty).
- GameUserSettings.ini: `sg.ResolutionQuality=60` (previously 80).
- Other scalability groups were already zero, mirrors hidden, frame cap 30, output 1608x720.

Verified the running game received both flags, used the DXVK HUD, loaded the world, and accepted movement. No EpicWebHelper processes remained. Browser-based pages may not work with nocef. Lower render scale softens the image.

A custom Engine.ini texture budget was tried, but the game removed the file at startup; its effect and persistence were not verified. It is not part of the confirmed settings.

## Observations, not a controlled benchmark

Before: MemAvailable 294500 KB, SwapFree 2967872 KB, GPUUsed 3222356 KB.
Final gameplay sample: MemAvailable 812496–857604 KB, SwapFree 6580432–6682628 KB, GPUUsed 2787200–2803520 KB. SurfaceFlinger measured 27.2 FPS over 20 seconds. Earlier DX12 gameplay measured 27.8 FPS. Streets, time of day, and session lifetime differed; do not attribute exact savings to an individual change or claim an FPS improvement.

The fixed reclaim monitor recognized Steam and game processes and logged successful page-outs with readback backoff. Page-out changes residency, not total allocations; swap figures are logical occupied swap, not physical compressed-memory savings.

## Resume on this device

Worktree: `/root/DroidDeck-memory-fix`. Local private backups and measurement artifacts: `/root/.cache/droiddeck/motor-town-low-memory/`; helper artifacts: `/root/.cache/droiddeck/memory-monitor-fix/`. Private config backups and user saves are intentionally not committed.

Build ARM64 helper inside a compatible ARM64 Linux environment:

```sh
cc -shared -fPIC -O2 -Wall -pthread -o /tmp/libblsession.so tools/linuxfs/preload/*.c -ldl
```

With the test Lab code active and debugging tools enabled, queue the script and helper with `droiddeckctl override` before starting Steam. Overrides last one session. Never launch Steam directly in the agent userland.

To undo game tuning, quit Motor Town, restore the private GameUserSettings.ini.before backup into the game's Saved/Config/Windows directory, and clear its Steam launch options. Saved games were untouched. Turning off the Lab build preserves app data and these separate Steam/game settings.
