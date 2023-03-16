from __future__ import annotations

import enum
import math

import zigpy.types


class PackedUInt21(zigpy.types.uint_t, bits=21):  # type: ignore[call-arg]
    def serialize(self) -> bytes:
        n = int(self)
        chunks = []

        while n:
            # Set the least significant bit on all other octets.
            chunks.append((n & 0b01111111) | 0b10000000)
            n >>= 7

        # Clear the most significant bit of the most significant octet.
        chunks[-1] &= 0b01111111

        return bytes(chunks)

    @classmethod
    def deserialize(cls, data: bytes) -> tuple[PackedUInt21, bytes]:
        chunks = []

        for byte in data:
            chunks.append(byte & 0b01111111)

            if len(chunks) > math.ceil(cls._bits / 8):
                raise ValueError(
                    f"Packed integer cannot be larger than {cls.max_value}"
                )

            if byte & 0b10000000 == 0:
                break

        n = 0

        for chunk in chunks[::-1]:
            n = (n << 7) | chunk

        return cls(n), data[len(chunks) :]


class CommandID(PackedUInt21, enum.Enum):
    NOOP = 0
    RESET = 1
    PROP_VALUE_GET = 2
    PROP_VALUE_SET = 3
    PROP_VALUE_INSERT = 4
    PROP_VALUE_REMOVE = 5
    PROP_VALUE_IS = 6
    PROP_VALUE_INSERTED = 7
    PROP_VALUE_REMOVED = 8

    PEEK = 18
    PEEK_RET = 19
    POKE = 20
    PROP_VALUE_MULTI_GET = 21
    PROP_VALUE_MULTI_SET = 22
    PROP_VALUES_ARE = 23

    NET_SAVE = 9
    NET_CLEAR = 10
    NET_RECALL = 11

    HBO_OFFLOAD = 12
    HBO_RECLAIM = 13
    HBO_DROP = 14
    HBO_OFFLOADED = 15
    HBO_RECLAIMED = 16
    HBO_DROPPED = 17


