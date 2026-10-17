"""Decode the bounded WL-ASPECT.DBG diagnostic record, without editing it."""
import argparse
import json
import struct
from pathlib import Path

MAGIC, CAPACITY, STRIDE, HEADER = 0x31444157, 64, 160, 24


def decode(data):
    if len(data) != HEADER + CAPACITY * STRIDE:
        raise ValueError('Incorrect trace length (possibly a short write)')
    magic, version, counter, capacity, stride, freeze = struct.unpack_from('<6I', data)
    if (magic, capacity, stride) != (MAGIC, CAPACITY, STRIDE) or version not in (1,2):
        raise ValueError('Unknown trace format')
    result = {'version': version, 'counter': counter, 'capacity': capacity, 'records': []}
    for slot in range(capacity):
        raw = data[HEADER+slot*stride:HEADER+(slot+1)*stride]
        seq, event, profile, caller = struct.unpack_from('<4I', raw)
        if not seq or seq>counter:
            continue
        if version==2 and slot<7:
            if event!=2 or profile!=slot:continue
        else:
            ring=capacity if version==1 else capacity-7
            offset=0 if version==1 else 7
            if seq<max(1,counter-ring+1) or offset+(seq-1)%ring!=slot:continue
        record = {'sequence': seq, 'event': event, 'profile': profile, 'caller': hex(caller)}
        payload = raw[16:]
        if event == 1:
            area, size, pointer, pair, *registers = struct.unpack_from('<8I', payload)
            record.update(area=area, size=size, pointer=hex(pointer),
                          wh=[pair & 65535, pair >> 16], saved_r4_r7=[hex(v) for v in registers])
        elif event == 2:
            fields = struct.unpack_from('<8I', payload)
            record['capture'] = dict(zip(('kind', 'flag98', 'flag104', 'area208_word',
                                         'word224', 'word228', 'word22c', 'word4d4'), fields))
            config = payload[32:144]
            for name, offset in (('parent_wh', 0x22), ('origin_xy', 0x26),
                                 ('trim_xy', 0x38), ('trim_wh', 0x3c), ('output_wh', 0x56)):
                record[name] = list(struct.unpack_from('<2H', config, offset))
            record['config_hex'] = config.hex()
        else:
            record['unknown_event'] = True
        result['records'].append(record)
    result['records'].sort(key=lambda item: item['sequence'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trace', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    text = json.dumps(decode(args.trace.read_bytes()), indent=2)
    if args.output:
        args.output.write_text(text+'\n', encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
