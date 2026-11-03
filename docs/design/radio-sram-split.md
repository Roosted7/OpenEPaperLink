# The TLSR AP's SRAM: one arena, split between the block cache and the allow-list at run time

The TLSR8258 has 64 KB of SRAM. Two things in the AP radio firmware are large enough to argue over
the last kilobytes of it, and until now the argument was settled at compile time:

| | bytes | |
| --- | --- | --- |
| block cache | 29276 | `CACHE_BLOCKS` × 4184 |
| inventory-lock allow-list | 8192 | `MAX_ALLOWED_MACS` × 8 |

`_bss_headroom_` — what the linker leaves above the 2048-byte stack floor — was **2696 bytes** in a
default build and **2052** in the `BOARD.md` site build. An eighth cache block needs 4184, so it did
not fit, and the RAM section of `README.md` set out at length why taking the room from the allow-list
was the wrong trade: a cache miss costs one tag 140 ms instead of 15, is bounded and is counted
(`cev`/`cevu`), whereas one `NAK NO_SLOT` makes the S3 set `allowListInUse = false` and give up the
radio-side inventory lock until the link restarts, with a single `wsErr` line as notice.

That reasoning was sound and the conclusion was still wrong, because **both numbers are worst cases
and the fixed split charged every site for both.** A site with 13 tags paid 8192 bytes for a list
holding 104. A site with 1000 tags had no spare list room — and was also told it could never have a
1001st tag, because the same constant that reserved the memory also capped the list.

So the split follows the site now, and neither tenant has a size of its own.

## The scheme

### One arena, three tenants, every maximum derived

```
CACHE_ARENA_BYTES   one measured number: the SRAM the budget can spare
CACHE_BLOCKS        the cache's floor - the only policy choice left
max blocks        = CACHE_ARENA_BYTES / 4104          reached while the list is small
max list bytes    = CACHE_ARENA_BYTES - CACHE_BLOCKS × 4104
max list entries  = max list bytes / bytes per entry  reported, not configured
```

The allow-list grows upwards from the arena's low end; the cache's block buffers are taken from its
high end, one 4104-byte region each, numbered 0 downwards from the top. Regions `0 .. liveBlocks-1`
belong to the cache and everything below them is the list's. No tag count and no block count sizes
any memory: `CACHE_ARENA_BYTES` does, and it is a measurement of what is spare.

```
arena   0                                                                   CACHE_ARENA_BYTES
        |  allow-list  -->            free            <--  region 2 | region 1 | region 0 |
```

`MAX_ALLOWED_MACS` survives only as an optional **cap**, for a site that wants the list bounded below
what the arena allows. It sizes nothing — the measurements below show `.bss` is byte-identical with
it halved — which is the cure for a bug an earlier draft of this change had and merely documented:
when the arena was sized *from* it, lowering it cost a cache block instead of buying one.

### The cache surrenders a region, through its own policy

When the list would cross into the lowest live region, the cache gives that region up. Which region
the list needs is an address; which *block* should be lost is a policy decision, and they are not the
same block. `cacheSurrender()` therefore:

1. finds the slot that owns the region at the list's edge;
2. if that slot is empty, just drops the region — nothing is lost at all;
3. otherwise asks `cacheClaim()` for a victim, which is the existing eviction policy
   (`1585ab92`): a free slot if there is one, else the block whose tag is least likely to ask for it
   next. It never picks the pinned slot;
4. copies the edge region's contents into the victim's region and **swaps the two slots' region
   ownership**, leaving the victim slot holding the edge region and free;
5. decrements `liveBlocks`.

Slot *indices* never move, which is the reason for step 4 rather than the simpler "move the slot".
`ap.c` holds a slot index across an entire block burst (`burstSlot`, pinned with `cachePin`), and a
burst advances one frame per main-loop pass — the same passes that handle the S3's `ALLOW_ADD`
frames. An index that moved under a running burst would send another block's bytes to the tag,
correctly checksummed. With region ownership moving instead, `cacheBuffer(burstSlot)` simply returns
the new address and the burst finishes with the right data. The cost is one 4100-byte copy per region
surrendered.

