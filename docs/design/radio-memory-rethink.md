# The TLSR AP radio: one clock, one switch, one pool

A proposal, radio side only. The S3's protocol (`oepl-radio-v2.h`: frame sizes, the 4-chunk window,
the request set) is taken as fixed here; the S3 side and the priority system are the next round.

Everything below is measured on this installation's radio (13 tags, channel 26, the `BOARD.md` site
build, `091f1f44` - a plain `make` since step 1) or read off the current image, not estimated.

## 1. The clock: 48 MHz, and 24 MHz goes

There is no reason left to keep 24 MHz.

- **Nothing depends on it.** The system timer is a fixed 16 MHz island at either speed
  (`chip_8258/timer.h`), so every timestamp, reply delay and burst gap is unchanged. 24 MHz was the
  closed-source firmware's choice, inherited, never argued for.
- **The UART is only exact at 48.** At 48 MHz, 2 Mbaud is `48 MHz / (2 × 12)`, 12 samples a bit
  (`hal_tlsr.c`). At 24 MHz the build falls back to the SDK's generic divider search.
- **The AP is mains powered.** The few mA the faster core costs are irrelevant on a USB supply.
- **It is the clock this site has run on** for the whole of the measured history.

What 48 MHz gives, concretely:

| | 24 MHz | 48 MHz |
| --- | --- | --- |
| slowest S3 request (a DATA_CHUNK, measured `rq`) | ~3.2 ms, more than its 1.3 ms on the line: the radio becomes the bottleneck of every preload and the S3's window sits full in the UART ring | **1.6 ms**, about line speed |
| a full scan of 1024 four-byte keys on the check-in path | ~300 µs of a ~2.4 ms reply window | **~150 µs** |
| longest main-loop pass (measured `lp`, after the one-frame-per-pass fix) | roughly double | **6.5 ms** |

The scan figure is the one that matters for the design: at 48 MHz a plain linear scan over the whole
fleet is affordable on the reply path, so the tag store below can be a flat array with no index.

## 2. The knobs: thirty to one

The makefile has 30 `?=` settings. Almost all of them are one of three things, and none of the
three should be a knob.

| kind | knobs | what to do |
| --- | --- | --- |
| **decided** - an A/B that has been measured and settled | `SYS_CLOCK_HZ`, `RADIO_TX_MODE`, `RADIO_REPLY_STX`, `UART_TX_DMA`, `EARLY_BURST`, `RADIO_APPLY_TX_POWER`, `RADIO_PONG_SPREAD` | fix the winning value and **delete the other code path** |
| **measured constants** - numbers a measurement set | `RADIO_REPLY_AT_US`, `RADIO_REPLY_PONG_US`, `RADIO_BURST_GAP_US`, `BURST_WINDOW_MS`, `BURST_COPIES`, `V2_LOG_GAP_MS`, `RADIO_XTAL_CAP` | constants in the source, next to the measurement that set them |
| **sizes** - which RAM goes where | `CACHE_ARENA_BYTES`, `CACHE_BLOCKS`, `CACHE_BLOCKS_CAP`, `MAX_ALLOWED_MACS`, `MAX_PENDING_MACS`, `URX_RING`, `RAMCODE_SPILL_OK` | **derived** from what the build leaves free and what the site holds (section 3) |
| **experiments** whose question is answered | `RADIO_REPLY_SPREAD`, `XTAL_SWEEP_N/_LO/_STEP/_DWELL_MS` | remove; git has them if the question reopens |

Done in step 1 (`ed9d0142`) as the table says, with one difference: `RADIO_XTAL_CAP` was removed with
its sweep rather than made a constant, since the boot leaves the module's factory code (32) and
nothing trims it.

What stays: **`AP_DEBUG`**, the one switch for instruments. **`TAG_TX_POWER_MAX_DB`** was to stay
until the S3 could switch it, and was removed instead, unshipped: it learned only from check-in
replies but lowered the power of every frame to the tag, block parts and acks included, where a loss
costs a request round rather than a retry; and what it buys - less interference to a neighbour on our
channel - is something only the S3 knows a site has. If it comes back, it is as a `SET_CONFIG` flag
in the S3 round, not as a build option.

## 3. RAM: what has to be fixed, and what should be one pool

### What the image holds today (site build - a plain `make` since step 1 - 57368 bytes of `.bss`)

