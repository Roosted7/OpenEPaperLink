# Tag timing per firmware family

What each tag family does while it waits for the AP, read from the tag firmware source. The AP has to
serve all of them with one radio, so these numbers decide every timing choice it makes: when a reply
may start, what `pleaseWaitMs` to offer, when a block burst may begin, and when a request is too old
to be worth answering.

Compiled 2026-10-31 from the firmware repositories of the OpenEPaperLink organisation at `master`,
plus the TLSR tag firmware in this repository. It corrects several numbers in the timing-budget table
of [radio-chain-analysis.md](radio-chain-analysis.md), which covered three families and had some of
them wrong; where the two disagree, this file is the later reading and cites the code.

**Provenance.** `Tag_FW_nRF52811` and `Tag_FW_88MZ100` get the protocol from
`Shared_Tag_CPP_Libraries/oepl-protocol.cpp`. The submodule pin was not resolvable through the GitHub
contents API, so those two were read at `Shared_Tag_CPP_Libraries@master` (HEAD `e3c43bc`,
2026-03-15). If either repository pins an older commit, their rows may differ.

## The table

| | ZBS243 (Solum M2) | nRF52811 (Solum M3) | TLSR8258 | Opticon (CC253x) | 88MZ100 | Chroma (CC111x) | EFR32xG22 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| repo | `Tag_FW_ZBS243` | `Tag_FW_nRF52811` + shared lib | this repo, `ARM_Tag_FW/OpenEPaperLink_TLSR` | `Tag_FW_Opticon` | `Tag_FW_88MZ100` + shared lib | `Tag_FW_Chroma` | `Tag_FW_EFR32xG22` |
| hwType | `0x00`–`0x05`, `0x11`, `0x12`, `0x20`–`0x27` (**`0x27` is the last M2**); seg `0xF0`/`0xF1` | `0x28`–`0x2D` (**confirmed M3**: `resources/tagtypes/28.json`…`2D.json` are "M3 1.6\" BWRY" … "M3 12.2\""), `0x2E`–`0x47`, `0x4A`–`0x4F`, `0x53`, `0x90`–`0x93`; `0x37` = 11.6" | `0x57`, `0x58`, `0x59`, `0x63`, `0x69` | `0x06`–`0x09` | `0x05`, `0x24`, `0x26` — **the same values a Solum M2 reports** | `0x80`, `0x82`, `0x83`, `0x84` (sub-GHz) | Solum M3 autodetect incl. `0x28`–`0x2D`; `0x50` for MODCHIP_HD150 |
| PING → PONG | **2 ms** × 20 | 5 ms × 20, but `HAL_msDelay(10)` sits inside the window, so effectively ≈ 10 ms and the PONG is read one ping later | 5 ms × 20 | **2 ms** × 20 | 10 ms × 3 | 150 ms × 3 | 20 ms |
| check-in reply | 5 ms × 14 | 5 ms × 14 | 5 ms × 14 | 5 ms × 10 | **4 ms × 3** | 200 ms × 14 | 20 ms, retries driven by a timeout count |
| **the windows above are measured from** (see "where a window actually starts") | **end of the ack wait**: `radioTx()` ends `while (!mAckTimePassed);`, and ping/check-in set `ackReqd` — **measured +1200…1350 µs** past the end of the frame | end of frame (`PHYEND`→`DISABLE`), +~50–70 µs | end of frame (TX-done IRQ), + `zigbee_rx_start()` and the RX settle, ~+100–200 µs | end of frame (`while (FSMSTAT1 & TX_ACTIVE)`), + the CC2530 turnaround, ≤+192 µs | **end of the ack wait** for ping and check-in (`ack_expected = tx_buffer[0] & 0x20`), ~+900 µs **[?]**; end of frame for the block request | not read | **~930 µs BEFORE the end of the frame** — `RAIL_StartTx()` is async and the timer starts while the frame is still queued; receiver live ~+190 µs |
| ack-request bit set on | ping, both check-ins, tag-return-data; **not** block request or xfer-complete | same (but no MAC, so it is decoration) | ping, both check-ins | **nothing — `ackReqd = 0` on all four paths** | ping, both check-ins | not read | ping, both check-ins (RAIL told to wait only for the ping) |
| short check-in | yes | yes | yes | **no** | yes | yes | yes |
| block request → ack | 7/8/9 ms × 30 | **6 ms × 10** | 50 ms × 30 | 11–12 ms × 10 | 6 ms × 10 | 30 s × 30 | 350 ms × 10 |
| how `pleaseWaitMs` is spent | `< 35`: busy-wait `pw − 5`, **radio on**. `≥ 35`: sleeps `pw − 25`, then radio on — so it listens **25 ms** early, not 5 | sleeps `pw` with the radio **off**, even for `pw = 0`, then re-inits the radio (≈ 0.5–2 ms) | busy-wait `pw − 10`, radio on | **radio OFF until `pw + 40 ms`** | busy-waits the **full** `pw`, radio on | `< 35`: full `pw`, radio on. `≥ 35`: sleeps `pw − 10` | radio **off**, sleeptimer for exactly `pw`; never listens early |
| `pleaseWaitMs = 0` | guarded: `if (ack->pleaseWaitMs) … else // immediately start with the reception` — straight into the receive loop, radio still on | **not guarded**: a bare `doSleep(pleaseWaitMs)` then `powerUp(INIT_UART \| INIT_RADIO)` and `radioRxEnable()`, so zero still costs the re-init | guarded, same shape as the ZBS243 | guarded — the radio is powered down *inside* the branch, so the `pw + 40` above does **not** apply at zero | guarded | guarded | assumed as the nRF (not re-read) |
| unsafe `pleaseWaitMs` | **1–4** → `pw − 5` underflows a `uint16_t` into a ~53 minute busy-wait, i.e. a watchdog reset | none | 1–9 (same shape, ≈ 65 s) | none | none | none | none |
| block-part window | 315 ms, **exits early** when complete | 300 ms; radio off early but the loop runs the full 300 ms, then sleeps 30 ms before the next request | 300 ms fixed | 280 ms, **no early exit in practice** | 290 ms, no early exit | 310 ms, exits early | 350 ms, then 10 partial-request retries |
| RX buffering | 3-deep ISR ring | 8-deep ISR ring, re-armed inside the IRQ | — | **one frame** (RXFIFO + DMA) | **one** buffer | 3-deep ring | RAIL FIFO, one packet per pass |
| XferComplete ack | 6 ms × 16 | 6 ms × 16 | 6 ms × 16 | 10 ms × 16 | 6 ms × 16 | 150 ms × 16 | 10 ms × 16 |