A slot whose region is no longer live is *parked*: never in use, and `cacheClaim()` skips it. When
the list shrinks — `ALLOW_CLEAR`, or a tag deleted on the S3 — `cacheListReserve()` walks
`liveBlocks` back up and the parked slot is in play again, empty. This matters more than it looks:
the S3 re-sends the whole database after every `HELLO`, starting with `ALLOW_CLEAR`, so without the
growing-back half a link restart would leave the radio permanently poorer.

### Four bytes a tag

An allow-list entry is **one 32-bit word**: the MAC's low three bytes, and a one-byte index into a
32-entry table of its top five.

That works because of what a fleet's MACs look like. The top four bytes are the family — this
installation has `0000032C…`, `00000335…`, `0000021D…` and `0000BC9F…` — and the fifth is the
serial's high byte, constant until a family's serial numbering crosses 16.7 million. The S3 prints a
MAC `mac[7]` first (`ESP32_AP-Flasher/src/tag_db.cpp:122`), so the shared bytes are the **high** ones
and the varying ones are `mac[0..2]`.

The encoding is exact. Two MACs produce the same word only if they agree on both the index and the
low three bytes, and agreeing on the index means agreeing on the top five — so the word identifies
the whole MAC. `the_compressed_entry_identifies_a_whole_mac` is the test of that claim, and it is
there because the failure it would otherwise hide is the worst kind: a tag wrongly refused looks
exactly like a tag that has wandered off, and a tag wrongly accepted is the lock quietly not working.
It adds two MACs from different families that *share their low three bytes*, checks both are
answered, checks a third family is not, checks that changing only `mac[3]` is not, and removes one of
the pair to see that the other survives.

It costs nothing in alignment or indirection, which was the trap every earlier sketch fell into: the
index rides in the word's top byte, where three suffix bytes leave it free. There is no parallel
array to keep in step, no per-entry type tag, and no realignment — so the scan stays one aligned
32-bit load per entry.

**A MAC whose family is not in the table, once the table is full, is stored full width in a second
list** — eight bytes, exactly as before, with its own count and sitting above the narrow list's
reserved words. This is the graceful edge the whole design turns on. The one thing that must never
happen is a refusal, so a fleet of wholly unrelated MACs costs what it always cost and goes on
working; it does not fail, and it is not refused.
`a_mac_with_nowhere_in_the_prefix_table_is_stored_full_width` drives 40 one-tag families through it.

The narrow list's capacity moves in granules of 32 entries so that appending to it does not shift
the wide list behind it on every add: one `memmove` per 32 adds instead of one per add, at a cost of
at most 128 bytes of slack.

## The arithmetic

A region holds `struct blockData` and the block, 4 + 4096 = 4100 bytes, rounded to **4104** for an
8-byte stride. A burst sends 42 parts of 99 bytes, which cover 4158, and regions used to be that size
(4160 with the rounding) so that the last part could be copied straight out of the region. Its last
58 bytes are always 0xFF, though, so `cacheCopyPart()` now makes them up as the part goes out and
the region stops storing them: 56 bytes a block, 560 across ten, which is what moved ten blocks from
41600 bytes to 41040. `cacheBuffer()` is gone from the interface with it - there is no longer a
buffer a burst could index by part and read past the end of.

The 24-byte `struct cacheSlot` and its byte of `slotRegion` stay outside the arena, as fixed
`CACHE_BLOCKS_MAX`-long arrays: a slot has to keep its index while its data moves, and 25 bytes a
block is not worth making dynamic.

The default arena is **48792 bytes**: eleven regions and 3648 bytes beside them for the pending table
and the list (the pending table became the arena's third tenant after this section was first
written; see "The pending table moves in" below). From it:

- **max blocks** = 48792 / 4104 = **11**
- **leftover at the maximum** = 48792 − 11 × 4104 = **3648 bytes**, about 30 bytes a tag on a site
  whose every tag is listed and holds a pending entry
