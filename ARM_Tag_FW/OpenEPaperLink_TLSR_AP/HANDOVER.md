# Handover: TLSR8258 AP radio firmware (state after `d3443c20`, 2026-11-01)

## Where to continue (2026-11-01, end of the memory-redesign session) - read this first

**Branch** `claude/cache-block-ram-optimize-bba10f` (worktree `.claude/worktrees/cache-block-ram-optimize-bba10f`),
tip **`55a4ac4c`**, nothing pushed, no PR. **On the radio: `55a4ac4c`, plain `make`, md5 `845681897c6345a7037bf08ec4e7572d`**,
hardware-tested 17:41-17:52 and good. Earlier good images, if a rollback is ever needed: rebuild any commit below
with plain `make` (builds are reproducible; the md5s are in the commit messages).

**What this session did, in order** (docs/design/radio-memory-rethink.md is the plan it followed, steps 1-5, then more):
`ed9d0142` one 48 MHz image, every settled build option and per-tag power removed (AP_DEBUG is the only switch) ·
`5c81fdb3` stack high-water `stk` line · `998dd593` S3 frames COBS-encoded into the UART DMA buffer, events + LOG in one
1 KB byte ring · `0240dd53` radio frames read in their DMA slot, built in the TX buffer, 4 RX slots ·
`71040485` (other session) SDK sleep/ADC/analog RAM code moved to flash, +768 B · `ace2bad1`/`69e9051a`/`07743947`/`1d05f328`/`94183b0a`/`9374d6f3`
(other session) listening bursts, learned waits, burst queue, receive-flag check (`fh` vs `fe`), CRC counter, silent-burst counters ·
`1c71c975` the arena is whatever RAM the link leaves, every maximum derived at boot, size options gone ·
`fcffe302` one 12-byte record per tag (+ 8-byte profiles, 16-byte offers) replaces allow-list, prefix table, pending
table and hwType table · `a6423778` both stacks sized at build time from the code (`tools/stackdepth.py`, two-pass
link, 4/3 margin; main bound ~824 B -> 1104 reserve, IRQ 116 -> 160) · `55a4ac4c` cache blocks stored at their own length.

**Latest hardware numbers** (13 tags, channel 26): `cfg ckb 53 tags 1024 pend 2176 ofr 340`; 24 preloads, 18 bursts all
from cache, 0 re-requests, 0 evictions, peak 13 blocks / 33 of 53 KB; `stk 776 irq 116`; `rep` ~3 %, `larm` 0-3 per run;
UART ring peak 924-940 of 1024; CRC failures 4-8 % of frames; `fh/fe` 1.06; first real yield seen (`yld 1 ylf 1`);
`dx` learns 570-1300 ms; `sile`/`sils` 0-1 per run. Before this session: 9 x 4 KB blocks, 250 pending tags, 2384 B headroom.

**How it was tested** (repeat this after every flash): the web-log reader `tools/weblog.py 192.168.1.94 /tmp/oepl-weblog.log`
runs in the background (keep exactly one); `tools/ap.sh flash-radio <bin>` (its version check cannot tell our images apart -
confirm by the new `cfg` line instead); `tools/ap.sh refresh all`; then `cmd=scan` and later `cmd=reboot` via `POST /tag_cmd`
to the four closest tags (0000032C9E2D3E19, 00000335065D3E17, 0000021D2755341D, 00000335059F3E1A) and wait until each checks
in again; read `hw`/`stk`/`air`/`blk`/`ci`/`xfer`/`rf` lines and `tools/weblog.py --sum`. Never `/imgupload` to any tag: none
is cleared for test content since 2026-11-01 (BOARD.md). The tags that were skipped 4-block test images on every image, so no
42-part burst has ever run here.

**Open, roughly in order:**
1. **Docs lag the code since `d24502d3`.** README (RAM section, options, tests), docs/design/radio-sram-split.md (regions,
   bands, cacheSurrender, `cblk`), radio-memory-rethink.md (steps 2-5 done, measured numbers), BOARD.md/this file (`cb`/`cu`,
   `ckb`, `evd`/`larm` now on the `stk` line, `sile`/`sils` on the `ci` line). Each agent's commit message lists its doc needs.
2. **Small code leftovers:** AP_DEBUG frequency-offset accumulators nothing reads; stale comments (old 6-line LOG queue,
   removed crystal sweep); `halRadioSetXtalCap()` nearly dead; `prof` on the `cfg` line is always 0 because that line goes
   out before the allow-list arrives - report it after the list; `_cache_arena_need_` in blockcache.c still reserves
   8 B/tag where the tag store guarantees 20; host_hal.c's default arena is a fixture that no longer equals the firmware's.
3. **A long `AP_DEBUG=1` soak** (hours, heavy S3 traffic, link restarts): stack marks against the derived reserve, `rep`,
   `larm`, `sile` vs `ebst`, `crc`, slowest request `rq` (up to 2.2 ms seen).
4. **S3 round (agreed for later):** send each tag's hwType in SET_PENDING (tags are typeless after a radio reboot until a
   full check-in), count preload needs in bytes, preload in order of each tag's next check-in, per-tag power as a
   SET_CONFIG flag if ever wanted. Radio-side the protocol was not changed at all; the C6 is untouched.
5. **Decisions for the owner:** UART RX ring 1024 holds the S3 window minus one frame (full window 1056 would need 2048);
   main-stack margin 4/3 is the only reserve left.
6. Agent worktrees `.claude/worktrees/agent-*` are leftovers and can be cleaned.


Notes for whoever continues this work (a person or a new Claude session, e.g. a local one with the
AP on USB). `README.md` describes the firmware; this file is the working memory: what was tried,
what was learned on hardware, what is open.

**The one thing to read first.** A cold tag still does not reliably join this firmware while it joins
the stock image at once, and that is the only thing wrong with it that anyone has seen. The cause is
now cornered rather than suspected: **power, the carrier, the channel, the frame we send, its
addressing, our own receive path and — since the stock image was disassembled against ours —
`rf_drv_init()`'s registers are all eliminated by evidence**, recorded under "the one that matters"
below. We answer pings, 80 of them in one scan, and the frames go out complete.

**The next step is a measurement, not another guess.** Three fixed PONG offsets have been tried and
all three failed against a tag three feet away — 970, 700, and 700 again once it turned out the
"fix" had edited a `#ifndef` the makefile was overriding, so no image had ever carried it. In every
one of them the per-ping line of an `AP_DEBUG` build showed a frame correct in every field, which
says the fault is not in the
field we compute but in the clock we compute it from. So `RADIO_PONG_SPREAD=1` walks the offset
across the tag's window instead of naming one, turning a scan's twenty attempts into twenty
independent trials. (It is **off** by default — `7226343f` put a single 1700 us offset back, since a
correct offset is heard on a tag's first attempt. `ap.c` claimed a default of 1 until `09390213`
while every image was built with `-DRADIO_PONG_SPREAD=0`, which is the same trap as the 970/700 one
below: read the makefile, not the `#ifndef`.) **Flash that build, power-cycle a tag, and read the `at` value an
`AP_DEBUG` build's per-ping line reports for the attempt that works.** That is the number nobody has been able to derive from the
datasheet or from the stock disassembly. If it still will not join, the fallback is the tag's own
3-deep receive queue filling with other tags' burst frames, and there is an experiment for that
below. Do not spend time re-excluding the seven things already excluded. (The spread has done its job
and is gone: it found the window opening after the tag's acknowledge wait, fact 17, and was removed in
`ed9d0142`. The PONG goes out at the fixed 1700 us, a constant in `src/ap.c`.)

## Goal

Replace the closed-source `binaries/TLSR/OEPL_TLSR_AP.bin` (AP type `F8`, reports version `007c`) for
the **OpenEPaperLink Nano TLSR** (ESP32-S3 + TLSR8258, env `OpenEPaperLink_Nano_TLSR`) with an
open-source firmware at the level of the ESP32-C6/H2 radio: protocol v2 at 2 Mbaud, block cache,
allow-list, diagnostics. Owner's test setup: 14 tags in the allow-list, all ZBS243 (Solum M2,
hwType `0x00`–`0x27`), S3 firmware with V2Transport. Note the range: `hwType < 0x2E` was used as the
M2 test for a long time and is wrong — `0x28`–`0x2D` are M3 tags — which is the bug `85b34e4d` fixed.
There are no M3 tags here, so every M3 path in this firmware is host-tested only.

## Where things are

