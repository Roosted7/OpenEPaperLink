# TLSR AP: the stock binary against ours, instruction by instruction

Written to answer one question: a tag doing a cold scan joins the closed-source stock image at once
and struggles against this firmware. Every candidate visible in C source had been eliminated
(`ARM_Tag_FW/OpenEPaperLink_TLSR_AP/HANDOVER.md`, "The one that matters"), so this is the binary
comparison that was asked for.

**It found the cause, and it is not in the radio registers. The reply delay the last commit was
written to fix never reached a build: the makefile still forces the old value and `-D` beats the
`#ifndef` the commit changed.** Every PONG this firmware has ever sent, including the image flashed
after that commit, left 970 µs after the request and finished 38 µs inside the tag's 2 ms window.

The register-level hypothesis the brief ranked highest - that stock's per-frame `rf_drv_init()`
resets something ours leaves latched - is **eliminated**, at instruction level, below.

## What was compared

| | |
| --- | --- |
| Stock | `binaries/TLSR/OEPL_TLSR_AP.bin`, reports `0x007C` |
| Owner's copy | `/tmp/claude-1000/stock-fw/stock_TLSR_AP_007C.bin` - **byte-identical** (`md5 423be47a94435381d1e06ce1b6e8a01c`), so the image observed pairing cold tags is the one the earlier analysis used; no divergence to worry about |
| Ours | `make clean && make SYS_CLOCK_HZ=48000000 RADIO_APPLY_TX_POWER=1 RADIO_REPLY_STX=1 UART_TX_DMA=1 EARLY_BURST=1 CACHE_BLOCKS=6`, `out/OEPL_TLSR_AP.elf` (33480 text) |
| Tools | `tools/fingerprint.py` (104 functions named in stock), `tools/listing.py`, `tools/tc32dis.py`; our side read with `tc32-elf-objdump` on the ELF, which has symbols |

Stock addresses below are file offsets in the image. Our addresses are ELF `.text` addresses.

## 1. The cause: the PONG goes out at 970 µs, not the 700 µs that was committed

`f27ae53e` ("send the PONG when it was designed to go") changed exactly one line:

```
-#define RADIO_REPLY_PONG_US 970
+#define RADIO_REPLY_PONG_US 700
```

in `src/ap.c`, inside `#ifndef RADIO_REPLY_PONG_US`. But `makefile:106` still reads
`RADIO_REPLY_PONG_US ?= 970` and `makefile:152` passes `-DRADIO_REPLY_PONG_US=$(RADIO_REPLY_PONG_US)`
on every compile. A `-D` on the command line defines the macro, so the `#ifndef` block never expands:
**the edited line is dead code.** The flags the build really uses:

```
-DRADIO_REPLY_STX=1  -DRADIO_REPLY_AT_US=1220  -DRADIO_REPLY_PONG_US=970
```

And in the image, in the inlined `sendPong()` inside `apLoop`:

```
2b72: tmovs r2, #238          ; 0xEE = PKT_PONG
2b74: tmovs r3, #1            ; payloadLen = 1
2b76: tjl   bc8 <prepareNormalFrame>
2b7a: tloadr r3, (2cd0)       ; &curChannel
2b7c: tloadrb r3, [r3, #0]
2b7e: tstorerb r3, [r0, #0]   ; payload[0] = curChannel
2b80: tloadr r0, (2cd4)       ; radiotxbuffer
2b82: tloadr r1, (2cd8)       ; = 0x000003ca = 970   <-- not 700
2b84: tjl   4f80 <halRadioTxReply>
```

The literal at `0x2cd8` is `0x3ca` = 970. There is no 700 anywhere on this path.

### Why 970 is fatal and 700 is not

Both firmwares build a 25-byte PSDU for the PONG. Stock, at `f_2e84`:

```
002e8c: tmov r3, #238 ; tstorerb r3, [r4, #22]   ; payload[0] = PKT_PONG
002e96: tmov r3, #25  ; tstorerb r3, [r4, #0]    ; radiotxbuffer[0] = 25
```

