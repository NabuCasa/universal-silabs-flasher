# Universal Silicon Labs Flasher

Automatically communicates with radios over CPC or EZSP to enter the bootloader and then
flashes a firmware image with XMODEM.  Firmware image GBL files are validated and their
checksums are verified before upload.

## Usage

```console
$ pip install git+https://github.com/puddly/universal-silabs-flasher.git
$ $ universal-silabs-flasher --device /dev/cu.SLAB_USBtoUART --baudrate 230400 --bootloader-baudrate 230400 flash --firmware NabuCasa_RCP_v4.1.3_rcp-uart-hw-802154_230400.gbl
```

## TODO

 - [ ] Add a [Click](https://click.palletsprojects.com/en/8.1.x/) CLI frontend
 - [ ] Create a multi-stage IEEE address re-flashing script
 - [ ] Implement firmware version validation to prevent downgrades
