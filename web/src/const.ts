import { mdiMulticast, mdiZigbee } from '@mdi/js';

export enum ApplicationType {
  GECKO_BOOTLOADER = 'bootloader',
  CPC = 'cpc',
  EZSP = 'ezsp',
}

export const ApplicationNames = {
  [ApplicationType.GECKO_BOOTLOADER]: 'Bootloader (recovery)',
  [ApplicationType.CPC]: 'CPC',
  [ApplicationType.EZSP]: 'Zigbee',
};

export enum FirmwareType {
  NCP_UART_HW = 'ncp-uart-hw',
  RCP_UART_802154 = 'rcp-uart-802154',
  ZIGBEE_NCP_RCP_UART_802154 = 'zigbee-ncp-rcp-uart-802154',
}

export const FirmwareIcons = {
  [FirmwareType.NCP_UART_HW]: mdiZigbee,
  [FirmwareType.RCP_UART_802154]: mdiMulticast,
  [FirmwareType.ZIGBEE_NCP_RCP_UART_802154]: mdiMulticast,
};

export const FirmwareNames = {
  [FirmwareType.NCP_UART_HW]: 'Zigbee (EZSP)',
  [FirmwareType.RCP_UART_802154]: 'Multi-PAN (RCP)',
  [FirmwareType.ZIGBEE_NCP_RCP_UART_802154]:
    'Multi-PAN (Zigbee NCP & Thread RCP)',
};

export const ApplicationTypeToFirmwareType = {
  [ApplicationType.CPC]: FirmwareType.RCP_UART_802154,
  [ApplicationType.EZSP]: FirmwareType.NCP_UART_HW,
  [ApplicationType.GECKO_BOOTLOADER]: undefined,
};

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
  product_name: string;
  bootloader_baudrate: number;
  application_baudrate: number;
  usb_filters: USBFilter[];
  firmwares: Firmware[];
  allow_custom_firmware_upload: boolean;
}

export const mdiFirmware =
  'M9.5,8.5L11,10L8,13L11,16L9.5,17.5L5,13L9.5,8.5M14.5,17.5L13,16L16,13L13,10L14.5,8.5L19,13L14.5,17.5M21,2H3A2,2 0 0,0 1,4V20A2,2 0 0,0 3,22H21A2,2 0 0,0 23,20V4A2,2 0 0,0 21,2M21,20H3V6H21V20Z';