Ours: `radiotxbuffer[0] = sizeof(struct MacFrameNormal) + 1 + payloadLen + RAW_PKT_PADDING`
= 21 + 1 + 1 + 2 = 25. `radioBuildTx()` then sets `txBuf[4] = 25`, so airtime is
`(25 + 6) * 32 = 992 µs`. **The two PONGs are the same length on air.**

`PING_REPLY_WINDOW` is 2000 µs and runs from when `sendPing()` returns, i.e. from the end of the
tag's ping; our delay is measured from the same instant (`lastRxEndTick`). So:

| reply delay | preamble | frame ends | slack in the 2 ms window |
| --- | --- | --- | --- |
| 970 (what ships) | +970 | +1962 | **38 µs** |
| 700 (what was committed) | +700 | +1692 | 308 µs |

38 µs is less than one byte of airtime. Anything that moves the preamble - the `txSettleUs` read from
`REG_ADDR16(0xf04)` being 150 where the code's own history assumed 120, a timestamp that falls back to
IRQ time (`tsfb`), a UART interrupt between the dequeue and the arming - puts the tail of the PONG
outside the window, and the tag discards a frame it received perfectly.

### Why this survived the "we do answer 80 PONGs" measurement

`png` counting 20-80 answered pings per window is consistent with this: the frame is built, armed and
transmitted, and all the AP's own counters are clean (`unh 0`, `bdrop 0`, `txto 0`, `larm 0`). The loss
is at the *tag*, 38 µs after our transmitter has finished and after every counter we own has been
incremented. No AP-side diagnostic can see it.

The commit's own counter-argument - that a thin margin costs one attempt, not a scan, because
`commsRxUnencrypted()` dequeues from a 3-deep ring that `detectAP()` flushes only before the loop -
is correct in isolation but does not rescue 970 µs, because the margin is not thin, it is *negative
under jitter*. A PONG whose tail lands after the window closes is still in the ring for attempt
*c+1*; but attempt *c+1* sends a fresh ping first, and a ZBS243's `detectAP()` compares the queued
frame's channel byte and type, not its age - so what the next attempt reads is a PONG that is one
attempt stale while a fresh one is again 38 µs late. The tag makes progress only on the attempts where
jitter happens to fall the right way.

### The fix, applied

```
-RADIO_REPLY_PONG_US ?= 970
+RADIO_REPLY_PONG_US ?= 700
```

in `makefile:106` (and the option table at the top of the makefile, which also said 970). The
`#ifndef` default in `src/ap.c` is already 700 and stays, but it is not what builds use.

Verified in the rebuilt image - the literal pool word `0x3ca` is gone and the call site now computes
700 inline:

```
2b82: tmovs  r1, #175
2b84: tshftls r1, r1, #2      ; 175 << 2 = 700
2b86: tjl    4f80 <halRadioTxReply>
```

`make -C test` is 33/33. **Any makefile default that shadows a source `#ifndef` is worth auditing the
same way** - `RADIO_REPLY_AT_US` has the identical shape (`makefile:105` = 1220, `src/ap.c:231` =
1220; they agree today, so nothing is hidden, but the same trap is set).

## 2. What stock actually does in its reply path, for any frame

This is the brief's priority 1, and it matters beyond the PONG because the same wrapper carries every
stock reply.

`radioTx` is `f_35e8`:

```
0035e8: tpush {r4, lr}
0035ea: tadd  r4, r0, #0
0035ec: tmov  r0, #250
0035ee: tshftl r0, r0, #1      ; 250 << 1 = 500
0035f0: tjl   0x9fc  <sleep_us>    ; sleep_us(500)
0035f4: tadd  r0, r4, #0
0035f6: tjl   0x3590 <radioTxLL>
```

`radioTxLL` (`0x3590`) re-initialises the radio before every frame and returns it to RX after:

```
003594: tloadr r3, (0x35b4) = 0x00841420   ; our_ch
003598: tjl   0x33ac <init_zigbee>
0035a6: tjl   0x32f8 <zigbee_tx>
0035aa: tjl   0x3430 <zigbee_rx_start>
```

`init_zigbee` (`f_33ac`, `0x33ac`) - and this is the whole of it:

```
0033b8: *0x8416f2 = *0x8416f0         ; current_buff_worked = current_buff: FLUSH the RX queue
0033ba: rf_set_power_level_index(63)  ; > 59, bounds-checked away at 0x49da: a no-op
0033c2: rf_drv_init(8)                ; RF_MODE_ZIGBEE_250K
0033c6: flash_read_page(0x77000, 1, &cap); if (cap != 0xFF) analog_write(0x8a, (analog_read(0x8a) & ~0x3F) | (cap & 0x3F))
003402: drv_hwTmr_init(3, 0)
003406: *0x800640 |= 0x2000           ; irq_set_mask(FLD_IRQ_ZB_RT_EN)
003416: rf_setChannel(channel)
```

`zigbee_tx` (`0x32f8`) then waits once more before handing the frame over:

```
003326: tmov r0, #120
003328: tjl  0x9fc <sleep_us>     ; WaitUs(ZB_TX_WAIT_US) = 120 µs
00332c: *0x800f20 = 1             ; ZB_RADIO_RX_DONE_CLR
003334: tx_done = 0
003338: tjl  0x9d8 <rf_tx_pkt>
00333c: while (!tx_done) ;        ; busy-wait, blocking
```

So **stock's delay from the main loop deciding to reply to the preamble leaving is
500 + init_zigbee + 120 µs.** Costing `init_zigbee` at 24 MHz: `LoadTblCmdSet` applies 33 table
entries at ~25 instructions each (the loop at `0x4d06`) ≈ 55 µs; the 1-byte flash read ≈ 15-25 µs;
`analog_read`/`analog_write` ≈ 8 µs; `drv_hwTmr_init` and the mask ≈ 3 µs; `rf_setChannel` ≈ 7 µs.
That is **70-110 µs**, so:

> **stock's reply preamble leaves ≈ 720 µs after the main loop picks the frame up, plus the loop's own
> latency from the end of the received frame.**

Stock has no RX timestamp and no scheduling: the 720 µs is a software constant and the loop latency
rides on top of it, so stock's real end-of-request→preamble figure is 720 µs plus tens of µs of
jitter, with ~290 µs of slack before the 2 ms window closes.

**This is the number the designed 700 µs was matching, and it is right.** Our 700 µs is stock's
timing with the loop jitter removed by the hardware tick - strictly better than stock. Our 970 µs is
250 µs *worse* than stock, and it spends exactly the margin that scheduling bought.

Stock applies the same wrapper to every reply, with no per-type delay: `f_35e8` has seven call sites
(`0x27d0`, `0x2898`, `0x296c`, `0x2b18`, `0x2bf8`, `0x2d5c`, `0x2ed4`), covering the PONG, the
check-in reply, the block-request ack and the rest. **So stock answers check-ins at ≈ 720 µs too,
where we answer them at `RADIO_REPLY_AT_US` = 1220 µs** - 500 µs later than the image that works.
See candidate 2 in the ranking.

### Stock's ping handler validates nothing

The dispatcher is a jump table. At `0x308c` it calls `getPacketType()`, then:

```
003090: tadd r3, r0, #0
003092: tadd r3, #31          ; type - 0xE1
003098: tcmp r3, #12
00309a: tjhi 0x30b6           ; out of range: drop
00309c: tshftl r3, r3, #2
00309e: tloadr r7, (0x3194) = 0x00004e78    ; jump table
0030a0: tloadr r3, [r7, r3]
0030a2: tmov pc, r3
```

