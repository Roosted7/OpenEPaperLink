# Working with a live access point

House rules for anyone — person or agent — given access to a running OpenEPaperLink AP to develop or
test this radio firmware. `tools/ap.sh` does the common operations; read this first.

The AP used while this firmware was written is an **OpenEPaperLink Nano TLSR**: an ESP32-S3 plus a
TLSR8258 radio, serving 14 e-paper tags on 802.15.4 channel 26. It is **somebody's home
installation**, not a lab rig. Most of what follows exists because something was broken once.

## The board is leased, not shared

The board has **one owner at a time**. Two parties flashing, rebooting or sweeping registers at once
produces failures that look like firmware bugs and are not.

- **Ask before you touch it**, and wait to be told it is yours.
- **Hold it only while you are using it.** If you are going to spend a while reading code, building,
  thinking or writing a report, **release it** and say so — then ask again when you next need it. A
  lease held through an hour of analysis blocks everyone for an hour.
- **Say what state you leave it in**: which firmware is on the radio, whether the link is up, and
  anything you changed that is not in a commit.
- If you find it in a broken state you did not cause, say so rather than quietly fixing it — the way
  it broke is usually the interesting part.

## Never

- **Never run two things that talk to the radio at the same time.** An SWS flash of the radio and an
  OTA of the S3 overlapping once left the radio with a half-written image (`ap_version 0x0`). Flash,
  wait, check, then do the next thing.
- **Never `POST /imgupload` to a tag that is not on the cleared list below** - which is empty. It
  overwrites that tag's `contentMode` and `modeConfigJson` wholesale, and the configuration — RSS
  feeds, a calendar URL, a weather location, an energy tariff — is hand-made. It has overwritten
  tags' configuration twice: four tags once, and the three then cleared for testing again on
  2026-11-01. Each time it had to be restored from a backup (see "Before you change stored state").
- **Never write flash `0x77000`** (the crystal-trim byte) without being told to. It is persistent
  configuration on someone's hardware. Apply a trim at runtime or in a build instead, and report the
  value you would burn.
- **Never kill the loggers.** A serial reader and a web-log reader may be running and appending to
  files; other work depends on that history. Read the files.
- **Never open `/dev/ttyACM0` casually.** That is the S3's USB-JTAG port, and opening or closing it
  toggles DTR/RTS, which **resets the S3**. The radio is then still at 2 Mbaud while the S3 probes at
  115200. Use the web log instead. If you must attach, deassert DTR and RTS *before* `open()` and clear
  `hupcl`, and then stay attached rather than reconnecting.
- **Never leave the AP unable to serve tags.** If your last flash does not come online, flash a known
  image back. The SWS flasher halts the radio's CPU, so a bad radio image is always recoverable.

## Cleared for test content

**None.** Until 2026-11-01 three tags were - ePaperBlack3 `0000032C91C23E17`, ePaperBlack2
`0000032C73D23E1A` and ePaperBlack7 `0000033467B43E16`, hwType 4, 3-colour, 23104-byte raw image -
because their configuration was thought lost. It was in the backups, the owner wanted it back, and
it is restored: they are ordinary tags now, and `tools/ap.sh testimg` refuses every tag. Ask the owner
before giving any tag test content.

What they were for: being 3-colour with a large raw image, a badly compressing picture on these
produces ~14 KB of payload: four blocks, three of them the full 42 parts.
**It does not, measured 2026-11-01:** the S3 preloads all four blocks, the tag is offered the image,
and it reports the transfer complete without requesting a single block (`data 1 xfc 1 brq 0`). The
nine-block image `99d152aa` does exactly the same, so it is the tag or the S3, not the radio - and no
42-part burst appears anywhere in this installation's logged history. Until that is understood the
full-length burst is exercised by the host tests only. Everything else on the AP keeps its
configuration.

`POST /tag_cmd cmd=refresh` is safe on **any** tag — it redraws the content that tag is already
configured for, which it would do anyway.

## You cannot wake a tag

Tags sleep and decide for themselves when to check in; the AP only answers. Queuing content does not
summon a tag, and neither does `cmd=reboot` (that is itself delivered at the next check-in). So:

- Expect to wait, and more than it looks. Measured on this fleet: **one check-in every 34 seconds
  across all 14 tags**, so an individual tag comes back roughly **every 6 minutes** — not every minute,
  whatever the `nextcheckin` spread suggests. Budget from that number, not from the configuration.
- Queue the thing you want measured on **several** tags so the next arrival is informative.
- Do not sit in a polling loop. Queue, go and do something else, come back and read the log.
- Some things simply cannot be reproduced here in reasonable time, and it is better to say so than to
  burn hours. Worked example: a burst is ~257 ms, and with one check-in every 34 s the chance that any
  given burst collides with one is under 1 %, so seeing a single naturally-occurring `late` would take
  on the order of a hundred bursts — hours. If what you need is to prove the *plumbing* rather than the
  contention, inject the condition in a debug build and say plainly in the report that it was
  synthetic.
- Per-sample measurements are noisy. The decoded frequency offset from one tag read 389 and 63 at the
  same setting. Anything built on a handful of samples is noise; bin and average, or do not claim it.

## Diagnostics

The radio's `LOG` frames reach the AP's **web log** intact, over a CRC-checked link. The S3's USB
console mangles them — other tasks print into the middle of a line — so prefer the web log.

`tools/ap.sh log` reads it; `tools/ap.sh watch` follows it. Lines look like:

```
radio: ci 1 data 0 rep 0 us 1205 max 1205 gap 1220 late 0 sile 0 sils 0
radio: xfer brq 1 bst 1 ebst 1 xfc 1 rereq 0 rparts 0 reack 0 cut 0 cxl 0 dl 0 dlx 0 dlate 0 dw 140 evd 0 cpy 2 png 0
radio: air yld 1 ylf 1 yto 0 fh 52 fe 55 oth 9 btx 226 hrm 0 dn 0 dx 551
radio: rf rx 2 crc 0 tx 2 txto 0 drop 0 bdrop 0 unh 0 tsfb 0 busy 73 nf 1A6300000000 udma 91 udmaf 0
```

A release build only logs because the S3 asks it to; `AP_DEBUG=1` logs regardless, and that is the
one switch that compiles any instrument in: the per-ping and per-join lines, the per-check-in `fo`
line and the `nf` histogram come with it. Nothing below is in a release image unless it
says otherwise.

## What the counters mean when you are judging a change

