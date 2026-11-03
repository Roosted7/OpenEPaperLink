# Radio findings backlog

Everything turned up by four investigations on 2026-10-31: the tag firmware of every family, Telink's
driver blob and register tables, the code `tc32-elf-gcc` 4.5.1 generates for the hot paths, and the
ESP32-S3 side against upstream. Kept in one place so none of it has to be worked out again, including
the items that were checked and rejected — those carry their reasons, which is the expensive part.

Status keys: **[done]** landed on this branch · **[open]** worth doing, not done · **[no]** checked,
decided against, reason given · **[?]** unverified, would need hardware or a source we could not reach.

Per-tag-type timing lives in its own file, [radio-tag-timing.md](radio-tag-timing.md), because it is
reference material for the whole project rather than a backlog item.

---

## A. Tag behaviour (see radio-tag-timing.md for the table)

- **[done]** `hwType < 0x2E` was standing in for five different behaviours. Replaced by
  `tagListensAfterAckMs()`: Opticon `0x06`–`0x09` listen only at `pw + 40 ms` (radio off until then),
  88MZ100 `0x05`/`0x24`/`0x26` busy-wait the full `pw` and cannot be told apart from a Solum M2,
  nRF52811/EFR32 `0x28`–`0x2D` sleep the full `pw`, ZBS243 listens 5 ms early for a short wait and
  **25 ms** early once it sleeps instead of busy-waiting.
- **[done]** Stale-request limits tightened: PING 2 ms (ZBS243, Opticon), check-in 4 ms with three
  tries (88MZ100), block request 6 ms (nRF52811).
- **[done, and it reverses the line above for PING]** Tightening was the wrong direction for a PING.
  A PING carries no hwType and comes from a tag we have never had a full check-in from, so the family
  is unknown while the windows run 2–20 ms; and the receive queue is not flushed between attempts, so
  a late PONG is usually still read. Priced in tag receiver-on milliseconds, a wrong refusal costs the
  tag a scan attempt and risks 40 ms per round of "No AP found" for ever, against 992 µs of our air —
  about three orders of magnitude the wrong way. `tooLateToAnswer()` now bounds the PING by the
  **widest** window instead: `832 + 20000 − 992 = 19840 µs`, where 832 µs is the tag's own ping
  airtime (`ageUs` runs from the start of its frame) and 992 µs is the PONG's. The check-in and
  block-request limits are left alone, but the same argument applies to them and they are worth
  re-deriving: `getAvailDataInfo()` enables the receiver before its attempt loop and never calls
  `radioRxFlush()` at all, so its 14 × 5 ms windows have the same slack, and a `rep` that is not 0
  (facts 13 in HANDOVER.md) may partly be replies the tag did read, one attempt later, rather than
  replies it lost.
- **[done]** Burst stops when the tag reports the transfer complete, checks in again, or asks for
  another block. ZBS243 and Chroma leave their receive loop as soon as the block is complete, so
  repeats after that are airtime nobody hears.
- **[done]** `pleaseWaitMs = 0` on the mid-burst re-ack is free on ZBS243/Opticon/Chroma/88MZ100 and
  the TLSR tag — all five guard the wait and fall into their receive loop with the radio on, and the
  Opticon's `pw + 40` is inside the branch it does not take — but an nRF52811 sleeps unconditionally
  and then re-initialises its radio, so the frames sent during that are the ones it comes back for.
  The ack still carries 0 for everyone and the burst holds its next frames for
  `tagRadioBackAfterAckMs()` instead: a non-zero wait would have to be ≥ 10 ms to clear the ZBS243's
  `pw − 5` and the TLSR's `pw − 10` underflow, and a wait under 35 ms is one a ZBS243 spends
  busy-waiting with its receiver **on**.
- **[done, and the numbers in the original entry were stale]** `tagListensEarly()` keyed "listens
  early" on `hwType < 0x2E`, but the M2 range ends at `0x27` and `0x28`–`0x2D` are M3 tags
  (`resources/tagtypes/28.json`…`2D.json`). So six M3 hwTypes were treated as ZBS243s and had their
  bursts aimed 25 ms before they start their radio at all — the expensive direction, since a part they
  never heard costs them another 315 ms request round. Fixed to `0x28`.

  The "~21 ms per block" this entry claimed no longer existed when it was re-measured: it was the
  state before `ZBS243_EARLY_SLEEP_MS` went in, when the burst was aimed at `pw − 5 + 1 = 136`. With
  the model correct the figure is **~1 ms** for a tag whose hwType is known, and ~30 ms for one whose
  is not (`pw + 5` against a wake at `pw − 25`). Aiming an unknown tag's burst early was **rejected**:
  a short burst — a two-part re-request is four frames — would then land entirely before an M3 or an
  Opticon starts listening, trading ~30 ms of one family's awake time for ~315 ms of another's. What
  was taken instead is the second option, `DATA_WAIT_MARGIN_MS` `20 → 25 + 1`, so the ZBS243's wake
  lands on the moment the burst can really start rather than 5 ms before the block exists; it also
  removes a systematic `lateMs` that was shortening every burst. The wait still only rises and stays
  far above 35.
