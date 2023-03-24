# Universal Silicon Labs Flasher

Universal Silicon Labs Flasher is a basic Python 3 script to flash the firmware of Silabs based Zigbee and Thread products to a newer version.

It automatically communicates with radios over CPC, EZSP, or Spinel to enter bootloader mode and then flashes a firmware image using XMODEM.

### Disclaimer

This utility should also work with other generic Silicon Labs based radio adapters and modules from other vendors if they support CPC (Co-Processor Communication), EZSP (EmberZNet Serial Protocol) or Spinel (Openthread Serial Protocol) interfaces. Note however that firmwares for products not from Nabu Casa are not provided as part of this package and there is no guarantees that that it will work with products that are not officially Home Assistant branded. Please be hereby warned that you may void your warranty or possibly even brick your adapter if the firmware image and firmware update method is not officially supported by your mnaufacturer.

## Installation

```console
$ pip install universal-silabs-flasher
```

## Usage

Note! The baudrate speed is set to 115200 by default as that is used as standard in most application firmware images, however keep in mind that you may have to try different baud rate speeds as some application firapplication firmware imagemware images use might use slower or faster. Also, if you enter the bootloader manually then you may need to use a other baudrate speed than what the application firmware image uses.

```console
Usage: universal-silabs-flasher [OPTIONS] COMMAND [ARGS]...

Options:
  -v, --verbose
  --device PATH_OR_URL           [required]
  --baudrate INTEGER             [default: 115200]
  --bootloader-baudrate INTEGER  [default: 115200]
  --cpc-baudrate INTEGER         [default: 115200]
  --ezsp-baudrate INTEGER        [default: 115200]
  --spinel-baudrate INTEGER      [default: 460800]
  --probe-method TEXT            [default: bootloader, cpc, ezsp, spinel]
  --help                         Show this message and exit.

Commands:
  flash
  write-ieee
```

## Flashing firmware
For safety, firmware GBL image files are validated and their checksums verified both before sending, and by the device bootloader itself.

In addition to validating the firmware image, the version number of the firmware image currently running on the device is read.

 - If the provided firmware image type does not match the running image type, the firmware will not be flashed. Cross-flashing can be enabled with `--allow-cross-flashing`.
 - If the provided firmware image is a lower version than the currently running image, the downgrade will not be allowed. Downgrades can be enabled with `--allow-downgrades`.
 - To always upgrade/downgrade firmware to a specific version (i.e. as the entry point for an addon bundling firmware), use `--ensure-exact-version`.
 - All of the above logic can be skipped with `--force`.

### Yellow
The Yellow's bootloader can always be activated with the `--yellow-gpio-reset` flag:

```bash
$ universal-silabs-flasher \
    --device /dev/ttyAMA1 \
    flash \
    --firmware NabuCasa_RCP_v4.1.3_rcp-uart-hw-802154_230400.gbl \
    --yellow-gpio-reset
```

### SkyConnect
The SkyConnect will be rebooted into its bootloader from the running application: either EmberZNet or CPC.

```bash
$ universal-silabs-flasher \
    --device /dev/cu.SLAB_USBtoUART \
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