- **room above the floor** = 48792 − 7 × 4104 = **20064**
- **pending table, most it may hold** = (20064 − 8 × 1024) / 26, in steps of four = **456 tags**
- **allow-list, guaranteed** = (20064 − 456 × 26) / 8 = 1026, capped at `MAX_ALLOWED_MACS` = **1024**

### The bands

Measured against a host build of the shipping geometry (`CACHE_ARENA_BYTES=48792 CACHE_BLOCKS=7`,
default caps), adding tags one at a time as a real site has them - each on the allow-list and with a
pending entry, which every tag holds while it is in a sleep cycle:

| | one family (4 + 26 bytes a tag) | every tag its own family (8 + 26) |
| --- | --- | --- |
| 11 blocks, up to | **120 tags** | 108 tags |
| 10 blocks, up to | **256 tags** | 231 tags |
| 9 blocks, up to | 392 tags | 352 tags |
| 8 blocks, up to | 456 tags (the pending table's maximum) | 456 tags |

Past 456 a tag is still listed and answered, but `SET_PENDING` is refused with NO_SLOT, which the
S3 retries at the tag's next check-in; the allow-list is never the one refused below its cap. "One
family" is the right column for any real installation: the prefix table has 32 slots and this site
uses four.

For comparison, the ten-block arena with a fixed 250-entry table that this replaced: ten blocks to 320
tags of list and nine to 1344, but with 6500 bytes reserved for pending on every site and **no
service past 250 tags**. The trade is plain: a site under 120 tags gains a block, a site of 121 to
256 has what it had, and a site of 257 to 320 has nine blocks where it had ten - and, unlike before,
has content for every tag past the 250th. The nine-block arena before that (40504, 4160-byte regions)
held nine blocks to 736 / 399 tags of list alone.

### What more arena would buy, per byte freed elsewhere

The table below is the list-only model at the ten-block arena (42320), from before the pending
table moved in; it is kept because its reading - place arena just above a multiple of the stride,
not on one - still holds, now at about 30 bytes a tag instead of 4. `X` is bytes added to (or,
negative, taken from) 42320; the floor stays at 7. The tag figures are the compressed encoding
unless marked:

| `X` | arena | max blocks | leftover | top band, shared | top band, wide | next band, shared | capacity, wide / shared |
| --- | --- | --- | --- | --- | --- | --- | --- |
| −1816 | 40504 | 9 | 3568 | 864 | 462 | 1888 | 1488 / 2944 |
| −1280 | 41040 | 10 | 0 | **0** | 0 | 1024 | 1555 / 3072 |
| −1024 | 41296 | 10 | 256 | 64 | 48 | 1088 | 1587 / 3136 |
| 0 | 42320 | 10 | 1280 | **320** | 176 | 1344 | 1715 / 3392 |
| 256 | 42576 | 10 | 1536 | 384 | 208 | 1408 | 1747 / 3456 |
| 512 | 42832 | 10 | 1792 | 448 | 240 | 1472 | 1779 / 3520 |
| 1024 | 43344 | 10 | 2304 | 576 | 304 | 1600 | 1843 / 3648 |
| 2824 | 45144 | 11 | 0 | 0 | 0 | 1024 | 2068 / 4096 |

−1024 is `URX_RING=2048`, which the makefile pays for out of the arena so that it stays one flag.
−1816 is the old arena at the new stride, to show what the stride alone did. The rest of the
reading is unchanged from when this table was first drawn: **a block count alone is not the goal**
(41040 reaches ten and holds them only while the list is empty), what matters is the leftover, and
the encoding doubles every band in the table. An eleventh block is 2824 bytes away and would start
with no band at all.

`CACHE_BLOCKS`, the floor, is still the only real policy choice, because it alone sets the capacity:
each block of floor is 4104 bytes the list cannot have, which is 513 tags guaranteed or 1026
compressed. `.bss` does not move with it, because the floor sizes nothing.

### Where the bytes for the tenth came from

The tenth region and its band needed the arena 1816 bytes larger than it was, plus 25 bytes of slot.
Against the release build's headroom of 2384 that would have left 543, and the `BOARD.md` site build
with per-tag power on (headroom 1668) would not have linked. So 767 bytes were found first, none of
them a margin:

| | bytes | |
| --- | --- | --- |
| RAM code ended 16 bytes past a 256-byte boundary | 256 | `.data` starts at the RAM code rounded **up** to 256 (`boot_8258_ap.link`), so 2832 bytes of RAM code cost 3072. `irq_handler`'s three tests for the timer interrupts this firmware never uses became one masked write to each of the two write-1-to-clear registers, whose timer bits coincide (asserted); the RAM code is now 2812 bytes, 4 short of the boundary, and `_ramcode_tail_` reports it |
| the received frame's decode buffer | 261 | COBS decodes in place - the write pointer never passes the read pointer - so the frame is decoded over `rxCobs` instead of into a static `raw`. Unlike the decode/encode pair the backlog rejected, this adds no invariant: `raw` was already static, so a re-entered `v2RxBytes` would have clobbered a payload being handled before this change too |
| the pending table's checksum byte | 250 | `addCRC()` computes it over the reply; the stored copy was never read. 27 bytes an entry to 26. The backlog's objection was that 27 is the closed-source firmware's stride, which is evidence about that binary's lineage and nothing this firmware depends on |

Then the arena was placed so that the tightest build there is - site, per-tag power, every
instrument - keeps 464 bytes: more than the 256 that the next RAM code past the boundary would cost,
so that crossing it is still a link and not a failure, and room for a counter. The release build
keeps 1244. (The figures are after the buffer high-water marks, which took 64, and after the pending
table moved in, which gave 6500 to the arena and so cost nothing. Since `ed9d0142` there are only two
builds: a plain `make` is the old site build at 680, and `make AP_DEBUG=1` is the tightest at 632.)

What was looked at and left: the interrupt stack (512, deepest path 116 - the backlog's first priced
item, still wanting the high-water measurement), `-Os` on the interrupt handler (12 bytes, on the
hottest path there is), and holding the UART ring's indices in registers across the drain (bigger,
not smaller, on this compiler). The ADC code in RAM is the SDK's low-voltage guard on flash writes and
stays.

