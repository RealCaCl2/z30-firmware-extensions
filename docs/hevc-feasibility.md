# Z30 C 1.20 HEVC feasibility — 2026-10-28

## Conclusion

No usable HEVC encoder or hidden HEVC recording mode was found. Existing evidence supports an AVC recording implementation. This is not proof that the silicon lacks HEVC circuitry. A menu or FourCC patch is not an implementation of HEVC.

## Evidence checked

- The repository stock Z_30_0120.bin was decoded with container.extract_eg, which enforces its pinned source SHA-256 and container/module checks. Its extracted EG matched the existing research EG byte for byte.
- EG size: 33,939,514 bytes. SHA-256: 8a99ced2d8220729ba067587b0624bf30c81757abc5d728768134b1c7fd457fa.
- Case-insensitive raw byte searches of EG, EX and TPJ modules found no ASCII or UTF-16LE matches for HEVC, H265, H.265, hvc1, hev1 or hvcC. This covers the decoded module bytes, not recursively decompressed undocumented payloads or code assembled constants.
- EG contains AH264Driver at 0x1206A408, H264DINT at 0x1206BC2C, H264EV1 at 0x102E94E0 and encoder/decoder H264 buffer event names. These are positive AVC implementation evidence; decoder events alone are not encoder evidence.
- The mux sample-entry pool at 0x10713048..0x10713153 contains avc1, avcC and AVC Coding, with no HEVC sample entry found by the searches above.
- A Thumb disassembly spot check of the known encoder configuration entry 0x102DFBFE confirms configuration writes including byte value 100 at +0x60 and level-like values at +0x61, consistent with the existing AVC analysis. This spot check does not exhaustively trace the lower driver, identify a codec selector, or prove all alternative paths absent.
- Existing mode-table and bitrate research is documented in bitrate-slowmo-uncap.md. It does not demonstrate HEVC support.
- Nikon official Z30 specifications list H.264/MPEG-4 AVC: https://onlinemanual.nikonimglib.com/z30/en/15-08.html

## Remaining uncertainty

The earlier sibling imaging research inferred hardware absence from negative firmware searches. That inference is too strong: stripped drivers, opaque accelerator payloads and undocumented registers require separate investigation. No silicon capability register or accelerator instruction-set evidence was established here.

Further research would need to trace the H264 driver dispatch into the accelerator interface, identify loaded microcode and capability checks, and obtain reliable hardware documentation or a proven compatible implementation. No known HEVC selector is available to trial. Even a demonstrated encoder requires HEVC parameter-set handling, mux configuration and recording lifecycle integration.

Adding software HEVC would be a separate encoder port with unmeasured performance; it is not a justified path to real-time 1080p/4K on current evidence. Changing AVC sample labels to HEVC would mislabel the bitstream.

The first pass was static. The deeper pass below also executes stock instructions offline. Neither pass modifies firmware or accesses a camera; no flashable patch or recording validation was performed.

## Deeper encoder investigation

### Recovered configuration-to-register path

| Address | Observed behavior |
| --- | --- |
| `0x102C9C28` | Stock caller of parameter constructor `0x102DFBFE` |
| `0x102DFBFE` | Allocates and initializes a `0x1A0`-byte encoder parameter object; it does not itself start the accelerator |
| `0xC1806E58` | Initialized scatter globals point at `EncodeParamKey` and `EncodeContextKey` |
| `0x102E080C` / `0x102E0722` | Context application path; `0x102E072E` calls parameter serialization |
| `0x102DFF5C` | Packs parameter fields into register values |
| `0x102E9CD2` | Branch veneer to register writer `0x102E93C4` |
| `0x102E93C4` | Loads `0x24070000` and writes `r1` at base + `r0` |
| `0x102E93CA` | Corresponding register read helper |

This distinguishes parameter construction, serialization, and physical hardware processing. The earlier shorthand "encoder open" must not be read as proof that the constructor actually encodes frames.

### Executed offline evidence