Table at `0x4e78`, 13 entries; index 12 (`0xED`, PKT_PING) → `0x30de` → `f_2e84`. `f_2e84` is 44
instructions and has **no length check, no channel check, no MAC filter and no staleness check**: it
fills `radiotxbuffer`, copies the ping's `src` into `dst` and the ping's `srcPan` into `pan`, sets
FCS bytes `0x41 0xCC` (frameType 1, panIdCompressed 1, destAddrType 3, srcAddrType 3 - exactly what
`pktIsUnicast()` demands) and calls `radioTx`. Field for field it is our `sendPong()`.

Note what stock does *not* have: a `tooLateToAnswer()`. Ours drops a ping older than 2500 µs. `late`
is 0 in every window, so this is not firing, but it is a behavioural difference worth recording.

## 3. Eliminated: the per-frame `rf_drv_init()` resets nothing that TX or RX disturbs

The brief ranked this the strongest candidate - "a register that drifts or latches after our first TX".
It can be settled exhaustively, because `rf_drv_init` is 20 instructions and two static tables.

`rf_drv_init` (`0x48a4`, identical code in both images - same `libdrivers_8258.a`):

```
0048ae: LoadTblCmdSet(0x5010, 5)
0048b6: LoadTblCmdSet(0x4f90, 28)
0048c6: *0x800c20 |= 0x0C        ; FLD_DMA_CHN_RF_RX | FLD_DMA_CHN_RF_TX
0048ca: *(u16)0x84ab4c = mode
```

`LoadTblCmdSet` (`0x4cd8`) entries are 4 bytes `{addr_lo, addr_hi, value, type}`; bit 6 of `type`
gates the entry, `type & 0x3f` selects 3 = `write_reg8(0x800000|addr, value)`, 8 = `analog_write`,
7 = `sleep_us`. All 33 entries here are type 3. The complete set of registers `rf_drv_init` writes:

```
0x1220 0x1221 0x1222 0x1223 0x122a   0x1254..0x1259
0x1276 0x1277 0x127b 0x12d2 0x12d3
0x0400 0x0402 0x0404 0x0405 0x0408..0x040b 0x0420 0x0421 0x0422 0x0460..0x0465
```

The complete set of registers either firmware's TX or RX path writes:

```
rf_trx_state_set   0xf02, 0x428, 0xf16, 0xf00  (+ rf_set_channel: 0x1244, 0x1245, 0x1228)
rf_tx_pkt          0xc43, 0xc0c, 0xc24
rf_start_stx       0xf18, 0xf16, 0xf00, 0xc0c
rf_set_tx_rx_off   0xf16, 0x428, 0xf02
rf_set_rxmode      0x428, 0xf02
rf_rx_buffer_set   0xc08, 0xc0a, 0xc0b      rf_rx_buffer_reconfig  0xc08
rf_rx_cfg          0xc0a, 0xc0b
done-clear         0xf20                    RFDMA enable/disable   0xc20
rf_set_power_level 0x1225, 0x1226, 0x1227
ZB_RADIO_TRX_CFG   0xf03, 0xf1c, 0xc0a, 0xc0b, dma irq mask
ZB_TIMESTAMP_ENABLE 0x430
```

**The two sets are disjoint.** Not one register that `rf_drv_init` restores is touched by
transmitting or receiving, in either firmware. In particular `0x1225`/`0x1226`/`0x1227` (the PA
code), `0x1228` (the VCO band, written by `rf_set_channel` on every frame in *both* firmwares) and
`0x1244`/`0x1245` (the channel word) are **not** in `rf_drv_init`'s tables, so a per-frame re-init
does not even restore the power registers.

The only overlap is `0xc20`, and it does not drift: `rf_drv_init` sets bits 2 and 3; our RX interrupt
clears bit 2 (`ZB_RADIO_RX_DISABLE`) and every path out of it sets bit 2 again
(`ZB_RADIO_RX_ENABLE`), including both early returns; bit 3 is never cleared by us.

A related suspicion also dies here. `ZB_RADIO_TRX_CFG` calls
`dma_irq_disable(FLD_DMA_CHN_RF_RX | FLD_DMA_CHN_RF_TX)`, and HANDOVER fact 2 records that this SDK's
`dma_chn_irq_enable(chn, 0)` *also* clears `reg_dma_chn_en` - the UART TX DMA bug. But
`dma_irq_disable` is a different function and touches only the mask:

```c
static inline void dma_irq_disable(unsigned int msk) { reg_dma_chn_irq_msk &= ~msk; }
```

so it does not disable the RF DMA channels. (`platform/chip_8258/dma.h:88`.)

**Conclusion: stock's per-frame `rf_drv_init()` is redundant. Its only effect on the air is the
70-110 µs it adds to every reply** - which, with the `sleep_us(500)` in front of it, is how stock
arrives at ~720 µs. Initialising once, as we do, is correct.

## 4. Eliminated: the crystal cap

- Stock applies it **before every frame**, inlined in `init_zigbee` at `0x33c6`: read flash `0x77000`,
  mask to 6 bits, `analog_write(0x8a, (analog_read(0x8a) & ~0x3F) | cap)`.
- Ours applies it in `radioInitHw()` - `rf_drv_cap(CFG_FREQUENCY_OFFSET_ADDR)` then
  `halRadioSetXtalCap(xtalCap)` - **once** at boot in the fast-TX build.
- Nothing in either firmware's TX or RX path writes analog register `0x8a`, so there is nothing to
  overwrite it and nothing to drift.

The byte at `0x77000` is `0x60` on this module, `0x60 & 0x3F` = 32, so both run cap code 32 and the
same carrier. This confirms HANDOVER's conclusion from the other direction: stock's self-healing
per-frame re-apply would matter only if something disturbed `0x8a`, and nothing does.

## 5. The receive configuration: equivalent in what it admits, different in how it buffers

Frame admission is byte-for-byte the same test. Stock's `rf_rx_irq_handler` (`0x3480`):

```
003482: if ((*0x800448 >> 5) & 1) return;          ; RF_DMA_BUSY()
003492: *0x800c20 &= ~4                            ; ZB_RADIO_RX_DISABLE
00349c: r3 = rxbuf[rxbuf[0] + 3]; if ((r3 & 0x51) != 0x10) drop   ; RF_ZIGBEE_PACKET_CRC_OK
0034a8: if (rxbuf[0] != rxbuf[4] + 9) drop                        ; RF_ZIGBEE_PACKET_LENGTH_OK
```

Ours calls the same two macros (`ZB_RADIO_CRC_OK`, `ZB_RADIO_PACKET_LENGTH_OK`) on the same offsets.
**Neither firmware enables hardware address filtering or auto-ACK, and neither is in a sniffer mode**;
both call `rf_drv_init(RF_MODE_ZIGBEE_250K)` - stock with the literal `8` at `0x33c0`, ours with the
enum, and `RF_MODE_ZIGBEE_250K = BIT(3) = 8`. Dest-address and PAN filtering is software in both.

Differences that exist but do not change which frames are seen:

| | stock | ours |
| --- | --- | --- |
| RX DMA buffer | one 250-byte buffer, re-pointed by `rf_rx_buffer_set` after every TX | ring of 8 × 160-byte slots, re-pointed in the interrupt (`rf_rx_buffer_reconfig`) |
| frame delivery | `memcpy` in the interrupt into a 50-deep `bbb[50][250]` queue | zero-copy; the main loop reads the slot |
| RX queue on TX | **flushed** (`current_buff_worked = current_buff`, `0x33b8`) before every frame | kept |
| `0xf1c` RF IRQ mask | never written; chip default | `FLD_RF_IRQ_RX \| FLD_RF_IRQ_TX` explicitly |
| `0xf03` bit 2 | never written | cleared (SRX timeout IRQ off) |
| `0x430` bit 1 | never written | set, for the RX timestamp |
| RX DMA size (`0xc0a`) | 250 → 15 | 160 → 10 |

160 bytes holds the worst case (5 header + 127 PSDU + 9 trailer = 141), so the smaller slot is safe.
Enabling the timestamp does not shift the trailer: `RF_ZIGBEE_PACKET_TIMESTAMP_GET` reads
`p[p[0]-4 .. p[0]-1]`, inside the 9 trailer bytes that `LENGTH_OK` already accounts for, which is why
our `LENGTH_OK` still passes.

