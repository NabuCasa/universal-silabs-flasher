export enum FirmwareType {
  NCP_UART_HW = 'ncp-uart-hw',
  RCP_UART_802154 = 'rcp-uart-802154',
  ZIGBEE_NCP_RCP_UART_802154 = 'zigbee-ncp-rcp-uart-802154',
}

export interface USBFilter {
  pid: number;
  vid: number;
}

export interface Firmware {
  name: string;
  url: string;
  type: FirmwareType;
  version: string;
}

export interface Manifest {
  bootloader_baudrate: number;
  application_baudrate: number;
  usb_filters: USBFilter[];
  firmwares: Firmware[];
  allow_custom_firmware_upload: boolean;
}