| | |
| --- | --- |
| `rep` | check-ins a tag had to repeat — it missed our reply. The reply-timing metric. |
| `gap` | request end to our reply going on air, in µs. Should equal `RADIO_REPLY_AT_US` (1220, `src/hal.h`). |
| `late` | requests dropped as too old to answer. Part of the harm the repeat budget weighs, but the small part: a frame that arrives while the radio transmits is never received at all, so it never counts here - which is why `late` read 0 in every window ever logged. |
| `rereq` / `rparts` | re-requests after a burst, and how many parts. Whether repeats are earning their airtime. |
| `cut` | bursts stopped early because the tag already had the block. |
| `cpy` | repeat copies per part the loop has settled on. |
| `air` line | sent in the report's one extra slot when the boot facts and the hw/stk lines leave it free and a burst has gone out since the last one; its counters run from one `air` line to the next, so a report it yields loses nothing. |
| `yld` / `ylf` / `yto` | burst parts held back because the receiver was in the middle of a frame; of those holds, the ones after which a good frame really was received; and holds that outlasted any frame (the flag is then not trusted for the rest of that burst). `ylf` is what says a `yld` was a tag: a hold with no frame after it is a frame that failed its CRC - or a flag that is up for no frame. |
| `fh` / `fe` | the receive flag (0x448 bit 5) seen up in one look per millisecond of listening, bursts or not, against what the frames received predict, those that failed their CRC included (`crc`): each holds it from sync to its last byte, (len + 3) x 32 us, so `fe` is the sum of that in ms. Since boot, halved together at 9999 - a site whose tags check in every few hours still builds up a comparison. Equal: the flag is up for whole frames. `fh` well below `fe`: up for part of a frame (or looks lost to long main-loop passes). `fh` 0 with `fe` rising: dead, and `yld` can never fire. `fh` far above `fe`: stuck. The first run with this read `fh` ~16% above a `fe` of good frames only - about 130 us, 4 bytes, a frame - which is either the flag rising in the preamble rather than after the SFD, or about one frame in eight failing its CRC; with failed frames now in `fe`, the excess staying says the first, going says the second. It replaced a 10 ms share that saw one frame in ten - 1-4 looks against 1-6 expected, which could not tell any of those apart. |
| `sile` / `sils` (ci line) | bursts after which the tag went quiet, by the timing the burst had: early (a known ZBS243) and safe (everything else). Read from the tag's next frame, so no timeout is guessed: XFC, a request for the next block or for missed parts is an answer; a check-in or a ping first means it gave up on the transfer and only came back on its next cycle, a whole check-in interval later - which nothing else counts, because no request round happened for `rereq` to see. Compare `sile` with `ebst` and `sils` with `bst - ebst`, summed over a long log. The web log before this counter had 11 such bursts in 127, all early (11 of 103 against 0 of 35). |
| `crc` (rf line) | frames received that failed their CRC or length check, since boot like `rx`. The receive interrupt drops them before anything sees them, so until this counter they were invisible: how much of what the radio hears arrives corrupt - a weak or distant tag, WiFi landing on a frame, two tags at once. Their time is in `fe` as well (they hold the flag up like any frame). |
| `bq` (blk line) | block requests that arrived while another tag's burst was due or on air, acked with a wait that ends as that burst does and served after it. `cxlb` is now only a third tag at that moment. Before, any tag asking within 1.2 s of another's block request was cancelled: 7 of ~110 transfers on this site, each 35-794 ms after the other burst had ended, each costing the tag a whole check-in cycle - 40-110 s here, and as long as the configured check-in interval anywhere, which can be hours. |
| `oth` / `btx` / `hrm` | frames from tags other than the one the bursts went to, the burst airtime in ms, and the other tags' attempts that airtime is estimated to have gone over: `oth` per ms of listening, times `btx`. `hrm` (plus `late`) at 5 ms each, against `rereq` at 300 ms each, is what moves `cpy`. |
| `dn` / `dx` | the learned time, from the end of a burst's first pass, for a tag that leaves when complete (ZBS243) to say so: by asking for the next block (`dn`), or with XFC after the last (`dx`). 0 = not seen yet. A burst waits that long only if a repeat still fits in the window after it, which `dx` never allows - a ZBS243 sends XFC 0.5-1.5 s after the last block - so `dx` is a measurement, and only `dn` ever pauses a burst. Only learned from tags the radio knows as ZBS243 from a full check-in (`ebst` counts their bursts). |
| `busy` | share of idle samples that found something on the channel (above -80 dBm). The only noise number anything decides on: it holds the burst repeat budget down through `BURST_BUSY_HOLD_PCT`. |
| `nf` | instrumented builds, and **not** the noise floor - it is the whole distribution. Eight hex digits, one per 8 dB bucket, lowest first: bucket 0 is -110..-103 dBm, bucket 7 is everything above -47. Each digit is sixteenths of the window's samples, except that a bucket with any sample in it reads at least 1 rather than 0 - so `0` means never, `F` means all of it, and a brief strong burst is visible instead of rounding away. Read the floor off the low digits and the occupancy off the high ones. It replaced a single wandering median that was read as the noise floor swinging 23 dB; that number was the estimator wandering between two modes of a bimodal channel, not the channel. |
| `dw` | the wait the radio is currently promising for an uncached block. |
| `bdrop` | frames lost because the next was already arriving. Should be 0, and can only rise if something holds interrupts off for ~200 µs — two 802.15.4 frames cannot be closer than that. |
| `hw` | a line of its own, sent only when one of its marks has risen: the most each sized buffer has held since boot. `urx` UART ring bytes (of `URX_RING`), `lp` longest pass-to-pass gap in µs since the link came up (the link bring-up itself is ~21 ms at every boot and is left out), `in P US` the part of `apLoop` that took longest (0 between passes, 1 a radio frame, 2 the S3's requests, 3 burst start, 4 burst frame, 5 events, 6 reports) and its time, `rq T US` the slowest S3 request by type, `rxq` radio slots (of 8), `evq`/`big` event cells (of 24) and side buffers (of 3), `logq`/`lgd` LOG lines queued (of 6) and lines a full queue dropped, `pend` the pending table's peak (of 250). Production builds carry it: it is what a site needs to judge its own sizing. First measured 2026-11-01, below. |
| `fo`, `hi` | `AP_DEBUG` builds only: the decoded 11-bit frequency offset (a relative number with no settled unit or sign — do not key anything on it) and the raw high byte of that field. `hi` should have bits 7 and 3 clear — both nibbles in 0…7, so `hi & 88` is `00` — because that byte is `{0, chn_efuse[2:0], 0, fdc[10:8]}` and Telink documents both of those bits as zero. Anything else means the trailer is not being read where we think it is. |

`late` is 0 in this entire installation's history, in every build — so anything keyed on it cannot be
tested here, and the host tests (`make -C test`) have to carry it. **`rep` is not**: counted over the
whole log it runs 4–17 % of check-ins, so that one does carry signal. Do not repeat the mistake that
produced the first version of this paragraph — it said both were 0, from reading a recent tail of the
log instead of all of it. Count over the whole file.

Two build traps that have each cost a measurement here:

- **`make` does not track flag changes.** Changing a `-D` on the command line does not rebuild the
  objects that depend on it, so a "new" build can be byte-identical to the old one and silently prove
  nothing. Always `make clean` when switching `AP_DEBUG` or a size.
- **`make` does not depend on `../../../oepl-radio-v2.h`.** Editing the shared protocol header relinks
  without recompiling, which produces a stale binary with no warning.

## Build, flash, check

```bash
cd ARM_Tag_FW/OpenEPaperLink_TLSR_AP
make clean && make   # the image: since ed9d0142 every behaviour is fixed and there is nothing to pick
# ... or make clean && make AP_DEBUG=1 while a failure is open: that is every instrument, the only
# way to get any of them, and nothing that goes on the air changes
make -C test          # must stay green before anything is flashed (45 of 45; it builds both
                      # AP_DEBUG states, because the instrument branches only compile in one)
tools/ap.sh flash-radio out/OEPL_TLSR_AP.bin
tools/ap.sh health
```

Working in a **git worktree** (which is how parallel work on this firmware is done): a worktree does
not get submodule contents, so the tc32 toolchain is not there. The makefile falls back to the copy in
the main checkout, and says what to do if it cannot find one at all.

RAM is the binding constraint, but it is a budget now rather than a cliff: the linker holds 2048 bytes
back for the stack and reports what is left over as `_bss_headroom_`, which is 1248 bytes in the build
above and 1208 with `AP_DEBUG=1`.
Report `.bss`, `_bss_headroom_` and `_ramcode_tail_` (`tc32-elf-nm out/OEPL_TLSR_AP.elf`) with any
change that adds state or RAM code, and read the RAM section of `README.md` before taking bytes from
`CACHE_ARENA_BYTES`, `MAX_ALLOWED_MACS` or `MAX_PENDING_MACS` — the three are not interchangeable and
the reasoning for each is written down there.

The cache, the pending table and the allow-list share one arena, so the cache is eleven blocks while
the site is small and gives one back each time the other two need the room: for a fleet whose every
tag is listed and has something pending, eleven up to 120 tags, ten up to 256, nine up to 392, and
the pending table holds up to 456 (it was a fixed 250). `cfg ... cb 11 tags 1024 pend 456` says so at
link start, and `cblk` on the `blk` line says how many blocks the cache has whenever that is below
eleven. The `CACHE_BLOCKS=6` this
command once carried, to fix a link failure that was really four bytes of stack rounding, is the
cautionary tale in the README's RAM section.

## Before you change stored state

Take a backup and keep it off the device — `tools/ap.sh backup` writes the tag database, the device's
own older copy of it, and the AP configuration into `oepl-backups/<timestamp>/` in the main checkout
(beside its `.git`, so every worktree writes to the same folder; `BACKUPS=<dir>` overrides it).

**Never commit a tag database.** It holds the owner's own configuration: feed URLs, a calendar
apps-script URL, locations, tariffs. That is why `/oepl-backups/` is listed in `.git/info/exclude`
(local to this clone, and shared by its worktrees) - a fresh clone needs that line added before its
first backup - and why the one thing worth doing before any session on a live AP is running that command
once. Restoring by hand took the owner real time after `/imgupload` overwrote four tags' content
configuration. Do it before anything that touches tag records, and read what the
endpoint actually does in `ESP32_AP-Flasher/src/` first: several of them rewrite more than their name
suggests.