The RX-queue flush is the one behavioural difference worth remembering: **stock throws away every
queued received frame each time it transmits**, so it can never act on a stale frame, while we can.
Our `tooLateToAnswer()` covers that, and `late` is 0, so it is not implicated - but it means stock
structurally cannot exhibit a stale-request bug and we can.

## Ranked candidates

**1. `RADIO_REPLY_PONG_US` is 970 in every build, not the 700 that was committed.** *This is the
cause.* Evidence: the compile flags, and the literal `0x3ca` at `0x2cd8` loaded at `0x2b82` into the
`halRadioTxReply` call that `sendPong` is inlined into. Predicts: a PONG preamble at +970 µs and its
tail 38 µs inside the tag's 2 ms window, so a cold scan joins only when jitter falls favourably, while
a paired tag's 5 ms check-in is untouched - the exact asymmetry observed. Fix: `makefile:106`
→ 700. Confirm with `AP_DEBUG=1` (which was `PING_LOG=1` when this was written): the per-ping line's
reply gap should read ~700, and `png` should be followed by the tag actually binding.

**2. Every other reply goes out at 1220 µs where stock sends at ≈ 720 µs.** Stock uses one wrapper
with one 500 µs constant for all seven of its reply sites; we use `RADIO_REPLY_AT_US` = 1220 for
check-ins, acks, XFC and cancels. 1220 + 992 = 2212 µs still fits a 4-5 ms check-in window, so this
is not obviously broken - but it is 500 µs later than the image that demonstrably works, on the path
whose `rep` runs 4-17 %, and HANDOVER already records the 950→1220 measurement as "unexplained by
anything the tag does". Predicts: `rep` falls when `RADIO_REPLY_AT_US` is brought to ~720. This is the
one to test *after* fixing candidate 1, and it is a one-line build-flag sweep
(`RADIO_REPLY_AT_US=720` against 1220, same image otherwise, `rep`/`ci` compared over equal windows).

**3. Nothing else differs in radio usage.** Priorities 2-5 of the brief come back clean: the receive
configuration admits the same frames, the transmit registers are the same (both run PA code 0x3F
= +10.46 dBm; `rf_drv_init` does not touch `0x1225`-`0x1227` at all), the crystal cap is code 32 in
both and nothing overwrites it, and the per-frame re-init restores a register set disjoint from
anything TX or RX writes. The only remaining structural differences - the RX ring depth, the queue
flush on TX, the timestamp bit - are listed above and none of them changes which frames reach the
dispatcher.

## If candidate 1 does not settle it

The measurement that discriminates, in one tag power-cycle, is already available: an `AP_DEBUG=1`
build (`PING_LOG=1` when this was written) after the makefile fix, reading the per-ping line's
**reply gap** (should be ~700, not ~970), the channel byte, the request age, the frame length, and
`larm`. If the gap reads 700 and a cold tag
still fails:

- **Log the armed tick against the tick that actually fired.** `rf_start_stx` matches `0xf18` for
  equality and the settle time comes from `REG_ADDR16(0xf04)`, which we read once at boot and only
  trust if it is 50..1000. Add `0xf04`'s value and the TX interrupt's `clock_time()` minus
  `lastRxEndTick` to the ping line: that turns the preamble's real position from an inference into a
  number. If `0xf04` is 150 where the code's history assumed 120, every scheduled reply is 30 µs out
  and the PONG's budget is the place it shows.
- **Then test the burst-contention hypothesis** HANDOVER already names, which this analysis neither
  confirms nor refutes: power-cycle a tag with no transfer running, then again during a 42-part
  burst. The 3-deep ring in `detectAP()` and a frame every ~4.9 ms (not ~1.6: that figure used a PONG's airtime for a block part's) is a real mechanism, and it is
  independent of the reply delay.