**Universal:** every family throws away the first `BLOCK_PART` it sees while still waiting for the
block-request ack, and uses it only as the signal to jump into its receive loop. The call that would
have stored it is commented out in ZBS243, Opticon and the TLSR, and absent in the shared library.

## Where a window actually starts

Every window in the table above is a software timeout taken *after the tag's transmit call returns*,
and **that is not the end of the frame on three of the seven families.** This was found by hardware
measurement, not by reading: a scanning ZBS243 three feet from the AP accepted a PONG at **1350 µs**
after the end of its ping and ignored 150, 300, 450, 600, 750, 900, 1050 and 1200 — three scans, nine
pongs each. Everything the AP had computed was aimed at a receiver that was not yet listening.

**The mechanism, from the tag's own driver.** `Tag_FW_ZBS243/zbs243_shared/soc/zbs243/radio.c:159-163`
ends `radioTx()` with

```c
    // wait for tx to end
    if (wait) {
        while (!mAckTimePassed)
            ;
        return true;
```

and `mAckTimePassed` is set by the radio interrupt on cause bit `0x40`, commented "ack time
happened" (`radio.c:30-34`). `sendPing()` sets `txframe->fcs.ackReqd = 1`
(`tag_fw/syncedproto.c:142`), so the radio runs the full 802.15.4 acknowledge wait —
`macAckWaitDuration`, 54 symbols = **864 µs** from the end of the frame — before raising that
interrupt. `detectAP()` only then reaches `t = timerGet() + …` (`syncedproto.c:158-159`). So the
window is `[end of ack wait + 8051 return overhead, +2 ms]`, and the measured 1200…1350 µs is 864 µs
plus that overhead. **While the ack wait runs the radio is in TX/ack-wait state and cannot receive at
all**, so a reply aimed inside it is destroyed in hardware, not merely read late.

**Which paths it applies to**, per family, with the exact lines:

| | sets `ackReqd` | so the window opens at |
| --- | --- | --- |
| ZBS243 ping `syncedproto.c:142`, full check-in `:202`, short check-in `:186` (`outBuffer[1] = 0x21`, bit 5), tag-return `:288` | **1** | end of frame **+1200…1350 µs** (measured) |
| ZBS243 block request `:389`, xfer-complete `:447` | 0 | end of frame + turnaround |
| 88MZ100 ping `oepl-protocol.cpp:96`, full check-in `:155`, short check-in `:139` | **1** | end of frame **+~900 µs [?]** — `Zigbee_tx_buffer` reads `ack_expected = (tx_buffer[0] & 0x20) != 0` (`tag_fw/zigbee.c:453`), writes it to PHY `0x4C000808` bit 25 (`:462`) and busy-waits for the TX-done interrupt that also carries the ack result (`:470-492`, `PHY3_IRQHandler:419-421`). **Inferred, not proven**: the ack-wait duration is silicon behaviour with no datasheet in the tree |
| 88MZ100 block request `:300`, xfer-complete `:356` | 0 | end of frame + ~30 µs |
| **Opticon — all four paths** `proto.c:115`, `:165`, `:302`, `:370` | **0** | end of frame + turnaround. `radioTx()` ends `while (FSMSTAT1 & FSMSTAT1_TX_ACTIVE);` (`lib/meinradio.c:177`) and there is no ack wait anywhere in the tree |
| nRF52811 | 1, but there is **no MAC**: the bare-metal `zigbee.cpp` has no ack handling, `PHYEND`→`DISABLE` ends the transmit at the end of the frame (`zigbee.cpp:151`, `:36-41`), and `TIFS = 192` is inert because the RX re-enable is in software | end of frame + ~50–70 µs |
| TLSR tag | 1, same — `radioTxLL()` is `init_zigbee(); zigbee_tx(); zigbee_rx_start();` and `zigbee_tx` waits only on the TX-done interrupt (`src/zigbee.c:29-30`, `:172-178`) | end of frame + ~100–200 µs |
| EFR32xG22 | 1 (RAIL told to wait only for the ping) | `RAIL_StartTx()` is asynchronous and the sleeptimer starts before the frame is even on air, so the *reference* is ~930 µs **early**; the receiver is live ~190 µs after the frame via the configured `TX_SUCCESS → RAIL_RF_STATE_RX` transition |

**So the correction applies to exactly two families, and they are the two with the tightest windows.**
It does not generalise: the Opticon — the other 2 ms family — sets `ackReqd = 0` everywhere and is
clean, and for the nRF52811, the TLSR tag and the EFR32 a reply aimed at the end of the frame is at
worst ~200 µs early inside a 5–20 ms window.

### The budget this leaves for a check-in reply

Taking the window as `[W, W + length]` from the end of the tag's frame, requiring our reply to start
after `W` and to finish before the window closes, and using the measured airtimes (`(PSDU + 6) × 32`,
so an `AvailDataInfo` reply is 41 bytes = **1504 µs**):

| family | `W` | window | reply may start in |
| --- | --- | --- | --- |
| ZBS243 | 1250 (measured) | 5 ms × 14 | **[1250, 4746]** |
| 88MZ100 | ~900 [?] | 4 ms × 3, and `millis()` truncation makes it 3.0–4.0 ms | **[900, 2396]** worst case |
| Opticon | ~200 | 5 ms × 10 (4.883 ms real, 32 kHz RC timebase) | [200, 3379] |
| TLSR tag | ~150 | 5 ms × 14 | [150, 3496] |
| nRF52811 | ~70 | 5 ms × 14 | [70, 3496] |
| EFR32xG22 | ~190 | 20 ms | [190, 18500] |

**The intersection is `[1250, 2396]`,** bounded below by the ZBS243's ack wait and above by the
88MZ100's short, truncated window. A single delay of about **1700 µs** sits ~450 µs inside both
edges. Note that 1700 also falls inside the ZBS243 *ping* budget — `[1250, 1250 + 2000 − 992] =
[1250, 2258]` — so one value can serve both, which is worth knowing because these two constants have
twice been moved together by accident.

## What a window costs the tag, in receiver-on milliseconds

**This is the objective function for the whole AP: tag receiver-on milliseconds per successful
update.** Not AP airtime, not frames on air. The AP has to be right for a handful of tags with no
neighbouring APs and for 300+ tags with other APs close by, and the quantity to minimise at both
scales is how long tags hold their receivers on.

Two facts from the tag firmware make the arithmetic concrete, and both cut against an
airtime-minimising instinct:

| what the tag is doing | receiver | cost when we do not land the reply |
| --- | --- | --- |
| `detectAP()` cold scan | enabled **before** the attempt loop, on throughout | 20 x 2 ms = **40 ms awake per channel round**, then "No AP found" and periodic rescans for ever |
| `getAvailDataInfo()` check-in | on across all attempts | 14 x 5 ms = **70 ms awake** |
| block part window | on | a re-request is another **~300 ms awake** |
| `pleaseWaitMs` >= 35 on a ZBS243 | **off** (sleeps `pw - 25`) | nearly free - a retry with a sleep in it is cheap |
| `pleaseWaitMs` < 35 on a ZBS243 | **on** (busy-waits) | the wait itself is awake time |