### The pending table moves in: the eleventh block

The pending table was the one large fixed allocation left: 250 entries of 26 bytes, 6500 bytes, the
same on a 13-tag site as on a 250-tag one, and a hard service ceiling at 250. Nothing about it needs
a fixed size. The S3 keeps one entry per tag (an idle "sleep n minutes" entry when there is nothing
to send), so its occupancy is the fleet - and the fleet is exactly what the arena is already sized
by.

So it is the arena's third tenant, at its low end, with the allow-list behind it:

```
arena   0                                                                          48792
        | pending table | allow-list -->        free           <-- region 2 | region 1 | region 0 |
```

- **It grows four entries (104 bytes) at a time**, when `findFreeSlot()` finds no free entry below
  the table's room; the list behind it is moved up by 104 bytes (`pendGrow`). The list is a few KB at
  most and this is once per four new tags, so the `memmove` is the cheap part. The room comes from
  `cacheListReserve()` like the list's own, so a block is surrendered through the eviction policy,
  with a pinned burst surviving it, exactly as for the list.
- **It gives the room back once a minute** (`pendShrink`), right after housekeeping has tightened
  `pendHigh` - the same pass that already finds the highest live entry - and the cache takes its block
  back with it.
- **Its maximum is derived, and leaves the list its cap.** An allow-list refusal makes the S3 drop
  the inventory lock until the link restarts; a pending refusal makes the S3 try again at the tag's
  next check-in. So the table may have every byte above the floor except `8 × MAX_ALLOWED_MACS`,
  which is the list at its cap in its widest encoding: **456 tags** at the default arena, against
  250 before. `MAX_PENDING_MACS` is an optional cap below that, as `MAX_ALLOWED_MACS` is for the
  list. HELLO_REPLY's `pendingSlots` and `pend` on the `cfg` line report the derived figure.
