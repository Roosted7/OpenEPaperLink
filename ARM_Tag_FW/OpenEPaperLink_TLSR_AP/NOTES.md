# OpenEPaperLink TLSR825x AP radio firmware (802.15.4)

Open-source firmware for a TLSR8258 radio module next to the ESP32-S3 of an OpenEPaperLink AP. It
replaces the closed-source `binaries/TLSR/OEPL_TLSR_AP.bin` (AP type `0xF8`, "TLSR Zigbee 802.15.4",
version `007c`, listed as `0001` in `firmware_TLSR.json`) and brings the TLSR to the level of the ESP32-C6/H2 radio:

| | closed-source (reports `007c`; `0001` in `firmware_TLSR.json`) | this firmware `0080` |
| --- | --- | --- |
| S3 link | ASCII protocol, 115200 baud | protocol v2 ([radio-protocol-v2.md](../../docs/design/radio-protocol-v2.md)): COBS frames, CRC16, seq/ACK, retransmission, 2 Mbaud |
| Block data | from the S3 per request, ≈ 550 ms (`pleaseWaitMs`) | 28 KB block cache filled ahead by the S3: 15 ms for a cached block, from 140 ms otherwise (measured against what this S3 really takes) |
| Block burst | always 42 frames, blocking | each requested part once to three times as the fleet and the channel allow, as many frames as the tag's listening window still holds; one frame per loop pass, the S3 is answered in between |
| Radio TX | full `rf_drv_init()` before every frame, busy wait | radio stays initialised; TX → RX switch in the TX interrupt |
| Radio RX | 50 × 250 byte copies in the interrupt | zero-copy DMA ring (8 slots), short interrupt so 2 Mbaud UART bytes are not lost |
| TX power | `rf_set_power_level_index(63)`: out of range, ignored by the driver (default power) | what the S3 asks for (10 dBm, near the TLSR8258's +10.46 dBm maximum), optionally up to 9 dB less per tag where that tag's RSSI has shown the margin for it |
| Tag inventory lock | – | allow-list in the radio (1024 MACs), learning mode |
| Late requests | answered anyway | skipped when too old for the tag to take the reply |
| Lost block ack | – | the tag asks again, gets the ack once more and the burst runs on |
| Diagnostics | – | STATUS every second (counters), BLOCK_SERVED per burst, LOG frames |

The AP logic (`src/ap.c`) is the logic of the ESP32-C6/H2 radio firmware 0x0024 (`../OpenEPaperLink_esp32_C6_AP/main/main.c`),
which in turn came from the ZBS243 AP; the S3 does not need any change.

**Status:** runs on an OpenEPaperLink Nano TLSR (ESP32-S3 + TLSR8258, ZBS243 tags): protocol v2 at
2 Mbaud, the block cache, and image transfers (check-in, one block request, one burst, XFC). First
hardware run, not yet a long-term test, so `firmware_TLSR.json` still offers the closed-source image;
the beta is `binaries/TLSR/OEPL_TLSR_AP_0080_beta.bin`. Test builds, hardware findings and open issues:
[HANDOVER.md](HANDOVER.md); the full list of build options is at the top of the `makefile`.

What the first hardware tests showed, for whoever touches the radio code next:

- **The burst is timed from the ack, not from the request.** A tag counts its `pleaseWaitMs` from the
  end of the block request ack, which leaves the antenna about 2 ms after the request (the reply delay
  plus its own time on air). The burst start is scheduled from that moment, and from the point where
  each tag type really starts reading frames: a ZBS243 with a short wait is already listening
  `pleaseWaitMs - 5` ms in, an nRF52811 only starts its radio when the wait is over.
- **A burst fills the tag's listening window.** A tag listens 300–315 ms for block parts, which is room
  for about 50 frames. The old cap of 42 frames per burst meant a tag asking for a whole 4 KB block
  (42 parts, what a fresh image asks for) got a single copy of every part, so one lost frame always
  cost it another request round. Repeats of the same part are also spread by an odd number of frames,
  so a tag that misses every second frame still gets every part.
- **How much a burst repeats is not a constant.** One copy of each requested part is not
  discretionary — the tag asked for it — but every repeat beyond that is paid for by the rest of the
  fleet, since the radio is deaf while it transmits. On this bench that costs nothing; on a 200-tag
  site with neighbouring APs a 52-frame burst is ~257 ms of transmitting and steps on roughly one
  check-in every time it runs. So the radio weighs the two costs it can measure, in milliseconds of a
  tag's radio: the other tags' attempts its bursts went out over (their frames heard per millisecond
  of listening, times the milliseconds it spent transmitting, plus requests heard too late), at a
  5 ms check-in window each, against tags coming back for parts, at a 300 ms receive window each.
  Copies come down when the first outweighs the second and go up when the second does *and* the
  channel is quiet enough to afford it; **no signal changes nothing** — a quiet install must never
  read silence as permission to grow. `BURST_COPIES` (2, in `src/ap.c`) is only the starting point and the window the
  only ceiling; `cpy` reports where it settled, the `air` line what it was weighed on.
- **A burst listens between its frames.** The receiver is on in the 600 µs gap between two parts,
  but every tag frame is longer than that, so it can only be caught *starting* in a gap — and sending
  the next part cut it off. A burst was therefore deaf from end to end: other tags' requests and the
  served tag's own "I have it" went unheard for up to 260 ms. Now a part that falls due while the
  receiver has synchronised on a frame (`is_rf_receiving_pkt()`, 0x448 bit 5) waits for it, the frame
  is answered, and the burst goes on. The hold is bounded by the longest frame 802.15.4 allows; a
  flag that outlasts it is not trusted for the rest of that burst. `yld`/`yto` count both.
- **A burst is sent in passes, and what happens between them is chosen per tag.** A ZBS243 leaves its
  receive loop the moment the block is complete and asks for the next one at once, so after the first
  pass the radio listens for as long as such a tag has been seen to take — and if it speaks, no repeat
  is ever sent and its request is answered on the first attempt instead of being retried through
  repeats nobody reads. That wait is learned, not set (`dn` after a block, `dx` after the last one):
  0 until a tag has been seen, then the shortest time a tag took, rising again when a whole window
  sees only longer ones. It pauses a burst only when a repeat still fits in the window after it - and
  for the last block it never does: a ZBS243 sends XFC 0.5–1.5 s after it (measured, growing with
  the image), so `dx` records that and the repeats go straight on. On a site whose images all fit one
  block, which compressed images on small tags do, nothing ever pauses, and `dn` stays 0 because no
  tag ever asks for a second block. Every other family listens out its whole window regardless, so waiting costs
  it nothing and the passes are spread evenly over that window instead: a clump of loss — a tag still
  settling, the AP's own WiFi — then cannot take out both copies of a part.
- **A tag that asks during another tag's burst waits its turn instead of being cancelled.** One
  radio sends one burst at a time; this used to be settled by sending CANCEL_XFER to any other tag
  for 1.2 s after a block request, which on hardware hit 7 transfers in ~110, every one after the
  other burst had already ended, and each cost its tag a whole check-in cycle (40-110 s there,
  hours on a site with long intervals). Now "busy" means a burst is due or on the air, and a tag
  arriving then is acked with the shortest wait after which, for its family, it starts listening
  once that burst is over - estimated from the burst's own plan - with its block asked of the S3 at
  once. One slot: only a third tag at the same moment is still cancelled (`cxlb`); the queued ones
  are `bq`.
- **Wait before replying** (now `RADIO_REPLY_AT_US` in `src/hal.h`: the reply starts a fixed 1220 µs after the end of the request, measured with the radio's RX timestamp; first fixed with a plain 500 µs wait, then by a nominal 950 µs that was not what it said — the code added the driver's 120 µs software wait and ignored the radio's own 150 µs TX settle, so the preamble really left ~270 µs late, at about 1220. Reading the settle from `0xf04` and subtracting it made the number honest, and a *true* 950 then repeated 6 of 7 check-ins, which is why the default is 1220. **Treat that measurement as unexplained rather than as a fact about tags**: the tag firmware does not need the turnaround it was credited with — `detectAP()` and `getAvailDataInfo()` enable the receiver before their attempt loop and leave it on, so an earlier reply is strictly safer for the tag — and `halRadioTxReply()` was arming `rf_start_stx` with a tick that could already be in the past, which the state machine matches for equality and so waits out a four-minute timer wrap. That is fixed and counted as `larm`; 1220 is a candidate for re-measuring with it fixed, and lowering it is cheap to try). Without any wait the replies leave about 0.6 ms
  after the end of the tag's request, and ZBS243 tags miss practically all of them: every check-in
  repeated until the tag gave up, although every reply was sent, complete and on time. The
  closed-source firmware has the same wait (`sleep_us(500)` in its TX wrapper, before `radioTxLL()`).
  Ruled out on the way: TX power, fast vs. legacy TX, frame contents (byte for byte those of the
  closed-source firmware), GPIO setup.
- Every frame goes out at the power the S3 configured (it was a build option until `ed9d0142`;
  the chip-reset power it replaced is the same +10.46 dBm on this hardware, see HANDOVER.md).
- **No per-tag transmit power.** It was built, host-tested and removed unshipped in `ed9d0142`: it
  learned only from check-in replies but lowered the power of every frame to the tag, block parts and
  acks included, where a loss costs a request round rather than a retry, and what it buys - less
  interference to a neighbour on our channel - is something only the S3 knows a site has. If it comes
  back, it is as a `SET_CONFIG` flag.
- The diagnostics the radio already keeps go out as LOG frames every 15 s when the S3 asks for them,
  and appear on its serial console as `radio: ci … rep … us …`: check-ins answered and repeated
  within 300 ms (`rep`, the tag missed our reply), reply time from the start of the request (`us`,
  `max`), block requests, bursts, XFC, RX/TX frames, TX timeouts, RX drops.
- `make AP_DEBUG=1` is the instrumented build, and it is one switch over every instrument in the
  firmware: those same lines whether the S3 asked for them or not, a line per ping and per join with
  the offset the tag accepted, and a line per check-in with the crystal cap code and the frequency
  offset. A production image carries none of it. It is worth about 1.3 KB of flash and 48 bytes of
  `.bss` — which is an eighty-fifth of a cache block, so do not expect the cache to grow by turning the
  instruments off; see [RAM](#ram).

## How the closed-source firmware was built

`tools/` has a TC32 disassembler and a function matcher. Matching `OEPL_TLSR_AP.bin` against the
objects of `libdrivers_8258.a` and the TLSR tag firmware's `.elf` (`../OpenEPaperLink_TLSR/out/`)
names 104 functions, byte for byte (call targets and literal offsets aside):

```
ar x ../OpenEPaperLink_TLSR/tl_zigbee_sdk/platform/lib/libdrivers_8258.a   # in an empty directory
python3 tools/fingerprint.py ../../binaries/TLSR/OEPL_TLSR_AP.bin 'objs/*.o' \
        ../OpenEPaperLink_TLSR/out/TLSR_OpenEPaperLink.elf | wc -l
```

What those names say, layer by layer:

- **SDK:** this repository's `tl_zigbee_sdk` (`libdrivers_8258.a` plus `proj/` drivers, `tlPrintf`,
  `string.c`), linked in the same order as the tag firmware's makefile.
- **Radio:** `zigbee.c` of the TLSR tag firmware, unchanged (`rf_setChannel`, `zigbee_tx`,
  `getRxBuffer`, `rf_rx_irq_handler`, `radioTxLL`, …). `radioTxLL()` calls `init_zigbee()` (a full
  `rf_drv_init()`) before every frame.
- **AP logic:** the ESP32-C6 AP `main.c` of 2023/24 (strings `Invalid block request received, 0
  parts..`, `Sending parts:`, `BUSY!`; 250-entry pending table of 27-byte entries; 60 s
  housekeeping; ASCII tokens `TYP> VER> MAC> ZCH> ZPW> PEN> NOP>`, no `HSPD`, no `SCH>`), about 5 KB
  between `adc_*` and `zigbee.c`.
- **Platform:** `drv_platform_init()` of the SDK with the tag firmware's `app_cfg.h` (8258 "DIY"
  board, 24 MHz), `watchdog_enable(10000)`.
- **Linker script:** not the SDK's `boot_8258.link`: `.bss` ends at `0x84ab54`, past that script's
  32 KB retention limit, so the AP was linked with that check removed (as `boot_8258_ap.link` here).
- **Compiler:** the tag firmware's `.elf` says `GCC: (GNU) 4.5.1-tc32-1.3`, the Windows toolchain in
  `../OpenEPaperLink_TLSR/tc32_windows`.

Built with this makefile (tc32 `4.5.1.tc32-elf-1.5`), **76** functions of `OEPL_TLSR_AP.bin` come out
the same in the new image. That is the other direction of the same match — the closed `.bin` against
our own `.elf`, so the names come from this build and AP-logic functions could appear:

```
make                       # the default configuration
python3 tools/fingerprint.py ../../binaries/TLSR/OEPL_TLSR_AP.bin out/OEPL_TLSR_AP.elf | wc -l
```

They are the SDK drivers (`adc_*`, `analog_*`, `clock_*`, `cpu_*`, `flash_*`, `gpio_*`, `pm_*`,
`rf_*`, `uart_*`), the SDK's ten `drv_*` wrappers, and `memcmp`. **Nothing of the AP logic is among
them**, and only `memcmp` is left of `string.c`: `string_min.c` replaces that file (it drops the
`is_ev_buf()` call, and the SDK's `memcpy`/`memset` also return `NULL` for a zero length and post an
exception on a null pointer), and `memcpy` has since gone word-at-a-time. `getBlockDataLength()`,
which this section used to name, is `static` with one call site, so GCC inlines it and the image holds
no such function at all. The image header (`KNLT`, flags, size at 0x18) has the same layout.

Read the number for what it is: the matcher ignores call targets and literal-pool offsets, and it
accepts a function whose tokens agree over a long enough prefix, because a symbol's trailing literal
pool does not decode as code. Of the 76, 47 agree over their whole length. It is a good test that the
SDK and platform layers are built the same way, and no evidence at all about the AP logic, which was
rewritten.

Hardware, from the code:

| | |
| --- | --- |
| UART to the S3 | TX **PB1**, RX **PA0** (`uart_gpio_set(UART_TX_PB1, UART_RX_PA0)`), `uart_init(12, 15)` = 115200 baud at 24 MHz, byte interrupt into an 8000-byte ring |
| PC4 | output, high from the start, never changed (enable line or LED) |
| AP MAC | `uid[i] ^ uid[i + 8]` of the 16-byte flash UID |
| Calibration | frequency offset (cap) from flash `0x77000` |
| Channels | `{11, 15, 20, 25, 26, 27}`, default 11 |

`OEPL_TLSR_GFSK_AP.bin` (`0xF9`, by ATC1441) shares the SDK and platform code but has its own radio
layer (`rf_pn_disable`, a private GFSK mode for other tags) and its own UART interrupt that empties the
4-byte receive buffer into a 2 KB ring; it switches the UART to 2 000 000 baud at 24 MHz. That is the
proof that the UART of this chip runs at 2 Mbaud with byte interrupts, which this firmware relies on.
The GFSK variant is not covered here.

## Layout

```
src/ap.c, ap.h          AP logic: pending table, allow-list, block requests, bursts, v2 requests and events
                        (the burst schedule and the reply delays per request type live here)
src/v2link.c, .h        protocol v2, radio side: framing, duplicates, event queue with retransmission
src/blockcache.c, .h    block cache, sharing one arena with the allow-list (7 to 10 blocks of 4104 bytes)
src/proto.h             tag <-> AP air protocol
src/hal.h               everything hardware: time, UART, radio, LED
src/tlsr/hal_tlsr.c     hal.h on the TLSR8258: radio, UART, interrupt handler, board start
src/tlsr/main.c         entry point
src/tlsr/*_cfg.h        SDK configuration, from the TLSR tag firmware
boot_8258_ap.link       the SDK linker script without the retention limit; the SRAM budget (see RAM)
test/                   host build of the AP logic and its tests
tools/                  TC32 disassembler and function matcher used for the analysis
```

The SDK is not copied: the makefile uses `../OpenEPaperLink_TLSR/tl_zigbee_sdk`.

## Building

Needs Telink's `tc32-elf-gcc` (GCC 4.5.1, Telink TC32 version 2.0).

- **Windows:** `..\OpenEPaperLink_TLSR\tc32_windows` is in the repository; run `make` (from the
  same toolchain, `..\OpenEPaperLink_TLSR\tc32_windows\bin\make`, or `makeit.exe` as the tag firmware does).
- **Linux:** Telink's Linux IDE has the same compiler ("Telink TC32 version 2.0 build",
  `4.5.1.tc32-elf-1.5`; its GCC headers are identical to the Windows copy). Telink's download servers
  hand it out as `tc32_gcc_v2.0.tar.bz2`; a copy is on the `linux` branch of
  [flyskywhy/tc32](https://github.com/flyskywhy/tc32):

  ```
  git submodule update --init ARM_Tag_FW/OpenEPaperLink_TLSR/tc32_linux
  make
  ```

  The submodule is [flyskywhy/tc32](https://github.com/flyskywhy/tc32) (branch `linux`), and
  `TC32_PATH` defaults to it; point `TC32_PATH` somewhere else to use another copy.

  Telink's GitHub SDKs (`telink_802154_sdk`, `telink_rf4ce_sdk`, `tl_platform_sdk`) contain no
  compiler. `telink_802154_sdk` (2025) has a newer `libdrivers_8258.a` and is a candidate to replace
  the 2024 `tl_zigbee_sdk` used here; not done, since the latter is what the closed-source AP and the
  tag firmware are built with.

```
make                    # out/OEPL_TLSR_AP.bin: the image, 48 MHz, every behaviour fixed
make AP_DEBUG=1         # the same image with every instrument in it
```

Compile-time options (`-D` in `GCC_FLAGS`, or the makefile variable). Since `ed9d0142` there are no
behaviour options: what used to choose the clock, the radio TX mode, STX replies, UART TX by DMA, early
bursts and applying the configured power is fixed at the value that won, and the timing figures are
constants in the source next to the measurements that set them - `RADIO_REPLY_AT_US` 1220 and
`RADIO_BURST_GAP_US` 600 in `src/hal.h`, `RADIO_REPLY_PONG_US` 1700, `BURST_WINDOW_MS` 260 and
`BURST_COPIES` 2 in `src/ap.c`, `V2_LOG_GAP_MS` 60 in `src/v2link.c`. The reply and PONG offset
sweeps, the crystal-cap trim and its sweep, and per-tag transmit power were removed. What is left is
the instruments and the sizes, and the sizes go when the pool replaces them
([radio-memory-rethink.md](../../docs/design/radio-memory-rethink.md), step 4):

| | default | |
| --- | --- | --- |
| `CACHE_ARENA_BYTES` | 48792 | the SRAM the block cache, the pending table and the allow-list share: eleven blocks of 4104 while the site is small, one fewer each time the other two need the room. 47768 with `URX_RING=2048`, which takes its kilobyte from here. See [RAM](#ram) |
| `CACHE_BLOCKS` | 7 | the cache's **floor**: blocks it keeps however long the allow-list grows. Sizes nothing; each block of floor is 4104 bytes the list cannot have |
| `MAX_PENDING_MACS` | 1024 | an optional **cap** on the pending table, which is in the arena and takes 26 bytes a tag as tags get something pending. What it may grow to is derived: 456 tags at the default arena, every byte above the floor except what the allow-list needs to reach its cap. Reported as `pend` on the `cfg` line |
| `MAX_ALLOWED_MACS` | 1024 | an optional **cap** on the allow-list. Sizes nothing either: the room comes out of the arena, four bytes a tag for MACs that share a family and eight for ones that do not. A radio that NAKs an `ALLOW_ADD` makes the S3 drop the radio-side lock until the link restarts, so the cap is what to lower, not the arena |
| `CACHE_BLOCKS_CAP` | 0 | the most blocks the cache may take however short the list is (0 = no cap). `CACHE_BLOCKS_CAP=7` pins it at the floor and reproduces the fixed split, for comparing the two on a site |
| `URX_RING` | 1024 | the UART receive ring, a power of two. 2048 if a site reports overflows; its kilobyte comes out of the arena, so it links in every build |
| `RAMCODE_SPILL_OK` | 0 | 1 = link even when RAM code ends just past a 256-byte boundary, which costs 256 bytes of SRAM (see `_ramcode_tail_` below) |
| `AP_DEBUG` | 0 | 1 = the instrumented build: every instrument in the firmware, and the only switch that compiles any of them. LOG frames whether the S3 asked or not, a line per ping and per join naming the offset the tag accepted, a line per check-in with the cap code, the frequency offset and the raw high byte of that field, and the idle channel's noise histogram on the `rf` line. Nothing that goes on the air changes. **48 bytes of RAM** and about 1.3 KB of flash. A production image sets it to 0 and carries none of it |
| `ACTIVITY_LED_PIN` | – | e.g. `GPIO_PD4`: a LED that flashes on radio activity, following the AP's LED setting |

## RAM

The TLSR8258 has 64 KB of SRAM and this firmware uses almost all of it, so the split is a decision
rather than an accident. What follows is that decision, the measurements it rests on, and the room
deliberately left for whoever adds the next counter.

A default build's 57368 bytes of `.bss`, by the map file:

| | | |
| --- | --- | --- |
| the arena: cache, pending table, allow-list | 48792 | `CACHE_ARENA_BYTES`, the one size in the budget: the three tenants share it and the boundaries move at run time, so none has a size of its own — see [docs/design/radio-sram-split.md](../../docs/design/radio-sram-split.md) and "the arena" below |
| cache slots | 275 | the arena's maximum block count × (24 + 1 byte of region index), outside the arena because a slot keeps its index while its block is moved |
| allow-list prefix table | 224 | 32 families × (5 bytes of MAC prefix + a reference count), which is what makes an allow-list entry four bytes instead of eight |
| v2 event queue | 1569 | 24 cells of 51 bytes and three 115-byte side buffers for the one event that is large |
| UART transmit buffers | 536 | two of 268 for DMA to the S3: one frame in flight, one being filled |
| UART receive ring | 1024 | 2 Mbaud from the S3, filled by the interrupt. Its fill is set by the S3's DATA_CHUNK window - three unacknowledged frames waiting while the fourth is handled - and peaked at 924 on hardware (the `hw` line) |
| radio receive ring | 1152 | 8 zero-copy DMA slots of 144 |
| frame buffers | 788 | received (decoded in place), raw and wire, 261-264 each |
| diagnostics line | 612 | the LOG queue: six lines of 101 |
| MAC → hwType table | 576 | `HWTYPE_TABLE` × 9 |
| interrupt stack | 512 | `irq_stk`, a separate stack: see below |
| the SDK's own, and the rest | ~1310 | drivers, flash, plus the odd handful of bytes the AP logic's own state costs. This row is the remainder, so it absorbs small changes — the named rows above are the ones to reason with |

Nothing in that table is instrumentation, and that is the answer to the obvious question. The whole
of `AP_DEBUG` is 48 bytes of `.bss` — most of it the idle-noise histogram's eight buckets and the
frequency-offset accumulators, which exist only in an instrumented build — against 4104 for a cache
block, so the instruments are an eighty-fifth of a block and taking them out of a production build
cannot buy one. The reason is simply that they were already off by default: a production image has
never carried them. What the one switch buys is that it cannot be shipped by accident, and about
1.3 KB of flash, which is not a budget here.

### How the budget is enforced

`boot_8258_ap.link` pins the stack top at `_sram_top_` = `0x84ff00` and asserts that `.bss` leaves at
least `_stack_min_` = 2048 bytes below it. Two symbols report the result, and
`tc32-elf-nm out/OEPL_TLSR_AP.elf` prints both: `_stack_size_` is what the stack actually gets
(3296 bytes in a default build, because unused SRAM is stack) and `_bss_headroom_` is that minus the
floor — **1248 bytes in a default build** and 1208 with `AP_DEBUG=1`, the tightest build there is,
which is what a future counter, table or buffer may take before the link fails. A third symbol,
`_ramcode_tail_`, is the other half of the budget and is easy to miss: `.data` starts after the RAM
code **rounded up to 256** (plus the 2304 bytes the instruction cache keeps after it), so RAM code
ending a byte past a boundary costs 255 bytes of `.bss` that nothing uses. The RAM code is 1936 bytes
(`_rstored_` is `0x790`) and ends 112 short of a boundary (`_ramcode_tail_` is `0x70`), so the next
112 bytes of it are free and the 113th costs 256.

It was 2812 bytes until three of the SDK's objects had their RAM code linked into `.text` instead
(`FLASH_RESIDENT` in `boot_8258_ap.link`), which gave `.bss` 768 bytes. The SDK marks code as RAM
code for chips that sleep and write flash, and this one does neither: `pm.o`'s `sleep_start` is
reached only through `cpu_sleep_wakeup`, which nothing calls, and its two 32 kHz tick readers are
called at boot from code that runs from flash anyway; `analog.o`'s two register accessors are in RAM
so that `sleep_start` can use them. `adc.o`'s supply check is the one with a real reason:
`flash_mspi_write_ram` calls it between the command and the address of a page program or sector
erase, when code cannot be fetched from flash. This image programs and erases nothing - the one flash
write it makes is the status register at boot, which has no address and does not reach the check -
and the linker asserts that it stays so: linking `flash_write_page` or `flash_erase_sector` fails the
build and says to put `adc.o` back, for 512 bytes. What is left in RAM code is what has to be there:
the vectors and startup, the interrupt handlers, `rf_trx_state_set`/`rf_tx_pkt` (called from the
radio interrupt), `sleep_us`, and the SDK's flash command routines, which drive the flash while it is
not being read from.

**The instruction cache.** Code outside RAM code is fetched from flash through a cache that lives in
SRAM, which is what those 2304 bytes are: 256 bytes of tags at the RAM code's end rounded up, then
2 KB of lines (`cstartup_8258.S` points `0x80060c`/`0x80060d` at them). Telink documents neither the
line size nor the associativity. A hit costs what RAM code costs; a miss reads the line over SPI, and
the SDK never sets the dual-line bits or the divider in `0x80000f`, so it is probably a single-line
read - estimated, not measured, at a few microseconds. The link order is not arranged for the cache
and need not be: the check-in path is about 2 KB of code and the S3's request path about 5 KB, so
both miss most of their lines after anything else has run, whatever the order - and the reply's
timing does not depend on how long processing takes, because the radio sends it at a fixed 1220 µs
after the request.

The stack top is fixed because the SDK's formula was `roundup256(_end_bss_ + 4 KB)`, and that
quantises: most of a kilobyte of `.bss` growth moves nothing, and then four bytes push the stack top
up by 256 and the link fails. A default build sat 636 bytes from that boundary, with three
consequences worth recording.

`UART_TX_DMA=1`, then in the build recipe in `BOARD.md` and now the only UART TX path, needed 640
bytes for its two transmit buffers and so had four too few — it would not link on its own, and only linked in the recipe while
`AP_LOG_ALWAYS=1` was in it (the diagnostics switch `AP_DEBUG` now covers), because the diagnostics
happened at the time to shave 8 bytes off `.bss`
and put the sum back under a boundary. When the recipe was later written down without the diagnostics it stopped
linking altogether, and was given `CACHE_BLOCKS=6` to fix it: 4184 bytes spent on a four-byte
problem, by someone with no way to see that it was a four-byte problem. Second, the per-tag power
controller's 144 bytes (the controller has since been removed) were paid for by shortening the allow-list to 960 entries, four times the room
it needed, because 512 bytes was the smallest thing that moved the boundary at all. Third, every
commit that added a few bytes reported only its own few bytes, so nothing in the history says where
the kilobyte went.

All three are gone: with the top pinned, a counter costs its own size, the headroom is a number
anyone can read, and the assert fires at the byte the budget is really exhausted with a message that
says what to lower.

### What the stack needs

2048 bytes is the floor, and it is not the 4 KB the SDK's script carried forward. **The worst-case
main-thread call depth is 796 bytes**, from the disassembly: per-function frames (registers pushed
plus the largest explicit `sp` subtraction) summed over the whole call graph, which has no recursion
and no reachable indirect call, so the number is a bound and not a sample. The path is
`main → apLoop → v2RxBytes → v2HandleRequest → diagAddSigned → diagAdd → memcpy`, and most of it is
one frame: `apLoop` alone is 380 bytes, 344 of them its own locals. It came to the same 796 bytes in
every flag set the firmware had when it was measured, per-tag power and the instruments included —
the depth is in `apLoop` and the v2 request handler, and none of the options reached deeper than
those.
(`tc32-elf-gcc` 4.5.1 has no `-fstack-usage`, which is why this is read off the listing.)

Interrupts do not use this stack. `cstartup_8258.S` runs `__irq` in IRQ mode with `SP` pointing at
`irq_stk`, a separate 512-byte array in `.bss`, and the deepest interrupt path
(`__irq → irq_handler → rf_trx_state_set → rf_set_channel`) is **116 bytes** of it. So the UART and
radio interrupts, which are what the hot paths worry about, are charged to a 512-byte stack with 4×
margin and cost the main stack nothing.

2048 leaves the main thread 2.6× its measured bound. The 2 KB that came back from the old 4 KB is the
cheapest RAM in the firmware, and it is where the headroom above comes from.

**What hardware could still add here**, and has not: `cstartup_8258.S` builds with `FLL_STK_EN 1`, so
it fills the whole stack region with `0xFFFFFFFF` before `main()`. Scanning up from `_end_bss_` for
the first word that is no longer `0xFFFFFFFF` gives the real high-water mark after a run, which would
confirm the 796 bytes above from the other direction. A `stk` counter in the diagnostics line would
cost about four bytes of the headroom.

### The arena: one size, and every maximum derived from it

These were the two adjustable items and they used to compete for the same last kilobytes at compile
time: 512 allow-list entries are 4096 bytes, which is one more cache block (4184) almost exactly. So
every build had to choose, and the choice was wrong for nearly every site, because both figures are
worst cases that no site reaches together. A site with 13 tags paid 8192 bytes for a list holding
104; a site with 1000 tags had nothing spare — and was also told it could never have a 1001st tag,
because the same constant that reserved the memory also capped the list.

**They now share one arena, with the pending table as a third tenant, and none has a size of its
own.** The arena is one number,
`CACHE_ARENA_BYTES`, chosen from what the budget can spare; everything else follows from it:

```
max blocks       = CACHE_ARENA_BYTES / 4104                  while the site is small
room above floor = CACHE_ARENA_BYTES - CACHE_BLOCKS * 4104   for the pending table and the list
max pending      = (room - 8 * MAX_ALLOWED_MACS) / 26        so the list can always reach its cap
max list entries = room less the pending table, / 4 bytes a tag (8 for unrelated MACs)
```

A region is the block and its 4-byte header, 4100 bytes, rounded to 4104. It used to be the 4158
bytes the 42 parts of a burst cover, the last 58 of which are always 0xFF; `cacheCopyPart()` makes
those up as the last part goes out instead, which is 560 bytes across ten blocks.

The pending table sits at the arena's low end and grows four entries at a time as tags get something
pending; the list follows it; the blocks are taken from the high end, and when either would cross
into the lowest block the cache surrenders that block — through the eviction policy, so
what is lost is the block whose tag is least likely to want it. When they shrink - the list as tags are deleted, the pending table
once a minute when housekeeping finds entries gone - the block comes back. An allow-list entry is four bytes rather than eight: the MAC's low three bytes plus a
one-byte index into a table of 32 families, since a fleet's MACs share their top five bytes. A MAC
whose family has nowhere in the table is kept at full width instead, so a fleet of unrelated MACs
costs exactly what it always cost and nothing is ever refused for lack of an encoding.

The default arena is 48792 bytes: eleven blocks and 3648 bytes beside them. A tag on a real site is
on the allow-list and holds a pending entry (the S3 announces "nothing for you, sleep n minutes" as
one), about 30 bytes a tag between them. Measured by adding tags that way, one at a time, against a
host build of that geometry:

| | one family (4 + 26 bytes a tag) | every tag its own family (8 + 26) |
| --- | --- | --- |
| 11 blocks, up to | **120 tags** | 108 tags |
| 10 blocks, up to | **256 tags** | 231 tags |
| 9 blocks, up to | 392 tags | 352 tags |
| 8 blocks, up to | 456 tags, the pending table's derived maximum | 456 tags |

A fleet of a few families behaves as the first column: there are 32 prefix slots, and this
installation uses four. Past 456 a tag is still answered and on the list - the allow-list keeps room
to reach its cap of 1024 whatever the table holds - but `SET_PENDING` answers `NAK NO_SLOT`, which
the S3 retries at the tag's next check-in. Against the ten-block arena with a fixed 250-entry table
this replaced: ten blocks to 320 tags of list, but **no service past 250 at all**; and against the
fixed split, **7 blocks at every size**. A list on its own, with nothing pending, keeps eleven blocks
to 896 entries. The inventory lock makes no difference to any of it: what the cache gives up follows
what the list and the table hold, not whether the lock is switched on.

`CACHE_BLOCKS` keeps its name and means the floor — the blocks the cache keeps whatever the list
holds — and it is the only real policy choice left, because each block of floor is 4104 bytes the
list cannot have, about 513 tags of it. `MAX_ALLOWED_MACS` survives only as an optional cap and sizes
nothing. `CACHE_BLOCKS_CAP=7` pins the cache at its floor and so reproduces the fixed split exactly,
which is how to compare the two on a site without changing anything else. `cblk` on the `blk`
diagnostics line reports how many blocks the cache has, but only when that is below its maximum —
i.e. only when the list or the pending table has actually taken one — and `cb`/`tags`/`pend` on the
`cfg` line report the arena's three derived maxima.

The design document has the arithmetic, the measured bands, the failure analysis, the cost of the
allow-list lookup on the check-in path, and a table of what further arena would buy per byte freed
elsewhere.

**Which configurations link, measured, `make clean` between every one.** Since `ed9d0142` there are
two builds, and a plain `make` is what the "site" flag set of the `BOARD.md` recipe used to build:

| flags | blocks | `.text` | `.bss` | `_bss_headroom_` |
| --- | --- | --- | --- | --- |
| `make` (arena 51496) | 7–12 | 37820 | 57568 | **1248** |
| `make AP_DEBUG=1` — the tightest build | 7–12 | 39100 | 57608 | **1208** |
| either, `URX_RING=2048` (arena 50472) | 7–12 | same | same | same |
| *`f7cb1a6e`, before `FLASH_RESIDENT`:* `make` / `make AP_DEBUG=1` | 7–12 | 37816 / 39096 | 57568 / 57608 | 480 / 440 |

**Which columns to trust as this moves.** `.bss` and `_bss_headroom_` are the budget and they only
change when something allocates, so they are worth re-measuring when you add state and worth
trusting otherwise. `.text` is not a budget — there is ample flash — and it moves with *any* code
change, so treat it as the size of the build that produced the row rather than a property of the
configuration, and do not re-measure the table for its sake.

Three things to read off it. **The eleventh block cost no headroom at all**: it is the 6500 bytes
the fixed 250-entry pending table used to occupy, which became arena when the table moved into it,
less 25 bytes of slot. The tenth before it cost 1072 bytes of headroom for 1816 of arena; the
difference was 767 bytes found elsewhere - 256 from RAM code that had ended 16 bytes past a 256-byte
boundary, 261 from decoding received frames in place, 250 from the pending table's dead checksum byte
- and a region no longer storing the 0xFF tail of a burst's last part, 56 bytes a block.

**`URX_RING=2048` is still one flag**, and still links in every build: the makefile takes its
kilobyte back out of the arena (47768), so what it costs is the eleven-block band shrinking from 120
tags to 84, not the stack floor.

**The arena is placed, not maximal.** 1208 bytes are left in the tightest build. 440 of them were
placed on purpose before `FLASH_RESIDENT` - more than the 256 the next RAM code past a boundary would
cost, plus room for a counter - and the 768 it gave back have not been spent: 112 bytes of RAM code
now fit before a boundary costs anything. Raise the arena only against measured headroom; every 30
bytes is about one more tag at each block count, so those 768 bytes would be about 25 more at each.

**And do not build anything on what the diagnostics appear to save.** Earlier versions of this table
had `AP_LOG_ALWAYS=1`, which `AP_DEBUG` replaces, saving 8 bytes of `.bss`, then exactly nothing,
then 8 bytes again, purely as a side effect of unrelated code landing. That wobble is the whole of
the four-byte-link story told two paragraphs above, and it is why the stack top is pinned: with a
fixed top, which side of the wobble a build lands on cannot decide whether it links. Read the row you
are actually building and ignore the coincidence.

**An allow-list that overflows fails much worse than a cache miss, and silently.** This is the
asymmetry the arena is arranged around and it has not changed. The S3 sends its whole tag database; a
radio that answers `ALLOW_ADD` with `NAK NO_SLOT` makes `v2transport.cpp` set `allowListInUse = false`
and stop using the radio-side inventory lock altogether, until the link is restarted. The lock is a
security feature, the degradation is to not having it, and the only notice is one line in the web
log. There is no bound to appeal to either: `tagDB` on the S3 is an unbounded `std::vector`, so
nothing says a site cannot have more tags than we have room for. A cache miss, by contrast, costs one
tag 140 ms instead of 15, once, and is counted as `cev`/`cevu`. So the list is never made to wait for
room and the cache always gives way — and 1024 is also what the ESP32-C6/H2 radio uses, so a fleet
that keeps its lock on a C6 keeps it on a TLSR.

**What else changed as a result:** no build option trades one tenant
against the other any more, because no build option sizes any of them: the arena does, once, and
the split between them is a run-time consequence of how many tags the site actually has.

Report `.bss`, `_stack_size_`, `_bss_headroom_` and `_ramcode_tail_` with any change that adds state
or RAM code, and spend the headroom knowingly: it is 1208 bytes in the tightest build, and what is
behind it is the eleven-block band.

## Flashing

From the AP's web interface (boards with an SWS line to the TLSR, `FLASHER_AP_SWS`): the update page
flashes `binaries/TLSR/OEPL_TLSR_AP.bin` (`firmware_TLSR.json`, type `F8`); to try the beta, upload it
to the AP's file system as `/TLSR_AP_FW.bin` and flash that file (`/flash_tlsr` without a URL). With a Telink USB
programmer: `../OpenEPaperLink_TLSR/flashing/telink_flasher.exe 1 8258 wf 0 -i out/OEPL_TLSR_AP.bin`.
The S3 needs a firmware with protocol v2 (V2Transport), as for the C6/H2 from 0x0023; with an older
S3 the radio is not recognised.

## Tests

```
make -C test
```

builds `src/ap.c`, `v2link.c` and `blockcache.c` with a PC version of `hal.h` (`test/host_hal.c`:
virtual clock, serial line and radio as buffers) under AddressSanitizer and UBSan, and runs
`test/test_ap.py`. That script plays the S3 (protocol v2 written from the specification, not from the
C code) and the tags (802.15.4 frames): start-up and negotiation, HELLO/STATUS, SET_CONFIG, duplicate
requests, check-ins (full, short, old 28-byte), preloaded and on-demand block transfers with the
contents of every part checked, partial requests, busy/unknown tags, late requests, ping, the
inventory lock and learning mode, the transmit power never moving from what the S3 configured,
event retransmission and the full event queue, pending timeouts,
the fallback to ASCII when the S3 goes quiet, cache eviction with two transfers competing for slots,
tag return data, bursts with airtime interleaved with S3 requests, how far the repeat budget moves on
what the fleet and the channel report, a mid-burst re-ack to a tag whose radio is off, a stale ping
still being answered, and LOG frames. 45 cases.

Six of those are the run-time split of the arena between the cache and the allow-list, and they build
their own library (`libap_split.so`): an arena of 24592 bytes with a floor of 2 blocks, so the cache
starts at 5 and the list has 16384 bytes above the floor. It gives up a block as the list passes 992
and 2016 entries compressed and 525, 1038 and 1551 at full width, which puts both encodings'
thresholds in one build where the firmware's own arena reaches one at most. The six: every threshold
in both directions with nothing refused except at the cap; the four-byte entry identifying a whole
MAC (two families sharing their low three bytes, a third that is not listed, one differing only in
`mac[3]`, and a removal that must not take the wrong entry); a MAC with nowhere in the 32-family
prefix table kept at full width; the surrendered block being the eviction policy's choice rather than
the one at the list's edge; a burst surviving the block under it being moved; and the whole database
arriving while every slot is in use. The eviction-policy tests keep a pinned six-slot geometry
(`CACHE_ARENA_BYTES=29120 CACHE_BLOCKS=6 CACHE_BLOCKS_CAP=6`), because they are about which block the
policy throws out and they say so with exact slot counts.

`src/tlsr/string_min.c` gets its own binary (`test/test_string_min.c`, 10131 checks) because it is
firmware-only: it replaces the SDK's `string.c`, and its `memcpy` copies a word at a time where the
pointers agree mod 4, which is type punning that only `may_alias` makes safe. The test therefore runs
under the same `-fstrict-aliasing` the firmware is built with, plus ASan and UBSan, whose
misaligned-pointer check is precisely the fault the chip would take.

`hal_tlsr.c` is not covered by these tests; it builds without warnings, and its radio register use was
checked against the driver library's `rf_trx_state_set()`, `rf_tx_pkt()` and `rf_rx_buffer_set()`.
