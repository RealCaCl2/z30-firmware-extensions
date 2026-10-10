"""Compose feature edits against one stock source and a shared aligned tail."""
from .patch import Edit, apply_patch
from .platform import z30_120 as z30_platform


class Plan:
    def __init__(self, source, platform=None):
        self.platform = platform or z30_platform
        self.image = self.platform.StockImage(source)
        self.source = self.image.data
        self.tail = bytearray()
        self.edits = []
        self.features = {}

    @property
    def address(self):
        return self.platform.BASE + len(self.source) - 2 + len(self.tail)

    def alloc(self, data):
        address = self.address
        self.tail.extend(data)
        self.tail.extend(bytes((-len(data)) % 4))
        return address

    def put(self, address, data):
        offset = address - (self.platform.BASE + len(self.source) - 2)
        if offset < 0 or offset + len(data) > len(self.tail):
            raise ValueError('Write outside the owned code/data tail')
        self.tail[offset:offset + len(data)] = data

    def edit(self, address, replacement, name, expected=None):
        replacement = bytes(replacement)
        actual = self.image.read(address, len(replacement))
        if expected is not None and actual != expected:
            raise ValueError(f'Unexpected source instruction/data at {address:#x}')
        self.edits.append(Edit(self.image.rom(address, len(replacement)) - self.platform.BASE,
                               actual, replacement, name))

    def finish(self):
        output, report = apply_patch(self.source, self.edits, self.tail,
                                     expected_sha256=self.platform.STOCK_SHA256,
                                     base=self.platform.BASE,
                                     capacity=self.platform.CAPACITY)
        report.update(model=self.platform.MODEL, firmware=self.platform.FIRMWARE,
                      features=self.features, camera_verified=False,
                      status_page_included=False,
                      limitations=['This exact composed image has not been physically tested.',
                                   'CRC and offline instruction execution do not prove boot, display, audio or exposure.'])
        return output, report