| | |
| --- | --- |
| Firmware | `ARM_Tag_FW/OpenEPaperLink_TLSR_AP/` (`src/ap.c` AP logic ported from C6 `main.c` 0x0024, `src/v2link.c` protocol v2, `src/blockcache.c`, `src/hal.h`, `src/tlsr/hal_tlsr.c` all hardware) |
| SDK | `ARM_Tag_FW/OpenEPaperLink_TLSR/tl_zigbee_sdk` (V3.6.8.3, used in place, not copied) |
| Toolchain | submodule `ARM_Tag_FW/OpenEPaperLink_TLSR/tc32_linux` (flyskywhy/tc32, branch `linux`: tc32-elf-gcc 4.5.1, "Telink TC32 version 2.0"), which is the makefile's default `TC32_PATH`; Windows copy in `../OpenEPaperLink_TLSR/tc32_windows` |
| Telink 802.15.4 SDK | submodule `ARM_Tag_FW/OpenEPaperLink_TLSR/telink_802154_sdk`, reference only (its MAC shows the canonical TX/RX turnaround); nothing links against it |
| Host tests | `make -C test` (ASan/UBSan, `test/test_ap.py` plays the S3 and the tags, `test/test_string_min.c` the word-at-a-time `memcpy`; 45/45 pass) |
| Analysis tools | `tools/tc32dis.py` (TC32 disassembler), `tools/fingerprint.py` (match SDK functions in a .bin), `tools/listing.py` |
| Test binaries | `binaries/TLSR/OEPL_TLSR_AP_0080_*.bin` (table below); `firmware_TLSR.json` still points at the closed-source image on purpose |
| S3 side | `ESP32_AP-Flasher/src/v2transport.cpp` prints LOG frames as `radio: …` and forwards them to the web log via `RX_CMD_LOG` 0x0A (`serialap.cpp`, `include/radiotransport.h`). Built and flashed on the owner's AP, and observed delivering whole lines to the web log; it has never been built here, so treat a build break in that file as plausible after a merge |
| Design notes | `docs/design/radio-protocol-v2.md`, `docs/design/radio-chain-*.md`, `docs/design/radio-tag-timing.md` (what each tag family does while it waits, and the receiver-on ledger every timing choice is argued in), `docs/design/radio-findings-backlog.md` (every finding, with what landed and what was rejected and why), `docs/design/radio-stock-binary-diff.md` (the closed-source image disassembled against ours) |

Build the current test configuration:

```
cd ARM_Tag_FW/OpenEPaperLink_TLSR_AP
make clean && make AP_DEBUG=1
```

Since `ed9d0142` there is nothing else to choose: the clock, the TX mode, STX replies, UART TX DMA,
early bursts and the applied power are fixed at the values the old flag set picked, and the timing
figures are constants in the source.

No `TC32_PATH` is needed: the makefile defaults to the `tc32_linux` submodule and falls back to the
copy in the main checkout, which is what makes this build in a worktree.

`AP_DEBUG=1` is **the** switch for every instrument in this firmware, and it is what makes the build
above the one that says what was in each PONG. It replaces `AP_LOG_ALWAYS`, `PING_LOG` and `FO_LOG`,
which were three switches where one will do — a dozen of them was a dozen chances to ship an
instrument by accident, and in a measurement build you want all of them anyway. Nothing it compiles
changes what goes on the air; the two sweeps that did (the check-in reply offset and the crystal cap)
were removed in `ed9d0142`.
Leave it off for anything flashed to stay: a production image carries no instruments, which is also
why turning them off does not free a cache block — all of it is 48 bytes of `.bss` against a block's
4104 (see the RAM section of `README.md`). Per-tag transmit power was removed in the same commit
(fact 11).

Flashing: the S3 flashes the TLSR over SWS (pvvx-style floader loaded into TLSR RAM at 0x40000, CPU
halted via SWS reg 0x0602) — it works regardless of what the TLSR firmware does, so a bad image is
always recoverable. Upload the .bin to the S3's LittleFS as `/TLSR_AP_FW.bin` and flash it
(`/flash_tlsr` without a URL). PA7 must stay SWS (do not set `PA7_FUNC`).

## Test binaries and what they showed

All are version `0080`, protocol v2, same AP logic unless noted.

| binary | flags | result on hardware |
| --- | --- | --- |
| `beta` | 500 µs wait before every TX, no log | transfers work — the first proven build |
| `debug_fasttx` | + the diagnostics switch (`AP_DEBUG` now) | diagnostics; found the reply-timing problem |
| `test1_ram` | SDK ev_buf pools dropped (own `string_min.c`), UART RX error flag fix | ok |
| `test2_48mhz` | `SYS_CLOCK_HZ=48000000` | ok |
| `test3_replytiming` | replies at RX timestamp + 950 µs | ok, `gap` steady |
| `test4_combined` | + TX power, STX replies, UART TX DMA, early bursts | **link dead**: UART TX DMA bug (below) |
| `test5_combined_bytetx` | test4 with `UART_TX_DMA=0` | ok |
| `test6_dmafix` | DMA fixed + fallback, 3 short log lines, hwType-by-MAC | ok: `udma` rising, `udmaf 0` |
| `test7_logpacing` | test6 + LOG frames ≥ 250 ms apart | ok; see the log below |
| `test8_burstwindow` | test7 + this round's burst and reply timing (gap 600 µs, window budget, odd repeat spacing, ack-anchored start, mid-burst re-ack, PONG delay, fast ASCII fallback) | ok, current |

## Tag behaviour the burst is built around (read from the tag firmware, not guessed)

From `ARM_Tag_FW/OpenEPaperLink_TLSR/src/syncedproto.c` and the timing budget in
[radio-chain-analysis.md](../../docs/design/radio-chain-analysis.md). Every burst decision below
follows from these:

- `performBlockRequest()` sends the request and waits **50 ms** for the ack, up to 30 times. A
  `BLOCK_PART` arriving during that wait makes the tag stop waiting and start receiving
  (`continueToRX()`), but **that frame is thrown away** — the call that would have used it is
  commented out.
- With an ack it waits `pleaseWaitMs` and then runs `blockRxLoop(300)`: a flat window with the radio on
  throughout. A ZBS243 listens 315 ms and stops as soon as the block is complete; a TLSR8258 always
  listens its full 300 ms.
- How the wait is spent differs per tag: a ZBS243 with `pleaseWaitMs < 35` busy-waits `pw − 5` **with
  its radio on**, a TLSR8258 busy-waits `pw − 10`, an nRF52811 sleeps `pw` with its radio off and only
  then starts it. So the earliest safe burst start is `pw` for an nRF and `pw − 5` for a ZBS243, both
  counted from the end of the ack.
- The tag copies and checksums each part before it reads the next frame, so frames sent back to back
  can overrun it. The closed-source AP left 500 µs plus a full `rf_drv_init()` between frames;
  `RADIO_BURST_GAP_US` (600) is this firmware's version of that gap.
- After the window the tag re-requests only the parts it is still missing, up to
  `BLOCK_TRANSFER_ATTEMPTS` (5) rounds. A round costs it another ack exchange and another 300 ms of
  radio, so repeats inside the first burst are far cheaper than a second round.

## Hard-won facts (do not re-learn these)

1. **Reply timing.** ZBS243 tags miss replies that start less than ~0.6 ms after the end of their
   request. The closed-source firmware's TX wrapper does `sleep_us(500)` before `radioTxLL()`. We
   schedule the reply at request end (RX hardware timestamp) + `RADIO_REPLY_AT_US`, now **1220 µs**;
   the radio's state machine starts it (`rf_start_stx`, the only reply path since `ed9d0142`). Ruled
   out on the way: TX power, fast vs legacy TX, frame bytes, GPIO.
   **Two corrections to this fact, both later than it.** The number was once written as 950 while the
   frame really left at ~1220, because the code added the driver's 120 µs software wait and ignored
   the hardware's own ~150 µs TX settle; the settle is read from `0xf04` and subtracted now, so the
   constant is a real air-start delay and the old `gap 1070` reading is not comparable with today's.
   And the "~0.6 ms turnaround" in the first sentence is **not in the tag firmware** — `detectAP()`
   and `getAvailDataInfo()` enable the receiver before their attempt loops and leave it on, so an
   earlier reply is strictly safer for the tag. What remains true is the *measurement* (at a true
   950 µs, 6 of 7 check-ins repeated); what is no longer true is the explanation for it. See next
   step 3.
2. **UART TX DMA** (test4): `dma_chn_irq_enable(FLD_DMA_CHN_UART_RX | FLD_DMA_CHN_UART_TX, 0)` in this
   SDK also clears the channel enables in `reg_dma_chn_en` (0xc20). Now: explicit enable, Telink's
   start sequence (0x9f=0x80 clear, addr 0xc04/0xc41=0x04, size, 0xc20|=2, 0xc24|=2), DMA only above
   115200 baud (ASCII handshake is always byte TX), timeout 2× line time + 5 ms → byte TX for good,
   registers 0xc20/0xc24/0x9c/0x9e captured into `udmar`.
