# VKmodules Ethernet for Home Assistant

An independent Home Assistant integration for **VKmodule**, a Ukrainian manufacturer of network-connected I/O controllers based in Kyiv, Ukraine. Control and monitor VKmodule SOCKET devices locally over TCP/IP, without MQTT, cloud services, or a separate bridge.

## Supported devices

| Device | Digital inputs | Relays | Additional sensors |
| --- | ---: | ---: | --- |
| SOCKET-1 | 4 | — | — |
| SOCKET-2 | 2 | 2 | — |
| SOCKET-2W (Wi-Fi) | 2 | 2 | Analog voltage input (0–1 V) |
| SOCKET-3 | — | 2 | 2 temperature sensors |
| SOCKET-4 | — | 8 | — |
| SOCKET-5 | 4 | 4 | — |
| SOCKET-GIANT | 16 | 16 | — |

The integration detects the module type and creates the appropriate Home Assistant `switch`, `binary_sensor`, and `sensor` entities. SOCKET-2W shares an identification code with SOCKET-2, so its analog input is enabled separately in the integration options. VRD-E is not supported.

## Features

- Local, asynchronous TCP connection with event-driven state updates and automatic reconnection.
- Relay on/off control and hardware-timed relay pulses.
- Digital input monitoring and configurable hardware debounce.
- Temperature readings for SOCKET-3 and analog voltage readings for SOCKET-2W.
- Group relay control for SOCKET-GIANT.
- UI-based setup, device identification, connection reconfiguration, diagnostics, and English/Ukrainian translations.

## Installation

**From a repository:** Add this GitHub repository as a custom integration in the Home Assistant Marketplace (or HACS on installations that still use it), then install **VKmodules Ethernet**.

**Manually:** Copy `custom_components/vkmodules` into your Home Assistant configuration directory at `config/custom_components/vkmodules`, then restart Home Assistant.

Open **Settings → Devices & services → Add integration → VKmodules Ethernet** and enter the module's IP address and TCP port. Configure the module as a **TCP server** in its web interface; use the actual port configured on your device.

> **Connection limit:** Some VKmodule controllers accept only one TCP client at a time. If another application is connected to the same module, disconnect it before setting up VKmodules. The two integrations can remain installed side by side.

## Available actions

| Action | Purpose |
| --- | --- |
| `vkmodules.pulse` | Turn on a relay for a hardware-timed pulse (100–25,500 ms, 100 ms steps). |
| `vkmodules.configure_input` | Enable/disable input processing and configure debounce (20 ms steps). |
| `vkmodules.set_relay_mask` | Set **all 16** SOCKET-GIANT relays using a 16-bit mask. |

For SOCKET-2W, the analog input must also be enabled in the module's own settings. **Never apply more than 1 V to the analog input.**

## Manufacturer documentation

- [VKmodule Ethernet and Wi-Fi controllers (official product overview)](https://vkmodule.com.ua/Ethernet_ua.html)
- [VKmodule Ethernet modules — TCP/IP protocol (official PDF, Ukrainian)](https://vkmodule.com.ua/Ethernet/pdf/Protocol%20Ethernet-modules%20Ua.pdf)