- **The table's address never moves**, because it is the arena's base: `pendingDataArr` is a pointer
  set once, and every index into it stays valid as the room behind it changes.

What it bought: the 6500 bytes became arena, less 25 for the eleventh slot, so **eleven blocks at no
cost in headroom** (the tightest build keeps the same 464). `the_pending_table_takes_room_as_tags_
arrive_and_gives_it_back` drives it in a geometry where the thresholds are reachable: a block taken
at the 157th entry, the list moved underneath it and still answering, refusal at exactly the derived
608 with the list at its cap, every entry still resolving, and the blocks back after housekeeping.

What it does not do yet: an idle entry still costs 26 bytes, 14 of them always zero (dataVer,
dataSize, dataTypeArgument - `prepareIdleReq()`). A two-width table like the allow-list's - idle
entries at 4 (compressed MAC) + 2 + 2 bytes, full entries only for tags with content - would take a
settled site's cost from 30 bytes a tag to 12.

## The lookup cost on the check-in path

With the lock on, every frame from a tag that is **not** on the list costs a full scan, and the reply
still has to leave inside that tag's window — 4000 µs for a check-in measured from the start of the
tag's frame, of which the frame's own airtime has already spent 1504. So the scan is on the clock.
From the disassembly (`out/OEPL_TLSR_AP.lst`), per entry that does not match:

| | instructions | loads | bytes read |
| --- | --- | --- | --- |
| `for (uint16_t c …) if (macs[c] == mac)` — before any of this | 10 | 2 | 8 |
| `for (uint32_t c …) if (w[2c] == lo && w[2c+1] == hi)` | 7 | 1 | 8 |
| `for (uint32_t c …) if (w[c] == key)` — the compressed form | **6** | **1** | **4** |

Both steps were worth having and neither was free to find. Two of the original ten instructions were
the `uint16_t` counter being truncated — a `tshftls`/`tshftrs` pair per iteration that does nothing
else — and GCC 4.5 loads *both* halves of a `uint64_t` before comparing either, where the explicit
low-half-first form skips the high half on the overwhelmingly common mismatch. The compressed form
then drops one more, because a one-word entry lets the compiler use a post-incrementing load
(`tloadm r3!`) and fold the pointer bump into it. At 1024 entries and 24 MHz the three forms are
roughly 500, 350 and 300 µs out of about 2500 µs of usable slack; at 48 MHz, 250, 175 and 150.

The prefix search is 32 slots, **once per lookup rather than once per entry** — about 7 µs — and it
pays for itself twice over: a tag from a family this radio has never heard of has no index in the
table, so the compressed scan is skipped entirely and only the (usually empty) wide list is walked.

A **binary search** was considered and rejected. It would take the scan to about 3 µs, but it needs
the list sorted and the list arrives in whatever order `tagDB` iterates. Sorting on insert is an
O(n) `memmove` per `ALLOW_ADD`, 30 times per request. Sorting lazily on first lookup puts an
O(n log n) pass of about 2 ms *on the check-in path* — the one place in this firmware where a stall
does not merely slow something down but destroys a reply. Sorting in the main loop behind a dirty
flag would work and is a separate change with its own failure mode. It is not needed for the memory,
so it is not in this one.

## How the S3 learns the cache size

It does not have to, and nothing in protocol v2 changes: same fields, same frames, no S3 file
touched. `cacheKb` in `HELLO_REPLY` carries `CACHE_BLOCKS_MAX × 4` — **the most the arena can ever
yield**, 40 KB by default — rather than the fixed `CACHE_BLOCKS × 4` of 28. `cacheUsedKb` in `STATUS`
is unchanged and still counts occupied slots only, so the S3's

```cpp
cacheFreeKb = (int32_t)cacheKb - st.cacheUsedKb;          // v2transport.cpp:537
```

