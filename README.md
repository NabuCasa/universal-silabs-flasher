# Universal Silicon Labs Flasher
Automatically communicates with radios over CPC or EZSP to enter the bootloader and then flashes a firmware image with XMODEM.

## Installation
```console
$ pip install universal-silabs-flasher
```

## Flashing firmware
For safety, firmware GBL image files are validated and their checksums verified both before sending, and by the device bootloader itself.

In addition to validating the firmware image, the version number of the firmware image currently running on the device is read.

 - If the provided firmware image type does not match the running image type, the firmware will not be flashed. Cross-flashing can be enabled with `--allow-cross-flashing`.
 - If the provided firmware image is a lower version than the currently running image, the downgrade will not be allowed. Downgrades can be enabled with `--allow-downgrades`.
 - If the provided firmware image already matches the version running on the device, the command will exit early. Firmware re-flashing can be enabled with `--allow-reflash-same-version`.

### Yellow
The Yellow's bootloader can always be activated with the `--yellow-gpio-reset` flag:

```bash
$ universal-silabs-flasher \
    --device /dev/ttyAMA1 \
    --bootloader-baudrate 115200 \
    --baudrate 115200 \
    flash \
    --firmware NabuCasa_RCP_v4.1.3_rcp-uart-hw-802154_230400.gbl \
    --yellow-gpio-reset
```

### SkyConnect
The SkyConnect will be rebooted into its bootloader from the running application: either EmberZNet or CPC.

```bash
$ universal-silabs-flasher \
    --device /dev/cu.SLAB_USBtoUART \
    --bootloader-baudrate 115200 \
    --baudrate 115200 \
    flash \
    --firmware NabuCasa_SkyConnect_EZSP_v7.1.3.0_ncp-uart-hw_115200.gbl
```


## Writing IEEE address
Ensure a target device running EmberZNet firmware has the correct node IEEE address:

```bash
$ universal-silabs-flasher \
    --device /dev/cu.SLAB_USBtoUART \
    write-ieee \
    --ieee 00:3c:84:ff:fe:92:bb:2c
```

The IEEE address can also be specified without colons: `--ieee 003c84fffe92bb2c`.

If the current device's IEEE address already matches the provided one, the command will not write it unnecesarily.
