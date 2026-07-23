# Universal Silicon Labs Flasher
Automatically communicates with radios over CPC, EZSP, or Spinel to enter the bootloader and then flashes a firmware image with XMODEM.

## Installation
```console
$ pip install universal-silabs-flasher
```

## Usage

```console
usage: universal-silabs-flasher [-h] [-v] [--device DEVICE] [--probe-methods PROBE_METHODS]
                                [--bootloader-reset BOOTLOADER_RESET]
                                {dump-gbl-metadata,probe,write-ieee,flash,erase-nvm3} ...

positional arguments:
  {dump-gbl-metadata,probe,write-ieee,flash,erase-nvm3}

options:
  -h, --help            show this help message and exit
  -v, --verbose
  --device DEVICE
  --probe-methods PROBE_METHODS
                        Comma-separated list of application type and baudrate pairs to use when probing the device.
                        Each pair should be in the format '<application_type>:<baudrate>'. Valid application types:
                        bootloader, cpc, ezsp, spinel, router. Example: 'ezsp:115200,ezsp:460800,spinel:460800'
  --bootloader-reset BOOTLOADER_RESET
                         Reset methods to attempt when triggering bootloader mode. Multiple methods can be chained by
                         separating them with a comma. Valid values: yellow, ihost, slzb07, rts_dtr, baudrate
```

For `flash`, you can also pass `--profile` to use a predefined device profile.
`--profile` cannot be combined with `--probe-methods` or `--bootloader-reset`.

## Flashing firmware
For safety, firmware GBL image files are validated and their checksums verified both before sending, and by the device bootloader itself.

### Yellow
The Yellow's bootloader can always be activated with the `--bootloader-reset yellow` option:

```bash
$ universal-silabs-flasher \
    --device /dev/ttyAMA1 \
    --bootloader-reset yellow \
    flash \
    --firmware NabuCasa_RCP_v4.1.3_rcp-uart-hw-802154_230400.gbl
```

### SkyConnect
The SkyConnect will be rebooted into its bootloader from the running application: either EmberZNet or CPC.

```bash
$ universal-silabs-flasher \
    --device /dev/cu.SLAB_USBtoUART \
    flash \
    --firmware NabuCasa_SkyConnect_EZSP_v7.1.3.0_ncp-uart-hw_115200.gbl
```

### Home Assistant Connect ZBT-2
The Home Assistant Connect ZBT-2 can use the built-in `zbt2` profile:

```bash
$ universal-silabs-flasher \
    --device /dev/ttyACM0 \
    flash \
    --profile zbt2 \
    --firmware zbt2_openthread_rcp_2.4.4.0_GitHub-7074a43e4_gsdk_4.4.4.gbl
```

### Sonoff ZBDongle-E
The Sonoff dongles use the RTS/DTR bootloader reset method:

```bash
$ universal-silabs-flasher \
    --device /dev/ttyUSB0 \
    --bootloader-reset rts_dtr \
    flash \
    --firmware ncp-uart-hw-v7.4.5.0-zbdonglee-115200.gbl
```


## Erasing NVM3
Erase the NVM3 token store (network settings, keys, frame counters) via the Gecko
bootloader. Because this works at the bootloader level, it can recover a device
whose application firmware no longer boots — for example after a firmware
downgrade left an incompatible NVM3 format behind:

```bash
$ universal-silabs-flasher \
    --device /dev/cu.SLAB_USBtoUART \
    --bootloader-reset rts_dtr \
    erase-nvm3 \
    --address 0x08176000 \
    --size 40960
```

NVM3 sits at the top of main flash, so `--address` is
`flash base + flash size - NVM3 size`. The size is firmware-specific (the SDK
default is `40960`; `32768` is also common) and must be a multiple of the flash
page size. Per-chip geometry is documented in
[Nerivec/silabs-firmware-recovery](https://github.com/Nerivec/silabs-firmware-recovery),
whose published recovery images this command replicates by generating an
equivalent erase GBL locally.

**This is destructive**: the device loses its network and must be re-joined or
re-commissioned afterwards.

## Writing IEEE address
Ensure a target device running EmberZNet firmware has the correct node IEEE address:

```bash
$ universal-silabs-flasher \
    --device /dev/cu.SLAB_USBtoUART \
    write-ieee \
    --ieee 00:3c:84:ff:fe:92:bb:2c
```

The IEEE address can also be specified without colons: `--ieee 003c84fffe92bb2c`.

If the current device's IEEE address already matches the provided one, the command will not write it unnecessarily.
Depending on firmware version, writing the IEEE address can be a **permanent** operation. If this is the case,
you will need to upgrade the firmware on your adapter to a more recent release of EmberZNet or perform the one-time
write with `--force`.