reads as the maximum less what is in it, and never goes negative (occupancy cannot exceed the
maximum). Surrendered blocks are simply not subtracted, which leaves the S3's view optimistic by
design.

Optimistic is the right direction, and the asymmetry is the reverse of the allow-list's.
`V2Transport::wantPreload()` is all-or-nothing:

```cpp
const int32_t needKb = (adi.dataSize + V2_BLOCK_SIZE - 1) / V2_BLOCK_SIZE * (V2_BLOCK_SIZE / 1024);
if (needKb > cacheFreeKb) return false;                   // v2transport.cpp:361
```

- **Announcing low** switches preloading off for a whole image — not partially, at all. Its tag then
  fetches every block through `DATA_NEEDED` at roughly 140 ms of receiver-on time instead of 15. A
  three-colour image is ~23 KB, six blocks, so one understated block can cost a tag the better part
  of a second of radio per update, repeatedly, for as long as the site stands.
- **Announcing high** costs the S3 a flash read and 8–25 ms of link per block the cache then evicts.
  No tag's reply is delayed by it: chunk traffic is handled in the main loop and replies are
  scheduled by the radio's own state machine (`rf_start_stx`). `cevu` counts exactly this waste.

Priced in tag receiver-on milliseconds, the only currency that matters here, the two are not
comparable. There is also a quieter gain: `cacheFreeKb` is decremented by each preload, so a
six-block image preloads only while the free figure is at least 24 KB. Against the old 28 it needed
all but one block free; against 40 it tolerates four blocks of other content resident. The threshold
moves even on sites whose cache really is seven blocks.

And a surplus block is not wasted for being unannounced. The cache holds whatever it is given,
including blocks fetched on demand, so extra capacity still reduces evictions and re-fetches for the
second concurrent transfer — the case policy-aware eviction exists for.

### What a protocol change would have bought, had one been allowed

Recorded so it can be judged rather than rediscovered:

- **A `cacheKb` field in `STATUS`, or a re-`HELLO`**, would let the S3 track the true usable size as
  the list fills. The gain is only that `cevu` stops counting preloads the cache cannot hold on a
  large site — cheap S3 work, by the pricing above. Not worth a protocol change.
- **A partial-preload reply** — the S3 sending the blocks that *do* fit instead of giving up on the
  whole item — is the real prize, and it is an S3-side weakness that exists today, independent of
  this change. It is already in the backlog. It would make announcing low harmless, at which point
  the exact figure stops mattering at all.

## Failure analysis

The rule the design is built around: **the list is never made to wait, and the cache always gives
way.** `cacheListReserve()` is called before anything is written and surrenders blocks until the list
fits. `allowAdd()` refuses a MAC in exactly two cases: the optional `MAX_ALLOWED_MACS` cap, and the
arena genuinely having no bytes left with the cache already at its floor — the derived capacity,
which is 1040 tags for the fleet that compresses worst and 2048 for a normal one, against the 1024
hard ceiling the fixed split had. `the_whole_database_is_accepted_with_the_cache_full` plays the ordering a real site
produces — the S3 preloading while it is still sending the database, so every block the list needs
has something in it — with the *uncompressible* fleet, which is the widest list and the one that
reaches the floor.

What can go wrong, and what it costs:

| | effect | bound | visible as |
| --- | --- | --- | --- |
| a surrender evicts a block a tag still wanted | that tag's next request goes to the S3 | 140 ms instead of 15, once per block | `cev`, `cevu`, `dlate` |
| a surrender relocates the block under a running burst | one 4100-byte copy in the main loop | ~1 ms at 24 MHz, once per region surrendered | — |
| the narrow list's capacity bumps a granule | one `memmove` of the wide list | once per 32 adds, during bring-up | — |
| the prefix table fills | later families cost 8 bytes a tag instead of 4 | the bands halve; nothing fails | `tags` on the `cfg` line, and `cblk` |
| the S3 preloads into a cache smaller than announced | the chunk is refused or the block is evicted | one S3 flash read and 8–25 ms of link | `cevu` |
| `cacheBlocks()` falls to the floor | the cache is what it always was | 7 blocks | `cblk` on the `blk` line |
| the allow-list reaches the derived capacity | the S3 drops the radio-side lock until the link restarts | **unbounded** | one `wsErr` line |