- **[?]** hwTypes `0x54`–`0x5A`, `0x60`–`0x62`, `0x66`–`0x71` (Hanshow Nebular), `0xD0`–`0xD5`
  (GFSK10), `0x81` (Chroma Aeon 7.4"): defined in the tagtype JSONs but no firmware in the
  organisation reports them, so their windows are unknown. If any behaves like the Opticon, the table
  in `tagListensAfterAckMs()` needs them.
- **[no]** A minimum inter-frame gap beyond the 600 µs already left: no family needs more. Even the
  single-buffer tags (Opticon, 88MZ100) get ~4.9 ms per frame and drain it in time.

---

## B. Code generation (tc32-elf-gcc 4.5.1)

Three premises that turned out to be false, recorded so they are not re-investigated:

- `sys_tick_per_us` is `#define sys_tick_per_us 16` (`timer.h:51`) — a compile-time constant, and
  **16 at both 24 and 48 MHz**, because the 8258's system timer is a fixed 16 MHz island. Every
  `/ sys_tick_per_us` is already folded to a shift; in `halRadioRx` it is folded into the byte
  extraction and costs nothing.
- `-fshort-enums` has no effect on this firmware: no variable or struct field anywhere has an enum
  type. It must stay for ABI compatibility with the blob regardless.
- The divide helper in `div_mod.S` is not a software divide — it drives a hardware divider at
  `0x800660` **with interrupts disabled**, ~45–55 cycles. So every `/` and `%` on a 32-bit integer is
  a ~1 µs interrupt-off critical section. None is in an interrupt handler today; it must stay that way.

Findings, measured instruction counts:

| | item | cost now | after | status |
| --- | --- | --- | --- | --- |
| B1 | **UART RX interrupt**: one interrupt per byte at 2 Mbaud, 110 instructions per byte of which only 25 do work. ~4 µs against a 5 µs byte period, i.e. 55–80% of the CPU while the S3 streams. Fix: an outer drain loop that re-reads `reg_uart_buf_cnt` (strictly drains more, never less, so no loss of overrun margin), plus grouping `uRxIdx`/`uHead`/`uTail`/`uOverflows` into one struct so GCC anchors them on a single base instead of shuffling five literals through high registers. | 110 instr/byte | ~100 single-byte entry, ~63 when the loop catches a second byte | **[done]** |
| B2 | **`v2Crc16` is bitwise**: 117 instructions per byte on the receive side (register pressure pushed the constant into `fp`), 95 on the send side. A 259-byte DATA_CHUNK costs ~734 µs; a 4 KB block ~11.7 ms. Table-driven version is 12 instructions per byte. 512 bytes of `.rodata`, i.e. **flash, no SRAM**. Verified bit-identical over 4000 random vectors and against the CCITT-FALSE check value `0x29B1`. Shared header, so the S3 gets the same win. | 734 µs/chunk | ~81 µs | **[done]** |
| B3 | **`-fpack-struct` makes `struct cacheSlot` 21 bytes**, so `slots[c].ver` is eight byte loads, seven shifts and seven ORs — 22 instructions, in a 7-iteration loop. `-fpack-struct` only removes padding: an explicit `__attribute__((aligned(8)))` still raises alignment and GCC then emits word loads (measured: 22 → 7). Costs +21 bytes of `.bss`. **Only for internal structs** — never for `src/proto.h`, which is over-the-air layout, and never for `struct pendingData`, whose 27-byte stride is documented as matching the closed-source firmware. | 22 instr | **2** | **[done]** `ae0177bb` (merge `207a5129`): better than predicted, because the 24-byte stride also turns the index arithmetic into a constant add. `.text` −448 bytes, `.bss` +24. The three bytes of padding the raised alignment paid for were not waste for long — the cache's eviction state moved into them later (`1585ab92`). |
| B4 | **COBS decode**: 22 instructions per byte. GCC 4.5 if-converted the three short-circuit bounds tests into condition-code→boolean→test sequences, and register pressure spilled `len` to the stack, reloaded every iteration. Fix: validate the bounds once per run (the `code` byte gives the run length) so the per-byte test is just the zero check, and make the counters `unsigned` to drop two truncation shifts. Real logic rewrite of a framing routine, so malformed-input behaviour must match exactly. | ~22 instr/byte | **9** | **[done]** `fbb4b5e0` (merge `207a5129`). The equality of malformed-input behaviour was *tested* rather than argued: 124 million inputs against the old implementation, exhaustive for short frames, identical everywhere. `v2CobsDecode` lives in the shared `oepl-radio-v2.h`, so the S3 and the C6/H2 got it too. |
| B5 | **`halMillis` calls `__udivsi3`** (interrupt-off) at all 32 call sites. An early exit when fewer than `TICKS_PER_MS` ticks have passed is behaviourally identical — the existing code adds 0 to both accumulators in that case — and takes the divide out of >99% of calls at 48 MHz. GCC is not at fault for the divide itself: Thumb-1 has no `umull`, so a reciprocal multiply is unavailable. | ~50 cycles/call | 0 | **[done]** |
| B6 | **`uartTxDmaStart` divides by a runtime baud rate** on every DMA chunk (`n * 10000000UL / uBaud`). Precompute microseconds-per-byte in `uartSetup`. Exact at 2 Mbaud (10000000/2000000 = 5), and DMA is only used above the boot baud, so the rounding difference never arises. | ~50 cycles/frame | 0 | **[done]** |
| B7 | **`struct halRxFrame` is packed for no reason** — `len`/`rssi`/`lqi` sit before two `uint32_t`, so those land at offsets 3 and 7 and are stored byte-by-byte: 14 instructions where 2 would do. Never goes over the air. Reordering also keeps `data` congruent mod 4 with the DMA source, which B8 needs. | 14 instr | 2 | **[done]** `34410ffa` (merge `207a5129`): the two `uint32_t` moved to the front and `rxGain` behind `data`, which puts `data` at offset 13, i.e. 1 mod 4 like the DMA slot's `p + 5`. A static assertion pins that residue rather than trusting the comment — and it caught the first attempt, which had the test backwards. The same commit also took the `__divsi3` out of `halRadioRx` with the LQI lookup table, which is the `[no]` item below that was waiting for this one. |
| B8 | **`memcpy` is byte-at-a-time**: 9 cycles/byte, so 99 bytes is ~18.6 µs. Honest framing: a byte on air is 32 µs, so this is 0.5% of a frame's airtime and irrelevant for throughput; it is 2–4% of the ~450 µs of per-reply processing. Word-at-a-time (guarded by `n>=16 && ((d^s)&3)==0`) gives ~2.25 cycles/byte. Alignment permits it: `rxSlots` is 4-aligned with a multiple-of-4 slot size, so the source is always ≡1 mod 4, and B7's layout makes the destination congruent. **The only item with real correctness exposure** (type punning under `-fstrict-aliasing`; `memmove`'s overlapping case must stay byte-wise). | ~18.6 µs/99 B | ~5.6 µs | **[done]** `a06904b5` (merge `207a5129`), deliberately its own commit so it can be dropped on its own. `may_alias` on the word pointers is what actually defends against `-fstrict-aliasing`; `memmove` stayed byte-wise in both directions. `test/test_string_min.c` makes 10131 checks under ASan, UBSan and strict aliasing, and was mutation-tested: dropping the run-in, the threshold, the xor guard or the tail, and inverting `memmove`'s direction, are all caught. `.text` +80 bytes, no RAM. |
| B9 | **Free `uint8_t` truncation removals** — GCC emits `tshftls #24; tshftrs #24` inside loops because it cannot prove an 8-bit accumulator stays in range. Widening the *local* to `unsigned` moves it out; the final narrowing store truncates for free. Three sites: `addCRC`/`checkCRC` (7→5 instructions in the ~102-byte sum, ~21→17 µs per call, 2–4 calls per frame), `uartIrq`'s `cnt` (24→22 per byte, pairs with B1), `v2CobsEncode`'s `code` (2 of 13 instructions per byte, ~12 µs/chunk). One word each. | ~30–40 µs/chunk | — | **[done]** |

Checked and rejected, with reasons:

- **[no] `irq_handler`'s own prologue/epilogue** is genuinely redundant — `__irq` in `cstartup_8258.S`
  already saves r0–r12, LR and CPSR before calling it, so 12 instructions and 36 bytes re-save what is
  saved. But it is 12 of 110 on the UART path and removing it needs a hand-written naked ISR in TC32
  assembly. It uses the separate 512-byte `irq_stk`, so it is **not** part of the SRAM headroom
  problem — and measured since, the deepest interrupt path uses 116 of those 512 bytes, so there is no
  pressure from that side either.
- **[no] `getPacketType` / the packed `struct MacFcs` bitfields.** GCC already does this well: it
  merges `destAddrType` and `srcAddrType` into one masked compare and tests `panIdCompressed` with a
  shift and a sign branch. A hand-masked version measured **worse** (20 instructions against 17).
- **[no] `getBlockDataLength`.** 42 iterations × 12 instructions ≈ 12 µs, once per block request. A
  popcount measured 60–276 instructions, a nibble table ~48. The README used to record it as one of
  the functions matching the closed-source binary byte for byte; it is not, and cannot be — it is
  `static` with one call site, so GCC inlines it and the image has no such symbol for the matcher to
  name.
- **[no] 64-bit `dataVer` compares on wire structs.** `blockReq->ver` is at offset 1 of a genuinely
  packed over-the-air struct, so the 22-instruction byte-wise load is unavoidable and correct. 3–4
  times per block request ≈ 4 µs. A shadow aligned copy would halve it and duplicate state.
- **[no] `memset`, `memcmp`.** 5 and 6–7 instructions per byte with no waste; `memcmp` is only used
  for 8-byte MAC compares (~0.8 µs).
- **[done, with B7] The `__divsi3` in `halRadioRx`** was `ZB_RADIO_RSSI_TO_LQI`, a signed divide by
  the constant 84. Thumb-1 has no `smull`, so no reciprocal multiply; once per frame, ~1–2 µs, and an
  interrupt-off window besides, since the helper drives the chip's one hardware divider. It was only
  worth folding in if `halRadioRx` was being edited anyway, which B7 did: `34410ffa` replaced it with
  the 85-byte `rssiToLqi` table, clamped to the macro's own −99…−15 dBm AGC range.
- **[no] `-O3` repo-wide.** Measured: `ap.c` 4129 → 8288 instructions (+101%), all of it inlining of
  the big handlers, none of the hot loops improved — the problems are algorithmic or ABI. ~5 KB of
  flash and a bigger `.ram_code` for nothing.
- **[no] `-funroll-loops` repo-wide.** `string_min.c` 78 → 439 instructions. On `string_min.c` alone
  it does give an 8-way byte loop (~1.8×) for one makefile line, but that is half of B8's benefit for
  5.6× the code.
- **[no] `-fno-strict-aliasing`.** Nothing in the hot paths depends on aliasing today. Only consider
  it scoped to `string_min.c` if B8 is taken.
- **[no] Removing `-fpack-struct` or `-fshort-enums`.** Both are ABI-fixed by the blob. B3 is the
  correct way to escape `-fpack-struct` where we own the layout.

**Two corrections to the audit's own numbers, from the agent that made it.** The headline "~40 % of
the CPU recovered" for B1 assumed the drain loop usually catches a second byte, which could not be
verified statically — a single-byte entry measures 110 → ~100 instructions, and the amortisation to
~63 only materialises when a second byte is already there. It should be, at one byte per 5 µs against
a sub-microsecond body, but **this wants a hardware measurement**: a GPIO toggle at interrupt entry and
exit, or `halSerialOverflows()` and throughput before and after. Likewise the "60–80 % of the CPU in
the UART interrupt" figure rests on a Cortex-M0 cycle model for a core with no published timings — the
*ratio* (77 % of the instructions are fixed overhead) is solid, the absolute share may be lower.

**Byte-for-byte caveat, as it was and as it turned out.** The worry was that B8 and the
`-funroll-loops` option would break the `string.c` match the README claimed. Measured with
`tools/fingerprint.py` before B8 landed, there was almost nothing left to break: the closed image
carries the SDK's `memcpy`, `memset`, `bcopy` and `memcmp`, and of those this firmware matches
`memcmp` alone — `string_min.c` had already diverged from `string.c` (it drops the `is_ev_buf()`
call, and the SDK's versions also return `NULL` for a zero length and post an exception on a null
pointer), and `bcopy` is not in this build at all. Fingerprinting before and after B8 gives the same
set, `memcmp` included, so that change cost nothing that was still there. What it did do is make a
future return to the SDK's `string.c` a decision about speed rather than about RAM. Everything else
proposed is new code with no closed-source counterpart. `tools/fingerprint.py` re-verifies after any
change; the README carries the current count and how to reproduce it.

---

## C. Radio hardware and Telink's driver blob

- **[done]** **The whole RX trailer is settled, from Telink's own table.** The B91 driver handbook
  prints the 802.15.4/zigbee receive format as a table of addresses, and with `rba` at the length byte
  (index 4) it reads, verbatim:

  | address | content | description |
  | --- | --- | --- |
  | `p[phr+5..8]` | `r_tstamp[31:0]` | time stamp |
  | `p[phr+9]` | `pkt_fdc[7:0]` | low byte of recorded frequency offset after demodulation |
  | `p[phr+10]` | `{1'b0,rx_packet_chn_efuse[2:0]},{1'b0,pkt_fdc[10:8]}` | high byte of the same |
  | `p[phr+11]` | `pkt_rssi` | recorded packet RSSI |
  | `p[phr+12]` | `[0]` crc error `[1]` sfd error `[2]` link layer error `[3]` power error `[4]` long range 125K `[7]` NoACK | status |

  The 8258's shipped macros put RSSI at `p[p[0]+2]` and the status byte at `p[p[0]+3]` with
  `p[0] == p[4]+9`, which lands on exactly those addresses — so the layout is confirmed from a primary
  source *and* from the driver, not by arithmetic alone. **The field is eleven bits, `pkt_fdc[10:0]`**,
  which is what the datasheet means by "valid bits of actual frequency offset may be less than 16 bits";
  masking the high byte with `0x07` is right. The same table is used for BLE, hybee, private TPLL and
  private SB — only the address arithmetic differs — and the cells survive intact in the TPLL and SB
  tables where the site's HTML mangles the zigbee one.
- **[done]** **The field above the offset is `rx_packet_chn_efuse[2:0]`, in bits 6..4** — not a gain
  stage, and not part of the offset. The old code read it as `p[phr+10] >> 3`, which takes the
  documented zero at bit 3 along and so **reported every value doubled**. Nothing decided anything on
  it, so the cost was to the investigation and not to the air, and it was expensive there: the upper
  field's apparent tracking of the offset's magnitude (value 4 → mean offset 5, 8 → 78, 10 → 320) is an
  artefact of **one byte feeding both halves of the old decode** — `(p[phr+10] & 0x07) << 8` is the low
  three bits of the very byte the upper field was read from. That correlation was the whole reason the
  eleven-bit layout fell under suspicion, and it was never evidence. `halRxFrame` now carries the raw
  high byte (`fdcHigh`) rather than a decoded value, and `FO_LOG` prints it, because the one thing a run
  can still prove is whether bits 7 and 3 are the zeroes the table claims.
- **[done]** **`rf_start_stx(addr, tick)` arms the state machine to *begin* settling at `tick`**, and
  the hardware's own TX settle (`REG_ADDR16(0xf04)`, 150 µs default, 113 µs minimum) follows. The code
  added `ZB_TX_WAIT_US` (120, which is the *driver's software* wait for the manual path) on top and
  ignored the hardware value, so every scheduled reply went out ~270 µs later than asked. Now the
  settle is read from the register and subtracted. **Measured consequence:** at a true 950 µs, 6 of 7
  check-ins were repeated; the value that had been working was ~1220 µs, so `RADIO_REPLY_AT_US` is
  1220 and is documented as a real air-start delay. **The same sweep took `RADIO_REPLY_PONG_US` to 970
  as collateral, and that was wrong**: a PONG has to be *complete* inside a scanning ZBS243's 2 ms
  window, and at 992 µs of frame, 970 left 38 µs of margin. A delay that is right for a check-in is
  not right for a PONG, and this is how the two got coupled — the change came in `c3b7cc3b`, a commit
  about CRC tables, so nobody chose it.

  **It then took two commits to actually undo, and the first one did nothing.** `f27ae53e` edited the
  `#ifndef RADIO_REPLY_PONG_US` fallback in `src/ap.c` while the makefile still passed
  `-DRADIO_REPLY_PONG_US=970` on every compile, so the `#ifndef` never expanded and the edit was dead
  code: every image ever flashed, the "fixed" one included, sent its PONG at 970. `d3443c20` changed
  the makefile default and verified the literal in the built image. **The lesson is the one worth
  keeping: every tunable here is a makefile variable passed with `-D`, so the `#ifndef` fallbacks in
  `ap.c` are documentation, not defaults — change the makefile, and check the image.** (Removed in
  `ed9d0142`: the timing figures are constants in the source now, one definition each, and no longer
  makefile variables.)

  **And then 700 failed too**, which retires the whole approach rather than the number: three fixed
  offsets have now been tried against a tag three feet away with `PING_LOG` showing a frame correct in
  every field each time, so the fault is not the value but the clock it is measured in
  (`lastRxEndTick` plus a settle read once from `0xf04`). `278e8a5c` replaced the constant with
  `RADIO_PONG_SPREAD`, on by default, which walks the offset across the window so that the attempt
  that works reports what the timing actually is. `RADIO_REPLY_PONG_US` is only consulted when the
  spread is off. (The spread was turned off in `7226343f` and removed in `ed9d0142`; the PONG goes
  out at 1700 µs.) The 1220 of `RADIO_REPLY_AT_US` rests on the same two clocks, so it is
  also no longer safe to treat as a fact about tags: see the cold-tag section of
  `ARM_Tag_FW/OpenEPaperLink_TLSR_AP/HANDOVER.md` and
  [radio-stock-binary-diff.md](radio-stock-binary-diff.md).
- **[done] The air-time reference, audited end to end** — because the PONG failures say our frames
  leave later than we ask, and three separate constants were blamed before anybody checked which of
  them the datasheets actually pin down. Taking them one at a time:

  | thing | status | source |
  | --- | --- | --- |
  | `0xf04` is the TX settle, **in microseconds**, default 150, minimum 112.5 | **confirmed** | B91 handbook, `rf_start_stx` section: "The default value of TX_SETTLE time is 150us… should not be less than 112.5us". Reads 149 here, so the model `preamble = tick + 149` is right about *this* term |
  | the scheduled tick fires on **greater-than**, not equality | **confirmed** | same section: "Trigger immediately when the current tick value is greater than the set tick" |
  | `clock_time()` is **16 MHz, constant**, at 24 and 48 MHz alike | **confirmed** | `chip_8258/timer.h`: `#define sys_tick_per_us 16` under the comment "system Timer : 16Mhz, Constant". So there is **no scale error**: every µs↔tick conversion in this firmware is exact, and a multiplicative explanation for the PONG failures is excluded |
  | `airtimeUs(psdu) = (psdu + 6) * 32` | **confirmed** | 802.15.4 at 2.4 GHz: 4 preamble bytes + SFD + PHR, 32 µs/byte. Gives 992 µs for the 25-byte PONG and 832 µs for the 20-byte ping, both of which match. E is a latency, not a rate, so this is unaffected by it either way |
  | the **RX timestamp's capture instant** | **not documented anywhere** | see the next entry |
  | `0xf16` bits 0 and 7..4 — `FLD_RF_TX_EN_DLY_EN` and `FLD_RF_R_TX_EN_DLY`, a **programmable transmit-enable delay** | **unmodelled, and never read** | the decisive new fact: **`rf_drv_init()`'s register tables contain no write to the `0xf00` block at all.** Both tables decode to 33 writes, all in `0x04xx`/`0x12xx`; a scan of every `.text` section in `libdrivers_8258.a`'s `rf_drv.o` for literals in `0xf00`–`0xf2f` finds exactly two functions, `rf_start_stx` (`0xf18`) and `rf_start_srx` (`0xf28`). So `0xf04`, `0xf03` and the rest of `0xf16` are at **chip reset values** on every boot, and `rf_start_stx` only ORs in bit 2 and leaves the delay fields as found. Whatever is in them is added to every scheduled reply. Now reported at boot as `lld` (`halRadioTxDelayRegs()`) |

  **Conclusion: the datasheets say the transmit model is right to within about 200 µs**, so a single
  unmodelled radio constant is unlikely to be worth the several hundred microseconds the PONG
  failures imply — and the one place left for it to hide is `0xf16`, which is four bits wide and now
  gets read. That makes the premise worth doubting as much as the model; see the entry after next.
- **[?] The RX timestamp's capture instant is undocumented, and bounded at ±192 µs.** The handbook's
  receive-packet table — the same table that settles `pkt_fdc`, the RSSI and the status byte — gives
  the four bytes at `p[phr+5..8]` as "time stamp byte0..3" and says nothing about when the counter is
  sampled. Telink's own 802.15.4 MAC passes it to `zb_macDataRecvHandler()` untouched
  (`ieee802154/mac/mac_phy.c:630`), so there is no reference arithmetic to copy, and nothing else in
  any SDK reads it. `hal_tlsr.c` assumes **capture at the start of the PHR**: frame end is
  `ts + (phr + 1) * 32 µs` and the preamble began `5 * 32 µs` earlier.

  It cannot be badly wrong, and that is the useful part:

  - **Not late in the frame.** A capture at the *end* of the packet would put `tsEnd` a whole
    `(phr + 1) * 32 µs` past the true end, the `irqLag` sanity check would see a negative lag and
    every frame would fall back to the interrupt time. `tsfb` is 0 on hardware in every build, so the
    counter is sampled inside the frame's header.
  - **Only six bytes of room inside the header.** Preamble start to end of PHR is 4 + 1 + 1 bytes,
    **192 µs**. Reading it as the preamble start would make our replies 160 µs *early*; reading it as
    the end of the PHR makes them 32 µs *late*.

  So this assumption can account for ~32 µs of lateness and at most 192 µs of error in either
  direction — **not for several hundred.** The two branches in `halRadioRx()` are at least mutually
  consistent: both place the preamble `(phr + 6)` bytes before the frame end, so `sfdUs` and `ageUs`
  mean the same thing whichever runs. Settling it properly needs a scope or a second radio; the cheap
  partial test is to raise the `irqLag` check's tolerance and see what lag it reports.
- **[done, and the answer turned out to be “nothing”] What a late transmit would have broken.** Kept because the audit is what ruled the receive side out, and because it is the ledger to reuse if a transmit delay is ever found: the entry below settles that there is none, so every “if `E` is 500 µs” below is hypothetical and `nf`/`busy` in particular are **not** suspect. Call the unmodelled delay
  between the armed tick plus settle and the first preamble symbol `E`. Audited in full, because only
  one of these turns out to matter and the rest were worth ruling out explicitly:

  - **`txEndTick` / `halRadioTxEndUs()` is `E` too early**, by construction: it is computed as
    `start + (txSettleUs + airtime)`. Everything anchored on "when our frame leaves the antenna"
    inherits that.
  - **`sampleNoise()` is the one real casualty.** `NF_TX_RECOVER_US` is 400 µs and the guard is
    `halMicros() - halRadioTxEndUs() < 400`. If `txEndTick` is 500 µs early then `sinceTx` reads
    500 µs too large, the guard is **entirely consumed**, and idle-RSSI samples can be taken while our
    own frame is still going out. That biases the noise floor **upwards**, and `busy` — which is
    `nfBusy / nfSamples` against `NF_BUSY_DBM` — feeds the burst budget through
    `BURST_BUSY_HOLD_PCT`. It is consistent with the observation already in HANDOVER that the floor
    reads −80…−92 while quiet windows used to read −86…−94, "busier than earlier runs". **So `nf` and
    `busy` are suspect until `E` is known, and `NF_TX_RECOVER_US` should be raised to `400 + E` once
    it is.** Nothing else reads them to make a decision that cannot wait.
  - **The burst anchor is not materially wrong.** `blockStartTimer` comes off `halRadioTxEndUs()` for
    the ack, and is then offset by `tagListensAfterAckMs()`, which is 5, 10, 25 or 40 **milliseconds**.
    500 µs against that is noise. The exception is the **early-burst path**, whose margin is documented
    as ~1 ms (`EARLY_BURST`, `pleaseWait 10 ms`): `E` of 500 µs is *half of it*, and in the safe
    direction (the burst starts early, and starting early is what the margin is for), but it should not
    be spent twice.
  - **`tooLateToAnswer()` is not affected by `E` at all.** It reads `rx.ageUs`, which is a
    *receive*-side quantity: how old the request is, not when our reply leaves. The only error that
    reaches it is the timestamp anchor, i.e. ≤192 µs, against thresholds of 4000 µs (check-in),
    5000 µs (XFC), 6000 µs (block request) and 19840 µs (`PING_ANSWER_MAX_US`). That is at worst 5 %
    of the tightest one, and `late` has been 0 in this installation's entire history, so nothing is
    anywhere near a threshold. **Leave it alone.**
  - **`airtimeUs()` is exact and unaffected** — see the table above.
  - **The millisecond-scale constants do not care, and this is stated rather than left open:**
    `pleaseWaitMs`, `tagListensAfterAckMs()`, `dataWaitMs`, `BURST_WINDOW_MS`, the tags' 300–315 ms
    receive windows and `RADIO_BURST_GAP_US` (a *relative* spacing between frames, so a common latency
    cancels out of it entirely). An `E` of 500 µs is 0.5 ms against 5–315 ms everywhere here.
  - **Structural, and easy to miss: replies and bursts do not share a transmit path.** A reply with
    `RADIO_REPLY_STX=1` goes out through `rf_start_stx`, i.e. auto mode, from a radio that
    `rf_set_tx_rx_off()` has just switched off and that must bring its PLL up inside the 149 µs settle.
    A burst frame goes out through `radioSend()`, i.e. **manual** TX: `ZB_RADIO_TRX_SWITCH(RF_MODE_TX)`,
    `WaitUs(120)`, `ZB_RADIO_TX_START`. So an `E` that lives in the auto-mode entry affects replies and
    **not** bursts, and the closed-source firmware — which is manual TX throughout — is not a
    like-for-like comparison for it. Any measurement of `E` has to say which path it was taken on.
- **[done, and it was the premise] The window starts after the tag's acknowledge wait.** This entry
  used to hedge between "our transmit chain is late by 300–850 µs" and "the tag's window is not where
  we think". The second was right, the first does not exist, and the measurement settled it: a
  scanning ZBS243 three feet away accepted a PONG at **1350 µs** after the end of its ping and ignored
  150, 300, 450, 600, 750, 900, 1050 and 1200 — three scans, nine pongs each.

  The cause is in the tag's driver, not in any datasheet. `radioTx()` ends
  `while (!mAckTimePassed);` (`Tag_FW_ZBS243/zbs243_shared/soc/zbs243/radio.c:159-163`) and
  `sendPing()` sets `fcs.ackReqd = 1` (`tag_fw/syncedproto.c:142`), so the radio runs the full
  802.15.4 acknowledge wait — `macAckWaitDuration`, 54 symbols = 864 µs from the end of the frame —
  before raising the "ack time happened" interrupt, and only then does `detectAP()` reach
  `t = timerGet() + …`. **During that wait the tag's radio cannot receive at all**, so a reply aimed
  inside it is destroyed in hardware and leaves no trace on our side: `late` stays 0, `larm` stays 0,
  the frame is correct in every field, and the tag simply asks again. 864 µs plus the 8051 return path
  is the measured 1200–1350.

  **It also closes the oldest question in HANDOVER — why the stock firmware works and ours did not.**
  Stock answers everything at about 720 µs *measured from the start of its own transmit wrapper*, and
  that wrapper re-initialises the whole radio before every frame, a flash read among it, on top of
  `sleep_us(500)`. Its reply therefore lands on the far side of the ack wait **by accident**: stock
  works because it is slow and imprecise, and this port failed because it is fast and hit exactly the
  970 µs it was told to.

  Three things from the earlier hedging are retired, with their reasons:

  - The `E` this section asked for **does not exist**. `0xf04` is the settle in microseconds, the
    scheduled tick is honoured, `clock_time()` is a constant 16 MHz, and `airtimeUs()` is exact — so
    the transmit model was right all along, which is why no source could account for the lateness.
  - The timestamp bound (±192 µs, from `tsfb` being 0) stands and was the useful part: it is what
    ruled the receive side out and forced the search onto the tag.
  - **`nf`/`busy` are off the suspect list.** They were suspect only because `txEndTick` would be `E`
    early; with `E = 0`, `NF_TX_RECOVER_US` of 400 µs is the guard it always was and needs no change.

  The re-referencing this forced on every family is in
  [radio-tag-timing.md](radio-tag-timing.md), "Where a window actually starts". The short version:
  **only the ZBS243 and the 88MZ100 are affected**, the Opticon — the other 2 ms family — is clean
  (`ackReqd = 0` on all four paths), and both affected families set `ackReqd = 0` on the block
  request, which is why the burst ack has always worked. The consequence for `RADIO_REPLY_AT_US` is
  that 1220 sits 30–130 µs *before* a ZBS243 starts listening, which is a quantitative explanation
  for `rep` at 4–17 %; the all-family budget is [1250, 2396] and ~1700 is 450 µs inside both edges.
  `tooLateToAnswer()` needs no change, because `ageUs` carrying the tag's own frame airtime cancels
  the late window opening to within ~100 µs on the binding family.
- **[done]** **`rf_drv_init()`'s register tables never write the PA registers** (`0x1225`, `0x1226`,
  `0x1227`), so without `RADIO_APPLY_TX_POWER` the transmit power is whatever the chip reset left —
  not a documented level. They are reported at boot now. `rf_set_power_level_index` silently ignores
  indices above 59, which is why the closed-source firmware's `rf_set_power_level_index(63)` did
  nothing. **`rf_power_Level_list` indices 50–59 are `0x00`** — a bogus setting, not −50 dBm; our
  `powerTable` uses 0–49 only and must never be widened.
- **[done]** **The `RF_DMA_BUSY()` early return in the RX interrupt silently loses a frame** and is now
  counted (`bdrop`). Nothing can be done at that point — the next frame is already being written over
  the one we were about to deliver — but it tells us whether anything further is needed. On hardware so
  far: 0. The macro is not what its name says and the window is wider than it looks: see the DMA FIFO
  entry below, which works out that `bdrop` can only rise if interrupts are held off for ~200 µs.
- **[done] The RSSI this radio reports IS gain-compensated, so the idle-noise metric is measuring the
  channel and not the receiver.** Asked because an uncompensated reading would make every idle sample
  a function of whatever the AGC happened to be doing, and a bimodal distribution is exactly what two
  gain states would look like. It is compensated, on four independent grounds:

  - **The datasheet specifies it as an absolute figure.** §3.3.3: "The TLSR8258 provides **accurate**
    RSSI… RSSI can be read from the 1byte at the tail of each received data packet… **RSSI monitoring
    resolution can reach +/-1dB**." A quantity specified to ±1 dB is a calibrated power, not a raw
    detector output; an uncompensated reading could not be specified at all.
  - **The same paragraph blesses our exact use.** "If no data packet is received (**e.g. to perform
    channel energy measurement when no desired signal is present**), real-time RSSI can also be read
    from specific registers which will be updated automatically." Reading `0x441` with no packet
    present, which is what `rf_rssi_get_154()` and `halRadioIdleRssi()` do, is the documented use of
    that register and not a trick.
  - **`rf_drv_init()` programs a per-gain-stage offset table into the hardware.** `tbl_rf_zigbee_250k`
    writes `0x0460`–`0x0465` with `0x36 0x46 0x51 0x61 0x6d 0x78` = 54, 70, 81, 97, 109, 120: six
    monotonic values, steps of 16, 11, 16, 12, 11, spanning 66 dB — a gain range, and the top entry
    120 is exactly the top of the 0…120 code range that `- 110` maps to −110…+10 dBm. The compensation
    is applied in hardware from that table, which is why no driver function anywhere has to do it:
    a scan of every `.text` section in `libdrivers_8258.a` for literals in `0x400`–`0x4ff` finds only
    `rf_pn_disable` (`0x401`) and the EMI functions. **Nothing reads or writes the gain registers.**
  - **Both generations expose one RSSI byte and no gain byte.** The trailer carries `pkt_rssi` alone
    (the handbook's receive table), and the packet path and `rf_rssi_get_154()` both just subtract 110.
    On B91 the gain latch is a *separate* register from the RSSI latch (`reg_rf_modem_gain_lat0` at
    modem+0x5c, `reg_rf_agc_rssi_lat` at +0x5d) and the DMA trailer carries neither — so Telink's own
    design assumes one byte is sufficient, which it is only if that byte is already corrected. And an
    802.15.4 stack's ED scan and `ZB_RADIO_RSSI_TO_LQI` are both built straight on it.

  **So `p[phr+11] - 110` and `rf_rssi_get_154()` are the same compensated quantity** and the per-frame
  RSSI the per-tag power controller uses is sound. (The one wart is the one already recorded: the
  inline casts the byte signed before subtracting while the packet path leaves it unsigned, which never
  bites because the code never reaches 128. Do not "fix" one to match the other.)

  **What this does NOT buy, and it is the part that matters here: compensated is not the same as
  comparable.** The compensation is a six-entry table, so the reading is only as good as the gain
  state being known and the table being right for it, and two things are undetermined when there is no
  packet. The AGC locks during RX settle ("RX Settle (RX_EN -> Before AGC Lock)", platform handbook)
  and then tracks, so at a random idle sample the gain sits wherever the last frame or the last burst
  of interference left it; and the table's own steps are **11 to 16 dB**, which is the same order as
  the ~23 dB between the two modes we measured. Two adjacent states' offsets, or one state's detector
  running out of range near its edge, would produce exactly the bimodal reading that was mistaken for
  the channel moving. **That is a hypothesis, it is cheap to test, and the test is in the next entry.**
- **[open, and it is one register read] Log the gain state next to the idle RSSI.** If the bimodality
  is the AGC rather than the channel, the gain state is bimodal too and the correlation settles it in
  one window. The candidate register is **`0x440`**, by position: on B91 `reg_rf_agc_rssi_lat` is
  modem+0x5d and `reg_rf_modem_gain_lat0` is modem+0x5c, and on the 8258 the RSSI latch is `0x441`
  (`rf_rssi_get_154` reads it) — so if `0x441` ≡ modem+0x5d then the gain latch ≡ `0x440`. **Treat it
  as a guess to be checked, not a fact**: third-party 826x maps name a different function at `0x43c`
  than the same positional arithmetic would give, so the modem base is not firmly established, and no
  Telink header names any gain register on any TC-series part. The experiment is to read `0x440`
  alongside each idle sample and bucket it the way the RSSI is bucketed; if it takes two values that
  track the two RSSI modes, the metric is measuring the receiver and the fix is the entry below. If it
  is constant, the channel really is bimodal and the histogram is already the right instrument.
- **[no, for now] Steering the AGC.** Two halves, and only one of them is even tempting.

  **Pinning the gain while sampling idle RSSI** is the attractive one: it would make the samples
  comparable to each other and to the absolute `NF_BUSY_DBM` threshold, which is the single thing the
  metric needs. But it cannot be done yet and possibly cannot be done at all: **neither Telink SDK
  generation exposes any receive-gain or AGC API** — not `rf_set_rx_maxgain`, not a fixed-gain mode,
  nothing; the only AGC-related symbol in any header is the B91 RSSI latch's name. The register that
  third-party 826x maps call `reg_rf_rx_gctl` (`0x43c`) is named nowhere in Telink's own headers, is
  written by nothing, and `0x43c` is also what the positional arithmetic above would make the
  frequency-offset debug latch — so even its identity is unsettled. Writing an undocumented analogue
  control to hold the gain during idle sampling means writing it back correctly before the next frame,
  with no documented way to read what it was. Do the measurement above first.

  **Steering the gain for reception is the wrong direction, and the fleet says so right now.** Every
  tag weaker than about −55 dBm on this installation has stopped checking in while the strong ones are
  fine, and the receiver is the suspect. Anything that reduces sensitivity hurts precisely those tags,
  and the gain tables are the analogue front end — the same argument that already closed re-tuning
  `tbl_rf_zigbee_250k`: `0x0460`–`0x0465` are the RSSI offsets, so getting them wrong corrupts every
  reported RSSI *and* the per-tag power controller that reads it, while getting the gain itself wrong
  silently costs sensitivity that nothing in the diagnostics would attribute to us. Priced against the
  far tags rather than the near ones, there is no version of this worth trying before the far tags are
  back.
- **[no, premise was wrong]** **Trim the crystal.** The job was framed as "the AP is certainly
  untrimmed, because `rf_drv_cap()` does nothing when flash 0x77000 holds 0xFF". **That byte holds
  0x60 on this module**, so `rf_drv_cap()` has been applying cap code 32 on every boot all along, and
  the capacitor bank is in circuit (analog 0x8a reads 0x60, so `FLD_XO_CAP_OFF_ANA` is clear). Cap 32
  is the board's factory calibration. Evidence: `boot pa 0000C81F settle 149 rst 1 rstev 00000000
  xtal 00006060`. Nothing was written to flash, and there is nothing to write.
  A 66-sample sweep over 42 cap codes gave per-tag slopes of +0.67, +0.06, +1.69, −1.72, +0.93 and
  −2.04 units per code — **scattered either side of zero, no agreed sign**, so neither a better code
  nor the sign convention nor the kHz/LSB scale was resolved. Within-tag spread is 100 units (one tag
  returned 389 and 63 at the same code), and the spread is **per-tag, not uniform** — one tag is tight
  at sd 3.0 across every code, and that tag's slope is +0.06. `rep` fell from 16.7 % to 4.0 % while
  the carrier was being moved, i.e. moving it did no measurable harm and the trim did no measurable
  good. Any future trim is a delta from 32, not from "untrimmed".
- **[no, as a reading job — it has to be measured]** **The offset's sign convention and its Hz per LSB
  are documented nowhere.** This was searched to exhaustion rather than skimmed: not in the TLSR8258
  datasheet (§3.3.3 says only that the offset "can be read from the 2bytes at the tail of the data
  packet" and that the valid bits vary by mode), not in the B91 driver handbook or the newer platform
  handbook, not in any `rf.h` / `rf_reg.h` / `dma.h` of any Telink SDK generation, and **no code
  anywhere reads the field at all** — every hit for the `*_dma_rx_offset_freq_offset` macros in every
  Telink SDK is a header definition with no caller, Zephyr's B91 802.15.4 driver takes RSSI and ignores
  it, and `pkt_fdc` as a token appears nowhere outside the two handbook pages. Note the deliberate
  contrast: Telink annotates the RSSI macro `//data len:1, signed` and leaves the frequency-offset
  macro unannotated. **There is nothing to port; the only way to settle this is a bench measurement.**

  What the code assumes meanwhile, and why: two's complement over the eleven bits. The receive
  tolerance is specified symmetrically (−300…+300 kHz for 802.15.4, −250…+300 kHz for BLE 1M), and an
  offset-binary field would read near 1024 at no offset while this installation reads 16…330. The
  assumption has **never been exercised** — bit 10 has not been seen set, so no reading so far is
  negative at any signed width, which also means our own "every reading is positive" was never
  evidence about our carrier either way.

  The earlier measurement that an 8-bit signed read tightened the pooled within-tag spread from 100.3
  to 68.0 is **retired, with its reason**: wrapping a distribution that straddles the wrap point always
  compresses its measured spread, so a width sweep scored on spread will always pick the narrowest
  width that wraps. It was never evidence about the field's width, and the handbook says bits 10..8
  carry real offset.

  **What to measure, and a hypothesis worth testing first.** If the field spanned the specified ±300 kHz
  over its eleven signed bits it would be ≈0.3 kHz/LSB, and one crystal cap code — worth a few ppm, so
  several kHz at 2.48 GHz — should move it by order ten units. The cap sweep measured 0.06…2.04 units
  per code, ten to a hundred times too little, in both signs. The handbook's own wording suggests why:
  it is the offset "**after demodulation**", i.e. the residual left by the receiver's own carrier
  recovery, not the raw error. A closed AFC loop would move its lock point with our crystal and leave
  the residual almost unchanged, which is exactly the shape of the sweep result. If that is right the
  field **cannot measure our own carrier at all**, and the crystal-trim investigation dead-ended for a
  second, structural reason on top of the bad decode. Testing it needs a signal generator with a known
  offset, or a second AP deliberately mistuned by a large number of cap codes — enough to exceed any
  pull-in range — and a log of `fo` against it. Until then, treat `fo` as a relative per-tag number
  with no unit, and do not key anything on it.
- **[no] `rf_start_stx2rx`.** The opcode is no longer a guess — **`0x87` is confirmed from Telink's own
  shipped B85 driver library.** `tc_platform_sdk`'s `chip/B85/drivers/lib/libdriver.a` has all six
  auto-mode entry points (the zigbee SDK's `libdrivers_8258.a` ships only `rf_start_stx`/`_srx`), and in
  that object `rf_start_stx2rx` is **byte-for-byte identical to `rf_start_stx` but for one immediate**:
  `…4885a216bb…` against `…4887a216bb…`, i.e. `mov r2,#0x85` against `mov r2,#0x87`. `rf_start_btx` is
  `0x81` in the same position. So the sequence is exactly the one we already use, with the command byte
  changed: `0xf18 = tick`, `0xf16 |= BIT(2)`, `0xf00 = 0x87`, `0xc0c = addr`. The **scheduled tick is
  therefore preserved identically** — the `0xf18` store and the `FLD_RF_CMD_SCHEDULE_EN` read-modify-write
  are the same instructions in both functions, so a reply would still leave at the tick it is armed for.

  It is still the wrong thing to do, and Telink's own header says why. Verbatim, from
  `chip/B85/drivers/lib/include/rf_drv.h`: *"In this mode, a packet is sent first, RF module waits for
  10us, stays in Rx status until data is received or timeout expires, **then exits this mode**. Timeout
  duration is set by the parameter timeout_us… Max value: 0xfff (4095)."* Three consequences, and each
  one alone is disqualifying for an access point:

  - **It is single-shot, and it leaves receive afterwards.** The handbook's work-mode table says the
    same thing in general terms: in auto-switch mode "after sending or receiving a packet of data…
    some modules about RF tx/rx will be closed automatically", against manual mode where they "will
    **NOT** be automatically closed". An AP has to hear every tag in the fleet at any moment; a receiver
    that shuts down after one frame is a regression, and the window it offers is capped at 4095 µs
    against the 300 ms a tag spends in its block window. This is the same argument that already
    rejected `rf_start_srx` further down, and it applies with more force here.
  - **The safety net gets worse, not better — which inverts the item's own premise.** The hope was that
    it "removes the `txTimeouts` failure mode where a lost TX interrupt leaves the radio deaf". It does
    the opposite: today the TX interrupt restores manual RX at a known point just after the frame, and
    `radioWaitTxFree()` forces it back after 10 ms if that interrupt is lost. With `0x87` the hardware
    leaves receive **by itself**, at a moment that depends on whether a frame arrived, so software still
    has to notice and re-arm — on a *new* interrupt (`FLD_RF_IRQ_CMD_DONE`, `0xf20` bit 5, which
    `ZB_RADIO_TRX_CFG` masks off and which nothing in any Telink SDK uses) and at a *later* and less
    predictable time. A lost interrupt then costs a longer deaf window than it does now.
  - **The timeout our own init disables makes the behaviour undefined.** The B85 signature is
    `(addr, tick)` only — the `timeout_us` in the doc block is a leftover, and the real timeout lives in
    `reg_rf_rx_timeout` (`0xf0a`) behind `FLD_RF_RX_TIMEOUT_EN` (`0xf03` bit 2). `ZB_RADIO_TRX_CFG`
    clears that bit on purpose (`write_reg8(0xf03, read_reg8(0xf03) & 0xfb)`, commented "disable SRX
    timeout interrupt"), so in our build the documented exit condition is switched off and what the
    state machine does instead is not written down anywhere.

  And the gain it was wanted for is not measurable. `rf_start_stx` already schedules the transmission,
  so nothing about reply timing changes; the only difference is who restores RX afterwards, which saves
  a handful of register writes at interrupt latency in a path where the tag is not due to transmit for
  at least its `pleaseWaitMs`. Trading an always-on receiver for that is not a trade. **Not implemented,
  and `halRadioTxReply()` was not touched.**
- **[no] DMA FIFO mode for the RX ring** (`FLD_DMA_FIFO_EN`, `reg_dma2_mode` bit 2). The *shape* of the
  idea was right — a hardware-advanced ring closes the window that `bdrop` counts, and unlike ping-pong
  it would not cut us to two buffers — but the hardware is not there on this chip, and four independent
  things say so:

  - **The depth selector was removed.** On 826x the DMA control was one 16-bit register
    (`reg_dma2_ctrl = REG_ADDR16(0x50a)`) and its mode enum named `FLD_DMA_FIFO8 = BIT(15)|BIT(14)|…` —
    a buffer-count selector. On 8258 the register is split into an 8-bit size and an 8-bit
    `reg_dma2_mode` (`0xc0b`), every surviving flag is renumbered down by the same eight bits, and
    **`FLD_DMA_FIFO8` is simply gone while the bare `FLD_DMA_FIFO_EN` name is carried forward.** A ring
    whose depth cannot be named is not a ring you can configure, and "more than two buffers" was the
    entire point.
  - **The pointer pair is block-wide, not per channel.** `reg_dma_rx_rptr`/`_wptr` (`0xc28`/`0xc29`) and
    `FLD_DMA_RPTR_NEXT`/`_CLR`/`_SET` are one pair for the whole DMA block, shared with UART RX. There
    is nowhere to keep per-channel FIFO state for channel 2.
  - **Nobody has ever set the bit.** `FLD_DMA_FIFO_EN` is declared once, in the shared enum under
    `reg_dma0_mode`, and across every Telink SDK generation, every pvvx repository and every
    third-party project it appears **only in headers** — no `.c` anywhere assigns or reads it, nor
    `FLD_DMA_AUTO_MODE`, `reg_dma2_mode`, or the `FLD_DMA_RPTR_*` flags. The 8258 datasheet has no DMA
    register table at all, so there is no document to fall back on either.
  - **B91 had to add new hardware to do this job.** It does have a real multi-buffer RF RX ring —
    `rf_set_rx_dma_fifo_num()` writing `reg_rf_rx_wptr_mask` ("mask value must in
    (0x01,0x03,0x07,0x0f)", i.e. 2/4/8/16 buffers) and `reg_rf_bb_rx_size` — but those are **RF baseband
    registers with no 8258 counterpart**, not a DMA mode bit. If the DMA FIFO bit had done it on the
    previous generation, that hardware would not have been added.

  And `rf_rx_cfg(size, 0)` writes `reg_dma2_mode`; the 826x equivalent does a full-word assignment that
  clears the whole mode field. So even a bit that worked would be wiped by the next init — **the exact
  mechanism that already defeated ping-pong** in the entry below.

  Worth recording what the drop actually is, because the premise deserves a correction too:
  `RF_DMA_BUSY()` is `is_rf_receiving_pkt()`, which reads the RF state machine's "receiving a packet"
  bit (`0x448` bit 5) — **not a DMA-busy flag.** It means a *new* frame's reception had already begun by
  the time the interrupt for the previous one ran, so the frame being lost is the one we were about to
  deliver. Our software ring already re-points the DMA in that interrupt, so the window being raced is
  just the interrupt latency, and two 802.15.4 frames cannot be closer than their 6-byte
  preamble+SFD+PHR, i.e. **192 µs**. So `bdrop` can only rise if something holds interrupts off for
  ~200 µs — and the longest interrupt-off region in this firmware is the `div_mod.S` hardware divider at
  about 1 µs. If `bdrop` ever becomes non-zero, the bug is whatever started holding interrupts off, not
  the buffering scheme. On hardware it remains 0.
- **[no] Hardware RNG** (`random_generator_init`, in `libdrivers_8258.a` alongside `rand`). **Nothing in
  this firmware wants randomness.** The one caller of anything random is the fallback MAC in
  `halRadioGetMac()`, and it is reached only when `flash_read_mid_uid_with_check()` fails *and* flash
  `0x76000` is blank — i.e. never, on a part that has a flash UID, which is all of them; this AP's MAC
  comes from the UID halves as it is meant to. Nowhere else: the protocol has no nonces and no
  encryption (hardware AES is already `[no]` for that reason), and nothing backs off or jitters —
  replies go out at scheduled ticks and burst frames at a fixed `RADIO_BURST_GAP_US`, so there is no
  contention window to randomise.

  The premise about the seed was right and then some. `analog_read(0x8a)` contributes **no entropy at
  all**: that is the crystal-cap register, and per the trim entry above it holds 0x60 on every boot of
  this module because `rf_drv_cap()` writes the factory calibration there — it is a constant, not a
  variable the trim would *make* constant. So the fallback MAC is `clock_time()` at boot and nothing
  else: power-on to that call, deterministic to within boot jitter. But the fix for that path is not a
  better generator, it is **persistence** — the MAC is recomputed every boot and never written back, so
  a tag that paired with one would lose the AP on the next power cycle no matter how good the entropy
  was. If anyone ever does care, write the generated MAC to `0x76000` once and read it thereafter.

  Against that: `random_generator_init()` touches the ADC, so it would impose an ordering constraint on
  boot (nothing else in this firmware uses the ADC today, and that would become a thing to remember).
  **Not against it, though: the code is already in the image.** `tc32-elf-nm` on a default build shows
  `random_generator_init`, `rng_init`, `adc_rng_result` and the `rng` state all linked — the SDK pulls
  them in and nothing of ours calls them — so the flash and the Marsaglia state are bytes already
  being paid, and calling it would cost the call and the ADC ordering and nothing else. The verdict
  does not change, because it is a code path that does not execute and the real fix for that path is
  persistence; but the cost is not the reason, and nobody should decline this on a RAM argument that
  does not hold.
- **[no] Hardware RX ping-pong.** It is real (`rf_rx_buffer_set(addr, size, 1)` sets `reg_dma2_mode`
  to 3), but: it gives **two** buffers against our eight, which is a downgrade for catching up after a
  long transmit burst; **there is no register anywhere that says which bank was just filled**, so it
  needs a software toggle plus the assumption that one interrupt equals one bank advance, which breaks
  on a coalesced interrupt or a CRC-failed frame and then silently reads the wrong buffer forever; and
  **`ZB_RADIO_TRX_CFG(size)` calls `rf_rx_cfg(size, 0)`, which writes `reg_dma2_mode = 1` and clears
  ping-pong again** — so enabling it in `halRadioInit()` before `TRX_CFG` would have been silently
  undone. Telink's own 802.15.4 MAC does what we do, only worse: it sends the ACK inline with RX DMA
  disabled, busy-waiting up to 1000 µs, so its deaf window is up to a millisecond against our tens of
  microseconds. `RFDMA_RX_DISABLE`/`ENABLE` are only `reg_dma_chn_en &= / |= BIT(2)`; while the bit is
  clear the baseband keeps demodulating into nowhere, which is exactly why `ZB_RADIO_RX_BUF_CLEAR`
  zeroes `p[0]`/`p[4]` so a stale buffer fails the length check. We already do that.
- **[no] Re-tuning `tbl_rf_zigbee_250k` / `tbl_rf_init`.** Both tables decoded (33 plain 8-bit register
  writes; format is 4-byte `{adr_lo, adr_hi, dat, cmd}` entries, all `cmd == 0xc3`). Identifiable:
  `0x0405` is `reg_rf_acc_len` = 4; `0x0408`–`0x040b` are the 4-byte sync word `0x000000A7`, i.e. three
  preamble zero bytes plus the 802.15.4 SFD — and the handbook says explicitly that **zigbee mode's SFD
  is auto-detected and needs no software configuration**; `0x0460`–`0x0465` are six monotonic values,
  almost certainly a per-gain-stage RSSI offset table, so changing them would only corrupt reported
  RSSI. Everything else in both tables is undocumented in every source available — not in
  `register.h`, neither datasheet, nor the handbook. **The decisive argument:** the newer 2025
  `telink_802154_sdk`'s `rf_drv.o` has `tbl_rf_zigbee_250k`, `tbl_rf_init`, `rf_chn_table` and
  `rf_power_Level_list` **byte-for-byte identical**. Telink shipped the same values across two SDK
  generations behind a −99.5 dBm typical sensitivity spec. Guessing at an analogue front-end register
  is a good way to silently lose 6 dB.
- **[no] `rf_pn_disable()`.** It writes `0x401 = 0` and `0x404 &= ~0x20`, but the zigbee table already
  sets `0x404 = 0xc0`, so whitening is already off in zigbee mode. It exists for the EMI test path.
- **[no] `rf_start_srx(tick)` for timed RX.** SRX is **single-shot** — it returns to IDLE after one
  packet, so it would need re-arming per frame, and each re-arm pays the RX settle (150 µs default),
  creating a *new* deaf window per frame. Manual RX, which we use, is explicitly better: the handbook
  says the settle is paid only once on entering RX and the device goes straight to the next receive
  after a packet. Its hard-coded `0x0fffffff` first-timeout shows its intended use is "listen for a
  reply then give up", the BLE connection-event pattern. Neither Telink SDK uses it.
- **[no] `rf_set_power_level` instead of `rf_set_power_level_index`.** Identical; `_index` is a
  one-line table lookup that tail-calls it. The only gain is access to all 128 raw PA codes instead of
  the 50 tabulated ones.
- **[no] Hardware AES.** `aes.o` is the genuine engine (key at `0x550`–`0x55f`, data through `0x548`,
  AES-128 ECB single block). OEPL frames are not encrypted and the protocol has no field for a MIC.
- **[no] `timer.o`.** Exports only `sleep_us`, mode/start/stop and a GPIO capture init. We already use
  the system timer directly, and the radio's own hardware timestamp beats any GPIO capture for RX.
- **[open, low] `emi.o` self-test.** `rf_emi_single_tone`, continuous/burst/ramped TX with PRBS9, and
  `rf_emi_rx` + `rf_emi_get_rxpkt_cnt` + `rf_emi_get_rssi_avg` for a PER/RSSI receiver test. ~1.8 KB
  in per-function sections, so `--gc-sections` charges only what is called. Real value for diagnosing
  a bad module or antenna, and a two-AP PER test. Entering any EMI mode destroys radio state
  (`rf_emi_rx` re-inits the driver and repoints RX DMA at its own buffer), so it belongs behind a debug
  command that reboots afterwards.

Two small notes worth keeping:

- `rf_rssi_get_154()` casts `read_reg8(0x441)` to **signed** before `-110`, while the packet path and
  Telink's own MAC treat the byte as **unsigned** before `-110`. Telink is inconsistent with itself. It
  never bites because the code for −110…+10 dBm spans 0…120, never ≥128. **Do not "fix" one to match
  the other.**
- `LOGICCHANNEL_TO_PHYSICAL` is correct: with `set == 0`, `rf_set_channel` treats its argument as MHz
  above 2400 and selects a VCO band from thresholds. `(ch-10)*5` gives 2405…2480 MHz. (With bit 15 of
  `set` it would index `rf_chn_table`, the BLE channel map — not what we want.)

---

## D. Parity with the ESP32-C6/H2 firmware

The C6/H2 has **not moved since the TLSR port began** (one commit in that tree, a version bump), so
this is a pure parity diff against 0x0024 with nothing newer to back-port. The H2 builds the C6
sources; there is no separate H2 behaviour.

- **[done]** Logging was in the radio path: `v2Log()` sent the frame itself through a blocking
  `halSerialWrite`, and the AP logs a block request **before** the ack to that request is scheduled.
  `3da03ccc` made it only queue — and **stopped one step short**, which took a second commit to
  notice: the *formatting* was still ahead of the ack. `logReRequest()` emits one `diagAdd` per missed
  part and each one divides, and on the TC32 a division is a call into `div_mod.S` that masks
  interrupts for ~50 cycles against the one hardware divider. At 27 re-requested parts that was enough
  to miss a tick 1070 µs away: the first evening `larm` existed it caught one window with `larm 1` and
  `gap 1542` against the configured 1220, in the same window as `rparts 27`. `775d4a4f` builds the
  lines after `rf_start_stx` is armed. **The principle to carry forward:** a scheduled reply fires
  from its own tick, so work after the arming costs the tag nothing and work before it is on the
  clock — "out of the radio path" means out of the arming path, not merely off the wire.
- **[done]** The S3 never asked for LOG frames (`SET_CONFIG` flags carried only the lock bits), so a
  release TLSR build was blind — on a chip with no second UART, unlike the C6 with its debug console.
- **[done]** `SET_CONFIG` accepted channel 27, which `channelList` carries because the C6 list does.
  The TLSR takes it literally and programs ~2485 MHz, where no tag transmits, and stays deaf until the
  next `SET_CONFIG`. This is finding C8, never fixed on either side.
- **[done]** Counters the port had lost: `CANCEL_XFER` sent, events dropped on a full queue, blocks
  that never arrived in time, and the S3's answer time for a block a tag is already waiting for.
- **[done]** The unguarded write in the re-request log line.
- **[done]** The TX power comment claimed the TLSR applies what the S3 asks for; it does only with
  `RADIO_APPLY_TX_POWER`, which is off by default — the same end state as the C6.
- **[done, and the obvious optimisation was wrong]** `PLEASE_WAIT_DOWNLOAD_MS` was an untuned C6
  constant, so it is measured now — but **not** in order to shorten it. A tag *sleeps* through that
  wait: at 35 ms or more a ZBS243 sleeps `pw − 25` with its radio off, and the nRF52811, Opticon and
  EFR32 sleep whatever they are told, while **below 35 ms the ZBS243 busy-waits with its radio ON**.
  So shortening the wait past that threshold stops a tag sleeping and makes it burn radio instead —
  the opposite of how it looks. A longer pause is also kinder to a coin cell, which sags under the
  burst and the panel refresh and recovers during the wait. And being too short is the expensive
  failure: the block is not ready, the tag has held its radio on for nothing, and it pays another ack
  exchange and another 300 ms window. The wait therefore moves in **one direction only** — it starts
  at 140 and rises when this S3 proves slower, never falling. The case it guards was observed: a block
  arriving 178 ms after the request against a 140 ms promise. An earlier version of this eased the
  value back down; that was removed as worth microamp-milliseconds of sleep against a real risk of
  underestimating again after having been right.
- **[done]** `PLEASE_WAIT_DOWNLOAD_MS` is no longer a constant on the TLSR AP: `f748f696` replaced it
  with `dataWaitMs`, which starts at the C6's 140 (`DATA_WAIT_START_MS`), rises to the slowest answer
  seen plus `DATA_WAIT_MARGIN_MS` whenever the S3 proves slower, backs off by half again when a block
  missed its burst entirely, and stops at `DATA_WAIT_MAX_MS` (550, what the closed-source firmware
  asked for unconditionally). `dl`/`dlx`/`dlate` report it. **The direction is the opposite of what
  this item proposed**: the wait only ever rises, because the tag spends it asleep — the reasoning is
  in the entry above, and it also retires the "~115 ms of tag radio burned" framing this item was
  written around. The margin is no longer a bare 20 either: `85b34e4d` made it
  `ZBS243_EARLY_SLEEP_MS + BURST_MARGIN_EARLY_MS` (26), because at 20 a ZBS243 woke 5 ms before the
  block could exist and the burst then started late and charged the difference to its own window
  budget. The C6/H2 still has the flat 140. Closes M3 stage B of the task list for the TLSR.
- **[done]** `halResetReason()` returned a hard 0 where the C6 gives `esp_reset_reason()`. `e3dcb0e5`
  decides it from the two things that outlive a reset: analog register 0x3b, which only a power cycle
  clears, and the watchdog's status bit `reg_tmr_sta` bit 3, read before `drv_wd_clear()` wipes it. No
  cookie means power-on, a cookie with the watchdog bit means the watchdog, a cookie without it means
  something reset the chip with the power on — on this board the S3's reset line or the flasher. It
  reports the C6's `ESP_RST_*` numbers so the S3 displays it unchanged, and `halResetReasonRaw()`
  sends the raw evidence beside it (`rst`, `rstev`), so a reading that does not fit that story is
  visible rather than silently wrong. Nothing on the 8258 *names* a reset cause, so this is inference,
  which is why the evidence travels with it.
- **[done, and decided against growing it]** **Cache sizing nuance:** 28.6 KB is almost exactly one
  23104-byte 3-colour image (6 blocks raw, about 4 as transferred). Two concurrent transfers evicted
  each other's not-yet-served blocks, turning 15 ms cached requests into 140 ms downloads. This item
  named the mechanism as "only the block being burst is pinned, and `cacheTouch` happens only at
  burst start", and it was worse than that: with least-recently-used eviction the victim was *always*
  the block about to be wanted, because a tag walks its image from block 0 upwards, so a preloaded
  image's oldest slot is the next one it will ask for.

  **Two separate things were decided, and both are closed.** The policy: `1585ab92` dropped age as the
  first criterion and groups the candidates instead — blocks nothing offered when they were stored,
  then blocks their tag has already had, then blocks still to come, furthest ahead of the tag first —
  with age deciding only inside the first two groups, which leaves the block that has just gone out
  last in line so a tag re-requesting lost parts still finds it. Where each transfer stands is learnt
  from the block requests and the bursts and kept in the slots' own padding (8 bytes of `.bss` in
  total, where the obvious side table cost 48 and did not link). `cev` counts evictions and `cevu` the
  ones never served to a tag, which is the figure that says a site needs a bigger cache. There is no
  `cacheTouch` in this firmware any more; covered by `b6033cc4`. **The C6/H2 still evicts
  least-recently-used.**

  The size: `bf5bfe66` settled it at 7 blocks, by argument rather than by what happened to link. An
  eighth does not change the failure mode — two concurrent transfers want 8 to 12 blocks and 12 is
  48 KB on a 64 KB part, so the cache was never going to hold two images and what a block buys is one
  block of prefetch runway. Measured, an eighth block is 4184 bytes and the only place to take them
  from is 512 allow-list entries (4096), which swaps a bounded, counted slowdown (`cev`/`cevu`) for
  the allow-list's silent and unbounded failure: one `NAK NO_SLOT` and `v2transport.cpp` stops using
  the radio-side inventory lock until the link restarts, with `tagDB` on the S3 an unbounded
  `std::vector` so no tag count can be ruled out. The full budget, its measurements and the stack
  analysis that paid for the headroom are in the RAM section of
  `ARM_Tag_FW/OpenEPaperLink_TLSR_AP/README.md`. `CACHE_BLOCKS=8 MAX_ALLOWED_MACS=512` remains
  available for a site whose tag count is known.
- **[done]** Shared with the C6, not a port gap: duplicate detection compared only `lastRxSeq` while
  the spec allows 4 outstanding DATA_CHUNKs. Now a ring of the last `V2_CHUNK_WINDOW` sequence numbers
  and how each was answered, in both firmwares; an older duplicate is answered as it was the first
  time and not handled again. **Robustness, not a live bug:** the S3 sends every control message
  stop-and-wait under `txActive` (`request()` in `ESP32_AP-Flasher/src/v2transport.cpp`), so its
  retransmission is always the newest sequence number the radio has seen and the old check caught it.
  Only `sendChunks()` has more than one request in flight, and `cacheChunk()` is idempotent. What the
  old code did get wrong for real, if only in the diagnostics: a repeated final DATA_CHUNK of a block
  a tag was waiting for fed `dataWaitArrived()` a second, longer sample, and `pleaseWaitMs` only ever
  rises. The one request that is not idempotent on its own is SET_PENDING (it resets `attemptsLeft`
  and overwrites the slot). CANCEL_PENDING, DATA_DROP, ALLOW_CLEAR/ADD/REMOVE and SET_CONFIG are each
  idempotent in isolation but destructive out of order: an older one re-applied undoes a newer one.
- **[done]** Shared quirk: tag-return-data dedupe was on `dataVer` only, so two different tags
  returning the same `dataVer` within a minute lost the second — and the version is the version of the
  data the tag was *given*, not something it invented, so two tags running the same image or both idle
  collide by construction. `1cf92b7f` adds the `memcmp` on the MAC, in both firmwares.
- **[done]** Documentation disagreed with the firmware in three places, all in
  `radio-protocol-v2.md`: it said the radio does not send LOG frames and does not announce bit 5 (the
  TLSR AP does both); it justified keeping debug output on UART0, which the TLSR does not have; and it
  recorded a design decision of "one `pleaseWaitMs` default for all tags, a tag-specific value only if
  a measurement shows it saves battery", which the hwType-keyed timing departs from without a
  before/after charge measurement. `1db7ec24` fixed all three by describing the two radios separately
  rather than by picking a side — and, for the third, by recording what was *really* decided: the
  waits offered are still identical for every tag, only the burst is aimed per family, and that saving
  is reasoned from the tag firmware's own behaviour and not measured.

  The document drifted again in two further places while this branch ran, fixed with this entry: the
  download wait is described as "140 ms, not tuned", which is still the C6/H2 but no longer the TLSR
  AP (see `PLEASE_WAIT_DOWNLOAD_MS` above); and the burst was described as one pass plus repeats to
  twice the requested parts, capped at 42 frames, which is the C6/H2's burst and not the window-sized,
  adaptively-repeating, early-stopping one the TLSR AP now sends. **The lesson for the next change:
  any statement in that document about timing or burst shape now needs saying per radio**, because the
  two have diverged on purpose and a sentence that is true of one reads as a lie about the other.
- **[done] Bugs the TLSR port fixed that the C6 still had:** no length check before `sendPong()` on
  any `PKT_PING` frame; `checkCRC(buffer+hdr, len-hdr)` on `TAG_RETURN_DATA` without checking
  `len > hdr`, an underflow on a short frame; and `lastBlockRequest = 0` at boot, so the C6 answered
  every block request in its first 1.2 s with `CANCEL_XFER` (the TLSR pre-ages it). Fixed in the
  C6/H2 by `6a30f861` (merge `727a3909`), which found a fourth of the same shape on the way:
  `PKT_XFER_COMPLETE` reads its source from a normal header and clears that MAC's pending data, so a
  short frame there cleared a pending entry picked out of whatever was left in the receive buffer.
  **Still not upstream** — it is a commit of its own against the C6/H2 sources, so it is ready to be
  sent as a PR, but no PR has been opened.
- **[done]** Logs the port dropped, restored as counters by `12f37d26`: cache evictions (`cev`, with
  `cevu` beside it), "busy with another tag" — which also split the one `cxl` counter into `cxlb` for
  a busy cancel and `cxln` for a no-data one, two things that mean quite different things — and a
  `SET_CONFIG` line, but only when the channel, power, lock mode or LED brightness actually moves,
  since the S3 re-sends the same configuration after every HELLO. Making room meant splitting the
  transfers line: at bench values it was already 98 characters and was silently dropping `dw` and
  `png`, because `diagAdd()` skips a field that will not fit and says nothing. The bursts stayed on
  `xfer`, the cancels and the cache moved to a new `blk` line, and the test suite now asserts each
  line's last field is still present — the only way a dropped field becomes visible at all.
- **[no]** Sub-GHz/CC1101, the UART0 debug console, a heap-sized cache, LED PWM with gamma: all
  deliberate — no hardware, one UART, 64 KB of RAM, and no known LED on these modules.
- **Derived requirements** of `radio-chain-analysis.md`: 1 (reply deadlines) met and now measured — the
  only implementation in the project that enforces them deterministically; 2 (nothing blocking in the
  radio path) met after the logging change; 3 (keep the radio busy as briefly as possible) is **the one
  the burst work trades against** — a 52-frame burst is ~257 ms against the C6's ~208 ms, which is why
  the burst now stops early and why the cancel counter was added; 4 and 5 met.
- **E1 note:** `main()` ignores `apLoop()`'s return value, so the `waitMs` / `v2MsUntilWork()`
  computation is only exercised by the host simulator. Correct for a mains-powered AP that never
  sleeps, but it is dead code on hardware and should be understood as such.

---

## E. ESP32-S3 side

The S3 on this branch is **3 + 10 + 1 lines** different from upstream master, all from the radio LOG
frame, and the branch is 0 commits behind. So everything below is a **live upstream bug in current
master**, not a fork regression.

**The insight that decodes most of the log:** `LittleFSImpl::exists()` is implemented as
`open(path, "r", false)`, and `VFSImpl::open()` logs an error on a miss. So *every* negative existence
check prints `[E][vfs_api.cpp:105] ... does not exist, no permits for creation`, and every `remove()`
miss prints the `vfs_api.cpp:182` line. These are the Arduino FS layer complaining about ordinary
checks, visible only because this env builds with `CORE_DEBUG_LEVEL=1`. Guarding a call with
`exists()` silences nothing — `exists()` logs too.

- **[done, diagnosis]** **E1a — who holds the file descriptor.** Not a leak: it is the web UI's preview fetch.
  `serveStatic("/current", ...)` with the library's default `_tryGzipFirst` probes `<path>.gz` first
  (that is the `.raw.gz` line, and the fingerprint proving a browser GET was in flight), then opens the
  file; the `File` is copied into the response and the descriptor lives until the request object is
  destroyed on client disconnect. The web UI reloads `current/<mac>.raw` on **every** tag websocket
  update — i.e. exactly when a new image is queued. A 14 KB image makes it reproducible because the
  response is in flight longer.
- **[done]** **E1b — the actual bug: `processXferComplete()` ignores the FS return values.** When the
  rename fails, `/current/<MAC>.raw` keeps the **previous** image, the `.pending` file is orphaned
  until the next boot's cleanup, and `taginfo->md5` is **nevertheless** stamped with the new version.
  That poisons `resendCurrentImage()` (wrong image) and `prepareDataAvail()`'s md5 compare, which then
  suppresses every identical render with "new image is the same as current image".
- **[done]** **E1c — a late `XFER_COMPLETE` eats a freshly queued item.** `espXferComplete` carries no
  data version, so the handler picks the queue head by MAC, and the `offeredDataVer` guard is
  overwritten at every check-in. Sequence: tag downloads image A → B is queued and announced → the tag
  checks in again and is offered B, so `offeredDataVer = B` → the late XFC for A arrives → the guard
  does not fire → **B is dequeued** and the md5 stamped with B's version. The same hole opens when
  `recordOffered()` returns "unknown" (after an S3 reboot) or when `attemptsLeft` has counted out.
  **Confirmed by observation this session**: the radio reported `data 0` at the next check-in with
  `xfc` but no block request, and re-uploading a different random image also did nothing — the md5
  poisoning from E1b. Proper fix is to put the finished `dataVer` in the XFC frame (the S3 plumbing
  already exists — `getQueueItem(mac, dataVer)` and `dequeueItem(mac, dataVer)` both take a version and
  are called with 0); the S3-only stopgap is to remember the *previously* offered version per MAC and
  treat an XFC matching it as late. **Fixed on the S3 side by `8b029386` (merge `4c04b38c`), as the
  corrected design two entries below and not as that stopgap, which does not work** — the frame still
  carries no version, so putting the finished `dataVer` in the XFC frame remains the proper fix and is
  still open as a protocol change.
- **[done]** **E2 — `remove()` on the AP's own `.raw` every minute.** Under `HAS_TFT` the AP record
  renders straight to the TFT and that file is never created, so the unconditional `remove()` is dead
  on every call. One-shot it.
- **[done]** **E3 — `setRxBufferSize()` is silently refused** because the port is already open, so the
  reader comes up with the constructor default of **256 bytes — 1.3 ms of buffering at 2 Mbaud** —
  against a task polling every 1 ms below the WiFi tasks. Expect dropped bytes and v2 CRC failures
  under load. The culprit leaving the port open is the TLSR flasher, which re-opens `Serial1` at the
  SWS baud and never closes it (`ota.cpp` and `radioselftest.cpp` both do call `end()`). One-line fix:
  `end()` before `setRxBufferSize()`. **Highest value per line in the whole set.**
- **[no]** **E4 — `tagDB.json.gz`.** A library probe for an optional compressed variant, reached from
  `/backup_db` when a dashboard tab survives a reboot. Nothing to fix in OEPL. Do **not** change
  `CORE_DEBUG_LEVEL` — it would hide genuine Arduino-layer errors, and `platformio.ini` is the one file
  open PR #487 conflicts on.
- **[no]** **E5 — "no template for content 24 / 3 / 25".** Expected: with the global timestamp option
  on, `getTemplate()` is probed for *every* content mode, and modes 3 (CountHours), 24 (external image)
  and 25 (Home Assistant) have no tagtype template by design. No tagtype JSON defines those keys
  anywhere. The message is itself the recent fix for upstream #618, already in master. Adding the keys
  would be ~90 files of churn for no behaviour change.
- **[done]** **E1c — the corrected design.** The stopgap first proposed for this was **wrong and must
  not be used**: it compared the *queue head* against the *previously offered* version, but
  `queueDataAvail()` retires the old item before enqueuing the new one, so the head is by construction
  the new item B and the comparison `B == A` is false in exactly the case it was meant to catch. It
  would have compiled, read well, and done nothing. The correct discriminator is *"did we ever serve a
  block of the item now at the head?"*, and the naive form of that is still unsafe — a tag that
  recognises the md5 completes with **zero** block requests on the `resendCurrentImage()` path, so
  guarding on "no block served" alone would re-announce the same item forever. The design, three pieces
  of state under `queueMutex` beside the existing `announced`/`offeredDataVer` maps:
  1. `lastServedVer[mac]`, set from `br->ver` in `processBlockRequest()` (and in `processBlockServed()`
     / `processPreload()` for v2 radios) — what the tag was really downloading.
  2. `replacedInFlight[mac] = {ver, millis()}`, set in the replacement loop in `queueDataAvail()`
     **only** when the item being erased has `dataVer == lastServedVer[mac]` — i.e. we pulled the rug
     out from under a transfer that was genuinely in progress.
  3. The guard, before the existing `offeredMatches` check: fire only when `replacedInFlight[mac]` is
     set and recent (reuse `RETIRED_ITEM_GRACE_MS`) **and** `lastServedVer[mac] != headVer`. Keep the
     item, `checkQueue()`, clear `replacedInFlight[mac]` so one replacement absorbs exactly one late
     XFC, return.
  Checks out on all three cases: a normal transfer has `lastServedVer == headVer` and does not fire;
  the observed failure fires once and B survives; the cached-md5 resend never set `replacedInFlight`.
  About 40 lines across four functions with a real false-positive surface, so it wants its own review
  and a bench test — which is cheap, because the failure reproduces on demand.
  Implemented as written in `8b029386` (`lastServedVer`, `replacedInFlight` and the guard, in
  `ESP32_AP-Flasher/src/newproto.cpp`). **The bench test is still owed**: the design was reasoned
  through its three cases and not yet run against a tag that gets a second image queued mid-download.
- **[done]** **E6 — stuck in legacy ASCII after an S3 reboot.** Three holes in `bringAPOnline()`:
  round 0 deliberately skips the reset line, but after an S3 reboot a v2 radio is *always* still at
  2 Mbaud, and the legacy parser is a 4-byte sliding-window matcher with no length or CRC validation —
  fed a 2 Mbaud stream sampled at 115200 it will eventually synthesise `ACK>`, `NFO>`, `RES>` from
  garbage; `apInfo.caps` is zeroed and never validated, so a `sendGetInfo()` that "succeeds" without a
  `CAP>` token leaves caps 0 and the code settles on ASCII **permanently**; and the three-round loop
  does not actually retry — two of the three exits `return false` outright. Secondary: `txStart()`
  loops forever with no timeout (its own comment asks whether it should have one), which is the endless
  "wait... tx busy". The radio-side fast fallback (done) is the complement; this is what stops it
  latching.
  Fixed by `80743e93` (merge `dc267448`): round 0 may now skip the reset line only for a radio this
  boot has already talked to (`apInfo.version != 0`), every failing round continues to the next one
  instead of returning, and `txStart()` gives up after 2 s and hands its caller the failure. **One
  thing was decided differently**: the proposal was to validate `apInfo.caps`, which cannot be done —
  radios older than protocol v2 legitimately have none — so the version is the proof that the answer
  came from a radio really listening at this baud, and `bringAPOnline()` now zeroes and re-requires
  it. A bare `ACK>` is exactly what the old parser builds out of noise, and it carries no version.
- **[open]** The other half of E6's "secondary": **the AP recovery trigger still counts only missed
  pings.** `attempts` in `APTask` is incremented by `sendPing()` alone, so a radio that answers
  heartbeats while the data path is broken never reaches `maxMissedPings()` and is never reset.
  `txStart()` now returns a failure for its callers to see, which is the prerequisite, but nothing
  feeds that failure into the counter yet.

**Upstream cross-reference.** Only five PRs are open and **none** touches `serialap.cpp`, `ota.cpp`,
`legacytransport.cpp`, `v2transport.cpp` or `tlsr_flasher.cpp`, so E3 and E6 are conflict-free. #574,
#532, #527 have low-risk overlap in `contentmanager.cpp`/`newproto.cpp`/`web.cpp`; #487 overlaps only
`platformio.ini`; #527 rewrites every `data/www/*.gz`, so leave those alone.

Open issues these fixes plausibly close — **candidates needing a reporter retest, not proven**:

- **#462** "Sometimes double refresh on Solum M3 2.9" — strongest match for E1c: the log shows two
  `reports xfer complete` with no block request between them.
- **#579** "Weather forecast not updating" — strong match for the E1b/E1c side effect: not updating for
  days with check-ins fine, fixed by changing the config, which is exactly md5 poisoning being broken
  by a render change.
- **#533** "Pixels of generated image are sometimes shifted" — weak, possible overlap with E3.
- Context to cite, not conflict: #552 and #567 (closed) are the ancestors of this queue code; #618
  (closed) created E5's message; #628 and #466 (closed) are the origin of the offered-version
  bookkeeping E1c extends.

Searches returning nothing upstream: `vfs_api`, `Has open FD`, `setRxBufferSize`, `RX buffer`,
`2 Mbaud`, `ASCII protocol`, `tagDB.json.gz`, `AP offline after flashing`. **E2, E3, E4 and E6 are not
reported upstream at all.**

---

## F. SRAM layout (the TLSR AP's `.bss`)

SRAM is the binding constraint on a 64 KB part, and the unit of value is a cache block at **4184
bytes**: nothing else in the budget is worth that much, so everything here is priced in fractions of
one. (Once the cache and the allow-list share one arena, the step function goes away and a freed byte
is worth a fraction of a block directly - which makes the bottom line of this section a plain byte
count rather than a comparison against 4184.) `_bss_headroom_` is a real linker symbol - read it with
`tc32-elf-nm`, never estimate it - and the RAM section of
`ARM_Tag_FW/OpenEPaperLink_TLSR_AP/README.md` carries the table of configurations.

The inventory this section works from, largest first, measured on the site build before any of it:
`bufs` 29106 (the cache), `allowedMacs` 8192, `pendingDataArr` 6750, `queue` 2928, `uRing` 2048,
`rxSlots` 1280, `ev_timer` 684, `uTxBuf` 608, `logBuf` 606, `tagTable` 576, then the frame buffers at
261-264 each. Of that, 3192 bytes have come back (3264 with `UART_TX_DMA`) and the pending table is
the only large row left untouched, deliberately.

### Done

| | item | before | after | status |
| --- | --- | --- | --- | --- |
| F1 | **The event queue was 24 cells the size of its largest member.** TAG_RETURN_DATA is 115 bytes; CHECKIN, which is what actually fills the queue, is 49, and the other three events are 16-26. Two size classes - 51-byte cells plus three 115-byte side buffers, with the cell holding an index - keep the 24-event depth and give back 1344 bytes. Which class an event uses is decided by its own length, not by its type: keyed on the type, a later event that outgrew a small cell would take a side buffer and be read back out of the cell. | 2928 | 1569 | **[done]** `93a26f37` |
| F2 | **`popHead` memmoved the whole queue down one cell** - up to 1173 bytes at 9 cycles a byte, once per event the S3 acknowledges, and deepest exactly when the S3 is behind. A sliding window (`qHead`) makes the pop three stores and only moves the array when the window reaches its end, which never happens on a queue that drains to empty. Insertion and the drop policy still see a contiguous run, so no wraparound arithmetic anywhere. | ~10.5 kcycles/pop | 0 | **[done]** `93a26f37` |
| F3 | **The MAC -> hwType/power table was an array of a 9-byte packed struct**, so every key was at a different residue mod 4 and a lookup called memcmp over eight byte loads: 25 instructions an entry, 1600 for a scan of 64, two or three scans per check-in. Columns instead (`uint64_t tagMac[64]` beside the byte arrays) put the key at an 8-aligned address: 8 instructions an entry, 512 a scan. **The bytes are identical** - 64 x 8 + 64 = 576, exactly what the struct cost - so this is not a trade. Same technique as B3 and as `allowedMacs`. | 25 instr/entry | **8** | **[done]** `8f869e37` |
| F4 | **The pending table was scanned to its end whatever the occupancy.** `findFreeSlot` has always handed out the lowest free slot, so live entries cluster at the bottom and everything above the high-water mark is free by construction; `pendHigh`, one byte, is that mark, raised when a slot is taken and tightened by the housekeeping pass that already walks the table. 14 tags now walk 14 entries instead of 250. | 250 entries | occupancy | **[done]** `8f869e37` |
| F5 | **`findSlotForMac` tested the expensive thing first.** One byte of the MAC refuses 255 of every 256 entries in three instructions, where reading the 16-bit `attemptsLeft` out of the packed struct takes four on its own and reaching the memcmp costs sixteen more. The conjunction's operands are side-effect-free, so reordering cannot change the answer. | 15 instr/entry | **7** | **[done]** `8f869e37` |
| F6 | **`% V2_LOG_QUEUE` on the log path.** Six is not a power of two, so GCC emitted `__modsi3` - the hardware divider with interrupts masked for ~50 cycles (see the note on `div_mod.S` in section B) - three times on the path that queues and sends a LOG line. An index known to be below the bound needs a conditional subtraction. | 3 x ~50 cycles | 0 | **[done]** `93a26f37` |
| F7 | **Two DMA buffers were round numbers.** `UTX_CHUNK` was 300 where the largest v2 frame on the wire is 264 (`V2_MAX_FRAME_COBS + 1`), across a double buffer: 72 bytes. `RX_SLOT_SIZE` was 160 where the DMA writes at most `4 + phr + 9` = 142 bytes for the largest PHR the frame check accepts, and 144 is the next multiple of the 16-byte unit `rf_rx_cfg()` takes: 128 bytes over eight slots. Both are now asserted at compile time rather than left to a comment, and the assertions were checked in both directions. | 1888 | 1688 | **[done]** `607d6812` |
| F9 | **The SDK's `ev_timer` is 684 bytes of software-timer pool this firmware never registers a timer with.** It was linked because `drv_platform_init()` calls `drv_pm_wakeupTimeUpdate()`, which calls two of its functions, and because `ev_timer` is a COMMON symbol reached from kept `.text`, `--gc-sections` cannot collect it — unlike `ev_buffer.c`'s 2.9 KB of pools, whose last reference went when `string_min.c` replaced the SDK's `memcpy` and its `is_ev_buf()` call. The file leaves the build and the two entry points become no-ops in `hal_tlsr.c`. The argument is not inspection but absence: `ev_timer_init`, `ev_on_timer`, `ev_timer_add`, `ev_timer_taskPost` and `ev_timer_process` are all missing from the image, so the list cannot acquire an entry, and `drv_pm_sleep` and `cpu_sleep_wakeup` are missing too, so `SYSTEM_DEEP_RETENTION` — the only state that reaches `drv_pm_wakeupTimeUpdate` — is unreachable. Boot sequence unchanged, so no hardware test is owed. | 684 | 0 | **[done]** `cca5eddc`. Two further functions are left **undefined** rather than stubbed, on purpose: their callers were collected and `objdump -r` shows no relocation, and undefined means the linker refuses any future change that revives them instead of letting it link and silently do nothing. |
| F10 | **The UART receive ring was 2048 bytes, 10.2 ms of line time at 2 Mbaud, with nothing written down about what it was 10.2 ms of.** The main loop drains it to empty every pass, so what has to fit is one pass; the longest ordinary one derivable from the code is ~2.1 ms (four unanswered DATA_CHUNKs means four ACKs, an event and a LOG frame — 262 bytes, 1.3 ms — plus ~0.8 ms of COBS, CRC16 and a completed block's 4 KB checksum). 1024 is 5.1 ms, 2.4x that. What decides it is the failure shape: a byte arriving with the ring full is dropped and counted in `halSerialOverflows()`, its frame then fails CRC and is counted again, and protocol v2 retransmits it — bounded, automatic and visible in STATUS within a second, which is the opposite end of the scale from the pending table. | 2048 | 1024 | **[done]** `c36d4efb`. The one case that exceeds 5.1 ms is named in the comment: a TX DMA that stops responding makes `uartTxDmaWait()` wait `2 * uTxMinUs + 5000` us, a one-off that turns DMA off for good and is counted. `URX_RING` is a build option. **This is the only row in F whose margin is an argument from the code rather than a measurement**, so `serialOverflows` is worth a look after the next flash. |
| F11 | **RAM code ended 16 bytes past a 256-byte boundary**, and `.data` starts at the RAM code rounded up to 256, so it cost 240 bytes nothing used. `irq_handler` tested three timer sources one at a time; both registers are write-1-to-clear and the timer bits coincide (asserted), so one masked write to each does the same. 2832 bytes of RAM code to 2812, 4 short of the boundary; `_ramcode_tail_` in the linker script reports it from now on. | 3072 | 2816 | **[done]**, with F12-F14, for the tenth cache block. Holding the UART ring's indices in registers was tried and made it bigger; `-Os` on the handler saves 12 and was not taken, on the hottest path there is |
| F12 | **The received frame was decoded into a second buffer.** COBS decodes in place - the write pointer never passes the read pointer - so `raw` is gone and the frame is decoded over `rxCobs`. This is not the sharing rejected below: that was the decode/encode pair, which depends on no handler reading its payload after sending. This one depends only on `v2RxBytes` not being re-entered, and `raw` was static, so that was already true. | 261 | 0 | **[done]** |
| F13 | **The pending table's checksum byte**, priced below at 250 and left because 27 is the closed-source firmware's stride. That is evidence about the closed binary's lineage (README, "How the closed-source firmware was built") and nothing this firmware or any tool reads; `addCRC()` computes the checksum over the reply and the stored copy was never read. A `struct pendingInfo` holds the AvailDataInfo's other fields in its order, asserted field by field, so the reply is still one copy. | 6750 | 6500 | **[done]** |
| F14 | **A cache region stored the 0xFF tail of a burst's last part**: 4158 bytes (42 × 99) where the block and its header are 4100. `cacheCopyPart()` pads the last part as it goes out, and the region is 4104. Not `.bss` but arena reach: ten blocks are 41040 bytes instead of 41600, which is 560 bytes of band. `cacheBuffer()` left the interface, so nothing can index a region by part. | 4160/block | 4104 | **[done]** |
| F8 | **`MAX_PENDING_MACS` is a documented build option**, beside `CACHE_BLOCKS` and `MAX_ALLOWED_MACS`, with what it costs to be wrong. Default unchanged at 250; the reasoning for that number is the `[no]` item below. | — | — | **[done]** `62fe1395` |

### Measured: what each buffer has actually needed (`hw` line, 2026-11-01)

The production image now reports a high-water mark for every buffer whose size is a choice (see the
`hw` row in BOARD.md). On this 13-tag site, at 48 MHz, through a refresh of every tag and four 4-block
preloads:

| buffer | size | peak | what sets it |
| --- | --- | --- | --- |
| UART receive ring | 1024 | **924** | the S3's DATA_CHUNK window, not pass time. Handling a chunk (1.45-1.65 ms) is slower than its 1.3 ms on the line, so the window's unacknowledged chunks pile up: three frames of 264 is 792, plus the control frames between them. F10's 2.1 ms-pass argument reached the right size for the wrong reason - and with 100 bytes left, **any increase of `V2_CHUNK_WINDOW` needs a bigger ring** |
| longest pass gap | - | **41 ms** | part 2, the S3's requests: `apLoop` drains serial until the ring is empty, and a preload stream keeps it non-empty for the length of the stream. No single request is slow; the loop never comes up for air. **No radio frame is looked at for those 41 ms**, against a 2.4 ms reply window. At 13 tags `rep` stayed 0; at 200 tags (1.7 check-ins a second, measured rate scaled) one block stream in about 15 would land on a check-in |
| radio receive slots | 8 | 1 | check-ins are rare next to a slot's lifetime; even the 41 ms stall caught no second frame here |
| event queue | 24 | 1 | the S3 ACKs within milliseconds; the depth only matters while it stalls (14 s of stall at 200 tags) |
| side buffers | 3 | 0 | tag return data; no button was pressed |
| LOG queue | 6 | 5 | a diagnostics report is four lines and a block request one more; per-tag power makes it six |
| pending table | 250 | 9 | the S3 does not hold an idle entry for every tag at every moment: 9 of 13 at the peak |

**[done] Bound the serial work per pass.** `apLoop` now reads the S3's input only up to the first
frame delimiter (`halSerialReadFrame`), handles that frame and goes back to the radio. Measured on
hardware through a refresh of every tag: the longest pass-to-pass gap went from 41 ms to **6.5 ms**,
and the ring's peak was unchanged (935), as the window model says it must be - so it cost nothing in
buffering. `one_request_from_the_s3_per_loop_pass` pins it. **[open]** One 6.2 ms sample remains in
part 2 that no single request accounts for (the slowest measured is 1.6 ms); the link's bring-up at
boot is ~21 ms and is now excluded from the mark (`loopTimingRestart`).

**[half done] Per-tag state is sized for three different fleets.** The hwType/power table holds 64 tags,
the pending table 250 and the allow-list 1024 (3392 compressed). At 200 tags the 64-entry table
evicts an entry after 64 check-ins - about 38 s - against each tag's ~2 minutes between check-ins,
so a tag's hwType is never there when it asks for a block: early bursts stop for every ZBS243 and
per-tag power stops working, silently. The pending table already holds exactly one entry per tag for
as long as it matters, so the hwType (1 byte) and the power state (2) belong in it; that removes the
576-704-byte table and makes the feature scale with the fleet. And an idle entry - most of the table
on a settled site - carries only a MAC, `nextCheckIn` and a TTL: 14 of its 26 bytes are always zero
(`prepareIdleReq()`). **Done:** the pending table is in the arena now, as its third tenant - an
eleventh block for a site under 120 tags, and 456 tags of service where there were 250 (see "The
pending table moves in" in radio-sram-split.md). **Still open:** the hwType/power table at 64, and
the idle entry's 14 zero bytes.

### Checked and rejected, with reasons

- **[no] Shrinking the pending table.** 6750 bytes is 1.6 cache blocks and the largest thing outside
  the cache and the allow-list, so it is the obvious place to look and has been proposed more than
  once. Three questions settle it, and they are worth keeping answered because each has to be
  re-derived from a different file.

  **How many entries can one tag hold? Exactly one.** `setPending()` calls `findSlotForMac()` before
  `findFreeSlot()`, so a second announcement for a tag reuses its slot; and the S3 announces one
  queue item per tag at a time (`checkQueue()` takes `getQueueItem(targetMac)`, the first match, and
  `setAnnounced()` keeps one record per MAC in `ESP32_AP-Flasher/src/newproto.cpp`). So entries never
  exceed distinct tags.

  **What does the C6/H2 use? The same 250**, at `ARM_Tag_FW/OpenEPaperLink_esp32_C6_AP/main/main.c:69`,
  and so do both Arduino sketches; and the closed-source TLSR binary's 250 x 27 table is one of the
  fingerprints the README identifies its AP-logic version by. A fleet that works on a C6 works here.

  **What can the S3 have outstanding? One per tag, permanently, for every tag in a sleep cycle** -
  and this is the answer that kills the idea. `prepareIdleReq()` sends the "nothing for you, check in
  again in n minutes" instruction as an *ordinary pending entry* with `DATATYPE_NOUPDATE` and
  `attemptsLeft = 10 + config.maxsleep` minutes (20 by default, up to 265). Nothing clears it: the
  radio does not clear an entry it has merely served, and the S3 does not cancel it either - its own
  comment in the check-in handler says why, verbatim, "The radio co-processor only drops its cached
  'data available' on an xfer-complete or an explicit cancel", which is there because a deep-sleep
  command had to be cancelled explicitly for exactly this reason. The tag's next check-in is at most
  `maxsleep` minutes away, which is less than the entry's life, so the entry is refreshed before it
  expires and the slot stays occupied for as long as the tag keeps checking in. **The steady-state
  occupancy of this table is the fleet, continuously** - not "tags seen in the last twenty minutes".

  So 250 entries is a 250-tag cap, and it is already the tightest fleet bound in the firmware, below
  the 1024 the allow-list assumes. 64 entries would cap an AP at 64 tags; on the 200-tag site this
  project's own documentation reasons about, 136 tags would silently never have content announced.
  And there is **no density to recover instead**: of the 27 bytes, 26 are live data (an 8-byte MAC,
  an 8-byte version, a 4-byte size, two 2-byte counters and two type bytes) and the 27th is a
  checksum that `addCRC()` recomputes when the entry is copied into a frame. The table is full, not
  padded, so the only lever is the entry count.

  The failure mode points the same way: a full table makes `SET_PENDING` answer `NAK NO_SLOT`, which
  the S3 reads as "try again at this tag's next check-in" and deliberately does *not* reset the radio
  over (`dc267448`, because it happens exactly when an AP is under load), so an overflow is content
  arriving late with nothing saying why. **One thing is better here than with the allow-list**: it is
  not invisible. `STATUS` carries `pending` and `pendingIdle` every second and `HELLO_REPLY` carries
  `pendingSlots`, so a site can watch its own margin - which is what makes F8's build option a knob
  rather than a trap, and makes `MAX_PENDING_MACS` the first of the three knobs to consider on a site
  that has actually looked at the counter.
- **[no] Padding or reordering `struct pendingData`.** Padding costs bytes for alignment the scans
  no longer need (F4, F5). The stride argument this entry used to rest on - that 27 matches the
  closed-source firmware - turned out to be provenance only, which is why F13 could take the checksum
  byte; the stride is 26 now.
- **[no] A parallel aligned MAC index for the pending table**, which is the only way to get F3's
  two-word compare there, since `targetMac` sits at offset 19 of a 27-byte stride. `uint64_t
  pendMac[250]` is **2000 bytes** - half a cache block - to save about 4 instructions an entry on a
  scan that F4 and F5 already took from 3750 instructions to 98 at this installation's occupancy.
  Wrong way round.
- **[no] A cheap pre-check in `findSlotForVer`.** F5's trick measured as worth nothing here, and the
  reason is worth recording: `dataVer` is a 64-bit field at offset 1 of a packed struct, so reading
  it is 22 instructions, and GCC recognises a `(uint8_t)dataVer == lo` pre-check as part of the value
  it is about to load and hoists the whole load above it - the cheap test then runs *after* the
  expensive one. `findSlotForMac` wins because its full compare is behind a call, which GCC will not
  speculate. Memoizing `verStillOffered()` (it is called once per DATA_CHUNK, ~18 times per block)
  would work, but it needs invalidating at six places where `attemptsLeft` changes, and a stale
  answer there silently mis-prioritises cache eviction. Not worth it while `pendHigh` bounds the
  scan: at 14 tags it is 476 instructions a chunk, and at a full table 3.2 ms spread over the ~140 ms
  a block takes to arrive.
- **[no] Shrinking `uRing`, the 2048-byte UART receive ring.** 2048 bytes is 10.2 ms of line time at
  2 Mbaud. The main loop drains it to empty every pass, so what has to fit is one pass, and the long
  part of a pass is that `halSerialWrite()` blocks until the DMA in flight is done: a reply, an event
  and a LOG frame in the same pass is about 4 ms of line time, i.e. ~800 bytes arriving. 1024 would
  leave about 200 bytes of margin on an estimate, which is not enough to spend a measurement's worth
  of confidence on. **What would settle it**: `serialOverflows` in `STATUS` already counts the loss,
  so a build with `URX_RING` at 1024 on a real transfer answers it directly. Until then it stays.
- **[no] Aligning the log buffer's cells.** `logBuf[6][101]` has a 101-byte stride, so the ~100-byte
  `memcpy` into it never takes the word path. Rounding the cell to 104 costs 18 bytes to save ~670
  cycles on an event that happens at most once every `V2_LOG_GAP_MS`. `V2_LOG_MAX` itself is coupled
  to `V2_LOG_LINE` in `ap.c`, which is the diagnostics' line length, so it is not a RAM knob.
- **[no] Aligning the event queue's cells.** Making the 51-byte cell 56 and putting the payload at
  offset 4 would let the ~49-byte `memcpy` into it run word at a time, saving ~330 cycles per
  check-in queued, for 120 bytes across the 24 cells. The copy *out* cannot be helped either way,
  because `sendFrame` assembles into `rawBuf + 3`. Priced against a cache block, no.
- **[no] Sharing the four 261-264 byte frame buffers.** `rxCobs` (incoming, 263), `raw` (decoded,
  261), `rawBuf` (outgoing, 261) and `wireBuf` (encoded, 264) have lifetimes that *appear* to allow
  overlapping two of them: `rxCobs` is dead once a frame is decoded, and the decode destination is
  dead once `v2HandleRequest` returns. Both arguments depend on argument evaluation order and on no
  future handler reading its payload after sending anything, which is exactly the kind of invariant
  that holds until someone edits a handler. 261 bytes is not worth a trap of that shape.

### Open, with the measurement attached

- **[open] 80 bytes: `hwTmr_vars`, the other half of the SDK scaffolding.** F9 took `ev_timer`'s 684
  because the reference that kept it alive is in a branch this firmware cannot enter. `hwTmr_vars` is
  different: `ZB_TIMER_INIT()` in `drv_platform_init()` calls `drv_hwTmr_init()` on **every** boot,
  and that writes hardware timer registers as well as the struct. Nothing in this firmware ever calls
  `drv_hwTmr_set()`, so the state is as dead as `ev_timer`'s was, but removing the call changes the
  register state at boot. 80 bytes for a boot-path change that only hardware can confirm is the wrong
  way round; it is recorded so nobody re-derives the 764 as if it were all available.
- **[open] 256 bytes of interrupt stack**, and the rest of the priced list at the end of this
  section. `irq_stk` is 512 and the README records the deepest reachable interrupt path as **116
  bytes** of it, read from the disassembly, so 256 would still be 2.2x a measured bound and it is one
  assembler flag (`__IRQ_STK_SIZE__`). It was not taken because at the time it would only have served
  to cross a cache-block threshold, and turning a safety margin into a function of a rounding
  boundary is the same mistake the README's "4184 bytes spent on a four-byte problem" paragraph is
  about. With the arena making bytes fungible that objection weakens, so this is the first of the
  priced items to revisit - but on its own merits, with the stack high-water measurement the README
  describes (`cstartup_8258.S` fills the stack with `0xFFFFFFFF`, so scanning it after a run gives
  the real figure) rather than on the bound alone.

### Where the budget stands after F1-F10

Every figure re-measured on both sides, `make clean` between every build, against the base this
branch started from.

| flags | `.bss` before | after | freed | `_bss_headroom_` before | after |
| --- | --- | --- | --- | --- | --- |
| release | 55104 | 51912 | **3192** | 2688 | **5880** |
| release, `TAG_TX_POWER_MAX_DB=9` | 55248 | 52056 | 3192 | 2544 | 5736 |
| site (the `BOARD.md` recipe) | 55740 | 52476 | **3264** | 2052 | **5316** |
| site, `TAG_TX_POWER_MAX_DB=9` | 55884 | 52620 | 3264 | 1908 | 5172 |
| site + `AP_DEBUG` + both sweeps, P9 | 55948 | 52692 | 3256 | 1844 | 5100 |

**3192 bytes freed, 3264 in a build with `UART_TX_DMA`** (F7's UART half only exists there). Where
they came from: the event queue 1359 (F1), the SDK timer pool 688 (F9), the UART receive ring 1024
(F10), the radio receive slots 128 (F7), the UART transmit buffers 72 (F7, DMA builds only). F3's
column split and F4/F5's bounded scans cost and saved nothing in bytes - they are the instruction
counts in the same table. The pending table is unchanged, for the reasons above.

**What that bought, in the terms that matter before the arena lands.** An eighth cache block did not
link in *any* flag set before this; it now links in all of them with 988 to 1704 bytes still free,
with the full 1024-entry allow-list, so the trade the README's RAM section was written to refuse - a
block against half the inventory lock - is no longer on offer. A ninth is still about 3 KB out of
reach. Once the cache and the allow-list share one arena, every byte here is a fraction of a block
rather than a step function, and the figure to carry forward is simply **3192** (3264 with DMA).

**How this lands on the arena's curve, and the band it should be placed in.** Once the cache and the
allow-list share one arena, the block count is `arena / 4160` and the remainder is what the
allow-list gets, so a freed byte is only worth something if it does not land the arena *on* a
multiple of 4160 - at a multiple the top block count holds only while the list is empty and the first
tag to check in takes the block back. Starting from `7 * 4160 + 8 * 1024` = 37312:

| freed | arena | blocks | leftover for the list | tags at 4 bytes each |
| --- | --- | --- | --- | --- |
| 0 | 37312 | 8 | 4032 | ~1008 |
| **3192** (this branch) | 40504 | **9** | **3064** | **~766** |
| 3264 (with `UART_TX_DMA`) | 40576 | 9 | 3136 | ~784 |
| 4288 | 41600 | 10 | 0 | 0 |
| 4857 (every priced item below, as well) | 42169 | 10 | 569 | ~142 |
| 6400 | 43712 | 10 | 2112 | ~528 |

So **3192 sits in the middle of a useful band**: nine blocks that hold to roughly 750 tags, which is
what "a few hundred tags on nine blocks" asked for. Two things follow, and they are the reason this
table is here rather than in a message.

**Taking the priced items below would make things worse, not better.** All six of them together are
1665 bytes, which lands at 4857 - ten blocks with 569 bytes of leftover, a band that holds about 142
tags and collapses to nine above it. That spends six real margins to buy a block no site keeps. The
items stay unspent until something large enough to clear 6400 appears, and if nothing does, nine
blocks at ~766 tags is the right place to stop.

**And the tag bands in that table are already capped at 250 by the pending table, in every row.** The
allow-list decides which tags are *answered*; the pending table decides how many can have something
*pending*, and because the occupancy is the fleet (see the rejection above), an AP with 1008 tags on
its allow-list can only hold entries for 250 of them. The rest are answered with `DATATYPE_NOUPDATE`
and `nextCheckIn` 0 - alive, visible to the S3, and never given either content or a sleep schedule,
while the S3 retries `SET_PENDING` at every check-in and is refused. So sizing the arena for a
512- or 1024-tag band buys inventory-lock coverage rather than service, unless `MAX_PENDING_MACS`
rises with it: 512 entries is 13824 bytes, i.e. **7074 more** than today, which is the opposite
direction from freeing bytes and is a decision about what size of site this firmware claims to serve.
Worth settling before the top band is advertised as a tag count.

**What is left, priced, so the next kilobyte starts from a list rather than from the inventory.**
Nothing below is waste; each costs a margin or a capability, which is why none of it was taken:

| | bytes | what it costs |
| --- | --- | --- |
| `irq_stk` 512 -> 256 | 256 | a measured margin: the deepest reachable interrupt path is 116 bytes, so 256 is still 2.2x it. One assembler flag (`__IRQ_STK_SIZE__`). Deliberately not taken to cross a threshold; worth doing on its own merits, with the stack high-water measurement the README describes |
| `V2_EVQ_LEN` 24 -> 16 | 408 | check-in and block-served statistics dropped sooner while the S3 is stalled. The depth is the whole point of the queue, and F1 already took the waste out of it |
| `V2_LOG_QUEUE` 6 -> 4 | 202 | housekeeping's warnings lost when they land in the same pass as a diagnostics report, which is exactly when they matter |
| `rxSlots` 8 -> 6 slots | 288 | radio frames dropped when the main loop falls behind, counted in `rxDropped` |
| sharing the decode and encode frame buffers | 261 | an aliasing invariant that holds only while no handler reads its payload after sending anything. Not worth a trap of that shape for a quarter of a block. (The received and decoded buffers were shared instead, F12, which needs no such invariant) |

---

## The burst length must adapt to the installation, not to this one

**[done]** — `b7e818db`, with `f3695e4b` on the channel-occupancy input. The design below is what was
built, with two departures recorded at the end of the section.

The one choice in this firmware that rests on reasoning rather than measurement, and the reasoning is
biased by the bench it was made on: 14 tags, quiet channel, one AP. Real installations run **200 or
more tags with several APs within range**, and there the arithmetic turns around:

- A 52-frame burst is ~257 ms of the AP transmitting, during which it is deaf and every other tag's
  2–6 ms window expires. At 200 tags checking in once a minute — about 3.3 check-ins a second — such a
  burst collides with roughly one check-in every time it runs. Each collision costs that tag a retry,
  and after fourteen it gives up and sleeps.
- Neighbouring APs on the same channel raise the per-frame loss, which makes the burst *want* more
  repeats, which makes it longer, which steps on more windows. That is a feedback loop, and a fixed
  budget tuned on a quiet bench sits on the wrong side of it.
- Equally, forcing everyone down to the C6's 42-frame cap penalises the small quiet install where
  airtime is free and a re-request round costs a tag 300 ms of radio.

So neither constant is right, and the radio already has the signals to decide for itself:

| signal | what it says | already counted as |
| --- | --- | --- |
| requests dropped as too old to answer | **the direct measure of the harm**: windows of other tags that passed while we transmitted | `late` |
| re-requests after a burst, and how many parts | whether the repeat frames are buying anything at all | `rereq`, `rparts` |
| bursts cut short because the tag was already done | repeats that were pure waste | `cut` |
| sampled channel noise floor | how busy the channel is | `nf`, `min` |
| allow-list size and pending occupancy | how many tags share this airtime | the allow-list, `pending` |

**Design.** One copy of each requested part is not discretionary — the tag asked for it. Everything
beyond that is, so that is the control variable: an *extra-frame budget* on top of one full pass.

- `late > 0` in a reporting window → halve the extra budget. We are measurably costing other tags
  their windows, and that is worse than a re-request.
- `late == 0` and a re-request arrived → raise the budget. Loss is real here and the repeats are
  earning their airtime.
- `late == 0` and no re-requests → leave it alone. Do not grow a budget nothing is asking for.
- Floor at 0 extra frames (one copy each, which always has to go out) and ceiling at the tag's
  listening window, as now.

On a quiet 14-tag bench `late` stays 0, nothing re-requests, and the budget sits where it is — the
behaviour measured here. On a 200-tag site with neighbours, `late` climbs and the AP stops hogging the
channel without anyone configuring anything. It is the same shape as the adaptive host wait above, and
for the same reason: a constant is a guess about someone else's installation.

**Honest limit:** on this bench `late` is 0 and may never move, so the loop can only be validated by
the host tests plus reasoning. It must therefore be written so that *no signal* means *no change* —
never so that silence is taken as permission to grow.

**What was built, and the two departures.** `burstCopiesAdapt()` in `src/ap.c` keeps the asymmetry and
the no-signal rule exactly as above, and `test_ap.py` covers it as
`burst_repeats_follow_what_the_fleet_can_afford`. Two things came out differently:

- **The control variable is copies per part, not an extra-frame budget.** `burstCopies` moves between
  1 and `BURST_COPIES_MAX` (3) from a default of 2, and the frame count is then the smaller of
  `parts × burstCopies` and what the tag's remaining listening window holds. That keeps the floor the
  design asked for — one copy of every requested part always goes out — while leaving the window, not
  a separate budget, as the ceiling, so there is one place where burst length is decided rather than
  two that can disagree.
- **A fourth signal gates growth: channel occupancy.** Raising the repeats also requires
  `busy < BURST_BUSY_HOLD_PCT` (25 %), because on a busy channel the losses a re-request reports are
  as likely to be somebody else transmitting, and answering those with more frames of our own makes it
  worse for both. `f3695e4b` had to make that reading absolute — the share of idle samples above
  −80 dBm — rather than relative to what the channel had shown before: taking the lowest reading ever
  seen as "quiet" pinned it at the receiver's own −109 dBm floor and so called an idle bench 67–77 %
  busy, and learning the reference instead fails the other way, because any median or low percentile
  is pulled up by exactly the traffic it is meant to measure. The median is still reported, and
  nothing is decided from it. `cut` and the allow-list occupancy are counted but are **not** inputs to
  the loop.

## Plan: what to do, and when

**Landed on this branch.** **All of section A** — the per-family listening model, the M3 hwTypes that
had been mis-keyed as M2, the re-ack that now holds its frames for a sleeping tag, the stale-request
limits (with the PING limit since deliberately reversed to the *widest* window, which is the entry to
read before touching the other two); the burst work (ack-anchored start, window budget, odd repeat
spacing, stop-when-done, mid-burst re-ack) and the adaptive repeat budget on top of it; C's
frequency-offset decode, TX settle, PA readback, `bdrop` counter and the crystal-cap knob and sweep;
**every numbered finding in section B** — B1 through B9, B7 with the LQI table folded in; D's logging
out of the radio path, the S3 asking for LOG frames, channel 27, the counters and the restored ones,
the measured download wait, the reset reason, the cache's eviction order and its size decision, the
tag-return identity, the three (four) C6 frame-length bugs and the protocol document's drift;
E1a/E1b, E1c, E2, E3, E6 and the two cosmetics. Per-tag transmit power is in as well, off by default,
and the SRAM budget that paid for it is in the README's RAM section (removed in `ed9d0142`,
unshipped).

**Still open, in one place — and it is a short list now.** The `fo` field's unit and sign, which need
a bench signal rather than more reading, and the crystal trim that waits on them; the `emi.o`
self-test, low value; D's note that the C6/H2's frame-length fixes have not been sent upstream; E's
recovery trigger that still counts only missed pings; and the bench test still owed for E1c's fix.
Plus the cold tag, which is not in a section because it is above all of them.

Everything else here is either done or marked `[no]` with its reason — including three items that
were `[open]` with their register details worked out and are now closed on the hardware's own limits
rather than for lack of time (`rf_start_stx2rx`, DMA FIFO mode, the hardware RNG). **Read a `[no]`
before reopening it:** several of them record a premise that turned out to be false, which is the
expensive part and the part that would otherwise be rediscovered.

**Next, in order, and why that order:**

0. **The cold tag that does not reliably join this AP**, which arrived after this list was written and
   outranks all of it: see "the one that matters" in
   [HANDOVER.md](../../ARM_Tag_FW/OpenEPaperLink_TLSR_AP/HANDOVER.md). The four-way experiment that
   used to head this entry **has been run**: we answer pings — 80 PONGs in one scan, `unh` and `late`
   at 0 — so the ping path, the channel, the frame and its addressing are all cleared, as are power
   and the carrier. The closed-source image has since been disassembled against ours
   ([radio-stock-binary-diff.md](radio-stock-binary-diff.md)), which killed the strongest remaining
   candidate: `rf_drv_init()`'s 33 register writes are disjoint from every register a TX or RX path
   touches, so stock's per-frame re-init restores nothing that transmitting disturbs and our
   once-at-boot init is correct.

   **Three fixed PONG offsets have now been tried and all three failed** against a tag three feet
   away: 970, then 700, then 700 again once it turned out `f27ae53e` had edited a `#ifndef` the
   makefile was overriding so no image had ever carried it. In every one, `PING_LOG` showed the frame
   was right in every field — channel 26, 25-byte PSDU, the destination read correctly out of the
   broadcast header, `larm 0`, the tag at −43 dBm. **A frame correct in every field and still not
   heard says the fault is not in the field we compute**, and the suspect is our own clock: every
   offset is measured from `lastRxEndTick` and the frame then leaves after a settle read once from
   `0xf04`, so if either is off by a few hundred microseconds an offset we call 700 is not 700 on air.
   `RADIO_PONG_SPREAD` (on by default) therefore walks the offset over 150–1800 µs in twelve steps —
   deliberately wider than the window, because bounding it to the window would reason in the very
   clock under suspicion — which turns a scan's twenty attempts into twenty independent trials
   instead of one guess made twenty times. **Next step: flash that build, power-cycle a tag, and read
   the offset `PING_LOG` reports as `at` for the attempt that works** (done - a tag joined at
   1350 µs, HANDOVER fact 17 - and the spread removed in `ed9d0142`) — that measures what the
   datasheet and the stock disassembly both failed to settle. If it still does not join, the fallback
   is the tag's own 3-deep receive ring filling with other tags' burst frames, tested by a
   power-cycle in a quiet window against one during a 42-part burst.
1. **Measure before adding anything.** `dl`/`dlx`/`dlate` say what this S3 really takes for a block a
   tag is waiting for, and the download wait moves itself from that — so the question is no longer
   whether 140 ms is right but whether the wait has risen, and how far. `bdrop` says whether anything
   in the RX path is being lost at all: C's ping-pong and DMA-FIFO questions are closed on the
   hardware's own limits now, and section C works out that `bdrop` can only move if interrupts are
   held off for ~200 µs, so a non-zero reading points at that rather than at the buffering. `cevu`
   says whether the cache is too small for this site, and `rereq`/`rparts` per `bst`, with `cpy`,
   whether the repeat budget settled anywhere sensible. Also `halSerialOverflows()` before and after
   B1, which is still the hardware measurement B1's own numbers are waiting for.
2. **C's crystal trim — but test the AFC hypothesis before sweeping anything again.** (The cap
   option and its sweep were removed in `ed9d0142`.) The appeal is
   unchanged (it is a transmit-direction fix: the trim moves our carrier, and a cheap tag's narrow
   receiver is what misses our replies), but another cap sweep is not the next step. The offset the
   sweep is scored on is the offset "after demodulation", and if that is the residual left by the
   receiver's own carrier recovery then it cannot see our carrier at all and no sweep will ever
   resolve a direction — which is exactly what the last one measured. Settle that first, against a
   signal generator or a deliberately far-mistuned second AP; if it holds, this item closes rather
   than succeeds.
3. **Send the C6/H2 frame-length fixes upstream** (`6a30f861`): a small PR, already written, and the
   only item here whose value is entirely to other people.
4. **Finish E1c and E6 off the bench.** E1c's design is in but untested against a real tag that has a
   second image queued mid-download, which reproduces on demand; E6 left the recovery trigger still
   counting only missed pings.
5. **Only if step 1 says the margins are tight:** every numbered finding in section B has landed, so
   what is left is the rejected list — `-O3`, `-funroll-loops` scoped to one file, a hand-written
   naked UART ISR in TC32 assembly. Each was measured and rejected on its own terms; re-measure
   before reopening any of them, and read the reason first.

**Not scheduled, deliberately:** the EMI self-test (behind a debug command, low value) and
everything under **[no]** above — which now includes `rf_start_stx2rx`, DMA FIFO mode for RX and the
hardware RNG. All three were **[open]** and all three are closed on their own evidence rather than for
lack of time: the first because the auto-mode sequence leaves receive after one frame, the second
because the depth selector that would give it more than two buffers does not exist on this chip, the
third because nothing in this firmware wants randomness. The reasons are in section C.