3. **RX timestamp** in the RX trailer at `p[phr+5..8]` needs `ZB_TIMESTAMP_ENABLE` (0x430 bit 1); an IRQ
   lag sanity check (< 3 ms) falls back to IRQ time (`tsfb`, so far always 0).
4. **The S3 console drops bytes.** Lines from the radio arrive intact (CRC-checked frames), but the S3
   prints them with `Serial.print` from the reader task while other tasks print (`ping ok`), and
   stretches go missing — mostly at the start/end of lines. Pacing (test7) helped but did not cure it.
   Fix on the S3 side (one `Serial.write` of a whole line, or rely on the web log), or — better for a
   local session — read the TLSR↔S3 UART directly (see next steps).
5. **"rx 0" ≠ RX broken**: tags sleep for minutes; a quiet log is normal. Force refreshes to test.
6. **Early bursts** need the tag's hwType at block-request time; only full check-ins carry it, so a
   64-entry MAC→hwType table (`rememberHwType`/`knownHwType`) remembers it. `ebst` counts them.
7. **RAM is tight, and the split has now been decided once** rather than drifting. The stack top is a
   fixed `0x84ff00`, the linker holds 2048 bytes back for the stack (measured worst-case main-thread
   depth: **796 bytes**; interrupts run on a separate 512-byte `irq_stk` and use 116 of it), and what
   is left is reported as `_bss_headroom_` — **5880 bytes** in a default build, 5316 in the
   `BOARD.md` site build at the time (today, with the arena taking that room, 680 in `make` and 632 in
   `make AP_DEBUG=1`), after 3192 bytes came back from going over the layout (backlog section F,
   which also carries the savings that were examined and refused — worth reading before re-deriving
   one, because the obvious places are now used up). `CACHE_BLOCKS` stays 7, `MAX_ALLOWED_MACS` 1024
   and `MAX_PENDING_MACS` 250: the full reasoning, the cost of every option and the matrix of
   (flag set × `CACHE_BLOCKS` × `MAX_ALLOWED_MACS` × power controller) against what links are in the
   RAM section of `README.md`. **Every configuration there links except nine cache blocks**; the
   `BOARD.md` recipe is at the default 7 blocks with about 5 KB of headroom, and the heaviest build
   anyone has reason to make — that recipe plus every instrument, both sweeps and per-tag power — has
   5100. An eighth block used to be 2132 bytes beyond the site build, which was 267 allow-list
   entries and the whole reason the README refused it; it now links with the full allow-list in every
   flag set, with 988 to 1704 bytes to spare, so that trade is simply off the table. 7 is still the
   default because of what a block is worth rather than because of what fits. The old formula rounded
   the stack top up to a 256-byte boundary,
   which is why `.bss` sat four bytes from a cliff, why `UART_TX_DMA=1` (640 bytes) would not link
   unless the diagnostics switch was in the same build to shave 8 bytes off, and why the recipe was given
   `CACHE_BLOCKS=6` — 4184 bytes spent on a four-byte problem, because nothing showed it was one.
   Every instrument is now one makefile option, `AP_DEBUG`, with a `-D` on every compile — the
   per-ping line was once only a `#define` in `ap.c`, so `make PING_LOG=1` silently did nothing.
8. Version must stay > `007c` or the S3 offers the closed-source image as an "update".
9. **The frequency offset's layout is settled; its unit is not, and may be unmeasurable here.** The
   trailer bytes are `pkt_fdc[7:0]` at `p[phr+9]` and
   `{1'b0,rx_packet_chn_efuse[2:0]},{1'b0,pkt_fdc[10:8]}` at `p[phr+10]`, from Telink's own handbook
   table — so the offset is eleven bits and the byte above it is a channel field, **not** a receiver
   gain. The old readings of ~16400 were all sixteen bits; ~0x40 of that was the channel field.
   The **sign convention and the Hz per LSB are documented nowhere and no code anywhere reads the
   field**, so they can only be measured, and the handbook's phrase "after demodulation" suggests the
   value is the residual left by the receiver's own carrier recovery — which would explain why 42 cap
   codes moved it by almost nothing and would mean it cannot see our own carrier at all. `fo` is a
   relative per-tag number with no unit; nothing keys on it. Section C of
   `docs/design/radio-findings-backlog.md` has the table, the reasoning and the measurement to take.
10. **Transmit power is set between frames, not when it is asked for.** `halRadioSetTxPower()` only
    records the value; the driver writes the PA registers in `radioApplyTxPower()`, after
    `radioWaitTxFree()` and before the frame is handed to the radio. Those registers belong to the
    frame on the air - `rf_set_power_level_index()` writes 0x1225 (supply), 0x1226 bit 7 and 0x1227
    bits 0..4 (the PA code) with no regard for what is transmitting - and a `SET_CONFIG` can ask for a
    new one while a block part may still be going out. (Per-tag power, which asked for one per frame,
    is gone; the deferral stays.) The call itself is
    about forty instructions, nothing against the ~650 us of slack a scheduled reply has.
11. **Per-tag transmit power was removed in `ed9d0142`, unshipped.** It learned only from check-in
    replies but lowered the power of every frame to the tag, block parts and acks included, where a
    loss costs a request round rather than a retry; and what it buys - less interference to a
    neighbour on our channel - is something only the S3 knows a site has. If it comes back, it is as
    a `SET_CONFIG` flag. The controller, its reasoning and its five host tests are in `ed9d0142^`.
12. **Its diagnostics line went with it** (`pwr … tags … dn … ap … up … bar …`).
13. **`rep` is not 0 on this installation.** Counted over a whole log rather than its tail: 16.7 % of
    check-ins repeated over the first 4100 s and 4.0 % in a later window, all of it at full power,
    while `late` genuinely is 0 in every build. Anything that treats a repeat as evidence about our
    own transmit power has to allow for that rate as background, which is why the (since removed)
    per-tag power controller's bar on a step was recoverable rather than permanent.
14. **The 64-entry MAC table holds `hwType` and nothing else** since per-tag power went. At 200 tags
    that table is evicted every few minutes and most tags read as unknown, which means the safe
    answer - the conservative burst timing - rather than the wrong one. `HWTYPE_TABLE` is the knob, at
    9 bytes an entry, so doubling it to 128 costs 576 bytes of the 680 of headroom. The table is
    columns rather than an
    array of a 9-byte struct, which costs the same bytes and makes a lookup two word loads instead of
    a `memcmp` call over eight byte loads — see backlog F3.
15. **The air-time reference has been audited, and most of it is confirmed - so suspect the premise,
    not the arithmetic.** `0xf04` is the TX settle in microseconds (handbook: default 150, minimum
    112.5; reads 149 here), the scheduled tick fires on greater-than, `clock_time()` is a constant
    16 MHz at both clock speeds so there is **no scale error anywhere**, and `airtimeUs()` is exact.
    The RX timestamp's capture instant is documented nowhere, but it is bounded: `tsfb` being 0 proves
    the counter is sampled in the frame header, and the header is only six bytes, so that assumption
    is worth ~32 us of lateness and at most 192 us of error. **The one unmodelled term is `0xf16`
    bits 0 and 7..4, a transmit-enable delay**: `rf_drv_init()`'s tables never write the `0xf00` block
    at all (only `rf_start_stx` and `rf_start_srx` touch it, for one register each), so it sits at
    chip reset and is added to every scheduled reply. It is reported at boot as `lld` now.
    **"Suspect the premise" was the right conclusion and fact 17 is what it was pointing at**: no
    source could account for the lateness because there was none on this side. Our clock is right -
    `lastRxEndTick` is right, and `usTicks` is `32 * sys_tick_per_us`, i.e. ticks per BYTE, so
    `(phr + 1) * usTicks` really is `(phr + 1) * 32 us` exactly as its comment says. The one piece of
    this audit still worth reading is the timestamp bound, because that is what ruled the receive side
    out and sent the search to the tag.
16. **Retired by fact 17: `nf`/`busy` are fine and `NF_TX_RECOVER_US` needs no change.** The audit
    reasoned that `sampleNoise()` holds off only 400 us after `halRadioTxEndUs()`, and that
    `txEndTick` is the armed tick plus the settle - so it would be early by any unmodelled transmit
    delay, and at 500 us the guard would be consumed and the idle RSSI sampled during our own frame.
    That was conditional on a delay which fact 17 shows does not exist, so the guard is the guard it
    always was. Kept because it is the ledger to reuse if one is ever found, and because the drift in
    the noise floor from -86..-94 to -80..-92 is therefore still unexplained and belongs to something
    else. `tooLateToAnswer()`, the burst anchor and every millisecond-scale constant were priced in
    the same audit and are not materially affected either way; section C of the backlog has each.
