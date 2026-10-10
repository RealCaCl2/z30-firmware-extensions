"""Mappings recovered from the unmodified Z7 C 3.80 MAIN payload."""
import binascii
import hashlib
import struct

NAME = 'z7'
MODEL = 'Nikon Z7'
FIRMWARE = 'C 3.80'
BASE = 0x10020000
STOCK_SHA256 = '7f59d8993080203a12692cf6927a0a8218786d3f72230c04174f1563c506a6f6'
STOCK_SIZE = 33052090
MAIN_PAYLOAD_SHA256 = 'e6df1764323fb1c287159dd3ce1c84ac93c72b96ba6eb09c070295c093fecbff'
MAIN_PAYLOAD_SIZE = 33052088
CAPACITY = 0x2000000
SCATTER = (0x11E9C214, 0xA1800000, 0x101200, 0x1002006C)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class StockImage:
    def __init__(self, data):
        self.data = bytes(data)
        if len(self.data) != STOCK_SIZE or sha256(self.data) != STOCK_SHA256:
            raise ValueError('Requires the unmodified Z7 C 3.80 eg1610 module')
        if binascii.crc_hqx(self.data, 0):
            raise ValueError('Invalid stock module CRC')
        if self.words(BASE + 0x14000, 4) != SCATTER:
            raise ValueError('Unexpected initialized-data mapping')

    def rom(self, address, size=1):
        if size < 0:
            raise ValueError('Negative read size')
        source, destination, length, _ = SCATTER
        if destination <= address < destination + length:
            if address + size > destination + length:
                raise ValueError('Read crosses initialized-data mapping')
            return source + address - destination
        if BASE <= address and address + size <= BASE + len(self.data):
            return address
        raise ValueError('Address is outside stock payload or initialized data')

    def read(self, address, size):
        offset = self.rom(address, size) - BASE
        if offset < 0 or offset + size > len(self.data):
            raise ValueError('Read is outside stock payload')
        return self.data[offset:offset + size]

    def words(self, address, count):
        if count < 0:
            raise ValueError('Negative word count')
        return struct.unpack('<' + 'I' * count, self.read(address, count * 4))