So: **a retry with a sleep between the attempts is cheap; only a retry that keeps the receiver on is
expensive.** And a repeat frame inside a burst costs the receiving tag nothing - it is already
listening - while saving it ~300 ms of awake time when it lands. What repeat frames really cost is
*other* tags' awake time, when our transmitting makes them miss a window and burn their own 70 ms or
300 ms. That, and not airtime, is the thing to weigh a burst against. Transmit power belongs in the
same ledger: more power than a link needs desensitises neighbouring APs and their tags, which pays for
our margin with their retries.

**Received frames are queued, and the queue is not flushed between attempts.** On a ZBS243
`commsRxUnencrypted()` dequeues from a 3-deep ISR ring (`radioRxDequeuePktGet` /
`radioRxDequeuedPktRelease`) and `detectAP()` calls `radioRxFlush()` only before its loop. Two
consequences the AP timing has to respect: a reply that arrives just after a window closes is still
read by the next attempt, so being slightly late costs one attempt rather than the exchange; but a tag
whose ring fills with traffic it does not want - every frame of someone else's burst - drops our reply
before its own code ever looks at it. The single-frame families (Opticon, 88MZ100) have no ring at all
and so no slack of the first kind.

## What this means for the AP

1. **`pleaseWaitMs` is not one number.** The latest a tag can start listening is `pw + 40 ms`
   (Opticon) and the earliest is `pw − 25 ms` (ZBS243 with a long wait). A burst aimed at the wrong
   one of those is airtime the tag never hears, and it pays for it with another request round — about
   300 ms of radio. `tagListensAfterAckMs()` in `ARM_Tag_FW/OpenEPaperLink_TLSR_AP/src/ap.c` is the
   model; it is keyed on hwType and defaults to the safe `pw` for a type it has not seen. At `pw = 0`
   it answers the radio re-init instead, because every family skips its wait there (point 6).

   The AP cannot aim at both ends of that 65 ms spread, so the download wait is chosen to make the
   *early* end harmless: `DATA_WAIT_MARGIN_MS` is `25 + 1`, so a ZBS243's `pw − 25` wake lands where
   the burst can really start — one millisecond after the slowest block the host has produced. At the
   20 ms it was, that tag woke 5 ms before the block existed and the burst then started late, which
   also charges those milliseconds against the burst's own window budget.
2. **"Listens early" ends at `0x27`, not `0x2E`.** The M2 range stops at `SOLUM_M2_BW_29 = 0x27` and
   `0x28`–`0x2D` are M3 tags, which sleep with the radio off; `0x2E` is only where
   `oepl-definitions.h` happens to give an M3 type its first *name*
   (`SOLUM_M3_BWR_97`), and keying on it took six M3 hwTypes for ZBS243s and aimed their bursts
   25 ms before they start their radio at all. `tagListensEarly()` uses `0x28` now. Below that the
   range still is not uniform: it also holds Opticon (`0x06`–`0x09`, radio off longest of all) and the
   88MZ100 (`0x05`, `0x24`, `0x26`, full wait), both excluded by cases of their own.
3. **The 88MZ100 cannot be distinguished from a Solum M2 by hwType.** Both report `0x05`, `0x24`,
   `0x26`. Anything keyed on those values has to take the safe side.
4. **The tightest window is the wrong input for a stale-request limit, and a PING is the clear case.**
   The old rule was "answer only inside the tightest family's window" — 2 ms for a PING, 4 ms for a
   check-in (88MZ100, three tries), 6 ms for a block request (nRF52811) — on the reasoning that a
   later reply is airtime the tag cannot use. Two things are wrong with it. The queue is not flushed
   between attempts, so a reply that arrives after a window closes is usually still read; and the
   family is **unknown** on a PING, because a tag that is pinging is one we have never had a full
   check-in from, while the windows range from 2 ms (ZBS243, Opticon) to 20 ms (EFR32xG22). Priced in
   the ledger above, refusing costs the tag a scan attempt with its receiver on and risks the 40 ms
   per round of "No AP found" for ever, against 992 µs of our air — so `tooLateToAnswer()` bounds the
   PING by the **widest** window and not the tightest: `832 µs` of the tag's own ping airtime plus
   20 ms, less the PONG's own 992 µs on air. That is a bound against transmitting into nothing, not a
   guess at the family.