Only the last row is dangerous, and it is further away than it was: 1040 for the fleet that
compresses worst and 2048 for a real one, against a hard 1024 before, and the figure is reported at
link start as `tags` next to `cb`, on the `cfg` line, so nobody has to deduce it. (They were on the
boot line until `cb` reached two digits and pushed `tags` past the LOG payload, silently - caught on
the first flash of the ten-block build.) Everything above it is bounded and counted, which is the side of the asymmetry the
cache is supposed to be on.

`cblk` on the `blk` diagnostics line is reported **only when the cache is below its maximum**, which
means the allow-list has actually taken a block. A site whose list is short pays no characters for
it. That is deliberate: this is the line that already runs closest to what a `LOG` payload holds, and
`diagAdd` abandons a field that will not fit in silence — which is exactly how the `rf` line came to
be lying about `udmaf` for weeks.

### A bug this change made possible, and the naming that prevents it

Worth recording in full, because it is the most instructive failure in the work and the fix is a
convention rather than a line of code.

When `CACHE_BLOCKS` stopped being *the* number of blocks and became the floor, every bound derived
from it had to be re-read. Two were missed — `cacheComplete()` and `cacheDrop()` kept
`slot < CACHE_BLOCKS` where they needed `slot < CACHE_BLOCKS_MAX`. So any slot above the floor
reported its block as incomplete:

- the chunks arrived and were stored, every byte correct;
- `cacheUsedKb` counted the slot as occupied, so `STATUS` looked right;
- `cev`/`cevu` stayed at zero, because nothing was being evicted;
- and every block request for those slots was answered `pleaseWaitMs` 140 and fetched again from the
  S3, forever.

A cache that holds the data, reports itself full, and serves nothing — with no counter anywhere
saying so. On a bench it reads as a slow S3; on a site it is every tag paying 140 ms a block for
nothing. The host tests caught it only because the split build has a floor of 2 and a maximum of 5,
so three of its five slots were above the floor; at the firmware's 7-and-8 it would have been one
slot in eight and might well have shipped.

The fix is that `CACHE_BLOCKS` is now aliased to `CACHE_BLOCKS_MIN` at the top of `blockcache.c` and
the bare name never appears again in that file. Every bound reads as `MIN` or `MAX`, which makes the
question "is this a floor or a slot index?" impossible to answer by accident — and a slot index is
always `MAX`, because slots exist for every region the arena can hold.

## Measurements

`make clean` between every build. `.text` and `.bss` from `tc32-elf-size`; `_bss_headroom_`,
`_stack_size_` and `_ramcode_tail_` from `tc32-elf-nm out/OEPL_TLSR_AP.elf`. Since `ed9d0142` every
behaviour option is gone and these are the only two builds; a plain `make` is what the `BOARD.md`
"site" flag set used to build, with the same `.bss` and headroom.

| configuration | blocks | `.text` | `.bss` | `_bss_headroom_` | `_stack_size_` |
| --- | --- | --- | --- | --- | --- |
| `make` (48792) | 7–11 | 37000 | 57368 | **680** | 2728 |
| `make AP_DEBUG=1` | 7–11 | 38312 | 57416 | **632** | 2680 |

`URX_RING=2048` (arena 47768) gives the same figures in both. `_ramcode_tail_` is 0 in both. `.text`
is not a budget here.

The earlier rows this section carried - the fixed split, the 37312 and 40504 arenas at 4160-byte
regions (`7bd679c7` and before), and the release, per-tag-power and sweep builds (before
`ed9d0142`) - are in the git history of this file and of the README; they are not repeated, because
the builds they measured no longer exist.

## Tests