| | bytes | bounded by | peak measured (`hw`) |
| --- | --- | --- | --- |
| arena: cache + pending table + allow-list | 48792 | a constant someone chose | - |
| radio RX DMA slots, 8 × 144 | 1152 | how many frames arrive during the longest pass | **1** |
| events to the S3, 24 cells × 51 | 1224 | how long an S3 stall is tolerated | **1** |
| ... and their 3 side buffers × 115 | 345 | tag return data in flight | **0** |
| LOG queue, 6 × 101 | 606 | one diagnostics report | 5 lines, ~350 bytes |
| UART RX ring | 1024 | **the S3's window**: 3 frames waiting while the 4th is handled | **935** |
| UART TX DMA buffers, 2 × 268 | 536 | one frame in flight, one being filled | - |
| S3 frame building: `rawBuf` 261 + `wireBuf` 264 | 525 | one frame | - |
| S3 frame receiving: `rxCobs` | 263 | one frame | - |
| last reply, for a lost-ack resend | 58 | one reply | - |
| radio TX: `radiotxbuffer` 128 + `txBuf` 133 | 261 | one frame | - |
| radio RX: the frame copy `rx` | 144 | one frame | - |
| MAC → hwType table, 64 × 9 | 576 | **64 tags** | - |
| allow-list prefix table, 32 × 7 | 224 | **32 families** | 13 used by 13 tags |
| cache slot metadata, 11 × 24 + 11 | 275 | the most blocks the arena can hold | - |
| interrupt stack | 512 | deepest IRQ path, **116 bytes** by static analysis | - |
| everything else | ~100 | | |
| *stack (not .bss): floor 2048, gets 2728 (768 more since `FLASH_RESIDENT`, below)* | | deepest main path, **796 bytes** by static analysis | not measured |

### Three things wrong with it

**1. The same data is stored, copied and indexed several times.**

- A tag's MAC is stored three times - in the allow-list, in its pending entry, and in the hwType
  table - with three different encodings and three different scans on every check-in.
- A radio frame received is DMA'd into a slot, then **copied** into `rx` before anything reads it.
  A radio frame sent is built in `radiotxbuffer`, then **copied** into `txBuf` for the DMA.
- A frame to the S3 is assembled in `rawBuf`, COBS-encoded into `wireBuf`, then **copied** again
  into a UART DMA buffer: three buffers and two copies for every byte sent.

**2. Things that scale with the site are fixed, and things fixed by physics are not where the slack is.**

- Three per-tag structures are sized for three different fleets: the hwType table for **64** tags,
  the pending table (until today) for **250**, the allow-list for **1024**. At 200 tags the 64-entry
  table evicts each entry ~38 s after it is written while a tag checks in every ~2 minutes, so early
  bursts (and per-tag power, while it existed) silently stop working on exactly the sites that need
  them.
- The outbound queues are sized for their worst case in fixed cells - 2.2 KB for traffic that
  measured at one event and five log lines.

**3. The allow-list's compression does not compress this fleet.** It assumes a fleet shares
`mac[3..7]`. On these Solum tags the serial is in `mac[2..3]`:

```
0000032C91C23E17  mac[0..7] = 17 3e c2 91 2c 03 00 00
0000032C73D23E1A  mac[0..7] = 1a 3e d2 73 2c 03 00 00
```

So 13 tags are 13 "families", the 32-slot prefix table is full at 32 tags, and every tag after that
costs the full 8 bytes. `mac[4..7]` groups them correctly: **7 families for 13 tags**. Every "one
family" band in `radio-sram-split.md` describes a fleet this hardware does not make; the "own family"
column is the real one past 32 tags.

### The structure it should have

Two kinds of memory, and the dividing line is simple: **is the size set by physics or the protocol,
or by the site?**

**Fixed, at the bound the hardware or protocol sets** - these are always needed at their bound, so a
pool would gain nothing:

| | size | why that size |
| --- | --- | --- |
| UART RX ring | 1024 | (window − 1) × 264 + control frames = 935 measured. It grows only if the S3's window does |
| UART TX DMA, 2 frames | 536 | one in flight, one being filled - **and frames are COBS-encoded straight into it**, streaming, with the CRC computed on the way: `rawBuf` and `wireBuf` go (**−525**) |
| radio TX DMA buffer | 133 | ap.c builds the frame **in it**: `radiotxbuffer` goes (**−128**) |
| radio RX DMA slots | 4 × 144 | the frames that can arrive during a pass. Peak measured 1 of 8, with passes now ≤ 6.5 ms; four leaves 3× margin and `drop` counts the loss (**−576**). Frames are parsed **in their slot**: `rx` goes (**−144**) |
| S3 frame receive | 263 | one frame, decoded in place (already) |
| one outbound ring, events and LOG lines | ~1024 | **one** byte ring of variable-length records instead of three fixed-cell arrays: a CHECKIN costs its 49 bytes, a LOG line its length, a tag report its 115, and the drop order is LOG lines oldest first, then check-ins, never a transfer result (**−1150**) |
| interrupt stack | 256 | 2.2× the 116-byte static bound - after a **measured** high-water (`cstartup` fills it with 0xFFFFFFFF) confirms it (**−256**) |
| main stack | 2048 floor | 2.6× the 796-byte static bound. The same fill pattern gives its real high-water; the floor is the one reserve left, so it is the last thing to move |

That is **~2.8 KB** that comes back from copies and fixed cells without giving up a margin that has
been measured to matter.

**And 768 bytes have already come back from RAM code** (`FLASH_RESIDENT` in `boot_8258_ap.link`):
the SDK's sleep entry, 32 kHz tick readers, analog register accessors and ADC supply check were RAM
code for a chip that sleeps and writes flash, and this one does neither, so they are linked into
`.text`. The RAM code went from 2812 bytes to 1936 and `.data` starts 768 bytes lower. The ADC check
is the one with a condition - a page program or sector erase needs it in RAM - so the linker refuses
a build that links either. Not yet run on the radio; the headroom it gave is unspent.

**One pool for everything the site sizes**, and the pool's size is **whatever the build leaves**:
the linker places it from the end of `.bss` to the stack floor, so `CACHE_ARENA_BYTES`,
`_bss_headroom_` and the RAM-code boundary all stop being things anyone tunes. A feature that costs
RAM costs pool, not a link failure; the boot `cfg` line reports what each tenant can have. Tenants:

1. **The tag store** - one record per tag the radio knows, replacing the allow-list, the pending
   table, the hwType/power table and the prefix table.
2. **Offers** - the content a tag has been told about, for the tags that have any.
3. **Cache blocks**, 4104 bytes each, from the pool's top.

### The tag store

One flat array of fixed-size records, scanned linearly on the check-in path - which 48 MHz makes
affordable at any fleet this radio can hold - with the hot key first so the scan is one aligned
load and compare per tag.

```c
struct tagRec {             // 12 bytes, 4-aligned
    uint32_t key;           // the three MAC bytes that vary, and the profile index: compared first
    uint8_t  flags;         // allowed, learned, has an offer, idle entry live
    uint8_t  pwr;           // was per-tag power state; spare since step 1 removed it
    uint16_t sleepUntil;    // the idle "check in again in n minutes", as a deadline in minutes
    uint16_t expires;       // when that entry lapses, likewise - deadlines, so housekeeping
                            // no longer rewrites every entry every minute
    uint16_t offer;         // index into the offer pool, or none
};
struct profile { uint8_t shared[5]; uint8_t hwType; uint16_t refs; };   // 8: 7 for this fleet
struct offer   { uint64_t dataVer; uint32_t dataSize; uint8_t dataType, dataTypeArg; uint16_t tag; };  // 16
```

**What a record holds is decided by what varies.** Per byte position, in this fleet (13 tags):

| | mac[0] | mac[1] | mac[2] | mac[3] | mac[4] | mac[5] | mac[6] | mac[7] |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| distinct values | 7 | 3 | **13** | **13** | 7 | 3 | 1 | 1 |

`mac[2..3]` is the serial, `mac[0]` looks like a check byte, and `mac[1]`, `mac[4..7]` are shared
within a family. So the key word is `mac[0]`, `mac[2]`, `mac[3]` and a one-byte **profile** index, and
the profile holds the five shared bytes - **and the hwType**, which on this fleet is a function of
the family (021x tags are hwType 0, 03xx are 4, BC9F is 225). Anything a group of tags has in common
goes in the profile; a tag that differs in any of it simply gets a profile of its own, so the
encoding is exact and the worst case is more profiles, never a wrong answer.

**Which bytes are "shared" is measured, not assumed.** The positions above are this hardware's; a
fleet from another vendor may put its serial elsewhere, which is exactly the mistake the current
encoding made. So when the S3 has sent its database (it sends the whole of it after every HELLO),
the radio counts distinct values per byte position, puts the three most varied in the key and the
rest in the profile, and builds the store in that order. Re-choosing is one O(n) pass, and it only
happens when the S3 re-sends the list anyway. This is the "always optimal" the encoding can afford:
optimal among fixed-width encodings for the fleet actually present.