5. **Two families leave their receive loop as soon as the block is complete** (ZBS243, Chroma) and
   four do not (nRF52811, 88MZ100, Opticon, TLSR). Repeat frames in a burst are therefore free
   insurance for the latter and wasted airtime for the former once the block is through — which is why
   the TLSR AP stops a burst when the tag reports the transfer complete, checks in again, or asks for
   another block.
6. **`pleaseWaitMs = 0` is not free on every tag, and the fix belongs in the burst, not the ack.** The
   nRF52811 sleeps unconditionally, so even a "carry on receiving" ack costs it a radio re-init of
   0.5–2 ms, and the frames sent during it are exactly the ones it comes back for. It is free on
   ZBS243, Opticon, Chroma, the 88MZ100 and the TLSR tag, which all guard the wait and fall straight
   into their receive loop with the radio on — the `pleaseWaitMs = 0` row of the table cites each.
   Note what that means for the Opticon: its `pw + 40 ms` is inside the guarded branch, so at zero it
   is as immediate as the rest.

   The ack therefore carries 0 for **every** family, and the mid-burst re-ack in `ap.c` holds the
   burst's next frames for `tagRadioBackAfterAckMs()` instead — 0 up to hwType `0x27`, the re-init
   allowance from `0x28` up and for an unknown type. Naming a non-zero wait would be worse on both
   sides: anything an M3 could sleep through has to be ≥ 10 ms to clear the ZBS243's `pw − 5` and the
   TLSR tag's `pw − 10` underflow, and a wait below 35 ms is one a ZBS243 or 88MZ100 spends
   busy-waiting with its receiver **on** — paying 10 ms of awake time to cure a problem it does not
   have.
7. **Minimum inter-frame gap:** none of the families needs more than the AP already leaves. Even the
   single-buffer tags (Opticon, 88MZ100) get a frame every ~4.9 ms and drain it before the next
   payload arrives.

## Not verified

Listed so nobody takes silence for confirmation:

- **hwTypes `0x54`–`0x5A`, `0x60`–`0x62`, `0x66`–`0x71`** ("HS …" / Hanshow Nebular). Present in
  `resources/tagtypes/*.json` and `Shared_OEPL_Definitions/oepl-definitions.h`, but no firmware in the
  organisation reports them. Their windows are unknown. If any behaves like the Opticon, point 1 above
  needs them too.
- **hwTypes `0xD0`–`0xD5`** ("GFSK10 …"). No firmware found that reports them.
- **hwType `0x81`** (Chroma Aeon 7.4"). No board header defines it; presumably the chroma74 build.
- The per-build hwType mapping inside `Tag_FW_ZBS243`, and `0x21`/`0x27` (ST-GM29…), were taken from
  the definitions header and the tagtype JSONs rather than from each board header.
- The radio re-initialisation times quoted for the nRF52811 (0.5–2 ms) and Opticon (≈ 200 µs) are
  estimates from reading the code, not measurements.
- **The 88MZ100's acknowledge wait, and that its TX-done interrupt is gated on it at all.** The
  firmware reads the ack-request bit and arms the MAC with it, then waits for one interrupt that also
  carries the ack result — so the interrupt must come after the ack handling, but the duration is
  silicon behaviour and there is no datasheet in the tree. ~900 µs is `macAckWaitDuration` plus an
  estimate of the return path, not a measurement. It is the one family where the AP is acting on an
  inference, and it is the family it cannot identify by hwType either (point 3 above). The test is the
  one that settled the ZBS243: a scope on the tag, or an AP-side sweep against a known 88MZ100.
- **`W` for each family other than the ZBS243.** Only the ZBS243's is measured (1200–1350 µs, from a
  tag accepting a PONG at 1350 and refusing every step down to 150). The rest are the sum of a
  documented or source-visible ack wait and an estimate of the software return path, and the
  turnaround figures inside them (CC2530 ≤192 µs, nRF `tTXDISABLE` + 40 µs fast ramp-up, RAIL
  `txToRx` ≈182 µs) are datasheet or SDK numbers, not readings from these parts.
- **The Chroma column of the reference row.** Its transmit path was not read; it is sub-GHz, so no
  2.4 GHz AP serves it, and its windows (150 ms ping, 200 ms check-in) are wide enough that the
  reference cannot matter.
- Chroma is sub-GHz, so a 2.4 GHz AP never serves it. Its windows are wide enough that no AP timing
  choice can break it; it is in the table for completeness.