17. **A tag's reply window starts after its acknowledge wait, and that is measured, not theorised.**
    A scanning ZBS243 accepted a PONG at **1350 us** after the end of its ping and ignored 150, 300,
    450, 600, 750, 900, 1050 and 1200 - three scans, nine pongs each. Its `radioTx()` ends
    `while (!mAckTimePassed);` and `sendPing()` sets `ackReqd`, so the 802.15.4 acknowledge wait
    (864 us from the end of the frame) runs before the call returns and before `detectAP()` takes the
    timestamp its 2 ms window is measured from. **During that wait the tag's radio cannot receive**,
    so a reply aimed inside it is destroyed in hardware and leaves no trace on this side at all.
18. **It does not generalise, and the re-reading says which families it touches.** Checked line by
    line across all six 2.4 GHz families (`docs/design/radio-tag-timing.md`, "Where a window actually
    starts"): only the **ZBS243** and the **88MZ100** set the ack-request bit on a ping or check-in
    *and* block on the wait. The **Opticon** - the other 2 ms family, and the obvious thing to fear -
    sets `ackReqd = 0` on all four of its transmit paths and ends its transmit at the end of the
    frame, so it is clean. The nRF52811, the TLSR tag and the EFR32 are within ~200 us of the end of
    the frame. Both affected families set `ackReqd = 0` on the **block request**, so that path's
    window really does open at the end of the frame - which is why the burst ack has always worked.
19. **This is the explanation for `rep`, and it says the check-in reply is parked on the wrong side of
    an edge.** A check-in sets `ackReqd` exactly as a ping does, so its window opens ~1250 us after
    the frame too - and `RADIO_REPLY_AT_US` is **1220**, i.e. 30 to 130 us *before* the tag starts
    listening. `rep` at 4-17 % is not random loss; it is the jitter in that edge deciding each time
    whether the tag was listening yet. It also explains the measurement that pinned 1220 (at 950, 6 of
    7 check-ins repeated - 950 is squarely inside the wait), and the thing nothing else explained:
    **the stock firmware works because it is slow.** It re-initialises the whole radio per frame, a
    flash read among it, on top of `sleep_us(500)`, which lands its reply past the ack wait by
    accident. Budget across every family: **[1250, 2396] us, and ~1700 sits 450 us inside both
    edges.** The default is deliberately left at 1220 - see next steps.
20. **`tooLateToAnswer()` does NOT need re-deriving, and that is the surprise.** Two errors cancel:
    `ageUs` runs from the start of the tag's frame, so it already carries that frame's airtime
    (832-1504 us), which the old derivation treated as if measured from the frame's end - too strict
    by that much - while the window opening late is too generous by ~900-1250. For the binding family
    (88MZ100, 4 ms, three tries, truncated to 3.0) the limit should be 3900 and it is 4000. What the
    re-referencing exposes instead is a **gap**: there is no too-early guard anywhere, and earliness
    is the failure that was measured. Earliness is fixed by the reply delay, not by a check.
21. **`nf` was never a noise floor and the channel is bimodal.** A stochastic median that walks one dB
    per sample cannot sit on a bimodal distribution - it wanders between the modes - so the -97 to -71
    swing from window to window was the estimator's position, not the channel's. Ruled out with
    numbers: our own transmissions (correlation with frames sent is +0.02 over 455 windows, and loud
    windows have the same median tx and rx counts as quiet ones, which also clears
    `NF_TX_RECOVER_US`), the firmware (identical across a complete image change), and our own fleet
    (`rx` medians 2 frames a window, 0.013 % of the airtime, which cannot make 73 % occupancy).
    Dropping the ESP's WiFi power moved it only -73 to -75. Replaced by an eight-bucket histogram,
    reported as `nf` in hex; `busy` is unchanged and is still the only noise number that decides
    anything.
22. **The RSSI is gain-compensated, so the metric measures the channel - but compensated is not
    comparable.** Settled four ways (datasheet specifies ±1 dB and explicitly blesses reading the
    register for channel energy with no packet present; `rf_drv_init` programs a six-entry per-gain
    offset table at `0x0460`-`0x0465`, 54/70/81/97/109/120, spanning 66 dB with its top entry at the
    top of the 0..120 code range; nothing in the whole driver library touches a gain register; both
    chip generations carry one RSSI byte and no gain byte). So `p[phr+11] - 110` and
    `rf_rssi_get_154()` are the same sound quantity and the per-tag power controller is fine.
    **But** the AGC locks during RX settle and then tracks, so at an idle sample with no packet the
    gain sits wherever the last frame left it, and that offset table's own steps are 11-16 dB - the
    same order as the ~23 dB between the two modes. The bimodality may be two gain states rather than
    two signal levels. Section C of the backlog has the evidence and the one-register test.
23. **No gain steering, and the fleet is the reason.** Neither SDK generation exposes any receive-gain
    or AGC API on any TC-series part - no fixed-gain mode, nothing but the B91 RSSI latch's name - and
    the register third-party 826x maps call `reg_rf_rx_gctl` is named in no Telink header, written by
    nothing, and sits at an address the other positional reading makes the frequency-offset latch.
    Pinning the gain *while sampling idle RSSI* is the one idea worth keeping, and it waits on the
    measurement above. Steering it for *reception* is the wrong direction outright: every tag weaker
    than about -55 dBm has stopped checking in while the strong ones are fine, so anything that costs
    sensitivity costs exactly those tags, and nothing in the diagnostics would attribute it to us.

## Diagnostic log lines (every 15 s on activity, else every 5 min; `AP_DEBUG=1` to send them unasked)

```
radio: ci C data D rep R us AVG max MAX gap GAPMAX late L sile E sils S
radio: xfer brq B bst S ebst E xfc X png P
radio: rf rx RX crc C tx TX txto T drop DR bdrop B unh U tsfb F busy PCT [nf HHHHHHHH] udma N udmaf 0|1 [udmar HEX]
radio: blk N parts K p p p …          (per block request: which parts the tag asked for)
boot pa HEX settle US lld HEX pwr DBM rst R rstev HEX xtal HEX   (once, from the first window)
ping MAC ch C age US len L larm N rs DBM at US   (AP_DEBUG builds, one per ping, rate limited)
joined MAC after N last US rs DBM             (AP_DEBUG builds, one per join: the offset it accepted)
fo MAC cap C o OFF rs DBM hi XX               (AP_DEBUG builds, one per check-in)
```

`ci` check-ins answered, `data` with data pending, `rep` check-ins repeated within 300 ms (tag missed
our reply), `us` request-start→reply, `late` requests too old to answer, `brq` block requests, `bst`
bursts, `ebst` early bursts, `xfc` transfers completed, `busy` the share of idle channel samples
above -80 dBm. The bracketed `nf` on the `rf` line is `AP_DEBUG` builds only: the eight-bucket noise
histogram (see BOARD.md for how to read it). On the per-check-in `fo` line, `hi` is the raw high byte
of the frequency-offset field, and it should have bits 7 and 3 clear (`hi & 88 == 00`).

On the boot line, `lld` is `0xf04` (TX settle, µs), `0xf16` and `0xf03` packed as `ssssddtt` — the
link-layer registers `rf_drv_init()` never writes, so they are chip reset values. `0xf16` bit 0 and
bits 7..4 are a transmit-enable delay that nothing sets and that our timing model does not account
for; if a scheduled reply leaves later than it was asked to, this is the one place the datasheets
leave room for it. On the `ping` line, `at` is the offset that PONG was armed with (1700 since the
spread was removed) and `larm` is read *after* arming, so it says whether that PONG kept its schedule.

## Last hardware log (test7) — summary

- Link: `ping ok, 2–3 ms`, `udma` 22→186, `udmaf 0`. DMA is good.
- Reply `gap 1070` constant; `rep 2` out of `ci 4` in one window (two tags re-sent once — watch this).
- Tag `…32C91C23E17`: request for block 7, one burst, XFC. Clean.
- Tag `…33467B43E16`: block 0, all 18 parts requested, early burst (`ebst 1`); XFC not yet seen when the
  log ended.
- Noise floor average −80…−92 dBm (min −103…−109): busier than earlier runs.
- Several `radio:` lines still lost their start or end on the S3 console (fact 4).
- Second log: `…21D2F69341A` blk 0 (10 parts) and `…32C73D23E1A` blk 0 (14 parts) requested within
  a second; `brq 2 bst 1 ebst 1 xfc 1` in that window (341A completed). Check whether the second
  request got its burst in the next window (that line was lost) — `brq` > `bst` may also mean a
  burst was skipped while another was running. No single-part re-requests seen in tests 6–7 so far.
- Console interleaving is visible directly (`queue: totradio: blk …`, `pingradio: xfer …`): other S3
  tasks print in the middle of a `radio:` line, confirming fact 4 is on the S3 side.

## This round's changes (burst and reply timing)

| change | why |
| --- | --- |
| Burst start timed from the end of the ack on air (`halRadioTxEndUs()`), and from where each tag type starts reading | it was timed from the request, about 2 ms earlier, which ate into the 1 ms margin of an early burst |
| `RADIO_BURST_GAP_US` 0 → 600 | the tag copies and checksums each part before reading the next; back-to-back frames can overrun it |
| Burst length from the tag's window (`BURST_WINDOW_MS` 260, ≈ 52 frames) instead of a 42-frame cap | a request for a whole block (42 parts) used to get one copy of each part, so any single loss cost a whole request round |
| Repeats spread by an odd number of frames | repeating the list unrotated put both copies of a part an even distance apart whenever an even number of parts was asked for, so the same parts were missed twice |
| A request for exactly what the running burst is sending gets the ack again with `pleaseWaitMs` 0 and the burst runs on (`reack`) | seen on hardware: the tag missed the ack, and the old code threw the burst away and sent all of it again |
| `RADIO_REPLY_PONG_US` 700 | a ZBS243 scanning channels looks for the `PONG` for 2 ms; at 950 µs the reply only finished after ≈ 2.06 ms |
| v2 → ASCII fallback also on bytes without a valid frame (1.2 s), not only on 5 s of silence | after an S3 reboot the radio is still at 2 Mbaud and the S3 at 115200, so the S3 had to reset the radio to find it |
| New counters `rereq`, `rparts`, `reack`, and a `rereq blk … pos …` line | the burst positions of the re-requested parts say whether losses are at the start, every second frame, or random |

`rereq`/`rparts` per `bst` is the reliability number to watch: each avoided request round is about
300 ms of tag radio.

## What this round measured on hardware

14 ZBS243 tags (hwType 0 and 4), channel 26, noise floor −86 to −94 dBm. Over the run with the new
firmware: **116 check-ins, 17 transfers completed, 13 bursts, 1 re-request (2 parts)**, and `late`,
`drop`, `txto`, `udmaf` all 0. The reply gap sat at exactly 1070 µs throughout, early bursts fired for
10 of 13 bursts, and 5 of 116 check-ins were repeated (4.3 %, the WiFi interference of fact 4 in
[radio-chain-results.md](../../docs/design/radio-chain-results.md), not something the radio controls).

The one re-request is the interesting one: a full 42-part block went out as a **52-frame burst** and the
tag came back for 2 parts, which had been sent at **burst positions 2 and 14** — both early, both single
copies, both at an even position. That is what moved the repeat pass to the front of the list (it had
been rotated to the middle, so the parts most often missed were the ones with no second copy). **That
fix is covered by the host tests but has not yet been seen on hardware**: reproducing a 42-part block
needs an image that compresses badly, and the S3's content pipeline kept replacing the test image with
the tag's own content before it was delivered. Push a poorly compressing image to a tag the owner has
cleared for testing - none is, since 2026-11-01 (see below) - and watch `rereq`/`rparts` and the `pos` list.

Diagnostics now come from the **web log**, not the USB console: the S3 forwards LOG frames to it
(`RX_CMD_LOG`, which now compiles and is flashed on the owner's AP), and lines arrive whole there.
`tools/`-style helpers used this round, worth rebuilding if needed: a websocket reader for
`ws://<ap>/ws` that writes `{"logMsg":...}` lines to a file, and a parser that sums the counters and
prints the `rereq ... pos` positions with their parity.

**The owner's AP is a live install.** Its tags carry hand-made per-tag content configuration
(`modecfgjson`: RSS feeds, a Google Calendar apps-script URL, weather location, energy tariff,
counters). `POST /imgupload` overwrites a tag's `contentMode` and `modecfgjson` wholesale; it has cost
tags their configuration twice, restored each time from a backup. Before anything that changes
stored state: read what the endpoint does in `ESP32_AP-Flasher/src/`, run `tools/ap.sh backup` (into
the main checkout's git-excluded `oepl-backups/`, BOARD.md), and ask. For radio testing
`POST /tag_cmd cmd=refresh` is safe — the tag redraws the content it is already configured for. No tag
is cleared for test content: the three that were (`0000032C91C23E17`, `0000032C73D23E1A`,
`0000033467B43E16`) had their configuration restored on 2026-11-01.

## The one that matters: a cold tag struggles to find this AP on our firmware

**Status: still open. One real defect found and fixed, which is NOT enough to explain the symptom.**

What follows is in the order it is worth reading: what is established, what is not, then the symptom
as it was first seen and everything that has been excluded since. Two earlier premises have been
removed rather than left standing — this radio *was* once said never to have answered a `PKT_PING`,
and the frame *was* once said never to reach `sendPong()`; both are now known to be false, and the
80-PONG scan below is why.

### Established

- **We do answer pings.** A tag rebooted next to the AP produced `png 60` then `png 20` in
  consecutive windows - 80 PONGs - with `unh 0`, `bdrop 0` and `rf rx` moving 7 -> 94. So the frame
  reaches `sendPong()`, is built, and goes out. `png` being 0 for the whole earlier session meant only
  that **no tag was cold-scanning while anyone was watching**: a paired tag goes straight to a
  check-in on its stored channel and never pings. Reading that as evidence about the ping path cost an
  hour, and the third row of the table below - "we do answer, and the tag is not hearing the PONG" -
  was the right one all along.
- **The PONG delay had regressed.** It was designed at 700 us and was 970 us. `PING_REPLY_WINDOW` is
  2 ms (`Tag_FW_ZBS243 tag_fw/powermgt.h`, 20 attempts) and our PONG is 992 us on air (25-byte PSDU),
  so at 970 us it finished 1962 us in - **38 us of margin**. The change to 970 came in `c3b7cc3b`, a
  commit about CRC tables and code generation, so nobody chose it.

  **`f27ae53e` did not actually fix it, and no image before this one ever ran at 700 us.** That commit
  edited the `#ifndef RADIO_REPLY_PONG_US` fallback in `src/ap.c`, but `makefile:106` still said
  `RADIO_REPLY_PONG_US ?= 970` and the makefile passes `-DRADIO_REPLY_PONG_US=...` on every compile,
  so the `#ifndef` block never expanded and the edited line was dead code. The flashed "fixed" image
  still sent PONGs at 970 us - which is why a tag on it answered 40 PONGs and still would not join.
  The makefile default is now 700, verified in the built image (the literal `0x3ca` is gone; the call
  site computes `175 << 2`). Full analysis, with the stock binary it was found against:
  [docs/design/radio-stock-binary-diff.md](../../docs/design/radio-stock-binary-diff.md).
- **Tags do not turn their radio around after transmitting.** `detectAP()` and `getAvailDataInfo()`
  both call `radioRxEnable(true, true)` *before* their attempt loop and leave the receiver on across
  it. So for the tag an earlier reply is strictly safer, and the "0.6 ms turnaround floor" that the
  comment on `RADIO_REPLY_AT_US` used to assert as the reason for these constants **is not in the tag
  firmware**. Which means the check-in measurement behind 1220 us (at a true 950 us, 6 of 7 check-ins
  repeated) is unexplained by anything the tag does, and the suspect is this side - see the note on
  `halRadioTxReply()` below.

### Not established - the fallback hypothesis, and why it was once the front-runner

**Read this with the correction above in hand.** The argument below was made against an image thought
to be sending PONGs at 700 µs, which none ever was; at 970 the PONG sits at the very edge of *every*
attempt rather than being late for one, so it no longer demotes the delay. Kept because the mechanism
is real and is the fallback if a genuine 700 µs build still does not join:

- `commsRxUnencrypted()` dequeues from a **queue** (`radioRxDequeuePktGet` /
  `radioRxDequeuedPktRelease`, a 3-deep ISR ring) and `detectAP()` flushes **before** the loop, not
  between attempts. So a PONG that misses attempt *c*'s 2 ms window is still queued and is read by
  attempt *c+1*. A thin margin therefore costs the tag one 2 ms attempt, **not a failed scan**.
- If something else stops a tag hearing 80 PONGs, **the hypothesis is that 3-deep queue
  filling with other tags' traffic.** During a block burst a frame leaves this AP every ~4.9 ms
  (`RADIO_BURST_GAP_US` 600 plus 4.3 ms of a block part's airtime; the 992 us once written here is a
  PONG's) for 42-52 frames. A scanning tag receives all of
  them - they are not addressed to it, but nothing filters them out before the queue - so three
  arrivals fill the ring and our PONG is dropped by the tag before `detectAP()` ever looks at it. Our
  own reply also cannot leave until `radioWaitTxFree()`, so a ping arriving mid-burst is answered late
  as well.
- **The experiment that separates them:** power-cycle a tag while no transfer is running, and again
  while a 42-part burst is going out. If it joins at once in the quiet case and not in the busy one,
  it is the queue, and the fix is on this side - a burst should yield to a scanning tag, because a tag
  that cannot join burns 40 ms of receiver per scan round for ever, while the downloading tag pays one
  re-request.

A latent bug found alongside this, in `halRadioTxReply()`: the clamped tick `start` was computed for a
reply whose processing had overrun its lead, and then **`due` was armed on the hardware anyway**.
`rf_start_stx` matches the tick for equality, so a tick already in the past is not matched until the
32-bit system timer wraps, four minutes later. Now `start` is armed. This is the best candidate for
the 950 us check-in failure above, since a check-in reply takes far longer to build than a PONG.

Observed 2026-10-31. Tags that had gone flat and were re-powered sat on "No AP found" against our
firmware. Flashing the **stock closed-source image (0x007C)** made three of them join immediately -
one that had been sitting on "No AP found" went to "Waiting for data..." the moment stock came up, as
did one that was then rebooted, and the first went on to update. So this is ours, not the environment
and not the tags.

What else is excluded, and how:

- Reception itself is fine: paired tags check in normally (`rf rx` advancing, `ci` every window,
  `rep` 0, `late` 0, `drop` 0). A **paired** tag goes straight to a check-in on its stored channel and
  never pings, which is why the ping path went unexercised for so long and why nothing caught the
  PONG regression before.
- `sendPong()` builds the same frame as the C6's, field for field, including replying on the incoming
  frame's `srcPan`. So neither the frame's contents nor its addressing is the difference.
- The lock is off, so `tagAccepted()` is not refusing anything.
- `tooLateToAnswer()` is not dropping them either: a ping discarded there increments `late`, and
  `late` is 0 everywhere, in every build. (It is also much more permissive for a PING now — the widest
  family window rather than the tightest, `19840 µs`; see section A of
  `docs/design/radio-findings-backlog.md`.)
- Nothing is being dropped silently on our side: `unh` counts every frame discarded for an unexpected
  length and every packet type with no case, and `bdrop` every frame the RX DMA lost. Both were 0
  across the 80-PONG scan.

Two more suspects are excluded by what the two firmwares have in common, not by measurement - which
is worth more, because it holds regardless of what the fleet does next:

- **Transmit power.** `rf_set_power_level_index()` bounds-checks `idx > 59` and returns silently, and
  the closed-source firmware calls it with **63** - so stock never sets a power at all and runs at
  whatever the chip reset left in the PA registers. On this hardware that is `pa 0000C81F`, i.e. PA
  code 0x3F, which is `rf_power_Level_list[0]` = **+10.46 dBm, the maximum**. Our builds ask for
  10 dBm, and `halRadioSetTxPower()` picks the highest step at or below 10.5 - which is that same
  +10.46 dBm entry. **Both firmwares transmit at the same power**, whether `RADIO_APPLY_TX_POWER` is
  on or off, so power cannot explain the difference.
- **The crystal, and therefore the carrier.** Both firmwares call `rf_drv_cap(0x77000)`, and that byte
  holds 0x60 on this module, so both run at cap code 32. **Same carrier frequency.** The cold-tag
  failures also persisted after the cap was parked back at 32, which says the same thing from the
  other direction.

**How this was localised, and what the localisation leaves.** The question used to be split four ways
by one power-cycle, reading `rf rx`, `png`, `unh` and `late` from that window: `rx` not moving would
have meant an RX configuration or channel difference from stock; `unh` moving, a length or type check
throwing the ping away before `sendPong()`; `late` moving, our staleness threshold refusing a
scanning tag; and `rx` with `png` both moving, that we answer and the tag does not hear it. That
experiment has been run. **`rx` and `png` both moved**, with `unh` and `late` at 0 — so it is the
fourth branch, and the first three are closed by measurement rather than by argument.

The one structural difference that branch left is **when the reply goes out**: stock sends the PONG as
soon as it has built it (after a full `rf_drv_init()`), while this firmware schedules it with
`rf_start_stx` at a fixed delay after the end of the request. That is where the 38 µs of PONG margin
and the late-arm bug were found.

**Read the order of events here carefully, because it was briefly concluded that the delay could not
be the cause.** The reasoning was: the tag does not flush its receive queue between scan attempts, so
a reply that is merely late costs it one 2 ms attempt and not a whole scan — which is sound, and it
is why the 3-deep-queue hypothesis was raised. But it was applied to an image believed to be sending
PONGs at 700 µs when it was still sending them at 970, because the fix had landed in a `#ifndef` the
makefile overrode. At 970 the PONG finishes 1962 µs into a 2 ms window, so it is not "slightly late"
for the attempt it belongs to; it is at the very edge of it, every time, for every attempt. The
argument that a late reply costs only one attempt does not rescue a reply that is marginal on all of
them. So the delay went back to being the leading candidate, and the queue hypothesis became the
fallback.

**Then 700 was tried for real and also failed**, which is what moved the suspicion from the value to
the clock it is measured in: an offset is computed from `lastRxEndTick` and the frame leaves after a
settle read once from `0xf04`, so if either is out by a few hundred microseconds then no computed
value is the value on air, and naming a better one is guessing again. Hence the spread. The point of
walking the offset is not that some step is expected to be magic — it is that the step which works
*reports what the timing really is*, and that is the one thing none of reading the datasheet,
disassembling the stock image, or three rounds of choosing a constant has produced.

So: change reply timings only as part of that sweep, one delay at a time, and keep the
burst-versus-quiet comparison for afterwards — a timing change mid-flight is the one thing that would
make it unreadable. (That sweep has run - fact 17 - and was removed in `ed9d0142`.)

## Open issues / next steps (in rough priority order)

0. **Do not let a serial monitor touch `/dev/ttyACM0` carelessly.** Opening or closing that port
   toggles DTR/RTS, which resets the ESP32-S3. The radio is then still at 2 Mbaud while the S3 probes at
   115200, and the S3 has to reset the radio over SWS to find it again; once it stayed in ASCII mode
   with `SCP`/`SDA` failing forever (this firmware answers only `NFO?`/`RDY?`/`FRM1` in ASCII). Open
   the port with DTR and RTS deasserted *before* `open()`, clear `hupcl`, and keep one logger attached
   instead of reconnecting.

1. **Direct serial tooling (local session).** Log both directions of the TLSR↔S3 UART (or the S3's USB
   console plus the web-log websocket) to files with timestamps; decode v2 frames (COBS + CRC16,
   `oepl-radio-v2.h`) so LOG/BLOCK_SERVED/STATUS come out intact. `test/test_ap.py` already has a
   Python v2 framer to reuse.
1b. **The check-in and block-request stale limits are still derived the old way.** `tooLateToAnswer()`
   bounds a PING by the widest family window now, for the reasons in section A of
   `docs/design/radio-findings-backlog.md`; the 4000 µs check-in and 6000 µs block-request limits were
   left alone, and the same argument applies to them. `getAvailDataInfo()` enables the receiver before
   its attempt loop and never calls `radioRxFlush()` at all, so its 14 × 5 ms windows have the same
   slack a PING's do — which also means some share of a non-zero `rep` (fact 13) may be replies the
   tag did read one attempt later rather than replies it lost. Re-deriving these needs a window where
   `late` is not 0, and `late` has never been anything but 0 here, so the host tests carry them.
   None of this round's three changes has been seen on hardware: `late` is 0, `reack` needs a tag that
   misses an ack mid-burst, and the fleet here is all ZBS243, so the M3 paths cannot be exercised at
   all.
2. **Single-part re-requests after bursts.** Addressed this round (gap, window budget, odd repeat
   spacing) but not yet proven over a long run. The number is `rereq`/`rparts` per `bst`; the
   `rereq blk … pos …` line gives the burst positions of the parts that were missed, so the pattern can
   be read off: positions clustered at the start means the tag is not ready yet, one parity only means
   it cannot keep up frame to frame (raise `RADIO_BURST_GAP_US`), scattered means plain radio loss
   (raise `BURST_COPIES`). The 600 µs gap is an estimate of the tag's per-part work, not a
   measurement.
2b. **Read `at`, `larm` and `lld` from the first tag that joins on the PONG spread.** (Done: fact 17;
   the spread was removed in `ed9d0142`.) That is the
   measurement the whole timing question now turns on, and all three fields are on the log already.
   `at` says which offset the tag accepted, `larm` (read after arming, so it covers that PONG) says
   whether the reply kept its schedule at all, and `lld` says whether the hardware was adding a
   transmit-enable delay nobody had read. Section C of `docs/design/radio-findings-backlog.md` has a
   table of what each range of `at` implies, including the case where the answer is that the PONG
   failures were never a timing problem. Do not re-point `RADIO_REPLY_AT_US` or the spread before this.
3. **Re-measure `RADIO_REPLY_AT_US` at ~1700, and `rep` is the metric.** The sweep has now given the
   number this was waiting on, and the analysis is in facts 17-19 and in `ap.c` above the constant:
   1220 sits 30-130 us *before* a ZBS243 starts listening, the all-family budget is [1250, 2396], and
   ~1700 is 450 us inside both edges. It is a constant now: set `RADIO_REPLY_AT_US` in `src/hal.h`
   and `make clean && make`, then
   count `rep` over a **whole** log rather than its tail (BOARD.md says why) and compare against the
   4-17 % baseline. If it falls, make 1700 the default; if it does not, the ack-wait edge is not what
   `rep` is counting and that is worth knowing too. **Change one delay at a time**: the PONG offset is
   separately set from its own measurement, and these two constants have twice been moved together by
   accident.
3b. **Retired: "try ~700".** That was the standing advice here and it is now the wrong direction -
   700 us is deep inside a ZBS243's acknowledge wait, where the reply is destroyed in hardware. The
   reasoning behind it was that stock answers every reply site at about 720 us, so 1220 looked longer
   than it needed to be. Stock's 720 is measured from the start of its own transmit wrapper, and that
   wrapper re-initialises the whole radio - a flash read among it - before the frame: its reply lands
   on the far side of the ack wait, which is why it works. The one piece of that advice still worth
   keeping is that **nRF M3 tags are untested anywhere**; their window opens ~70 us after the frame
   and is 5 ms wide, so 1700 suits them comfortably, but nobody has watched one.
3c. **Read `nf` from an instrumented build, and if the gain hypothesis holds, read `0x440` with it.**
   The histogram is the instrument for the degraded fleet: a genuinely bimodal channel and a bimodal
   AGC look identical in RSSI alone, and they want opposite responses. Flash
   `make AP_DEBUG=1` and read `nf`
   over several windows. A low-bucket population that comes and goes together with a high-bucket one
   is the channel; a distribution that jumps between two buckets with nothing between them is the
   receiver, and then the cheap confirmation is to log `0x440` next to each sample (facts 22-23 and
   section C). **Do not touch receive gain either way until the far tags are back.**
4. `fo` is decoded (fact 9). What is left is **not a reading job**: its unit and sign need a signal
   generator with a known offset, or a second AP mistuned by enough cap codes to exceed any pull-in
   range. Test the "residual after AFC" hypothesis first - if it holds, there is no crystal trim to do
   from this measurement and the item closes. An `AP_DEBUG` run meanwhile should show `hi` with bits 7 and
   3 clear on every frame, which is the cheap confirmation the layout is read right.
5. Defaults decided: power, DMA, 48 MHz and STX are the only behaviour since `ed9d0142`. What is left
   is pointing `firmware_TLSR.json` at the new image. The README's option table and RAM
   section are current as of this commit and no longer need catching up.
6. Per-tag adaptive TX power from RX RSSI was removed unshipped (fact 11). Also:
   `telink_802154_sdk` (newer libdrivers, restrictive
   license) — radio driver identical up to zigbee SDK V4.0.1, so no gain found.

## Measured: `rep` does not depend on the reply offset

Counted per flashed image over a 6500-second log, splitting at each radio boot so each segment is one
build: at `RADIO_REPLY_AT_US` 1220, 6 repeats in 183 check-ins; at 1700, 8 in 432. Whole log 14 in
615, **2.3%**. The check-in reply sweep says the same from the other direction - offsets from 970 to
2220 produced 1 repeat in 55 check-ins.

So **the acknowledge-wait edge does not reach the check-in path**, and the prediction that 1700 would
drop `rep` toward zero is refused. `rep` at 2-3% is background loss, not a reply parked on the edge of
a window, and the quantitative account of it offered when the window table was re-referenced is wrong
for the check-in even though it is right for the ping.

The asymmetry has one cause, and it is the tag's bug both ways. `detectAP()` calls `radioRxFlush()`,
which sets `mRxBufNumFree = RX_BUFFER_NUM` and leaves `mRxBufNextR`/`mRxBufNextW` where they were, so
a scan starts with a misaligned ring and the reader consumes up to three slots nothing refilled -
which is why a cold join lands on the fourth attempt, measured twice at a fixed offset.
`getAvailDataInfo()` never calls that flush at all, so its ring holds an early reply until the next
poll and the offset barely matters. One defect, two symptoms that look nothing alike.

What this means for the constants: 1700 is fine and so was 1220. 1700 is kept because it is further
from the ping path's floor, not because it fixes `rep` - it does not. The reasoning recorded with that
change, that 1220 sat below the floor, holds for a scanning tag and not for a checking-in one.

Also worth noting against the 4-17% figure quoted elsewhere in this file: tonight's whole-log rate is
2.3%, so that range came from a different log or a different condition and should not be treated as
this installation's constant.

## What is on the radio, and what is still open

**Flashed and verified** (`make SYS_CLOCK_HZ=48000000 RADIO_APPLY_TX_POWER=1 UART_TX_DMA=1 EARLY_BURST=1`,
md5 `99d152aa853b4e1bf625f63f864745a8`): `AP_DEBUG=0`, `RADIO_REPLY_STX=1`, `RADIO_REPLY_AT_US=1220`,
`RADIO_REPLY_PONG_US=1700`, `RADIO_PONG_SPREAD=0`, `CACHE_ARENA_BYTES=40504` (nine blocks),
`URX_RING=1024`, `MAX_PENDING_MACS=250`. On hardware: `ci` flowing, `rep` 0-1, `gap 1220`, `late 0`,
`larm 0`, `txto 0`, `drop 0`, `bdrop 0`, `udmaf 0`, no serial overflows, `busy` 2-5%, all 11 live tags
heard inside ten minutes with the weakest at -79 dBm.

**On the radio since 2026-11-01 14:04: eleven blocks, the pending table in the arena, one S3 frame per
loop pass** (same flags as above, md5 `9cb1a1bc3997d839f1daf91436663f29`, from an uncommitted tree).
Verified on hardware: `cfg ... cb 11 tags 1024 pend 456`; through a refresh of all 13 tags, 9
transfers, 6 bursts (5 early), no re-requests, no evictions, the cache at eleven blocks throughout,
`rep 1` in 77 check-ins; the longest pass-to-pass gap down from 41 ms to **6.5 ms**. One sample of
6.2 ms in part 2 (the S3's requests) is not explained: no single request has measured over 1.6 ms,
so something else in that part takes ~4.5 ms once. Worth an instrument before anything is keyed on it.

**Since `ed9d0142` the tree builds this behaviour with a plain `make`**: that commit fixed every
option above at the value in this flag set and deleted the alternatives, and the default build's code
is instruction-for-instruction the `9cb1a1bc` image (differing only in GCC's numbering of clones and
function statics, and the link order that follows), with `.bss` and headroom unchanged; the binary
is not byte-identical for that reason.

**Before that, 13:37: ten blocks, with the buffer high-water line** (md5
`3d6e5320e73f47e17508126645e4b416`). Verified on hardware: `cfg ...
cb 10 tags 1024`, check-ins with `rep 0` and `gap 1220`, 11 transfers and 6 block bursts with three
early bursts and no re-requests during a refresh of every tag, four S3 preloads of 4 blocks into the
cache with no eviction, a channel scan on two tags answered (`png 4`) and both back to checking in,
`drop 0 bdrop 0 udmaf 0 evd 0` throughout. Not exercised here: the inventory lock (this AP runs
`lock 0`; the allow-list itself is sent and held regardless), tag return data (needs a button),
per-tag power (not in this flag set), and a full 42-part burst (see BOARD.md: the test images are
skipped by the tags, on the old image too). The nine-block image is `99d152aa` and rebuilds
byte-identical from `df4be9e1`.

**Measured with it** (the `hw` line, since boot, through a refresh of all 13 tags): UART ring **924 of
1024**, longest pass gap **41 ms**, all of it in part 2 (the S3's requests), slowest request a
DATA_CHUNK at 1.65 ms, radio slots 1 of 8, events 1 of 24, side buffers 0 of 3, LOG queue 5 of 6 with
none dropped, pending peak 9 of 250. Two things follow, both in the backlog's section F: the ring's
fill is set by the S3's chunk window, not by pass time, and the serial drain loop holds the radio off
for the whole of a preload stream.

**The ten-block arena itself:** `CACHE_ARENA_BYTES=42320` with 4104-byte regions:
ten blocks up to 320 tags of a shared family (176 of unrelated MACs), nine up to 1344 (689), so with
the default 1024-tag cap a normal fleet never goes below nine. Paid for by 767 bytes found in RAM
code rounding, the decode buffer and the pending table's checksum byte, plus the regions no longer
storing a burst's 0xFF tail - see "Where the bytes for the tenth came from" in
`docs/design/radio-sram-split.md`. Two of the changes are in paths only hardware exercises for real:
`irq_handler`'s timer-interrupt clear (one masked write instead of three tests; the bits are asserted
to coincide) and `sendPart`'s last part, whose 58-byte 0xFF tail is now generated. The host tests
check every part of every burst against the file, so the second is covered; for the first, a site
that boots and keeps answering is the test, since a stuck timer interrupt would starve the main loop.

**Built and host-tested, not yet on the radio: bursts that listen.** A burst used to be deaf from
end to end: the receiver is on in the 600 us gap between parts, but a tag frame is 768 us or longer,
so it could only start in a gap and the next part cut it off. Now a part waits while
`is_rf_receiving_pkt()` (0x448 bit 5) says a frame is arriving or one waits unread, the frame is
answered, and the burst goes on; the hold is bounded by the longest 802.15.4 frame. The burst is sent
in passes: after the first, a ZBS243 is listened for as long as such a tag has been seen to take to ask
for the next block (`dn`) or send XFC (`dx`), learned from 0, so its request is heard on the first
attempt and no repeat goes out after it; other families get their passes spread over the window.
`late` is no longer the repeat budget's harm signal - it cannot see a frame that arrived while the
radio transmitted - and the budget now weighs the measured estimate (`oth` x `btx` / listening time,
reported as `hrm`) against re-requests, with the window as its only ceiling. On the reporting rule:
`air` is not a fifth core line but the report's one extra slot, taken only when the boot facts and the
hw/stk lines leave it free, with counters that run from one `air` line to the next so it loses nothing
by waiting. On the reply timing (`larm`): a part is never started while a frame waits in a slot or is
arriving, so the one way a reply was known to go late during a burst - built behind a part already on
the air - no longer arises from a burst; the remaining gap wait is at most what is left of 600 us after
our own frame, in which no tag frame can complete. Built on `71040485`: text +1956, `.data` +4, `.bss`
+32, headroom 1248 -> 1216 (AP_DEBUG 1208 -> 1168), twelve blocks unchanged. 56 of 56 host tests.

### Open, in the order worth doing

0. **First hardware run of the listening bursts (web log, 95 bursts): `dn`/`dx` stayed 0 throughout,
   and that was the learner, not the tags.** Every image on this site fits one block - all 95 bursts
   were block 0 - so no tag ever asks for a next block and `dn` has nothing to learn from. The only
   "done" is XFC, which the ZBS243s sent **550-1500 ms** after the burst, growing with the image (about
   550 for 3-4 parts, 770 for 7-10, 1200 for 12, 1460 for 27-28), and the learner discarded anything
   over 300 ms. Readings are kept now and a pause is taken only when a repeat still fits after it,
   so `dx` reports that latency and never pauses; `dn` will move only on a multi-block image (an
   uncompressed or large one). 22 of the 93 bursts went to tags whose type the radio had not yet seen
   in a full check-in (`ebst` 71 of `bst` 93), and those are not watched at all.
   **Second run: `dx` learned (1322, 745, 795), `larm` 0, and seven busy cancels in the whole log -
   all 35-794 ms after the other tag's burst had ended - which is what the queue (`bq`) now replaces.**
   The 10 ms flag share read 1-4 looks where the frames received predicted 1-6: the same order, but
   too few to tell a flag up for whole frames from one up for part of them. `fh` against `fe`, at a
   look per millisecond and counted since boot, is the comparison to read now.
   **Third run: `fh` sat ~16% above `fe` over ~200 frames, a steady ~130 us (4 bytes) a frame.**
   Either the flag rises during the preamble, not after the SFD, or about one frame in eight fails its
   CRC - those hold the flag but were dropped in the receive interrupt without a trace. They are
   counted now (`crc` on the rf line) and their time added to `fe`: if `fh` and `fe` now agree, it was
   corrupt frames, and `crc` / `rx` is the share; if the excess stays, the flag rises early - which
   means a burst's yield sees an arriving frame ~130 us sooner than assumed.
   **Fourth run, on hardware: the first yield (`yld 1 ylf 1`, a tag's repeated block request caught
   mid-burst and re-acked), and 7 of 60 frames failing their CRC.** Across the whole web log, 11 of
   127 bursts got no answer from their tag - no XFC, no re-request - and the tag only came back on its
   next check-in; all 11 had the early ZBS243 timing (11 of 103, against 0 of 35 safe). `sile`/`sils`
   now count that per timing from the tag's next frame. If `sile` keeps outrunning its share of `ebst`
   over a day, the early burst is losing those tags and its margin is the place to look.
   **Check the flag is what Telink says.** `fh` looks at it every millisecond whatever the burst is
   doing, `fe` is what the frames received predict, and `ylf` counts the holds after which a frame
   really arrived; `yld 0` across every window says nothing about the flag on its own, because 12
   tags give a burst few chances. What to read: `fh` close to `fe` (the flag is up for whole frames;
   well below it, for part of them - then a hold starts later in a frame than assumed); `ylf` equal or
   close to `yld` (a hold is a tag); `yto` at 0 (a climbing `yto` means the flag is up when no frame
   is arriving, and the hold only costs time). With a multi-block image - push an uncompressed one to
   a tag the owner clears for it (none is, BOARD.md) - `dn` should settle at whatever a ZBS243 takes to write a block away, `cut`
   should rise, and BLOCK_SERVED's frame count for its full blocks should drop from 52 towards 42 -
   the repeats that went out after it had the block. `hrm` should read 0 on this 14-tag site; if it does not, `cpy` will start
   falling, and that is the number to look at before anything else.
1. **`URX_RING=1024` wants a day of uptime.** The only change in this round whose margin is an
   argument from the code (2.1 ms of arriving bytes against 5.1 ms of ring) rather than a
   measurement. Overflow is bounded and counted twice, so watch `serialOverflows`; `URX_RING=2048`
   restores it in one flag.
2. **The reply offset is unresolved, not settled.** 1700 is where two independent readings put the
   centre of every window and 1220 lands 30-130 us early, but every measurement of 1700 was taken
   while this AP's own WiFi had the receiver's floor some 20 dB high. Re-test on a quiet channel, and
   **score it on tags not heard in ten minutes, from `lastseen`** - not on `rep`, which cannot see a
   tag that has stopped checking in, and not on `nextcheckin`, which is the host's prediction and runs
   stale. Both of those produced false conclusions in the session that set this value. The offset is
   a constant in `src/hal.h` now, so the re-test is an edit and a rebuild, not a flag.
3. **`BURST_BUSY_HOLD_PCT` has never been exercised honestly.** `busy` ran at 50-75% all session
   because of a websocket client streaming the AP's log over its WiFi, and reads 2-5% without it. The
   hold has therefore effectively never engaged, and whatever it appeared to be tuned against was an
   artefact. The `nf` histogram is the instrument for doing it properly.
4. **The 250-tag service ceiling is the real product limit**, and the arena did not move it. Past 250
   a tag is answered but gets neither content nor a sleep schedule while the host retries
   `SET_PENDING` and is refused. The table cannot shrink to fund cache - the host holds one entry per
   tag permanently - and raising it to 512 costs 7074 bytes the budget does not have. Section F of the
   backlog has the curve.
5. **The tenth cache block is in the tree (see above); an eleventh is 2824 bytes away** and would start
   with no band at all. Headroom is 1208 in the tightest build (`make AP_DEBUG=1`): the 440 placed
   above the 256 the next RAM code past a boundary costs, and 768 that `FLASH_RESIDENT`
   (`boot_8258_ap.link`) gave back by linking the SDK's sleep, ADC and analog RAM code into flash -
   not yet on the radio, and unspent. The priced list at the end of the backlog's
   section F is what is left, and every item on it is a margin or a capability. Place anything
   further in 128-byte steps above a multiple of 4104, never on one.
6. **The tag firmware's `radioRxFlush()` is broken and it is not ours.** It resets the free count and
   leaves `mRxBufNextR`/`mRxBufNextW` alone, so `detectAP()` starts a scan with a misaligned ring and
   discards up to three PONGs - which is why a cold join lands on the fourth attempt, measured twice
   at a fixed offset. Worth reporting upstream.
7. **Unexplained and unclaimed:** the frequency offset's unit and sign (undocumented in every Telink
   source, and possibly a post-AFC residual that cannot measure our carrier at all), and the one
   `busy 67` window that survived switching the WiFi client off.

### The methodological lesson, because it cost most of a night

Three conclusions in this session were drawn from numbers that did not mean what they appeared to:
`nf` read as a noise floor when it was a wandering median; `rep` read as fleet health when it only
counts tags that still check in; `nextcheckin` read as overdue when it is a stale prediction. Each
produced a confident, wrong diagnosis - and in one case a revert of a change that turned out to be
innocent. Before a counter is used to judge a change, read what computes it.