**Why not an entropy code - Huffman, Elias-Fano, a delta-coded sorted list.** Measured, the
information left in a tag's ID after the profile is about 22-28 bits for a large fleet (the 16-bit
serial, a few bits of check byte, a few of profile), against the key's 32. So the best any code can
do is ~0.5-1 byte a tag: 230-450 bytes at 456 tags, a tenth of a cache block. For that, every lookup
on the reply path - which has ~2.4 ms, and today is one aligned load and compare per tag - would
become a sequential bit-level decode, and every ALLOW_ADD or SET_PENDING would rewrite the coded
stream. The redundancy that is worth taking is the repetition, and a dictionary takes nearly all of
it at fixed width; the rest is genuinely per tag.

The same rule applies to everything else a record could hold: per-tag fields are only what is per
tag. The idle entry's `nextCheckIn` and TTL become deadlines (2 bytes each), not counters every tag
rewrites once a minute; the offer's 16 bytes exist only for tags that have content; and the byte
that was to hold per-tag power state is spare (the record stays 12 for alignment).

- **One key, one scan.** A check-in, a block request, a tag report and the lock all find the tag
  the same way: scan `lo`, confirm `fam`. Today they make three scans of three structures.
- **What a tag costs is what it uses**: 12 bytes for a tag that is only listed and sleeping - which
  is nearly every tag nearly all the time - and 16 more while content is offered to it. Today it is
  8 (list, at real MACs) + 26 (pending) + 9 (hwType, for 64 of them) = 43.
- **Nothing is sized for a fleet.** Records and offers grow in granules within the pool, and give
  the room back; records with no flag set (a tag heard but neither listed nor pending) are the first
  reclaimed. Deletion is swap-with-last, and fixing the one offer that points at the moved record.
- **The guarantee carries over unchanged**: the inventory lock is never the thing refused. A
  listed tag always gets a record; an offer that finds no room is `NAK NO_SLOT`, which the S3 already
  retries at the next check-in.
- **hwType stops being lost at scale**, because it lives in the one record a tag keeps
  for as long as it is in the fleet.

### What the pool buys, against today

**Projected, not measured** - arithmetic on the sizes above, to be replaced by measurement as each
step lands. Same rule as today (the stack floor is the only reserve), a fleet whose every tag is
listed and sleeping, real MAC structure:

| fleet | blocks today | blocks proposed |
| --- | --- | --- |
| 13 tags (this site) | 11 | **12** |
| 120 | 11 → 10 | 12 |
| 200 | 10 | **12** |
| 456 | 8 (the pending maximum) | 11 |
| 1000 | refused past 456 | 9 |

The pool is ~52 KB (today's arena, the ~2.8 KB above, and the headroom the linker now hands over),
so twelve blocks (49248) leave ~2.9 KB for records - about 240 sleeping tags - before the cache gives
anything up, and each block given up is room for about 340 more.

## 4. Order of work, each step measured on the radio before the next

1. **Decide and delete**: 48 MHz, fast TX, STX replies, UART DMA, early bursts, applied power, the
   PONG offset; timing knobs to constants; the two finished experiments removed. Behaviour-identical
   to what is on the radio now, so the check is that nothing moves. **Done in `ed9d0142`**, with
   per-tag power removed as well (section 2): the default build's code is instruction-for-instruction
   the `9cb1a1bc` image on the radio, `.bss` 57368 and headroom 680 unchanged (632 with `AP_DEBUG=1`),
   45 of 45 host tests.
2. **Measure the stacks**: high-water of both stacks on the `hw` line (the 0xFFFFFFFF fill), so the
   one reserve left is a measurement, not an analysis.
3. **Stop copying**: radio frames parsed and built in their DMA buffers, S3 frames encoded straight
   into the UART DMA buffer, events and LOG lines in one byte ring, RX slots to four.
4. **The pool**: linker-sized, replacing `CACHE_ARENA_BYTES`; maxima reported, not configured.
5. **The tag store**: records, measured profiles, offers - replacing four structures. The host
   tests' fleets get real MAC structure (serials in `mac[2..3]`), so their bands mean something.

Steps 1-3 are each a day or less and independently useful. Step 5 is the big one and the one with
the largest gain; it is also the one that most wants the S3 round straight after, because the S3's
own tag database and priorities are where offers come from.