`tools/inspect_hevc_encoder.py` decodes the pinned stock container, maps initialized scatter data, and runs the actual constructor and serializer in Unicorn. Only the registry lookup and pool allocation calls at `0x102E9CC8` and `0x103967BC` are replaced with synthetic buffer returns. The arithmetic helper reached via ARM BLX executes as stock code. The MMIO window is ordinary mapped RAM, not an encoder model.

Four synthetic scenarios completed within the instruction bound with balanced stacks and the expected returned parameter pointer:

| Input dimensions / frame-rate code | Constructed profile byte | Constructed level byte | MMIO write count |
| --- | --- | --- | --- |
| 3840x2160 / 29 | 100 | 51 | 89 |
| 1920x1080 / 59 | 100 | 50 | 88 |
| 1920x1080 / 29 | 100 | 41 | 89 |
| 3840x2160 / 29, then overwrite profile byte with 1 | 100 before overwrite | 51 | 89 |

All scenarios deliberately use the same synthetic 144 Mbps bitrate arguments and explicit context defaults. These levels are outputs for those synthetic inputs, not measurements of actual camera modes.

Important dependencies:

- Constructor writes `100` to parameter `+0x60`. The serializer writes it to `0x24070E30`; the adjacent level byte is consumed by the same serializer.
- Changing only `+0x60` from `100` to `1` changes exactly one write in this trace: `0x24070E30`, from `0x64` to `0x1`. The serializer follows the same write sequence. This is a field-dependency experiment, not a supported codec selection or accepted hardware profile.
- Native helper `0x102E9DE8`, executed with level value `51`, returns `983040`. The serializer also computes a block count using width and height divided by 16 around `0x102E05CA`. Together with profile 100 and the AVC mux entries, this strongly supports an AVC-specific implementation. FFmpeg's own [H.264 level table](https://www.ffmpeg.org/doxygen/4.4/h264__levels_8c_source.html) identifies level 51 with that macroblock rate. It is not a silicon capability register.

The tool asserts these dependencies on each run; it does not merely print inferred labels.

### Capability reads and microcode boundary

The known read veneer `0x102E9CD6` has candidate direct Thumb callers at `0x102E06E4`, `0x102E9AE0`, `0x102E9B0A`, `0x102E9B34`, `0x102E9B5E`, `0x102E9B7E`, `0x102E9C7E`, `0x102E9C92`, and `0x1053F252`. Inspection shows handshake polling, read-modify-write controls of offset `0x4F0`, and reset/status polling at offset `0x4FC`. None of these inspected sites was established as an HEVC capability query. These callers do not enumerate indirect accessors or all other hardware interfaces.

Initialization entry `0x104E7DD2` installs a callback and invokes reset/control helpers. The inspected configuration/serialization execution does not load a selectable codec microcode image. That does not exclude an upload elsewhere during boot. A separate initialization path at `0x102DFA2C` references a 4 KiB ROM-to-RAM data copy and a 34-halfword table; their semantics remain unidentified and must not be labeled HEVC microcode on size alone.

### Assessment after the deeper pass

The evidence now reaches an actual MMIO serializer and AVC level arithmetic, beyond negative string searches. No HEVC selector, HEVC hardware capability result, HEVC microcode, or HEVC mux construction was recovered. Modifying the profile byte is not a demonstrated way to change codecs.

Unlocking HEVC through a small patch is therefore unsupported by the present evidence. A new HEVC implementation would first require independent identification of usable accelerator support, then a compatible driver and HEVC parameter-set/mux integration. Hardware absence remains unproven. Transplanting an EXPEED 7 or generic Socionext encoder cannot be justified without matching register ABI and actual silicon evidence.

Reproduction, with the project's verification dependency (`unicorn`) installed:

```powershell
python tools/inspect_hevc_encoder.py --source Z_30_0120.bin --output build/research/hevc-encoder/trace.json
```

The resulting JSON contains every traced MMIO write, scenario inputs, synthetic substitutions and limitations. It is a local diagnostic artifact, not a firmware image or real-device result.
