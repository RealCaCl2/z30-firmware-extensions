"""Mappings recovered from the unmodified Z30 C 1.20 EG module."""
import binascii
import hashlib
import struct

NAME = 'z30'
MODEL = 'Nikon Z30'
FIRMWARE = 'C 1.20'
BASE = 0x10020000
STOCK_SHA256 = '8a99ced2d8220729ba067587b0624bf30c81757abc5d728768134b1c7fd457fa'
STOCK_SIZE = 33939514
CAPACITY = 0x2940000
SCATTER = (0x11F81C94, 0xC1800000, 0xF8258, 0x1002006C)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class StockImage:
    def __init__(self, data):
        self.data = bytes(data)
        if len(self.data) != STOCK_SIZE or sha256(self.data) != STOCK_SHA256:
            raise ValueError('Requires the unmodified Z30 C 1.20 EG module, not the outer Z_30 container')
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
        if BASE <= address and address + size <= BASE + len(self.data) - 2:
            return address
        raise ValueError('Address is outside stock payload or initialized data')

    def read(self, address, size):
        offset = self.rom(address, size) - BASE
        if offset < 0 or offset + size > len(self.data) - 2:
            raise ValueError('Read is outside stock payload')
        return self.data[offset:offset + size]

    def words(self, address, count):
        if count < 0:
            raise ValueError('Negative word count')
        return struct.unpack('<' + 'I' * count, self.read(address, count * 4))