`make -C test` — **45 cases**. The eviction-policy tests pin their geometry
(`CACHE_ARENA_BYTES=39368 CACHE_BLOCKS=6 CACHE_BLOCKS_CAP=6 MAX_PENDING_MACS=250`: six blocks, and
room above them for a 250-entry pending table beside a list at its cap), because they are about which block the
policy throws out when the cache is over-subscribed and they say so with exact slot counts.

The arena and the encoding get their own build, `libap_split.so`: an arena of 24592 bytes with a
floor of 2, so the cache starts at 5 and the list has 16384 bytes above the floor. The cache gives up
a block as the list passes **992 and 2016** entries compressed and **525, 1038 and 1551** full width
— both encodings' thresholds in one build, where the firmware's own arena reaches its lower ones only
past its cap. That is why the mechanism is driven here and not at the shipping geometry. (It was
24704 at 4160-byte regions, for the same 16384 above the floor; the full-width thresholds this
paragraph quoted then, 488/1008/1528, were 16 below what that build measured.) The six new cases:

- `the_allow_list_takes_cache_blocks_only_as_it_fills` — every threshold in both directions, the
  announced `cacheKb` holding still at the maximum, the cap being the only thing that refuses a MAC,
  and the blocks coming back on `ALLOW_CLEAR` and on single removals.
- `the_compressed_entry_identifies_a_whole_mac` — two families sharing their low three bytes, a third
  family that is not listed, a MAC differing only in `mac[3]`, and a removal that must not take the
  wrong entry with it.
- `a_mac_with_nowhere_in_the_prefix_table_is_stored_full_width` — 40 one-tag families, so the last
  eight have nowhere in the 32-slot table; all 40 answered, and removal works in both lists.
- `a_block_the_allow_list_takes_goes_through_the_eviction_policy` — the tag has had block 0 and is
  walking upwards, so block 0 is what the list is given, not the block at the list's edge; `cev 1
  cevu 0`, `cblk 4` on the line, absent before the surrender, and the line still inside a `LOG`
  payload with it.
- `a_burst_survives_the_block_under_it_being_moved` — `ALLOW_ADD`s arriving mid-burst while the
  pinned slot owns the edge region, checked on the burst's own 42 parts against the file. It also
  asserts `cacheEvictions() == 1`, so it cannot quietly stop exercising the relocating path.
- `the_whole_database_is_accepted_with_the_cache_full` — the full 2032-entry cap reached with the
  uncompressible fleet and every slot in use throughout, three blocks surrendered, nothing refused.

## Appendix: the refutation this document used to carry, and why it was wrong

An earlier draft recommended **against** the encoding, on this argument: a statically sized array must
be reserved for its worst case; the worst case of any prefix scheme is a fleet sharing no prefix; no
representation of such a MAC costs under 8 bytes; therefore `8 × MAX_ALLOWED_MACS` stays the
reservation and compression frees nothing.

Every step is true **of a static array**, and none of it is true of an arena, which is what the first
half of this change had already built. In an arena there is no reservation to defend. A fleet with
many prefixes simply uses more of the arena, and the cache surrenders a region through the eviction
policy — the mechanism already written, tested, and shown to survive a pinned burst. Nobody is
refused, nothing overflows, and the pathological fleet degrades to exactly the behaviour that shipped
before. So the encoding never had to be safe in the worst case. It only had to help in the typical
one, and it does: four bytes against eight, which doubles every band in the table above.

The draft also claimed the discriminator had to be per-entry, and priced five layouts at 9 or 10
bytes for the overflow case. That was the real error, and it is worth naming: **two counts and two
arrays discriminate for free.** A wide entry needs no tag byte because it is not in the narrow list,
and so it costs exactly the eight bytes it always did — which is how the worst case ends up no worse
than the status quo rather than 25 % over it.

It is kept here because the shape of the mistake is reusable: a worst case that must be *survived* is
not a worst case that must be *reserved*, and conflating the two is what a fixed-size allocation
trains you to do.