class PropertyID(PackedUInt21, enum.Enum):
    # Core Properties
    LAST_STATUS = 0
    PROTOCOL_VERSION = 1
    NCP_VERSION = 2
    INTERFACE_TYPE = 3
    INTERFACE_VENDOR_ID = 4
    CAPS = 5
    INTERFACE_COUNT = 6
    POWER_STATE = 7
    HWADDR = 8
    LOCK = 9

    # Host Buffer Offload
    HBO_MEM_MAX = 10
    HBO_BLOCK_MAX = 11

    # Stream Properties
    STREAM_DEBUG = 112
    STREAM_RAW = 113
    STREAM_NET = 114
    # STREAM_NET_INSECURE = 114  # has the same ID as `STREAM_NET`?

    # PHY Properties
    PHY_ENABLED = 32
    PHY_CHAN = 33
    PHY_CHAN_SUPPORTED = 34
    PHY_FREQ = 35
    PHY_CCA_THRESHOLD = 36
    PHY_TX_POWER = 37
    PHY_RSSI = 38
    PHY_RX_SENSITIVITY = 39

    # MAC Properties
    MAC_SCAN_STATE = 38
    MAC_SCAN_MASK = 49
    MAC_SCAN_PERIOD = 50
    MAC_SCAN_BEACON = 51
    MAC_15_4_LADDR = 52
    MAC_15_4_SADDR = 53
    MAC_15_4_PANID = 54
    MAC_RAW_STREAM_ENABLED = 55
    MAC_PROMISCUOUS_MODE = 56
    MAC_ENERGY_SCAN_RESULT = 57
    MAC_WHITELIST = 4864
    MAC_WHITELIST_ENABLED = 4865

    # NET Properties
    NET_SAVED = 64
    NET_IF_UP = 65
    NET_STACK_UP = 66
    NET_ROLE = 67
    NET_NETWORK_NAME = 68
    NET_XPANID = 69
    NET_MASTER_KEY = 70
    NET_KEY_SEQUENCE_COUNTER = 71
    NET_PARTITION_ID = 72
    NET_REQUIRE_JOIN_EXISTING = 73
    NET_KEY_SWITCH_GUARDTIME = 74
    NET_PSKC = 75

    # IPv6 Properties
    IPV6_LL_ADDR = 96
    IPV6_ML_ADDR = 97
    IPV6_ML_PREFIX = 98
    IPV6_ADDRESS_TABLE = 99
    IPV6_ICMP_PING_OFFLOAD = 101

    # Debug Properties
    DEBUG_TEST_ASSERT = 16384
    DEBUG_NCP_LOG_LEVEL = 16385

    # Thread Properties
    THREAD_LEADER_ADDR = 80
    THREAD_PARENT = 81
    THREAD_CHILD_TABLE = 82
    THREAD_LEADER_RID = 83
    THREAD_LEADER_WEIGHT = 84
    THREAD_LOCAL_LEADER_WEIGHT = 85
    THREAD_NETWORK_DATA = 86
    THREAD_NETWORK_DATA_VERSION = 87
    THREAD_STABLE_NETWORK_DATA = 88
    THREAD_STABLE_NETWORK_DATA_VERSION = 89
    THREAD_ON_MESH_NETS = 90
    THREAD_LOCAL_ROUTES = 91
    THREAD_ASSISTING_PORTS = 92
    THREAD_ALLOW_LOCAL_NET_DATA_CHANGE = 93
    THREAD_MODE = 94
    THREAD_CHILD_TIMEOUT = 5376
    THREAD_RLOC16 = 5377
    THREAD_ROUTER_UPGRADE_THRESHOLD = 5378
    THREAD_CONTEXT_REUSE_DELAY = 5379
    THREAD_NETWORK_ID_TIMEOUT = 5380
    THREAD_ACTIVE_ROUTER_IDS = 5381
    THREAD_RLOC16_DEBUG_PASSTHRU = 5382
    THREAD_ROUTER_ROLE_ENABLED = 5383
    THREAD_ROUTER_DOWNGRADE_THRESHOLD = 5384
    THREAD_ROUTER_SELECTION_JITTER = 5385
    THREAD_PREFERRED_ROUTER_ID = 5386
    THREAD_NEIGHBOR_TABLE = 5387
    THREAD_CHILD_COUNT_MAX = 5388
    THREAD_LEADER_NETWORK_DATA = 5389
    THREAD_STABLE_LEADER_NETWORK_DATA = 5390
    THREAD_JOINERS = 5391
    THREAD_COMMISSIONER_ENABLED = 5392
    THREAD_BA_PROXY_ENABLED = 5393
    THREAD_BA_PROXY_STREAM = 5394
    THREAD_DISOVERY_SCAN_JOINER_FLAG = 5395
    THREAD_DISCOVERY_SCAN_ENABLE_FILTERING = 5396
    THREAD_DISCOVERY_SCAN_PANID = 5397
    THREAD_STEERING_DATA = 5398

    # Jam detection
    JAM_DETECT_ENABLE = 4608
    JAM_DETECTED = 4609
    JAM_DETECT_RSSI_THRESHOLD = 4610
    JAM_DETECT_WINDOW = 4611
    JAM_DETECT_BUSY = 4612
    JAM_DETECT_HISTORY_BITMAP = 4613

    # GPIO
    GPIO_CONFIG = 4096
    GPIO_STATE = 4098
    GPIO_STATE_SET = 4099
    GPIO_STATE_CLEAR = 4100

    # True random number generation
    TRNG_32 = 4101
    TRNG_128 = 4102
    TRNG_RAW_32 = 4103


class ResetReason(zigpy.types.enum8):
    PLATFORM = 1
    STACK = 2
    BOOTLOADER = 3


class HDLCSpecial(enum.IntEnum):
    FLAG = 0x7E
    ESCAPE = 0x7D
    XON = 0x11
    XOFF = 0x13
    VENDOR = 0xF8
