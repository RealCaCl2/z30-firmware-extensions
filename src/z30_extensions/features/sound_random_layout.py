# SPDX-License-Identifier: LicenseRef-Wonder-Lantern-Community
# Copyright (c) 2026 Wonder Lantern Z30 contributors
# See licenses/Wonder-Lantern.txt and THIRD_PARTY_NOTICES.md.
"""Four bounded PCM slots for the opt-in random sound preset.

Slot zero remains the original fixed-mode PCM buffer. Existing menu objects
and metadata keep their offsets; three additional slots follow them. PCM
must never be copied contiguously across the intervening menu/metadata gap.
"""
from .sound_layout import NEW_SIZE, MAX_DATA, PCM_OFFSET

RANDOM_INDEX = 5
RANDOM_META_PTR = 120
ACTIVE_PCM = 124
RANDOM_META_OFFSET = (NEW_SIZE + 3) & ~3
RANDOM_META_SIZE = 64
COUNT, USED, RNG, LAST = 0, 4, 8, 12
DESCRIPTORS = 16  # Four (PCM-relative offset, PCM byte length) pairs.
CANDIDATE = 48  # Existing metadata padding; committed to LAST on native start success.
RANDOM_SEED = 0x6D2B79F5

MAX_FILES = 4
PCM_CAPACITY = MAX_DATA * 12  # Per-file/fixed-mode capacity, still 1.08 s.
MAX_TOTAL_DATA = MAX_DATA * MAX_FILES
TOTAL_PCM_CAPACITY = PCM_CAPACITY * MAX_FILES
RANDOM_EXTRA_PCM_OFFSET = (RANDOM_META_OFFSET + RANDOM_META_SIZE + 31) & ~31
RANDOM_SLOT_GAP = RANDOM_EXTRA_PCM_OFFSET - (PCM_OFFSET + PCM_CAPACITY)
PCM_SLOT_OFFSETS = (0,) + tuple(
    RANDOM_EXTRA_PCM_OFFSET - PCM_OFFSET + i * PCM_CAPACITY
    for i in range(MAX_FILES - 1)
)
RANDOM_SIZE = RANDOM_EXTRA_PCM_OFFSET + (MAX_FILES - 1) * PCM_CAPACITY
